# Known issues & how to avoid them

**Read this first — it explains why this bot is fussy.**

The macro never touches the game. It only *looks at your screen* — the same
pixels you look at — and moves the mouse. That is what keeps it on the safe
side of an exploit, but it also means **anything that changes those pixels can
confuse it**: a cosmetic, a fruit's particles, the time of day, your GPU's
colors, another window on top. Most of the problems below are not "the program
is broken" — they are the screen looking different from what it expects. The
good news is almost all of them are avoidable once you know what to keep off
your screen.

Everything here was seen in a real recording, not guessed.

---

## 0. The bar tracks the fish, then slides to one side and gives up

**This is the single most common report, and it is almost always one thing:
the macro is not running as administrator.**

If Roblox is running elevated (as admin) and the macro is not, Windows
**silently discards every click and keypress the macro sends** — a security
rule called UIPI. The macro's detection and control are working perfectly; its
input just never arrives. The fishing zone gets no steering, drifts to whichever
edge the game pulls it toward, and the fish escapes. The **same rule** is why
**`F2`/`F4` do nothing** for some people — the global hotkey is blocked too.

**Fix:** run the macro as administrator. **Right-click `easy_run.py` → *Run as
administrator*** (say Yes to the popup), or right-click your terminal → *Run as
administrator* and run `python easy_run.py`.

There is also a rarer, intermittent version of the same symptom — the zone jams
against a wall for one reel out of several even when input generally works,
because a single mouse event was dropped in transit. The macro now re-asserts
the button a few times a second so a dropped event heals within ~0.15 s instead
of costing the whole fight (v1.2.1+). Make sure you are on the latest build.

---

## 1. False bites, or missed bites (the bite `!` detector)

The bot watches a box above your character for the pink **`!`** that pops up
when a fish bites. It looks for a bright magenta-pink ring. **Anything else
pink, red, or brightly glowing in that box can set it off** — or, if it is
washed out, hide the real one.

**Causes:**

- **Red or pink items near your head** — hats, hair, faces, halos, crowns.
  Candy-cane and valentine cosmetics are the classic offenders.
