# Fishing Macro

An auto-fisher for the Blox Fruits fishing minigame. It reads the screen, plays
the reel minigame, buys bait when it runs low, and sells the catch.

Everything it does is driven by what is on screen — colors and shapes — rather
than by fixed timings, so it adapts to different rods, lighting and times of day.

---

## Quick start (new computer)

**You need:** Windows, [Python](https://www.python.org/downloads/) 3.10 or newer
(tick **"Add Python to PATH"** during install), and Roblox.

> Python **3.12** is the safest choice. Very new versions sometimes don't have
> prebuilt downloads for these libraries yet, which makes the install fail.

1. Download this repository (green **Code** button → **Download ZIP**) and
   unzip it somewhere you can find again.
2. Double-click **`easy_run.py`**.

That's it. The first launch installs what it needs (about a minute) and then
opens a window that walks you through the rest.

If double-clicking opens the file in a text editor instead of running it, open a
terminal in the folder and run:

```bash
python easy_run.py
```

### Prefer a terminal?

```bash
pip install -r requirements.txt
python run.py
```

Same bot, same questions, no window.

---

## Before you press start

The bot clicks at fixed places on screen, so a few things have to be true or the
clicks land on nothing. The app shows this list too.

1. **Run the macro as administrator.** This is the big one. If Roblox is
   running as admin and the macro is not, Windows silently drops every click
   and keypress it sends — the fishing bar drifts to one side and never
   catches, and `F2`/`F4` do nothing. Easiest way: double-click
   **`Run as Admin.bat`** (say Yes to the popup). Or right-click your terminal
   → *Run as administrator*, then `python easy_run.py`.
2. **Stand at the NPC**, right on the edge of interaction range — the
   `Interact` prompt should be showing.
3. **Turn OFF auto-run / running.**
4. **Turn OFF shift lock** — the bot switches it on itself and tracks the state.
5. **Leave Roblox focused** and don't touch the mouse while it runs.
6. **Don't let another window overlap the game.** The bot reads the screen; a
   covered panel reads as "not there".
7. **Equip your fishing rod** and tell the app which hotbar slot it is in. The
   bot stows and re-draws the rod around each bait trip — that is what clears a
   known game bug where the character freezes after a catch.

Two more things that matter, because of how the bite marker is detected:

* **Don't wear red or pink items.** The bite marker is a pink `!`; a red
  cosmetic near your head can look like one.
* A **standard-size character** is assumed. A very different avatar height can
  push the marker outside the area the bot watches — fixable with **Calibrate
  controls** (below).

`F2` starts and stops. `F4` quits. If the hotkeys do not work, the window has
buttons that do the same thing (a global hotkey needs administrator rights on
Windows).

---

## Calibrate controls

Every position the bot uses is stored as a *fraction* of the game window, so it
travels between machines — but the numbers it ships with were measured on one
particular setup. If clicks are missing, calibrate once and you are done.

Open **`easy_run.py` → Calibrate controls**. You get a screenshot of your game
with the overlays grouped and color-coded:

| Group | What it covers |
|---|---|
| 🎣 **Fishing** | reel bar band, zone track (optional), bite marker area, cast charge meter |
| 💬 **Catch popups** | catch card, the `Learn` button on the rare recipe note |
| 🧑 **Talking to the NPC** | the menu button stack, and the buttons the bot clicks |
| 🪙 **Buying bait** | the craft window, `+`, `Craft`, `Close` |

Pick an item on the left. Only that one becomes editable — everything else greys
out so the view stays readable. Each item explains where it belongs and why it
matters.

* **▭ areas** — drag inside to move, drag the white corner to resize.
* **⦿ click points** — drag the dot onto the button.

### The reel bar band is worth your time

Of the fifteen, this is the one that pays. The bot has never confused the reel
bar with something *near* it — it gets confused by things at the far edges of
the screen that happen to look similar: your green health and energy bars on
the left, the two-tone Power/Mastery strip on the right. Cropping the band in
around the bar removes them from the picture entirely, which no amount of
color-matching can do as reliably.

Measured on a recording, pulling the band in from full width to hugging the bar
exactly cost nothing: the bar was still found in **100%** of the frames it was
found in before, reading the same track edges to the pixel.

One caveat, and it is the reason to leave a margin. A band that cuts *into* the
bar does not fail cleanly — it still finds a bar, just a short one, and every
position the reel aims at is then a fraction of the wrong width. So leave a
visible gap either side. The bot warns you in the log if the bar reaches the
edge of your band.

### The zone track box — for stubborn imprecision (optional)

If the bot tracks but never quite settles — worse on small beginner zones — or
if at night it confuses the zone with the dock floor and the fish with the
water, the **zone track** box helps. It is off until you turn it on.

Draw it *tightly* around the inner track — the dark rail the zone slides along,
including the thin progress strip beneath it — with only a hair of margin, then
tick **"Use this box to read the bar"**. When on, the bot finds and reads the bar
from this box instead of the wider reel bar band. That does two things:

* **It pins the track width.** The reel aims at every target as a *fraction of
  the track width*, and the width is measured once at the start of each catch.
  Measured on one 4K recording, re-reading the bar each frame, that measurement
  swung between 1117 and 1763 px depending on whether a tile was covering the
  edge of the progress strip when it was taken — and a catch that starts on a
  short read is scaled wrong for its whole duration. A tight box removes the
  scenery that causes those short reads.
* **It keeps the ground and water out of shot**, which is what throws detection
  off at night.

The reel bar band still does its main job (spotting that a minigame is on, and
finding the fish by its saved picture); the zone track box is a focus assistant
on top of it. Leave the box unticked and nothing changes.

> Even without the box, this release also measures the track width over the
> first few frames of every catch and keeps the widest reading, so a single
> unlucky short read no longer skews the whole fight. The box makes that even
> more reliable by removing the scenery in the first place.

### The cast charge meter is the exact opposite

Do **not** crop this one. The reel bar is drawn at a fixed place on screen; the
charge meter hangs next to your character in the world, so it lands somewhere
different on every cast. Measured across two casts a minute apart on the same
character, it moved 3% of the screen width and **9% of the screen height**
while staying exactly the same size.

A box drawn snugly around the meter therefore catches some casts and misses
others — which shows up as `[cast] no charge — retrying` and looks like a
timing bug rather than a calibration one. Leave it as a wide central band. The
log now prints the peak the meter reached against the threshold it needed, so
you can tell "not in the box" (peak near zero) from "reading short".

### Advanced — colors (optional)

The bot recognises the reel bar, chests and so on by color, and different
graphics cards render those colors a little differently — occasionally enough
to throw detection off. The **🎨 Advanced — colors** section at the bottom of
the list lets you pin the detector to *your* screen's exact colors:

1. With a fishing minigame on screen, pick an item (start with **Treasure chest
   tile** — that's the one that helps most).
2. **Click that element on the screenshot.** It samples the color there.
3. The screenshot tints **magenta** everywhere the bot would now match — check
   it lights up the chest tile and little else. Nudge **Tolerance** if needed.
4. **Save.**

It's entirely optional and off until you capture something — leave it alone and
detection works exactly as before. **Reset to default** undoes a capture.

**Every** capture only ever *adds* your color on top of the built-in
detection — it never replaces it. That is deliberate: a capture that turns out
to be off (you clicked a semi-transparent edge, say) can only widen what the bot
matches, never blank it. So a bad capture can slow things down but can never make
the bot go blind to a bar that is plainly on screen. (The reel bar's own track
color is the important one here: earlier builds let its capture *replace* the
default, and one bad sample there stopped the bot seeing the bar at all —
`bar never appeared` over a visible bar. Captures are additive now.)

The **zone** and **fish** each change color depending on whether the fish is
inside the zone, so each has an **in** and an **out** capture (zone green/grey,
fish tile in/out). If the bot loses the fish when it runs to the edge, capture
**Fish tile (out)** — that's usually the fix.

**Fish image (template)** is a color-independent fallback for machines whose
fish tile is an unusual color: click the centre of the fish, size the box to
frame it, and the bot finds the fish by its *picture* instead of its color. A
green box shows where it matches. Recapture it if you change your window size.
It saves `fish_template.png` next to `config.json`.

(Drop reference pictures into `assets/gui/calib/` — `track.png`, `chest.png`,
`progress.png`, `zone.png`, `zone_out.png`, `fish.png`, `fish_out.png` — to
illustrate each one.)

**Re-shoot** retakes the screenshot (open the relevant dialogue in game first,
so you can line things up against the real UI). **Save** writes to `config.json`.

A good order: open the Fisherman's dialogue → Re-shoot → line up the menu box
and the three menu dots → Save. The craft window needs its own pass, since it
has to be open to see it.

---

## Options

Set in the app, or in `config.json` afterwards.

| Option | What it does |
|---|---|
| **NPC** | Fisherman, or "don't buy" to fish on the bait you have. |
| **Bait per purchase** | How much to buy per trip (multiples of 10). |
| **Rod hotbar slot** | Which key stows/draws your rod. |
| **Sell every N catches** | 0 turns auto-selling off. The NPC never buys favourited fish or your heaviest. |
| **Bait you have now** | Optional. Leave blank and the bot never stops to buy. |
| **Slower fish trick** | Turn on if you end up holding a glitched fish. Slower per catch, but avoids it. |
| **Faster bite reaction** | Reacts in ~7 ms instead of ~80 ms. Uses more CPU, and can occasionally react to a false marker. |

---

## Troubleshooting

| Symptom | Likely cause |
|---|---|
| **Bar tracks the fish then drifts to one side and gives up; F2/F4 dead** | **Not running as administrator.** Roblox is elevated and the macro isn't, so Windows drops its input. Run **`Run as Admin.bat`**. This is the #1 cause of "it gives up on the fish". |
| `NPC dialogue never opened` | Out of interaction range, or the menu box needs calibrating. |
| `CRAFT window never opened` | The craft-button area is mis-calibrated, or another window overlaps it. |
| Bot bites at nothing | The bite area reaches the player list in the top-right — shrink it, or remove red/pink cosmetics. |
| Clicks land in the wrong place | Calibrate. The shipped positions assume a particular window size. |
| Swims past treasure chests / never collects them | The default chest color may not match your screen's. In **Calibrate → 🎨 Advanced — colors**, capture **Treasure chest tile**. (On one tester's 80-second recording the default detector saw zero chests until this was captured.) |
| Tracks but never quite settles, worse on small zones; or night confuses zone/ground | Turn on the **zone track** box (see *Calibrate controls*). |
| `bar never appeared` while a reel bar is plainly on screen | A **color capture is off** (most often the reel-bar track). On the latest build captures can't cause this, but if you're on an older one, **Reset to default** the captured colors. Also check the **zone track** box isn't cutting off the thin progress strip. |
| `[warn] the Zone track box did not contain a full reel bar` | Your zone track box is too tight (cutting the progress strip). Redraw it a little taller to include the strip, or untick it. The bot keeps fishing via the Reel bar band meanwhile. |
| Hotkeys do nothing | Run as administrator (`Run as Admin.bat`), or use the on-screen buttons. |
| Nothing works after editing `config.json` | Delete it — the defaults come back. |
| `ModuleNotFoundError: No module named 'numpy'` | The libraries aren't installed. Both entry points install them automatically now, so make sure you're on the latest version. Otherwise run `pip install -r requirements.txt` yourself. |
| pip installs, but packages are still missing | Your Python is probably too new for prebuilt downloads to exist yet. Python 3.12 is the safest choice. |

Running `python run.py --debug` prints tracking error and progress during each
reel, which is the fastest way to see what the bot thinks is happening.

**See [docs/KNOWN_ISSUES.md](docs/KNOWN_ISSUES.md)** for the full list of what
throws the bot off — red/pink cosmetics and fruit auras near your head, a
mis-cropped detection box, daytime glare, and the rest — with how to avoid each.
The one-line version: plain avatar, no aura fruit, fish at night, calibrate once
on your own machine, Roblox fullscreen and focused, hands off the mouse.

**Reporting a bug?** Run `python run.py --dev`. It turns on the log, the
failure-pixel dumps and the working-strip captures all at once and writes them
into a single `capture_<timestamp>/` folder (with `run.log`) — zip that one
folder and send it. (`--diag` alone still works if you only want the failure
frames in `diag/`.) These say far more than a gameplay video, which is
re-encoded and never shows what the bot actually sampled.

---

## How it works

Short version: the reel zone is a double integrator (you can only accelerate it
left or right, never stop it), which makes optimal control a closed-form
switching curve. The bot measures its own acceleration live, so different rods
self-correct.

The long version — every measured constant, every color, and the reasoning
behind each detector — is in [`docs/MECHANICS.md`](docs/MECHANICS.md). It is
worth reading if you want to change anything.

```
bloxfish/
  capture.py     screen grabs, locating the game window
  vision.py      all the detectors (bar, fish, chest, bite marker, popups)
  controller.py  the reel controller
  engine.py      the state machine: cast -> bite -> reel -> catch
  shop.py        buying bait and selling fish at the NPC
  config.py      every tunable value (only your own settings are saved)
tools/           calibration and offline replay utilities
```

---

## Notes

* Windows only. It uses Windows input APIs directly, because Roblox ignores
  synthetic window messages.
* `config.json` holds **only your own settings** — the calibration boxes and
  the answers to the setup questions. Detector thresholds deliberately stay in
  the code, so an update can improve them. A saved file used to include every
  threshold too, which quietly pinned them forever: one tester kept hitting a
  bug months after the fix shipped, because their config still carried the old
  number. Old files are migrated automatically. Not tracked by git.
* This is a personal project shared as-is. Use it at your own risk; automating
  a game may be against its rules.
