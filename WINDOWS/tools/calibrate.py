"""Calibration / diagnostics. Run this before trusting the bot.

    python tools/calibrate.py window      # where is the game, how fast can we grab
    python tools/calibrate.py bite        # live bite-marker detection (cast, watch)
    python tools/calibrate.py meter       # live cast charge-meter score
    python tools/calibrate.py bar         # live reel-bar reading (start a minigame)
    python tools/calibrate.py physics     # drive the bar and measure accel/v_max

`physics` takes over the mouse for ~4 s during a real minigame and deliberately
sacrifices that catch to measure your rod's acceleration and top speed, then
prints the config block to paste into config.json.

Bait is tracked in software (you enter the starting amount before F2 in
`run.py`), so there is no bait calibration here.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.capture import Screen, find_game_window          # noqa: E402
from bloxfish.config import Config                             # noqa: E402
from bloxfish.inputs import Mouse                              # noqa: E402
from bloxfish.vision import (                                  # noqa: E402
    charge_meter_score, find_bar, find_bite_marker, read_bar, read_progress,
)


def cmd_window(cfg: Config, screen: Screen) -> None:
    win, _found = find_game_window(cfg.window_title, screen)
    print(f"game window : {win.width}x{win.height} at ({win.left},{win.top})")
    d = cfg.detection
    bar = win.sub(0.0, d.bar_search_top, 1.0, d.bar_search_bottom)
    bite = win.sub(d.bite_left, d.bite_top, d.bite_right, d.bite_bottom)
    print(f"bar search  : {bar.width}x{bar.height} at ({bar.left},{bar.top})")
    print(f"bite ROI    : {bite.width}x{bite.height} at ({bite.left},{bite.top})"
          f"  threshold {bite_threshold(bite, d):.0f}")

    for name, rect in (("bar search", bar), ("bite ROI", bite)):
        screen.grab(rect)
        t0 = time.perf_counter()
        n = 60
        for _ in range(n):
            screen.grab(rect)
        dt = (time.perf_counter() - t0) / n
        print(f"grab {name:11}: {dt*1000:6.2f} ms  ->  {1/dt:6.0f} Hz")


def cmd_meter(cfg: Config, screen: Screen) -> None:
    win, _found = find_game_window(cfg.window_title, screen)
    d = cfg.detection
    roi = win.sub(d.meter_left, d.meter_top, d.meter_right, d.meter_bottom)
    thr = max(30.0, d.meter_min_score_frac * win.height)
    print(f"meter ROI {roi.width}x{roi.height} at ({roi.left},{roi.top}), "
          f"threshold {thr:.0f}")
    print("hold LMB to cast and watch the score jump. Ctrl+C to stop.")
    peak = 0
    try:
        while True:
            s = charge_meter_score(screen.grab(roi))
            peak = max(peak, s)
            flag = "  <<< CHARGING" if s >= thr else ""
            print(f"\rscore {s:6d}   peak {peak:6d}{flag}   ", end="")
            time.sleep(0.03)
    except KeyboardInterrupt:
        print()


def cmd_bite(cfg: Config, screen: Screen) -> None:
    win, _found = find_game_window(cfg.window_title, screen)
    d = cfg.detection
    roi = win.sub(d.bite_left, d.bite_top, d.bite_right, d.bite_bottom)
    print(f"bite ROI {roi.width}x{roi.height} — cast, wait for the '!', watch. "
          "Ctrl+C to stop.")
    try:
        while True:
            m = find_bite_marker(screen.grab(roi), cfg.colors, d,
                                 (roi.left, roi.top))
            if m is None:
                print("\rno marker" + " " * 50, end="")
            else:
                print(f"\rMARKER  bbox {m.w}x{m.h}  area {m.area}  "
                      f"at ({int(m.cx)},{int(m.cy)})        ", end="")
            time.sleep(0.05)
    except KeyboardInterrupt:
        print()


def _acquire_bar(cfg: Config, screen: Screen, win, timeout=60.0):
    d = cfg.detection
    search = win.sub(d.bar_search_left, d.bar_search_top,
                     d.bar_search_right, d.bar_search_bottom)
    print("waiting for a minigame...")
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        geo = find_bar(screen.grab(search), (search.left, search.top),
                       cfg.colors, d, win.width)
        if geo is not None:
            print(f"bar: track x {geo.x0}..{geo.x1} (w={geo.width}), "
                  f"band y {geo.y0}..{geo.y1}, zone {geo.zone_w:.0f} px "
                  f"({geo.zone_w/geo.width:.1%} of track)")
            return geo
        time.sleep(0.03)
    print("no minigame found")
    return None


def cmd_bar(cfg: Config, screen: Screen) -> None:
    win, _found = find_game_window(cfg.window_title, screen)
    geo = _acquire_bar(cfg, screen, win)
    if geo is None:
        return
    zw = geo.zone_w
    try:
        while True:
            st = read_bar(screen.grab(geo.strip), geo, cfg.colors, zw)
            if st is None:
                print("\rbar gone" + " " * 50, end="")
            else:
                z = (st.zone_c - geo.x0) / geo.width
                f = "  --  " if st.fish_c is None else \
                    f"{(st.fish_c - geo.x0) / geo.width:.3f}"
                p = read_progress(screen.grab(geo.prog)) if geo.prog else None
                ps = f"{p:5.1%}" if p is not None else "  -  "
                print(f"\rzone {z:.3f}  fish {f}  width {st.zone_w:5.0f}  "
                      f"progress {ps}   ", end="")
            time.sleep(0.02)
    except KeyboardInterrupt:
        print()


def cmd_physics(cfg: Config, screen: Screen) -> None:
    """Bang the zone left and right and fit the plant.

    Sacrifices one catch. Everything is measured in track widths, so the
    numbers drop straight into config.json regardless of resolution.
    """
    win, _found = find_game_window(cfg.window_title, screen)
    geo = _acquire_bar(cfg, screen, win)
    if geo is None:
        return
    zw = geo.zone_w
    samples: list[tuple[float, float, bool]] = []

    print("measuring (4 s, this catch is sacrificed)...")
    with Mouse() as mouse:
        t0 = time.perf_counter()
        while True:
            t = time.perf_counter() - t0
            if t > 4.0:
                break
            # 0.5 s square wave: long enough to build real velocity, short
            # enough not to slam into the track ends.
            hold = int(t / 0.5) % 2 == 0
            mouse.set(hold)
            st = read_bar(screen.grab(geo.strip), geo, cfg.colors, zw)
            if st is not None:
                samples.append((t, (st.zone_c - geo.x0) / geo.width, hold))
            time.sleep(0.006)

    if len(samples) < 60:
        print("not enough samples (did the minigame end?)")
        return

    ts = np.array([s[0] for s in samples])
    xs = np.array([s[1] for s in samples])
    hs = np.array([s[2] for s in samples])

    # Central-difference velocity, then fit dv/dt over each constant-input run.
    vs = np.gradient(xs, ts)
    accels = {True: [], False: []}
    edges = np.flatnonzero(np.diff(hs.astype(int)) != 0) + 1
    for a, b in zip(np.r_[0, edges], np.r_[edges, len(ts)]):
        if b - a < 15:
            continue
        seg = slice(a + 5, b - 3)          # drop the input-latency transient
        if ts[seg].size < 8:
            continue
        unsat = np.abs(vs[seg]) < np.abs(vs).max() * 0.85
        if unsat.sum() < 6:
            continue
        slope = np.polyfit(ts[seg][unsat], vs[seg][unsat], 1)[0]
        accels[bool(hs[a])].append(slope)

    a_hold = float(np.median(accels[True])) if accels[True] else float("nan")
    a_rel = float(np.median(accels[False])) if accels[False] else float("nan")
    v_max = float(np.percentile(np.abs(vs), 97))

    print()
    print(f"  a_hold    {a_hold:+.3f} track/s^2   (reference +1.92)")
    print(f"  a_release {a_rel:+.3f} track/s^2   (reference -1.92)")
    print(f"  v_max      {v_max:.3f} track/s     (reference  0.545)")
    print(f"  zone width {zw/geo.width:.3f} of track (reference 0.307)")
    accel = float(np.nanmean([abs(a_hold), abs(a_rel)]))
    print("\npaste into config.json:")
    print(json.dumps({"physics": {"accel": round(accel, 3),
                                  "v_max": round(v_max, 3)}}, indent=2))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["window", "bite", "meter", "bar",
                                        "physics"])
    args = ap.parse_args()
    cfg = Config.load()
    screen = Screen()
    try:
        {"window": cmd_window, "bite": cmd_bite, "meter": cmd_meter,
         "bar": cmd_bar, "physics": cmd_physics}[args.command](cfg, screen)
    finally:
        screen.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
