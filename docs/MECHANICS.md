# Blox Fruits fishing — measured mechanics

Everything below was measured frame-by-frame from a reference recording
(3840x2160 desktop capture, 60 fps, 60.1 s, 3 complete catches).
The extraction scripts live in `tools/` (`trace_video.py`).

## 1. Cycle / state machine

| # | State | Enter when | Action | Measured timing |
|---|-------|-----------|--------|-----------------|
| 0 | `IDLE` | rod stowed, no line in water | hold LMB to charge, release to cast | charge meter fills in **~0.4 s**; user held 1–2 s |
| 1 | `WAITING` | line in water | watch for the bite marker | bite came **4.5 / 6.5 / 8.1 s** after the cast (user reports up to 20 s) |
| 2 | `BITE` | pink `!` above the head | single LMB click | marker stays up **~0.9 s** — the click must land inside that window |
| 3 | `MINIGAME` | reel bar appears | keep the fish inside the green zone | bar appears **44 frames (0.73 s)** after the bite click; minigame lasts **5.2–5.7 s** |
| 4 | `CATCH` | reel bar disappears | 2 clicks, <0.7 s apart, to dismiss the "Species / Weight" popup | popup appears ~**1.7 s** after the bar disappears |
| — | → `IDLE` | popup dismissed | | |

Full cycle in the recording: ~17–19 s per fish.

## 2. Reel minigame — geometry

Absolute pixels as recorded (3840x2160). The engine does **not** hardcode these —
it re-detects the bar by colour every run — but they are the reference for
normalised ratios.

| Element | Absolute | Normalised |
|---|---|---|
| Track (playfield) | x 1036 → 2798 | width **1762 px**, centred on the viewport (centre 1917 ≈ 1920) |
| Track band (vertical) | y 1558 → 1664 | height 106 px |
| Progress bar | y 1676 → 1698, fills left→right from x 1036 | same width as the track |
| Green zone (player bar) | width **541 px** | **30.7 %** of the track |
| Fish tile | width **153 px** | **8.7 %** of the track |
| Slack (half-zone − half-fish) | **194 px** | 11.0 % of the track each side |

### Colours (BGR, exact)

| Element | BGR |
|---|---|
| Track background | `(33,33,33)` – `(36,36,36)` |
| Green zone | `(16,150,21)` |
| Fish tile background | `(133,153,16)` |
| Zone arrow decoration | `(193,230,195)` |
| Progress fill | `(85,250,149)` → `(36,188,52)` gradient |
| Bite `!` marker | pink: `r>190`, `110<b<200`, `40<g<130` |

> The pale arrow inside the green zone is **cosmetic**. It alternates
> left/right every ~16 frames (0.27 s) regardless of what the fish or the
> zone are doing — verified over all three minigames. Do not treat it as a tell.

## 3. Reel minigame — physics

### Player zone: a pure double integrator (bang-bang input)

Fitted from 36 monotonic velocity ramps across the three minigames:

```
a_hold    = +0.95 px/frame^2   (LMB held   -> accelerate right)
a_release = -0.93 px/frame^2   (LMB up     -> accelerate left)
```

Symmetric to within 2 % → **no gravity term, no drag**: the zone is
`z'' = ±a`, sign chosen by the mouse button. Speed saturates at

```
|v_max| ~= 16 px/frame = 960 px/s
```

Normalised by the track width (units of "track widths"):

```
a     = 1.92 track/s^2
v_max = 0.545 track/s
```

This is what makes the game hard: you cannot *stop* the zone, only reverse its
acceleration, so every correction has to be started early. It is also what makes
it easy to automate — time-optimal control of a double integrator is a
closed-form switching curve (see §5).

### Fish: constant speed, instant random reversals

The fish never accelerates. It moves at a fixed speed and flips direction
instantly, at random.

```
speed (per catch)      2.34 / 1.22 / 1.90 px/frame  =  140 / 73 / 114 px/s
                       = 4.1 % .. 8.0 % of the track per second
direction run lengths  20 .. 164 frames  (0.33 .. 2.7 s)
```

Speed differs per fish, so the engine estimates it live instead of assuming it.

The zone is **~7–13x faster** than the fish. The whole difficulty is the
momentum, not the fish's speed.

### Progress bar

* Starts at **50 %** of the track width (verified: the detector reads 0.506 on
  the first frame of every minigame and 1.000 on the last).
* Fills at **+0.068 /s** while the fish is inside the zone. Averaged over a
  whole catch including the fade-in it works out at ~0.093 /s, i.e. **50 % →
  100 % in ~5.4 s** of perfect tracking.
* Drains at about **−0.034 /s** while the fish is outside — half the fill rate.
  Measured across the one lapse in the recording (frames 2994–3010): progress
  stalls, then bleeds slowly. Losing the fish briefly is recoverable.

### The escape alarm

