"""Reading the game state out of pixels.

Two detectors:

  * `find_bar`   — locates the reel minigame bar anywhere in a search region,
                   by colour. Run once when the minigame starts.
  * `read_bar`   — given a located bar, extracts zone / fish / progress from a
                   thin strip. This is the one that runs at 140 Hz.
  * `find_bite_marker` — finds the "!" bite ring by hue + shape.

Nothing here hardcodes a resolution: the bar is found by colour and everything
downstream is expressed relative to the detected track width.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .capture import Rect
from .config import Colors, Detection


# --------------------------------------------------------------------------
# masks
# --------------------------------------------------------------------------

def _split(img: np.ndarray):
    return (img[:, :, 0].astype(np.int16),
            img[:, :, 1].astype(np.int16),
            img[:, :, 2].astype(np.int16))


def _near(img: np.ndarray, ref, tol: int) -> np.ndarray:
    """Pixels within `tol` of the captured BGR `ref` on every channel.

    The opt-in per-machine colour override (Calibrate -> Advanced). Only ever
    used for the stable-coloured elements -- the track background, the chest
    tile, the progress fill -- never the zone or fish, which change colour
    during a fight and stay on their relational masks.
    """
    b, g, r = _split(img)
    return ((np.abs(b - int(ref[0])) <= tol)
            & (np.abs(g - int(ref[1])) <= tol)
            & (np.abs(r - int(ref[2])) <= tol))


def zone_mask(img: np.ndarray, c: Colors) -> np.ndarray:
    """Green player zone — strict, green-dominant only.

    Used to *locate* the bar, where the search region still contains scenery.
    It has to stay strict: a grey dock floor is neutral and bright, and would
    otherwise swamp the search. The zone is always green at the moment a
    minigame starts, so nothing is lost here.
    """
    b, g, r = _split(img)
    return ((g > b + c.zone_g_over_b) & (g > r + c.zone_g_over_r)
            & (g > c.zone_g_min) & (np.abs(b - r) <= c.zone_br_diff_max))


def zone_mask_tracking(img: np.ndarray, c: Colors) -> np.ndarray:
    """Green player zone, in both its normal and alarm looks.

    Used to *track* the zone inside an already-located bar, where the only
    things present are the track, the zone and the tiles — no scenery — so this
    can afford to be permissive.

    It must be. While the fish is out of the zone the game desaturates it, and
    if the fish stays out the fade does not stop at "grey-green": it settles on
    a fully neutral `(89,89,89)`, where green-dominance is exactly zero. The
    strict mask loses the entire zone about a second into any sustained escape,
    which reads as "the bar vanished" and ends the catch early.

    Both looks are neutral between blue and red, so that is the common gate; on
    top of it a pixel qualifies either by being green-dominant (normal zone and
    its pale arrow) or by being neutral and clearly brighter than the empty
    track's `(33,33,33)`. The upper brightness bound keeps near-white sprite
    highlights out of the zone span.
    """
    b, g, r = _split(img)
    neutral = np.abs(b - r) <= c.zone_br_diff_max
    greenish = ((g > b + c.zone_g_over_b) & (g > r + c.zone_g_over_r)
                & (g > c.zone_g_min))
    greyed = ((np.abs(g - b) <= c.zone_neutral_tol)
              & (g >= c.zone_g_min) & (g <= c.zone_neutral_g_max))
    return neutral & (greenish | greyed)


def track_mask(img: np.ndarray, c: Colors) -> np.ndarray:
    """The reel track's dark background.

    Neutral grey close to (34,34,34): the channels must also agree with each
    other, which rejects most of the dark scenery. How closely they have to
    agree is `track_neutral_tol` and it is not a formality — it was hardcoded
    at 6, and on a machine whose track renders (24,32,32) the blue channel sits
    8 below green, so every track pixel failed and coverage read 0.04 instead
    of 0.70. The bar was plainly there; the mask simply could not see it.
    """
    if c.cap_track_on:
        return _near(img, c.cap_track_bgr, c.cap_track_tol)
    b, g, r = _split(img)
    tol = c.track_neutral_tol
    return ((np.abs(b - c.track[0]) <= c.track_tol)
            & (np.abs(b - g) <= tol) & (np.abs(g - r) <= tol))


def fish_mask(img: np.ndarray, c: Colors) -> np.ndarray:
    """Fish tile, teal or alarm-blue, including the sprite drawn on it."""
    b, g, r = _split(img)
    return ((b > c.fish_b_min) & (b > r + c.fish_b_over_r)
            & (g > r + c.fish_g_over_r))


def chest_mask(img: np.ndarray, c: Colors) -> np.ndarray:
    """Gold/amber chest tile on the reel track.

    The chest tile is warm (red>=green>>blue); everything else in the playfield
    is cool or neutral — zone green `(16,150,21)`, fish teal `(133,153,16)`,
    track grey `(33,33,33)` — so a warm-dominant test separates it cleanly and
    cannot be confused with the fish.
    """
    if c.cap_chest_on:
        return _near(img, c.cap_chest_bgr, c.cap_chest_tol)
    b, g, r = _split(img)
    return ((r > c.chest_r_min) & (g > c.chest_g_min) & (b < c.chest_b_max)
            & (r > b + c.chest_r_over_b) & (g > b + c.chest_g_over_b))


def find_chest(strip: np.ndarray, colors: Colors,
               min_width: int) -> tuple[float, float] | None:
    """Column span of the chest tile within a playfield strip, or None.

    Only the *closed* tile needs finding: once a grab starts the engine holds a
    remembered position rather than re-detecting, because the tile turns white
    while collecting and swaps to an open-chest icon afterwards.
    """
    cols = chest_mask(strip, colors).sum(axis=0)
    xs = np.flatnonzero(cols > strip.shape[0] * 0.30)
    if len(xs) < max(3, min_width):
        return None
    return float(xs.min()), float(xs.max())


def progress_mask(img: np.ndarray, c: Colors | None = None) -> np.ndarray:
    """Filled part of the thin progress bar: bright green (85,250,149)->(36,188,52).

    `c` is optional so the many call sites that only ever see the default green
    stay untouched; pass it to honour a per-machine capture of the fill colour.
    The two-tone witness (`progress_track_mask`) is deliberately left on its
    hardcoded numbers -- it is load-bearing and gets its own pass later.
    """
    if c is not None and c.cap_progress_on:
        return _near(img, c.cap_progress_bgr, c.cap_progress_tol)
    b, g, r = _split(img)
    return (g > 150) & (b < 145) & (r < 185)


def progress_track_mask(img: np.ndarray) -> np.ndarray:
    """Filled *and* unfilled progress bar: bright green plus the (19,61,25) rest.

    This is the anchor used to measure the track width, because unlike the
    playfield row it is a single unbroken two-tone strip with no sprite drawn
    over it — one contiguous run spanning exactly the track.
    """
    b, g, r = _split(img)
    return (g > b + 15) & (g > r + 15) & (g > 40)


# --------------------------------------------------------------------------
# bar location
# --------------------------------------------------------------------------

@dataclass
class BarGeometry:
    """Absolute screen coordinates of a located reel bar."""

    x0: int            # track interior, left edge
    x1: int            # track interior, right edge (exclusive)
    y0: int            # zone/fish band, top
    y1: int            # zone/fish band, bottom (exclusive)
    strip: Rect        # playfield band, in absolute screen coords
    zone_w: float      # observed green-zone width in px
    prog: Rect | None = None   # progress bar band, in absolute screen coords
    # One rectangle covering both, so the reel loop needs a single grab per
    # tick. A grab blocks until the next vblank, so a second one would halve
    # the control rate.
    full: Rect | None = None
    band_rows: int = 0          # height of the playfield band inside `full`
    prog_row0: int = 0          # first progress row inside `full`
    prog_rows: int = 0

    def slice_band(self, img: np.ndarray) -> np.ndarray:
        return img[:self.band_rows]

    def slice_prog(self, img: np.ndarray) -> np.ndarray | None:
        if not self.prog_rows:
            return None
        return img[self.prog_row0:self.prog_row0 + self.prog_rows]

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    def norm(self, px: float) -> float:
        """Pixels -> fraction of the track width."""
        return px / self.width

    def px(self, frac: float) -> float:
        return frac * self.width


def progress_bar_present(img: np.ndarray, min_w_frac: float = 0.5) -> bool:
    """True if the thin progress strip is still drawn in `img`.

    `img` is a grab of `geo.prog`. This is the honest witness for "is the
    minigame still running": the strip spans the whole track as one unbroken
    two-tone run with **no sprite ever drawn over it**, which is exactly why
    `find_bar` uses it to measure the track. It is there for as long as the
    catch is, and gone the instant the catch ends.

    The zone is not a safe witness. It can be hidden while the fight is very
    much still on — most obviously with a chest parked on top of it, which on a
    small (beginner) zone covers almost the whole thing.
    """
    pt = progress_track_mask(img)
    if not pt.any():
        return False
    need = int(img.shape[1] * min_w_frac)
    for y in range(pt.shape[0]):
        run = _longest_run(pt[y])
        if run and (run[1] - run[0]) >= need:
            return True
    return False


def _row_span(mask_row: np.ndarray, max_gap: int = 8,
              min_fill: float = 0.55):
    """Extent of a row's True pixels, tolerating small gaps.

    Deliberately *not* "the longest unbroken run". The progress strip is one
    solid two-tone bar in a clean high-resolution capture, but on a smaller
    window, a different UI scale, or anything that has been through video
    compression, it breaks into pieces. Requiring one contiguous run then
    measures only the largest fragment, that falls under the width threshold,
    and the bar is never located at all - which is what left the bot casting in
    the middle of a fight.

    Pixels no more than `max_gap` apart count as the same bar. A group only
    counts if it is at least `min_fill` solid, so scattered specks across the
    row cannot merge into one huge false span.
    """
    xs = np.flatnonzero(mask_row)
    if len(xs) < 2:
        return None
    breaks = np.flatnonzero(np.diff(xs) > max_gap)
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(xs) - 1]))
    best = None
    for a_i, b_i in zip(starts, ends):
        a, b = int(xs[a_i]), int(xs[b_i])
        width = b - a
        if width <= 0:
            continue
        fill = (b_i - a_i + 1) / float(width + 1)
        if fill >= min_fill and (best is None or width > best[1] - best[0]):
            best = (a, b)
    return best


def _longest_run(mask_row: np.ndarray):
    """(start, end) of the longest True run in a 1-D bool array, or None."""
    if not mask_row.any():
        return None
    d = np.diff(np.concatenate(([0], mask_row.view(np.int8), [0])))
    starts = np.flatnonzero(d == 1)
    ends = np.flatnonzero(d == -1)
    i = int(np.argmax(ends - starts))
    return int(starts[i]), int(ends[i])


def find_bar(img: np.ndarray, origin: tuple[int, int],
             colors: Colors, det: Detection,
             ref_width: int | None = None) -> BarGeometry | None:
    """Locate the reel bar inside `img` (a grab of the search region).

    Two-step, because neither cue alone is enough:

      1. A green blob gives a candidate vertical band.
      2. A progress bar directly underneath it confirms the band really is the
         reel bar, and gives the track's width. The playfield row itself is
         useless for width: the fish sprite and the zone's anti-aliased edges
         chop it into fragments.

    **Every** green candidate is tried, not just the biggest one. That matters:
    the player's health bar is green, wide and sits inside the search region, so
    on many layouts it is the largest green blob on screen. Picking by size
    alone locked onto the health bar, found no progress strip beneath it, and
    reported "no minigame" for the entire fight - which left the bot casting
    while a fish was still on the line. The progress strip underneath is the
    thing that actually identifies the bar, so it decides.

    `origin` is the absolute (left, top) of `img`, so the result is in screen
    coordinates. Returns None when no bar is on screen.

    `ref_width` is the game window's width, which is what the width bounds are
    fractions of. Pass it whenever the search box is not the full window width:
    scoring the track against the box instead would mean a box cropped in
    around the bar makes the bar look proportionally wider, until it trips
    `bar_max_width_frac` and the real bar is thrown away.
    """
    h, w = img.shape[:2]
    ref = int(ref_width or w)
    min_w = int(ref * det.bar_min_width_frac)
    max_w = int(ref * det.bar_max_width_frac)

    zone = zone_mask(img, colors).astype(np.uint8)
    n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(zone, 8)
    parts = [stats[i] for i in range(1, n)
             if stats[i, cv2.CC_STAT_HEIGHT] >= 20 and stats[i, cv2.CC_STAT_AREA] >= 800]
    if not parts:
        return find_bar_by_strip(img, origin, colors, det, min_w, max_w)

    ptrack = progress_track_mask(img)
    tmask = track_mask(img, colors)
    fmask = fish_mask(img, colors)

    # Try each candidate band, biggest first. The first one with a real
    # progress bar under it wins.
    seen_bands: list[int] = []
    for cand in sorted(parts, key=lambda s: -s[cv2.CC_STAT_AREA]):
        band_top = int(cand[cv2.CC_STAT_TOP])
        band_bot = band_top + int(cand[cv2.CC_STAT_HEIGHT])
        if any(abs(band_top - b) < 8 for b in seen_bands):
            continue                      # already tried this band
        seen_bands.append(band_top)

        # Every blob sharing this band — the fish tile splits the zone in two.
        same_band = [s for s in parts
                     if abs(int(s[cv2.CC_STAT_TOP]) - band_top) < 12
                     and abs(int(s[cv2.CC_STAT_HEIGHT])
                             - int(cand[cv2.CC_STAT_HEIGHT])) < 12]
        zone_l = min(int(s[cv2.CC_STAT_LEFT]) for s in same_band)
        zone_r = max(int(s[cv2.CC_STAT_LEFT]) + int(s[cv2.CC_STAT_WIDTH])
                     for s in same_band)

        y_top, y_bot = band_top + 3, band_bot - 3
        if y_bot - y_top < 6:
            continue

        # Progress bar directly beneath this band.
        best_run = None
        scan_to = min(h, band_bot + max(8, int((band_bot - band_top) * 0.9)))
        for y in range(band_bot + 1, scan_to):
            run = _row_span(ptrack[y])
            if run and (best_run is None
                        or (run[1] - run[0]) > (best_run[1] - best_run[0])):
                best_run = run

        if best_run is not None and (best_run[1] - best_run[0]) >= min_w:
            x0, x1 = best_run
            prog_rows = [y for y in range(band_bot + 1, min(h, band_bot + 40))
                         if ptrack[y, x0:x1].mean() > 0.55]
        else:
            # No progress bar under this band. Fall back to the dark track on
            # the band's centre row — but only for *this* candidate.
            mid = (y_top + y_bot) // 2
            barish = zone.astype(bool) | tmask | fmask
            run = _row_span(barish[mid])
            if not run or (run[1] - run[0]) < min_w:
                continue                  # not the bar; try the next candidate
            x0, x1 = run
            prog_rows = []

        if zone_l < x0 or zone_r > x1 or (zone_r - zone_l) > (x1 - x0) * 0.9:
            continue
        if not (min_w <= (x1 - x0) <= max_w):
            continue

        ox, oy = origin
        strip = Rect(ox + x0, oy + y_top, max(1, x1 - x0), max(1, y_bot - y_top))
        prog = None
        if prog_rows:
            p0, p1 = min(prog_rows), max(prog_rows) + 1
            prog = Rect(ox + x0, oy + p0, max(1, x1 - x0), max(1, p1 - p0))
        full_top = y_top
        full_bot = max(y_bot, (prog_rows and max(prog_rows) + 1) or y_bot)
        full = Rect(ox + x0, oy + full_top, max(1, x1 - x0),
                    max(1, full_bot - full_top))
        return BarGeometry(
            x0=ox + x0, x1=ox + x1, y0=oy + y_top, y1=oy + y_bot,
            strip=strip, zone_w=float(zone_r - zone_l), prog=prog, full=full,
            band_rows=y_bot - y_top,
            prog_row0=(min(prog_rows) - full_top) if prog_rows else 0,
            prog_rows=(max(prog_rows) + 1 - min(prog_rows)) if prog_rows else 0,
        )

    return find_bar_by_strip(img, origin, colors, det, min_w, max_w)


def find_bar_by_strip(img: np.ndarray, origin: tuple[int, int], colors: Colors,
                       det: Detection, min_w: int,
                       max_w: int) -> BarGeometry | None:
    """Locate the bar from its progress strip upwards, with no green needed.

    The main path starts from a green blob, which quietly assumes the zone is
    green when we go looking. It is not always: while the fish is outside it
    the game desaturates the zone, and a sustained escape fades it to a fully
    neutral grey. Measured on one recording, in the band rows where the bar
    plainly was, the strict `zone_mask` matched **zero** pixels while the
    tracking mask matched 240-327 per row — so `find_bar` had no candidate to
    start from and reported no minigame for 1252 of 1256 frames.

    That is the worst possible time to go blind. The bot arrives here right
    after a bite, and if the zone is grey for the whole `bite_to_bar_timeout`
    it never acquires the bar, gives up, and recasts into a live fight.

    The progress strip has no such problem: nothing is ever drawn over it and
    it does not change colour, which is why it is already the thing that
    *confirms* a candidate. Here it becomes the thing that finds one. Walk up
    from the strip while the rows still look like bar (dark track, zone in
    either state, or fish tile) and that is the band.
    """
    h, w = img.shape[:2]
    ptrack = progress_track_mask(img)
    tmask = track_mask(img, colors)
    zmask = zone_mask_tracking(img, colors)
    fmask = fish_mask(img, colors)
    barish = tmask | zmask | fmask

    # Rows that carry a full-width two-tone run: the strip, and only the strip.
    runs: list[tuple[int, int, int]] = []
    for y in range(h):
        r = _row_span(ptrack[y])
        if r and (r[1] - r[0]) >= min_w:
            runs.append((y, r[0], r[1]))
    if not runs:
        return None

    # Group the rows into strips, then try each from the thickest down.
    groups: list[list[tuple[int, int, int]]] = [[runs[0]]]
    for rec in runs[1:]:
        if rec[0] - groups[-1][-1][0] <= 3:
            groups[-1].append(rec)
        else:
            groups.append([rec])

    for grp in sorted(groups, key=len, reverse=True):
        p0, p1 = grp[0][0], grp[-1][0] + 1
        x0 = min(a for _, a, _ in grp)
        x1 = max(b for _, _, b in grp)
        if not (min_w <= (x1 - x0) <= max_w):
            continue

        # Walk up from the strip for the playfield band. The two are not
        # flush: the bar draws a near-black border between them, measured at
        # (9,7,8) and about 10 rows deep, which is neither track nor zone. So
        # step over the gap to the first bar-like row before walking, rather
        # than treating the border as the end of the bar and giving up on it.
        need = 0.5
        gap_limit = max(12, int(h * 0.05))
        band_bot = None
        for y in range(p0 - 1, max(-1, p0 - gap_limit), -1):
            if barish[y, x0:x1].mean() >= need:
                band_bot = y
                break
        if band_bot is None:
            continue
        y_top = band_bot
        misses = 0
        for y in range(band_bot - 1, -1, -1):
            if barish[y, x0:x1].mean() >= need:
                y_top = y
                misses = 0
            else:
                misses += 1
                if misses >= 4:
                    break
        y_bot = band_bot + 1
        # A real strip has a deep playfield above it; a HUD bar has almost
        # nothing. This is the gate that keeps the fallback off the level XP
        # bar in the bottom-left corner, which is two-tone, ~0.19 of the screen
        # wide and a perfectly good "progress strip" as far as the mask is
        # concerned. Measured band-height / strip-height: 4.4 and 5.1 for real
        # bars on two machines, 0.2 for the XP bar. Without this the fallback
        # reported 102 phantom bars against 19 real ones on one recording --
        # worse than the health-bar confusion it was written to replace.
        prog_h = p1 - p0
        if (y_bot - y_top) < max(10, prog_h * 2):
            continue

        band = zmask[y_top + 3:y_bot - 3, x0:x1]
        if band.size == 0:
            continue
        zcols = np.flatnonzero(band.sum(axis=0) > band.shape[0] * 0.5)
        if len(zcols) < 4:
            continue
        zone_l, zone_r = int(zcols.min()), int(zcols.max())
        if (zone_r - zone_l) > (x1 - x0) * 0.9:
            continue

        ox, oy = origin
        y_t, y_b = y_top + 3, y_bot - 3
        strip = Rect(ox + x0, oy + y_t, max(1, x1 - x0), max(1, y_b - y_t))
        prog = Rect(ox + x0, oy + p0, max(1, x1 - x0), max(1, p1 - p0))
        full = Rect(ox + x0, oy + y_t, max(1, x1 - x0), max(1, p1 - y_t))
        return BarGeometry(
            x0=ox + x0, x1=ox + x1, y0=oy + y_t, y1=oy + y_b,
            strip=strip, zone_w=float(zone_r - zone_l), prog=prog, full=full,
            band_rows=y_b - y_t,
            prog_row0=p0 - y_t,
            prog_rows=p1 - p0,
        )
    return None


@dataclass
class BarState:
    zone_l: float
    zone_r: float
    fish_l: float | None
    fish_r: float | None
    chest_l: float | None = None
    chest_r: float | None = None

    @property
    def zone_c(self) -> float:
        return (self.zone_l + self.zone_r) * 0.5

    @property
    def zone_w(self) -> float:
        return self.zone_r - self.zone_l

    @property
    def fish_c(self) -> float | None:
        if self.fish_l is None:
            return None
        return (self.fish_l + self.fish_r) * 0.5

    @property
    def chest_c(self) -> float | None:
        if self.chest_l is None:
            return None
        return (self.chest_l + self.chest_r) * 0.5


def read_bar(strip: np.ndarray, geo: BarGeometry, colors: Colors,
             zone_w_ref: float | None = None,
             chest_min_w: int = 0,
             track_min_frac: float = 0.0) -> BarState | None:
    """Extract zone, fish and chest spans from a strip grabbed at `geo.strip`.

    The fish and chest tiles are drawn *on top of* the green zone. When one
    straddles an end of the zone it hides that edge, so the zone comes back
    short; we rebuild the missing edge from the reference width.
    """
    rows = strip.shape[0]
    thresh = rows * 0.5

    # Is the bar even still on screen? The zone tracker accepts the neutral-grey
    # alarm state, which a grey dock floor also satisfies, so "I can see a zone"
    # is no longer proof on its own. The track's dark background is: it covers
    # >=55 % of the strip throughout a minigame and collapses once the bar goes.
    if track_min_frac and track_mask(strip, colors).mean() < track_min_frac:
        return None

    zcols = zone_mask_tracking(strip, colors).sum(axis=0)
    fcols = fish_mask(strip, colors).sum(axis=0)

    zx = np.flatnonzero(zcols > thresh)
    if len(zx) < 4:
        return None

    zone_l = float(zx.min())
    zone_r = float(zx.max())

    fx = np.flatnonzero(fcols > rows * 0.25)
    fish_l = fish_r = None
    if len(fx) >= 4:
        fish_l, fish_r = float(fx.min()), float(fx.max())

    chest = find_chest(strip, colors, chest_min_w) if chest_min_w else None
    chest_l, chest_r = chest if chest else (None, None)

    if zone_w_ref and (zone_r - zone_l) < zone_w_ref - 8:
        # A tile is clipping one end of the zone; restore it. Either the fish
        # or the chest can be the occluder.
        occluders = [(a, b) for a, b in ((fish_l, fish_r), (chest_l, chest_r))
                     if a is not None]
        left_hit = any(a <= zone_l + 6 for a, _ in occluders)
        right_hit = any(b >= zone_r - 6 for _, b in occluders)
        if left_hit and not right_hit:
            zone_l = zone_r - zone_w_ref
        elif right_hit and not left_hit:
            zone_r = zone_l + zone_w_ref

    off = geo.strip.left
    return BarState(
        zone_l=zone_l + off,
        zone_r=zone_r + off,
        fish_l=None if fish_l is None else fish_l + off,
        fish_r=None if fish_r is None else fish_r + off,
        chest_l=None if chest_l is None else chest_l + off,
        chest_r=None if chest_r is None else chest_r + off,
    )


def read_progress(img: np.ndarray, c: Colors | None = None) -> float | None:
    """Fraction 0..1 of the thin progress bar. `img` is a grab of geo.prog.

    Starts at 0.50 and reaches 1.0 after ~4.85 s of perfect tracking.
    """
    cols = progress_mask(img, c).sum(axis=0)
    xs = np.flatnonzero(cols > img.shape[0] * 0.3)
    if len(xs) < 3:
        return None
    return float(xs.max() + 1) / max(1, img.shape[1])


# --------------------------------------------------------------------------
# bite marker
# --------------------------------------------------------------------------

@dataclass
class BiteMarker:
    x: int          # bbox, absolute screen coords
    y: int
    w: int
    h: int
    area: int

    @property
    def cx(self) -> float:
        return self.x + self.w / 2

    @property
    def cy(self) -> float:
        return self.y + self.h / 2


def find_bite_marker(img: np.ndarray, colors: Colors, det: Detection,
                     origin: tuple[int, int] = (0, 0)) -> BiteMarker | None:
    """Locate the bite `!` billboard by *shape*, not by a raw colour count.

    The old detector summed pink pixels inside a hard-coded BGR box over a fixed
    rectangle. That broke across characters and cameras for three reasons: the
    marker's on-screen size changes with zoom (so a pixel-count threshold is
    wrong at other scales), its height on screen changes with the character (so
    a fixed ROI clips it), and a narrow BGR box misses the marker whenever
    lighting or graphics settings render it a little lighter.

    The marker itself is a fixed game sprite: a bright **magenta-pink ring with
    an exclamation mark**. Two properties make it identifiable regardless of who
    is fishing or how the camera sits:

      * its hue is magenta-pink (OpenCV H ~= 160-178), which is distinct from a
        pure-red costume item like a candy cane (H ~= 0-8) — so hue in HSV, not
        an RGB box, and it survives brightness changes;
      * it forms one **compact, roughly square blob** (the ring closes up under a
        small morphological close). A character's stray red/pink bits show up as
        scattered specks that never form a blob this size or shape.

    Returns the marker's bounding box (in absolute coords via `origin`), or None.
    Size-relative thresholds keep it resolution independent.
    """
    h, w = img.shape[:2]
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    H, S, V = hsv[:, :, 0], hsv[:, :, 1], hsv[:, :, 2]
    mask = ((H >= colors.bite_hue_lo) & (H <= colors.bite_hue_hi)
            & (S >= colors.bite_sat_min) & (V >= colors.bite_val_min)).astype(np.uint8)

    # Close small gaps so the ring + "!" become one solid blob, without merging
    # distant specks (kernel stays small, ~0.4% of the search width).
    k = max(3, int(round(w * det.bite_close_frac))) | 1
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))

    n, _lbl, stats, _cent = cv2.connectedComponentsWithStats(mask, 8)
    area_floor = det.bite_min_area_frac * (w * h)

    min_dim = det.bite_max_dim_frac * w   # larger side must clear this
    ox, oy = origin
    best = None
    for i in range(1, n):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        # Size is the decisive gate: the marker's ring/"!" is big; player-list
        # icons and colour specks are small.
        if max(bw, bh) < min_dim:
            continue
        if area < area_floor:
            continue
        aspect = bw / max(1, bh)
        if not (det.bite_aspect_lo < aspect < det.bite_aspect_hi):
            continue
        # The ring+! fills a good chunk of its bounding box; specks/streaks do not.
        if area / max(1, bw * bh) < det.bite_fill_min:
            continue
        if best is None or area > best.area:
            best = BiteMarker(ox + x, oy + y, bw, bh, area)
    return best


# --------------------------------------------------------------------------
# cast charge meter
# --------------------------------------------------------------------------

# --------------------------------------------------------------------------
# NPC shop UI
# --------------------------------------------------------------------------

def menu_button_count(img: np.ndarray) -> int:
    """Number of NPC dialogue buttons visible in `img` (a grab of the menu ROI).

    The buttons are light-grey right-aligned panels. Counting them tells us the
    dialogue is up *and* roughly which page we are on (main menu = 4, after
    'Shop' = 3). Used to wait for the dialogue instead of guessing a delay —
    the failure this replaced was clicking 'Shop' 0.3 s before the menu existed.
    """
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    m = ((g > 115) & (g < 210)).astype(np.uint8)
    kx = max(9, (img.shape[1] // 18) | 1)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, kx), np.uint8))
    n, _lbl, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    min_w = img.shape[1] * 0.25
    return sum(1 for i in range(1, n)
               if stats[i][2] > min_w
               and img.shape[0] * 0.04 < stats[i][3] < img.shape[0] * 0.25
               and stats[i][4] > (img.shape[0] * img.shape[1]) * 0.012)


def craft_window_open(img: np.ndarray, min_w_frac: float = 0.25) -> bool:
    """True if the CRAFT window's yellow **Craft button** is in `img`.

    `img` is a grab of the band where that button sits (lower-middle of the
    window). Keying on the button rather than the window's title bar is
    deliberate: the title bar spans the *top-middle* of the screen, which is
    exactly where a terminal or editor tends to sit. In one failing run VS Code
    covered its left half, the bar measured 714 px instead of 1444, and the
    "is the craft window open?" check said no for the whole timeout while the
    window was plainly open.

    The Craft button is low and central — clear of the usual window furniture —
    and it is the thing we are about to click anyway, so seeing it is the right
    precondition. Measured 302-304 px wide against a ~768 px band.
    """
    b, g, r = _split(img)
    m = ((r > 180) & (g > 150) & (b < 110)).astype(np.uint8)
    if not m.any():
        return False
    n, _lbl, stats, _c = cv2.connectedComponentsWithStats(m, 8)
    return any(stats[i][2] > img.shape[1] * min_w_frac for i in range(1, n))


def dialogue_overlay_frac(img: np.ndarray) -> float:
    """How much of `img` is a dialogue panel (0..1).

    Every popup the game puts over the middle of the screen — the
    Species/Weight catch card, the NPC dialogue, the "new recipe" note — is the
    same dark, blue-dominant, very flat panel. Measured over the centre band:
    ~0.57-0.65 while one is up, <0.01 with a clear view of the water.
    """
    b, g, r = _split(img)
    panel = (b > r + 25) & (b < 170) & (g < 120) & (r < 90)
    return float(panel.mean())


def learn_button_present(img: np.ndarray, navy_min: float = 0.45,
                         white_min: float = 0.01) -> bool:
    """True if the recipe note's `Learn` button is in `img`.

    `img` is a grab of the button's ROI. This popup is the one that never times
    out — it sits there until Learn is clicked — so it has to be recognised
    rather than waited out. The button is a navy panel carrying white text;
    requiring both keeps plain scenery from matching.
    """
    b, g, r = _split(img)
    navy = (b > 70) & (b < 160) & (g > 30) & (g < 100) & (r < 70) & (b > r + 45)
    if float(navy.mean()) < navy_min:
        return False
    white = (b > 200) & (g > 200) & (r > 200)
    return float(white.mean()) >= white_min


def charge_meter_score(img: np.ndarray) -> int:
    """Green pixels in the busiest column of `img` — the cast-meter signal.

    While you hold to cast, a thin bright-green bar (~(16,249,31)) fills next to
    the character and stays lit for the whole hold. It is a solid ~13x240 px bar
    (at 4K), so the column it occupies carries ~240 green pixels while nothing
    else in the play area exceeds a handful. Measured: 0 when not charging,
    240-250 while charging. `img` should be a central crop that excludes the
    left/right HUD, whose health/energy bars are also green.
    """
    b, g, r = _split(img)
    m = (g > 150) & (g > b + 80) & (g > r + 80)
    if not m.any():
        return 0
    return int(m.sum(axis=0).max())
