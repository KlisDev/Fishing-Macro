"""Buying bait from a fishing NPC.

The legacy interface was mapped frame-by-frame from two full purchase cycles.
Update 30 keeps the same route and CRAFT window, but its NPC rows form a
bottom-anchored stack whose vertical position changes with page length. The
shop layer therefore finds the currently visible row immediately before each
click and falls back to a user's calibrated legacy point only when that visual
proof is unavailable.

## Path F — Fisherman

Update 30 uses bottom-anchored pages. A row's **position is not its
function**, so the route is expressed as page/action pairs:

    root    : Shop / Fishing Index / Job Stats / Nevermind
    shop    : Buy Bait / Sell Fish / Nevermind
    bait    : Basic Bait / Back
    confirm : Confirm / Nevermind

The purchase path is `root.Shop -> shop.Buy Bait -> bait.Basic Bait`.

The CRAFT window starts at **10** bait for 1000 Money. Each `+` click adds
another **10** (verified: 10 -> 20 took the cost 1000 -> 2000), so buying N bait
is `N/10 - 1` clicks on `+` followed by `Craft`.

Crafting drops you back on the bait menu. The normal exit remains the bottom
visible row twice:

    click visible bottom -> "Back"       -> main menu
    click visible bottom -> "Nevermind"  -> dialogue closes

Recovery verifies the visible page before each exit click. It never walks
through an open or uncertain dialogue.

Closing the dialogue leaves shift lock **off** (the cursor is free again). The
game's interaction push establishes the fishing position, so the macro only
re-engages shift lock; it never tries to cancel that push with `W`.

Observed click positions (logical desktop px at 1920x1080, game window
1920x1032) and what they become as fractions of the window:

    centre / Interact   (960, 527)   -> (0.5000, 0.5107)
    menu item 1         (1415, 513)  -> (0.7370, 0.4971)
    craft  +            (1217, 541)  -> (0.6339, 0.5242)
    craft  Craft        (987, 666)   -> (0.5141, 0.6453)
    menu last           (1346, 675)  -> (0.7010, 0.6541)

"""

from __future__ import annotations

import time
from collections.abc import Callable

from .capture import Rect
from .routes import npc_route_enabled
from .debug import DEBUG
from .inputs import SC_LSHIFT, SC_S, digit_scan
from .vision import (
    craft_window_open, learn_button_present,
    find_falling_menu_buttons, update30_dialogue_present,
)


class ShopError(RuntimeError):
    pass


# Keep this as data rather than relying on "top" and "second" comments at
# every call site. The live detector supplies the actual current coordinates;
# these values only describe what that row means on the page the route expects.
MENU_ACTION_ROWS: dict[str, dict[str, int]] = {
    "root": {
        "shop": 0,
        "fishing_index": 1,
        "job_stats": 2,
        "nevermind": -1,
    },
    "shop": {
        "buy_bait": 0,
        "sell_fish": 1,
        "nevermind": -1,
    },
    "bait": {
        "basic_bait": 0,
        "back": -1,
    },
    "confirm": {
        "confirm": 0,
        "nevermind": -1,
    },
}

# A named action is safe only after the page that owns it has finished falling.
# These counts are UI structure, not a mapping from count to meaning: e.g. the
# root's four rows and Shop's three rows deliberately keep separate names.
MENU_PAGE_ROWS = {
    "root": 4,
    "shop": 3,
    "bait": 2,
    "confirm": 2,
}


def _abs(window, frac) -> tuple[int, int]:
    """Fraction of the game window -> absolute desktop pixel."""
    fx, fy = frac
    return (window.left + int(round(window.width * fx)),
            window.top + int(round(window.height * fy)))


