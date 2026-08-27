"""The fishing state machine.

    IDLE ---cast---> WAITING ---"!"---> REELING ---bar gone---> CATCH ---> IDLE

Each state owns its own polling rate: the reel loop runs flat out (~140 Hz)
because control quality depends on it, everything else idles at ~18 Hz.
"""

from __future__ import annotations

import ctypes
import threading
import time
from dataclasses import dataclass, field
from enum import Enum

from .capture import Rect, Screen, find_game_window
from .config import Config, VERSION
from .controller import ReelController
from .inputs import Keyboard, Mouse, valid_slot
from . import shop as shop_mod
from .vision import (
    BarGeometry, charge_meter_score, find_bar, find_bar_by_strip,
    find_bite_marker, progress_bar_present, read_bar, read_progress,
)


class State(Enum):
    IDLE = "idle"
    WAITING = "waiting"
    REELING = "reeling"
    CATCH = "catch"


@dataclass
class Stats:
    casts: int = 0
    bites: int = 0
    catches: int = 0
    missed_bar: int = 0
    escapes: int = 0
    bite_timeouts: int = 0
    purchases: int = 0
    sales: int = 0
    started: float = field(default_factory=time.perf_counter)

    def summary(self) -> str:
        mins = (time.perf_counter() - self.started) / 60.0
        rate = self.catches / mins if mins > 0.01 else 0.0
        return (f"casts={self.casts} bites={self.bites} catches={self.catches} "
                f"escapes={self.escapes} "
                f"missed_bar={self.missed_bar} timeouts={self.bite_timeouts} "
                f"buys={self.purchases} sells={self.sales} "
                f"| {rate:.1f} fish/min over {mins:.1f} min")


def _timer_resolution(ms: int = 1):
    """Ask Windows for a 1 ms scheduler tick so the reel loop can actually
    hit its target rate (the default 15.6 ms tick would cap us at ~64 Hz)."""
    try:
        ctypes.windll.winmm.timeBeginPeriod(ms)
        return lambda: ctypes.windll.winmm.timeEndPeriod(ms)
    except Exception:
        return lambda: None


def _sleep_until(deadline: float) -> None:
    """Sleep most of the way, then spin. Keeps loop jitter under ~0.5 ms."""
    while True:
        remaining = deadline - time.perf_counter()
        if remaining <= 0:
            return
        if remaining > 0.0025:
            time.sleep(remaining - 0.0015)
        else:
            time.sleep(0)


