"""Plug the Linux uinput + X11 backends into the shared macro.

Both Linux entry points (``run_linux.py`` terminal, ``easy_run_linux.py`` GUI)
call ``patch()`` once, before any engine is constructed. It replaces the backend
names the code resolves at run time:

  * ``bloxfish.engine.{Mouse, Keyboard, find_game_window}`` — the engine binds
    these from its own module globals when it builds them.
  * ``bloxfish.capture.find_game_window`` — the calibrator (``easy_run.py``)
    imports it lazily from ``capture`` when it takes its screenshot.

``Screen`` is left as the shared ``mss`` one, which already captures on X11.
"""
from __future__ import annotations

import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# The shared core lives in the sibling WINDOWS/ folder (bloxfish/, easy_run.py,
# assets/ …). It is cross-platform despite the folder name; only inputs.py has a
# Windows branch, which this backend replaces.
_CORE = os.path.join(os.path.dirname(_HERE), "WINDOWS")
for _p in (_CORE, _HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def patch() -> None:
    import bloxfish.engine as engine
    import bloxfish.capture as capture
    from inputs_linux import Mouse, Keyboard
    from find_window_linux import find_game_window

    engine.Mouse = Mouse
    engine.Keyboard = Keyboard
    engine.find_game_window = find_game_window
    capture.find_game_window = find_game_window
