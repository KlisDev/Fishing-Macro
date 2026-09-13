"""Safe, no-input readiness checks for the Sober/X11 launchers."""
from __future__ import annotations

import importlib
import os
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class Preflight:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def _probe_sober_frame(title: str) -> tuple[bool, bool]:
    """Return ``(window_found, looks_blank)`` without creating input devices."""
    import numpy as np
    from bloxfish.capture import Screen
    from find_window_linux import find_game_window

    screen = Screen()
    try:
        rect, found = find_game_window(title, screen)
        if not found:
            return False, False
        frame = screen.grab(rect)
        # A genuinely all-black capture is a common portal/compositor failure.
        # A dark scene is merely warned about; it must not stop setup.
        return True, bool(frame.size and int(np.max(frame)) <= 3)
    finally:
        screen.close()


def validate_sober_x11(
        title: str = "Sober", *, gui: bool = False,
        environ: dict[str, str] | None = None,
        access: Callable[[str, int], bool] = os.access,
        exists: Callable[[str], bool] = os.path.exists,
        importer: Callable[[str], object] = importlib.import_module,
        frame_probe: Callable[[str], tuple[bool, bool]] = _probe_sober_frame,
) -> Preflight:
    """Check X11/Sober readiness without injecting, focusing, or clicking.

    The injectable dependencies make the decision logic testable on Windows and
    CI, where neither an X display nor ``/dev/uinput`` exists.
    """
    env = os.environ if environ is None else environ
    report = Preflight()
    if env.get("XDG_SESSION_TYPE", "").strip().lower() != "x11":
        report.errors.append(
            "Sober support requires an X11 session. Log out and choose an Xorg/X11 session.")
    if not env.get("DISPLAY", "").strip():
        report.errors.append("DISPLAY is not set; start this from the active X11 desktop session.")
    if not exists("/dev/uinput") or not access("/dev/uinput", os.W_OK):
        report.errors.append(
            "Cannot write /dev/uinput. Run: sudo bash LINUX/install-udev.sh, then log out and back in.")

    modules = ["mss", "numpy", "cv2", "PIL", "evdev", "Xlib", "pynput"]
    if gui:
        modules.append("customtkinter")
    for module in modules:
        try:
            importer(module)
        except Exception:                              # noqa: BLE001
            report.errors.append(
                f"Missing Linux dependency: {module}. Run: pip install -r LINUX/requirements-linux.txt")

    if report.errors:
        return report
    try:
        found, looks_blank = frame_probe(title)
    except Exception as exc:                           # noqa: BLE001
        report.errors.append(f"Could not inspect the Sober window: {exc}")
        return report
    if not found:
        report.errors.append("No Sober window was found. Open Sober with Roblox visible, then retry.")
    elif looks_blank:
        report.warnings.append(
            "Sober capture looks black. Continue only after Calibrate shows the real game instead of a blank frame.")
    return report


def run_preflight(title: str = "Sober", *, gui: bool = False) -> bool:
    """Print actionable readiness results and return whether launching is safe."""
    report = validate_sober_x11(title, gui=gui)
    for warning in report.warnings:
        print(f"[preflight warning] {warning}")
    for error in report.errors:
        print(f"[preflight] {error}")
    if report.ok:
        print("[preflight] Sober/X11 input, capture, and window checks passed.")
    return report.ok