def plus_clicks(amount: int, step: int) -> int:
    """How many `+` presses to go from the default `step` up to `amount`."""
    return max(0, (amount // step) - 1)


def _menu_roi(engine) -> Rect:
    c = engine.cfg.shop
    return engine.window.sub(c.menu_left, c.menu_top, c.menu_right, c.menu_bottom)


def _craft_roi(engine) -> Rect:
    c = engine.cfg.shop
    return engine.window.sub(c.craft_btn_left, c.craft_btn_top,
                             c.craft_btn_right, c.craft_btn_bottom)


def menu_items(engine) -> int:
    """Count current action rows, never scenery from the old grey-band scan.

    Optional catch-header color sampling is not a UI-version selector. The
    shipped game uses Update 30 with or without that sample, and a closed menu
    must not become a phantom page just because the sample is disabled.
    """
    return len(_falling_menu_buttons(engine))


def _coherent_menu_stack(buttons: list[tuple[int, int]], roi: Rect) -> list[tuple[int, int]]:
    """Accept only the vertically spaced rows of a real NPC action stack.

    The live finder correctly locates the bright text/panel ingredients, but a
    pair of unrelated highlights can occur inside a generously calibrated menu
    region.  In recording 0909(17), the ocean/character UI produced two marks
    only 27--42 px apart; the previous ``len >= 2`` check called that an open
    dialogue and prevented an otherwise necessary range probe.  Real Update 30
    rows are a short, evenly separated vertical stack.  This is a *witness*
    filter, not a click target: uncertain marks become no rows and never block
    buying or selling.
    """
    if len(buttons) < 2:
        return []
    ordered = sorted(buttons, key=lambda point: point[1])
    # Label lengths shift the clickable medians modestly, but two highlights
    # on opposite sides of the envelope are not one aligned button stack.
    if max(x for x, _y in ordered) - min(x for x, _y in ordered) > max(24, roi.width * 0.30):
        return []
    # Roblox's row gap is a physical UI measurement (about 80 px in the
    # supplied 1080p footage), not a fraction of the user's search box.  A
    # fixed lower bound keeps an intentionally roomy/full-window calibration
    # from rejecting a legitimate stack.
    min_gap = 48
    max_gap = max(min_gap + 1, int(roi.height * 0.45))
    gaps = [later[1] - earlier[1]
            for earlier, later in zip(ordered, ordered[1:])]
    if not all(min_gap <= gap <= max_gap for gap in gaps):
        return []
    return ordered


def _falling_menu_buttons(engine, *, strict: bool = False):
    """Find the live stack inside its calibrated full-page envelope.

    The menu area scopes live detection as well as the older fixed fallback:
    text from a terminal, player list, or debug overlay elsewhere in a wide/4K
    window must never become a phantom menu row. Calibrate it to include all
    four root rows and the lower two-row bait page.
    """
    try:
        roi = _menu_roi(engine)
        found = find_falling_menu_buttons(engine.screen.grab(roi))
        buttons = [(roi.left + int(round(button.x)),
                    roi.top + int(round(button.y)))
                   for button in found]
        # Windows and Sober both use a physical, calibrated screen-space menu.
        # Apply the same false-positive filter before either platform decides
        # that a dialogue is open or clicks a named action.
        return (_coherent_menu_stack(buttons, roi)
                if interaction_safety_guard() else buttons)
    except Exception:
        if strict:
            raise
        return []


def action_menu_open(engine) -> bool:
    """Whether the live Update 30 action-row stack is visibly present.

    This deliberately does not use ``menu_button_count``. Its legacy
    threshold can count the Fisherman's post-sale result strip as two broad
    bands even though the clickable Confirm / Nevermind stack has gone. It is
    therefore the right completion witness immediately *after* Confirm.
    """
    return len(_falling_menu_buttons(engine)) >= 2


def click_menu_item(engine, index: int, name: str = "menu button") -> None:
    """Click an NPC menu item by live stack position, with legacy fallback.

    Update 30 makes a two-item page fall below the four-item root page, so the
    old saved top-button point can be empty. The detector gives us the visible
    row immediately before every click. If it cannot prove a stack is present,
    preserve the user's calibrated target instead of guessing.
    """
    # A page proof is valid only until this click can change the menu. Clear it
    # here, then let the destination page establish one new proof.
    _clear_menu_page_witness(engine)
    buttons = _falling_menu_buttons(engine)
    if buttons:
        chosen = index if index >= 0 else len(buttons) + index
        if 0 <= chosen < len(buttons):
            x, y = buttons[chosen]
            DEBUG.click(f"{name} (live stack)", x, y)
            engine.mouse.click_at(x, y)
            return

    cfg = engine.cfg.shop
    if index == 0:
        frac = cfg.menu_item1
    elif index == 1:
        frac = cfg.menu_item2
    elif index == 2:
        frac = cfg.menu_item3
    elif index == -1:
        frac = cfg.menu_last
    else:
        # An unknown page must use the escape target rather than a random row.
        frac = cfg.menu_last
    x, y = _abs(engine.window, frac)
    DEBUG.click(f"{name} (calibrated)", x, y)
    engine.mouse.click_at(x, y)


def click_menu_action(engine, page: str, action: str) -> bool:
    """Click a named Update 30 menu action on its expected page.

    This names the role before converting it into the page-local visible row.
    A second row is therefore `root.fishing_index` on the root page and
    `shop.sell_fish` after Shop was chosen.
    """
    try:
        index = MENU_ACTION_ROWS[page][action]
    except KeyError as exc:
        raise ShopError(f"unknown menu action {page}.{action}") from exc
    prior = getattr(engine, "_menu_page_witness", None)
    if prior is None:
        # Preserve the low-level calibrated action API. Production routes
        # obtain a settled page witness before entering this function.
        click_menu_item(engine, index, f"{page}.{action}")
        return True
    signature = _menu_page_signature(engine)
    if (prior[0] != page or signature is None
            or len(signature) != MENU_PAGE_ROWS[page]
            or not _same_menu_page_signature(signature, prior[1])):
        _clear_menu_page_witness(engine)
        DEBUG.event("menu", "changed before click; skipped", page=page, action=action)
        return False
    # Use the very observation we verified. Recapturing inside click_menu_item
    # could silently target a different page after a delayed transition.
    x, y = signature[index]
    _clear_menu_page_witness(engine)
    DEBUG.click(f"{page}.{action} (live stack)", x, y)
    engine.mouse.click_at(x, y)
    return True


def craft_up(engine) -> bool:
    return craft_window_open(engine.screen.grab(_craft_roi(engine)),
                             engine.cfg.shop.craft_btn_min_w_frac,
                             engine.cfg.colors)


def interaction_safety_guard() -> bool:
    """Whether the active supported runtime must prove interaction state.

    Windows and Sober/X11 both expose a real desktop cursor position and send
    scan-code input.  They therefore share the same safety contract: a visible
    dialogue prevents movement, and automatic NPC fishing routes start only
    after Shift Lock's cursor snap is observed. Missing observations fail closed.
    """
    return True


def windows_interaction_guard() -> bool:
    """Compatibility alias for integrations that used the former name."""
    return interaction_safety_guard()


def _shift_lock_cursor_position(engine) -> tuple[int, int] | None:
    """Return the physical cursor position when the backend can observe it."""
    position = getattr(engine.mouse, "position", None)
    if not callable(position):
        return None
    try:
        x, y = position()
        return int(x), int(y)
    except Exception:                              # noqa: BLE001
        return None


def _shift_lock_geometry(engine) -> tuple[int, int, int]:
    """Desktop centre and tolerance, independent of the Interact click point."""
    window = engine.window
    cx, cy = _abs(window, (0.5, 0.5))
    tolerance = max(24, int(min(window.width, window.height) * 0.03))
    return cx, cy, tolerance


def _shift_lock_centered(engine, position: tuple[int, int] | None = None) -> bool:
    """Verify Roblox captured Shift Lock by observing the OS cursor centre.

    The game pins the physical cursor when Shift Lock is accepted.  This is a
    usable postcondition on Windows and Sober/X11; merely remembering that we
    sent Left Shift is not proof that Roblox received it. A failed check is
    deliberately a safe no-cast condition, not a reason to blind-toggle a
    second time.
    """
    if not interaction_safety_guard():
        return True
    point = position if position is not None else _shift_lock_cursor_position(engine)
    if point is None:
        return False
    try:
        x, y = point
        cx, cy, tolerance = _shift_lock_geometry(engine)
        # A 3%-of-window envelope accepts Windows' DPI/input rounding while
        # remaining far tighter than the distance from any dialogue button.
        return abs(x - cx) <= tolerance and abs(y - cy) <= tolerance
    except Exception:                              # noqa: BLE001
        return False


def set_shift_lock(engine, on: bool) -> bool:
    """Drive shift lock to a known state instead of blind-toggling it.

    Shift lock is a *toggle*, so firing Left Shift without knowing the current
    state is a coin flip. The two states the bot needs are opposite:

      * **fishing** wants it ON  — the cursor pins to centre, which is where the
        cast/bite clicks have to land;
      * **dialogue** wants it OFF — with it on the cursor cannot leave centre,
        so every menu click is stuck on the middle of the screen.

    The user is told to start with it off, which anchors the tracking.
    """
    if engine._shift_lock == on:
        return (not on or getattr(engine, "_shift_lock_verified", False)
                or not interaction_safety_guard())
    # Roblox sometimes drops an ultra-short modifier tap while it is releasing
    # a dialogue. Both supported runtimes use this measured scan-code press.
    # Looking only at the cursor *after* Shift was tapped produced a false
    # positive: the preceding Interact click itself happens at the screen
    # centre, so a rejected Shift press could look successful. Both supported
    # backends require an observed snap from away from centre to centre.
    # If it cannot be proved, do not cast with a free cursor.
    before = (_shift_lock_cursor_position(engine)
              if on and interaction_safety_guard() else None)
    engine.keyboard.tap(SC_LSHIFT, 0.10)
    engine._shift_lock = on
    engine._sleep(engine.cfg.shop.after_shift)
    if not on:
        engine._shift_lock_verified = True
        return True
    after = _shift_lock_cursor_position(engine)
    verified = (before is not None and after is not None
                and _shift_lock_centered(engine, after)
                and not _shift_lock_centered(engine, before))
    engine._shift_lock_verified = verified
    if verified:
        engine.log("[input] Shift Lock ON — centre cursor confirmed")
        return True
    # Do not let an unreceived toggle turn into a stale internal "on" state.
    engine._shift_lock = False
    if before is None or after is None:
        reason = "the cursor position could not be read"
    elif _shift_lock_centered(engine, before):
        reason = "the cursor was already centred before the tap"
    else:
        reason = "the cursor did not snap to centre"
    cx, cy, tolerance = _shift_lock_geometry(engine)
    engine.log(f"[input] Could not verify Shift Lock ({reason}) — "
               f"before={before}, after={after}, expected=({cx}, {cy}), "
               f"tolerance={tolerance}px; refusing to fish. "
               "Check Roblox focus and Shift Lock Switch in Settings; "
               "leave the current lock OFF before F2, then press F2 again. "
               "The Interact click point does not control this check.")
    return False


def fishing_shift_lock_ready(engine) -> bool:
    """True only when the active runtime's lock was physically confirmed."""
    if not interaction_safety_guard():
        return True
    if not (bool(getattr(engine, "_shift_lock", False))
            and bool(getattr(engine, "_shift_lock_verified", False))):
        return False
    # Focus can be lost or Shift Lock can be changed manually between catches.
    # Re-observe the cursor before each cast instead of trusting a transition
    # that happened several seconds ago.
    still_locked = _shift_lock_centered(engine)
    engine._shift_lock_verified = still_locked
    if not still_locked:
        engine._shift_lock = False
    return still_locked


def set_rod(engine, equipped: bool) -> None:
    """Put the fishing rod away / take it back out, tracked like shift lock.

    This exists to work around a game bug, not for tidiness: after a catch the
    character sometimes locks up with no dialogue on screen — movement keys do
    nothing, so the walk back to the NPC silently fails and the bot retries
    forever against a character that cannot move. Toggling the rod off and on
    clears it.

    The hotbar key is a toggle, so as with shift lock we track state rather than
    pressing blind; the user is required to start with the rod equipped, which
    anchors it.
    """
    if engine._rod_equipped == equipped:
        return
    engine.keyboard.tap(digit_scan(engine.cfg.rod_slot))
    engine._rod_equipped = equipped
    engine._sleep(engine.cfg.shop.after_rod)


def flick_rod(engine) -> None:
    """Unequip and immediately re-equip the rod, right after a catch.

    This is a game quirk, and a large one: done quickly enough the
    Species/Weight card never appears at all. No card means nothing to wait for
    and nothing to dismiss, which removes the whole tail of the catch cycle —
    measured at ~1.3 s from catch to the next cast, against several seconds of
    card-watching before.

    The two presses cancel out, so `_rod_equipped` is unchanged; this
    deliberately bypasses `set_rod`, whose per-press settle would be far too
    slow for the trick to work.
    """
    t = engine.cfg.timing
    scan = digit_scan(engine.cfg.rod_slot)
    if t.slow_rod_flick:
        # Deliberately unhurried: an instant flick leaves some accounts holding
        # a glitched fish. The catch card will render, which is fine — it is
        # cleared the same way it was before the trick existed.
        engine._sleep(t.rod_flick_slow_delay)
        engine.keyboard.tap(scan)
        engine._sleep(t.rod_flick_slow_gap)
        engine.keyboard.tap(scan)
    else:
        engine.keyboard.tap(scan)
        time.sleep(t.rod_flick_gap)
        engine.keyboard.tap(scan)
    engine._sleep(t.rod_flick_settle)


def _dialog_roi(engine) -> Rect:
    d = engine.cfg.dialog
    return engine.window.sub(d.left, d.top, d.right, d.bottom)


def _learn_roi(engine) -> Rect:
    d = engine.cfg.dialog
    return engine.window.sub(d.learn_left, d.learn_top,
                             d.learn_right, d.learn_bottom)


def popup_up(engine) -> bool:
    # Update 30 moved the catch card to the bottom and changed its panel from
    # the old blue centre block to a yellow-header card. Check the complete game
    # frame. Optional color sampling supplements this detector; it never
    # enables the obsolete blue-card detector for the current game.
    image = engine.screen.grab(engine.window)
    # The Fisherman's own name strip is yellow too. A visible NPC menu proves
    # this is a dialogue, not a catch popup that should delay the shop route.
    uses_npc = npc_route_enabled(engine.cfg, getattr(engine, "bait_count", None),
                                 getattr(engine, "_buy_failures", 0))
    if ((not uses_npc or not _falling_menu_buttons(engine))
            and update30_dialogue_present(image, engine.cfg.colors)):
        return True
    # The old blue-card ROI can look like a solid panel during normal fishing
    # (water, flooring, Safe Zone text or the charge meter). It is not evidence
    # of a current catch card, regardless of the optional sample's checkbox.
    return False


def learn_up(engine) -> bool:
    d = engine.cfg.dialog
    return learn_button_present(engine.screen.grab(_learn_roi(engine)),
                                d.learn_navy_min, d.learn_white_min)


def clear_recipe_note(engine) -> bool:
    """Dismiss the recipe note if it is up. One grab; never blocks.

    This is the only popup that must be handled, because it is the only one
    that never goes away by itself. Ordinary catch cards fade on their own
    schedule and are not worth waiting for — see `wait_popup_clear`.
    """
    d = engine.cfg.dialog
    if not d.enabled or not learn_up(engine):
        return False
    if not npc_route_enabled(engine.cfg, getattr(engine, "bait_count", None),
                             getattr(engine, "_buy_failures", 0)):
        # In fish-only mode the user owns Shift Lock. We cannot free the cursor
        # for Learn and restore its unknown state without a blind toggle.
        engine.log("[catch] recipe note needs attention: fish-only mode keeps "
                   "Shift Lock unchanged. Clear Learn manually, restore your "
                   "fishing aim, then press F2 to restart.")
        engine.stop()
        return False
    engine.log("[catch] new-recipe note — clicking Learn")
    was_locked = engine._shift_lock
    set_shift_lock(engine, False)
    x, y = _abs(engine.window, d.learn_click)
    engine.mouse.click_at(x, y)
    engine._sleep(0.6)
    set_shift_lock(engine, was_locked)
    return True


def wait_popup_clear(engine, why: str = "", timeout: float | None = None) -> bool:
    """Block until nothing is covering the middle of the screen.

    Acting through a catch popup is how clicks get eaten: the press meant for
    the rod (or for the NPC) lands on the card instead. Most popups fade in
    ~1.2 s, so this usually returns almost immediately.

    The exception is the "you found a new recipe" note. It never times out — it
    waits for **Learn** — so if that button is showing we click it. Clicking
    needs the cursor free, and shift lock pins it to centre, so the lock is
    dropped for the click and put back exactly as it was.
    """
    d = engine.cfg.dialog
    if not d.enabled:
        return True
    deadline = time.perf_counter() + (d.clear_timeout if timeout is None else timeout)
    learned = False
    saw_popup = False
    while time.perf_counter() < deadline:
        if not engine._alive():
            return False
        if not popup_up(engine):
            if saw_popup:
                engine._sleep(d.after_clear)
            return True
        saw_popup = True
        if not learned and learn_up(engine):
            engine.log("[catch] new-recipe note — clicking Learn")
            was_locked = engine._shift_lock
            set_shift_lock(engine, False)
            x, y = _abs(engine.window, d.learn_click)
            engine.mouse.click_at(x, y)
            learned = True
            engine._sleep(0.6)
            set_shift_lock(engine, was_locked)
            continue
        time.sleep(d.poll)
    engine.log(f"[catch] popup still up after {d.clear_timeout:.0f}s"
               f"{' (' + why + ')' if why else ''} — carrying on")
    return False


def _wait_until(engine, pred, timeout: float) -> bool:
    """Poll `pred` until true. False on timeout or if the user hit stop."""
    poll = engine.cfg.shop.poll
    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        if not engine._alive():
            return False
        if pred():
            return True
        time.sleep(poll)
    return False


def _menu_page_signature(engine) -> tuple | None:
    """Return the currently visible action stack as a stable page witness.

    The count identifies the expected number of rows; their positions make
    real row movement reset the settle clock even if the count is unchanged.
    Only current live rows can prove a page; legacy grey bands cannot.
    """
    try:
        buttons = _falling_menu_buttons(engine, strict=True)
    except Exception as exc:
        DEBUG.event("menu", "observation unavailable", error=str(exc))
        return None
    return tuple((int(round(x)), int(round(y))) for x, y in buttons)


def _clear_menu_page_witness(engine) -> None:
    """Forget a page proof immediately before a click can change the page."""
    try:
        del engine._menu_page_witness
    except AttributeError:
        pass


def _same_menu_page_signature(first: tuple, second: tuple) -> bool:
    """Allow small label-centre jitter while preserving the page/row proof.

    Moving water behind translucent buttons and a pointer over a label shift
    the median of white pixels by a few pixels even after the menu settles.
    Compare against the original baseline, never accumulate movement across
    samples. A changed count or actual falling row still resets the clock.
    """
    if first is None or second is None:
        return False
    if first == second:
        return True
    if (not first or not second or first[0] == "legacy"
            or second[0] == "legacy" or len(first) != len(second)):
        return False
    return all(abs(a[0] - b[0]) <= 8 and abs(a[1] - b[1]) <= 3
               for a, b in zip(first, second))


def wait_for_menu_page(engine, page: str, timeout: float, *,
                       saw_dialogue: list[bool] | None = None) -> bool:
    """Wait for a complete target page to stop animating before acting.

    Update 30 animates rows into place one at a time. An instantaneous count
    of three can describe either a finished Shop page *or* a root page while
    its top entry is falling away. In that exact race, clicking the supposed
    ``shop.sell_fish`` row clicks root ``fishing_index`` instead. A complete
    stack must therefore keep the same row count and positions for a measured
    settle window; an uncertain transition times out safely instead of clicking
    a semantic action on the wrong page.
    """
    expected = MENU_PAGE_ROWS.get(page)
    if expected is None:
        raise ShopError(f"unknown menu page {page!r}")
    poll = max(0.03, engine.cfg.shop.poll)
    # Rendering may consume the whole transition timeout. Settling is an
    # additional bounded observation window, not part of that render budget.
    settle = max(0.0, engine.cfg.shop.menu_page_settle)
    deadline = time.perf_counter() + max(0.0, timeout) + settle
    # The caller immediately after a successful transition already owns a
    # complete, settled proof for this exact page. Re-paying the full settle
    # time made buy and sell wait twice on every page.
    prior = getattr(engine, "_menu_page_witness", None)
    if prior is not None and prior[0] == page:
        signature = _menu_page_signature(engine)
        count = len(signature) if signature is not None else -1
        if _same_menu_page_signature(signature, prior[1]) and count == expected:
            return True
        _clear_menu_page_witness(engine)
    stable_since: float | None = None
    stable_signature: tuple | None = None
    observed_counts: set[int] = set()
    longest_stable = 0.0
    while time.perf_counter() < deadline:
        if not engine._alive():
            return False
        # The root menu falls in one row at a time.  This lightweight latch is
        # intentionally less strict than a page proof: it is *not* permission
        # to click an action, only a hard boundary against walking backwards
        # through a menu that is visibly opening.
        signature = _menu_page_signature(engine)
        count = len(signature) if signature is not None else -1
        if saw_dialogue is not None and (count >= 2 or signature is None):
            # An unreadable frame is also a boundary against blind S movement.
            saw_dialogue[0] = True
        observed_counts.add(count)
        if count == expected:
            now = time.perf_counter()
            if (stable_signature is None or
                    not _same_menu_page_signature(signature, stable_signature)):
                stable_signature = signature
                stable_since = now
            elif stable_since is not None and now - stable_since >= settle:
                engine._menu_page_witness = (page, signature)
                return True
            if stable_since is not None:
                longest_stable = max(longest_stable, now - stable_since)
        else:
            stable_since = None
            stable_signature = None
        time.sleep(poll)
    log = getattr(engine, "log", None)
    if callable(log):
        counts = "/".join("unavailable" if count < 0 else str(count)
                          for count in sorted(observed_counts)) or "none"
        log(f"[shop] {page} page not confirmed: expected {expected} settled rows; "
            f"observed {counts}; longest stable {longest_stable:.2f}s / {settle:.2f}s "
            "required - check that the NPC menu area includes every action row")
    return False


def open_npc_dialogue(engine) -> bool:
    """Get to the Fisherman and open his dialogue. Shared by buy and sell.

    The NPC push changes both the character position and camera, so an S key
    is not a durable "walk back to the NPC" direction. First test Interact from
    the position Roblox left us in. Only when that fails do at most two tiny S
    probes, confirming the root menu after each. Never dead-reckon or
    compensate with W.
    """
    cfg = engine.cfg.shop
    m, kb, win, log = engine.mouse, engine.keyboard, engine.window, engine.log

    wait_popup_clear(engine, "before talking to the NPC")

    # The old path clicked Interact before asking whether the action stack was
    # already visible. If that detector was a frame late, it then sent an S
    # probe from an open dialogue, pushing the character farther than intended.
    # On every supported runtime, a visible menu is a hard no-movement
    # boundary: prove its root page and reuse it, or fail safely without an
    # Interact click or an S tap.
    if interaction_safety_guard() and in_dialogue(engine):
        set_shift_lock(engine, False)
        log("[shop] dialogue already visible — no Interact click or S movement")
        if wait_for_menu_page(engine, "root", cfg.root_menu_timeout):
            engine._at_npc = True
            engine._npc_repositioned = True
            return True
        log("[shop] dialogue is visible but root rows are not confirmed — "
            "not moving; check the NPC menu search area")
        return False

    set_rod(engine, False)

    cx, cy = _abs(win, cfg.center)

    def interact(timeout: float) -> tuple[bool, bool]:
        if not engine._alive():
            return False, False
        set_shift_lock(engine, False)
        m.move_to(cx, cy)
        engine._sleep(0.25)
        _clear_menu_page_witness(engine)
        m.click_at(cx, cy)                   # Interact
        # The root page is four rows. Waiting for all four prevents a half-drawn
        # root page from being mistaken for the later three-row Shop page.
        saw_dialogue = [False]
        return (wait_for_menu_page(engine, "root", timeout,
                                   saw_dialogue=saw_dialogue),
                saw_dialogue[0])

    # The direct test prevents a needless step when Roblox has left us on the
    # usable edge of its interaction radius.
    opened, saw_dialogue = interact(cfg.direct_dialog_timeout)
    if opened:
        engine._at_npc = True
        engine._npc_repositioned = True
        return True
    if interaction_safety_guard() and saw_dialogue:
        # The UI is on screen but was not settled within the short direct
        # window.  Give it its ordinary root-page window; either result is
        # safer than an S probe from an already-open dialogue.
        log("[shop] dialogue rows appeared while the root menu was opening — "
            "waiting without S movement")
        if wait_for_menu_page(engine, "root", cfg.root_menu_timeout):
            engine._at_npc = True
            engine._npc_repositioned = True
            return True
        log("[shop] dialogue rows did not settle into the root menu — not "
            "moving; check the NPC menu search area")
        return False

    engine._at_npc = False
    for probe in range(1, max(0, cfg.max_approach_attempts) + 1):
        log(f"[shop] no dialogue — S range probe {probe}/"
            f"{max(0, cfg.max_approach_attempts)}")
        kb.tap(SC_S, cfg.walk_back_tap)
        engine._sleep(cfg.approach_wait)
        opened, saw_dialogue = interact(cfg.dialog_timeout)
        if opened:
            engine._at_npc = True
            engine._npc_repositioned = True
            return True
        if interaction_safety_guard() and saw_dialogue:
            log("[shop] dialogue rows appeared after the S probe but did not "
                "settle — stopping further movement")
            return False
    return False


def _click_menu_action_until(engine, page: str, action: str, *,
                             done: Callable[[], bool], tries: int,
                             wait: float, next_page: str | None = None) -> bool:
    """Retry one named action only from a stable page, observing its result."""
    for attempt in range(max(1, tries)):
        if not engine._alive():
            return False
        if next_page is None and attempt and done():
            return True
        if attempt and next_page is not None:
            signature = _menu_page_signature(engine)
            if signature is not None and len(signature) == MENU_PAGE_ROWS[next_page]:
                return wait_for_menu_page(engine, next_page, wait)
        if not wait_for_menu_page(engine, page, wait):
            return (wait_for_menu_page(engine, next_page, wait)
                    if next_page is not None and engine._alive() else False)
        # Do not wait for the destination before sending the source action.
        # That used to spend a full Shop-page timeout while the root menu was
        # still correctly on screen, making every dialogue action feel frozen.
        if click_menu_action(engine, page, action) is False:
            continue
        if next_page is not None:
            if wait_for_menu_page(engine, next_page, wait):
                return True
        elif _wait_until(engine, done, wait):
            return True
    # The final click already spent the full transition window. A late page is
    # safer to classify as a failed route than to add another blind wait or
    # click a potentially changed menu.
    return False if next_page is not None else done()


def sell(engine, *, stay_at_npc: bool = False) -> bool:
    """Sell the fish stock. Returns True once the sale is confirmed.

    Route (measured): `Shop` -> `Sell Fish` -> `Confirm`. `Sell Fish` is the
    *second* menu entry, unlike everything in the buy route, and `Confirm`
    lands back on the first. The dialogue closes itself afterwards.
    """
    cfg = engine.cfg.shop
    s = engine.cfg.sell
    log = engine.log

    npc = cfg.npc.lower()
    if npc in ("none", "off", "") or not npc.startswith("f"):
        raise ShopError("selling is only mapped for the fisherman")

    def fail(why: str) -> bool:
        log(f"[sell] FAILED: {why} — nothing sold")
        if not _recover(engine) or not engine._alive():
            engine.stop()
            return False
        set_rod(engine, True)
        enter_fishing_stance(engine)
        return False

    log("[sell] selling the fish stock")
    if not open_npc_dialogue(engine):
        return fail("NPC dialogue never opened")

    # The root has four rows, Shop has three, then Confirm has two. Count the
    # live stack after each named transition; a row's former Y coordinate is
    # never treated as its current function.
    if not _click_menu_action_until(
            engine, "root", "shop", done=lambda: menu_items(engine) == 3,
            tries=4, wait=s.after_click + 0.6, next_page="shop"):
        return fail("Shop page never appeared")
    if not _click_menu_action_until(
            engine, "shop", "sell_fish", done=lambda: menu_items(engine) == 2,
            tries=4, wait=s.confirm_timeout, next_page="confirm"):
        return fail("sell confirmation never appeared")
    engine._sleep(s.after_click)
    if not _confirm_sale(engine, tries=3, wait=s.confirm_timeout):
        return fail("sell confirmation did not close")

    # Confirm removes the action stack before its passive result line fades.
    # That disappearance is the sale witness; waiting for every visual trace
    # of the NPC would wrongly classify the successful result line as dialogue.
    engine._sleep(cfg.after_nevermind)
    if stay_at_npc:
        # A sale closes the dialogue by pushing the player forward, just out
        # of range. The following bait route must take one fresh S step rather
        # than wasting the full dialogue timeout on a click from that new spot.
        # Keep the rod stowed; buy() will perform the necessary re-approach.
        engine._at_npc = False
        log("[sell] done — reopening the NPC for bait")
        return True

    set_rod(engine, True)
    enter_fishing_stance(engine)
    log("[sell] done")
    return True


def _confirm_sale(engine, *, tries: int, wait: float) -> bool:
    """Click Confirm and observe its own live stack vanish.

    Do not use the generic ``in_dialogue`` check here: the post-sale message
    contains broad dark bars that the legacy fallback can mistake for menu
    rows. We wait for the known two-row Confirm page before every retry, then
    accept only disappearance of the Update 30 action stack after the click.
    """
    for _ in range(max(1, tries)):
        if not engine._alive() or not wait_for_menu_page(engine, "confirm", wait):
            return False
        if click_menu_action(engine, "confirm", "confirm") is False:
            continue
        if _wait_dialogue_closed(engine, wait):
            return True
    return False


def in_dialogue(engine) -> bool:
    """Whether an NPC action menu is still open.

    A catch card and the Fisherman's yellow name strip are both visual
    overlays, but neither is an open *action menu*. Treating either as one made
    the exit loop keep sending Nevermind after the real menu had already
    vanished. The visible action-row stack is the only safe witness for an NPC
    dialogue. Every actionable Fisherman page has at least two rows; a lone
    broad panel can instead be the passive post-sale message. Requiring two
    rows prevents a completed sale from being retried as if Confirm were still
    on screen.
    """
    try:
        return action_menu_open(engine)
    except Exception:                                # noqa: BLE001
        return False


def _click_until(engine, click, done, tries: int = 6, wait: float = 1.2) -> bool:
    """Click a target until the thing it should cause has actually happened.

    This is the answer to lag. A click landing before its button has rendered
    simply does nothing, so firing once and assuming it worked is what
    desynchronised the whole purchase. Clicking again is harmless, and checking
    the outcome makes the step self-correcting.
    """
    for _ in range(max(1, tries)):
        if not engine._alive():
            return False
        if done():
            return True
        click()
        if _wait_until(engine, done, wait):
            return True
    return done()


def _wait_dialogue_closed(engine, timeout: float) -> bool:
    """Require sustained, successfully captured absence after an exit click."""
    deadline = time.perf_counter() + timeout
    absent_since = None
    poll = max(.03, engine.cfg.shop.poll)
    while time.perf_counter() < deadline:
        if not engine._alive():
            return False
        signature = _menu_page_signature(engine)
        now = time.perf_counter()
        if signature == ():
            if absent_since is None:
                absent_since = now
            elif now - absent_since >= .24:
                _clear_menu_page_witness(engine)
                return True
        else:
            # None is a failed capture, never evidence that the menu closed.
            absent_since = None
        time.sleep(poll)
    return False


def _confirm_craft_purchase(engine, click, *, tries: int, wait: float) -> bool:
    """Send Craft only while its button is present, then prove it stays gone.

    A pre-click missing frame is not a purchase. Neither is a one-frame fade
    or failed capture. Retry only if the real Craft button is still visible;
    never repeat that click over the returned NPC page.
    """
    def observe():
        try:
            return craft_up(engine)
        except Exception as exc:
            DEBUG.event("craft", "observation unavailable", error=str(exc))
            return None

    poll = max(.03, engine.cfg.shop.poll)
    for _ in range(max(1, tries)):
        if not engine._alive():
            return False
        if observe() is not True:
            engine.log("[shop] Craft button not verified before confirmation; no click sent")
            return False
        _clear_menu_page_witness(engine)
        click()
        deadline = time.perf_counter() + wait
        absent_since = None
        while time.perf_counter() < deadline:
            if not engine._alive():
                return False
            visible = observe()
            now = time.perf_counter()
            if visible is False:
                if absent_since is None:
                    absent_since = now
                elif now - absent_since >= .24:
                    return True
            else:
                absent_since = None
            time.sleep(poll)
    return False


def leave_dialogue(engine, tries: int = 3) -> bool:
    """Get out of the NPC dialogue from whatever page we are on.

    'Back' then 'Nevermind' is the normal way out. Update 30 redraws through a
    brief blank/partial state after Back, so a missing row is **not** evidence
    that the dialogue closed. First wait for the complete root page, then let
    its Nevermind row settle before clicking it. If that still fails, stop
    safely rather than trying to close it with movement.

    There is no movement recovery here. A W/S pair is not reversible after the
    NPC's radial push and would slowly rotate the player away from the NPC.
    """
    cfg = engine.cfg.shop
    # Let the menu finish rendering before the first click. The craft window
    # closing snaps the dialogue back to the main menu, and 'Nevermind' is the
    # last entry to draw; clicking into a half-drawn menu misses and the bot
    # visibly stabs at it. Tunable in Advanced cooldowns ("Before clicking
    # Nevermind").
    engine._sleep(cfg.before_leave)
    def root_ready() -> bool:
        signature = _menu_page_signature(engine)
        return signature is not None and len(signature) == MENU_PAGE_ROWS["root"]

    # CRAFT normally returns to the two-row bait page. If the detector catches
    # the short fade instead, wait for a real menu before deciding which bottom
    # action is safe to use.
    visible = _wait_until(engine, lambda: action_menu_open(engine),
                          cfg.root_menu_timeout)
    if not visible:
        return _wait_dialogue_closed(engine, cfg.nevermind_retry)

    prior = getattr(engine, "_menu_page_witness", None)
    if not root_ready() and not (prior is not None and prior[0] == "root"):
        if not wait_for_menu_page(engine, "bait", cfg.root_menu_timeout):
            return False
        if click_menu_action(engine, "bait", "back") is False:
            return False
        # Never use "no rows right now" as the success condition here. Back
        # creates exactly that transient and was the reason Nevermind got lost.
        if not wait_for_menu_page(engine, "root", cfg.root_menu_timeout):
            return False

    engine._sleep(cfg.root_menu_settle)
    for _ in range(max(1, tries)):
        if not engine._alive():
            return False
        if not wait_for_menu_page(engine, "root", cfg.root_menu_timeout):
            return _wait_dialogue_closed(engine, cfg.nevermind_retry)
        if click_menu_action(engine, "root", "nevermind") is False:
            continue
        if _wait_dialogue_closed(engine, cfg.nevermind_retry):
            return True
        # If the row is still visible, it was not yet accepted. Re-assert only
        # after a measured retry window; never burst-click a moving menu.
        if root_ready():
            engine._sleep(cfg.after_back)

    if in_dialogue(engine):
        engine.log("[shop] could not close the dialogue — stopping this shop "
                   "route rather than moving and losing the NPC position")
    return False


def buy(engine, amount: int, first_time: bool = False) -> bool:
    """Run the purchase. Returns True if the sequence completed.

    `engine` supplies window/mouse/keyboard/log and an `_alive()` abort check,
    so pressing F2 stops us mid-dialogue instead of clicking on regardless.

    Whether we need to walk back to the NPC is read from `engine._at_npc`, not
    from a "first time" flag: the bot steps *away* from the NPC on start-up to
    reach casting position, so even the first purchase has to walk back.
    """
    cfg = engine.cfg.shop
    npc = cfg.npc.lower()
    if npc in ("none", "off", ""):
        raise ShopError("buying is disabled for this run")
    if not npc.startswith("f"):
        raise ShopError(f"unknown shop npc {cfg.npc!r} (expected fisherman)")

    m, kb, win, log = engine.mouse, engine.keyboard, engine.window, engine.log

    def click(frac, pause: float = 0.0, name: str = "shop click") -> None:
        x, y = _abs(win, frac)
        DEBUG.click(name, x, y)
        m.click_at(x, y)
        if pause:
            engine._sleep(pause)

    def fail(why: str) -> bool:
        log(f"[shop] FAILED: {why} - bait purchase not confirmed")
        if not _recover(engine) or not engine._alive():
            engine.stop()
            return False
        # Always hand back a fishable state: rod in hand, shift lock on, one
        # step off the NPC. Otherwise the next cast would click centre while
        # still in range and re-open the dialogue we just backed out of — or
        # worse, cast with the rod still stowed.
        set_rod(engine, True)
        enter_fishing_stance(engine)
        return False

    step = max(1, cfg.craft_step)
    n_plus = plus_clicks(amount, step)
    bought = step * (n_plus + 1)
    log(f"[shop] buying x{bought} bait from the fisherman "
        f"({n_plus} '+' click{'s' if n_plus != 1 else ''})")

    if not open_npc_dialogue(engine):
        return fail("NPC dialogue never opened — either you are out of "
                    "interaction range, or the menu box needs calibrating "
                    "(easy_run.py -> Calibrate controls -> shop.menu)")

    # They all happen to occupy visible row zero, but their *roles* change as
    # the page falls. Verify 4 -> 3 -> 2 before taking Basic Bait into CRAFT;
    # a late click cannot accidentally be applied to a later page.
    if not _click_menu_action_until(
            engine, "root", "shop", done=lambda: menu_items(engine) == 3,
            tries=4, wait=cfg.after_click + 0.6, next_page="shop"):
        return fail("Shop page never appeared")
    if not _click_menu_action_until(
            engine, "shop", "buy_bait", done=lambda: menu_items(engine) == 2,
            tries=4, wait=cfg.after_click + 0.6, next_page="bait"):
        return fail("Buy Bait page never appeared")
    if not _click_menu_action_until(
            engine, "bait", "basic_bait", done=lambda: craft_up(engine),
            tries=4, wait=cfg.after_click + 0.6):
        return fail("CRAFT window never opened")

    for _ in range(n_plus):                  # quantity: +10 each
        if not engine._alive():
            return False
        click(cfg.craft_plus, cfg.after_plus)

    # A verified Craft click followed by sustained closure is the available
    # screen-based purchase acknowledgment, not an inventory readback.
    if not _confirm_craft_purchase(
            engine, lambda: click(cfg.craft_button, name="Craft"),
            tries=4, wait=cfg.craft_timeout):
        return fail("CRAFT confirmation failed")

    # From here the purchase is acknowledged. However messy the exit is, it
    # must still be credited: not doing so is what had the bot buying
    # over and over every few catches.
    if not leave_dialogue(engine):
        # The bait purchase is real, but fishing while an NPC menu may still be
        # covering centre-screen is not safe. Stop rather than cast or walk
        # through an unverified dialogue state.
        log("[shop] bait bought, but dialogue did not close — stopping safely")
        engine.stop()
        return True

    # Dismissing the dialogue locks the character briefly; moving during that
    # window silently goes nowhere.
    engine._sleep(cfg.after_nevermind)
    set_rod(engine, True)                    # rod back out; no forward movement
    enter_fishing_stance(engine)
    log(f"[shop] done — {bought} bait bought")
    return True


def escape_dialogue(engine) -> bool:
    """Close an NPC dialogue we did not mean to open, and get back to fishing.

    A failed cast can reveal a dialogue that is still open at centre-screen.
    Close it, clear the known rod state, and let the NPC's own push establish
    the post-dialogue fishing position. No compensating walk is allowed.

    Returns True if a dialogue was found and dealt with.
    """
    if not in_dialogue(engine):
        return False
    cfg = engine.cfg.shop
    engine.log("[cast] a dialogue is open; verifying its exit before fishing")
    # Clicking menu entries needs the cursor free.
    set_shift_lock(engine, False)
    if not _recover(engine) or not engine._alive():
        engine.stop()
        return True
    # Toggle the rod: this is the known cure for the post-catch stuck state.
    set_rod(engine, False)
    set_rod(engine, True)
    # A real dialogue means the NPC has already supplied its perimeter
    # repositioning; do not add a movement key after closing it.
    engine._at_npc = True
    engine._npc_repositioned = True
    enter_fishing_stance(engine)
    return True


def _recover(engine) -> bool:
    """Best effort: get out of whatever dialogue we are stuck in.

    Close a detected craft window, then use the same verified exit as the
    normal route. Unknown pages and failed captures must never become blind
    clicks on an old Back/Nevermind coordinate.
    """
    cfg = engine.cfg.shop
    win = engine.window
    try:
        if not engine._alive():
            return False
        if craft_up(engine):
            x, y = _abs(win, cfg.craft_close)
            engine.mouse.click_at(x, y)
            if not _wait_until(engine, lambda: not craft_up(engine), cfg.close_timeout):
                return False
        if not leave_dialogue(engine):
            return False
        # Same post-dismiss lock as the normal exit.
        engine._sleep(cfg.after_nevermind)
        return engine._alive()
    except Exception as exc:                 # recovery must never raise
        engine.log(f"[shop] recovery could not verify closure: {exc}")
        return False


def enter_fishing_stance(engine) -> bool:
    """Re-engage shift lock after the NPC establishes fishing position.

    Automatic NPC routes use Shift Lock to pin the cursor at centre, where
    cast and bite clicks land. Roblox's dialogue push already takes the player
    to its fishing-side perimeter position. Sending a fixed W from there would
    be open-loop movement toward the water, so this function deliberately has
    no movement key at all.
    """
    if not set_shift_lock(engine, True):
        return False
    engine._npc_repositioned = False
    engine._at_npc = False
    return True


def establish_fishing_anchor(engine) -> bool:
    """Use one confirmed NPC dialogue as the F2 position reset.

    This replaces the old fixed forward walk. If the initial dialogue cannot
    be confirmed, fishing must not begin: we have no trustworthy position from
    which to make a centre-screen cast.
    """
    engine.log("[start] opening NPC dialogue to establish fishing position")
    if not open_npc_dialogue(engine):
        engine.log("[start] NPC anchor failed — not starting the fishing loop")
        return False
    if not leave_dialogue(engine):
        engine.log("[start] NPC dialogue did not close — not starting the "
                   "fishing loop")
        return False
    engine._sleep(engine.cfg.shop.after_nevermind)
    if not engine._alive():
        return False
    set_rod(engine, True)
    if not enter_fishing_stance(engine):
        engine.log("[start] Shift Lock did not engage — not starting the fishing loop")
        return False
    engine.log("[start] NPC anchor confirmed — fishing from the pushed position")
    return engine._alive()
