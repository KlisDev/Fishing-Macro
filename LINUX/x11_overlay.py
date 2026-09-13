"""Click-through X11 hitboxes for the Linux GUI's F8 diagnostic mode.

This is intentionally not a Tk overlay.  A normal full-screen transparent Tk
window is compositor-dependent and can leak pixels into ``mss`` captures.  The
X Shape extension limits this window to the tiny coloured primitives we draw,
and its input shape is empty so it cannot steal a click from Sober.
"""
from __future__ import annotations

import threading
import time


_COLOURS = {
    "zone": "#34d399", "fish": "#38bdf8", "chest": "#fbbf24",
    "bite": "#ffd166", "meter": "#f0b23a", "charge": "#f0b23a",
    "menu": "#e879f9", "craft": "#e879f9", "learn": "#e879f9",
    "interact": "#e879f9", "bar": "#22d3ee",
}
_DEFAULT = "#9be15d"


def _colour(name: str) -> str:
    lower = name.lower()
    for key, value in _COLOURS.items():
        if key in lower:
            return value
    return _DEFAULT


def _intersects(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


class X11OverlayRenderer:
    """A shaped, input-transparent X11 overlay driven only while F8 is on."""

    TTL = 1.0
    FRAME = 4
    TICK = 0.04

    def __init__(self, _parent=None) -> None:
        self._lock = threading.Lock()
        self._boxes: dict[str, tuple[tuple[int, int, int, int], float]] = {}
        self._dots: dict[str, tuple[int, int, float]] = {}
        self._marks: dict[str, tuple[int, int, int, float]] = {}
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._failure: Exception | None = None
        self._thread = threading.Thread(target=self._run, name="bloxfish-x11-overlay",
                                        daemon=True)
        self._thread.start()
        self._ready.wait(0.75)
        if self._failure is not None:
            raise RuntimeError(str(self._failure)) from self._failure
        if not self._ready.is_set():
            raise RuntimeError("X11 overlay did not start")

    # DebugBus may call these from the engine thread.
    def box(self, name: str, rect) -> None:
        bounds = (int(rect.left), int(rect.top),
                  max(1, int(rect.right - rect.left)),
                  max(1, int(rect.bottom - rect.top)))
        with self._lock:
            self._boxes[name] = (bounds, time.perf_counter() + self.TTL)

    def dot(self, name: str, x: float, y: float) -> None:
        with self._lock:
            self._dots[name] = (int(x), int(y), time.perf_counter() + self.TTL)

    def mark(self, name: str, x0: float, x1: float, y: float) -> None:
        with self._lock:
            self._marks[name] = (int(x0), int(x1), int(y),
                                 time.perf_counter() + self.TTL)

    def close(self) -> None:
        self._stop.set()
        if self._thread is not threading.current_thread():
            self._thread.join(timeout=0.75)

    @staticmethod
    def _safe(candidate: tuple[int, int, int, int], protected) -> bool:
        return candidate[2] > 0 and candidate[3] > 0 and not any(
            _intersects(candidate, rect) for rect in protected)

    def _label_for(self, name: str, bounds: tuple[int, int, int, int], protected):
        """Put a compact text pill beside a hitbox without touching its crop."""
        x, y, w, h = bounds
        label_w = min(220, max(58, len(name) * 7 + 16))
        label_h = 18
        gap = self.FRAME + 2
        candidates = (
            (x, y - label_h - gap, label_w, label_h),
            (x, y + h + gap, label_w, label_h),
            (x + w + gap, y, label_w, label_h),
        )
        for candidate in candidates:
            # Negative positions may be accepted by X but leave a clipped and
            # misleading label.  Try the next safe side instead.
            if candidate[0] >= 0 and candidate[1] >= 0 and self._safe(candidate, protected):
                return (*candidate, name, _colour(name))
        return None

    def _primitives(self):
        """Build only pixels outside every detector/click region.

        Capturing our own overlay would invalidate a debugging session.  The
        protected union is intentionally conservative: if a label/marker has no
        safe place, it is omitted instead of contaminating a detector crop.
        """
        now = time.perf_counter()
        with self._lock:
            boxes = {name: item for name, item in self._boxes.items() if item[1] > now}
            dots = {name: item for name, item in self._dots.items() if item[2] > now}
            marks = {name: item for name, item in self._marks.items() if item[3] > now}
            self._boxes = boxes
            self._dots = dots
            self._marks = marks
        protected = [bounds for bounds, _expiry in boxes.values()]
        shapes: list[tuple[int, int, int, int, str]] = []
        labels: list[tuple[int, int, int, int, str, str]] = []
        for name, (bounds, _expiry) in boxes.items():
            x, y, w, h = bounds
            m = self.FRAME
            candidates = (
                (x - m, y - m, w + 2 * m, m),
                (x - m, y + h, w + 2 * m, m),
                (x - m, y, m, h),
                (x + w, y, m, h),
            )
            for candidate in candidates:
                if self._safe(candidate, protected):
                    shapes.append((*candidate, _colour(name)))
            label = self._label_for(name, bounds, protected)
            if label is not None:
                labels.append(label)
        for name, (x, y, _expiry) in dots.items():
            candidate = (x - 3, y - 3, 7, 7)
            if self._safe(candidate, protected):
                shapes.append((*candidate, _colour(name)))
                label = self._label_for(name, candidate, protected)
                if label is not None:
                    labels.append(label)
        for name, (x0, x1, y, _expiry) in marks.items():
            candidate = (min(x0, x1), y - 4, max(3, abs(x1 - x0)), 4)
            if self._safe(candidate, protected):
                shapes.append((*candidate, _colour(name)))
                label = self._label_for(name, candidate, protected)
                if label is not None:
                    labels.append(label)
        return shapes, labels

    @staticmethod
    def _shape_rectangles(window, shape, kind, rectangles) -> None:
        operation = shape.SO.Set
        # X Shape's Rectangles request orders its parameters as documented by
        # python-xlib: operation, destination kind, ordering, x/y offset,
        # then rectangles.  Keeping this in one helper makes the empty input
        # shape and the visual bounding shape use exactly the same API.
        ordering = 0  # Unsorted; the X server does not need ordered strips.
        # Python-Xlib installs extension methods on Window after importing
        # ``shape``.  A missing method means this X server cannot guarantee a
        # click-through overlay, so fail closed rather than draw an opaque one.
        method = getattr(window, "shape_rectangles", None)
        if method is None:
            raise RuntimeError("X Shape extension methods are unavailable")
        method(operation, kind, ordering, 0, 0, rectangles)

    def _run(self) -> None:
        display = window = None
        try:
            from Xlib import X, display as xdisplay
            from Xlib.ext import shape

            display = xdisplay.Display()
            if not display.has_extension("SHAPE"):
                raise RuntimeError("the X server does not provide the Shape extension")
            screen = display.screen()
            root = screen.root
            geometry = root.get_geometry()
            window = root.create_window(
                0, 0, geometry.width, geometry.height, 0,
                X.CopyFromParent, X.InputOutput, X.CopyFromParent,
                override_redirect=1, background_pixel=screen.black_pixel,
                event_mask=0)
            # Empty input shape is the safety gate: no hitbox can intercept a
            # Sober click even if it overlaps a button on screen.
            self._shape_rectangles(window, shape, shape.SK.Input, [])
            self._shape_rectangles(window, shape, shape.SK.Bounding, [])
            window.map()
            window.configure(stack_mode=X.Above)
            display.sync()
            self._ready.set()

            pixels: dict[str, int] = {}
            pill_pixel = screen.default_colormap.alloc_named_color("#08111f").pixel
            while not self._stop.wait(self.TICK):
                primitives, labels = self._primitives()
                rects = [(x, y, w, h) for x, y, w, h, _col in primitives]
                rects.extend((x, y, w, h) for x, y, w, h, _text, _col in labels)
                rects.sort(
                               key=lambda item: (item[1], item[0]))
                self._shape_rectangles(window, shape, shape.SK.Bounding, rects)
                window.clear_area(0, 0, 0, 0)
                for x, y, w, h, colour in primitives:
                    pixel = pixels.get(colour)
                    if pixel is None:
                        pixel = screen.default_colormap.alloc_named_color(colour).pixel
                        pixels[colour] = pixel
                    gc = window.create_gc(foreground=pixel)
                    window.fill_rectangle(gc, x, y, w, h)
                    gc.free()
                for x, y, w, h, text, colour in labels:
                    pill = window.create_gc(foreground=pill_pixel)
                    window.fill_rectangle(pill, x, y, w, h)
                    pill.free()
                    pixel = pixels.get(colour)
                    if pixel is None:
                        pixel = screen.default_colormap.alloc_named_color(colour).pixel
                        pixels[colour] = pixel
                    text_gc = window.create_gc(foreground=pixel)
                    # The default server font is deliberately used: loading a
                    # font per F8 session is needless overhead and can fail on
                    # minimal desktops.  The short labels still identify each
                    # detector/click target in a recording.
                    window.draw_text(text_gc, x + 7, y + 13, text[:30])
                    text_gc.free()
                display.flush()
        except Exception as exc:                       # noqa: BLE001
            self._failure = exc
            self._ready.set()
        finally:
            if window is not None:
                try:
                    window.destroy()
                except Exception:                      # noqa: BLE001
                    pass
            if display is not None:
                try:
                    display.close()
                except Exception:                      # noqa: BLE001
                    pass


def create_x11_overlay(parent=None) -> X11OverlayRenderer:
    """Factory registered by the Linux backend with the shared DebugBus."""
    return X11OverlayRenderer(parent)
