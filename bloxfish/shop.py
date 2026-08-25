"""Buying bait from a fishing NPC.

Mapped frame-by-frame from a screen recording (two full purchase cycles,
x10 then x20). Everything below is measured, not guessed.

## Path F — Fisherman

The dialogue is a stack of right-aligned buttons. The useful accident is that
**the option we want is always the first one**, at the same screen position
three times in a row:

    click centre      -> "Interact"  -> dialogue opens
    click menu item 1 -> "Shop"
    click menu item 1 -> "Buy Bait"
    click menu item 1 -> "Basic Bait"   -> CRAFT window opens

The CRAFT window starts at **10** bait for 1000 Money. Each `+` click adds
another **10** (verified: 10 -> 20 took the cost 1000 -> 2000), so buying N bait
is `N/10 - 1` clicks on `+` followed by `Craft`.

Crafting drops you back on the bait menu, and the way out is two more clicks —
both on the **last** menu entry, which lands at the same spot for both:

    click menu last -> "Back"       -> main menu
    click menu last -> "Nevermind"  -> dialogue closes

Closing the dialogue leaves shift lock **off** (the cursor is free again), so
the last step is to press Left Shift to re-engage it and tap `W` to step back
into fishing position.

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

from .capture import Rect
from .inputs import SC_LSHIFT, SC_S, SC_W, digit_scan
from .vision import (
    craft_window_open, dialogue_overlay_frac, learn_button_present,
    menu_button_count,
)


class ShopError(RuntimeError):
    pass


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
    return menu_button_count(engine.screen.grab(_menu_roi(engine)))


def craft_up(engine) -> bool:
    return craft_window_open(engine.screen.grab(_craft_roi(engine)),
                             engine.cfg.shop.craft_btn_min_w_frac)


def set_shift_lock(engine, on: bool) -> None:
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
        return
    engine.keyboard.tap(SC_LSHIFT)
    engine._shift_lock = on
    engine._sleep(engine.cfg.shop.after_shift)


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
    d = engine.cfg.dialog
    return dialogue_overlay_frac(engine.screen.grab(_dialog_roi(engine))) >= d.present_frac


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


def open_npc_dialogue(engine) -> bool:
    """Get to the Fisherman and open his dialogue. Shared by buy and sell.

    Covers everything that has bitten this sequence before: waiting out a catch
    card that would eat the Interact click, stowing the rod to clear the
    stuck-movement bug, walking back into range, and dropping shift lock so the
    cursor can leave centre. Confirmed by the menu actually appearing, with a
    second walk-back if it did not.
    """
    cfg = engine.cfg.shop
    m, kb, win, log = engine.mouse, engine.keyboard, engine.window, engine.log

    wait_popup_clear(engine, "before talking to the NPC")
    set_rod(engine, False)

    cx, cy = _abs(win, cfg.center)
    for attempt in range(1, max(1, cfg.max_approach_attempts) + 1):
        if not engine._at_npc:
            kb.tap(SC_S, cfg.walk_tap)
            engine._at_npc = True
            engine._sleep(cfg.approach_wait)
        if not engine._alive():
            return False

        set_shift_lock(engine, False)
        m.move_to(cx, cy)
        engine._sleep(0.25)
        m.click_at(cx, cy)                   # Interact

        if _wait_until(engine, lambda: menu_items(engine) >= 2,
                       cfg.dialog_timeout):
            return True
        if attempt < cfg.max_approach_attempts:
            log(f"[shop] no dialogue (attempt {attempt}) — stepping back again")
            engine._at_npc = False
    return False


def sell(engine) -> bool:
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

    def click(frac, pause: float = 0.0) -> None:
        x, y = _abs(engine.window, frac)
        engine.mouse.click_at(x, y)
        if pause:
            engine._sleep(pause)

    def fail(why: str) -> bool:
        log(f"[sell] FAILED: {why} — nothing sold")
        _recover(engine)
        set_rod(engine, True)
        enter_fishing_stance(engine)
        return False

    log("[sell] selling the fish stock")
    if not open_npc_dialogue(engine):
        return fail("NPC dialogue never opened")

    click(cfg.menu_item1, s.after_click)     # Shop
    click(cfg.menu_item2)                    # Sell Fish  (second entry)

    # The confirm page is a two-button menu; wait for it rather than guessing.
    if not _wait_until(engine, lambda: menu_items(engine) >= 1, s.confirm_timeout):
        return fail("sell confirmation never appeared")
    engine._sleep(s.after_click)
    click(cfg.menu_item1)                    # Confirm (back on the first slot)

    # A sale ends with the dialogue dismissing *itself*. If it is still sitting
    # there, the tree never advanced and nothing was sold — say so rather than
    # resetting the counter, otherwise the stock silently never gets sold.
    sold = _wait_until(engine, lambda: not in_dialogue(engine), s.confirm_timeout)
    if not sold:
        log("[sell] dialogue never closed — assuming nothing was sold")
        leave_dialogue(engine)

    engine._sleep(cfg.after_nevermind)
    set_rod(engine, True)
    enter_fishing_stance(engine)
    if sold:
        log("[sell] done")
    return sold


def in_dialogue(engine) -> bool:
    """Are we on *any* NPC page? Two witnesses; either one is enough.

    The button counter alone proved unreliable across window sizes, so the
    centre-panel test backs it up.
    """
    try:
        return menu_items(engine) >= 1 or popup_up(engine)
    except Exception:                                # noqa: BLE001
        return False


def _click_until(engine, frac, done, tries: int = 6, wait: float = 1.2) -> bool:
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
        x, y = _abs(engine.window, frac)
        engine.mouse.click_at(x, y)
        if _wait_until(engine, done, wait):
            return True
    return done()


def leave_dialogue(engine, tries: int = 3) -> bool:
    """Get out of the NPC dialogue from whatever page we are on.

    'Back' then 'Nevermind' is the normal way out, but the bot used to fire
    exactly two clicks and assume they worked — when they did not it sat at the
    NPC forever. So: click the last entry until the dialogue is really gone,
    and if that still fails, fall back on the fact that *moving* closes it.

    The walk is one short tap, immediately undone by an equal tap the other
    way: stepping forward repeatedly would march the character into the water.
    """
    cfg = engine.cfg.shop
    # Let the menu finish rendering before the first click. The craft window
    # closing snaps the dialogue back to the main menu, and 'Nevermind' is the
    # last entry to draw; clicking into a half-drawn menu misses and the bot
    # visibly stabs at it. Tunable in Advanced cooldowns ("Before clicking
    # Nevermind").
    engine._sleep(cfg.before_leave)
    for k in range(max(1, tries)):
        if not engine._alive() or not in_dialogue(engine):
            return True
        x, y = _abs(engine.window, cfg.menu_last)
        engine.mouse.click_at(x, y)
        # 'Back' only advances to the main menu; it does not close anything, so
        # allow just a short beat and click again ('Nevermind') rather than
        # sitting out close_timeout waiting for a close that will not come. A
        # real close IS caught here -- _wait_until returns the instant the
        # dialogue is gone -- so Nevermind still exits promptly. The last
        # attempt gets the full close_timeout as a safety net under lag.
        budget = cfg.close_timeout if k == max(1, tries) - 1 else cfg.after_back
        if _wait_until(engine, lambda: not in_dialogue(engine), budget):
            return True

    for _ in range(2):
        if not engine._alive() or not in_dialogue(engine):
            return True
        engine.log("[shop] stuck in the dialogue — stepping to break out")
        engine.keyboard.tap(SC_W, cfg.walk_tap)
        engine._sleep(0.45)
        engine.keyboard.tap(SC_S, cfg.walk_tap)    # undo; never drift forward
        engine._sleep(0.45)
    return not in_dialogue(engine)


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

    def click(frac, pause: float = 0.0) -> None:
        x, y = _abs(win, frac)
        m.click_at(x, y)
        if pause:
            engine._sleep(pause)

    def fail(why: str) -> bool:
        log(f"[shop] FAILED: {why} — no bait bought")
        _recover(engine)
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

    # Shop -> Buy Bait -> Basic Bait are three clicks on the *same* spot, so
    # instead of firing three and hoping, keep clicking until the craft window
    # is genuinely up. Under lag a click can land before its button exists and
    # do nothing; repeating is free and self-corrects.
    if not _click_until(engine, cfg.menu_item1, lambda: craft_up(engine),
                        tries=8, wait=cfg.after_click + 0.6):
        return fail("CRAFT window never opened")

    for _ in range(n_plus):                  # quantity: +10 each
        click(cfg.craft_plus, cfg.after_plus)

    # The craft window closing is the one unambiguous "the bait is bought"
    # signal, so it gets the same treatment.
    if not _click_until(engine, cfg.craft_button,
                        lambda: not craft_up(engine), tries=4,
                        wait=cfg.craft_timeout):
        return fail("CRAFT window did not close — purchase unconfirmed")

    # From here the bait IS bought. However messy the exit turns out to be, the
    # purchase must still be credited: not doing so is what had the bot buying
    # over and over every few catches.
    if not leave_dialogue(engine):
        log("[shop] could not close the dialogue cleanly — carrying on")

    # Dismissing the dialogue locks the character briefly; moving during that
    # window silently goes nowhere.
    engine._sleep(cfg.after_nevermind)
    set_rod(engine, True)                    # rod back out, then step forward
    enter_fishing_stance(engine)
    log(f"[shop] done — {bought} bait bought")
    return True


def escape_dialogue(engine) -> bool:
    """Close an NPC dialogue we did not mean to open, and get back to fishing.

    Symptom this exists for: after a purchase the step forward sometimes does
    not move the character (the same stuck-movement bug the rod toggle cures),
    leaving us inside the NPC's radius. The next cast clicks screen centre —
    which in range talks to the NPC instead of charging the rod — so the bot
    sits there re-opening the dialogue while the cast "never charges".

    Returns True if a dialogue was found and dealt with.
    """
    if menu_items(engine) < 2:
        return False
    cfg = engine.cfg.shop
    engine.log("[cast] a dialogue is open — closing it and stepping away")
    # Clicking menu entries needs the cursor free.
    set_shift_lock(engine, False)
    _recover(engine)
    # Toggle the rod: this is the known cure for the stuck movement that
    # stranded us in range to begin with, so the step forward below can work.
    set_rod(engine, False)
    set_rod(engine, True)
    enter_fishing_stance(engine)
    return True


def _recover(engine) -> None:
    """Best effort: get out of whatever dialogue we are stuck in.

    Clicking the craft window's Close, then the last menu entry ('Nevermind')
    a couple of times, walks back out of every page of this NPC's tree. We
    never press Escape — in Roblox that opens the game menu.
    """
    cfg = engine.cfg.shop
    win = engine.window
    try:
        if craft_up(engine):
            x, y = _abs(win, cfg.craft_close)
            engine.mouse.click_at(x, y)
            engine._sleep(0.5)
        # Always send both — 'Back' then 'Nevermind'. Bailing out as soon as the
        # button counter dipped was how the bot ended up half-way out of the
        # tree: Back pressed, Nevermind never, dialogue left open. The counter
        # is not reliable across window sizes, and an extra click on a menu that
        # has already closed is harmless.
        for _ in range(3):
            if not engine._alive():
                break
            x, y = _abs(win, cfg.menu_last)
            engine.mouse.click_at(x, y)
            engine._sleep(0.7)
            if menu_items(engine) < 1:
                break
        # Same post-dismiss lock as the normal exit.
        engine._sleep(cfg.after_nevermind)
    except Exception:                        # recovery must never raise
        pass


def enter_fishing_stance(engine) -> None:
    """Shift lock ON, then step forward into casting position.

    Fishing needs shift lock on so the cursor stays pinned at centre where the
    cast and bite clicks land. Stepping forward is what takes us *out* of the
    NPC's range — which is the point, otherwise a cast click would open the
    dialogue instead — so this is also where `_at_npc` goes false.
    """
    cfg = engine.cfg.shop
    set_shift_lock(engine, True)
    engine.keyboard.tap(SC_W, cfg.walk_tap)
    engine._at_npc = False
    engine._sleep(cfg.after_step)
