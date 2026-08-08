"""Extract per-frame minigame state from a recording.

Produces the ground-truth traces the mechanics document is based on, and the
recorded fish trajectories that validate_controller.py replays.

    python tools/trace_video.py "<video.mkv>" -o data/fish_tracks.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.capture import Rect                      # noqa: E402
from bloxfish.config import Config                     # noqa: E402
from bloxfish.vision import find_bar, read_bar, read_progress  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("-o", "--out", default="data/fish_tracks.json")
    args = ap.parse_args()

    cfg = Config()
    cap = cv2.VideoCapture(args.video)
    if not cap.isOpened():
        print("could not open", args.video)
        return 1
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 60.0
    search = Rect(0, 0, w, h).sub(0.0, cfg.detection.bar_search_top,
                                  1.0, cfg.detection.bar_search_bottom)

    geo = None
    zw = None
    runs: list[dict] = []
    cur: dict | None = None
    i = -1

    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        sub = frame[search.top:search.bottom, search.left:search.right]
        if geo is None:
            geo = find_bar(sub, (search.left, search.top), cfg.colors, cfg.detection)
            if geo is not None:
                zw = geo.zone_w
        st = None
        if geo is not None:
            st = read_bar(geo.slice_band(frame[geo.full.top:geo.full.bottom,
                                               geo.full.left:geo.full.right]),
                          geo, cfg.colors, zw)
            if st is None:
                geo = None

        if st is None or st.fish_c is None:
            if cur and len(cur["fish"]) > 30:
                runs.append(cur)
            cur = None
            continue

        if cur is None:
            cur = {"start_frame": i, "fps": fps, "track_px": geo.width,
                   "zone_w": geo.zone_w / geo.width, "fish": [], "zone": [],
                   "progress": []}
        cur["fish"].append(round((st.fish_c - geo.x0) / geo.width, 5))
        cur["zone"].append(round((st.zone_c - geo.x0) / geo.width, 5))
        pimg = geo.slice_prog(frame[geo.full.top:geo.full.bottom,
                                    geo.full.left:geo.full.right])
        p = read_progress(pimg) if pimg is not None else None
        cur["progress"].append(None if p is None else round(p, 4))

    if cur and len(cur["fish"]) > 30:
        runs.append(cur)
    cap.release()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(runs), encoding="utf-8")

    print(f"{len(runs)} minigame(s) -> {out}")
    for r in runs:
        n = len(r["fish"])
        fish_w = [abs(r["fish"][k + 1] - r["fish"][k]) for k in range(n - 1)]
        speed = sum(fish_w) / max(1e-9, (n - 1)) * r["fps"]
        print(f"  frame {r['start_frame']:5d}  {n/r['fps']:5.2f} s  "
              f"zone {r['zone_w']:.3f} of track  fish speed {speed:.4f} track/s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
