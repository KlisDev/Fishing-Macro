"""Screen capture and game-window location.

The reel loop needs to run at >100 Hz, so the design is two-tier:

  * `grab(rect)` pulls an arbitrary rectangle (used for the low-rate scans).
  * Once the reel bar is located, the loop only ever grabs the ~1760x110 strip
    it occupies, which is cheap enough to poll at 140 Hz even on a 4K display.

`mss` keeps its Windows device contexts in a `threading.local`, so an instance
created on one thread blows up the moment another thread grabs with it. `Screen`
therefore creates one `mss` per thread on demand: construct it wherever you
like, use it from wherever you like.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass

import numpy as np
import mss


@dataclass(frozen=True)
class Rect:
    left: int
    top: int
    width: int
    height: int

    @property
    def right(self) -> int:
        return self.left + self.width

    @property
    def bottom(self) -> int:
        return self.top + self.height

    def sub(self, fx0: float, fy0: float, fx1: float, fy1: float) -> "Rect":
        """Sub-rectangle from fractional coordinates of this rect."""
        x0 = self.left + int(self.width * fx0)
        y0 = self.top + int(self.height * fy0)
        x1 = self.left + int(self.width * fx1)
        y1 = self.top + int(self.height * fy1)
        return Rect(x0, y0, max(1, x1 - x0), max(1, y1 - y0))

    def as_dict(self) -> dict:
        return {"left": self.left, "top": self.top,
                "width": self.width, "height": self.height}


class Screen:
    """Thin BGR-returning wrapper around mss, safe to share across threads."""

    def __init__(self) -> None:
        self._local = threading.local()
        self._all: list = []
        self._lock = threading.Lock()

    @property
    def _sct(self):
        sct = getattr(self._local, "sct", None)
        if sct is None:
            sct = mss.mss()
            self._local.sct = sct
            with self._lock:
                self._all.append(sct)
        return sct

    def grab(self, rect: Rect) -> np.ndarray:
        """Return an HxWx3 uint8 BGR array for `rect`."""
        raw = self._sct.grab(rect.as_dict())
        # mss gives BGRA; drop alpha without copying the whole buffer twice.
        return np.frombuffer(raw.raw, dtype=np.uint8).reshape(
            raw.height, raw.width, 4
        )[:, :, :3]

    def virtual_screen(self) -> Rect:
        m = self._sct.monitors[0]
        return Rect(m["left"], m["top"], m["width"], m["height"])

    def primary(self) -> Rect:
        m = self._sct.monitors[1]
        return Rect(m["left"], m["top"], m["width"], m["height"])

    def close(self) -> None:
        with self._lock:
            instances, self._all = self._all, []
        for sct in instances:
            try:
                sct.close()
            except Exception:
                pass
        self._local = threading.local()


def find_game_window(title: str, screen: Screen) -> Rect:
    """Locate the game window, falling back to the primary monitor.

    Roblox renders its client area under the title bar; we shave a few pixels
    so the border never leaks into the colour masks.
    """
    try:
        import pygetwindow as gw

        matches = [w for w in gw.getWindowsWithTitle(title)
                   if w.width > 400 and w.height > 300]
        if matches:
            w = max(matches, key=lambda w: w.width * w.height)
            if w.isMinimized:
                w.restore()
            return Rect(w.left + 8, w.top + 8,
                        max(1, w.width - 16), max(1, w.height - 16))
    except Exception:
        pass
    return screen.primary()
