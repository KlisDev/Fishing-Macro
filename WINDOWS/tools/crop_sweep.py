"""How tight can the reel bar search box be before the detector loses the bar?

`bar_search_left/right/top/bottom` are now yours to crop, and cropping in is
the point: the health bars and the Power/Mastery strip only ever get mistaken
for the reel bar because they sit inside a full-width box. But a box cropped
past the bar itself is just as broken, and silently so. This measures where
the cliff is, on a real recording, instead of assuming.

It finds the bar once with a generous box to learn where the bar lives, then
re-runs `find_bar` over every frame with the box pulled in to a series of
margins, and reports how often the bar is still found.

    python tools/crop_sweep.py "path/to/recording.mkv"
    python tools/crop_sweep.py <video> --frames 400
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.capture import Rect                       # noqa: E402
from bloxfish.config import Config                      # noqa: E402
from bloxfish.vision import find_bar                    # noqa: E402

# Margin around the bar, as a fraction of the bar's own size. 'full' is the
# shipped full-width box, for comparison.
MARGINS = [None, 1.00, 0.50, 0.25, 0.12, 0.06, 0.03, 0.00, -0.03]


def _frames(video: str, limit: int):
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"could not open {video}")
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    out = []
    while len(out) < limit:
        ok, frame = cap.read()
        if not ok:
            break
        out.append(frame)
    cap.release()
    return out, Rect(0, 0, w, h)


def _box(window: Rect, geo, margin: float | None, cfg) -> Rect:
    d = cfg.detection
    if margin is None:
        return window.sub(0.0, d.bar_search_top, 1.0, d.bar_search_bottom)
    bw = geo.x1 - geo.x0
    bh = geo.full.height
    mx, my = int(bw * margin), int(bh * margin)
    left = max(0, geo.x0 - mx)
    top = max(0, geo.full.top - my)
    right = min(window.width, geo.x1 + mx)
    bottom = min(window.height, geo.full.bottom + my)
    return Rect(left, top, max(1, right - left), max(1, bottom - top))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--frames", type=int, default=300)
    args = ap.parse_args()

    cfg = Config()
    frames, window = _frames(args.video, args.frames)
    print(f"{len(frames)} frames, {window.width}x{window.height}")

    # Locate the bar once, with the shipped box.
    ref = _box(window, None, None, cfg)
    geo = None
    for f in frames:
        sub = f[ref.top:ref.bottom, ref.left:ref.right]
        geo = find_bar(sub, (ref.left, ref.top), cfg.colors, cfg.detection,
                       window.width)
        if geo is not None:
            break
    if geo is None:
        print("no minigame anywhere in these frames — try another recording")
        return 1
    print(f"bar: x {geo.x0}..{geo.x1} (w={geo.x1 - geo.x0}, "
          f"{(geo.x1 - geo.x0) / window.width:.0%} of window), "
          f"y {geo.full.top}..{geo.full.bottom} (h={geo.full.height})\n")

    # Run every crop, keeping what each one saw per frame.
    seen: dict[object, list] = {}
    for m in MARGINS:
        box = _box(window, geo, m, cfg)
        row = []
        for f in frames:
            sub = f[box.top:box.bottom, box.left:box.right]
            row.append(find_bar(sub, (box.left, box.top), cfg.colors,
                                cfg.detection, window.width))
        seen[m] = row

    # Score against the shipped full-width box. Counting hits over all frames
    # would mostly measure how much of the recording has no minigame in it.
    ref = seen[None]
    live = [i for i, g in enumerate(ref) if g is not None]
    dead = [i for i, g in enumerate(ref) if g is None]
    print(f"minigame on screen in {len(live)}/{len(frames)} frames "
          f"(per the shipped box)\n")

    print(f"{'margin':>8}  {'search box':>14}  {'% win':>6}  {'kept':>7}  "
          f"{'same bar':>8}  {'extra':>6}")
    print("-" * 62)
    for m in MARGINS:
        box = _box(window, geo, m, cfg)
        row = seen[m]
        kept = sum(1 for i in live if row[i] is not None)
        same = sum(1 for i in live if row[i] is not None
                   and abs(row[i].x0 - ref[i].x0) <= 4
                   and abs(row[i].x1 - ref[i].x1) <= 4)
        extra = sum(1 for i in dead if row[i] is not None)
        name = "full" if m is None else f"{m:+.0%}"
        print(f"{name:>8}  {box.width:>6}x{box.height:<7}  "
              f"{box.width * 100 // window.width:>5}%  "
              f"{kept / max(1, len(live)):>6.1%}  "
              f"{same / max(1, len(live)):>7.1%}  {extra:>6}")

    print("\nmargin  padding around the bar as a fraction of its own size; "
          "+0% hugs it exactly")
    print("kept    of the frames the full box found a bar in, how many this "
          "one also did")
    print("same    ...and read the same track edges (within 4 px)")
    print("extra   frames the full box saw nothing in but this one did")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
