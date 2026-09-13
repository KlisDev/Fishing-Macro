"""Opt-in debug overlay + diagnostic log (toggled with F8). Off by default.

Two renderers fed by one event bus:

  * ``LogRenderer`` — timestamped events to ``diag_session_<ts>.log`` next to
    config.json. Every platform, every entry point. This is the reliable debug
    artifact — usually more useful than the video.
  * ``OverlayRenderer`` — a transparent, click-through, always-on-top window that
    frames each detection box and rings each click, so they show up in a screen
    recording.  Platform launchers can register an equivalent renderer.

The crucial safety property: the overlay draws each box's frame a few pixels
*OUTSIDE* its detection rectangle. The macro reads the screen with `mss`, which
would otherwise capture the overlay and corrupt detection; drawing outside the
rect means the macro's own grabs never contain a single overlay pixel.

The hot-path hooks (`DEBUG.box/click/event`) are a single attribute check when
disarmed, so leaving them in the engine costs nothing in normal use.
"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path


# A distinct colour per category, for the overlay only (frames are drawn outside
# the read rect, so colour can never affect detection). Order matters — more
# specific names first ("zone track" before "zone", "reel bar band" before it).
_PALETTE = [
    ("zone track", "#2dd4bf"),     # teal   (opt-in tight search box)
    ("reel bar band", "#3b82f6"),  # blue   (wide search region)
    ("reel bar", "#22d3ee"),       # cyan   (the located bar)
    ("bite", "#ffd166"),           # yellow
    ("meter", "#f0b23a"), ("charge", "#f0b23a"),   # amber
    ("card", "#fb923c"), ("popup", "#fb923c"),     # orange
    ("zone", "#34d399"),           # green  (detected zone span)
    ("fish", "#38bdf8"),           # sky    (detected fish)
    ("chest", "#fbbf24"),          # gold   (detected chest)
    ("menu", "#e879f9"), ("craft", "#e879f9"),
    ("learn", "#e879f9"), ("interact", "#e879f9"),  # magenta (clicks)
]


def _colour_for(name: str) -> str:
    n = name.lower()
    for key, col in _PALETTE:
        if key in n:
            return col
    return "#9be15d"

# Search boxes (where the bot LOOKS) are drawn dashed and dimmer; located things
# (the actual bar, the detected zone/fish) are solid, so the two read apart.
_SEARCH = ("reel bar band", "zone track", "bite marker", "charge meter",
           "catch card")


class LogRenderer:
    def __init__(self, out_dir: Path | None = None) -> None:
        if out_dir is None:
            from .config import CONFIG_PATH
            out_dir = CONFIG_PATH.parent
        ts = time.strftime("%Y%m%d_%H%M%S")
        self.path = Path(out_dir) / f"diag_session_{ts}.log"
        self._f = open(self.path, "w", encoding="utf-8")
        self._t0 = time.perf_counter()
        self._counts: dict[str, int] = {}
        import platform
        self.line("=== fishing macro debug session ===")
        self.line(f"when {ts}   python {platform.python_version()}   "
                  f"os {platform.system()} {platform.release()}")

    def _stamp(self) -> str:
        return f"{time.perf_counter() - self._t0:8.2f}s"

    def line(self, text: str) -> None:
        try:
            self._f.write(f"{self._stamp()}  {text}\n")
            self._f.flush()
        except Exception:                              # noqa: BLE001
            pass

    def event(self, tag: str, msg: str, kv: dict) -> None:
        self._counts[tag] = self._counts.get(tag, 0) + 1
        extra = "  ".join(f"{k}={v}" for k, v in kv.items())
        self.line(f"{tag:<9}{msg} {extra}".rstrip())

    def summary(self) -> None:
        self.line("--- summary (event counts) ---")
        for k, v in sorted(self._counts.items()):
            self.line(f"  {k:<12} {v}")

    def close(self) -> None:
        try:
            self._f.close()
        except Exception:                              # noqa: BLE001
            pass


class OverlayRenderer:
    """Full-screen transparent click-through canvas (Windows only)."""

    TTL = 1.0
    KEY = "#010203"          # transparent colour key (unlikely to be drawn)

    def __init__(self, tk_parent) -> None:
        if sys.platform != "win32":
            raise RuntimeError("overlay is Windows-only for now")
        import tkinter as tk
        self._tk = tk
        self.win = tk.Toplevel(tk_parent)
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        # Cover the full virtual desktop, not only the primary display.  Every
        # detector/click coordinate is absolute desktop space, so the previous
        # 0,0-primary canvas drew nothing useful when Roblox was on a monitor to
        # the left (negative x) or beyond the primary monitor's bounds.
        import ctypes
        user32 = ctypes.windll.user32
        self._ox = int(user32.GetSystemMetrics(76))     # SM_XVIRTUALSCREEN
        self._oy = int(user32.GetSystemMetrics(77))     # SM_YVIRTUALSCREEN
        sw = int(user32.GetSystemMetrics(78))           # SM_CXVIRTUALSCREEN
        sh = int(user32.GetSystemMetrics(79))           # SM_CYVIRTUALSCREEN
        self.win.geometry(f"{sw}x{sh}{self._ox:+d}{self._oy:+d}")
        self.win.configure(bg=self.KEY)
        self.win.attributes("-transparentcolor", self.KEY)   # Windows-only
        self.canvas = tk.Canvas(self.win, bg=self.KEY, highlightthickness=0,
                                borderwidth=0)
        self.canvas.pack(fill="both", expand=True)
        self._passthrough()
        self._lock = threading.Lock()
        self._boxes: dict = {}   # name -> (left, top, right, bottom, expiry)
        self._dots: dict = {}    # name -> (x, y, expiry)
        self._marks: dict = {}   # name -> (x0, x1, y, expiry)  brackets above bar
        self._alive = True
        self.win.after(30, self._tick)

    def _passthrough(self) -> None:
        """WS_EX_LAYERED | TRANSPARENT so the mouse passes through to the game."""
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(self.win.winfo_id())
        GWL_EXSTYLE = -20
        WS_EX_LAYERED = 0x00080000
        WS_EX_TRANSPARENT = 0x00000020
        WS_EX_TOOLWINDOW = 0x00000080
        cur = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ctypes.windll.user32.SetWindowLongW(
            hwnd, GWL_EXSTYLE,
            cur | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)

    # -- fed from the debug bus (any thread) --
    def box(self, name: str, rect) -> None:
        with self._lock:
            self._boxes[name] = (rect.left - self._ox, rect.top - self._oy,
                                 rect.right - self._ox, rect.bottom - self._oy,
                                 time.perf_counter() + self.TTL)

    def dot(self, name: str, x: float, y: float) -> None:
        with self._lock:
            self._dots[name] = (int(x) - self._ox, int(y) - self._oy,
                                time.perf_counter() + self.TTL)

    def mark(self, name: str, x0: float, x1: float, y: float) -> None:
        """A bracket/tick at a detected x-range, y sits OUTSIDE the read strip."""
        with self._lock:
            self._marks[name] = (int(x0) - self._ox, int(x1) - self._ox,
                                 int(y) - self._oy,
                                 time.perf_counter() + self.TTL)

    def _label(self, x: int, y: int, text: str, col: str, anchor="w") -> None:
        """Text with a dark pill behind it, so it reads over any background."""
        f = ("Segoe UI", 10, "bold")
        tid = self.canvas.create_text(x, y, text=text, fill=col, font=f,
                                      anchor=anchor)
        bx = self.canvas.bbox(tid)
        if bx:
            pad = 3
            self.canvas.create_rectangle(bx[0] - pad, bx[1] - 1, bx[2] + pad,
                                         bx[3] + 1, fill="#0b0d0f", outline="")
            self.canvas.tag_raise(tid)

    def _tick(self) -> None:
        if not self._alive:
            return
        now = time.perf_counter()
        self.canvas.delete("all")
        with self._lock:
            boxes = list(self._boxes.items())
            dots = list(self._dots.items())
            marks = list(self._marks.items())
        for name, (l, t, r, b, exp) in boxes:
            if now > exp:
                with self._lock:
                    self._boxes.pop(name, None)
                continue
            m = 4                                       # frame OUTSIDE the rect
            col = _colour_for(name)
            search = name in _SEARCH
            self.canvas.create_rectangle(
                l - m, t - m, r + m, b + m, outline=col,
                width=2 if search else 3,
                dash=(5, 4) if search else ())
            # label top-left, just above the box (outside the read rect)
            self._label(l - m, t - m - 10, name, col, anchor="w")
        for name, (x0, x1, y, exp) in marks:
            if now > exp:
                with self._lock:
                    self._marks.pop(name, None)
                continue
            col = _colour_for(name)
            if x1 - x0 > 6:                             # a span (the zone)
                self.canvas.create_line(x0, y, x1, y, fill=col, width=3)
                self.canvas.create_line(x0, y, x0, y + 7, fill=col, width=3)
                self.canvas.create_line(x1, y, x1, y + 7, fill=col, width=3)
                self._label((x0 + x1) // 2, y - 9, name, col, anchor="center")
            else:                                       # a point (fish / chest)
                cx = (x0 + x1) // 2
                self.canvas.create_polygon(cx, y + 8, cx - 6, y - 2, cx + 6,
                                           y - 2, fill=col, outline="")
                self._label(cx, y - 10, name, col, anchor="center")
        for name, (x, y, exp) in dots:
            if now > exp:
                with self._lock:
                    self._dots.pop(name, None)
                continue
            col = _colour_for(name)
            self.canvas.create_oval(x - 12, y - 12, x + 12, y + 12,
                                    outline=col, width=3)
            self.canvas.create_oval(x - 2, y - 2, x + 2, y + 2, fill=col,
                                    outline="")
            self._label(x, y - 22, name, col, anchor="center")
        self.win.after(40, self._tick)

    def close(self) -> None:
        self._alive = False
        try:
            self.win.destroy()
        except Exception:                              # noqa: BLE001
            pass


class DebugBus:
    """Singleton the engine/shop call. Disarmed = one bool check and return."""

    def __init__(self) -> None:
        self.enabled = False
        self._log: LogRenderer | None = None
        self._overlay: object | None = None
        self._overlay_factory = None

    def register_overlay_factory(self, factory) -> None:
        """Register a platform renderer before a debug session is armed.

        The shared Windows renderer remains the fallback.  Linux registers its
        X11 Shape overlay through this small seam instead of copying the debug
        bus or teaching the core about uinput/X11 details.
        """
        if self.enabled:
            raise RuntimeError("cannot replace the debug overlay while it is active")
        self._overlay_factory = factory

    @property
    def overlay_visible(self) -> bool:
        return self._overlay is not None

    # -- hot-path hooks --
    def box(self, name: str, rect) -> None:
        if not self.enabled:
            return
        ov = self._overlay
        if ov is not None:
            try:
                ov.box(name, rect)
            except Exception:                          # noqa: BLE001
                pass

    def click(self, name: str, x: float, y: float) -> None:
        if not self.enabled:
            return
        if self._log:
            self._log.event("click", name, {"x": int(x), "y": int(y)})
        if self._overlay:
            try:
                self._overlay.dot(name, x, y)
            except Exception:                          # noqa: BLE001
                pass

    def mark(self, name: str, x0: float, x1: float, y: float) -> None:
        """A detected span/point (zone/fish/chest), drawn just above the bar."""
        if not self.enabled:
            return
        ov = self._overlay
        if ov is not None:
            try:
                ov.mark(name, x0, x1, y)
            except Exception:                          # noqa: BLE001
                pass

    def event(self, tag: str, msg: str = "", **kv) -> None:
        if not self.enabled:
            return
        if self._log:
            self._log.event(tag, msg, kv)

    # -- control (call arm/disarm on the GUI thread when using an overlay) --
    def arm(self, overlay_parent=None, out_dir=None, *, show_overlay: bool | None = None):
        if self.enabled:
            return self._log.path if self._log else None
        self._log = LogRenderer(out_dir)
        if show_overlay is None:
            show_overlay = overlay_parent is not None
        if show_overlay:
            try:
                factory = self._overlay_factory
                self._overlay = (factory(overlay_parent) if factory is not None
                                 else OverlayRenderer(overlay_parent))
            except Exception as exc:                   # noqa: BLE001
                self._log.line(f"[overlay unavailable: {exc}]")
                self._overlay = None
        self.enabled = True
        self.event("debug", "armed",
                   overlay=self._overlay is not None)
        return self._log.path

    def disarm(self):
        if not self.enabled:
            return None
        self.event("debug", "disarmed")
        self.enabled = False
        if self._overlay is not None:
            self._overlay.close()
            self._overlay = None
        path = None
        if self._log is not None:
            self._log.summary()
            path = self._log.path
            self._log.close()
            self._log = None
        return path


DEBUG = DebugBus()
