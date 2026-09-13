"""Linux/Sober runtime context for the shared macro implementation.

The macro code lives in ``WINDOWS/`` for historical reasons, but Linux must
never save Sober calibration, templates, debug logs, or captures there.  Both
Linux launchers call :func:`configure_linux_runtime` before importing the core.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


HERE = Path(__file__).resolve().parent
CORE = HERE.parent / "WINDOWS"


def linux_environment(config_path: str | Path | None = None) -> dict[str, str]:
    """Return the complete, isolated Sober runtime environment.

    ``--config`` is deliberately also the parent for templates and diagnostics:
    a portable profile remains one self-contained folder.
    """
    config = (Path(config_path).expanduser().resolve() if config_path
              else HERE / "config.json")
    runtime_root = config.parent
    return {
        "BLOXFISH_NO_BOOT": "1",
        "BLOXFISH_CONFIG_PATH": str(config),
        "BLOXFISH_RUNTIME_ROOT": str(runtime_root),
        "BLOXFISH_GUI_ASSETS": str(HERE / "assets" / "gui"),
        "BLOXFISH_PLATFORM": "sober-x11",
    }


def configure_linux_runtime(config_path: str | Path | None = None) -> Path:
    """Set paths before shared modules import, then expose both code roots."""
    env = linux_environment(config_path)
    os.environ.update(env)
    for path in (str(CORE), str(HERE)):
        if path not in sys.path:
            sys.path.insert(0, path)
    return Path(env["BLOXFISH_CONFIG_PATH"])
