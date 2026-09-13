#!/usr/bin/env python3
"""The full GUI on Linux (Sober / X11).

Identical to Windows ``easy_run.py`` — the setup wizard, Calibrate controls,
the colour eyedropper, the Zone track box, the Advanced cooldowns editor,
F2/F4 hotkeys — but driving the uinput + X11 backend. The GUI code and the whole
detection/control core are the shared ones; only the input/window layer differs.

    python LINUX/easy_run_linux.py

Setup (see LINUX/README_LINUX.md): an X11 session, Sober running, deps from
requirements-linux.txt, and the one-time /dev/uinput permission
(``sudo bash LINUX/install-udev.sh``).
"""
from __future__ import annotations

import sys


def main() -> int:
    if sys.platform == "win32":
        print("This is the Linux entry point — run easy_run.py on Windows.")
        return 1

    # This must precede every shared import: it isolates Sober calibration,
    # assets, templates and diagnostics from the Windows profile.
    from linux_runtime import configure_linux_runtime
    configure_linux_runtime()

    from preflight_linux import run_preflight
    if not run_preflight(gui=True):
        return 1

    from _backend import patch
    try:
        patch()
    except Exception as exc:                           # noqa: BLE001
        print(f"Could not load the Linux backend: {exc}\n")
        print("  1) pip install -r LINUX/requirements-linux.txt")
        print("  2) sudo bash LINUX/install-udev.sh   (then log out and back in)")
        return 1

    # Import AFTER context + backend patching so App uses Linux paths, Sober
    # focus/uinput, and the X11 F8 hitbox overlay from its first construction.
    import easy_run
    app = easy_run.App()
    app.protocol("WM_DELETE_WINDOW", app._close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
