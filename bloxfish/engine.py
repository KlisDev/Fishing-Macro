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
from .config import Config
from .controller import ReelController
from .inputs import Keyboard, Mouse, valid_slot
from . import shop as shop_mod
from .vision import (
    BarGeometry, charge_meter_score, find_bar, find_bite_marker,
    progress_bar_present, read_bar, read_progress,
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
    bite_timeouts: int = 0
    purchases: int = 0
    sales: int = 0
    started: float = field(default_factory=time.perf_counter)

    def summary(self) -> str:
        mins = (time.perf_counter() - self.started) / 60.0
        rate = self.catches / mins if mins > 0.01 else 0.0
        return (f"casts={self.casts} bites={self.bites} catches={self.catches} "
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
        self.bar_search = self.window.sub(0.0, d.bar_search_top, 1.0, d.bar_search_bottom)
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
        # Session state. `run()` re-anchors these at the start of every session;
        # they are defaulted here so the engine is a valid object before then —
        # the GUI builds one to show settings, and the shop helpers read them.
        self._at_npc = True
        self._shift_lock = False
        self._rod_equipped = True
        self._buy_failures = 0
        self._since_sell = 0

        if not valid_slot(cfg.rod_slot):
            self.log(f"[warn] rod_slot {cfg.rod_slot!r} is not 1-9 or 0 — "
                     f"using 1 instead")
            cfg.rod_slot = "1"

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
        self.log(f"bite marker search over a "
                 f"{self.bite_roi.width}x{self.bite_roi.height} ROI "
                 f"(HSV ring, confirm x{cfg.detection.bite_confirm})")

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
    def _look_for_bar(self) -> BarGeometry | None:
        img = self.screen.grab(self.bar_search)
        return find_bar(img, (self.bar_search.left, self.bar_search.top),
                        self.cfg.colors, self.cfg.detection)

    def _bite_now(self) -> bool:
        img = self.screen.grab(self.bite_roi)
        marker = find_bite_marker(img, self.cfg.colors, self.cfg.detection,
                                  (self.bite_roi.left, self.bite_roi.top))
        return marker is not None

    def _meter_score(self) -> int:
        return charge_meter_score(self.screen.grab(self.meter_roi))

    # -- states ------------------------------------------------------------
    def _minigame_running(self) -> bool:
        """Independent check that a reel minigame is on screen.

        Deliberately does not use `find_bar`. That looks for the green zone
        first, and the player's health bar is also green and also inside the
        search region - so on some layouts it locks onto the health bar and
        reports no minigame for an entire fight. This looks only for the
        progress strip, which nothing else on screen resembles.

        This is the guard that makes "cast while a fish is still on the line"
        impossible, whatever the bar detector happens to be doing.
        """
        try:
            img = self.screen.grab(self.bar_search)
            from .vision import progress_track_mask, _row_span
            pt = progress_track_mask(img)
            need = img.shape[1] * self.cfg.detection.bar_min_width_frac
            for y in range(0, pt.shape[0], 2):
                run = _row_span(pt[y])
                if run and (run[1] - run[0]) >= need:
                    return True
        except Exception:                              # noqa: BLE001
            pass
        return False

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
        attempts = t.max_cast_attempts if t.verify_cast else 1

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
            while self._alive() and time.perf_counter() < deadline:
                if t.verify_cast and self._meter_score() >= self.meter_thr:
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
            self.log(f"[cast] no charge — retrying ({attempt}/{attempts})")
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
        (~0.1 s) cost nothing but reject any one-frame colour fluke. A bar can
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
            return False

        self.state = State.REELING
        self._zone_w_ref = geo.zone_w
        self.controller.reset()
        track_w = float(geo.width)
        fish_half = 0.0
        # control_hz <= 0 means unpaced: the grab already blocks until the next
        # vblank, so adding a sleep on top only risks missing one.
        period = 1.0 / t.control_hz if t.control_hz > 0 else 0.0
        next_tick = time.perf_counter()
        last_progress = None
        ticks = 0
        t0 = time.perf_counter()

        cc = self.cfg.chest
        chest_min_w = int(cc.min_width_frac * track_w) if cc.enabled else 0
        chest_until = 0.0          # while now < this, the zone stays on a chest
        chest_at: float | None = None
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
                timed_out = True
                break

            frame = self.screen.grab(geo.full or geo.strip)
            st = read_bar(geo.slice_band(frame), geo, self.cfg.colors,
                          self._zone_w_ref, chest_min_w,
                          self.cfg.detection.bar_track_min_frac)
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
            elif chest_until:
                chest_until = 0.0
                self.controller.retarget()
                self.log(f"[chest] collected at {chest_at:.2f}, back to the fish")
                chest_at = None
            elif cc.enabled and st.chest_c is not None and len(chest_done) < cc.max_grabs:
                x = (st.chest_c - geo.x0) / track_w
                fresh = all(abs(x - d0) > cc.same_chest_frac for d0 in chest_done)
                affordable = progress is None or progress >= cc.min_progress
                if fresh and affordable:
                    chest_at = x
                    chest_until = now + cc.hold
                    chest_done.append(x)
                    self.controller.retarget()
                    self.log(f"[chest] grabbing at {x:.2f} for {cc.hold:.1f}s")
                    target = x

            d = self.controller.step(now, zone_c, target, zone_half, fish_half)
            self.mouse.set(d.hold)

            # Progress comes from the same grab, so reading it every tick is
            # free. The chest logic needs it to decide whether a detour is
            # affordable.
            pimg = geo.slice_prog(frame)
            p = read_progress(pimg) if pimg is not None else None
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
        self.log(f"[reel] done in {elapsed:.2f} s at {ticks/max(elapsed,1e-3):.0f} Hz "
                 f"(accel est {self.controller.accel:.2f} track/s^2)")
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
