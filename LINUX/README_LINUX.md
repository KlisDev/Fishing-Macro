# Linux support (Sober) — experimental

This runs the **same** fishing macro on Linux, against Roblox played through
**[Sober](https://sober.vinegarhq.org/)** (the Flatpak Android build — the only
practical way to run Roblox on Linux). Only the input and window-finding layer is
Linux-specific; all the detection and control logic is the shared code in
`bloxfish/`.

> **Status: experimental.** It has been built against the Windows client's
> behaviour and needs testing on a real Sober box. Read *Known limitations*
> below before expecting it to just work.

---

## Requirements

- An **X11 session** (log out and pick "… on Xorg" at the login screen).
  **Wayland is not supported yet** — the screen grabber (`mss`) can't capture a
  Wayland desktop. Check with: `echo $XDG_SESSION_TYPE` → it must say `x11`.
- **Sober** installed and Roblox running, standing at the Fisherman with the
  fishing spot ready (same pre-flight as Windows).
- Python 3.10+.

## Setup

```bash
# 1. Dependencies  (Tk comes from the system, not pip)
sudo apt install python3-tk          # or your distro's equivalent
pip install -r LINUX/requirements-linux.txt

# 2. Allow input injection (the Linux "run as administrator")
sudo bash LINUX/install-udev.sh
#    then log out and back in so the 'input' group applies

# 3. Run — the full GUI (same as Windows)
python LINUX/easy_run_linux.py       # setup wizard, Calibrate, cooldowns, F2/F4

#    …or a terminal-only run, no window:
python LINUX/run_linux.py            # F2 start/stop, F4 quit (or Ctrl+C)
```

`easy_run_linux.py` is the **same GUI as Windows** — the setup wizard, **Calibrate
controls** (with the colour eyedropper, the **Zone track** box, reference
images), the **Advanced cooldowns** editor, and F2/F4 hotkeys — just driving the
uinput/X11 backend.

The first thing to do is **calibrate**: the Sober (Android) UI can differ from
the Windows client, so the shipped boxes/colours may not line up. Open
**Calibrate controls** and use the colour eyedropper + **Zone track** box (see
the main README) — they exist precisely to pin detection to a different-looking
client.

## How it fits together

- `inputs_linux.py` — a **uinput** virtual mouse + keyboard (kernel-level, like
  Windows `SendInput`), so Sober forwards it to Android Roblox as real hardware.
- `find_window_linux.py` — finds the **Sober** window on X11 (by `WM_CLASS`
  `org.vinegarhq.Sober` / title "Sober").
- `easy_run_linux.py` / `run_linux.py` — plug those backends into the shared
  engine (GUI / terminal). The screen grabber (`mss`), the whole GUI, and the
  detection/control core are reused unchanged.

## The "run as administrator" trap, Linux edition

If `/dev/uinput` isn't writable by you, injected clicks are **silently dropped**
— the bar drifts to one side and the bot looks like it "gives up on the fish",
exactly the Windows UIPI symptom. That's what `install-udev.sh` fixes. If you see
that behaviour, confirm you're in the `input` group (`groups | grep input`) and
that you logged out and back in.

## Known limitations / things to test first

Run these three checks **in order** — if the first fails, the rest is moot:

1. **Does input reach Sober at all?** With Sober focused, does the macro's cursor
   move and a menu click register in-game? (uinput → Sober → Android is the
   unproven link.)
2. **Does capture return the game, not black?** Some GPU/compositor combos hand
   back black frames for a specific window.
3. **Does the fishing UI match the detectors?** The Android client may lay the
   reel bar out differently — re-tune via colour capture + the Zone track box
   rather than changing code.

Also note:
- **Wayland**: not supported (capture). A PipeWire/portal path could be added
  later. Check with `echo $XDG_SESSION_TYPE` — it must be `x11`.
- **Checklist wording**: the pre-flight checklist still says "run as
  administrator" (Windows wording); on Linux the equivalent is the udev step
  above. The steps otherwise apply.
- **Anti-cheat / ToS**: same caveat as every platform — automating Roblox may
  violate its rules, and Hyperion could flag synthetic input. Use at your own
  risk.
