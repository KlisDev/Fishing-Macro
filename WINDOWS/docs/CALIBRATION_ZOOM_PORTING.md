# Calibration image inspector: implementation and porting guide

## Outcome and scope

Version 1.8.1.1.39 adds original-resolution, read-only zoom inspectors for the
captured calibration screenshot and the selected reference example. It also
standardizes the shared GUI's visible "colour/colours" wording to "color/colors".
Windows and Linux use this same GUI implementation; no second Linux copy is needed.

The inspector follows the existing navy panels, cyan accents, rounded buttons,
and compact typography. It offers pointer-centered wheel zoom, drag-to-pan,
Fit, 1:1, +/- controls, zoom percentage, Escape, and an optional outline toggle.
The screenshot outline identifies the currently selected calibration region or
click point. Its label stays below the image instead of obscuring source pixels.

This is an **inspection tool, not a second calibration editor**. To move a region,
resize a box, or sample a color, close the inspector and use the normal workspace.
The screenshot is a snapshot, not a live game feed. Reopen it after changing a tool.

## 1. Audit before implementation

Locate these pieces in the destination project before editing:

- The original screenshot object, before it is resized for the GUI.
- The editable canvas's image scale and coordinate-conversion helpers.
- The source reference-image path resolver, including aliases and platform overrides.
- Screenshot capture, save, reset, drag/resize, color sampling, and review tracking.
- The parent window's close behavior and any global mouse-wheel bindings.
- Compact-layout behavior: keep reference thumbnails reachable by scrolling.

In this project those pieces are in `WINDOWS/easy_run.py`, class `Calibrator`.
`_shot` contains the source screenshot; `scale` belongs to the editable canvas.
`_box_px` and `_dot_px` use the same rounding as the engine. `ASSETS` already
resolves Windows/Linux profile assets. The original reference alias map is
`CALIB_IMAGE_ALIASES`.

Baseline decision: do not add zoom transforms to the editable canvas. That would
require rewriting every hit-test, drag, resize, sampling, and saved-coordinate
conversion. A detached viewer satisfies inspection without that additional risk.
This reduces risk; it is not an absolute guarantee that no UI bug can ever exist.

## 2. Reusable module

Copy `WINDOWS/image_inspector.py` beside the destination GUI module. It requires
only the project's existing Tkinter, CustomTkinter, and Pillow dependencies.

The module contains:

- `ImageViewport`: pure zoom, pan, fit, clamp, and resize geometry.
- `viewport_image`: renders only the visible viewport from the original image.
- `ImageInspector`: the styled viewer window and event/lifecycle handling.

Minimal integration:

```python
from image_inspector import ImageInspector

# source_image is the full-resolution PIL image, not a thumbnail or PhotoImage.
viewer = ImageInspector(calibration_window, source_image, "Screenshot snapshot")
```

The viewer converts the source into its own detached RGB image. It receives no
configuration object, save callback, review callback, or color-sampling function.
Reference images can therefore be opened in a context manager and immediately
closed after construction; the viewer owns its independent pixels.

Optional outline contract:

```python
overlay = ("box", (left, top, right, bottom), "#38bdf8", "Reel-bar search area")
# or: ("dot", (x, y), "#b980ff", "Craft confirmation click point")
viewer = ImageInspector(calibration_window, source_image, "Screenshot snapshot", overlay)
```

These coordinates are **image-local source pixels**, not desktop coordinates,
normalized fractions, or coordinates on the scaled main canvas. In this project,
we reuse `_box_px` / `_dot_px`, then divide their results by the editable canvas
scale. This preserves the existing engine-compatible pixel rounding, including
when the game window starts on a negative-coordinate monitor.

## 3. Coordinate safety rules

Maintain three separate spaces:

1. Saved calibration: fractions relative to the game rectangle.
2. Original screenshot: source pixels.
3. Inspector canvas: display pixels after zoom and pan.

Only spaces 2 and 3 participate in inspection:

```text
canvas_x = source_x * viewer_scale + pan_x
canvas_y = source_y * viewer_scale + pan_y
source_x = (canvas_x - pan_x) / viewer_scale
source_y = (canvas_y - pan_y) / viewer_scale
```

