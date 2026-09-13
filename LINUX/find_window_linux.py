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

import time

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


def _find_sober_window(display, title: str):
    """Return the largest visible Sober candidate for an existing Display."""
    root = display.screen().root
    wanted = (title or "Roblox").strip().lower()
    best = None                                    # (area, Window)
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
            _x, _y, width, height = _abs_geometry(win, root)
        except Exception:                          # noqa: BLE001
            continue
        hay = " ".join(cls).lower() if cls else ""
        nm = (name or "").lower()
        if not ("sober" in hay or "sober" in nm or (wanted and wanted in nm)):
            continue
        if width > 400 and height > 300:
            area = width * height
            if best is None or area > best[0]:
                best = (area, win)
    return root, best[1] if best is not None else None


def find_game_window(title: str, screen) -> tuple[Rect, bool]:
    try:
        from Xlib import display

        d = display.Display()
        try:
            root, win = _find_sober_window(d, title)
            if win is not None:
                x, y, w, h = _abs_geometry(win, root)
                return Rect(x + _INSET, y + _INSET,
                            max(1, w - 2 * _INSET),
                            max(1, h - 2 * _INSET)), True
        finally:
            d.close()
    except Exception:                                  # noqa: BLE001
        pass
    return screen.primary(), False


def focus_game_window(title: str, *, display_factory=None, x_constants=None,
                      now=time.perf_counter, pause=time.sleep) -> bool:
    """Raise Sober and confirm X11 focus before sending any macro input."""
    try:
        if display_factory is None or x_constants is None:
            from Xlib import X, display
            display_factory = display.Display
            x_constants = X

        d = display_factory()
        try:
            _root, win = _find_sober_window(d, title)
            if win is None:
                return False
            # A hotkey press is user interaction, so most X11 window managers
            # allow this focus request.  Confirmation prevents blind uinput.
            win.configure(stack_mode=x_constants.Above)
            win.set_input_focus(x_constants.RevertToParent, x_constants.CurrentTime)
            d.sync()
            deadline = now() + 0.35
            while now() < deadline:
                focus = d.get_input_focus().focus
                if getattr(focus, "id", None) == win.id:
                    return True
                pause(0.02)
        finally:
            d.close()
    except Exception:                                  # noqa: BLE001
        pass
    return False
