"""Linux input backend — a uinput virtual mouse + keyboard.

Drop-in replacement for ``bloxfish.inputs.Mouse`` / ``Keyboard`` on Linux. It
injects at the *kernel* level (the analog of Windows ``SendInput``), so Sober
forwards the events to Android Roblox as if they came from real hardware — which
is exactly the "games read raw input, not window messages" requirement the
Windows layer was built around.

Two virtual devices are created (kept separate so the compositor classifies each
cleanly rather than as one confused pointer-and-keyboard hybrid):

  * an **absolute pointer** (``BTN_LEFT`` + ``ABS_X``/``ABS_Y``), so a move lands
    the cursor at a screen coordinate — the analog of ``MOUSEEVENTF_ABSOLUTE``;
  * a **keyboard**, emitting the movement / hotbar keys.

The public API matches ``bloxfish.inputs`` exactly, so ``engine.py`` and
``shop.py`` use it unchanged.

Needs write access to ``/dev/uinput`` — see ``LINUX/99-uinput.rules`` and
``install-udev.sh``. Without it, ``UInput(...)`` raises ``PermissionError`` and
the entry point prints the fix.
"""
from __future__ import annotations

import time

try:
    from evdev import UInput, AbsInfo, ecodes as e
except ImportError as exc:                             # pragma: no cover
    raise ImportError(
        "The Linux backend needs python-evdev.  pip install -r "
        "requirements-linux.txt  (or: pip install evdev)"
    ) from exc

# The scan codes the game uses are, by luck of history, identical to Linux evdev
# key codes (both descend from the AT set-1 scan codes): SC_W 0x11 == KEY_W 17,
# SC_LSHIFT 0x2A == KEY_LEFTSHIFT 42, digits 0x02.. == KEY_1.. — so a scan code
# is emitted straight through as an EV_KEY code, no translation table.
from bloxfish.inputs import (                          # noqa: E402
    SC_W, SC_S, SC_A, SC_D, SC_LSHIFT, SC_E, SC_DIGITS,
)

ABS_MAX = 65535        # normalise coordinates like the Windows VIRTUALDESK path
_KEYS = [SC_W, SC_S, SC_A, SC_D, SC_LSHIFT, SC_E] + list(SC_DIGITS.values())


def _screen_box() -> tuple[int, int, int, int]:
    """(width, height, left, top) of the whole virtual desktop, via mss —
    matches the Windows layer normalising against the virtual screen."""
    import mss
    with mss.mss() as sct:
        m = sct.monitors[0]        # [0] is the union of all monitors
        return (int(m["width"]), int(m["height"]),
                int(m.get("left", 0)), int(m.get("top", 0)))


def _make(caps: dict, name: str, props=None):
    """Create a UInput device, tolerating older python-evdev without input_props."""
    if props is not None:
        try:
            return UInput(caps, name=name, input_props=props)
        except TypeError:                              # evdev < 1.0
            pass
    return UInput(caps, name=name)


# One pointer + one keyboard device, shared across the process. Created lazily so
# importing the module never touches /dev/uinput (and a permission error surfaces
# only when the engine actually starts).
_pointer = None
_kbd = None


def _pointer_dev():
    global _pointer
    if _pointer is None:
        w, h, ox, oy = _screen_box()
        caps = {
            e.EV_KEY: [e.BTN_LEFT],
            e.EV_ABS: [
                (e.ABS_X, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
                (e.ABS_Y, AbsInfo(0, 0, ABS_MAX, 0, 0, 0)),
            ],
        }
        ui = _make(caps, "bloxfish-virtual-pointer", props=[e.INPUT_PROP_POINTER])
        ui._box = (w, h, ox, oy)                       # stash for move_to
        time.sleep(0.3)                                # let udev/X register it
        _pointer = ui
    return _pointer


def _kbd_dev():
    global _kbd
    if _kbd is None:
        _kbd = _make({e.EV_KEY: _KEYS}, "bloxfish-virtual-keyboard")
        time.sleep(0.1)
    return _kbd


class Mouse:
    """uinput left button + absolute cursor. Mirrors bloxfish.inputs.Mouse."""

    REASSERT_AFTER = 0.15

    def __init__(self) -> None:
        self._ui = _pointer_dev()
        self._down = False
        self._asserted = 0.0

    @property
    def is_down(self) -> bool:
        return self._down

    def _emit(self, down: bool) -> None:
        self._ui.write(e.EV_KEY, e.BTN_LEFT, 1 if down else 0)
        self._ui.syn()

    def set(self, down: bool) -> None:
        """Drive the button, re-asserting periodically — same contract as the
        Windows layer (see its docstring).

        NOTE: the Linux input core de-duplicates a same-value EV_KEY, so a
        re-asserted DOWN/UP is a kernel no-op when nothing was actually lost. It
        is kept for parity and to cover a drop in Sober's own forwarding; a
        *release-then-press* would be unsafe (it would drop the reel mid-hold),
        so we never do that. How much the re-assert buys on Linux is a test item.
        """
        now = time.perf_counter()
        if down != self._down or now - self._asserted >= self.REASSERT_AFTER:
            self._emit(down)
            self._down = down
            self._asserted = now

    def press(self) -> None:
        self.set(True)

    def release(self) -> None:
        self.set(False)

    def click(self, hold: float = 0.045) -> None:
        self.press()
        time.sleep(hold)
        self.release()

    def move_to(self, x: int, y: int) -> None:
        """Absolute cursor move, normalised 0..ABS_MAX across the virtual desktop
        (the analog of MOUSEEVENTF_MOVE|ABSOLUTE|VIRTUALDESK)."""
        w, h, ox, oy = self._ui._box
        nx = int(round((int(x) - ox) * ABS_MAX / max(1, w - 1)))
        ny = int(round((int(y) - oy) * ABS_MAX / max(1, h - 1)))
        nx = max(0, min(ABS_MAX, nx))
        ny = max(0, min(ABS_MAX, ny))
        self._ui.write(e.EV_ABS, e.ABS_X, nx)
        self._ui.write(e.EV_ABS, e.ABS_Y, ny)
        self._ui.syn()

    def position(self) -> tuple[int, int]:
        """Best-effort current cursor position (X11). Not needed by the reel or
        the shop clicks — click_at drives move_to directly."""
        try:
            from Xlib import display
            p = display.Display().screen().root.query_pointer()
            return int(p.root_x), int(p.root_y)
        except Exception:                              # noqa: BLE001
            return (0, 0)

    def click_at(self, x: int, y: int, settle: float = 0.12,
                 hold: float = 0.05) -> None:
        """Move in two hops so there is always a real delta, then click."""
        self.move_to(int(x) - 8, int(y) - 8)
        time.sleep(0.02)
        self.move_to(x, y)
        time.sleep(settle)
        self.click(hold)

    def __enter__(self) -> "Mouse":
        return self

    def __exit__(self, *exc) -> None:
        self.release()


class Keyboard:
    """uinput key output. Mirrors bloxfish.inputs.Keyboard (scan code == key code)."""

    def __init__(self) -> None:
        self._ui = _kbd_dev()

    def down(self, scan: int) -> None:
        self._ui.write(e.EV_KEY, scan, 1)
        self._ui.syn()

    def up(self, scan: int) -> None:
        self._ui.write(e.EV_KEY, scan, 0)
        self._ui.syn()

    def tap(self, scan: int, hold: float = 0.06) -> None:
        self.down(scan)
        time.sleep(hold)
        self.up(scan)