- **Fruit auras and particles that hang around your body.** These sit right in
  the bite box:
  - **Gas** — a purple gaseous aura wraps your whole body ([wiki](https://blox-fruits.fandom.com/wiki/Gas)).
  - **Rumble / Lightning** — an ambient electric glow ([wiki](https://blox-fruits.fandom.com/wiki/Rumble)).
  - **Light, Portal, Spirit, Dragon, Kitsune, Leopard, Dough** and other
    transformation fruits — bright, colored effects around the character.
  - **Skull Guitar** and other particle-emitting accessories.
  - Any **skill animation** you leave lingering next to you.
- **The top-right Player / Bounty list**, if the bite box is calibrated too
  wide — the red *Pirates* / *Marines* rows look like a `!`.
- **Bright daytime sun-glare** washing the pink marker out so it is missed, or
  a bright background that happens to match it.

**Avoid it:** farm with a **plain avatar**, nothing red or pink near the head,
**no aura fruit equipped**, and keep the **Bite marker** box snug around your
character in *Calibrate controls* so it never reaches the player list.

---

## 2. "It forgot it was fishing" / bar not found / reel runs forever

The bot finds the reel bar by its shape: a **green zone with a thin two-tone
progress strip beneath it**. When it can't, it either never starts reeling, or
latches onto the wrong thing and reels a phantom.

**Causes:**

- **The Reel bar band box is calibrated wrong.**
  - *Too wide* — it can lock onto your green **health / energy bars** (left),
    the **Power / Mastery** strip (right), or the **level XP bar** (bottom-left).
    None of those are the reel bar.
  - *Too narrow* — it cuts the bar off and mis-measures its width, so the
    controller then aims against the wrong size.
- **Daytime.** The fishing UI is semi-transparent, so bright water and
  sun-glare bleed *through* the bar and shift the colors it keys on. **Night
  is noticeably more reliable.**
- **Your graphics card / settings render the bar's colors slightly
  differently** than the shipped defaults. (Measured: one machine's track is
  `(24,32,32)` where another's is `(34,34,34)`.) If this is you, **Calibrate →
  🎨 Advanced — colors** lets you click the bar and the chest tile on a
  screenshot to pin detection to your own screen's colors.
- **An off color capture** (Advanced — colors). Captures are *additive* now,
  so a bad one can only slow things down — but on an **older build** a bad
  reel-bar-track sample *replaced* the default and blinded the bot to a bar
  plainly on screen (`bar never appeared`). If you see that on an older build,
  **Reset to default** the captured colors, or update.
- **A too-tight zone track box.** If you turned the **zone track** box on and
  drew it so tight it cuts off the thin progress strip beneath the track, the bot
  can't confirm the bar inside it. The latest build falls back to the Reel bar
  band and logs a warning; redraw the box a little taller (include the strip) or
  untick it.
- **Window size.** Roblox does not scale this UI evenly, so the bar can be
  anywhere from ~27% to ~97% of the window wide depending on your resolution.

**Avoid it:** crop the **Reel bar band** snugly around the bar with a small
margin on every side — that removes the health bars and the Mastery strip from
the picture, which is worth more than any color rule. Fish **at night** if
daytime is flaky. **Recalibrate on your own machine** rather than trusting the
shipped boxes.

---

## 3. "No charge — retrying" / slow casts (the charge meter)

The bot confirms a cast landed by watching the **green charge meter** that
fills next to you while you hold. If it can't see it, it retries a few times
and casts anyway, which is slow.

**Causes:**

- **The charge meter is drawn beside your character in the world, not at a
  fixed spot on screen** — so it lands somewhere slightly different on *every
  cast*. A **Cast charge meter** box cropped too tight catches some casts and
  misses others.
- Health / energy bars are green too — a box that drifts onto them misreads.

**Avoid it:** keep the **Cast charge meter** box **big and central**. This is
the opposite of the reel bar band — do **not** crop it tight.

---

## 4. The reeling looks jittery / imprecise

- The bot steers at your **display's refresh rate** (~46–60 Hz). The controller
  is bang-bang (hold or release, nothing in between), so the zone naturally
  **chatters** around the fish. It looks busy even while catching everything —
  check the log: `outside 0%` means the fish never actually left the zone.
- **Fast rods** (e.g. the **Treasure Rod**) accelerate the zone harder. The bot
  learns this live, and on some machines that estimate is noisy and causes
  overshoot.
- **Fast, darting fish** are simply harder to hold centred.

The chatter itself has no calibration — it is the nature of the control loop at
your monitor's refresh rate, and it costs almost no catches; it just looks
restless. But a **second, fixable** cause looks the same from outside and *does*
cost precision, worst on small beginner zones:

- **The track width is mis-measured when the catch starts.** The reel aims at
  every target as a *fraction of the track width*, and the width is read once,
  at the start of each catch. If a tile happened to be covering the edge of the
  progress strip at that instant, the width comes back short and the whole fight
  is scaled wrong. Measured on one 4K recording, re-reading each frame, the width
  swung between **1117 and 1763 px**. This release now reads the width over the
  first few frames and **keeps the widest** (the strip is only ever covered up,
  never drawn wider than it really is), so a single short read no longer skews
  the fight. Turning on the **zone track** box (Calibrate → 🎣 Fishing) removes
  the scenery that causes the short reads in the first place, and is the fix if
  imprecision persists — especially at night, when it also keeps the dock floor
  and water out of shot.

---

## 5. Chests get missed

- **Chest detection was tuned from screenshots, not extensive play**, so it can
  miss a chest or occasionally react to a warm-colored tile. On one tester's
  80-second recording the default detector saw **zero** chests — its tile
  rendered a color the default did not match. **Fix:** in **Calibrate → 🎨
  Advanced — colors**, capture **Treasure chest tile** to pin it to your screen.
- A chest **far across the track** takes time for the zone to reach; a very
  hard fish during that detour can slip.
- **After collecting a chest the zone can slide to one side instead of
  re-centring on the fish** (reported, not yet reproduced here — the clip we had
  detected no chests, see above). If you hit this, first capture the chest tile
  color as above so the chest is actually seen, then send a recording made with
  `python run.py --dev` of a catch that has a chest in it, so it can be fixed
  from what the bot actually sampled.

---

## 6. NPC / buying bait goes wrong

- **Menu buttons or the Craft button calibrated in the wrong place** → the
  clicks land on nothing.
- **Another window overlapping the game** while it is clicking the NPC.
- **The game's own glitch** where your character won't step back to the NPC.
  (The bot works around this by stowing and re-drawing the rod — make sure you
  told it the right rod slot.)
- **Starting with shift lock or auto-run ON.** The bot expects both **off** and
  toggles shift lock itself; leaving them on desyncs it.

---

## 7. Setup & environment (the most common tickets)

- **Roblox not found** — it's minimised, closed, or its window title changed.
  The bot then falls back to the whole screen and *nothing* lines up. Keep
  Roblox open, not minimised; fullscreen or borderless is safest.
- **Another window over the game** — chat, an editor, a browser. Covered UI
  reads as "not there".
- **You moved the mouse** during a run — that rotates the camera and moves
  everything the bot is aiming at. Leave it alone while it runs.
- **Rod not equipped, or the wrong hotbar slot** entered in setup.
- **F2 / F4 do nothing** — global hotkeys need **administrator rights** on
  Windows. Run as administrator, or just use the on-screen buttons.
- **Libraries won't install** — your Python is probably too new for prebuilt
  downloads. **Python 3.12** is the safe choice.
- **`ModuleNotFoundError: numpy`** — you ran `run.py`. Run **`easy_run.py`**,
  which installs everything on first launch.

---

## The short version

For the smoothest run: **plain avatar, no aura fruit, fish at night, one clean
calibration on your own machine, Roblox fullscreen and focused, and don't touch
the mouse.** Most of the issues above simply don't happen under those
conditions.

---

*Sources for the cosmetic/particle notes:*
[Gas](https://blox-fruits.fandom.com/wiki/Gas) ·
[Rumble](https://blox-fruits.fandom.com/wiki/Rumble) ·
[Light](https://blox-fruits.fandom.com/wiki/Light) ·
[Portal](https://blox-fruits.fandom.com/wiki/Portal) —
Blox Fruits Wiki (Fandom).
