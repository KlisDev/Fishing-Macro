"""Replay a screen recording through the real detectors.

This is the offline proof that vision.py reads the game correctly. It runs
find_bar / read_bar / find_bite_marker over every frame of a capture and reports what
it saw, so a detector regression shows up without launching the game.

    python tools/replay_video.py "path/to/recording.mkv"
    python tools/replay_video.py <video> --dump out_dir   # annotated frames
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.capture import Rect                       # noqa: E402
from bloxfish.config import Config                      # noqa: E402
from bloxfish.vision import (                           # noqa: E402
    find_bar, read_bar, find_bite_marker,
)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--dump", default=None, help="write annotated frames here")
    ap.add_argument("--every", type=int, default=1)
    args = ap.parse_args()

    cfg = Config()
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print("could not open", args.video)
        return 1

    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    window = Rect(0, 0, w, h)
    search = window.sub(0.0, cfg.detection.bar_search_top,
                        1.0, cfg.detection.bar_search_bottom)
    bite_roi = window.sub(cfg.detection.bite_left, cfg.detection.bite_top,
                          cfg.detection.bite_right, cfg.detection.bite_bottom)
    print(f"video {w}x{h} @{fps:.0f}   "
          f"bite ROI {bite_roi.width}x{bite_roi.height}")

    dump = Path(args.dump) if args.dump else None
    if dump:
        dump.mkdir(parents=True, exist_ok=True)

    geo = None
    zone_w_ref = None
    bar_frames, bite_frames, misses = [], [], []
    fish_lost = 0
    i = -1

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        if i % args.every:
            continue

        sub = frame[search.top:search.bottom, search.left:search.right]
        if geo is None:
            geo = find_bar(sub, (search.left, search.top), cfg.colors, cfg.detection)
            if geo is not None:
                zone_w_ref = geo.zone_w
        state = None
        if geo is not None:
            strip = frame[geo.strip.top:geo.strip.bottom,
                          geo.strip.left:geo.strip.right]
            state = read_bar(strip, geo, cfg.colors, zone_w_ref,
                             int(cfg.chest.min_width_frac * geo.width),
                             cfg.detection.bar_track_min_frac)
            if state is None:
                geo = None
            else:
                bar_frames.append(i)
                if state.fish_c is None:
                    fish_lost += 1
                    misses.append(i)

        roi = frame[bite_roi.top:bite_roi.bottom, bite_roi.left:bite_roi.right]
        if find_bite_marker(roi, cfg.colors, cfg.detection,
                            (bite_roi.left, bite_roi.top)) is not None:
            bite_frames.append(i)

        if dump and state is not None and i % 20 == 0:
            vis = frame.copy()
            cv2.rectangle(vis, (geo.x0, geo.y0), (geo.x1, geo.y1), (255, 255, 0), 3)
            cv2.rectangle(vis, (int(state.zone_l), geo.y0),
                          (int(state.zone_r), geo.y1), (0, 255, 255), 3)
            if state.fish_c is not None:
                cv2.line(vis, (int(state.fish_c), geo.y0 - 40),
                         (int(state.fish_c), geo.y1 + 40), (0, 0, 255), 4)
            cv2.imwrite(str(dump / f"f{i:05d}.jpg"),
                        cv2.resize(vis, (w // 3, h // 3)),
                        [cv2.IMWRITE_JPEG_QUALITY, 85])

    cap.release()

    def blocks(frames, gap=8):
        out, cur = [], []
        for f in frames:
            if cur and f - cur[-1] > gap:
                out.append((cur[0], cur[-1]))
                cur = []
            cur.append(f)
        if cur:
            out.append((cur[0], cur[-1]))
        return out

    print("\nminigames detected:")
    for a, b in blocks(bar_frames):
        print(f"  frames {a:5d} - {b:5d}   {(b-a)/fps:5.2f} s")
    print(f"  total bar frames {len(bar_frames)}, "
          f"fish lost on {fish_lost} ({100*fish_lost/max(1,len(bar_frames)):.1f}%)")

    print("\nbites detected:")
    for a, b in blocks(bite_frames, gap=30):
        print(f"  frames {a:5d} - {b:5d}   ({a/fps:5.2f} s, visible {(b-a)/fps:.2f} s)")
    if misses:
        print(f"\nframes where the fish was not readable: {misses[:20]}"
              f"{' ...' if len(misses) > 20 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
