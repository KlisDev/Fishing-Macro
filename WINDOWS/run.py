"""Blox Fruits auto-fisher — entry point.

    python run.py                 # F2 to start/stop, F4 to quit
    python run.py --debug         # log progress/error while reeling
    python run.py --now           # start immediately, no hotkey wait

The bot never moves the cursor: park it somewhere harmless inside the Roblox
window (a moving cursor rotates the camera) and leave Roblox focused.
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _bootstrap import ensure_requirements          # noqa: E402

# Must happen before anything pulls in numpy/opencv, otherwise a fresh machine
# just gets `ModuleNotFoundError` with no hint about what to do.
if not ensure_requirements():
    print()
    print("Cannot start until those are installed.")
    raise SystemExit(1)

from bloxfish.config import Config                  # noqa: E402
from bloxfish.engine import FishingEngine           # noqa: E402


def _ask(prompt: str, default: str = "") -> str:
    try:
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return default


def _setup_prompts(cfg, engine) -> bool:
    """Collect the run settings and show the pre-flight checklist.

    Returns False if the user backed out.
    """
    shop = cfg.shop

    # --- which NPC ------------------------------------------------------
    while True:
        ans = _ask("Which NPC will you buy from?  [F]isherman "
                   "(Enter = Fisherman, or N for no buying): ", "f").lower()
        if ans in ("", "f", "fisherman"):
            shop.npc = "fisherman"
            break
        if ans in ("n", "no", "none"):
            shop.npc = "none"
            break
        print("  please answer F, A, or N")

    # --- how much bait per purchase -------------------------------------
    if shop.npc == "fisherman":
        step = max(1, shop.craft_step)
        while True:
            raw = _ask(f"How much bait per purchase? (multiple of {step}, "
                       f"Enter = {shop.bait_per_purchase}): ")
            if not raw:
                break
            if raw.isdigit() and int(raw) >= step and int(raw) % step == 0:
                shop.bait_per_purchase = int(raw)
                break
            print(f"  must be a multiple of {step} and at least {step}")
        clicks = (shop.bait_per_purchase // step) - 1
        print(f"  -> {shop.bait_per_purchase} bait per trip "
              f"({clicks} '+' click{'s' if clicks != 1 else ''} in the craft window)")

    # --- fishing rod hotbar slot -----------------------------------------
    if shop.npc == "fisherman":
        while True:
            raw = _ask(f"Which hotbar slot is your fishing rod in? 1-9 or 0 "
                       f"(Enter = {cfg.rod_slot}): ")
            if not raw:
                break
            if raw in [str(d) for d in range(10)]:
                cfg.rod_slot = raw
                break
            print("  must be a single digit 1-9 or 0")
        print(f"  -> will press '{cfg.rod_slot}' to stow/draw the rod")

    # --- auto-sell --------------------------------------------------------
    if shop.npc == "fisherman":
        while True:
            raw = _ask(f"Sell your fish every how many catches? 0 = never "
                       f"(Enter = {cfg.sell.every}): ")
            if not raw:
                break
            if raw.isdigit():
                cfg.sell.every = int(raw)
                cfg.sell.enabled = cfg.sell.every > 0
                break
            print("  must be a whole number (0 to disable)")
        print(f"  -> {'selling every %d catches' % cfg.sell.every}"
              if cfg.sell.enabled else "  -> auto-sell off")

    # --- current bait ----------------------------------------------------
    raw = _ask("How much bait do you have right now? (Enter to skip tracking) ")
    engine.bait_count = int(raw) if raw.isdigit() else None
    if engine.bait_count is None:
        print("  bait tracking off — the bot will never stop to buy")
    else:
        print(f"  tracking {engine.bait_count}; will restock at "
              f"{shop.buy_at}")

    # --- pre-flight checklist -------------------------------------------
    print("\n" + "=" * 62)
    print("BEFORE YOU START — get these exactly right or the clicks miss:")
    print("  1. Stand at the NPC, right on the edge of interaction range")
    print("     (the 'Interact' prompt should be showing).")
    print("  2. Turn OFF auto-run / running.")
    print("  3. Turn OFF shift lock — the bot switches it on itself.")
    print("  4. Leave Roblox focused and don't touch the mouse while it runs.")
    print("  5. Don't let your editor/terminal overlap the game window — the")
    print("     bot reads the screen, and a covered panel reads as 'not there'.")
    print(f"  6. EQUIP your fishing rod now (slot {cfg.rod_slot}) and leave it out.")
    print("     The bot stows and re-draws it around each bait trip, which is")
    print("     what clears the post-catch stuck state.")
    print("=" * 62)
    ans = _ask("Ready? [Enter to continue, N to quit] ", "n").lower()
    if ans in ("n", "no", "q", "quit"):
        print("aborted")
        return False
    return True


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--now", action="store_true", help="skip the hotkey wait")
    ap.add_argument("--diag", action="store_true",
                    help="save the pixels behind any detection failure to diag/")
    ap.add_argument("--record", action="store_true",
                    help="save the reel strip while it works, to record/")
    ap.add_argument("--dev", action="store_true",
                    help="debug + diag + record together into one capture_<ts>/ "
                         "folder (with run.log) -- one folder to zip for a report")
    ap.add_argument("--config", default=None)
    args = ap.parse_args()

    cfg = Config.load(args.config)
    if args.debug:
        cfg.debug = True
    if args.diag:
        cfg.diag = True
    if args.record:
        cfg.record = True

    log = print
    if args.dev:
        import datetime
        cfg.debug = cfg.diag = cfg.record = True
        root = Path(__file__).resolve().parent
        stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        cap = root / f"capture_{stamp}"
        cap.mkdir(parents=True, exist_ok=True)
        cfg.capture_dir = str(cap)
        logfile = open(cap / "run.log", "a", encoding="utf-8")

        def log(*parts):                               # tee console + file
            line = " ".join(str(p) for p in parts)
            print(line)
            try:
                logfile.write(line + "\n")
                logfile.flush()
            except Exception:                          # noqa: BLE001
                pass
        log(f"[dev] capturing everything to {cap}")

    engine = FishingEngine(cfg, log=log)
    worker: threading.Thread | None = None

    if not _setup_prompts(cfg, engine):
        engine.close()
        return 0

    def start() -> None:
        nonlocal worker
        if worker and worker.is_alive():
            return
        print("--- started ---")
        worker = threading.Thread(target=engine.run, daemon=True)
        worker.start()

    def stop() -> None:
        if engine.running:
            print("--- stopping ---")
            engine.stop()

    try:
        import keyboard
    except Exception:
        keyboard = None

    if args.now or keyboard is None:
        if keyboard is None and not args.now:
            print("`keyboard` unavailable — starting immediately, Ctrl+C to quit")
        start()
    else:
        print(f"[{cfg.start_stop_key.upper()}] start/stop   "
              f"[{cfg.quit_key.upper()}] quit")

    quitting = threading.Event()

    if keyboard is not None:
        last_toggle = [0.0]

        def toggle() -> None:
            # Debounce: the keyboard hook can deliver a single physical press as
            # two events, which would start-then-stop and leave the bot idle.
            now = time.perf_counter()
            if now - last_toggle[0] < 0.4:
                return
            last_toggle[0] = now
            if engine.running:
                stop()
            else:
                start()

        def toggle_debug() -> None:
            # Terminal mode has no window to host the overlay, so this is the
            # diagnostic LOG only (the GUI, easy_run.py, adds the visual overlay).
            from bloxfish.debug import DEBUG
            if DEBUG.enabled:
                p = DEBUG.disarm()
                print(f"[debug] OFF — saved {p}" if p else "[debug] OFF")
            else:
                print(f"[debug] ON (log) — writing {DEBUG.arm()}")

        # A global hook needs admin on Windows; without it this raises. Fall
        # back to starting straight away rather than sitting on a hotkey that
        # will never fire.
        try:
            keyboard.add_hotkey(cfg.start_stop_key, toggle)
            keyboard.add_hotkey(cfg.quit_key, quitting.set)
            keyboard.add_hotkey("f8", toggle_debug)
        except Exception as exc:                       # noqa: BLE001
            print(f"hotkeys unavailable ({exc}) — starting now, Ctrl+C to quit")
            keyboard = None
            start()

    try:
        while not quitting.is_set():
            time.sleep(0.1)
            if (args.now or keyboard is None) and worker and not worker.is_alive():
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop()
        if worker and worker.is_alive():
            worker.join(timeout=3.0)
        engine.close()
        print("bye")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
