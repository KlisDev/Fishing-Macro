# Fishing guide research and maintenance

The root `GUIDE.html` adapts the layout of the creator's supplied
`SharkmanV3-main/GUIDE.html`. Its fishing instructions were checked against
the local shared core, initially version **1.8.1.1.44** on **10 October 2026**.
Release guidance was updated for **1.8.2.1** on **17 October 2026** after
reviewing issues #53 and #55, recorded-image replays, and regression tests.

The guide is a local HTML document, with inline CSS/JavaScript and no remote
display assets. Search, category filters, answer links, command copying, native
disclosure controls, reduced motion, and print expansion support quick lookup.
Installation and answers remain readable without JavaScript.

## Comment coverage

vidIQ supplied comment threads and the replies actually returned for these
three videos. Metadata verified their common owner; owner comments/replies
were excluded using comparable channel IDs or the verified handle. Viewer
identities and raw comments are not included in this document or the guide.
Question headings are original paraphrases; answers are newly written from
current code/documentation, not adopted from unverified viewer advice.

| Video | Pages inspected | Distinct threads | Viewer top comments | Viewer replies | Owner messages excluded | Viewer date coverage (UTC) |
|---|---|---:|---:|---:|---:|---|
| [I FINALLY Fixed the Best Blox Fruits Fishing Macro!](https://youtu.be/39x3cgEXBiE) | Relevance pages 1 and 2 (100 each), latest page (100) | 208 | 207 | 56 | 64 | 17 September to 10 October 2026 |
| [NOOB to MAX Fisherman With a MACRO Only](https://youtu.be/U5MLhsHdVgw) | Relevance page (79), latest page (81) | 81 | 80 | 50 | 48 | 5 to 28 September 2026 |
| [I Tried The Best Blox Fruits Fishing Macro For 24 Hours!](https://youtu.be/tI4rrSr6sTo) | Relevance pages 1 and 2 (100 each), latest page (100) | 259 | 258 | 109 | 114 | 19 August to 10 October 2026 |
| **Total after overlapping-page deduplication** | **8 comment-page calls** | **548** | **545** | **215** | **226** | **19 August to 10 October 2026** |

Threads were deduplicated within each video using top-comment text and
publication time because this tool did not return stable thread IDs. Returned
replies, rather than advertised reply counts, determine reply totals. All
included authors had comparable identity forms; no unknown-identity messages
were counted. No verbatim comment fragments are published.

Sampling cap: at most 300 thread rows per video (two relevance pages plus one
latest page). The middle video exhausted both returned listings below that cap;
the other videos still had page cursors. This is **not an exhaustive export**
of every comment or reply. Relevance favours prominent threads; latest favours
recent reports. Edited comments and different page snapshots can change results.
Counts describe the sampled discussion, not independently confirmed bugs or the
percentage of all users affected. All three sources belong to one creator, so
these patterns should not be generalized to unrelated channels.

## Topic coverage

The following concerns appeared in the usable viewer material from **3 of 3**
selected videos (all three had usable comment samples):

- Shift Lock verification and the difference between its icon and cursor snap.
- NPC anchoring, root pages, menu transitions, and position drift.
- Bait purchases, counters, equipping, and running out of stock.
- Reel detection/steering, zones disappearing, and premature resets.
- Calibration, small images, screenshot capture, saving, and display changes.
- Launching, Python versions, missing libraries, and incomplete extraction.
- Windows/Linux/Mac/mobile requests and compatibility boundaries.
- Angler/quest requests and the gap between reading tasks and completing them.
- Chest detection versus opening inventory chests.
- Stopping/restarting, global controls, and long-run failures.
- Fruit, aura, race, and cosmetic visual interference.
- Ban risk, download alerts, and unsupported assurances about safety.
- Hardware, memory, screen layouts, and performance questions.

These are qualitative topic classifications, not quantitative prevalence
rankings. Installation, NPC/Shift Lock, bait, reeling, and calibration get direct
links because they recur and block progress. More detailed questions remain
searchable in the same guide.

## Sources that govern the answers

- `WINDOWS/bloxfish/config.py`: current version, thresholds, purchase settings,
  profile fields, and movement limits.
- `WINDOWS/bloxfish/routes.py`: explicit buying/selling/fish-only enablement.
- `WINDOWS/bloxfish/engine.py`: spending bait at a registered bite, purchase
  bookkeeping/failure limits, catch confirmation, watchdog, and restart paths.
- `WINDOWS/bloxfish/shop.py`: Shift Lock cursor verification, rod state,
  menu-page maps, dialogue evidence, movement probes, and route recovery.
- `WINDOWS/easy_run.py`: setup meanings, Re-shoot, selection/editing, saving,
  zoom inspectors, preparation, and GUI/terminal control differences.
- `WINDOWS/_bootstrap.py`: Windows dependency installation into the launching
  interpreter, unlike Sharkman's separate private environment.
- `WINDOWS/README.md`, `LINUX/README_LINUX.md`, Linux requirements/preflight
  and `install-udev.sh`: platform installation and diagnostics.
- `WINDOWS/docs/MECHANICS.md`: detector history and radial-push explanation.
- [Python Windows downloads](https://www.python.org/downloads/windows/) and
  [installation documentation](https://docs.python.org/3/using/windows.html).
- [Sober's official site](https://sober.vinegarhq.org/) for its current setup.
- [Roblox cheating/exploiting guidance](https://en.help.roblox.com/hc/en-us/articles/203312450-Cheating-and-Exploiting)
  for avoiding an invented ban guarantee.

The Mac fork link is present in the video's creator-written descriptions, not
in this project's maintained platform folders. Its current functionality could
not be independently verified; the guide explicitly identifies it as a separate
community fork rather than a supported launcher.

## Corrections to older advice

- Interact calibration is independent of the true game-window Shift Lock centre.
  Do not prescribe `shop.center` Y = `0.5107` as a universal fix.
- The reel and optional zone-track regions need the thin progress strip.
  The viewer suggestion to exclude it conflicts with the current detector.
- Blank starting bait disables restocking. Purchase amount is a different field.
- A registered bite spends estimated bait even if the fish is lost. The older
  config comment saying "per catch" is stale; engine behaviour governs.
- No general inventory OCR or auto-equip-bait route exists. A successful route
  does not independently verify the actual inventory quantity.
- Don’t buy disables selling and NPC logic, including Shift Lock verification.
- Correct F8 click dots show attempted targets, not confirmed input delivery.
- Version .44 verifies the Windows overlay's native click-through and can fall
  back to diagnostic logging without a visible overlay.
- No measured minimum PC/RAM specification, future OCR RAM cost, current reward
  cap, or fixed hourly profit is established. Avoid invented precise numbers.
- A private server and lack of injection do not establish immunity from bans.
- Download warnings require investigation, not blanket false-positive advice.

## Updating the guide

The 1.8.2.1 update adds searchable answers for partial reel width and a restock
stuck on Basic Bait / Back, plus release notes, full-button calibration margins,
purchase acknowledgment limits, and Windows F8 capture protection. Existing
answer anchors are preserved. The supplied #53 recording reproduces the width
defect; #55's exact intermittent run and reported camera turning are not
live-confirmed, since that report has no attached profile or captures.

Headless Chrome checks passed at 1440x900, 900x600, and 390x844: searching the
new symptoms, opening answer links, collapsing answers, and filtering categories.
Screenshots were inspected at desktop and phone sizes. No horizontal overflow,
page script errors, or remote resource requests were found. These browser checks
exercise the local guide only, not Roblox or Sober.

Update its visible version and reviewed date with meaningful releases. Recheck
page/action maps, setup fields, route enablement, stock bookkeeping, platform
support, diagnostics, and commands. Keep anchor IDs stable so shared answer
links remain useful. Add questions without exposing commenter identities or
turning speculation into confirmed features. Run `test_guide.py` and inspect
search/category/deep links in a browser when changing the guide's interaction.
