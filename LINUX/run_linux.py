#!/usr/bin/env python3
"""Linux (Sober / X11) entry point for the fishing macro.

Wires the uinput + X11 backends into the *shared* engine and runs it. The core
(vision, controller, engine, shop, config) is imported from ``bloxfish/`` — not
copied — so this stays in step with the Windows version automatically.

    python LINUX/run_linux.py            # F2 start/stop, F4 quit (or Ctrl+C)
    python LINUX/run_linux.py --now      # start immediately

Setup (see LINUX/README_LINUX.md): an **X11 session**, Sober running with the
fishing spot ready, and one-time ``/dev/uinput`` permission
(``sudo bash LINUX/install-udev.sh``).
"""
from __future__ import annotations

import argparse
import datetime
import sys
import threading
import time

def _patch_backends() -> None:
    """Substitute the Linux uinput + X11 backends into the shared engine.
    See LINUX/_backend.py."""
    from _backend import patch
    patch()


def _install_hotkeys(cfg, start, stop, quitting):
    """F2/F4 via pynput on X11. Returns True if the listener is armed."""
    try:
        from pynput import keyboard as pk
    except Exception:                                  # noqa: BLE001
        return False

    last = [0.0]

    def toggle():
        now = time.perf_counter()
        if now - last[0] < 0.4:                        # debounce double-delivery
            return
        last[0] = now
        stop() if _engine_running() else start()

    def _engine_running():
        return getattr(_install_hotkeys, "_eng", None) and _install_hotkeys._eng.running

    def key(name):
        return f"<{name.lower()}>"

    def toggle_debug():
        # Log only (no window to host an overlay in terminal mode).
        from bloxfish.debug import DEBUG
        if DEBUG.enabled:
            p = DEBUG.disarm()
            print(f"[debug] OFF — saved {p}" if p else "[debug] OFF")
        else:
            print(f"[debug] ON (log) — writing {DEBUG.arm()}")

    try:
        hk = pk.GlobalHotKeys({key(cfg.start_stop_key): toggle,
                               key(cfg.quit_key): quitting.set,
                               "<f8>": toggle_debug})
        hk.daemon = True
        hk.start()
        return True
    except Exception as exc:                           # noqa: BLE001
        print(f"hotkeys unavailable ({exc})")
        return False


def main() -> int:
    if sys.platform == "win32":
        print("run_linux.py is the Linux entry point — use easy_run.py on Windows.")
        return 1

    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true",
                    help="start immediately, don't wait for a hotkey")
    ap.add_argument("--debug", action="store_true", help="log while reeling")
    ap.add_argument("--diag", action="store_true",
                    help="save detector pixels after failures")
    ap.add_argument("--record", action="store_true",
                    help="save sampled reel strips while fishing")
    ap.add_argument("--dev", action="store_true",
                    help="debug + diag + record into one timestamped capture folder")
    ap.add_argument("--config", default=None,
                    help="isolated Sober config file (templates/logs sit beside it)")
    args = ap.parse_args()

    # Establish Linux paths before ``_backend`` or any shared module imports.
    from linux_runtime import configure_linux_runtime
    configure_linux_runtime(args.config)

    from preflight_linux import run_preflight
    if not run_preflight(gui=False):
        return 1

    try:
        _patch_backends()
    except Exception as exc:                           # noqa: BLE001
        print(f"Could not load the Linux backend: {exc}\n")
        print("  1) pip install -r LINUX/requirements-linux.txt")
        print("  2) sudo bash LINUX/install-udev.sh   (then log out and back in)")
        return 1

    import bloxfish.engine as E
    from bloxfish.config import CONFIG_PATH, Config

    cfg = Config.load()
    cfg.debug = args.debug or cfg.debug
    cfg.diag = args.diag or cfg.diag
    cfg.record = args.record or cfg.record
    logfile = None
    log = print
    if args.dev:
        cfg.debug = cfg.diag = cfg.record = True
        capture = CONFIG_PATH.parent / (
            "capture_" + datetime.datetime.now().strftime("%Y%m%d_%H%M%S"))
        capture.mkdir(parents=True, exist_ok=True)
        cfg.capture_dir = str(capture)
        logfile = open(capture / "run.log", "a", encoding="utf-8")

        def log(*parts):
            line = " ".join(str(part) for part in parts)
            print(line)
            try:
                logfile.write(line + "\n")
                logfile.flush()
            except Exception:                          # noqa: BLE001
                pass
        log(f"[dev] capturing Sober diagnostics to {capture}")

    engine = E.FishingEngine(cfg, log=log)
    _install_hotkeys._eng = engine

    worker = {"t": None}

    def start():
        if worker["t"] and worker["t"].is_alive():
            return
        print("--- started ---")
        worker["t"] = threading.Thread(target=engine.run, daemon=True)
        worker["t"].start()

    def stop():
        if engine.running:
            print("--- stopping ---")
            engine.stop()

    quitting = threading.Event()
    armed = False if args.now else _install_hotkeys(cfg, start, stop, quitting)

    if args.now or not armed:
        if not args.now:
            print("Global hotkeys unavailable — starting now. Ctrl+C to quit.")
        start()
    else:
        print(f"[{cfg.start_stop_key.upper()}] start/stop   "
              f"[{cfg.quit_key.upper()}] quit   (or Ctrl+C)")

    try:
        while not quitting.is_set():
            time.sleep(0.1)
            if (args.now or not armed or engine.safety_stopped) and (
                    worker["t"] and not worker["t"].is_alive()):
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        time.sleep(0.2)
        try:
            engine.close()
        except Exception:                              # noqa: BLE001
            pass
        if logfile is not None:
            try:
                logfile.close()
            except Exception:                          # noqa: BLE001
                pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