class FishingEngine:
    def __init__(self, cfg: Config, log=print) -> None:
        self.cfg = cfg
        self.log = log
        self.screen = Screen()
        self.mouse = Mouse()
        self.keyboard = Keyboard()
        self.stats = Stats()
        self.state = State.IDLE
        self.running = False
        self._stop = False
        self._end_timer = _timer_resolution()
        self._run_lock = threading.Lock()

        self.window, found = find_game_window(cfg.window_title, self.screen)
        d = cfg.detection
        self.bar_search = self.window.sub(d.bar_search_left, d.bar_search_top,
                                          d.bar_search_right, d.bar_search_bottom)
        # Optional tighter "zone track" region for reading the bar (see
        # Detection.zone_track_on). Always built; used only when enabled. The
        # wide bar_search stays the presence check either way.
        self.zone_track = self.window.sub(d.zone_track_left, d.zone_track_top,
                                          d.zone_track_right, d.zone_track_bottom)
        self.bite_roi = self.window.sub(d.bite_left, d.bite_top, d.bite_right, d.bite_bottom)
        self.meter_roi = self.window.sub(d.meter_left, d.meter_top,
                                         d.meter_right, d.meter_bottom)
        self.meter_thr = max(30.0, d.meter_min_score_frac * self.window.height)
        # Bait is tracked in software, not read off screen: the user enters the
        # starting amount before F2 and it drops by one per catch. None = the
        # user didn't provide a count, so we simply don't track it.
        self.bait_count: int | None = None
        self.controller = ReelController(cfg.physics, cfg.control, cfg.timing)
        self._zone_w_ref: float | None = None
        self._fish_tpl = self._load_fish_template()   # color-independent fallback
        # Session state. `run()` re-anchors these at the start of every session;
        # they are defaulted here so the engine is a valid object before then —
        # the GUI builds one to show settings, and the shop helpers read them.
        self._at_npc = True
        self._shift_lock = False
        self._rod_equipped = True
        self._buy_failures = 0
        self._since_sell = 0
        self._clip_warned = False
        self._zt_warned = False       # warned once that the zone track box is too tight
        self._zt_fallback = False     # latched: read the wide band, not the box

        if not valid_slot(cfg.rod_slot):
            self.log(f"[warn] rod_slot {cfg.rod_slot!r} is not 1-9 or 0 — "
                     f"using 1 instead")
            cfg.rod_slot = "1"

        self.log(f"=== Fishing Macro v{VERSION} ===")
        if found:
            self.log(f"game window {self.window.width}x{self.window.height} "
                     f"at ({self.window.left},{self.window.top})")
        else:
            self.log("*" * 64)
            self.log("COULD NOT FIND THE ROBLOX WINDOW - using the whole screen.")
            self.log("Every box the bot looks at is a fraction of that window, so")
            self.log("if Roblox is windowed, NOTHING will line up: it will not see")
            self.log("the reel bar and will cast while a fish is still hooked.")
            self.log("Fix: make sure Roblox is open and not minimised, then")
            self.log("restart the bot. Fullscreen/borderless is the safest.")
            self.log("*" * 64)
        self.log(f"reel bar search over a "
                 f"{self.bar_search.width}x{self.bar_search.height} box "
                 f"({self.bar_search.width * 100 // max(1, self.window.width)}% "
                 f"of the window wide)")
        # A box cropped tighter than the narrowest bar we would accept can
        # never contain a whole one, so the bot would simply never see the
        # minigame. That is indistinguishable from the game not running, which
        # is the worst possible way for a mis-calibration to present itself.
        need_w = int(self.window.width * cfg.detection.bar_min_width_frac)
        if self.bar_search.width < need_w:
            self.log(f"[warn] the reel bar box is only {self.bar_search.width}px "
                     f"wide; the bar needs at least {need_w}px to be accepted. "
                     f"Widen it in Calibrate controls or the bar will never be "
                     f"found.")
        # Height has to be judged against the window, not in raw pixels. A flat
        # "under 60 px" rule fired on a 1264x704 window where the box was 59 px
        # -- 8.4% of the screen for a bar that measures 6.6%, which was fine and
        # was proven fine: the margin can go to zero on both axes without losing
        # a single frame. Only a box shorter than the bar itself is a problem.
        need_h = int(self.window.height * 0.075)
        if self.bar_search.height < need_h:
            self.log(f"[warn] the reel bar box is only {self.bar_search.height}px "
                     f"tall and the bar itself is about "
                     f"{int(self.window.height * 0.066)}px. It also needs the "
                     f"thin progress strip underneath, which is what identifies "
                     f"the bar at all — give it a little room below.")
        self.log(f"bite marker search over a "
                 f"{self.bite_roi.width}x{self.bite_roi.height} ROI "
                 f"(HSV ring, confirm x{cfg.detection.bite_confirm})")
        # Unlike the reel bar, the charge meter is not pinned to the screen —
        # it hangs beside the character, so it lands somewhere different every
        # cast. Cropping this box tight is therefore the opposite of the right
        # move, and it fails intermittently rather than outright, which makes
        # it very hard to attribute. Measured drift between two casts: 3% of
        # the width, 9% of the height. Anything under a quarter of the screen
        # is asking for a miss.
        if (self.meter_roi.width < self.window.width * 0.20
                or self.meter_roi.height < self.window.height * 0.25):
            self.log(f"[warn] the charge meter box is "
                     f"{self.meter_roi.width * 100 // max(1, self.window.width)}%"
                     f"x{self.meter_roi.height * 100 // max(1, self.window.height)}%"
                     f" of the window. The meter moves with your character "
                     f"between casts, so a box this snug will catch some casts "
                     f"and miss others. Make it bigger in Calibrate controls "
                     f"(the shipped box is 56%x50%).")

    # -- lifecycle ---------------------------------------------------------
    def stop(self) -> None:
        self._stop = True

    def close(self) -> None:
        self.mouse.release()
        self.screen.close()
        self._end_timer()

    def _alive(self) -> bool:
        return not self._stop

    # -- helpers -----------------------------------------------------------
    def _read_region(self):
        """Where the reel bar is found and read from.

        The tighter zone-track box when the user enabled it, else the wide reel
        bar band. Reading from the box pins the track width and keeps out-of-bar
        scenery (dock floor, water) out of the picture; the wide band stays the
        presence check (`_minigame_running`).
        """
        if (self.cfg.detection.zone_track_on
                and not getattr(self, "_zt_fallback", False)):
            return self.zone_track
        return self.bar_search

    def _find_bar_in(self, region) -> BarGeometry | None:
        img = self.screen.grab(region)
        return find_bar(img, (region.left, region.top),
                        self.cfg.colors, self.cfg.detection, self.window.width)

    def _look_for_bar(self) -> BarGeometry | None:
        region = self._read_region()
        geo = self._find_bar_in(region)
        if geo is None and region is not self.bar_search:
            # The zone track box found nothing. Only fall back — and only warn —
            # if the *wider* band does find a bar here: that means the box is too
            # tight (typically cutting off the thin progress strip find_bar needs)
            # rather than there simply being no minigame on screen. A mis-drawn
            # box must never brick the reel with "bar never appeared".
            wide = self._find_bar_in(self.bar_search)
            if wide is not None:
                # A box that clips the bar clips it every frame — a permanent
                # geometry problem, not a transient. Latch the fallback so the
                # rest of the session reads the wide band directly, instead of
                # grabbing twice on every poll (costly on a slow machine, and it
                # would stall the width lock at reel start).
                self._zt_fallback = True
                self._warn_zone_track_fallback()
                self._check_clipped(wide, self.bar_search)
                return wide
        if geo is not None:
            self._check_clipped(geo, region)
        return geo

    def _warn_zone_track_fallback(self) -> None:
        if getattr(self, "_zt_warned", False):
            return
        self._zt_warned = True
        self.log("[warn] the Zone track box did not contain a full reel bar — it "
                 "is probably cutting off the thin progress strip beneath the "
                 "track. Using the wider Reel bar band instead. Redraw it a little "
                 "taller so it includes the progress strip, or untick it in "
                 "Calibrate.")

    def _check_clipped(self, geo: BarGeometry, region=None) -> None:
        """Complain if the search box looks like it is cutting the bar in half.

        Measured on a recording: the box can be pulled in until it hugs the bar
        exactly and the track still reads to the pixel. One notch tighter than
        that and detection does NOT fail — it succeeds, on a clipped track, and
        every position downstream is a fraction of the wrong width. The reel
        then aims at the wrong place for the whole fight. A track running hard
        into the edge of the box is the only warning sign available, so say so
        rather than let it read wrong in silence. (The width lock at reel start
        mitigates this by keeping the widest read, but a box clipped on *every*
        frame still reads short, so the warning stays.)
        """
        if getattr(self, "_clip_warned", False):
            return
        region = region or self.bar_search
        # Name whichever box actually clipped, not merely whichever is enabled:
        # on a zone-track fallback we check the *bar_search* box here.
        box = "Zone track" if region is self.zone_track else "Reel bar band"
        if (geo.x0 <= region.left + 1
                or geo.x1 >= region.right - 1):
            self._clip_warned = True
            self.log("[warn] the reel bar runs right to the edge of the search "
                     "box, so the box may be cutting it short — the bot would "
                     f"then aim against the wrong track width. Widen the '{box}' "
                     "in Calibrate controls until there is a visible gap either "
                     "side of the bar.")

    def _acquire_widest(self, geo: BarGeometry) -> BarGeometry:
        """Return the widest bar read over the next few frames.

        `track_w` is locked once per reel and every controller target is a
        fraction of it, so a single acquisition that caught the track clipped --
        the fish or chest tile sitting over an end of the progress strip when
        `find_bar` measured it -- mis-scales the entire fight. Observed on one 4K
        clip: re-running the finder per frame, the measured width swung from 1117
        to 1763 px. The progress strip is only ever occluded, never drawn wider
        than the real track, so the widest read across a handful of frames is the
        true width. Cheap: a few grabs at the very start of a 5-6 s fight, each
        blocking to the next frame so successive reads see the tile in different
        places.
        """
        best = geo
        seen = 1
        for _ in range(max(1, self.cfg.detection.bar_lock_frames) - 1):
            if not self._alive():
                break
            g = self._look_for_bar()
            if g is not None:
                seen += 1
                if g.width > best.width:
                    best = g
        self._lock_frames_seen = seen
        return best

    def _bite_now(self) -> bool:
        img = self.screen.grab(self.bite_roi)
        marker = find_bite_marker(img, self.cfg.colors, self.cfg.detection,
                                  (self.bite_roi.left, self.bite_roi.top))
        return marker is not None

    def _meter_score(self) -> int:
        return charge_meter_score(self.screen.grab(self.meter_roi))

    def _load_fish_template(self):
        """Load fish_template.png (next to config.json) once, or None.

        Only when `fish_tpl_on` is set. Returns a BGR array read_bar can match.
        """
        if not self.cfg.detection.fish_tpl_on:
            return None
        try:
            import cv2
            from .config import CONFIG_PATH
            path = CONFIG_PATH.parent / "fish_template.png"
            if not path.exists():
                self.log("[warn] fish template is enabled but "
                         "fish_template.png is missing — recapture it in "
                         "Calibrate -> Advanced.")
                return None
            tpl = cv2.imread(str(path))
            if tpl is not None:
                self.log(f"[fish] template fallback on "
                         f"({tpl.shape[1]}x{tpl.shape[0]})")
            return tpl
        except Exception:                              # noqa: BLE001
            return None

    # -- states ------------------------------------------------------------
    def _out_dir(self, name: str):
        """Where diag/record files go: under `--dev`'s capture folder if set,
        else the project's own `diag/` or `record/`."""
        from pathlib import Path as _P
        root = (_P(self.cfg.capture_dir) if self.cfg.capture_dir
                else _P(__file__).resolve().parent.parent)
        out = root / name
        out.mkdir(parents=True, exist_ok=True)
        return out

    def _dump_diag(self, tag: str) -> None:
        """Save the pixels behind a detection failure, for later analysis.

        Guessing at daylight failures from a gameplay video does not work: the
        recording is re-encoded, often rescaled, and never shows what the bot
        actually sampled. This writes the exact regions the detectors read, so a
        bug report carries evidence instead of a description.
        """
        if not self.cfg.diag:
            return
        try:
            import cv2
            out = self._out_dir("diag")
            n = self._diag_n = getattr(self, "_diag_n", 0) + 1
            if n > self.cfg.diag_max:
                return
            stamp = f"{n:03d}_{tag}"
            cv2.imwrite(str(out / f"{stamp}_barsearch.png"),
                        self.screen.grab(self.bar_search))
            cv2.imwrite(str(out / f"{stamp}_bite.png"),
                        self.screen.grab(self.bite_roi))
            cv2.imwrite(str(out / f"{stamp}_meter.png"),
                        self.screen.grab(self.meter_roi))
            self.log(f"[diag] wrote diag/{stamp}_*.png")
        except Exception:                              # noqa: BLE001
            pass

    def _record(self, geo) -> None:
        """Save the regions the detectors read, losslessly, while things work.

        `--diag` only fires on a failure, which is too late for the questions
        that keep coming up. Every color threshold in vision.py is a question
        about what a pixel really is, and a re-encoded gameplay video cannot
        answer one: a tester's track measured (24,32,32) where the author's
        measured (34,34,34), and their recording also renders pure white as
        (248,251,251), so part of that gap is the encoder and part is the
        machine. There is no way to tell which from the video. A PNG of the
        strip is the same bytes the bot sampled, and the strip is small enough
        that a whole session costs a few MB.
        """
        if not self.cfg.record:
            return
        now = time.perf_counter()
        if now - getattr(self, "_rec_at", 0.0) < self.cfg.record_every:
            return
        self._rec_at = now
        n = self._rec_n = getattr(self, "_rec_n", 0) + 1
        if n > self.cfg.record_max:
            return
        try:
            import cv2
            out = self._out_dir("record")
            cv2.imwrite(str(out / f"{n:04d}_strip.png"),
                        self.screen.grab(geo.strip))
            if geo.prog is not None:
                cv2.imwrite(str(out / f"{n:04d}_prog.png"),
                            self.screen.grab(geo.prog))
            if n == 1:
                self.log(f"[record] saving detector input to record/ "
                         f"(every {self.cfg.record_every}s, max "
                         f"{self.cfg.record_max})")
        except Exception:                              # noqa: BLE001
            pass

    def _minigame_running(self) -> bool:
        """Independent check that a reel minigame is on screen.

        Deliberately does not go through the green-zone path. That looks for
        the zone first, and the player's health bar is also green and also
        inside the search region, so on some layouts it locks onto the health
        bar and reports no minigame for an entire fight.

        It used to be cruder than that: any two-tone run wider than
        `bar_min_width_frac` of the window counted, on the reasoning that
        nothing else on screen resembles the progress strip. Something does.
        The level XP bar in the bottom-left corner is two-tone and 0.19 of the
        screen wide, and when that fraction was lowered from 0.25 to 0.18 the
        threshold at 1920 px went from 480 to 345 -- under the XP bar's 365.
        The guard then reported a minigame permanently and the bot never cast
        again: `casts=0`, "a minigame is still running" forever.

        So it now uses the structural finder, which wants a real playfield band
        above the strip and rejects a bare HUD bar. Still independent of the
        green zone; no longer fooled by the corner of the screen.
        """
        try:
            img = self.screen.grab(self.bar_search)
            d = self.cfg.detection
            return find_bar_by_strip(
                img, (self.bar_search.left, self.bar_search.top),
                self.cfg.colors, d,
                int(self.window.width * d.bar_min_width_frac),
                int(self.window.width * d.bar_max_width_frac)) is not None
        except Exception:                              # noqa: BLE001
            return False

    def _meter_miss(self, peak: int) -> None:
        """Once the meter has been missed a few times, name the likely cause.

        Measured across two casts a minute apart on the same character: the
        meter appeared at x 0.408 / y 0.566 of the window for one and x 0.434 /
        y 0.472 for the other — it had moved 3% of the width and 9% of the
        height, while staying exactly the same size. It is drawn beside the
        character in the world, not pinned to the screen, so it lands somewhere
        different every cast. A search box cropped snugly around it therefore
        works for a while and then stops, which is very hard to guess at from
        the outside.
        """
        self._meter_misses = getattr(self, "_meter_misses", 0) + 1
        if self._meter_misses != 3 or getattr(self, "_meter_warned", False):
            return
        self._meter_warned = True
        w_frac = self.meter_roi.width / max(1, self.window.width)
        h_frac = self.meter_roi.height / max(1, self.window.height)
        self.log(f"[warn] the charge meter has been missed three times "
                 f"(last peak {peak}, needs {self.meter_thr:.0f}).")
        if peak >= self.meter_thr * 0.35:
            # It is in the box, just reading short. Nothing about the box will
            # help, so do not send anyone off to resize it.
            self.log("[warn] the meter is being seen but reads short, so the "
                     "box is in the right place. Run with --diag and send the "
                     "diag/*_meter.png files.")
        elif w_frac < 0.20 or h_frac < 0.25:
            self.log(f"[warn] your 'Cast charge meter' box is only "
                     f"{w_frac:.0%}x{h_frac:.0%} of the window. The meter is "
                     f"drawn next to your character and shifts by up to ~3% of "
                     f"the width and ~9% of the height from one cast to the "
                     f"next, so a snug box misses it. Make it bigger in "
                     f"Calibrate controls (the shipped box is 56%x50%).")
        else:
            self.log("[warn] the meter is not in the box at all, but the box "
                     "is a normal size. Check it still covers the middle of "
                     "the screen in Calibrate controls, and run with --diag.")
        self._dump_diag("no_charge")

    def _do_cast(self) -> bool:
        """Charge and release, verifying the cast actually took.

        Right after a catch the first press is often swallowed — by the tail of
        the catch dialog (a record fish adds an extra page) or by the rod not
        being ready yet — and no bait goes out. We watch for the charge meter
        while holding: if it never lights, the press did nothing, so we release
        and try again. A swallowed press simply dismisses whatever ate it, so
        the next attempt casts cleanly. Returns True once a cast is confirmed.
        """
        self.state = State.IDLE
        t = self.cfg.timing
        # max(1, …): a hand-edited or Advanced-cooldowns 0 must not silently
        # stop the bot casting entirely (range(1, 1) is empty).
        attempts = max(1, t.max_cast_attempts) if t.verify_cast else 1

        # Never cast into a live minigame. If the bar detector has lost the bar
        # for any reason, casting here is the "forgot it was fishing" symptom;
        # waiting costs nothing, because a real fight ends on its own.
        if self._minigame_running():
            self.log("[cast] a minigame is still running — not casting")
            deadline = time.perf_counter() + t.max_reel_seconds
            while (self._alive() and time.perf_counter() < deadline
                   and self._minigame_running()):
                time.sleep(0.1)
            return False

        for attempt in range(1, attempts + 1):
            self.mouse.press()
            deadline = time.perf_counter() + t.cast_hold
            charged = False
            peak = 0
            while self._alive() and time.perf_counter() < deadline:
                if t.verify_cast:
                    peak = max(peak, self._meter_score())
                    if peak >= self.meter_thr:
                        charged = True
                        break
                time.sleep(0.008)
            # Hold a beat longer at full charge, then release to actually throw.
            if charged:
                self._sleep(min(0.15, t.cast_hold))
            self.mouse.release()

            if not t.verify_cast or charged:
                self.stats.casts += 1
                extra = "" if attempt == 1 else f" (attempt {attempt})"
                self.log(f"[cast] #{self.stats.casts}{extra}")
                self._sleep(t.cast_settle)
                return True

            if not self._alive():
                break
            # A press that does not charge usually means it went somewhere else.
            # The costly case is being inside the NPC's radius, where the click
            # talks to him instead: retrying then just re-opens the dialogue
            # forever. Close it and step out before trying again.
            if shop_mod.escape_dialogue(self):
                continue
            # Say what was actually seen. "No charge" has two very different
            # causes and the number tells them apart: a peak near zero means
            # the meter is not inside the search box at all (it is drawn beside
            # your character and moves with them, so a box cropped tight around
            # one cast misses the next), while a peak just short of the
            # threshold means the box is right and the bar is simply reading
            # short.
            self.log(f"[cast] no charge — retrying ({attempt}/{attempts}) "
                     f"[meter peaked at {peak}, needs {self.meter_thr:.0f}]")
            self._meter_miss(peak)
            self._sleep(t.cast_retry_gap)

        # Meter never lit. Count it so the summary is honest and let the loop
        # move on; _wait_for_bite will time out and we come back here.
        self.stats.casts += 1
        self.log(f"[cast] #{self.stats.casts} unverified — continuing")
        self._sleep(t.cast_settle)
        return False

    def _wait_for_bite(self) -> bool:
        """Poll for the '!' ring. Returns True once we hook and click one.

        We require the marker to persist for a few consecutive polls before
        acting: the real billboard stays up ~0.9 s, so a couple of confirmations
        (~0.1 s) cost nothing but reject any one-frame color fluke. A bar can
        also appear straight from a bite we polled through, so we watch for that
        too and let the caller reel it.
        """
        self.state = State.WAITING
        t = self.cfg.timing
        confirm_needed = max(1, self.cfg.detection.bite_confirm)
        if t.fast_bite:
            confirm_needed = max(1, t.fast_bite_confirm)
        # "Faster bite reaction": poll harder so the "!" is seen sooner. The
        # confirmation count is untouched — at 60 Hz two polls is ~33 ms, so
        # the false-bite protection costs almost nothing.
        hz = t.fast_bite_scan_hz if t.fast_bite else t.scan_hz
        period = 1.0 / max(1.0, hz)
        deadline = time.perf_counter() + t.max_wait_for_bite
        next_tick = time.perf_counter()
        seen = 0

        while self._alive() and time.perf_counter() < deadline:
            _sleep_until(next_tick)
            next_tick = time.perf_counter() + period

            if self._bite_now():
                seen += 1
                if seen >= confirm_needed:
                    pad = 0.0 if t.fast_bite else t.bite_click_delay
                    if pad:
                        time.sleep(pad)
                    self.mouse.click()
                    self.stats.bites += 1
                    self._dump_diag("bite")
                    self.log("[bite] hooked")
                    self._spend_bait()      # bait is consumed at the bite
                    return True
            else:
                seen = 0

        self.stats.bite_timeouts += 1
        self.log("[bite] timed out, recasting")
        return False

    def _reel(self) -> bool:
        """Drive the minigame. Returns True if we saw it through to the end."""
        t = self.cfg.timing
        geo = None
        deadline = time.perf_counter() + t.bite_to_bar_timeout
        while self._alive() and time.perf_counter() < deadline:
            geo = self._look_for_bar()
            if geo is not None:
                break
            time.sleep(0.02)

        if geo is None:
            self.stats.missed_bar += 1
            self.log("[reel] bar never appeared")
            self._dump_diag("no_bar")
            return False

        # Width lock: the first read may have caught the track clipped by a tile
        # over the progress strip, which would mis-scale every target for the
        # whole reel. Keep the widest read over a few more frames.
        geo = self._acquire_widest(geo)

        self.state = State.REELING
        self._zone_w_ref = geo.zone_w
        self.controller.reset()
        track_w = float(geo.width)
        self.log(f"[reel] track width locked at {int(track_w)} px "
                 f"(widest of {self._lock_frames_seen}"
                 + (", zone track box" if self.cfg.detection.zone_track_on
                    else "") + ")")
        fish_half = 0.0
        # control_hz <= 0 means unpaced: the grab already blocks until the next
        # vblank, so adding a sleep on top only risks missing one.
        period = 1.0 / t.control_hz if t.control_hz > 0 else 0.0
        next_tick = time.perf_counter()
        last_progress = None
        ticks = 0
        # Precision accounting. A bang-bang controller cannot sit still, so a
        # little error is normal; what matters is whether the fish ever leaves
        # the zone (`out_ticks`) and how far it strays (`err_max`). Reporting
        # these turns "it feels less precise" into a number the next recording
        # can confirm or refute.
        err_sum = 0.0
        err_max = 0.0
        out_ticks = 0
        drive_ticks = 0
        t0 = time.perf_counter()

        cc = self.cfg.chest
        chest_min_w = int(cc.min_width_frac * track_w) if cc.enabled else 0
        chest_until = 0.0          # while now < this, the zone stays on a chest
        chest_at: float | None = None
        chest_on = False           # has the zone actually reached the chest?
        chest_since = 0.0          # when the chase started, for the log
        chest_done: list[float] = []
        progress = None
        stalling = False
        lost_since: float | None = None
        flicked = False

        timed_out = False
        while self._alive():
            if period:
                _sleep_until(next_tick)
                next_tick = time.perf_counter() + period
            now = time.perf_counter()
            ticks += 1

            # Hard stop: a real minigame never lasts this long. If we are still
            # here, the detector is latched onto something that is not a live
            # bar; abandon it so the outer loop recasts.
            if now - t0 > t.max_reel_seconds:
                # A real minigame is 5-6 s, so hitting this ceiling means the
                # reel loop was latched onto something that is not a live bar
                # for the whole 12 s -- the "forgets it is fishing" symptom. It
                # has no diag hook (unlike "bar never appeared"), so the pixels
                # it was stuck on were never captured. Grab them now: what sits
                # at the locked strip/prog is the whole answer.
                timed_out = True
                self._dump_diag("stuck_reel")
                break

            frame = self.screen.grab(geo.full or geo.strip)
            st = read_bar(geo.slice_band(frame), geo, self.cfg.colors,
                          self._zone_w_ref, chest_min_w,
                          self.cfg.detection.bar_track_min_frac,
                          self._fish_tpl, self.cfg.detection.fish_tpl_thr)
            if st is None:
                # Time, not frames. The loop is unpaced (~60-140 Hz), so the old
                # 6-frame rule declared the fish caught after ~100 ms of not
                # reading the bar — a single hiccup from lighting, a sprite or a
                # dropped frame was enough. A bar that has really gone stays
                # gone, so give it a proper interval.
                if lost_since is None:
                    lost_since = now
                    # Decide *immediately* whether the bar really went. If the
                    # progress strip went with it the fight is over, and the
                    # flick has to fire now — the card renders within a couple
                    # of frames and the trick stops working once it is up.
                    # Waiting for `bar_lost_seconds` here would be far too late.
                    pimg0 = geo.slice_prog(frame)
                    gone = not (pimg0 is not None and progress_bar_present(
                        pimg0, self.cfg.detection.prog_present_frac))
                    if gone and not flicked and self.cfg.timing.rod_flick:
                        shop_mod.flick_rod(self)
                        flicked = True
                if now - lost_since >= self.cfg.detection.bar_lost_seconds:
                    # Losing the *zone* is not the same as the catch being over.
                    # A chest parked on a small zone hides nearly all of it, and
                    # ending here means clicking and recasting mid-fight — the
                    # "forgot it was fishing" bug. The progress strip is the
                    # honest witness: nothing is ever drawn over it, so if it is
                    # still there, so is the minigame.
                    pimg = geo.slice_prog(frame)
                    strip_up = pimg is not None and progress_bar_present(
                        pimg, self.cfg.detection.prog_present_frac)
                    if strip_up:
                        if not stalling:
                            stalling = True
                            self.log("[reel] zone hidden (chest/alarm) — "
                                     "bar still up, holding on")
                        lost_since = None
                        continue
                    break
                continue
            self._record(geo)
            if stalling:
                stalling = False
                self.log("[reel] zone visible again")
            lost_since = None

            if st.fish_c is None:
                # Fish momentarily unreadable: coast on the last decision rather
                # than jerking the zone somewhere arbitrary.
                continue

            zone_c = (st.zone_c - geo.x0) / track_w
            fish_c = (st.fish_c - geo.x0) / track_w
            zone_half = st.zone_w / track_w / 2
            if st.fish_r is not None:
                fish_half = (st.fish_r - st.fish_l) / track_w / 2

            # -- chests -------------------------------------------------
            # A chest is static, so once one is spotted we park the zone on the
            # remembered spot for a fixed hold and mark it done. We deliberately
            # do not watch the collect animation: the tile whitens while
            # collecting and becomes an open chest afterwards, so re-detecting
            # would lose it mid-grab or re-trigger on the spent tile.
            target = fish_c
            if chest_until and now < chest_until and chest_at is not None:
                target = chest_at
                # The hold has to measure time spent ON the chest, not time
                # since it was spotted. It used to start counting at detection,
                # so the flight out to the tile came out of the 2.5 s -- and
                # the zone accelerates at ~2 track/s^2, so a chest half a track
                # away eats about a second of it. That is why chests were
                # reached and then not collected. Start the clock on arrival.
                if not chest_on and abs(zone_c - chest_at) <= max(zone_half, 0.02):
                    chest_on = True
                    chest_until = now + cc.hold
                    self.log(f"[chest] reached it after {now - chest_since:.1f}s "
                             f"— holding {cc.hold:.1f}s")
            elif chest_until:
                chest_until = 0.0
                self.controller.retarget()
                if chest_on:
                    self.log(f"[chest] collected at {chest_at:.2f}, back to the fish")
                else:
                    # Never actually got there inside the grace window. Say so:
                    # silently "collecting" a chest the zone never touched is
                    # how this looked fine in the log while missing in game.
                    self.log(f"[chest] gave up on {chest_at:.2f} — could not "
                             f"reach it in time, back to the fish")
                chest_at = None
                chest_on = False
            elif cc.enabled and st.chest_c is not None and len(chest_done) < cc.max_grabs:
                x = (st.chest_c - geo.x0) / track_w
                fresh = all(abs(x - d0) > cc.same_chest_frac for d0 in chest_done)
                affordable = progress is None or progress >= cc.min_progress
                if fresh and affordable:
                    chest_at = x
                    chest_on = False
                    chest_since = now
                    # Deadline to *get there*; once there it becomes now + hold.
                    chest_until = now + cc.hold + cc.travel_grace
                    chest_done.append(x)
                    self.controller.retarget()
                    self.log(f"[chest] grabbing at {x:.2f} for {cc.hold:.1f}s")
                    target = x

            d = self.controller.step(now, zone_c, target, zone_half, fish_half)
            self.mouse.set(d.hold)

            # Only score while actually chasing the fish, not during a chest
            # detour (the zone is deliberately off the fish then).
            if not (chest_until and now < chest_until):
                ae = abs(d.error)
                err_sum += ae
                err_max = max(err_max, ae)
                if ae > max(zone_half, 1e-6):
                    out_ticks += 1
                drive_ticks += 1

            # Progress comes from the same grab, so reading it every tick is
            # free. The chest logic needs it to decide whether a detour is
            # affordable.
            pimg = geo.slice_prog(frame)
            p = read_progress(pimg, self.cfg.colors) if pimg is not None else None
            if p is not None:
                progress = p
                if self.cfg.debug and (last_progress is None
                                       or abs(p - last_progress) > 0.02):
                    last_progress = p
                    self.log(f"  progress {p:5.1%} err {d.error*100:+6.2f}% "
                             f"vz {d.v_zone:+.3f} vf {d.v_fish:+.3f} a {self.controller.accel:.2f}")

        self.mouse.release()
        elapsed = time.perf_counter() - t0
        if timed_out:
            self.log(f"[reel] gave up after {elapsed:.1f} s (bar never cleared); "
                     f"recasting")
            return False
        # Losing the bar is not proof of a catch. Confirm before the caller acts
        # on it, otherwise the dismiss clicks land in a live minigame.
        self._flicked = flicked
        if not self._confirm_catch():
            self.log(f"[reel] bar came back after {elapsed:.2f} s — still "
                     f"fishing, not a catch")
            return False
        err_rms = (err_sum / drive_ticks) if drive_ticks else 0.0
        out_pct = (100.0 * out_ticks / drive_ticks) if drive_ticks else 0.0
        # Caught, or did it get away? The bar vanishes either way, so "the reel
        # ended" was being counted as a catch unconditionally -- an escaped fish
        # went in the tally as a success and nothing ever said otherwise. The
        # progress strip is the difference: it climbs to full on a catch and
        # falls back toward empty while the fish is outside the zone, so the
        # last value read before the bar went tells the two apart.
        self._last_progress = progress
        escaped = progress is not None and progress < self.cfg.detection.catch_progress_min
        self._last_escaped = escaped
        self.log(f"[reel] done in {elapsed:.2f} s at {ticks/max(elapsed,1e-3):.0f} Hz "
                 f"(accel est {self.controller.accel:.2f} track/s^2) "
                 f"| err avg {err_rms*100:.1f}% max {err_max*100:.1f}% "
                 f"outside {out_pct:.0f}%"
                 + (f" | progress {progress:.0%}" if progress is not None else ""))
        if escaped:
            self.stats.escapes += 1
            self.log(f"[reel] the fish got away (progress only {progress:.0%}, "
                     f"outside the zone {out_pct:.0f}% of the fight)")
            self._dump_diag("escaped")
        return True

    def _dismiss_catch(self) -> None:
        """Clear the Species/Weight popup, then wait until the screen is clear.

        Clicking on a fixed schedule and hoping was the old approach; it left
        the popup up often enough that the next action — a cast, or the walk to
        the NPC — got eaten by it. Now the clicks are followed by a *check*:
        nothing proceeds until the centre of the screen is actually clear, which
        also catches the rare 'new recipe' note that never fades on its own.
        """
        self.state = State.CATCH
        t = self.cfg.timing

        if t.rod_flick:
            # The fast path. The flick normally already happened the moment the
            # bar vanished (inside the reel loop) — that timing is what makes the
            # card never render. Only do it here if that did not happen.
            if not getattr(self, "_flicked", False):
                shop_mod.flick_rod(self)
            self._flicked = False
        else:
            # Fallback: wait *for* the card, then click it away.
            deadline = time.perf_counter() + t.catch_popup_delay
            while time.perf_counter() < deadline and self._alive():
                if shop_mod.popup_up(self):
                    break
                time.sleep(0.04)
            self.mouse.click()
            self._sleep(t.catch_click_gap)
            self.mouse.click()

        if getattr(self, "_last_escaped", False):
            # Still dismiss and recast -- the screen state is the same -- but do
            # not claim a fish that swam off.
            self.log("[catch] none — that one escaped; recasting")
        else:
            self.stats.catches += 1
            self._since_sell += 1
            self.log(f"[catch] #{self.stats.catches} — recasting")

        # Never wait for a card to fade. With the flick there is none; without
        # it, some ignore the dismiss clicks and sit there on the game's own
        # schedule (a "first one you've found!" card measured 8 s). Casting
        # through one is safe anyway — `verify_cast` re-presses if the card ate
        # the click.
        #
        # The recipe note is the exception: it never leaves on its own. The
        # flick normally beats it to the screen, but a lag spike can let it
        # through, so it still gets checked — one grab, no waiting.
        shop_mod.clear_recipe_note(self)
        if not t.rod_flick:
            self._sleep(t.catch_settle)

    def _confirm_catch(self) -> bool:
        """Positive check that the fight really ended, before acting on it.

        The reel loop stops when it cannot read the bar, and that has proved a
        poor proxy for "caught": chests, the greyed alarm and lighting changes
        all hide the bar mid-fight. Reporting a catch there fires the dismiss
        clicks into a live minigame — the "forgot it was fishing" bug.

        So before believing it, spend the wait we owe the catch card anyway and
        watch for either outcome: the card appearing (it really was a catch), or
        the bar coming back (we never lost the fish). Nothing is clicked here.
        """
        t = self.cfg.timing
        # Short, and *not* card-based: the rod flick deliberately stops the card
        # from ever rendering, so waiting for it would both defeat the trick and
        # never succeed. The bar reappearing is the only signal left that we are
        # still fishing.
        deadline = time.perf_counter() + t.catch_confirm_window
        while time.perf_counter() < deadline and self._alive():
            if self._look_for_bar() is not None:
                return False
            time.sleep(0.03)
        return True

    def _spend_bait(self) -> None:
        """One bait is consumed the moment a bite registers (not on the catch —
        a bite that gets away still costs the bait). Logs what is left."""
        if self.bait_count is None:
            return
        self.bait_count = max(0, self.bait_count - 1)
        n = self.bait_count
        if n <= self.cfg.shop.buy_at:
            self.log(f"[bait] {n} left — buying at {self.cfg.shop.buy_at}")
        elif n <= self.cfg.low_bait_warn:
            self.log(f"[bait] {n} left (low)")
        else:
            self.log(f"[bait] {n} left")

    def _needs_bait(self) -> bool:
        return (self.bait_count is not None
                and self.bait_count <= self.cfg.shop.buy_at)

    def _wait_bar_clear(self) -> None:
        """Block until the reel UI is gone, so a fading bar right after a catch
        is never picked up as a new minigame on the next loop."""
        t = self.cfg.timing
        deadline = time.perf_counter() + t.bar_clear_timeout
        while self._alive() and time.perf_counter() < deadline:
            if self._look_for_bar() is None:
                return
            time.sleep(0.03)

    def _sleep(self, seconds: float) -> None:
        deadline = time.perf_counter() + seconds
        while self._alive() and time.perf_counter() < deadline:
            time.sleep(min(0.02, max(0.0, deadline - time.perf_counter())))

    # -- shop --------------------------------------------------------------
    def _buy_bait(self) -> None:
        """Top the bait back up. Runs the mapped NPC dialogue, then returns to
        fishing stance. A failure here is logged, not fatal — the loop keeps
        fishing on whatever bait is left."""
        cfg = self.cfg.shop
        amount = cfg.bait_per_purchase
        try:
            ok = shop_mod.buy(self, amount)
        except shop_mod.ShopError as exc:
            self.log(f"[shop] {exc}")
            self.bait_count = None          # stop asking every cycle
            return
        if ok:
            self._buy_failures = 0
            step = max(1, cfg.craft_step)
            self.bait_count = (self.bait_count or 0) + step * (
                shop_mod.plus_clicks(amount, step) + 1)
            self.stats.purchases += 1
            self.log(f"[bait] topped up to {self.bait_count}")
            return

        # Only credit bait we actually saw bought. Repeated failures mean the
        # click positions or the NPC state are wrong, and blindly retrying just
        # spams the dialogue, so stop asking and keep fishing on what is left.
        self._buy_failures += 1
        if self._buy_failures >= cfg.max_failures:
            self.log(f"[shop] giving up after {self._buy_failures} failed "
                     f"attempts — check the shop.* positions in config.json. "
                     f"Fishing on without restocking.")
            self.bait_count = None

    def _needs_sell(self) -> bool:
        s = self.cfg.sell
        return (s.enabled and s.every > 0 and self._since_sell >= s.every
                and self.cfg.shop.npc.lower().startswith("f"))

    def _sell_fish(self) -> None:
        """Empty the fish stock at the NPC. Never fatal: a failed sale just
        means we keep fishing with a fuller inventory."""
        try:
            ok = shop_mod.sell(self)
        except shop_mod.ShopError as exc:
            self.log(f"[sell] {exc}")
            self.cfg.sell.enabled = False       # stop asking every cycle
            return
        if ok:
            self.stats.sales += 1
            self._since_sell = 0
        else:
            # Don't retry immediately on every cycle; try again next batch.
            self._since_sell = max(0, self._since_sell - max(1, self.cfg.sell.every // 5))

    # -- main loop ---------------------------------------------------------
    def run(self) -> None:
        # One control loop at a time. The GUI button and the F2 hotkey can both
        # fire, and two loops would fight over the mouse button state.
        if not self._run_lock.acquire(blocking=False):
            self.log("[warn] already running — ignoring duplicate start")
            return
        try:
            self._run_locked()
        finally:
            self._run_lock.release()

    def _run_locked(self) -> None:
        self.running = True
        self._stop = False
        # Count from the start of this session, not from construction, so the
        # fish/min figure is not diluted by however long we sat on the hotkey.
        self.stats.started = time.perf_counter()
        # The user is told to start inside the NPC's range with shift lock OFF.
        # Both are tracked as state: entering the fishing stance steps us out of
        # range and turns shift lock on, and shop.buy() reads them to know
        # whether to walk back and to release the cursor before clicking menus.
        self._at_npc = True
        self._shift_lock = False
        # The checklist requires starting with the rod equipped; the shop stows
        # it before walking (game bug workaround) and takes it back out after.
        self._rod_equipped = True
        self._buy_failures = 0
        self._since_sell = 0
        # Get into casting position: shift lock on, one step off the NPC. This
        # is required — standing in range, a cast click opens the dialogue.
        if self.cfg.shop.enter_stance_on_start:
            shop_mod.enter_fishing_stance(self)
        try:
            while self._alive():
                # One cycle is wrapped so a transient fault (a failed grab, the
                # window moving, a detector hiccup) heals into a recast instead
                # of killing the whole session. Only an explicit stop ends it.
                try:
                    self._cycle()
                except Exception as exc:  # noqa: BLE001 - deliberately broad
                    self.mouse.release()
                    self.log(f"[warn] cycle error: {exc!r} — recovering")
                    self._sleep(self.cfg.timing.error_recovery)
        finally:
            self.mouse.release()
            self.running = False
            self.log(self.stats.summary())

    def _cycle(self) -> None:
        """A single cast -> reel -> catch pass. Always returns to the caller so
        the outer loop can immediately start the next cast."""
        # Recover mid-cycle: if a bar is already up (we were started during a
        # minigame, or a bite resolved faster than we polled) reel it now.
        if self._look_for_bar() is not None:
            if self._reel():
                self._dismiss_catch()
            self._wait_bar_clear()
            return

        # Sell and restock before casting, never mid-cycle.
        if self._needs_sell():
            self._sell_fish()
            if not self._alive():
                return

        # Buying at 1 rather than 0 matters: at zero the game unequips the bait.
        if self._needs_bait():
            self._buy_bait()
            if not self._alive():
                return

        # If the cast could not be verified, don't sit in the long bite wait —
        # loop straight back and cast again (the retry usually lands quickly).
        if not self._do_cast():
            return
        if not self._alive():
            return
        if not self._wait_for_bite():
            return
        if not self._alive():
            return
        if self._reel():
            self._dismiss_catch()
        self._wait_bar_clear()