Before changing zoom, calculate the source pixel under the pointer. Adjust the
new pan offsets so that pixel stays under the pointer, unless the image boundary
requires clamping. Keep smaller images centered and prevent panning the image
entirely off-screen. Clamp zoom between Fit and 800%; Fit never enlarges a small
source beyond native size. 1:1 means one source pixel per canvas pixel.

Do not assign the inspector scale to `Calibrator.scale`. Do not call `_commit`,
`_sample_color`, `_save_template`, or review tracking from inspector events.
Panning uses separate state and changes only the viewer's offsets.

## 4. Hook into the calibration UI

The implementation changes in `easy_run.py` are deliberately localized:

1. Import `ImageInspector`; initialize `_inspector` and `_reference_key`.
2. Extract `_calib_image_path(key)` from existing thumbnail resolution. Both the
   thumbnail and zoom view must use exactly the same aliases and asset root.
3. Add `Zoom screenshot` and `Zoom reference` above the editable canvas. Give the
   canvas the expanding grid row below the toolbar. Disable screenshot zoom until
   capture succeeds and reference zoom when no reference is available.
4. Bind the reference thumbnail and its zoom button to the same reference opener.
5. In `_guide_for`, remember the current reference key and update button states.
6. `_inspect_screenshot` passes the original `_shot` and an optional value-only
   selected outline to the viewer.
7. `_inspect_reference` opens the original PNG, never the downscaled cached
   thumbnail. Catch missing/unreadable files and display a nonfatal status message.
8. Keep only one viewer open: `_close_inspector()` precedes each replacement.
9. Close the viewer at the start of `shoot()`, before hiding windows/capturing.
10. Close it before destroying the parent calibration window.

As of version 1.8.1.1.40, the right workspace is a `CTkScrollableFrame` using
`speed_scroll`. The screenshot shell disables geometry propagation and keeps
a logical height of `max(380, min(560, window_height - 410))`. Update this height
before the compact-mode early return on every window resize. The screenshot's
own canvas remains an ordinary editing canvas with its original event bindings.

Compact mode moves the reference to row 10 below the instructions; spacious mode
restores row 0, column 1, spanning eight rows. Keep descriptions, warnings, and
checks visible in both modes. Wrap guidance to the available logical width.
Thumbnail bounds are 420×220 in compact mode and 230×180 otherwise, using Pillow
`thumbnail` to limit both dimensions. Include both dimensions in the cache key.
Refresh only the reference when mode changes; do not reselect the tool or reset
an expanded guide. The initial window uses display-aware sizing with a 900×520
minimum, accounting for CustomTkinter scaling when measuring display dimensions.

The zoom toolbar and reference button remain available through workspace scrolling.
Visual testing also found that the existing instruction line could consume the
space needed by Save, Re-shoot, and Reset. Those actions now occupy their own row,
and the instruction wraps to the available width. Preserve this separation when
porting to avoid losing essential controls at small window sizes.

## 5. Events, performance, and lifetime

- Windows wheel uses `<MouseWheel>`; Linux/X11 uses `<Button-4>` / `<Button-5>`.
- Bind these to the inspector canvas, not globally. Return `"break"` so zoom does
  not also scroll a parent calibration panel.
- Bind left press/drag/release only to viewer pan state.
- Render with a single coalesced `after(16, ...)` callback while events arrive.
  There is no recurring animation/redraw loop while idle.
- Render a viewport-sized PIL image using `Image.Transform.EXTENT`. Do not resize
  the whole source to 800%, which would create unnecessary huge allocations.
- Keep a reference to the current `ImageTk.PhotoImage`; replace it on redraw.
- On close: guard against repeat destruction, cancel pending render, detach the
  native transient owner, destroy widgets, release PhotoImage and source pixels.
- Finish native window mapping with `update_idletasks()` after constructing the
  inspector. The immediate-parent-close stress test reproduced a native Windows
  Tk access violation without this initialization barrier; it passes with it.
- Name drawing methods `_render_view`, not `_draw`: CustomTkinter reserves
  internal drawing methods and may call them with framework-specific arguments.

Memory still includes one original-resolution RGB copy plus the viewport and Tk
display image. Rendering costs grow with window size, not the selected zoom level.
No new live screen capture, network request, disk write, or input injection occurs
when opening the viewer. Large sources can still cost memory, and zoom cannot
recover detail that does not exist in a small reference PNG.

