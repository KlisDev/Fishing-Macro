"""Blox Fruits auto-fisher — double-click launcher with a GUI.

Same bot as `run.py`, without the terminal. Aimed at people who just want to
double-click a file:

  * installs `requirements.txt` on first run (once per machine, tracked by a
    marker file next to the config);
  * asks the setup questions as an illustrated form, one card per question;
  * shows the pre-flight checklist, with pictures for the two steps people get
    wrong most (standing in range, and equipping the rod);
  * runs the engine with a live log, F2 to start/stop.

Drop your own screenshots into `assets/gui/` to illustrate the cards — see
`IMAGES` below for the filenames. Anything missing is simply skipped, so the
app works with no images at all.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT / "assets" / "gui"

sys.path.insert(0, str(ROOT))
from _bootstrap import ensure_requirements          # noqa: E402

# Card key -> image filename in assets/gui/. Add the PNGs yourself.
IMAGES = {
    "npc": "npc.png",
    "bait_amount": "bait_amount.png",
    "rod_slot": "rod_slot.png",
    "sell_every": "sell_every.png",
    "bait_now": "bait_now.png",
    "slow_flick": "slow_flick.png",
    "fast_bite": "fast_bite.png",
    "check_range": "check_range.png",   # checklist item 1
    "check_rod": "check_rod.png",       # checklist item 6
}


# --------------------------------------------------------------------------
# first run: install dependencies
# --------------------------------------------------------------------------

def _boot() -> None:
    """Get the dependencies in place before anything imports them."""
    ensure_requirements()
    try:
        import customtkinter  # noqa: F401
    except ImportError:
        # The GUI extras are not in the core requirement check, so fetch them
        # explicitly rather than failing with a traceback.
        try:
            subprocess.run([sys.executable, "-m", "pip", "install",
                            "customtkinter", "pillow"],
                           capture_output=True, text=True, timeout=600)
        except Exception:                          # noqa: BLE001
            pass


_boot()

import customtkinter as ctk                        # noqa: E402

try:
    from PIL import Image
except ImportError:                                # noqa: BLE001
    Image = None

from bloxfish.config import Config                 # noqa: E402
from bloxfish.engine import FishingEngine          # noqa: E402

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

ACCENT = "#2fa572"
BG_CARD = "#1d1f22"
MUTED = "#9aa0a6"


def load_image(key: str, width: int = 320):
    """CTkImage for a card, or None if the PNG has not been added."""
    if Image is None:
        return None
    path = ASSETS / IMAGES.get(key, "")
    if not path.exists():
        return None
    try:
        img = Image.open(path)
        h = max(1, int(img.height * width / img.width))
        return ctk.CTkImage(light_image=img, dark_image=img, size=(width, h))
    except Exception:                              # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# a single question card
# --------------------------------------------------------------------------

class Card(ctk.CTkFrame):
    """One question: title, optional picture, hint, and an input widget."""

    def __init__(self, master, title: str, hint: str, image_key: str) -> None:
        super().__init__(master, fg_color=BG_CARD, corner_radius=14)
        self.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(self, text=title, font=ctk.CTkFont(size=17, weight="bold"),
                     anchor="w", justify="left", wraplength=560).grid(
            row=0, column=0, sticky="ew", padx=18, pady=(16, 2))
        if hint:
            ctk.CTkLabel(self, text=hint, font=ctk.CTkFont(size=12),
                         text_color=MUTED, anchor="w", justify="left",
                         wraplength=560).grid(row=1, column=0, sticky="ew",
                                              padx=18, pady=(0, 8))
        img = load_image(image_key)
        if img is not None:
            ctk.CTkLabel(self, text="", image=img).grid(
                row=2, column=0, padx=18, pady=(2, 8))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=3, column=0, sticky="ew", padx=18, pady=(0, 16))


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

# Groups: (title, icon, colour, entries)
# Entry:  (key, kind, config-holder, fields, label, description, image key)
#   kind "box" -> search area   : drag to move, drag the corner to resize
#   kind "dot" -> click point   : drag onto the button
# A box needs 4 fields (l, t, r, b), or 2 for a full-width band (top, bottom).
CALIB_GROUPS = [
    ("Fishing", "🎣", "#22d3ee", [
        ("bar_search", "box", "detection",
         ("bar_search_top", "bar_search_bottom"), "Reel bar band",
         "Cover the strip of screen the reel minigame bar appears in. "
         "Only the top and bottom edges matter — it always spans the full "
         "width. Leave room above and below it.", "bar_search"),
        ("bite", "box", "detection",
         ("bite_left", "bite_top", "bite_right", "bite_bottom"), "Bite marker",
         "Cover where the pink “!” pops up over your head. Keep it snug around "
         "your character: if it reaches the player list in the top-right "
         "corner, that red row looks like a “!” and the bot bites at nothing.",
         "bite"),
        ("meter", "box", "detection",
         ("meter_left", "meter_top", "meter_right", "meter_bottom"),
         "Cast charge meter",
         "Cover the thin green bar that fills next to you while a cast charges. "
         "Stay in the middle of the screen — your health and energy bars are "
         "green too, and would be mistaken for it.", "meter"),
    ]),
    ("Catch popups", "💬", "#fb923c", [
        ("popup", "box", "dialog", ("left", "top", "right", "bottom"),
         "Catch card",
         "Cover where the “Species / Weight” card appears after a catch. This "
         "is how the bot knows something is covering the screen.", "popup"),
        ("learn", "box", "dialog",
         ("learn_left", "learn_top", "learn_right", "learn_bottom"),
         "“Learn” button — area",
         "Cover the blue “Learn” button on the rare new-recipe note. That note "
         "never disappears on its own, so the bot has to spot it.", "learn"),
        ("learn_click", "dot", "dialog", ("learn_click",),
         "“Learn” button",
         "Drop the dot in the middle of the “Learn” button.", "learn"),
    ]),
    ("Talking to the NPC", "🧑", "#60a5fa", [
        ("menu", "box", "shop",
         ("menu_left", "menu_top", "menu_right", "menu_bottom"),
         "Menu buttons — area",
         "Cover the whole stack of dialogue buttons (Shop, Fishing Index, Job "
         "Stats, Nevermind). The bot counts the buttons in here to tell whether "
         "the dialogue is open, so include all of them and little else.",
         "menu"),
        ("center", "dot", "shop", ("center",), "“Interact”",
         "Drop the dot on the “Interact” prompt — the middle of your screen "
         "while you are stood at the NPC.", "center"),
        ("menu_item1", "dot", "shop", ("menu_item1",), "Top menu button",
         "Drop the dot on the TOP button in the stack. The bot presses it for "
         "“Shop”, “Buy Bait”, “Basic Bait” and “Confirm”.", "menu"),
        ("menu_item2", "dot", "shop", ("menu_item2",), "2nd menu button",
         "Drop the dot on the SECOND button down. Used only for “Sell Fish”.",
         "menu"),
        ("menu_last", "dot", "shop", ("menu_last",), "Bottom menu button",
         "Drop the dot on the BOTTOM button. The bot presses it for “Back” and "
         "then “Nevermind” to leave the dialogue.", "menu"),
    ]),
    ("Buying bait", "🪙", "#c084fc", [
        ("craft_btn", "box", "shop",
         ("craft_btn_left", "craft_btn_top", "craft_btn_right", "craft_btn_bottom"),
         "“Craft” button — area",
         "Cover the yellow “Craft” button. The bot watches this to tell whether "
         "the craft window is open, so keep other windows from overlapping it.",
         "craft_btn"),
        ("craft_plus", "dot", "shop", ("craft_plus",), "“+” quantity",
         "Drop the dot on the blue “+” next to the bait count. One press = 10 "
         "more bait.", "craft_plus"),
        ("craft_button", "dot", "shop", ("craft_button",), "“Craft”",
         "Drop the dot in the middle of the yellow “Craft” button.", "craft_btn"),
        ("craft_close", "dot", "shop", ("craft_close",), "“Close”",
         "Drop the dot on the red “Close” at the top-right of the craft window. "
         "Only used to back out if something goes wrong.", "craft_close"),
    ]),
]

GHOST = "#5b6169"


def _group_of(key: str):
    for title, icon, colour, entries in CALIB_GROUPS:
        for e in entries:
            if e[0] == key:
                return title, icon, colour, e
    return None, None, None, None


_BLANK_IMG = None


def _blank_image():
    """A transparent placeholder.

    `CTkLabel.configure(image=None)` raises `TclError: image ... doesn't exist`
    once a real image has been shown, which killed the whole selection handler
    the first time an item without a reference PNG was picked. Swapping in a
    blank image clears the slot safely.
    """
    global _BLANK_IMG
    if _BLANK_IMG is None and Image is not None:
        blank = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        _BLANK_IMG = ctk.CTkImage(light_image=blank, dark_image=blank,
                                  size=(1, 1))
    return _BLANK_IMG


def _calib_image(key: str, width: int = 230):
    """Reference PNG for an item (assets/gui/calib/<key>.png), or None."""
    if Image is None or not key:
        return None
    path = ASSETS / "calib" / f"{key}.png"
    if not path.exists():
        return None
    try:
        img = Image.open(path)
        h = max(1, int(img.height * width / img.width))
        return ctk.CTkImage(light_image=img, dark_image=img, size=(width, h))
    except Exception:                                  # noqa: BLE001
        return None


class Calibrator(ctk.CTkToplevel):
    """Line the bot's boxes and click points up with your own game UI.

    Positions are stored as fractions of the game window so they travel between
    machines — but the shipped numbers came from one particular layout. Pick an
    item on the left, drag it into place, save. Once per computer.
    """

    HANDLE = 10     # corner grip for a free box
    GRIP = 30       # half-width of the edge grips on a full-width band
    DOT_R = 10

    def __init__(self, master, cfg: Config) -> None:
        super().__init__(master)
        self.title("Calibrate")
        self.cfg = cfg
        self.geometry("1280x820")
        self.master_app = master
        self.sel: str | None = None
        self.drag: tuple | None = None
        self.shapes: dict[str, dict] = {}
        self.scale = 1.0
        # Set by shoot(). Until then there is nothing to draw against, and
        # every coordinate helper would raise on a missing attribute.
        self.win = None
        self.found = False

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(0, weight=1)

        # ---- left: grouped list -----------------------------------------
        left = ctk.CTkScrollableFrame(outer, width=300,
                                      label_text="  Pick something to line up")
        left.grid(row=0, column=0, sticky="ns", padx=(0, 10))
        self.buttons: dict[str, ctk.CTkButton] = {}
        for title, icon, colour, entries in CALIB_GROUPS:
            hdr = ctk.CTkFrame(left, fg_color="transparent")
            hdr.pack(fill="x", pady=(14, 4), padx=2)
            ctk.CTkLabel(hdr, text=f"{icon}  {title}", anchor="w",
                         font=ctk.CTkFont(size=13, weight="bold"),
                         text_color=colour).pack(side="left")
            for key, kind, _h, _f, label, _d, _i in entries:
                mark = "▭" if kind == "box" else "⦿"
                what = "area" if kind == "box" else "click"
                row = ctk.CTkButton(
                    left, text=f"   {mark}   {label}", anchor="w", height=34,
                    fg_color="transparent", hover_color="#2b3036",
                    text_color="#d7dade",
                    font=ctk.CTkFont(size=12),
                    command=lambda k=key: self.select(k))
                row.pack(fill="x", padx=2, pady=1)
                self.buttons[key] = row

        ctk.CTkLabel(left, text="\n▭  area the bot looks at\n"
                               "⦿  point the bot clicks\n",
                     anchor="w", justify="left", text_color=MUTED,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=6, pady=(16, 4))

        # ---- right: canvas + details ------------------------------------
        right = ctk.CTkFrame(outer, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(right, fg_color="transparent")
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        self.hint = ctk.CTkLabel(bar, text="Pick an item on the left to begin.",
                                 text_color=MUTED, anchor="w")
        self.hint.pack(side="left")
        # Calibrating against the wrong rectangle is silent and ruinous: every
        # number here is a fraction of the game window, so if we photographed
        # the whole desktop instead, the boxes you line up are stored against
        # the wrong size and the bot ends up searching empty screen.
        self.warn = ctk.CTkLabel(right, text="", text_color="#ef476f",
                                 anchor="w", justify="left", wraplength=880,
                                 font=ctk.CTkFont(size=12, weight="bold"))
        ctk.CTkButton(bar, text="💾  Save", width=100, fg_color=ACCENT,
                      command=self.save).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bar, text="📷  Re-shoot", width=110, fg_color="#3a3f45",
                      hover_color="#4a5057", command=self.shoot).pack(side="right")

        self.canvas = ctk.CTkCanvas(right, bg="#0d0f11", highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.canvas.bind("<Configure>", lambda e: self._fit())

        det = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=12)
        det.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        det.grid_columnconfigure(0, weight=1)
        self.d_title = ctk.CTkLabel(det, text="Nothing selected", anchor="w",
                                    font=ctk.CTkFont(size=15, weight="bold"))
        self.d_title.grid(row=0, column=0, sticky="ew", padx=16, pady=(12, 0))
        self.d_text = ctk.CTkLabel(
            det, text="Everything is greyed out until you pick an item — only "
                      "the one you choose can be moved.",
            anchor="w", justify="left", wraplength=600, text_color=MUTED)
        self.d_text.grid(row=1, column=0, sticky="ew", padx=16, pady=(4, 12))
        self.d_img = ctk.CTkLabel(det, text="")
        self.d_img.grid(row=0, column=1, rowspan=2, padx=16, pady=12)

        self.after(250, self.shoot)

    # -- screenshot -------------------------------------------------------
    def shoot(self) -> None:
        """Grab the game with our own windows hidden.

        Without this the tool photographs itself sitting on top of the very UI
        you are trying to line up against.
        """
        from bloxfish.capture import Screen, find_game_window
        self.withdraw()
        try:
            self.master_app.withdraw()
        except Exception:                              # noqa: BLE001
            pass
        self.update()
        time.sleep(0.45)                               # let the compositor settle
        scr = Screen()
        try:
            self.win, self.found = find_game_window(self.cfg.window_title, scr)
            shot = scr.grab(self.win)
        finally:
            scr.close()
        if self.found:
            self.warn.grid_forget()
        else:
            self.warn.configure(
                text="⚠  Roblox was not found — this is a picture of your whole "
                     "screen, not the game window. Anything you line up now "
                     "will be saved against the wrong size and the bot will "
                     "look in the wrong place. Start Roblox, bring it to the "
                     "front, then press Re-shoot.")
            self.warn.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self.deiconify()
        try:
            self.master_app.deiconify()
        except Exception:                              # noqa: BLE001
            pass
        self.lift()
        if Image is None:
            return
        self._shot = Image.fromarray(shot[:, :, ::-1])
        self._fit()

    def _fit(self) -> None:
        """Scale the screenshot to fill the canvas.

        The canvas must be measured *after* Tk has laid it out — asking too
        early returns 1, which used to fall back to a 400 px image marooned in
        the corner of a much larger canvas, with every box and dot squeezed
        into it.
        """
        img = getattr(self, "_shot", None)
        if img is None:
            return
        self.canvas.update_idletasks()
        cw, ch = self.canvas.winfo_width(), self.canvas.winfo_height()
        if cw < 50 or ch < 50:                       # not laid out yet
            self.after(120, self._fit)
            return
        self.scale = min(cw / img.width, ch / img.height)
        size = (max(1, int(img.width * self.scale)),
                max(1, int(img.height * self.scale)))
        self._photo = ctk.CTkImage(light_image=img, dark_image=img, size=size)
        self._tk_img = self._photo._get_scaled_light_photo_image(size)
        self.redraw()

    # -- drawing ----------------------------------------------------------
    # The two helpers below deliberately go through the *same* arithmetic the
    # bot does, rather than re-deriving it: `Rect.sub` for search areas (as in
    # FishingEngine.__init__) and int(round(...)) for click points (as in
    # shop._abs). What you see outlined here is therefore the exact rectangle
    # the bot will grab, down to the pixel, not an approximation of it.
    def _box_px(self, l: float, t: float, r: float,
                b: float) -> tuple[float, float, float, float]:
        sub = self.win.sub(l, t, r, b)
        x0 = (sub.left - self.win.left) * self.scale
        y0 = (sub.top - self.win.top) * self.scale
        return x0, y0, x0 + sub.width * self.scale, y0 + sub.height * self.scale

    def _dot_px(self, fx: float, fy: float) -> tuple[float, float]:
        return (int(round(self.win.width * fx)) * self.scale,
                int(round(self.win.height * fy)) * self.scale)

    _HALO = ((-1, 0), (1, 0), (0, -1), (0, 1),
             (-1, -1), (1, -1), (-1, 1), (1, 1), (-2, 0), (2, 0))

    def _text(self, x: float, y: float, text: str, colour: str) -> list:
        """Canvas text with a black halo — the game behind it is any colour.

        Returns every id it made, each with its offset from the anchor, so the
        label can be dragged along with its shape instead of being redrawn.
        """
        font = ("Segoe UI", 10, "bold")
        ids = [(self.canvas.create_text(x + dx, y + dy, text=text,
                                        fill="#000000", anchor="w", font=font),
                dx, dy) for dx, dy in self._HALO]
        ids.append((self.canvas.create_text(x, y, text=text, fill=colour,
                                            anchor="w", font=font), 0, 0))
        return ids

    def _place_text(self, ids, x: float, y: float) -> None:
        for cid, dx, dy in ids or ():
            self.canvas.coords(cid, x + dx, y + dy)

    def _fracs(self, spec) -> tuple:
        """The stored value of an entry, as plain fractions."""
        _k, kind, holder, fields, *_ = spec
        obj = getattr(self.cfg, holder)
        if kind != "box":
            return tuple(getattr(obj, fields[0]))
        if len(fields) == 2:
            # A full-width band. Left and right are not stored at all, so they
            # are not editable either -- see _move.
            return 0.0, getattr(obj, fields[0]), 1.0, getattr(obj, fields[1])
        return tuple(getattr(obj, f) for f in fields)

    def redraw(self) -> None:
        self.canvas.delete("all")
        self.shapes.clear()
        if getattr(self, "_tk_img", None) is not None:
            self.canvas.create_image(0, 0, anchor="nw", image=self._tk_img)
        if self.win is None:                       # no screenshot yet
            return

        for title, icon, colour, entries in CALIB_GROUPS:
            for entry in entries:
                key, kind, _holder, fields, label = entry[:5]
                active = key == self.sel
                col = colour if active else GHOST
                if kind == "box":
                    x0, y0, x1, y1 = self._box_px(*self._fracs(entry))
                    rid = self.canvas.create_rectangle(
                        x0, y0, x1, y1, outline=col, width=3 if active else 1,
                        dash=() if active else (3, 4))
                    grips: dict[str, int] = {}
                    tid = None
                    if active:
                        # Only the selected item is labelled. Drawing all of
                        # them at once was unreadable clutter.
                        tid = self._text(x0 + 8, y0 + 13, f"{icon} {label}", col)
                        g = lambda a, b_, c, d: self.canvas.create_rectangle(   # noqa: E731
                            a, b_, c, d, outline="#ffffff", fill=col, width=2)
                        if len(fields) == 2:
                            # Height-only band: grips on the edges that exist.
                            # It used to get a corner handle like any other
                            # box, so you could drag its sides inwards, watch
                            # it narrow, save -- and find it full width again
                            # on the next open, because there is nowhere to
                            # store a left or a right.
                            cx = (x0 + x1) / 2
                            grips["top"] = g(cx - self.GRIP, y0 - 4,
                                             cx + self.GRIP, y0 + 4)
                            grips["bottom"] = g(cx - self.GRIP, y1 - 4,
                                                cx + self.GRIP, y1 + 4)
                        else:
                            grips["corner"] = g(x1 - self.HANDLE,
                                                y1 - self.HANDLE, x1, y1)
                    self.shapes[key] = {"kind": "box", "id": rid, "label": tid,
                                        "grips": grips, "entry": entry}
                else:
                    x, y = self._dot_px(*self._fracs(entry))
                    r = self.DOT_R + (4 if active else 0)
                    oid = self.canvas.create_oval(
                        x - r, y - r, x + r, y + r,
                        outline="#ffffff" if active else col,
                        width=3 if active else 1,
                        fill=col if active else "")
                    tid = tick = None
                    if active:
                        tick = self.canvas.create_line(
                            x - r - 7, y, x - r - 1, y, fill="#ffffff", width=2)
                        tid = self._text(x + r + 7, y, f"{icon} {label}", col)
                    self.shapes[key] = {"kind": "dot", "id": oid, "label": tid,
                                        "tick": tick, "r": r, "entry": entry}

    # -- selection --------------------------------------------------------
    def select(self, key: str) -> None:
        """Make `key` the editable item. Must never raise: if this dies, the
        list stops responding and the tool looks frozen."""
        self.sel = key
        title, icon, colour, spec = _group_of(key)
        for k, b in self.buttons.items():
            b.configure(fg_color=colour if k == key else "transparent")
        if spec:
            _k, kind, _h, _f, label, desc, img_key = spec
            self.d_title.configure(text=f"{icon}  {label}", text_color=colour)
            self.d_text.configure(text=desc, text_color="#d7dade")
            img = _calib_image(img_key) or _blank_image()
            try:
                self.d_img.configure(image=img, text="")
            except Exception:                          # noqa: BLE001
                pass                                   # never break selection
            self.d_img.image = img
            if kind != "box":
                tip = "Drag the dot onto the button"
            elif len(_f) == 2:
                tip = ("Drag the band up or down • drag the white grip on the "
                       "top or bottom edge to change its height (it always "
                       "spans the full width)")
            else:
                tip = ("Drag inside the box to move it • drag the white corner "
                       "to resize")
            self.hint.configure(text=tip)
        self.redraw()

    # -- dragging (selected item only) -------------------------------------
    def _down(self, ev) -> None:
        if not self.sel or self.sel not in self.shapes:
            return
        it = self.shapes[self.sel]
        for name, gid in it.get("grips", {}).items():
            gx0, gy0, gx1, gy1 = self.canvas.coords(gid)
            if gx0 - 4 <= ev.x <= gx1 + 4 and gy0 - 4 <= ev.y <= gy1 + 4:
                self.drag = (name, ev.x, ev.y)
                return
        x0, y0, x1, y1 = self.canvas.coords(it["id"])
        # Dots are small targets, so allow a wide grab radius around them.
        pad = 16 if it["kind"] == "dot" else 8
        if x0 - pad <= ev.x <= x1 + pad and y0 - pad <= ev.y <= y1 + pad:
            self.drag = ("move", ev.x, ev.y)

    def _up(self, _ev=None) -> None:
        # Commit on release as well: a click that nudges by a pixel never fires
        # <B1-Motion>, and the edit would otherwise be dropped.
        if self.drag and self.sel:
            self._commit(self.sel)
            self._resync(self.sel)
        self.drag = None

    def _move(self, ev) -> None:
        if not self.drag or not self.sel:
            return
        mode, px, py = self.drag
        dx, dy = ev.x - px, ev.y - py
        it = self.shapes[self.sel]
        x0, y0, x1, y1 = self.canvas.coords(it["id"])
        if mode == "move":
            x0, y0, x1, y1 = x0 + dx, y0 + dy, x1 + dx, y1 + dy
        elif mode == "corner":
            x1, y1 = max(x0 + 26, x1 + dx), max(y0 + 20, y1 + dy)
        elif mode == "bottom":
            y1 = max(y0 + 20, y1 + dy)
        elif mode == "top":
            y0 = min(y1 - 20, y0 + dy)
        self.canvas.coords(it["id"], x0, y0, x1, y1)
        self.drag = (mode, ev.x, ev.y)
        self._commit(self.sel)
        self._resync(self.sel)

    def _commit(self, key: str) -> None:
        """Read the shape off the canvas and store it as fractions."""
        _t, _i, _c, spec = _group_of(key)
        if not spec or self.win is None or key not in self.shapes:
            return
        _k, kind, holder, fields, *_ = spec
        obj = getattr(self.cfg, holder)
        W = self.win.width * self.scale
        H = self.win.height * self.scale
        if W < 1 or H < 1:
            return
        x0, y0, x1, y1 = self.canvas.coords(self.shapes[key]["id"])
        clip = lambda v: round(min(1.0, max(0.0, v)), 4)     # noqa: E731
        if kind == "box":
            if len(fields) == 2:
                setattr(obj, fields[0], clip(y0 / H))
                setattr(obj, fields[1], clip(y1 / H))
            else:
                for f, v in zip(fields, (x0 / W, y0 / H, x1 / W, y1 / H)):
                    setattr(obj, f, clip(v))
        else:
            setattr(obj, fields[0], (clip((x0 + x1) / 2 / W),
                                     clip((y0 + y1) / 2 / H)))

    def _resync(self, key: str) -> None:
        """Redraw the shape from the value that was actually stored.

        The stored fractions are the truth; the outline is only a view of them.
        Snapping back after every commit means the tool can never show you a
        box it did not keep -- which is what made a resized full-width band
        look saved and then reappear at its old size on the next open.
        """
        it = self.shapes.get(key)
        if it is None or self.win is None:
            return
        entry = it["entry"]
        if it["kind"] == "box":
            x0, y0, x1, y1 = self._box_px(*self._fracs(entry))
            self.canvas.coords(it["id"], x0, y0, x1, y1)
            cx = (x0 + x1) / 2
            grips = it.get("grips", {})
            if "corner" in grips:
                self.canvas.coords(grips["corner"], x1 - self.HANDLE,
                                   y1 - self.HANDLE, x1, y1)
            if "top" in grips:
                self.canvas.coords(grips["top"], cx - self.GRIP, y0 - 4,
                                   cx + self.GRIP, y0 + 4)
            if "bottom" in grips:
                self.canvas.coords(grips["bottom"], cx - self.GRIP, y1 - 4,
                                   cx + self.GRIP, y1 + 4)
            self._place_text(it.get("label"), x0 + 8, y0 + 13)
        else:
            x, y = self._dot_px(*self._fracs(entry))
            r = it["r"]
            self.canvas.coords(it["id"], x - r, y - r, x + r, y + r)
            if it.get("tick") is not None:
                self.canvas.coords(it["tick"], x - r - 7, y, x - r - 1, y)
            self._place_text(it.get("label"), x + r + 7, y)

    def save(self) -> None:
        if self.sel:
            self._commit(self.sel)
        try:
            self.cfg.save()
        except Exception as exc:                       # noqa: BLE001
            # Read-only folder, a cloud-sync client holding the file open, no
            # permission in Program Files... silently swallowing this was the
            # difference between "saved" and "saved nowhere".
            self.hint.configure(text=f"✖  Could not write config.json: {exc}",
                                text_color="#ef476f")
            return
        self.hint.configure(text="✔  Saved to config.json", text_color=ACCENT)
        self.after(2500, lambda: self.hint.configure(text_color=MUTED))
        self.redraw()


# --------------------------------------------------------------------------
# main app
# --------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Blox Fruits Auto-Fisher")
        self.geometry("720x820")
        self.minsize(660, 620)
        self.cfg = Config.load()
        self.engine: FishingEngine | None = None
        self.worker: threading.Thread | None = None
        self._quit = False
        self._build_form()

    # -- page 1: the form -------------------------------------------------
    def _build_form(self) -> None:
        self._page = "form"
        for w in self.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 4))
        ctk.CTkLabel(head, text="Set up your run",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        ctk.CTkButton(head, text="Calibrate controls", width=150,
                      fg_color="#3a3f45", hover_color="#4a5057",
                      command=self._calibrate).pack(side="right")

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=6)
        body.grid_columnconfigure(0, weight=1)
        row = 0

        def add(card: Card) -> None:
            nonlocal row
            card.grid(row=row, column=0, sticky="ew", pady=8)
            row += 1

        # 1. NPC
        c = Card(body, "Which NPC will you buy from?",
                 "Only the Fisherman is mapped. Pick “Don't buy” to fish on the "
                 "bait you already have.", "npc")
        self.v_npc = ctk.StringVar(value="Fisherman")
        ctk.CTkSegmentedButton(c.body, values=["Fisherman", "Don't buy"],
                               variable=self.v_npc).pack(fill="x")
        add(c)

        # 2. bait per purchase
        step = max(1, self.cfg.shop.craft_step)
        c = Card(body, "How much bait per purchase?",
                 f"Must be a multiple of {step}. Each extra {step} is one more "
                 f"“+” click in the craft window.", "bait_amount")
        self.v_amount = ctk.StringVar(value=str(self.cfg.shop.bait_per_purchase))
        ctk.CTkOptionMenu(c.body, values=[str(step * n) for n in range(1, 11)],
                          variable=self.v_amount).pack(fill="x")
        add(c)

        # 3. rod slot
        c = Card(body, "Which hotbar slot is your fishing rod in?",
                 "The bot stows and re-draws the rod around each bait trip — "
                 "that is what clears the post-catch stuck state.", "rod_slot")
        self.v_rod = ctk.StringVar(value=str(self.cfg.rod_slot))
        ctk.CTkSegmentedButton(c.body,
                               values=[str(d) for d in range(1, 10)] + ["0"],
                               variable=self.v_rod).pack(fill="x")
        add(c)

        # 4. auto-sell
        c = Card(body, "Sell your fish every how many catches?",
                 "0 turns auto-selling off. The NPC never buys favourited fish "
                 "or your heaviest.", "sell_every")
        self.v_sell = ctk.StringVar(value=str(self.cfg.sell.every))
        ctk.CTkOptionMenu(c.body, values=["0", "25", "50", "100", "150", "200"],
                          variable=self.v_sell).pack(fill="x")
        add(c)

        # 5. current bait (optional)
        c = Card(body, "How much bait do you have right now?",
                 "Optional — leave blank and the bot simply never stops to buy.",
                 "bait_now")
        self.v_bait = ctk.StringVar(value="")
        ctk.CTkEntry(c.body, textvariable=self.v_bait,
                     placeholder_text="e.g. 80  (blank = don't track)").pack(fill="x")
        add(c)

        # 6. slower fish trick
        c = Card(body, "Slower fish trick",
                 "Turn this on if you always end up with a glitched fish in "
                 "your hand.\n\nNormally the rod is flicked off and on the "
                 "instant a fish lands, which skips the catch card entirely. "
                 "This waits 0.5 s before the flick and 0.5 s between the two "
                 "presses - slower per fish, but it avoids the glitch.",
                 "slow_flick")
        self.v_slow = ctk.BooleanVar(value=self.cfg.timing.slow_rod_flick)
        ctk.CTkSwitch(c.body, text="  Use the slower, safer flick",
                      variable=self.v_slow, progress_color=ACCENT,
                      font=ctk.CTkFont(size=13)).pack(anchor="w")
        add(c)

        # 7. faster bite reaction
        c = Card(body, "Faster bite reaction time",
                 "Turn this on if your computer is fast enough to keep up "
                 "with the program.\n\nThe bot normally takes about 0.8-1.5 s "
                 "to react once a fish bites. This polls the screen far "
                 "harder and drops the reaction padding, bringing it under "
                 "0.2 s. It uses more CPU, so leave it off on a slow machine.",
                 "fast_bite")
        self.v_fast = ctk.BooleanVar(value=self.cfg.timing.fast_bite)
        ctk.CTkSwitch(c.body, text="  React as fast as possible",
                      variable=self.v_fast, progress_color=ACCENT,
                      font=ctk.CTkFont(size=13)).pack(anchor="w")
        add(c)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(4, 18))
        self.err = ctk.CTkLabel(foot, text="", text_color="#ef476f")
        self.err.pack(side="left")
        ctk.CTkButton(foot, text="Continue  →", width=150, height=40,
                      fg_color=ACCENT, hover_color="#268a5f",
                      font=ctk.CTkFont(size=15, weight="bold"),
                      command=self._apply_form).pack(side="right")

    def _calibrate(self) -> None:
        # One at a time. Two of these fight over withdrawing the main window
        # for the screenshot, and each saves over the other's numbers.
        win = getattr(self, "_calib", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        self._calib = Calibrator(self, self.cfg)

    def _apply_form(self) -> None:
        # A quick double-click on Continue fires this twice; the first call
        # tears the page down, so the second would poke destroyed widgets and
        # crash the window. Ignore anything after the first.
        if getattr(self, "_page", "form") != "form":
            return
        cfg = self.cfg
        cfg.shop.npc = "fisherman" if self.v_npc.get() == "Fisherman" else "none"
        cfg.shop.bait_per_purchase = int(self.v_amount.get())
        cfg.rod_slot = self.v_rod.get()
        cfg.sell.every = int(self.v_sell.get())
        cfg.sell.enabled = cfg.sell.every > 0
        cfg.timing.slow_rod_flick = bool(self.v_slow.get())
        cfg.timing.fast_bite = bool(self.v_fast.get())
        raw = self.v_bait.get().strip()
        if raw and not raw.isdigit():
            self.err.configure(text="Bait must be a whole number, or blank.")
            return
        self.bait = int(raw) if raw else None
        self._build_checklist()

    # -- page 2: the checklist -------------------------------------------
    def _build_checklist(self) -> None:
        self._page = "checklist"
        for w in self.winfo_children():
            w.destroy()
        ctk.CTkLabel(self, text="Before you start",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(
            anchor="w", padx=20, pady=(18, 0))
        ctk.CTkLabel(self, text="Get these exactly right, or the clicks miss.",
                     text_color=MUTED).pack(anchor="w", padx=20, pady=(0, 8))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=4)
        body.grid_columnconfigure(0, weight=1)

        steps = [
            ("1. Stand at the NPC, right on the edge of interaction range.",
             "The “Interact” prompt should be showing.", "check_range"),
            ("2. Turn OFF auto-run / running.", "", None),
            ("3. Turn OFF shift lock.", "The bot switches it on itself.", None),
            ("4. Leave Roblox focused and don't touch the mouse while it runs.",
             "", None),
            ("5. Don't let another window overlap the game.",
             "The bot reads the screen — a covered panel reads as “not there”.",
             None),
            (f"6. EQUIP your fishing rod (slot {self.cfg.rod_slot}) and leave it out.",
             "The bot stows and re-draws it around each bait trip, which is what "
             "clears the post-catch stuck state.", "check_rod"),
        ]
        for i, (title, hint, key) in enumerate(steps):
            f = ctk.CTkFrame(body, fg_color=BG_CARD, corner_radius=12)
            f.grid(row=i, column=0, sticky="ew", pady=6)
            f.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(f, text=title, font=ctk.CTkFont(size=15, weight="bold"),
                         anchor="w", justify="left", wraplength=560).grid(
                row=0, column=0, sticky="ew", padx=16, pady=(12, 2))
            if hint:
                ctk.CTkLabel(f, text=hint, text_color=MUTED, anchor="w",
                             justify="left", wraplength=560).grid(
                    row=1, column=0, sticky="ew", padx=16, pady=(0, 6))
            img = load_image(key) if key else None
            if img is not None:
                ctk.CTkLabel(f, text="", image=img).grid(row=2, column=0,
                                                         padx=16, pady=(2, 12))
            else:
                ctk.CTkFrame(f, height=4, fg_color="transparent").grid(row=2, column=0)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(4, 18))
        ctk.CTkButton(foot, text="←  Back", width=110, fg_color="#FC0404",
                      hover_color="#4a5057", command=self._build_form).pack(side="left")
        self.v_ready = ctk.BooleanVar(value=False)
        ctk.CTkCheckBox(foot, text="I've done all of the above",
                        variable=self.v_ready,
                        command=lambda: self.go.configure(
                            state="normal" if self.v_ready.get() else "disabled")
                        ).pack(side="left", padx=16)
        self.go = ctk.CTkButton(foot, text="YES — start", width=160, height=40,
                                fg_color=ACCENT, hover_color="#268a5f",
                                state="disabled",
                                font=ctk.CTkFont(size=15, weight="bold"),
                                command=self._build_runner)
        self.go.pack(side="right")

    # -- page 3: running --------------------------------------------------
    def _build_runner(self) -> None:
        self._page = "runner"
        for w in self.winfo_children():
            w.destroy()
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=20, pady=(18, 6))
        ctk.CTkLabel(head, text="Running",
                     font=ctk.CTkFont(size=26, weight="bold")).pack(side="left")
        self.badge = ctk.CTkLabel(head, text="idle", text_color=MUTED,
                                  font=ctk.CTkFont(size=14, weight="bold"))
        self.badge.pack(side="right")

        self.logbox = ctk.CTkTextbox(self, font=ctk.CTkFont(family="Consolas",
                                                            size=12))
        self.logbox.pack(fill="both", expand=True, padx=20, pady=6)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=20, pady=(4, 18))
        ctk.CTkLabel(foot, text="F2 start / stop      F4 quit",
                     text_color=MUTED).pack(side="left")
        self.btn = ctk.CTkButton(foot, text="Start  (F2)", width=150, height=40,
                                 fg_color=ACCENT, hover_color="#268a5f",
                                 font=ctk.CTkFont(size=15, weight="bold"),
                                 command=self._toggle)
        self.btn.pack(side="right")

        self.engine = FishingEngine(self.cfg, log=self._log)
        self.engine.bait_count = self.bait
        self._bind_hotkeys()
        self._log("Ready. Press Start (or F2) with Roblox focused.")

    def _log(self, *parts) -> None:
        line = " ".join(str(p) for p in parts)
        def append() -> None:
            self.logbox.insert("end", line + "\n")
            self.logbox.see("end")
        try:
            self.after(0, append)
        except Exception:                          # noqa: BLE001
            pass

    def _bind_hotkeys(self) -> None:
        try:
            import keyboard
        except Exception:                          # noqa: BLE001
            self._log("`keyboard` not available — use the button.")
            return
        last = [0.0]

        def toggle() -> None:
            now = time.perf_counter()
            if now - last[0] < 0.4:
                return
            last[0] = now
            self._toggle()

        # `keyboard` needs administrator rights on Windows for a global hook.
        # Without them add_hotkey raises — which must not take the window down,
        # since the on-screen button does the same job.
        try:
            keyboard.add_hotkey(self.cfg.start_stop_key, toggle)
            keyboard.add_hotkey(self.cfg.quit_key,
                                lambda: self.after(0, self._close))
        except Exception as exc:                       # noqa: BLE001
            self._log(f"Hotkeys unavailable ({exc}) — use the button instead. "
                      f"Run as administrator if you want F2/F4.")

    def _toggle(self) -> None:
        eng = self.engine
        if eng is None:
            return
        if eng.running:
            eng.stop()
            self.badge.configure(text="stopping", text_color="#ffd166")
            self.btn.configure(text="Start  (F2)")
            return
        if self.worker and self.worker.is_alive():
            return
        self.badge.configure(text="running", text_color=ACCENT)
        self.btn.configure(text="Stop  (F2)")
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()

    def _run(self) -> None:
        try:
            self.engine.run()
        finally:
            self.after(0, lambda: (self.badge.configure(text="idle",
                                                        text_color=MUTED),
                                   self.btn.configure(text="Start  (F2)")))

    def _close(self) -> None:
        if self._quit:
            return
        self._quit = True
        try:
            if self.engine:
                self.engine.stop()
                if self.worker and self.worker.is_alive():
                    self.worker.join(timeout=3.0)
                self.engine.close()
        except Exception:                          # noqa: BLE001
            pass
        self.destroy()


def main() -> int:
    ensure_requirements()
    app = App()
    app.protocol("WM_DELETE_WINDOW", app._close)
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
