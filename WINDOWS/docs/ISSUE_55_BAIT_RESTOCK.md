# Issue #55: Craft confirmation and unstable NPC rows

Issue: https://github.com/KlisDev/Fishing-Macro/issues/55

Release: 1.8.2.1, alongside issue #53.

## What the report establishes

The reported .44 run reaches a restock attempt after successful fishing. Its
final error is `CRAFT window did not close - purchase unconfirmed`. The
`bait page not confirmed` line can be emitted by recovery after that failure,
so it does not establish that Back was the original failing action.

The issue currently contains console text and no config, screenshots, or raw
captures. Its intermittent root detection and reported turning cannot be
assigned a confirmed cause from those logs alone.

## Reproduced code defects

### The NPC name banner counted as an open CRAFT window

The old detector accepted any yellow component wider than a fraction of the
Craft search area. A search area that includes the Update 30 Fisherman name
banner therefore continues to report CRAFT after the real window closes.
This prevents purchase confirmation and can send recovery into the wrong
stage, matching the reported error and Basic Bait / Back symptom.

Previously supplied gameplay footage independently reproduces the error.
In a 384x151 search crop, the Fisherman banner is roughly 211x21 pixels,
while the actual Craft action is about 150x42 pixels. The old width-only check
accepts both. The patch requires a compact button shape, sufficient height,
and colored area, so the long thin name banner no longer qualifies. Optional
color samples still supplement color matching; they cannot bypass shape.
Both horizontal ends must be visible: clipping the banner could otherwise
make its apparent aspect ratio look compact. Top-cropped Craft buttons remain
supported. Keep a small horizontal margin around the complete button.

### Hovering removed a real action row

A hovered NPC action uses a lighter gray background. Requiring dark backing
around every label discarded that row. In a recorded Shop frame, all three
actions are plainly visible but the old detector returns only Sell Fish and
Nevermind. Synthetic two-row bait pages can drop to zero when one row is
hovered, since a single remaining label cannot form a page.

The patch accepts a bounded neutral-gray highlight only when it has dark top
and bottom borders. It still requires the white label/icon pixels and the
regular action stack. Flat gray scenery, empty gray bands, and unbordered
text do not become menu evidence. Required page counts remain 4/3/2, with the
existing position tolerances and settle duration.

### Rendering consumed the settling budget

Some transitions allowed 1.2 seconds total while also requiring 0.65 seconds
of stable rows. A valid page arriving at 1.0 seconds had no time to settle.
The transition now gets its configured render timeout plus one bounded settle
window. The deadline does not extend indefinitely when rows keep changing.
Timeout logs report both observed counts and the longest stable interval.

## Purchase and cancellation safeguards

- A real Craft button must be observed immediately before each confirmation
  click. A missing frame before any click cannot credit bait.
- After clicking, the button must remain absent for at least 0.24 seconds.
  One missing frame or a capture error cannot prove purchase completion.
- Retry only while the actual button is still present. Do not repeat Craft
  blindly over the returned NPC page.
- Stop sending quantity `+` clicks as soon as the run is cancelled.
- Preserve the existing accounting rule: once the purchase is confirmed,
  credit it exactly once even if the subsequent exit cannot be verified.
  An uncertain exit still stops rather than moving through the menu.
- Describe failed confirmation as unconfirmed, rather than asserting that
  the game definitely bought no bait.

These shared changes apply to Windows and Linux/Sober. No saved settings or
calibration points are moved, and no config reset is required. No movement,
extra Shift Lock toggles, or relaxed page counts are added.

## Validation

The replay uses older supplied footage, not a new capture from the #55
reporter. The Craft search crop deliberately includes the name banner to
reproduce the ambiguity; it is not claimed to match their missing config.

| Recorded check | Committed detector | Patched detector |
| --- | --- | --- |
| Craft open/closed states, ten selected frames | 4/10 correct | 10/10 correct |
| Shop with Buy Bait hovered | 2 rows | 3 rows |

The real buy route, driven by those recorded images and fake input, completes
Shop -> Buy Bait -> Basic Bait -> four quantity clicks -> Craft -> Back ->
Nevermind. It sends one Craft click and zero keyboard taps. Shift Lock is
mocked in this replay; it is not a live input-acceptance test.

Twenty targeted tests cover the banner, normal and top-cropped Craft buttons,
horizontal clipping rejection and a tight complete-button crop,
UI scales, optional colors, all hovered rows on 2/3/4-row pages, negative
scenery cases, late page arrival, moving/wrong pages, capture failures,
missing frames, cancellation, and full restock accounting. The full synthetic
restock uses the real Craft detector: eight quantity clicks buy 90, taking
software stock from 1 to 91 with exactly one credited purchase. Cancelling
after the first quantity click sends no remaining quantity or Craft clicks.

The final release suite ran **211 tests with native Windows/Tk checks enabled:
211 passed, zero failures, zero errors and zero skips**. This includes all
20 targeted restock tests and 27 Linux platform tests. `git diff --check`
passes. The guide's stale version and a preexisting clipped mastery-HUD false
positive were also corrected during release review. Fourteen NPC menu tests
and all 530 sampled open/closed cases from the older #38 recordings pass;
the anchor replay succeeds with one click and zero movement taps.

The guide now describes 1.8.2.1 and includes searchable answers for both
issues. Browser search, answer links, category filters, collapsing, and layout
checks pass at desktop, small-window, and phone widths without horizontal
overflow or remote resource requests. Windows and Linux use the same version
constant. Existing config keys, defaults, and saved coordinates are preserved.

## Remaining verification

The recorded and synthetic cases establish the defects and corrected behavior.
They do not establish that every intermittent failure described in #55 has
these causes. The reporter's exact profile, live purchase acknowledgment,
long-running camera behavior, and live Sober remain unverified. Persistent
0/2/3 or 3/3-but-unsettled failures should be assessed using the new stable-time
diagnostic and the offered config/screenshot or development captures.

Personal footage, extracted frames, and replay helpers remain ignored and
are not part of the source changes.
