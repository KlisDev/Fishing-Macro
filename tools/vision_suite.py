"""Regression suite for the screen detectors.

`validate_controller` proves the reel controller can catch a fish. Nothing
proved the detectors could still *see* one, so every vision fix so far was
checked by a throwaway script and then forgotten -- which is how a threshold
tuned on one machine survived long enough to cost three bug reports. This runs
the real detectors over recorded gameplay and reports the numbers that actually
predict a failure in the field.

    python tools/vision_suite.py ../bloxfish-clips      # every video in a folder
    python tools/vision_suite.py a.mp4 b.mp4            # named files
    python tools/vision_suite.py <folder> --no-split    # one segment per file
    python tools/vision_suite.py <folder> --csv out.csv

Keep the recordings OUTSIDE the project folder. They are hundreds of MB and
the project gets zipped and sent to testers; they also show usernames, levels
and balances, so they must never travel with it.

Clips that are several recordings concatenated together are split at scene
cuts, so each scenario is scored on its own instead of being averaged into the
others.

THE METRIC THAT MATTERS is `steered`: of the frames where a minigame is
demonstrably on screen, how many did `read_bar` return a state for. Every frame
it does not, the reel loop skips its tick and the zone is not driven -- the
fish drifts out while the bot looks like it is still working. That number was
55% across three machines when this suite was written, and had never been
measured.

Ground truth for "a minigame is on screen" is the progress strip, which is
independent of everything under test here (the zone masks, the track mask, the
width gates). It is not independent of itself: this suite cannot validate
`progress_bar_present`, only everything downstream of it.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bloxfish.capture import Rect                       # noqa: E402
from bloxfish.config import Config                      # noqa: E402
from bloxfish.vision import (                           # noqa: E402
    find_bar, find_bite_marker, progress_bar_present, read_bar, track_mask,
)

VIDEO_EXT = {".mp4", ".mkv", ".mov", ".avi", ".wmv"}

# A segment must be at least this long to be worth scoring on its own.
MIN_SEGMENT = 45

# Pass marks. Deliberately not 100%: a clip that starts or ends mid-minigame
# has a few frames where the bar is genuinely half-drawn.
MIN_STEERED = 0.98
MAX_PHANTOM = 0.02


@dataclass
class Result:
    name: str
    frames: int = 0
    size: str = ""
    live: int = 0
    steered: int = 0
    phantom: int = 0
    bar_seen: int = 0
    track_cov: list = field(default_factory=list)
    widths: set = field(default_factory=set)
    bites: int = 0
    bites_live: int = 0
    acquired: bool = False

    @property
    def steer_rate(self) -> float:
        return self.steered / self.live if self.live else float("nan")

    @property
    def phantom_rate(self) -> float:
        dead = self.frames - self.live
        return self.phantom / dead if dead else 0.0

    def verdict(self) -> str:
        if not self.acquired:
            return "NO BAR"
        if not self.live:
            return "no minigame"
        if self.steer_rate < MIN_STEERED:
            return "FAIL steer"
        if self.phantom_rate > MAX_PHANTOM:
            return "FAIL phantom"
        return "pass"


def find_cuts(path: Path, thresh: float = 26.0) -> list[int]:
    """Frame indices where the picture changes completely.

    Concatenated recordings are the normal case here -- eight scenarios arrive
    as three files. Scoring them as one average hides the very thing the suite
    exists to find, because a scenario that fails on 100% of its own frames is
    only a few percent of the total.
    """
    cap = cv2.VideoCapture(str(path))
    prev = None
    cuts = [0]
    i = -1
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        i += 1
        small = cv2.resize(frame, (64, 36)).astype(np.int16)
        if prev is not None:
            if float(np.abs(small - prev).mean()) > thresh and i - cuts[-1] > MIN_SEGMENT:
                cuts.append(i)
        prev = small
    cap.release()
    cuts.append(i + 1)
    return cuts


def score(path: Path, lo: int, hi: int, cfg: Config, name: str) -> Result:
    """Run the detectors over frames [lo, hi) and tally what they saw."""
    d = cfg.detection
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, lo)
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    window = Rect(0, 0, w, h)
    search = window.sub(d.bar_search_left, d.bar_search_top,
                        d.bar_search_right, d.bar_search_bottom)
    bite = window.sub(d.bite_left, d.bite_top, d.bite_right, d.bite_bottom)
    res = Result(name=name, size=f"{w}x{h}")

    geo = None            # last known bar, kept so prog/strip stay measurable
    for i in range(lo, hi):
        ok, frame = cap.read()
        if not ok:
            break
        res.frames += 1

        sub = frame[search.top:search.bottom, search.left:search.right]
        found = find_bar(sub, (search.left, search.top), cfg.colors, d, w)
        if found is not None and found.prog is not None:
            geo = found
            res.acquired = True
            res.widths.add(round((found.x1 - found.x0) / w, 3))
        if found is not None:
            res.bar_seen += 1

        live = False
        if geo is not None:
            p = frame[geo.prog.top:geo.prog.bottom, geo.prog.left:geo.prog.right]
            if p.size:
                live = progress_bar_present(p, d.prog_present_frac)

            strip = frame[geo.strip.top:geo.strip.bottom,
                          geo.strip.left:geo.strip.right]
            if strip.size:
                st = read_bar(strip, geo, cfg.colors, geo.zone_w, 0,
                              d.bar_track_min_frac)
                if live:
                    res.live += 1
                    res.track_cov.append(float(track_mask(strip, cfg.colors).mean()))
                    if st is not None:
                        res.steered += 1
                elif st is not None:
                    res.phantom += 1

        roi = frame[bite.top:bite.bottom, bite.left:bite.right]
        if roi.size and find_bite_marker(roi, cfg.colors, d,
                                         (bite.left, bite.top)) is not None:
            res.bites += 1
            if live:
                res.bites_live += 1
    cap.release()
    return res


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--no-split", action="store_true",
                    help="score each file whole instead of per scene")
    ap.add_argument("--csv", default=None)
    args = ap.parse_args()

    files: list[Path] = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            files += sorted(q for q in path.iterdir()
                            if q.suffix.lower() in VIDEO_EXT)
        elif path.exists():
            files.append(path)
        else:
            print(f"missing: {p}")
    if not files:
        print("nothing to do")
        return 1

    cfg = Config()
    print(f"track_neutral_tol={cfg.colors.track_neutral_tol}  "
          f"bar_track_min_frac={cfg.detection.bar_track_min_frac}  "
          f"bar width gate {cfg.detection.bar_min_width_frac}"
          f"..{cfg.detection.bar_max_width_frac}\n")
    hdr = (f"{'clip':<34}{'size':>10}{'frames':>7}{'live':>6}"
           f"{'steered':>9}{'phantom':>8}{'track min':>10}{'bites':>6}  verdict")
    print(hdr)
    print("-" * len(hdr))

    results: list[Result] = []
    for f in files:
        cuts = [0, int(cv2.VideoCapture(str(f)).get(cv2.CAP_PROP_FRAME_COUNT))] \
            if args.no_split else find_cuts(f)
        segs = list(zip(cuts, cuts[1:]))
        for n, (lo, hi) in enumerate(segs, 1):
            if hi - lo < MIN_SEGMENT:
                continue
            label = f.stem if len(segs) == 1 else f"{f.stem} #{n}"
            r = score(f, lo, hi, cfg, label)
            results.append(r)
            tmin = f"{min(r.track_cov):.3f}" if r.track_cov else "-"
            steer = f"{r.steer_rate:.1%}" if r.live else "-"
            ph = f"{r.phantom_rate:.1%}" if r.frames > r.live else "-"
            print(f"{label[:33]:<34}{r.size:>10}{r.frames:>7}{r.live:>6}"
                  f"{steer:>9}{ph:>8}{tmin:>10}{r.bites:>6}  {r.verdict()}")

    live = sum(r.live for r in results)
    steered = sum(r.steered for r in results)
    dead = sum(r.frames - r.live for r in results)
    phantom = sum(r.phantom for r in results)
    bad = [r for r in results if r.verdict().startswith(("FAIL", "NO BAR"))]
    print("-" * len(hdr))
    print(f"{len(results)} segments   steered {steered}/{live} "
          f"({steered / max(1, live):.1%})   "
          f"phantom {phantom}/{dead} ({phantom / max(1, dead):.1%})")
    widths = sorted({w for r in results for w in r.widths})
    if widths:
        print(f"bar width seen: {min(widths):.3f}..{max(widths):.3f} of window "
              f"(gate allows {cfg.detection.bar_min_width_frac}"
              f"..{cfg.detection.bar_max_width_frac})")
    if bad:
        print(f"\n{len(bad)} segment(s) need attention:")
        for r in bad:
            print(f"  {r.name}: {r.verdict()}")
    else:
        print("\nall segments pass")

    if args.csv:
        with open(args.csv, "w", encoding="utf-8") as fh:
            fh.write("clip,size,frames,live,steered,phantom,track_min,bites,verdict\n")
            for r in results:
                fh.write(f"{r.name},{r.size},{r.frames},{r.live},{r.steered},"
                         f"{r.phantom},"
                         f"{min(r.track_cov) if r.track_cov else ''},"
                         f"{r.bites},{r.verdict()}\n")
        print(f"wrote {args.csv}")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
