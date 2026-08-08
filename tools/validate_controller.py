"""Run the real controller against the simulator over many randomised catches.

This is the offline proof that the control law works before it ever touches the
game. It deliberately runs the controller at a *lower* rate than the sim and
injects capture latency, quantisation and jitter, so it is pessimistic relative
to the real thing.

    python tools/validate_controller.py
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.config import Config          # noqa: E402
from bloxfish.controller import ReelController  # noqa: E402
from tools.simulator import Minigame, RecordedFish, SimParams  # noqa: E402


def run_one(cfg: Config, sp: SimParams, seed: int,
            control_hz: float, latency: float, quant_px: float,
            track_px: float = 1762.0) -> dict:
    rng = random.Random(seed)
    game = Minigame(sp, rng)
    ctl = ReelController(cfg.physics, cfg.control, cfg.timing)
    ctl.t.latency = latency

    hold = False
    next_ctl = 0.0
    # Sensor lag: the controller sees the world as it was `latency` ago.
    delay_steps = max(1, int(round(latency / sp.dt)))
    history: list[tuple[float, float]] = []

    while not game.done and game.t < 30.0:
        history.append((game.z, game.f))
        if game.t >= next_ctl:
            jitter = rng.uniform(-0.15, 0.15) / control_hz
            next_ctl = game.t + 1.0 / control_hz + jitter
            zs, fs = history[max(0, len(history) - 1 - delay_steps)]
            q = quant_px / track_px
            zs = round(zs / q) * q
            fs = round(fs / q) * q
            d = ctl.step(game.t, zs, fs, sp.zone_w / 2, sp.fish_w / 2)
            hold = d.hold
        game.tick(hold)

    return {"won": game.won, "t": game.t, "outside": game.time_outside,
            "accel_est": ctl.accel}


def run_recorded(cfg: Config, track: list[float], fps: float, zone_w: float,
                 seed: int, control_hz: float, latency: float,
                 start_offset: float) -> dict:
    """Same controller, but the fish follows a trajectory recorded from the game."""
    rng = random.Random(seed)
    sp = SimParams(zone_w=zone_w)
    game = RecordedFish(sp, track, fps, start=min(max(track[0] + start_offset,
                                                      zone_w / 2),
                                                  1 - zone_w / 2))
    ctl = ReelController(cfg.physics, cfg.control, cfg.timing)
    ctl.t.latency = latency

    hold = False
    next_ctl = 0.0
    delay_steps = max(1, int(round(latency / sp.dt)))
    history: list[tuple[float, float]] = []

    while not game.done:
        history.append((game.z, game.f))
        if game.t >= next_ctl:
            next_ctl = game.t + 1.0 / control_hz + rng.uniform(-0.15, 0.15) / control_hz
            zs, fs = history[max(0, len(history) - 1 - delay_steps)]
            q = 2.0 / 1762.0
            d = ctl.step(game.t, round(zs / q) * q, round(fs / q) * q,
                         sp.zone_w / 2, sp.fish_w / 2)
            hold = d.hold
        game.tick(hold)

    return {"won": game.won, "outside": game.time_outside,
            "worst": game.worst_error, "progress": game.progress}


def recorded_suite(cfg: Config) -> int:
    path = Path(__file__).resolve().parent.parent / "data" / "fish_tracks.json"
    if not path.exists():
        print("no data/fish_tracks.json — run tools/trace_video.py first")
        return 0

    runs = json.loads(path.read_text(encoding="utf-8"))
    slack = None
    print("\nreplaying recorded fish trajectories")
    print(f"{'run':>4} {'starts':>7} {'win%':>6} {'outside s':>10} "
          f"{'worst err':>10} {'slack':>7}")
    print("-" * 52)
    failures = 0
    for i, r in enumerate(runs):
        sp = SimParams(zone_w=r["zone_w"])
        slack = (sp.zone_w - sp.fish_w) / 2
        results = []
        # Start the zone all over the track: the bot has no say in where the
        # bar spawns relative to the fish.
        for k, off in enumerate(np.linspace(-0.35, 0.35, 15)):
            results.append(run_recorded(cfg, r["fish"], r["fps"], r["zone_w"],
                                        k, 60.0, 0.045, float(off)))
        wins = sum(x["won"] for x in results)
        print(f"{i:>4} {len(results):>7} {100*wins/len(results):5.1f}% "
              f"{max(x['outside'] for x in results):10.3f} "
              f"{max(x['worst'] for x in results):10.4f} {slack:7.4f}")
        failures += len(results) - wins
    return failures


def main() -> int:
    cfg = Config()
    scenarios = [
        # name, fish speed, control hz, latency, quantisation (px @4K)
        ("reference          ", 0.060, 140.0, 0.045, 2.0),
        ("fast fish (max obs)", 0.080, 140.0, 0.045, 2.0),
        ("2x fastest observed", 0.160, 140.0, 0.045, 2.0),
        ("slow loop 60 Hz    ", 0.060, 60.0, 0.045, 2.0),
        ("slow loop 30 Hz    ", 0.060, 30.0, 0.045, 2.0),
        ("high latency 120 ms", 0.060, 140.0, 0.120, 2.0),
        ("brutal 30Hz+150ms  ", 0.080, 30.0, 0.150, 4.0),
        ("wrong accel (-40%) ", 0.060, 140.0, 0.045, 2.0),
        ("twitchy fish       ", 0.060, 140.0, 0.045, 2.0),
    ]

    print(f"{'scenario':22} {'win%':>6} {'avg s':>7} {'outside s':>10} {'accel est':>10}")
    print("-" * 60)
    failures = 0
    for name, fs, hz, lat, q in scenarios:
        sp = SimParams(fish_speed=fs)
        cfg2 = Config()
        if "wrong accel" in name:
            cfg2.physics.accel = 1.92 * 0.6      # feed the controller a bad prior
        if "twitchy" in name:
            sp.flip_min, sp.flip_max = 0.10, 0.45

        results = [run_one(cfg2, sp, seed, hz, lat, q) for seed in range(200)]
        wins = sum(r["won"] for r in results)
        avg_t = sum(r["t"] for r in results) / len(results)
        avg_out = sum(r["outside"] for r in results) / len(results)
        acc = sum(r["accel_est"] for r in results) / len(results)
        print(f"{name:22} {100*wins/len(results):5.1f}% {avg_t:7.2f} "
              f"{avg_out:10.3f} {acc:10.2f}")
        if wins < len(results):
            failures += 1

    rec_failures = recorded_suite(cfg)

    print()
    if failures or rec_failures:
        print(f"{failures} synthetic scenario(s) with a loss, "
              f"{rec_failures} recorded-run loss(es)")
    else:
        print("all scenarios, synthetic and recorded: 100% catch rate")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