**This is the one that will break a naive bot.** When the fish leaves the zone,
the game plays an alarm animation over ~0.3 s:

* the green zone desaturates all the way to grey-green — `(16,150,21)` sweeps
  through `(40,114,43)` → `(58,86,59)` → `(70,86,71)` → `(87,108,87)` and back;
* the fish tile shifts from teal `(133,153,16)` to blue `(142,95,24)`.

**And the fade does not stop there.** The `(70,86,71)` above is a *mid-flash*
sample. If the fish stays out — which is exactly what a chest grab causes, since
the zone parks off the fish for 2.5 s — the zone keeps desaturating until it is
**fully neutral**:

```
fr320 (42,113, 45)   g-b +71     zone still green
fr330 (70, 86, 71)   g-b +16     the "grey-green" previously measured
fr342 (84, 88, 83)   g-b  +4
fr360 (89, 89, 89)   g-b   0     neutral — and it stays here
empty track (33,33,33)
```

At `g-b = 0` any green-dominance test loses the entire zone, `read_bar` returns
None, six frames later the engine calls the catch finished and clicks the
dismiss twice — mid-fight. That is the "stops recognising the fish" bug, and it
correlates with the zone sitting far left only because that is where a chest
grab strands it.

Brightness is what separates the settled alarm from the empty track (89 vs 33),
so the *tracking* mask accepts either green-dominant **or** neutral-and-bright.
That has a sting: grey scenery also satisfies "neutral and bright", so once the
bar is gone a locked-on strip keeps reporting a zone and the reel never ends.
Two guards handle it:

* **locating** the bar (`zone_mask`) stays strict green-dominant — the search
  region still contains scenery, and the zone is always green when a minigame
  starts;
* **tracking** it (`zone_mask_tracking`) is permissive, but `read_bar` first
  requires the track's dark background to still cover `bar_track_min_frac` of
  the strip.

That last gate is a **floor, not a discriminator**, and it took two field
reports to learn the difference. It was set at 0.35 from one machine, where
coverage runs 0.53–0.61. Elsewhere, mid-fight:

| recording | track coverage while running | why |
|---|---|---|
| author, 4K | 0.530–0.609 | — |
| tester A, 1080p | **0.273**–0.526 | a bright effect shining through the semi-transparent bar |
| tester B, 1080p | **0.036**–0.165 | track renders `(24,32,32)`; `track_mask` demanded the channels agree within 6 |

Every frame under the threshold is a frame `read_bar` returns `None`, the reel
loop `continue`s, and **the controller is not driven**. The fight carries on
un-steered — 29% of one catch — which from outside looks exactly like the bot
giving up mid-fish.

Both were fixed by measurement, not by guessing: `track_neutral_tol` (12, was a
hardcoded 6) makes the mask see tester B's track at 0.548 instead of 0.036, and
the threshold drops to 0.12. That costs nothing, because across 713 frames from
three recordings taken *after* the bar was gone, this gate rejected **none** of
them at any threshold down to 0.0 — `read_bar`'s zone-column test was already
doing the whole job.

Measured over 1252 frames, three machines: frames steered while the minigame
was live went from **297/539 (55%) to 539/539 (100%)**, with phantom reads
staying at **0/713**.

Verified: the failing recording goes from 4 phantom "catches" to **0**, while
the reference still yields exactly 3 minigames with the same boundaries and
unchanged accuracy (zone p95 0.00 px, fish p95 1.00 px).

A fixed "reference colour ± tolerance" match loses both the zone *and* the fish
for the whole animation — precisely when the controller most needs to see. The
detectors here use relational predicates instead:

```
zone:  g > b+10  and  g > r+10  and  g > 62  and  |b-r| <= 16
fish:  b > 110   and  b > r+60  and  g > r+30
```

Both verified pixel-by-pixel across frames 2985–3025: zone blob stays
42k–53k px, fish blob 13.9k–14.1k px, neither ever drops out.

The alarm is also a free "you are losing" signal if a future version wants it.

## 4. Bite marker detection

A bright **magenta-pink ring with an exclamation mark**, a billboard above the
character (~150x150 px at 4K, but it scales with camera zoom). Bite windows in
the reference recording: frames 663–693, 1712–1768, 2792–2872.

The marker is a fixed game sprite, so it looks the same for every player — but
*where* and *how big* it is on screen is not fixed. Its height above the ground
tracks the character's head, which varies by avatar and camera zoom, and its
size scales with distance. So a detector must not assume a fixed position or a
fixed pixel count.

Colour, measured from the reference (night) marker (HSV, OpenCV ranges H 0-179):

```
Hue        160 .. 178   (magenta-pink — NOT pure red, which is H 0-8)
Saturation 109 .. 172
Value      195 .. 247   (bright)
```

