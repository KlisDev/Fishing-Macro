"""First-run dependency install, shared by both entry points.

Standard library only, on purpose: this has to run *before* anything imports
numpy or opencv, so it cannot depend on them itself.

Without this, launching the bot on a fresh machine — or hitting the Run button
in an editor — fails with a bare `ModuleNotFoundError: No module named 'numpy'`,
which tells a non-technical user nothing at all.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STAMP = ROOT / ".deps_installed"
REQUIREMENTS = ROOT / "requirements.txt"

# Import name -> what to tell pip. Mostly the same, but not always.
REQUIRED = {
    "numpy": "numpy",
    "cv2": "opencv-python",
    "mss": "mss",
}


def _missing() -> list[str]:
    out = []
    for module, package in REQUIRED.items():
        try:
            __import__(module)
        except ImportError:
            out.append(package)
    return out


def ensure_requirements(log=print, force: bool = False) -> bool:
    """Install requirements.txt if anything is missing. True if we can proceed.

    The marker file makes the common case free, but we still check that the
    core packages actually import — a stale marker from a different Python
    install would otherwise skip the install and fail confusingly later.
    """
    missing = _missing()
    if not missing and STAMP.exists() and not force:
        return True
    if not missing and not force:
        STAMP.write_text("ok\n", encoding="utf-8")
        return True

    if not REQUIREMENTS.exists():
        log(f"Missing packages: {', '.join(missing)}")
        log(f"Install them with:  {Path(sys.executable).name} -m pip install "
            f"{' '.join(missing)}")
        return False

    log("First run - installing what the bot needs. This takes a minute...")
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS)],
            capture_output=True, text=True, timeout=900,
        )
    except Exception as exc:                              # noqa: BLE001
        log(f"Could not run pip: {exc}")
        _manual_hint(log, missing)
        return False

    if proc.returncode != 0:
        log("pip could not install everything:")
        log((proc.stderr or proc.stdout or "")[-1200:])
        _manual_hint(log, missing)
        return False

    still = _missing()
    if still:
        log(f"pip finished but these are still missing: {', '.join(still)}")
        _manual_hint(log, still)
        return False

    STAMP.write_text("ok\n", encoding="utf-8")
    log("All set.")
    return True


def _manual_hint(log, packages: list[str]) -> None:
    exe = sys.executable
    log("")
    log("Install them yourself with this command:")
    log(f'  "{exe}" -m pip install ' + " ".join(packages))
    log("")
    log("If that fails, the usual cause is a Python version too new for these")
    log("packages to have a prebuilt download yet. Python 3.12 is a safe choice.")