## 6. Wording and compatibility

Update visible headings, descriptions, review counters, status messages, and
preparation copy to American "color/colors". Do not rename serialized fields,
asset filenames, or APIs purely for spelling. This patch makes no config migration
and does not change detector, movement, Shift Lock, buying/selling, or input logic.

Keep platform asset resolution intact. Linux imports the shared GUI from
`WINDOWS/` after setting its own runtime context; adding a second inspector
implementation under `LINUX/` would create an unnecessary maintenance fork.

## 7. Validation and how to repeat it

Copy/adapt `WINDOWS/tests/test_image_inspector.py` into the destination tests.
Use synthetic screenshot pixels rather than a running game for deterministic QA.
The tests exercise:

- Pointer-centered zoom, Fit/800% limits, pan bounds, and resize behavior.
- 4K input rendered into a bounded viewport without modifying source bytes.
- Original reference resolution and separate craft reference slots.
- Actual Tk wheel/pan/resize events for regions, points, color and template modes.
- Exact config/review/source-image invariance before versus after inspection.
- Config save/reload retaining calibrated values.
- Windows and X11 wheel event bindings, repeated viewer replacement/close.
- Missing reference handling and compact-window access to zoom/save/reset/re-shoot.
- Pending redraw cancellation and immediate parent shutdown.
- Re-shoot closing the viewer before mocked capture and retaining review progress.

From the repository root, with dependencies installed, PowerShell:

```powershell
$env:BLOXFISH_NO_BOOT = '1'
$env:PYTHONPATH = Join-Path (Get-Location) 'WINDOWS'
python -B -m unittest discover -s WINDOWS/tests

# Requires a real graphical desktop; does not start the engine or send game input.
$env:BLOXFISH_TEST_GUI = '1'
python -B -m unittest discover -s WINDOWS/tests
```

For optional QA screenshots, create a temporary directory and set
`BLOXFISH_TEST_SCREENSHOTS` to it. The test will briefly put synthetic test windows
on top and capture their rectangles. Screenshots are local, not uploaded or saved
in this repository. Inspect them before sharing in case the desktop obscured a
test window. A normal Python installation should locate Tcl/Tk automatically;
bundled/custom runtimes may require their own `TCL_LIBRARY` and `TK_LIBRARY` paths.

### Verification performed for this patch

All **102 tests passed** on Windows after the 1.8.1.1.40 scaling update,
including the opt-in real-Tk tests, with
synthetic images and no game input. `git diff --check` also passed.
The full suite includes the existing Windows and mocked Linux platform regressions.
Actual screenshots of the screenshot inspector, reference inspector, and compact
calibrator were reviewed for spacing, controls, colors, and clipped content.
The compact header and initial fit were refined after those checks.
The scrolling layout was also exercised and visually inspected at 900×520,
1000×680, and 1440×900. Tests additionally resize within spacious mode, verify
portrait thumbnail limits, exercise both zoom buttons and workspace scrolling,
and confirm that calibration, selection, expanded guidance, and reviews persist.

Live Roblox calibration and a live Linux/Sober desktop were not exercised.
Linux wheel events were generated in the Windows Tk tests, which verifies event
handling but not every Linux window manager or distribution. Before release on a
different project, repeat GUI QA on its supported platforms and display scaling.

## 8. Porting checklist for another project

- Copy the standalone viewer module and adapt only theme constants if needed.
- Resolve original screenshot/reference pixels and preserve image slot aliases.
- Add labeled zoom buttons, thumbnail activation, and disabled/missing-file states.
- Pass no configuration or editing callbacks to the inspector.
- Convert selected overlays to source pixels using existing rounding helpers.
- Keep the editable canvas and every save/sample operation unchanged.
- Close the viewer before new capture, replacement, and parent shutdown.
- Preserve compact access and keep essential actions out of the hint's width budget.
- Run geometry, invariance, lifecycle, and existing regression tests.
- Inspect normal/compact/high-DPI layouts and repeat live-platform acceptance.

Suggested handoff instruction: "Port the read-only ImageInspector and its tests;
do not add zoom transforms to the calibration editor. Keep reference source paths,
coordinate rounding, config schema, and capture lifecycle compatible. Demonstrate
unchanged calibration state after zoom/pan and safe immediate shutdown."