**The marker is semi-transparent.** Over a dark night background the pink is
vivid (the numbers above). Over a bright daytime background (sand, sky) the
background bleeds through the glow: saturation drops (down toward ~45) and the
hue shifts a little toward salmon, so a night-tuned `S≥80` mask catches only the
parts of the ring over dark pixels — the ring fragments into arcs too small to
pass the size gate, and a real daytime bite is *missed*. The `sat`/`val` floors
are therefore kept low (`S≥45, V≥110`). That is safe only because the tight ROI
excludes the player list and the character wears no red — nothing else pink can
be in the box — so widening the colour band cannot re-introduce a false bite.

The hue is the load-bearing fact: it sits on the magenta side of red, so a
pure-red costume item (a candy cane, H ~0-8) does **not** match, while the
marker does regardless of the avatar. Detection (see `vision.find_bite_marker`):

1. HSV mask `H∈[158,179], S≥80, V≥150`.
2. morphological close → the ring + `!` become one solid blob.
3. connected components → accept a blob whose **larger side clears ~5.5 % of the
   ROI width** (the decisive gate), is roughly the right aspect (0.40–2.30) and
   is reasonably filled.
4. the engine requires the marker to persist a couple of polls before acting
   (the real one is up ~0.9 s), rejecting one-frame flukes.

Sizes, measured (at 4K): the marker's ring is ~150×156 px and the `!` is
44×96 px, so the larger side is always ≥ ~90 px. Every competing red/pink UI
element is much smaller — a player-list faction icon is ≤56 px, a colour speck
smaller still — so the size gate separates them cleanly.

### Two detectors this replaced, and why

* The **very first** bite detector counted pixels inside a hard-coded BGR box
  over a fixed rectangle. It broke across characters/cameras: a differently-lit
  marker fell outside the box, a zoomed marker fell under the count threshold.
* The **second** used the HSV hue + a compact-blob test, but over a wide ROI
  (`x 0.12–0.88`). That ROI reached into the **top-right player/bounty list**,
  whose red faction row (`~113×35` plus square `~31–56 px` icons) matches the
  marker hue. On busy servers that row is always present, so **every cast
  false-triggered a bite → no reel bar → recast**, an endless loop. The user
  first saw it correlate with "morning", which was really "more players online,
  so the list shows a red row". The fixes: a **tight centre ROI** that excludes
  the player list, and the **size floor** above (a player-list icon can never be
  as large as the ring).

### Usage requirements this detector assumes

* **Shift lock on**, so the character stays horizontally centred and the marker
  lands in the tight ROI. (Shift lock is also needed for the auto-buy step.)
* **Same-size characters** — the ROI is a fixed band tuned to the standard
  avatar height; a very different height could ride out of it.
* **No red/pink items worn** — a red cosmetic near the head could enter the ROI
  and, if large enough, mimic the marker.

## 5. Control law

The zone is `z'' = u*a`, `u ∈ {+1 (hold), −1 (release)}`; the target is the fish
centre `x_f` moving at roughly constant `v_f`. Time-optimal tracking is
bang-bang on the switching curve:

```
e  = x_f - z                     position error
ev = v_f - v_z                   velocity error
s  = e + 0.5 * ev * |ev| / a     switching function
u  = HOLD    if s > 0
     RELEASE otherwise
```

`s > 0` means "even at maximum braking you would still land short" → keep
accelerating right. Near `s = 0` the law chatters at the loop rate, which is
exactly what is wanted: ~50 % duty cycle averages to zero acceleration and the
zone coasts along with the fish.

Both `z` and `x_f` are extrapolated forward by the measured input+capture
latency before `s` is evaluated, so the switch happens at the right *game*
frame rather than the right *capture* frame.

## 6. Capture rate ceiling

Worth recording because it shapes the engine: on Windows, DWM throttles screen
capture to the display refresh. Measured on this machine, every grab costs
**exactly 16.66 ms (60 Hz)** regardless of region size and regardless of API:

```
mss 1765x100   16.65 ms      raw GDI BitBlt          16.66 ms
mss 1765x20    16.66 ms      BitBlt + GetDIBits      16.67 ms
mss 400x100    16.67 ms
mss 1920x700   16.62 ms
```

Consequences baked into the engine:

* the reel loop runs **unpaced** — the blocking grab is its own clock; adding a
  sleep on top only risks missing a vblank and halving the real rate;
* the playfield and the progress bar are read from **one** rectangle, because
  a second grab would cost another whole frame;
* 60 Hz is fine — the simulator holds a 100 % catch rate down to 30 Hz.

## 7. Cast charge meter (the "did the cast take" signal)

While you hold LMB to cast, a thin bright-green bar fills next to the character:

* colour **`(16,249,31)`** (BGR) — brighter and purer green than anything else
  on the playfield;
