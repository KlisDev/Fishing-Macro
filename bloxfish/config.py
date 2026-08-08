"""Tunable settings for the fishing engine.

Every value here was either measured from the reference recording (see
docs/MECHANICS.md) or is a latency/threshold knob you may want to touch.
Load order: defaults -> config.json next to the project root (if present).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict, is_dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"


@dataclass
class Colors:
    """Colour predicates for the reel bar.

    These are *relational* rather than "reference colour +/- tolerance" on
    purpose. When the fish leaves the zone the game plays an alarm animation:
    the green zone desaturates all the way to (70,86,71) and the fish tile
    shifts from teal (133,153,16) to blue (142,95,24). A fixed-colour match
    goes blind for ~0.3 s exactly when the bot most needs to see. The
    relations below hold through the whole animation (verified frame by frame
    over the flash at frames 2985-3025 of the reference recording).
    """

    track: tuple = (34, 34, 34)
    track_tol: int = 14

    # Green zone. Two accepted looks, both neutral between blue and red:
    #   * green-dominant — the normal zone (16,150,21) and its arrow (193,230,195)
    #   * bright but *fully neutral* — the sustained escape alarm, which decays
    #     all the way to (89,89,89), i.e. g-b = g-r = 0. A green-dominance test
    #     alone loses the zone entirely once the alarm settles; the empty track
    #     is (33,33,33), so brightness is what separates them.
    zone_g_min: int = 62
    zone_g_over_b: int = 10
    zone_g_over_r: int = 10
    zone_br_diff_max: int = 16
    # Neutral (alarm) branch: how far g may sit from b, and how bright it may
    # get. The cap keeps near-white sprite highlights out of the zone span.
    zone_neutral_tol: int = 12
    zone_neutral_g_max: int = 170

    # Fish tile: blue-dominant.
    # normal (133,153,16) ... flash (142,95,24)
    fish_b_min: int = 110
    fish_b_over_r: int = 60
    fish_g_over_r: int = 30

    # Treasure-chest tile: gold/amber, i.e. warm (red>=green>>blue). Everything
    # else on the track is cool or neutral, so this cannot collide with the fish.
    # NOTE: tuned from screenshots, not yet from a recording — see docs.
    chest_r_min: int = 140
    chest_g_min: int = 90
    chest_b_max: int = 130
    chest_r_over_b: int = 60
    chest_g_over_b: int = 20

    # Bite "!" billboard: a bright magenta-pink ring. Detected in HSV (OpenCV
    # ranges: H 0-179, S/V 0-255) so it survives brightness/character changes.
    # Measured from the reference marker: H 160-178, S 109-172, V 195-247. The
    # bands below are widened for tolerance; the magenta hue (>=158) is what
    # keeps a pure-red costume item (candy cane, H ~0-8) from matching.
    bite_hue_lo: int = 158
    bite_hue_hi: int = 179
    # Saturation/value kept low: the marker is a *semi-transparent* glow, so
    # over a bright daytime background (sand, sky) the pink washes out and its
    # saturation drops well below the night value (109-172). Relaxing these is
    # safe now that the tight ROI excludes the player list and no red items are
    # worn — nothing else pink can appear in the box. Validated: no new false
    # positives, and it recovers the washed-out daytime marker.
    bite_sat_min: int = 45
    bite_val_min: int = 110


@dataclass
class Physics:
    """Reel-bar dynamics, in *fractions of the track width* per second.

    Resolution independent: the engine converts to pixels once the bar is
    located. Defaults are the fitted values from the reference recording.
    """

    accel: float = 1.92          # track widths / s^2, both directions
    v_max: float = 0.545         # track widths / s
    # Live re-estimation: the engine measures its own acceleration during a
    # minigame and blends it in, so a different rod/level self-corrects.
    adapt_accel: bool = True
    accel_adapt_rate: float = 0.05


@dataclass
class Timing:
    """Latencies and fixed waits, in seconds."""

    # Screen capture + input round trip, used to extrapolate the world forward
    # before deciding hold/release. A screen grab blocks until the next vblank
    # (DWM throttles BitBlt/DXGI alike), so the frame we act on is up to one
    # refresh old; add the input's own trip through the game and ~45 ms is the
    # right ballpark for a 60 Hz display.
    latency: float = 0.045

    # 0 = unpaced: the reel loop runs as fast as the capture allows. Do not set
    # this to a positive rate unless you know your capture is faster than it --
    # sleeping on top of an already-blocking grab just makes you miss vblanks
    # and halves the real rate.
    control_hz: float = 0.0
    # Bite polling rate. 18 Hz was far too slow: the "!" is sometimes only
    # *detectable* for ~50 ms (measured), and at 55 ms per poll needing two
    # consecutive confirmations the bot could not see it at all — it missed the
    # bite and reacted seconds late. The ROI is small, so polling hard is cheap.
    scan_hz: float = 60.0

    cast_hold: float = 1.20      # LMB hold to charge a full-power cast (~0.5 s to full)
    cast_settle: float = 1.60    # after release, before we start watching for a bite

    # A cast is only trusted once the charge meter is seen while holding. If it
    # never appears the press was swallowed (a lingering catch dialog, or the
    # rod not ready yet right after a catch), so we release and try again. This
    # is what makes the post-catch recast reliable.
    verify_cast: bool = True
    max_cast_attempts: int = 4
    cast_retry_gap: float = 0.45   # wait between attempts

    bite_click_delay: float = 0.05   # reaction padding before clicking the "!"
    # "Faster bite reaction": poll much harder while waiting for the "!" and
    # drop the reaction padding. Costs CPU, so it is opt-in. The two-poll
    # confirmation is kept — at 60 Hz that is ~33 ms, so it stays cheap.
    fast_bite: bool = False
    fast_bite_scan_hz: float = 144.0
    # With the marker sometimes visible for only ~50 ms, a second confirmation
    # can cost more than it protects. Fast mode acts on the first sighting.
    fast_bite_confirm: int = 1
    bite_to_bar_timeout: float = 3.0  # bar should appear ~0.73 s after the click

    max_wait_for_bite: float = 30.0  # user reports up to 20 s; give margin

    # Hard ceiling on one reel. A real minigame is 5-6 s; anything longer means
    # the bar detector is stuck on a false positive (e.g. a lingering catch
    # animation), so we bail out and recast instead of hanging forever. This is
    # the safeguard that guarantees the loop keeps going after every catch.
    max_reel_seconds: float = 12.0

    # Flicking the rod (unequip + re-equip) right after a catch skips the
    # Species/Weight card *entirely* — it never appears, so there is nothing to
    # wait for and nothing to click. Measured on 2026-08-06 17-53-33: catch to
    # next cast in ~1.3 s with no card at any point. Worth ~70% throughput.
    rod_flick: bool = True
    rod_flick_gap: float = 0.08      # between the two key presses; keep it fast
    # "Slower fish trick": some accounts end up holding a glitched fish when the
    # flick is instant. Waiting before the flick, and longer between the two
    # presses, avoids it at the cost of ~1 s per catch.
    slow_rod_flick: bool = False
    rod_flick_slow_delay: float = 0.50   # wait before unequipping
    rod_flick_slow_gap: float = 0.50     # wait between unequip and equip
    rod_flick_settle: float = 0.50   # after re-equipping, before anything else
    # How long to watch for the bar coming back before accepting the catch.
    # Short: the flick has already fired by now, so this only guards against
    # having mistaken a hiccup for the end of the fight.
    catch_confirm_window: float = 0.30

    # Fallback path, used only when rod_flick is off.
    catch_popup_delay: float = 1.60  # bar gone -> "Species/Weight" popup
    catch_click_gap: float = 0.35    # the two dismiss clicks (<0.7 s apart)
    # After dismissing, before recasting. Short on purpose: the catch card may
    # still be fading, and casting through it is safe because `verify_cast`
    # re-presses if the card swallows the click. Waiting it out instead cost
    # ~8 s per catch on cards that ignore the dismiss clicks.
    catch_settle: float = 0.55
    # After a catch, wait until the reel UI is really gone before recasting, so
    # a fading bar is never mistaken for a fresh minigame.
    bar_clear_timeout: float = 3.0

    # After an unexpected error in a cycle, pause this long, then carry on.
    error_recovery: float = 1.0


@dataclass
class Detection:
    """Where and how hard to look."""

    # Reel bar search window, as fractions of the game window.
    bar_search_top: float = 0.45
    bar_search_bottom: float = 0.98
    # The reel track is a fixed-scale Roblox GUI element and measures 0.460 of
    # the window width in every session recorded. Bounding it rejects the
    # look-alikes: green terrain plus yellow HUD buttons can otherwise satisfy
    # the colour tests and produce a "bar" 0.22-0.83 of the window wide, which
    # would send the engine into a phantom minigame and fire stray clicks.
    bar_min_width_frac: float = 0.40
    bar_max_width_frac: float = 0.55

    # Bite marker search window, as fractions of the game window. A tight box
    # around where the '!' rides above the character. This deliberately excludes
    # the screen-edge HUD -- most importantly the top-right player/bounty list,
    # whose red faction row matched the marker hue and caused a false-bite loop.
    # Relies on same-size characters + shift-lock keeping the character centred
    # (a documented usage requirement).
    bite_top: float = 0.16
    bite_bottom: float = 0.60
    bite_left: float = 0.28
    bite_right: float = 0.72
    # Bite marker shape gates (see vision.find_bite_marker). Scale-relative so
    # they hold at any resolution.
    bite_close_frac: float = 0.006     # morphological-close kernel, frac of ROI width
    bite_min_area_frac: float = 6e-4   # blob must be at least this frac of ROI area
    # The marker's ring/"!" is big (~150 px at 4K); a player-list icon or stray
    # speck is small (<=56 px). Require the blob's larger side to clear this
    # fraction of the ROI width -- the single most decisive gate.
    bite_max_dim_frac: float = 0.055
    bite_aspect_lo: float = 0.40       # bbox width/height; the "!" is tall (~0.46)
    bite_aspect_hi: float = 2.30       # ... the wide player-list bar (~3.2) is out
    bite_fill_min: float = 0.15        # blob area / bbox area
    # Consecutive polls the marker must persist before we act. The real marker
    # stays ~0.9 s; this rejects a one-frame speck without missing the window.
    bite_confirm: int = 2

    # How long the bar must stay unreadable before the minigame counts as over.
    # This is a *duration*, not a frame count: the reel loop is unpaced and runs
    # at 60-140 Hz, so the old 6-frame rule fired after ~100 ms — short enough
    # that one hiccup (a lighting change, a sprite, a dropped frame) ended a
    # live fight and fired the dismiss clicks into it. A bar that has genuinely
    # gone stays gone, so a generous interval costs nothing real.
    bar_lost_seconds: float = 0.9
    # Proof the bar is still on screen: this fraction of the strip must still be
    # the track's dark background. Needed because the zone tracker accepts the
    # neutral-grey alarm state, and grey scenery would otherwise read as a zone
    # once the bar is gone. Measured: >=0.553 while a minigame runs, <=0.210 at
    # the 95th percentile once it ends.
    bar_track_min_frac: float = 0.35
    # Before calling a catch finished, confirm the progress strip is really
    # gone. It must span at least this fraction of the track to count as still
    # drawn. Losing the zone alone means nothing — a chest sitting on a small
    # zone hides almost all of it while the fight continues.
    prog_present_frac: float = 0.5

    # Cast charge-meter search window, as fractions of the game window. Central
    # band that excludes the left/right HUD bars. Green pixels in the busiest
    # column above `meter_min_score` (scaled by window height) = charging.
    meter_top: float = 0.35
    meter_bottom: float = 0.85
    meter_left: float = 0.22
    meter_right: float = 0.78
    meter_min_score_frac: float = 0.05   # of window height (~52 px @1080, 108 @2160)


@dataclass
class Control:
    """Controller shaping."""

    # Aim point inside the zone, 0 = zone centre. Positive biases right.
    aim_bias: float = 0.0
    # Velocity estimator window (samples).
    vel_window: int = 5
    # Deadband on the switching function, as a fraction of the track width.
    # Below this the controller PWMs instead of hard switching.
    deadband: float = 0.004
    # Keep the zone this far from the track ends (fraction of track width).
    edge_margin: float = 0.01


@dataclass
class Sell:
    """Selling the fish stock at the Fisherman.

    Route measured from a reference recording: Interact -> `Shop` (menu
    slot 1) -> `Sell Fish` (slot 2) -> `Confirm` (back at slot 1), after which
    the dialogue closes itself. The recording showed $72,935,060 -> $73,214,076
    on one sale.

    The NPC will not buy favourited fish or your heaviest, so nothing needs
    protecting here.
    """

    enabled: bool = True
    every: int = 100                # sell once this many fish have been caught
    after_click: float = 0.7        # between menu clicks
    confirm_timeout: float = 6.0    # wait for the Confirm page, and for the close


@dataclass
class Dialog:
    """Popups that cover the middle of the screen after a catch.

    The bot must not act while one is up: a click meant for the rod goes to the
    popup instead. Most clear themselves in ~1.2 s, but the rare "you found a
    new recipe" note (~0.1 % of catches) waits for a click on **Learn** and
    otherwise blocks the run forever.
    """

    enabled: bool = True
    # Centre band to watch, as fractions of the game window.
    left: float = 0.20
    right: float = 0.80
    top: float = 0.46
    bottom: float = 0.60
    # Panel coverage above this means a popup is up (measured 0.57-0.65 up,
    # <0.01 clear).
    present_frac: float = 0.55
    # How long to wait for one to clear before giving up and carrying on.
    clear_timeout: float = 8.0
    poll: float = 0.06
    # Settle time once the screen is clear, before the next action.
    after_clear: float = 0.35

    # The recipe note's Learn button: ROI to look in, and where to click.
    learn_left: float = 0.660
    learn_right: float = 0.840
    learn_top: float = 0.470
    learn_bottom: float = 0.570
    learn_navy_min: float = 0.45
    learn_white_min: float = 0.01
    learn_click: tuple = (0.7484, 0.5155)   # (1437,532) — centre of the button


@dataclass
class Chest:
    """Treasure chests that appear on the reel track mid-catch.

    Collecting one means parking the zone over it for ~1.5-2 s. The chest does
    not move, and after collection it stays on the bar with an open-chest icon,
    so the engine holds a *remembered* position for a fixed time and marks it
    done rather than trying to read the collect animation (the tile whitens
    while collecting, which would defeat a colour test at exactly the wrong
    moment).
    """

    enabled: bool = True
    # Hold the zone on the chest this long. The mechanic needs 1.5-2 s; 2.5 s
    # is the margin the user asked for.
    hold: float = 2.5
    # Ignore specks: the tile is ~8.7 % of the track, same as the fish.
    min_width_frac: float = 0.035
    # Two sightings within this distance are the same chest (it never moves).
    same_chest_frac: float = 0.03
    # Don't chase a chest if the catch is already in trouble — the fish drains
    # progress at ~0.034/s while we are away, and 2.5 s costs ~0.085.
    min_progress: float = 0.20
    # Safety stop, in case a tile is somehow never marked done.
    max_grabs: int = 4


@dataclass
class Shop:
    """Buying bait from the fishing NPC. See bloxfish/shop.py for the mapping.

    Click positions are fractions of the game window, converted from the
    logical-pixel positions measured in the reference recording.
    """

    npc: str = "fisherman"          # "fisherman", or "none" to skip buying
    bait_per_purchase: int = 20     # must be a multiple of craft_step
    # Buy at 1, not 0: at zero the game unequips the bait, which would break
    # the cast. Bait is spent when a bite registers.
    buy_at: int = 1
    craft_step: int = 10            # CRAFT starts at 10 and each '+' adds 10

    # Click targets, as fractions of the game window. These are the measured
    # *centres* of each button, not wherever the cursor happened to sit in the
    # recording — several of those observed positions were within a pixel or two
    # of a button's top edge.
    center: tuple = (0.5000, 0.5107)        # (960,527) Interact / screen centre
    menu_item1: tuple = (0.7490, 0.5184)    # (1438,535) Shop->Buy Bait->Basic Bait
    menu_item2: tuple = (0.7490, 0.5717)    # (1438,590) second entry, e.g. 'Sell Fish'
    menu_last: tuple = (0.7490, 0.6880)     # (1438,710) 'Back' then 'Nevermind'
    craft_plus: tuple = (0.6365, 0.5407)    # (1222,558) '+' quantity
    craft_button: tuple = (0.5000, 0.6667)  # (960,688)  'Craft'
    craft_close: tuple = (0.6600, 0.2926)   # (1267,302) craft 'Close' (recovery)

    # Where to look to confirm each step actually happened, as fractions of the
    # game window.
    menu_left: float = 0.677
    menu_right: float = 0.823
    menu_top: float = 0.484
    menu_bottom: float = 0.727
    # The CRAFT window is detected by its yellow *Craft button*, not its title
    # bar: the title bar sits top-middle where a terminal/editor often overlaps,
    # and a half-covered bar silently reads as "window not open".
    craft_btn_left: float = 0.40
    craft_btn_right: float = 0.60
    craft_btn_top: float = 0.60
    craft_btn_bottom: float = 0.74
    craft_btn_min_w_frac: float = 0.25
    # The main menu renders progressively and 'Nevermind' is the *last* entry to
    # appear, so we wait for this many buttons before clicking it.
    main_menu_items: int = 4

    # How long to wait for a UI state before giving up. The dialogue took 1.7 s
    # to appear in the failing run (vs 1.0 s in the reference), which is exactly
    # why these are waits-until, not fixed sleeps.
    dialog_timeout: float = 6.0
    craft_timeout: float = 6.0
    close_timeout: float = 4.0

    # Waits, in seconds (measured: menu swaps ~0.5 s).
    after_click: float = 0.6
    after_plus: float = 0.25
    after_shift: float = 0.35
    after_step: float = 0.8
    # Dismissing the dialogue with 'Nevermind' locks the character for ~1.5 s.
    # Walking during that window goes nowhere, which would leave us short of the
    # fishing spot, so wait it out before moving.
    after_nevermind: float = 1.5
    # Let the rod finish being put away / taken back out before moving.
    after_rod: float = 0.45
    walk_tap: float = 0.12          # how long to hold S/W when stepping
    approach_wait: float = 1.2      # after tapping S, before clicking Interact
    # If the dialogue doesn't open, step back again and retry. Out of range the
    # same click charges the rod instead, so this self-corrects.
    max_approach_attempts: int = 3
    poll: float = 0.08              # how often to re-check a UI state

    # Give up on buying after this many consecutive failures.
    max_failures: int = 3

    # On F2, engage shift lock and step forward into casting position. The user
    # is told to start standing at the NPC with shift lock off, so this is what
    # gets them fishing.
    enter_stance_on_start: bool = True


@dataclass
class Config:
    window_title: str = "Roblox"
    colors: Colors = field(default_factory=Colors)
    physics: Physics = field(default_factory=Physics)
    timing: Timing = field(default_factory=Timing)
    detection: Detection = field(default_factory=Detection)
    control: Control = field(default_factory=Control)
    shop: Shop = field(default_factory=Shop)
    chest: Chest = field(default_factory=Chest)
    dialog: Dialog = field(default_factory=Dialog)
    sell: Sell = field(default_factory=Sell)

    start_stop_key: str = "f2"
    quit_key: str = "f4"
    debug: bool = False

    # Hotbar slot holding the fishing rod ('1'-'9' or '0'). Pressing it toggles
    # the rod in and out of the character's hands, which is the known cure for
    # the post-catch stuck state (see docs/MECHANICS.md).
    rod_slot: str = "1"

    # Bait is tracked in software: you enter the starting amount before F2 and
    # it drops by one per catch. Warn once the remaining count is this low (the
    # auto-buy step will hook in here next).
    low_bait_warn: int = 5

    @staticmethod
    def load(path: Path | None = None) -> "Config":
        """Defaults, overlaid with config.json if it is readable.

        Never raises. This file is hand-edited by users and shipped between
        machines, so a truncated write or a stray comma must not stop the bot
        from starting — bad input is ignored and the defaults stand.
        """
        cfg = Config()
        path = path or CONFIG_PATH
        try:
            if not path.exists():
                return cfg
            raw = path.read_text(encoding="utf-8").strip()
            if not raw:
                return cfg
            data = json.loads(raw)
            if isinstance(data, dict):
                _merge(cfg, data)
        except Exception:                       # noqa: BLE001
            # Keep the broken file for the user to look at; carry on defaulted.
            pass
        return cfg

    def save(self, path: Path | None = None) -> None:
        path = path or CONFIG_PATH
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")


def _merge(obj, data: dict) -> None:
    """Overlay `data` onto a dataclass, skipping anything that does not fit.

    Everything here is defensive on purpose: the JSON comes from a file people
    edit by hand. A key of the wrong type, or `null` where a section belongs,
    must leave the default in place rather than poisoning the config with a
    value the engine will later trip over.
    """
    if not isinstance(data, dict):
        return
    for key, value in data.items():
        if not hasattr(obj, key):
            continue
        current = getattr(obj, key)
        if is_dataclass(current):
            _merge(current, value)              # ignores non-dicts, incl. null
        elif isinstance(current, tuple):
            if isinstance(value, (list, tuple)) and len(value) == len(current):
                try:
                    setattr(obj, key, tuple(float(v) for v in value))
                except (TypeError, ValueError):
                    pass
        elif value is None:
            continue                            # never overwrite with null
        elif isinstance(current, bool):
            if isinstance(value, bool):
                setattr(obj, key, value)
        elif isinstance(current, (int, float)):
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                setattr(obj, key, type(current)(value))
        elif isinstance(current, str):
            setattr(obj, key, str(value))
        else:
            setattr(obj, key, value)
