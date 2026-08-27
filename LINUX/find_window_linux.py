"""Locate the Sober (Roblox) window on X11.

Drop-in replacement for ``bloxfish.capture.find_game_window`` on Linux. Returns
``(Rect, found)`` exactly like the Windows version, and falls back to the whole
screen (``found=False``) when the window can't be identified — the engine already
treats that fallback as "couldn't line up, don't cast blindly".

Sober (the Flatpak Android Roblox) usually presents a window whose ``WM_CLASS``
is ``org.vinegarhq.Sober`` / instance ``sober`` and whose title is "Sober" (not
"Roblox"), so we match on either, plus the user's configured title.
"""
from __future__ import annotations

from bloxfish.capture import Rect

# A few px are shaved off so a window border never leaks into the colour masks,
# matching the 8px inset the Windows finder uses.
_INSET = 6


def _abs_geometry(win, root):
    """Absolute (x, y, w, h) of a client window in root coordinates."""
    geo = win.get_geometry()
    # translate_coords(dest, x, y) expresses (x, y) — here the window's own
    # origin (0, 0) — in the destination window's coordinates. Against root that
    # is the window's absolute top-left, decorations accounted for.
    t = win.translate_coords(root, 0, 0)
    return t.x, t.y, geo.width, geo.height


def find_game_window(title: str, screen) -> tuple[Rect, bool]:
    try:
        from Xlib import display

        wanted = (title or "Roblox").strip().lower()
        d = display.Display()
        root = d.screen().root

        best = None                                    # (area, Rect)
        stack = [root]
        while stack:
            win = stack.pop()
            try:
                children = win.query_tree().children
            except Exception:                          # noqa: BLE001
                children = []
            stack.extend(children)

            try:
                cls = win.get_wm_class()               # (instance, class) or None
                name = win.get_wm_name() or ""
            except Exception:                          # noqa: BLE001
                continue
            hay = " ".join(cls).lower() if cls else ""
            nm = (name or "").lower()
            if not ("sober" in hay or "sober" in nm
                    or (wanted and wanted in nm)):
                continue
            try:
                x, y, w, h = _abs_geometry(win, root)
            except Exception:                          # noqa: BLE001
                continue
            if w > 400 and h > 300:
                area = w * h
                if best is None or area > best[0]:
                    best = (area, Rect(x + _INSET, y + _INSET,
                                       max(1, w - 2 * _INSET),
                                       max(1, h - 2 * _INSET)))
        if best is not None:
            return best[1], True
    except Exception:                                  # noqa: BLE001
        pass
    return screen.primary(), False
