"""Mouse and keyboard output.

Two rules that matter for this game:

  * **While fishing, never move the cursor.** The reference recording shows the
    player keeping it perfectly still; we only press and release where it
    already is. Moving is fine (and necessary) while an NPC dialogue is open —
    Roblox only swings the camera on right-drag or under shift lock.
  * Use SendInput, not PostMessage. Roblox reads raw input; window messages are
    ignored. Keys go out as **scan codes**, which is what games read.

`Mouse` tracks button state so the engine can idempotently ask for "held" or
"released" at 140 Hz without spamming the input queue, and so an abort always
leaves the button up.
"""

from __future__ import annotations

import ctypes
import sys
import time
from ctypes import wintypes

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008

# Scan codes (set 1). Games read these rather than virtual-key codes.
SC_W = 0x11
SC_S = 0x1F
SC_A = 0x1E
SC_D = 0x20
SC_LSHIFT = 0x2A
SC_E = 0x12

# Number row 1..9 then 0 — the hotbar slots.
SC_DIGITS = {"1": 0x02, "2": 0x03, "3": 0x04, "4": 0x05, "5": 0x06,
             "6": 0x07, "7": 0x08, "8": 0x09, "9": 0x0A, "0": 0x0B}


def digit_scan(slot: str, default: str = "1") -> int:
    """Scan code for a hotbar slot key ('1'..'9', '0').

    Falls back to `default` rather than raising: this is reached from the catch
    path on every fish, and a typo in a hand-edited config.json would otherwise
    throw once per catch for the whole session.
    """
    key = str(slot).strip()
    if key in SC_DIGITS:
        return SC_DIGITS[key]
    return SC_DIGITS.get(str(default).strip(), SC_DIGITS["1"])


def valid_slot(slot) -> bool:
    return str(slot).strip() in SC_DIGITS

ULONG_PTR = ctypes.c_ulonglong if ctypes.sizeof(ctypes.c_void_p) == 8 else ctypes.c_ulong


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD),
                ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", wintypes.DWORD), ("u", _U)]


# Windows-only: WinDLL exists only on Windows, and binding it at import would
# crash the whole package on Linux/macOS. The Windows Mouse/Keyboard below use
# it; other platforms substitute their own backend (see LINUX/inputs_linux.py)
# and never touch these helpers. Gate the bind so the module still imports.
_user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None


def _send(flags: int, dx: int = 0, dy: int = 0) -> None:
    inp = _INPUT(type=INPUT_MOUSE,
                 mi=_MOUSEINPUT(dx, dy, 0, flags, 0, 0))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


# Virtual-desktop metrics, for normalising absolute mouse moves.
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def _send_key(scan: int, up: bool) -> None:
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    inp = _INPUT(type=INPUT_KEYBOARD,
                 ki=_KEYBDINPUT(0, scan, flags, 0, 0))
    _user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT))


class Keyboard:
    """Scan-code key output for the movement / shift-lock keys."""

    def down(self, scan: int) -> None:
        _send_key(scan, False)

    def up(self, scan: int) -> None:
        _send_key(scan, True)

    def tap(self, scan: int, hold: float = 0.06) -> None:
        import time
        self.down(scan)
        time.sleep(hold)
        self.up(scan)


class Mouse:
    # A held/released state is re-emitted at least this often, even when it has
    # not changed. See `set`.
    REASSERT_AFTER = 0.15

    def __init__(self) -> None:
        self._down = False
        self._asserted = 0.0

    @property
    def is_down(self) -> bool:
        return self._down

    def set(self, down: bool) -> None:
        """Drive the left button to `down`, re-asserting periodically.

        A plain "only emit on change" guard assumes the game's button state
        always matches ours. It does not: SendInput can be dropped -- under
        load, or when focus flickers -- and a single lost LEFTUP/LEFTDOWN
        desyncs us from the game permanently, because from then on every call
        agrees with our stale `_down` and emits nothing. The reel minigame then
        sees the button jammed and the zone pins against a wall for the whole
        fight (measured: a lost LEFTUP left the zone stuck at the right edge for
        5 s while the fish escaped and progress bled from 0.80 to 0.16).

        So: emit on every change, and also re-emit the current state if it has
        not been asserted in REASSERT_AFTER seconds. A dropped event now
        self-heals within ~0.15 s instead of costing the whole reel, and
        re-emitting a state the button is already in is a no-op in game. The
        re-assert only fires during a *sustained* hold or release -- exactly the
        moments the zone would otherwise sit jammed -- because the controller's
        normal chatter changes the state far more often than every 0.15 s.
        """
        now = time.perf_counter()
        if down != self._down or now - self._asserted >= self.REASSERT_AFTER:
            _send(MOUSEEVENTF_LEFTDOWN if down else MOUSEEVENTF_LEFTUP)
            self._down = down
            self._asserted = now

    def press(self) -> None:
        self.set(True)

    def release(self) -> None:
        self.set(False)

    def click(self, hold: float = 0.045) -> None:
        import time
        self.press()
        time.sleep(hold)
        self.release()

    # -- cursor movement (shop / dialogue only, never while fishing) --------
    def move_to(self, x: int, y: int) -> None:
        """Put the cursor at an absolute desktop position, the way a real mouse
        would.

        **Not** SetCursorPos. That moves the OS cursor without injecting a mouse
        event, and Roblox tracks its GUI cursor from the input stream, not by
        polling GetCursorPos. The result was a cursor sitting visibly on a menu
        button, the OS reporting the click, and the game ignoring it because
        *its* cursor was still parked wherever the last real movement left it.

        SendInput with MOVE|ABSOLUTE|VIRTUALDESK injects a genuine move, so the
        game's cursor follows. Coordinates are normalised to 0..65535 across the
        whole virtual desktop.
        """
        vx = _user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = _user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = max(1, _user32.GetSystemMetrics(SM_CXVIRTUALSCREEN) - 1)
        vh = max(1, _user32.GetSystemMetrics(SM_CYVIRTUALSCREEN) - 1)
        nx = int(round((int(x) - vx) * 65535 / vw))
        ny = int(round((int(y) - vy) * 65535 / vh))
        nx = max(0, min(65535, nx))
        ny = max(0, min(65535, ny))
        _send(MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK,
              nx, ny)
        # Nail the exact pixel too: the 65535-step grid can land a pixel off,
        # and the injected move above has already done the job of telling the
        # game the mouse moved.
        _user32.SetCursorPos(int(x), int(y))

    def position(self) -> tuple[int, int]:
        pt = wintypes.POINT()
        _user32.GetCursorPos(ctypes.byref(pt))
        return int(pt.x), int(pt.y)

    def click_at(self, x: int, y: int, settle: float = 0.12,
                 hold: float = 0.05) -> None:
        """Move, let the GUI register the hover, then click.

        The move is done in two hops so there is always a real delta for the
        game to see, even if the cursor already happened to be on the target.
        """
        import time
        self.move_to(int(x) - 8, int(y) - 8)
        time.sleep(0.02)
        self.move_to(x, y)
        time.sleep(settle)
        self.click(hold)

    def __enter__(self) -> "Mouse":
        return self

    def __exit__(self, *exc) -> None:
        self.release()