* ~**13 px wide x 240 px tall** at 4K, world-anchored (moves with the camera);
* the busiest column carries ~**240 green pixels while charging, 0 otherwise** —
  measured 193–250 during a hold, 0–4 at all other times. A clean 50:1 signal.

This matters because of a failure found in bot testing: **the first cast right
after a catch often does not throw.** The press is swallowed — usually by the
tail of the catch dialog (a record fish adds an *"It's the biggest one you've
found!"* page on top of Species/Weight), sometimes by the rod not being ready
yet — and no bait goes out, so the bot then waits for a bite that can never come.
The meter is the reliable "did it actually charge" check: the engine watches for
it while holding and, if it never lights, releases and casts again. A swallowed
press merely dismisses whatever ate it, so the retry lands cleanly.

There is also a separate **"MASTERY LEVEL UP!"** toast (top-right) that can fire
on a catch; it auto-dismisses and needs no click.

## 8. Buying bait — Fisherman (path F)

Mapped frame-by-frame from a reference recording (two purchase cycles, x10
then x20). Positions are logical desktop pixels at 1920x1080 with the game
window 1920x1032, read off the on-screen mouse-position overlay.

### Dialogue tree

The option we want is always the **first** entry, so the same click position
works three times running:

| # | Click | Position | Result |
|---|---|---|---|
| 1 | screen centre | (960, 527) | `Interact` → dialogue opens |
| 2 | menu item 1 | (1438, 535) | `Shop` |
| 3 | menu item 1 | (1438, 535) | `Buy Bait` |
| 4 | menu item 1 | (1438, 535) | `Basic Bait` → CRAFT window |
| 5 | `+` ×(N/10−1) | (1222, 558) | quantity 10 → 20 → 30 … |
| 6 | `Craft` | (960, 688) | buys, window closes |
| 7 | menu last | (1438, 710) | `Back` → main menu |
| 8 | menu last | (1438, 710) | `Nevermind` → dialogue closes |

`Back` and `Nevermind` are both the **last** entry of their menu and land on the
same spot, so one position covers both.

These are the measured button **centres**, not where the cursor happened to sit
in the recording. The observed positions — `Shop` at (1409,510), `Nevermind` at
(1348,676) — worked, but sat within a pixel or two of a button's top edge; the
menu rows are 43 logical px tall at y 535 / 596 / 651 / 710, so the centres give
~20 px of margin in every direction.

### The dialogue does not open on a fixed delay

The first live run failed with a stuck menu. Cause: the sequence used fixed
sleeps, and clicked `Shop` **0.3 s before the menu existed**, so every
subsequent click landed on nothing while the code reported success.

Menu-open latency after the `Interact` click, measured:

```
reference run ("How's the fishing?")            ~1.0 s
failing run   ("You're low on Bait, ...")       ~1.7 s
```

The greeting differs when you are low on bait, and it is slower. So the shop
sequence **waits for UI states** rather than sleeping:

| Gate | Detector |
|---|---|
| dialogue really open | ≥2 light-grey menu buttons in the menu ROI |
| CRAFT window open / closed | its wide yellow title bar |
| dialogue closed | <2 menu buttons |

Verified against both recordings, including the exact frame where the dialogue
*text* was up but the buttons were not (frame 840 of the failing run): the
detector correctly reports "not open" there, which is precisely the moment the
old code clicked into the void. A gate that times out fails the purchase
honestly — it does **not** credit the bait — clicks its way back out of the
dialogue, and after `shop.max_failures` consecutive failures stops trying.

### Quantity

CRAFT opens at **10 bait for 1000 Money**; each `+` adds **10** (verified: one
click took it to 20 / 2000 Money, i.e. 100 Money per bait). Buying N bait is
`N/10 − 1` clicks on `+`.

### The post-catch stuck state (game bug) — stow the rod to clear it

After a catch the character sometimes locks up **with no dialogue on screen**:
movement keys do nothing. Seen in a reference recording — the Angelfish is
landed at 2.7 s and the character then sits in the *same spot from 5.4 s to
32.4 s* while the bot taps `S` and retries, because there is nothing on screen
to detect and nothing wrong with the input.

The cure is to **toggle the fishing rod out of and back into the character's
hands** with its hotbar key. So the purchase does:

```
rod OFF  (hotbar key)      <- clears the lock, before any movement
S        walk back to the NPC
Shift    release shift lock
... Interact / Shop / Buy Bait / Basic Bait / + / Craft / Back / Nevermind ...
wait     ~1.5 s post-dismiss lock
rod ON   (hotbar key)      <- draw it again, then step forward
Shift    shift lock on
W        step into casting position
```

The hotbar key is a **toggle**, so — like shift lock — the state is tracked
(`engine._rod_equipped`) rather than pressed blind, anchored by the checklist
requiring the rod to be equipped at start. The slot is asked for on launch and
stored as `rod_slot`. A failed purchase re-draws the rod too, so the bot never
returns to fishing with the rod stowed.

Doing this unconditionally on every bait trip is cheaper and more reliable than
trying to detect the stuck state, and costs one key press.

### Casting while still in the NPC's radius re-opens the dialogue

A purchase can succeed and *still* strand the bot, because the step forward
afterwards is subject to the same stuck-movement bug. Observed in
a reference recording:

```
[shop] no dialogue (attempt 1) — stepping back again   <- the S step also failed once
[shop] done — 10 bait bought                            <- purchase itself fine
[bait] topped up to 11
[cast] no charge — retrying (1/4)
[cast] no charge — retrying (2/4)
```

The `W` step never moved the character, so it was still inside the NPC's
radius, where a click at screen centre **talks to him instead of charging the
rod** — the recording shows the dialogue re-opening on each cast attempt. The
cast verification correctly reported "no charge", but the plain retry just
clicked again and re-opened the dialogue, forever.

So a failed charge now checks whether a dialogue is open, and if so:

```
Shift -> OFF      release the cursor
Nevermind ...     close the dialogue
rod OFF, rod ON   clear the stuck movement that stranded us in range
Shift -> ON, W    step out of range
                  then retry the cast
```

With no dialogue present the check costs one screen grab and the retry behaves
exactly as before.

### The rod flick — skipping the catch card entirely

Flicking the rod (unequip, then immediately re-equip) the moment a catch lands
stops the Species/Weight card from ever rendering. No card means nothing to wait
for and nothing to click, which deletes the whole tail of the catch cycle.

Measured on a reference recording: bar gone at ~3.2 s, **no card at any
point** (mastery ticks 41 -> 42, so the catch did register), charge meter already
filling by 4.5 s. Post-catch cost in the bot drops to **~0.58 s**, against ~0.9 s
for the click path and ~8-10 s back when it waited for the card to fade.

Two details make it work:

* the presses must be **fast** (`rod_flick_gap` 0.08 s), so this bypasses
  `set_rod`, whose per-press settle would be far too slow;
* the two presses cancel, so the tracked rod state is unchanged.

The recipe note is still checked afterwards. The flick normally beats it to the
screen, but a lag spike can let it through, and that one never leaves on its own.
Set `timing.rod_flick` to `false` to fall back to the old click path.

### Catch popups must be gone before the next action

Every popup the game lays over the middle of the screen — the Species/Weight
card, the NPC dialogue, the recipe note — is the same dark, flat, blue-dominant
panel, which makes one detector enough. Measured over the centre band
(`x 0.20-0.80, y 0.46-0.60` of the window):

```
clear water          coverage <0.01
catch card up        coverage  0.57-0.67   (lasts ~1.2 s, then fades)
recipe note up       coverage  0.69        (never fades on its own)
```

**Not all of them can be dismissed, and waiting for them is expensive.** A
"first one you've found!" card measured **8 s** on screen, ignoring both dismiss
clicks and clearing only on the game's own schedule. Blocking until the centre
was clear therefore cost ~8 s on *every* such catch — about a 40 % throughput
loss — for no benefit.

So the two paths are treated differently:

* **recasting** does not wait. Casting through a card is safe: `verify_cast`
  watches the charge meter and simply re-presses if the card ate the click.
  Post-catch cost is ~0.9 s with a card up, ~2.5 s worst case.
* **buying** does wait (`wait_popup_clear`), because the NPC clicks have no
  such retry to fall back on and a swallowed one derails the whole sequence.

The recipe note is the exception on both paths — it never leaves by itself, so
`clear_recipe_note()` checks for it after every catch. That costs a single
screen grab and never blocks.

### The "new recipe" note never times out

About 0.1 % of catches yield a recipe note — *"You found a note in a bottle…
A new Fish Kebab recipe!"* — which sits there until **Learn** is clicked. Left
alone it blocks the run indefinitely.

It is recognised by its `Learn` button: a navy panel carrying white text, at
`x 0.660-0.840, y 0.470-0.570` of the window. Requiring both the navy panel
*and* white pixels keeps scenery out — across a 139 s session recording the test
fired exactly once, on the frame the note appeared, and never otherwise.

Clicking it needs the cursor free, and shift lock pins the cursor to centre, so
the lock is dropped for the click and restored to exactly what it was. The
button's centre is `(0.7484, 0.5155)` — logical `(1437, 532)`; the player's own
clicks in the recording landed at `(1373, 512)` and `(1364, 497)`, both inside
it.

### You must step away from the NPC to fish

Standing inside interaction range, a click at screen centre opens the
**dialogue**; outside it, the same click **charges the rod**. That is why the
bot presses Left Shift + `W` on start-up — and it is also a trap: the second
live failure was the bot stepping away for casting position, then trying to buy
without walking back, so its "Interact" click charged a cast instead (the green
charge meter is visible in the recording at 13 s and 16 s while the log sat
waiting for a dialogue that could never appear).

So "do I need to walk back?" is tracked as **state** (`engine._at_npc`), not as
a "first purchase" flag: `enter_fishing_stance()` clears it, and the purchase
sets it again after tapping `S`. If the dialogue still does not open, the bot
taps `S` and retries up to `shop.max_approach_attempts` times before failing.

### Getting in and out

Shift lock pins the cursor to screen centre, and the two phases need it in
**opposite** states:

| Phase | Shift lock | Why |
|---|---|---|
| fishing | **ON** | cast and bite clicks must land at centre |
| dialogue | **OFF** | with it on the cursor *cannot leave centre*, so every menu click lands mid-screen and nothing is ever pressed |

It is a **toggle**, so firing Left Shift without knowing the current state is a
coin flip. The engine tracks it (`engine._shift_lock`), anchored by the user
starting with it off, and drives it to whatever each phase needs:

```
start (at NPC, lock OFF)   Shift->ON, W                 -> fishing
buy                        S            (walk back)
                           Shift->OFF   (cursor freed)
                           Interact, Shop, Buy Bait, Basic Bait,
                           +, Craft, Back, Nevermind
                           Shift->ON, W                 -> fishing
```

This was the third live failure, and a silent one: the previous version held
shift lock on through the whole dialogue, so every menu click piled up on the
centre of the screen.

* Releasing shift lock also **leaves the cursor at centre** — exactly where the
  `Interact` prompt is — so no separate centring step is needed.
* A **S** tap walks back into range and the `Interact` prompt reappears
  (~0.5 s), then the sequence repeats from step 1.
* A failed purchase still restores the fishing state (lock ON, one step off the
  NPC), so the next cast cannot re-open the dialogue it just backed out of.

### Roblox ignores SetCursorPos

The fourth live failure, and the one that looked impossible: the recording shows
the cursor sitting **visibly on the `Shop` button** at exactly (1438, 535) — dead
centre of its 100x43 box — the on-screen overlay reporting `[CLICK]`, and the
menu simply not reacting. Four seconds of that, then the same for the
`Nevermind` recovery clicks.

Menu geometry was identical in the failing run, the earlier working recording,
and a manual run by the user, so position was not the problem:

```
Shop button   phys x2777-2975 y1027-1113   logical centre (1438,535)
              — identical in all three recordings
```

The tell is *which* click worked: `Interact` did (the dialogue opened), and it
was the one click the bot did **not** have to move for — releasing shift lock
leaves the cursor at centre, which is where the Interact prompt is. Every click
that required moving first, failed.

Cause: the code moved with `SetCursorPos`, which repositions the OS cursor
without injecting a mouse event. **Roblox tracks its GUI cursor from the input
stream, not by polling `GetCursorPos`**, so the game's cursor never left centre.
The clicks were dispatched at *Roblox's* idea of the cursor — the middle of the
screen — where there is no button.

Fix: move with `SendInput(MOUSEEVENTF_MOVE | ABSOLUTE | VIRTUALDESK)`, which is
a genuine mouse move that the game processes, then `SetCursorPos` to nail the
exact pixel (the 0..65535 normalised grid can land a pixel off). Each click also
moves in **two hops** (8 px away, then the target) so there is always a real
delta even if the cursor already happened to be on the button.

Verified on the target machine: all five click targets land on the exact pixel.

### The menu redraws progressively — 'Nevermind' arrives last

After `Back`, the dialogue does not simply swap contents. Measured frame by
frame:

```
 9.8 s  'Basic Bait / Back'          2 buttons
10.2 s  BLANK                        0-1 buttons   <- menu is empty here
10.7 s  Shop / Fishing Index / Job Stats   3 buttons  <- no 'Nevermind' yet
11.0 s  ... + Nevermind              4 buttons     <- only now clickable
```

A fixed 0.6 s wait after `Back` fired the `Nevermind` click straight into the
blank gap, so the dialogue stayed open and the bot walked off still talking to
the NPC. The exit therefore waits for the menu to reach its **full 4 buttons**
before pressing the last entry. Counting is enough to tell the states apart:
bait menu 2, transition 0-1, partial main menu 3, complete main menu 4.

Dismissing with `Nevermind` also **locks the character for ~1.5 s**; walking
during that window goes nowhere and leaves the bot short of its fishing spot,
so the exit waits it out (`shop.after_nevermind`) before pressing Shift + W.

### Detect the CRAFT window by its button, not its title bar

The title bar spans the *top-middle* of the screen — exactly where an editor or
terminal tends to sit. In one failing run VS Code covered its left half, so the
yellow bar measured **714 px instead of 1444**, fell under the width threshold,
and "is the craft window open?" answered no for the entire timeout while the
window was plainly open on screen.

The check now looks for the yellow **Craft button** (lower-middle, 302-304 px
wide in a ~768 px band), which is clear of the usual window furniture and is the
thing about to be clicked anyway. Validated across 9 states from four
recordings, including the occluded frame that defeated the old check: 0
mismatches.

More generally: **anything overlapping the game window can blind a detector.**
The pre-flight checklist says so.

### Timings measured

interact → menu ≈ 1.0 s; each menu click → next menu ≈ 0.5 s; `Craft` → back on
the bait menu ≈ 1.0–1.5 s. Config defaults are set a little longer than these.

### When to buy

At **1 bait, not 0** — at zero the game unequips the bait and the next cast
would do nothing. Bait is consumed when a **bite registers** (a bite that gets
away still costs it), which is where the engine decrements.

### Selling the stock

Mapped from a reference recording. Same NPC, and the layout is convenient —
two of the three clicks reuse positions the buy route already knows:

| # | Click | Position | Result |
|---|---|---|---|
| 1 | screen centre | (960, 527) | `Interact` |
| 2 | menu item 1 | (1438, 530) | `Shop` |
| 3 | **menu item 2** | **(1438, 590)** | `Sell Fish` |
| 4 | menu item 1 | (1438, 530) | `Confirm` |

`Sell Fish` is the **second** entry — the only place in either route that is not
the first or last slot — and `Confirm` lands back on the first. After Confirm
the dialogue closes itself; the recording shows the balance going
`$72,935,060 -> $73,214,076` on a single sale.

Nothing needs protecting: the NPC states outright that it will not buy
favourited fish or your heaviest.

Approach, rod handling and shift lock are identical to buying, so both routes
share `open_npc_dialogue()` — popup clear, stow rod, walk back, drop shift lock,
Interact, confirmed by the menu appearing.

## 9. Treasure chests (part 3)

Chests appear on the reel track **1-4 s after the bite**, at random, as a
gold/amber tile the same size as the fish tile (~8.7 % of the track).

| Property | Behaviour |
|---|---|
| Movement | **None** — the chest is static once it appears |
| Collecting | keep the zone over it for **1.5-2 s** |
| While collecting | the tile's background washes toward **white** |
| After collecting | the icon becomes an **open** chest; the tile **stays on the bar** |
| Fish overlap | if the fish is over the chest you may finish the catch first — that is fine |

### Why the engine does not watch the collect animation

Both of the "after" states are traps for a colour detector: the tile whitens
*while* collecting (so a gold test drops out mid-grab, exactly when we must not
lose it) and stays on the bar afterwards (so a gold test would re-trigger on a
spent chest forever).

Since the chest never moves, the engine instead:

1. detects a gold tile, and takes its centre;
2. parks the zone on that **remembered** position for a fixed hold
   (`chest.hold`, default 2.5 s — the margin over the 1.5-2 s requirement);
3. records the position as done and returns to tracking the fish.

Positions are compared with a tolerance (`chest.same_chest_frac`), so the spent
tile is never grabbed twice, and a genuinely different chest still is.

### Cost of a detour

Progress drains at ~0.034/s while the fish is outside the zone (§3), so a 2.5 s
grab costs ~0.085 of the bar — about 1.25 s of tracking to earn back, out of a
~5 s catch. Affordable, but not when the catch is already failing, so a chest is
skipped if progress is under `chest.min_progress` (default 0.20).

### Detection

The chest is warm (red >= green >> blue). Everything else on the playfield is
cool or neutral — zone green `(16,150,21)`, fish teal `(133,153,16)`, track grey
`(33,33,33)` — so a warm-dominant test cannot collide with the fish:

```
chest:  r > 140  and  g > 90  and  b < 130  and  r > b+60  and  g > b+20
```

The zone-edge reconstruction in `read_bar` now treats **either** the fish or the
chest as the occluder, since either can straddle an end of the zone and make it
read short.

### The real cause: find_bar locked onto the health bar

Everything below this heading was a symptom. The cause was in `find_bar`.

It located the bar by taking the **largest green blob** in the search region.
The player's **health bar** is also green, also wide, and also sits inside that
region - so on many layouts it is the largest green blob on screen. `find_bar`
locked onto it, scanned the rows beneath it for a progress strip, found nothing,
and reported "no minigame" for the entire fight.

Measured on a user capture (854x480): `find_bar` succeeded on **5 of 220
frames** - and only on the frames where the reel zone happened to be
momentarily larger than the health bar:

```
qualifying green blobs at fr70
  x16  y176  164x26  area 2140   <- health bar  (picked: largest)
  (reel zone not even in the running)
scanning rows under it -> no progress strip -> rejected
```

That is why the bug followed some machines and not others, and why every
threshold fix only moved it around: on the author's layout the zone won the
size contest, so it never reproduced there.

The fix is structural: **try every green candidate, not just the biggest**, and
let the progress strip underneath decide which one is really the bar. Same
capture afterwards: bar acquired once, then **132 frames tracked continuously,
0 % fish loss, 0 premature ends**.

Two supporting changes:

* `_row_span` replaces "longest contiguous run" when measuring the progress
  strip. On a compressed or downscaled capture the strip breaks into fragments,
  and the largest fragment alone falls under the width threshold.
* `_minigame_running()` is an independent witness used before every cast. It
  looks **only** for the progress strip - never the zone - so even if the bar
  detector fails again for some new reason, the bot cannot cast into a live
  minigame. Verified it does not fire on a blank screen or on a health bar
  alone.

### "Forgot it was fishing" — a 100 ms gap was enough

The real structural fault, found after several rounds of adding witnesses. The
loop ended a catch on `bar_lost_frames = 6`, and the reel loop is **unpaced** —
it runs at whatever the capture allows, 60-140 Hz. Six frames is therefore
**~100 ms**. Any hiccup that brief — a lighting change, a sprite crossing the
zone, a dropped frame — was enough to declare the fish caught, fire two dismiss
clicks into a live minigame and recast.

Every earlier fix was of the form "add another witness so the detector does not
hiccup" (§ zone alarm, § progress strip). Each held until some new condition
defeated that witness. The threshold itself was the bug.

Two changes:

* **Loss is timed, not counted.** `bar_lost_seconds = 0.9`. A bar that has
  genuinely gone stays gone, so a generous interval costs nothing; a hiccup no
  longer ends anything.
* **A catch needs positive evidence.** When the bar does go, `_confirm_catch()`
  spends the wait already owed to the catch card watching for either outcome —
  the card appearing (it really was a catch) or **the bar coming back** (we
  never lost the fish). It clicks nothing. If the bar returns, `_reel` reports
  *not a catch*, so no dismiss clicks fire and the caller simply resumes.

The point of the second one is that a false negative is now cheap — the loop
re-acquires and carries on — while a false positive used to be destructive.

### The zone is not the witness either

The recurring bug where the bot abandons a live catch, clicks the dismiss and
recasts, was structural rather than a tuning problem. `_reel` ended like this:

```python
if st is None:              # read_bar could not read the ZONE
    lost += 1
    if lost >= bar_lost_frames:
        break               # ...treated as "the catch is finished"
```

So **any** six-frame failure to read the zone was reported to the caller as a
completed catch — which then fires the two dismiss clicks and a fresh cast, in
the middle of a fight.

Greying the zone out (§3) was one way to trigger it; a chest is another, and
worse. A chest grab deliberately parks the zone *on* the chest, and the chest
tile is ~153 px: against a beginner rod's short zone that covers nearly all of
it, for the full 2.5 s hold.

The fix is to stop asking the zone. The **progress strip** is the honest
witness: it spans the whole track as one unbroken two-tone run with *nothing
ever drawn over it* — which is precisely why `find_bar` uses it to measure the
track — and it exists for exactly as long as the minigame does. So a catch is
only declared over once that strip is gone; while it is still there the loop
holds its last decision and waits for the zone to reappear, bounded as ever by
`max_reel_seconds`.

Measured against the engine's own tracking on a real session:

```
while read_bar works (catch demonstrably live)   strip present 430/430, absent 0
at the moment the old code would have ended      strip absent (correct)
```

Zero false holds at a genuine end, and no live catch where the strip is
missing.

### Phantom bars on green terrain

Sweeping 54 recordings for chests turned up no chest, but did surface a latent
bug: on grassy islands the terrain satisfies the zone-green test and the yellow
`Items`/`Shop` HUD buttons satisfy the progress-bar test, so `find_bar` returned
a "minigame" that was really a hillside. Left alone that sends the engine into a
phantom reel and, on exit, fires the two catch-dismiss clicks for nothing.

The track is a fixed-scale Roblox GUI element, so its share of the window is
constant. Measured:

```
genuine bar   0.460 of window width   (identical in every session)
phantom bars  0.222 / 0.271 / 0.287 / 0.378 / 0.621 / 0.825
```

Bounding the width to `0.40 - 0.55` of the window keeps every genuine bar and
rejects all six phantoms.

> **Not yet validated against a recording.** No chest occurs in any capture
> available here, so the colour thresholds above come from screenshots rather
> than measured pixels. What *is* verified: the detector produces **zero false
> positives** across 942 real minigame frames, and zone/fish accuracy is
> unchanged (zone p95 0.00 px, fish p95 1.00 px). A single recording of a chest
> appearing would let the thresholds be measured properly.

## 10. Not yet modelled

* **Chest mechanic** (part 3 of the project) — does not occur in this recording.
* Whether the zone's velocity is zeroed when it hits a track edge (the player
  never reached one).
* The cast-power meter is a world-anchored vertical bar next to the character,
  so it moves with the camera. The engine holds for a fixed time instead of
  reading it.
