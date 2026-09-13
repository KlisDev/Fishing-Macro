# Optional calibration reference images

`easy_run.py` shows a reference picture for a calibration control when this
folder contains a PNG with that control's slot name. It is a visual aid only;
the macro does not match against these files and works when any slot is empty.

For the Update 30 Fisherman menu, the ordinal row slots are:

| Slot | Root-page role | Other page role |
|---|---|---|
| `menu_item1.png` | Shop | Buy Bait / Basic Bait / Confirm |
| `menu_item2.png` | Fishing Index | Sell Fish |
| `menu_item3.png` | Job Stats | none |
| `menu_last.png` | Nevermind | Back on bait; Nevermind elsewhere |

The optional yellow-detector illustrations are `dialogue.png` for the catch
card header and `craft.png` for the Craft button. Use a cropped, sanitised UI
reference only; never put an account name, chat, player list, or other personal
information in these images.

## Buying Bait slots

The three click points in the Craft window each have an independent reference
slot. Add a cropped PNG for the exact control you are calibrating:

| Control | Filename |
|---|---|
| Add-bait `+` click point | `craft_plus.png` |
| Yellow Craft confirmation click point | `craft_button.png` |
| Red Close recovery click point | `craft_close.png` |

`craft_btn.png` remains the separate reference for the **Craft-button search
area**. A missing optional PNG simply leaves its guide image blank; it does not
affect the macro or calibration values.

## Optional colour-sample slots

The yellow Craft-colour sample has its own separate reference slot:

| Control | Filename |
|---|---|
| Craft button (yellow) sample | `craft.png` |

This is independent from `craft_btn.png` and the three Craft-window click-point
images above.
