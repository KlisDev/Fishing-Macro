# Issue #53: a partial progress strip became the locked reel width

Issue: https://github.com/KlisDev/Fishing-Macro/issues/53

Release: 1.8.2.1, alongside issue #55.

## Reproduction

The maintainer supplied an 18.1-second, 3840x2160, 60-fps recording. The console
shows a reel width locked at 557 pixels, while the visible cyan box covers only
part of the real track. A later fight repeats the same partial measurement.

The actual track is about 1764 recording pixels wide, or 882 pixels in a
1920x1080 replay. During its opening fade, the empty progress trough renders
near BGR `(12,18,13)`. The old mask expects the settled `(19,61,25)` appearance,
so it accepts the bright filled section and misses the dark remainder.

Taking the widest of five early frames does not solve this when every early
frame contains the same fade. The resulting short width stays locked for the
entire fight, even after the complete track becomes easy to detect. It also
mis-scales progress and the controller's positions.

A second, separately reproduced contributor is F8 feedback: a smaller box's
cyan border can fall inside the larger search rectangle and split its progress
pixels. Drawing each frame outside its own box does not protect nested crops.

## Correction

- Preserve the strong progress-strip seed and the acquisition minimum width.
  Weak dim green alone cannot identify a minigame.
- For geometry, allow that seed to extend through the adjacent dim unfilled
  trough. Keep the known left edge of the filled strip fixed.
- Allow only a narrow cyan interruption between supported trough pixels.
  A cyan object alone cannot extend the right end or bridge a wide gap.
- Keep the dark outer border separating the rail from scenery. Validate the
  deep playfield above the progress strip before returning geometry.
- Scale the progress-row scan with the detected band height instead of cutting
  it off at a fixed 40 pixels on large interfaces.
- Within an already identified progress crop, keep a low-progress live reel
  present when its unfilled trough is still dim. Require a bright seed covering
  at least 15% of the known rail; weak green or a small speck cannot keep a
  vanished bar alive. This prevents an early rod flick after correcting width.
- Clear all active detector interiors from the Windows overlay after drawing.
  This protects a larger capture from nested frames, labels, and click markers.
  The transparent color key reveals the original game pixels.

The shared detector correction applies to Windows and Linux/Sober. Linux's
existing X11 overlay already omits primitives inside protected regions.
No controller physics, mouse input timing, saved settings, or calibration
coordinates are changed. The ordinary color samples remain optional.

## Recorded replay

Sampled the video every six frames (10 samples per second) using the saved
Reel-bar search area. Live intervals are 0.0-4.6 and 11.4-17.1 seconds; the
other sampled frames contain no complete live reel. Both native recording
resolution and a 1920x1080 replay were checked.

| Resolution | Before: full-width reads | After: full-width reads | After width range |
| --- | ---: | ---: | ---: |
| 3840x2160 | 80/105 | 105/105 | 1762-1766 px |
| 1920x1080 | 103/105 | 105/105 | 881-883 px |

Before the fix, widths reached as low as 1118 and 559 pixels respectively,
and one live scaled frame was not acquired. All 76 sampled idle/fade frames
were rejected at each resolution, both before and after the fix.

These are 362 evaluations of 181 sampled recorded frames, not 362 independent
game sessions. Video compression and scaling limit exact pixel comparisons.

## Automated checks

Synthetic regressions reproduce the dark empty trough independently of the
video, including green and alarm-gray zones, several scales and filled
fractions, the progress-first finder, translated/cropped search areas, fixed
early-frame width locking, nearby scenery, and bounded debug interruptions.
They also verify that reported progress remains a fraction of the full rail
and that weak green without a playfield does not create a minigame.

The Windows overlay regression uses an owned test window and reads physical
pixels where a nested frame and marker cross its larger protected capture.
It fails against the committed renderer and passes against the patch, including
the current desktop's scaling. Tests do not send input to Roblox. Reporter
media and local replay helpers remain in ignored diagnostic storage.

Final release suite: **211 tests run, 211 passed, zero failures, zero errors,
zero skips**, with native Windows/Tk checks enabled on Python 3.14.4 and
OpenCV 4.13.0. All 13 targeted reel regressions and the new native overlay
regression pass. Linux profile/backend tests also pass. `git diff --check`
passes.

Two checks initially failed against the committed baseline: the guide's
version label was stale, and clipped mastery-HUD labels could count as two
NPC rows. The release updates the guide and locally suppresses the narrow
HUD backing without hiding a complete menu beside it. The old #38 recordings
still pass all 530 sampled open/closed cases; the anchor replay succeeds with
one click and no movement taps. These replays use simulated input only.

## Limits

The recorded width error is reproduced and corrected. Live Roblox sessions
and live Sober/X11 have not been exercised by the automated replay. A search
box that physically excludes an end of the bar still needs calibration: this
patch does not infer pixels outside the selected area. Keep the complete rail
and its progress strip inside that area, with a small margin on each side.
