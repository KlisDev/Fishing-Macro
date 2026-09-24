# Linux / Sober support — X11 only

This is the Linux launcher for the same Blox Fruits fishing macro and premium
GUI used on Windows. It targets Roblox through **Sober** on an **X11** desktop.
The detector, Update 30 NPC logic, safety watchdog, calibration workflow, and
all four GUI pages are shared with the Windows build; Linux replaces only the
window, focus, input, runtime-profile, and F8-overlay layers.

> Wayland is intentionally unsupported in this release. Capture, global
> hotkeys, input injection, and the click-through hitbox overlay rely on X11.

## Before launching

1. Confirm the desktop session is X11:

   ```bash
   echo $XDG_SESSION_TYPE   # must print x11
   ```

2. Install Python and GUI dependencies. Tk comes from your system package
   manager, not pip:

   ```bash
   sudo apt install python3-tk
   pip install -r LINUX/requirements-linux.txt
   ```

3. Install the one-time uinput rule, then log out and back in. Do **not** run
   the macro itself as root.

   ```bash
   sudo bash LINUX/install-udev.sh
   ```

4. Open Sober with Roblox visible. The launcher checks that Sober is found,
   `/dev/uinput` is writable, and capture can inspect the target window before
   it creates the GUI or sends any game input.

## GUI run

```bash
python LINUX/easy_run_linux.py
```

The GUI includes the polished Setup workspace, complete Calibrate page,
expandable Preparation Guide with the NPC-position simulation, and lightweight
Run Console. Linux-specific preflight guidance appears where input permission
matters.

Controls in the **GUI Run Console**:

- `F2` — Start / Pause
- `F4` — Stop the current run without closing the application
- `F8` — Show / hide hitboxes and write a diagnostic log

F8 uses an X11 Shape overlay with an empty input region. It is click-through
and clips its drawing outside protected detector regions so it cannot steal
Sober input or color the pixels the macro reads. If the X server lacks the
Shape extension, F8 keeps the diagnostic log and reports that visible hitboxes
are unavailable instead of opening an unsafe opaque overlay.

## Separate Sober profile and assets

Linux never reuses Windows calibration or capture output:

- `LINUX/config.json` — Sober settings and calibration
- `LINUX/fish_template.png` — Sober fish-template fallback
- `LINUX/diag_session_*.log`, `LINUX/diag/`, `LINUX/record/` — diagnostics
- `LINUX/assets/gui/` — copied GUI examples and calibration references

The copied pictures are only examples. Replace them with Sober-specific
screenshots whenever the Android layout differs; doing so does not affect
Windows. Calibration positions, detector regions, color samples, templates,
and saved setup values are isolated in the Linux profile for the same reason.

Calibration includes **Zoom screenshot** and **Zoom reference** inspectors.
Scroll over the image to zoom; drag to pan; use Fit, 1:1, or Escape.
These are read-only views of the original images. Move calibration controls
and sample colors only in the main calibration workspace. Re-shoot closes
the inspector before capture. No extra Linux dependency is required.

## Terminal run

```bash
python LINUX/run_linux.py [--now] [--debug] [--diag] [--record] [--dev] [--config PATH]
```

Terminal controls remain intentionally conventional:

- `F2` — start / stop
- `F4` — quit
- `F8` — diagnostic log only

`--dev` enables debug, failure captures, and reel-strip recording together in a
timestamped `capture_*` directory next to the active config. `--config PATH`
uses that config and keeps its template, logs, diagnostics, recordings, and
development captures in the same parent directory.

## Sober checks and troubleshooting

The launchers reject these unsafe states before a run starts:

- Wayland or no `DISPLAY`: log into an X11/Xorg session.
- `/dev/uinput` unavailable: rerun `install-udev.sh`, then log out and in.
- No Sober window: open Roblox through Sober and leave it visible.
- Missing Python dependency: install `requirements-linux.txt` again.

A black initial capture is a warning rather than an automatic block: a dark
scene can be legitimate. Open **Calibrate** and confirm the live workspace
shows the actual game before pressing F2. Recalibrate after any Sober UI,
resolution, display-scaling, or window-mode change.

When F2 starts, the X11 backend raises and focuses the detected Sober window and
confirms focus before sending input. If focus cannot be confirmed, the macro
warns rather than assuming injected input reached Roblox.

Sober uses the same interaction safeguards as Windows. Enable Roblox's **Shift
Lock Switch**, leave the current lock off before F2, and let the macro change
it for dialogue/fishing phases. Before a cast it requires the X11 cursor to
have actually snapped to the game window's centre; otherwise it stops instead of
fishing with a free cursor. A dialogue is accepted only when its visible action
rows form a properly spaced vertical stack, so unrelated bright pixels cannot
block a necessary NPC-range probe.

Shift Lock verification is independent of the calibrated **Interact click point**
(`shop.center`). Keep that point on the actual Interact prompt; do not change it
to `0.5107` to satisfy Shift Lock. Update older builds that coupled these two
positions. If you used that workaround, recalibrate Interact to the real prompt.
Failed starts now return to idle, allowing another F2 attempt. Leave the current
lock off before retrying. Cursor-query failures stop verification; the diagnostic
message includes the before/after positions, expected centre, and tolerance.

## Privacy and bug reports

`config.json`, fish templates, diagnostic images, recordings, development
captures, and F8 logs are ignored by Git. They can reveal UI, game state, and
account information. Keep them out of archives or bug reports unless you have
sanitized them and deliberately chosen to share them.

Automation may violate Roblox rules or be detected by anti-cheat. Use it at
your own risk.
