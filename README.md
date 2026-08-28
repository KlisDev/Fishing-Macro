# Fishing Macro

An auto-fisher for the Blox Fruits fishing minigame. It reads the screen, plays
the reel minigame, buys bait when it runs low, and sells the catch — driven by
what's on screen (colours and shapes), not fixed timings, so it adapts to
different rods, lighting and times of day.

The project is split by platform:

## 🪟 [`WINDOWS/`](WINDOWS/README.md) — the main version

Everything you need on Windows. Download this repository (green **Code** button →
**Download ZIP**), unzip it, then open the **`Windows`** folder and:

- double-click **`easy_run.py`**, or
- for the reliable path, run it **as administrator** — right-click
  **`easy_run.py` → *Run as administrator*** (or open an admin terminal and run
  `python easy_run.py`). Why: if Roblox is elevated and the macro isn't, Windows
  drops its input and the bar drifts off and gives up.

Full setup, calibration, options, and troubleshooting are in
**[`WINDOWS/README.md`](WINDOWS/README.md)**.

> **If your browser or antivirus warns about the download — it's a false
> positive.** To play the minigame the macro sends mouse/keyboard input and reads
> the screen, the same behaviour a keylogger has, so heuristic scanners sometimes
> flag it. There are **no binaries and no scripts here — only readable Python**
> you can inspect line by line. See `WINDOWS/README.md` for details.

## 🐧 [`LINUX/`](LINUX/README_LINUX.md) — experimental (Sober)

Runs the **same** macro on Linux against Roblox via
[Sober](https://sober.vinegarhq.org/) (X11 sessions), swapping only the
input/window layer for a `uinput` backend. It reuses the shared core in
`WINDOWS/` — the detection/control code is cross-platform; only `inputs.py` has a
Windows branch. Setup and the current limitations are in
**[`LINUX/README_LINUX.md`](LINUX/README_LINUX.md)**.

---

*Personal project shared as-is. Automating a game may be against its rules; use
at your own risk.*
