# Fishing Macro

An auto-fisher for the Blox Fruits fishing minigame. It reads the screen, plays
the reel minigame, buys bait when it runs low, and sells the catch.

Everything it does is driven by what is on screen — colours and shapes — rather
than by fixed timings, so it adapts to different rods, lighting and times of day.

---

## Quick start (new computer)

**You need:** Windows, [Python 3.10 or newer](https://www.python.org/downloads/)
(tick **"Add Python to PATH"** during install), and Roblox.

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

1. **Stand at the NPC**, right on the edge of interaction range — the
   `Interact` prompt should be showing.
2. **Turn OFF auto-run / running.**
3. **Turn OFF shift lock** — the bot switches it on itself and tracks the state.
4. **Leave Roblox focused** and don't touch the mouse while it runs.
5. **Don't let another window overlap the game.** The bot reads the screen; a
   covered panel reads as "not there".
6. **Equip your fishing rod** and tell the app which hotbar slot it is in. The
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
with 15 overlays, grouped and colour-coded:

| Group | What it covers |
|---|---|
| 🎣 **Fishing** | reel bar band, bite marker area, cast charge meter |
| 💬 **Catch popups** | catch card, the `Learn` button on the rare recipe note |
| 🧑 **Talking to the NPC** | the menu button stack, and the buttons the bot clicks |
| 🪙 **Buying bait** | the craft window, `+`, `Craft`, `Close` |

Pick an item on the left. Only that one becomes editable — everything else greys
out so the view stays readable. Each item explains where it belongs and why it
matters.

* **▭ areas** — drag inside to move, drag the white corner to resize.
* **⦿ click points** — drag the dot onto the button.

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
| `NPC dialogue never opened` | Out of interaction range, or the menu box needs calibrating. |
| `CRAFT window never opened` | The craft-button area is mis-calibrated, or another window overlaps it. |
| Bot bites at nothing | The bite area reaches the player list in the top-right — shrink it, or remove red/pink cosmetics. |
| Clicks land in the wrong place | Calibrate. The shipped positions assume a particular window size. |
| Hotkeys do nothing | Run as administrator, or use the buttons. |
| Nothing works after editing `config.json` | Delete it — the defaults come back. |

Running `python run.py --debug` prints tracking error and progress during each
reel, which is the fastest way to see what the bot thinks is happening.

---

## How it works

Short version: the reel zone is a double integrator (you can only accelerate it
left or right, never stop it), which makes optimal control a closed-form
switching curve. The bot measures its own acceleration live, so different rods
self-correct.

The long version — every measured constant, every colour, and the reasoning
behind each detector — is in [`docs/MECHANICS.md`](docs/MECHANICS.md). It is
worth reading if you want to change anything.

```
bloxfish/
  capture.py     screen grabs, locating the game window
  vision.py      all the detectors (bar, fish, chest, bite marker, popups)
  controller.py  the reel controller
  engine.py      the state machine: cast -> bite -> reel -> catch
  shop.py        buying bait and selling fish at the NPC
  config.py      every tunable value, saved to config.json
tools/           calibration and offline replay utilities
```

---

## Notes

* Windows only. It uses Windows input APIs directly, because Roblox ignores
  synthetic window messages.
* `config.json` is created on first run and is yours — it is not tracked by git.
* This is a personal project shared as-is. Use it at your own risk; automating
  a game may be against its rules.
