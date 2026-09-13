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

import os
import math
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# The Linux launcher supplies its own asset root before importing this shared
# GUI.  Windows keeps the source-tree default, so existing launches do not
# change and each platform can use UI references that match its own client.
_asset_override = os.environ.get("BLOXFISH_GUI_ASSETS", "").strip()
ASSETS = (Path(_asset_override).expanduser().resolve()
          if _asset_override else ROOT / "assets" / "gui")

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
    # A platform launcher can manage its own dependencies and skip this — the
    # Linux entry (LINUX/easy_run_linux.py) installs from requirements-linux.txt
    # and sets this, so the Windows-oriented check never runs there.
    if os.environ.get("BLOXFISH_NO_BOOT"):
        return
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

from bloxfish.config import Config, COOLDOWNS, VERSION  # noqa: E402
from bloxfish.engine import FishingEngine          # noqa: E402
from bloxfish import vision                        # noqa: E402

try:
    import numpy as np
except ImportError:                                # noqa: BLE001
    np = None

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("dark-blue")

# The app deliberately uses a restrained set of dark-blue surfaces.  Apart
# from looking easier on the eyes beside a bright game, this costs essentially
# nothing to draw: there are no continuously animated gradients or effects.
APP_BG = "#08111f"
BG_CARD = "#111d31"
BG_CARD_RAISED = "#162642"
BG_SOFT = "#0c1729"
BORDER = "#253956"
TEXT = "#eff6ff"
MUTED = "#91a4c3"
ACCENT = "#38bdf8"
ACCENT_HOVER = "#149bd7"
SUCCESS = "#34d399"
WARNING = "#fbbf24"
DANGER = "#fb7185"


def speed_scroll(frame, factor: int = 2) -> None:
    """Make a CTkScrollableFrame's mouse wheel scroll `factor`x faster.

    CTk binds its own wheel handler at init, so rather than fight it we add an
    *extra* scroll on top — but only while the pointer is actually over this
    frame, so it doesn't hijack scrolling elsewhere (e.g. the cooldown editor).
    """
    canvas = getattr(frame, "_parent_canvas", None)
    if canvas is None:
        return

    def _on_wheel(event):
        try:
            w = frame.winfo_containing(event.x_root, event.y_root)
        except Exception:                              # noqa: BLE001
            return
        while w is not None:
            if w is frame:
                step = 1 if event.delta > 0 else -1
                canvas.yview_scroll(-factor * step, "units")
                break
            w = getattr(w, "master", None)

    try:
        frame.bind_all("<MouseWheel>", _on_wheel, add=True)
    except Exception:                                  # noqa: BLE001
        pass


_IMAGE_CACHE: dict[tuple[str, int], object] = {}


def load_image(key: str, width: int = 220):
    """Return one cached, downsampled preview with rounded corners.

    The source screenshots are much larger than their visible card previews.
    Resizing once at load time keeps scrolling inexpensive and the cache avoids
    decoding the same asset again whenever a setup page is rebuilt.
    """
    if Image is None:
        return None
    path = ASSETS / IMAGES.get(key, "")
    if not path.exists():
        return None
    cache_key = (str(path), width)
    if cache_key in _IMAGE_CACHE:
        return _IMAGE_CACHE[cache_key]
    try:
        # ``copy`` detaches the decoded pixels from the file before Pillow
        # closes it; ``thumbnail`` never upscales an already small image.
        with Image.open(path) as source:
            img = source.convert("RGBA")
        target_h = max(1, int(img.height * width / img.width))
        if img.width > width:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            img.thumbnail((width, target_h), resampling)
        h = img.height
        # A transparent alpha mask gives every guide image the same softened
        # edge without creating a separate asset on disk.
        try:
            from PIL import ImageDraw
            mask = Image.new("L", (img.width, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, img.width - 1, h - 1), radius=min(16, h // 5), fill=255)
            img.putalpha(mask)
        except Exception:                           # noqa: BLE001
            pass
        rendered = ctk.CTkImage(light_image=img, dark_image=img,
                                size=(img.width, h))
        _IMAGE_CACHE[cache_key] = rendered
        return rendered
    except Exception:                              # noqa: BLE001
        return None


# --------------------------------------------------------------------------
# a single question card
# --------------------------------------------------------------------------

class Card(ctk.CTkFrame):
    """A setup card with a compact, cached guide image when one is useful."""

    def __init__(self, master, title: str, hint: str, image_key: str,
                 number: int | None = None) -> None:
        super().__init__(master, fg_color=BG_CARD, corner_radius=18,
                         border_width=1, border_color=BORDER)
        self.grid_columnconfigure(0, weight=1)
        self.preview = None
        title_row = ctk.CTkFrame(self, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="ew", padx=18, pady=(15, 1))
        title_row.grid_columnconfigure(1, weight=1)
        if number is not None:
            ctk.CTkLabel(title_row, text=f"{number:02d}", width=28, height=24,
                         corner_radius=8, fg_color="#16314c", text_color=ACCENT,
                         font=ctk.CTkFont(size=11, weight="bold")).grid(
                             row=0, column=0, padx=(0, 10))
        ctk.CTkLabel(title_row, text=title, font=ctk.CTkFont(size=16, weight="bold"),
                     text_color=TEXT, anchor="w", justify="left",
                     wraplength=470).grid(row=0, column=1, sticky="ew")
        if hint:
            ctk.CTkLabel(self, text=hint, font=ctk.CTkFont(size=12),
                         text_color=MUTED, anchor="w", justify="left",
                         wraplength=520).grid(row=1, column=0, sticky="ew",
                                              padx=18, pady=(1, 10))
        self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=2, column=0, sticky="ew", padx=18, pady=(0, 16))
        img = load_image(image_key, width=180)
        if img is not None:
            self.preview = ctk.CTkLabel(self, text="", image=img)
            self.preview.grid(row=0, column=1, rowspan=3, sticky="e",
                              padx=(0, 16), pady=16)

    def set_compact(self, compact: bool) -> None:
        """Hide non-essential screenshots in narrow windows, not controls."""
        if self.preview is None:
            return
        if compact:
            self.preview.grid_remove()
        else:
            self.preview.grid()


class NPCPositionSimulation(ctk.CTkFrame):
    """A finite, explanatory interaction-range animation.

    It intentionally draws only while Play is active. Labels live outside the
    canvas so resizing cannot make an arrow collide with explanatory copy.
    """

    def __init__(self, master) -> None:
        super().__init__(master, fg_color="#091727", corner_radius=14,
                         border_width=1, border_color="#285073")
        self._offset = 0.0
        self._progress = 0.0
        self._playing = False
        self._tick_job = None
        self._started_at = 0.0

        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=13, pady=(12, 5))
        ctk.CTkLabel(top, text="INTERACTION-RANGE PRE-SIMULATION",
                     text_color="#83d8ff", font=ctk.CTkFont(size=10, weight="bold")).pack(
                         side="left")
        ctk.CTkLabel(top, text="Illustration — not an in-game measurement",
                     text_color=MUTED, font=ctk.CTkFont(size=10)).pack(side="right")

        controls = ctk.CTkFrame(self, fg_color="transparent")
        controls.pack(fill="x", padx=13, pady=(0, 6))
        ctk.CTkLabel(controls, text="Starting offset", text_color="#c9dbea",
                     font=ctk.CTkFont(size=11, weight="bold")).pack(side="left")
        self.offset_label = ctk.CTkLabel(controls, text="0° · aligned lane",
                                         text_color=SUCCESS, width=118, anchor="w",
                                         font=ctk.CTkFont(size=11, weight="bold"))
        self.offset_label.pack(side="left", padx=(8, 6))
        self.offset_slider = ctk.CTkSlider(
            controls, from_=0, to=18, number_of_steps=18,
            progress_color=WARNING, button_color="#fef3c7", button_hover_color="#ffffff",
            command=self._set_offset)
        self.offset_slider.pack(side="left", fill="x", expand=True, padx=(0, 8))
        self.offset_slider.set(0)
        self.play_button = ctk.CTkButton(
            controls, text="Play", width=68, height=28, fg_color="#17563a",
            hover_color="#20714a", text_color="#ecfdf3", command=self._toggle_play,
            font=ctk.CTkFont(size=11, weight="bold"))
        self.play_button.pack(side="right")
        ctk.CTkButton(controls, text="Reset", width=64, height=28,
                      fg_color=BG_CARD_RAISED, hover_color="#203858", border_width=1,
                      border_color=BORDER, text_color=TEXT, command=self._reset,
                      font=ctk.CTkFont(size=11, weight="bold")).pack(side="right", padx=(0, 6))

        self.canvas = ctk.CTkCanvas(self, height=270, bg="#07111f",
                                    highlightthickness=0, bd=0)
        self.canvas.pack(fill="x", padx=13, pady=(1, 7))
        self.canvas.bind("<Configure>", lambda _event: self._draw_scene())

        legend = ctk.CTkFrame(self, fg_color="#0d2238", corner_radius=10)
        legend.pack(fill="x", padx=13, pady=(0, 6))
        for text, color in (
            ("1  BLUE · wait at the outer edge", "#60a5fa"),
            ("2  ORANGE · confirmed interaction", "#fb923c"),
            ("3  GREEN · game push to fishing position", SUCCESS),
        ):
            ctk.CTkLabel(legend, text=text, text_color=color,
                         font=ctk.CTkFont(size=10, weight="bold")).pack(
                             anchor="w", padx=11, pady=2)
        self.status = ctk.CTkLabel(
            self, text="Correct setup: the blue start point is aligned with the NPC centre.",
            text_color="#c9dbea", anchor="w", justify="left", wraplength=720,
            font=ctk.CTkFont(size=11))
        self.status.pack(fill="x", padx=13, pady=(2, 10))
        self._draw_scene()

    def _set_offset(self, value) -> None:
        self._offset = float(value)
        label = ("0° · aligned lane" if self._offset < 0.5 else
                 f"{self._offset:.0f}° · off lane")
        self.offset_label.configure(text=label,
                                    text_color=SUCCESS if self._offset < 0.5 else WARNING)
        if not self._playing:
            self._progress = 0.0
        self._draw_scene()

    def _toggle_play(self) -> None:
        if self._playing:
            self._playing = False
            self.play_button.configure(text="Resume")
            return
        self._playing = True
        self.play_button.configure(text="Pause")
        self._started_at = time.perf_counter() - self._progress * 1.65
        self._animate()

    def _reset(self) -> None:
        self._playing = False
        self._progress = 0.0
        self.offset_slider.set(0)
        self._offset = 0.0
        self.offset_label.configure(text="0° · aligned lane", text_color=SUCCESS)
        self.play_button.configure(text="Play")
        self._draw_scene()

    @staticmethod
    def _point(cx: float, cy: float, radius: float, angle: float) -> tuple[float, float]:
        return cx + math.cos(angle) * radius, cy + math.sin(angle) * radius

    def _arrow(self, x0: float, y0: float, x1: float, y1: float, color: str) -> None:
        self.canvas.create_line(x0, y0, x1, y1, fill=color, width=3,
                                arrow="last", arrowshape=(10, 12, 4))

    def _animate(self) -> None:
        if not self._playing or not self.winfo_exists():
            return
        self._progress = min(1.0, (time.perf_counter() - self._started_at) / 1.65)
        self._draw_scene()
        if self._progress >= 1.0:
            self._playing = False
            self.play_button.configure(text="Replay")
            return
        self._tick_job = self.after(33, self._animate)

    def _draw_scene(self) -> None:
        if not hasattr(self, "canvas"):
            return
        canvas = self.canvas
        canvas.delete("all")
        width, height = max(1, canvas.winfo_width()), max(1, canvas.winfo_height())
        cx, cy = width * 0.50, height * 0.57
        radius = max(45, min(width * 0.27, height * 0.34))
        angle = -math.pi / 2 + math.radians(self._offset)
        end_angle = -math.pi / 2 + math.radians(self._offset * 1.7)
        start = self._point(cx, cy, radius, angle)
        interaction = self._point(cx, cy, radius * 0.43, angle)
        pushed = self._point(cx, cy, radius, end_angle)

        canvas.create_line(0, cy, width, cy, fill="#12233a", dash=(3, 5))
        canvas.create_line(cx, 0, cx, height, fill="#12233a", dash=(3, 5))
        canvas.create_oval(cx - radius, cy - radius, cx + radius, cy + radius,
                           outline="#d7e8f7", width=2)
        canvas.create_line(cx, cy - radius - 12, cx, cy + radius + 12,
                           fill="#355170", dash=(4, 5))
        canvas.create_text(cx, cy + 18, text="NPC centre", fill="#eff6ff",
                           font=("Segoe UI", 10, "bold"))
        canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#f8fafc", outline="")

        progress = self._progress
        if progress <= 0.42:
            t = progress / 0.42 if progress else 0.0
            current = (start[0] + (interaction[0] - start[0]) * t,
                       start[1] + (interaction[1] - start[1]) * t)
            self._arrow(start[0], start[1], current[0], current[1], "#fb923c")
            message = "Step 2: orange shows the confirmed interaction."
        else:
            t = (progress - 0.42) / 0.58
            current = (interaction[0] + (pushed[0] - interaction[0]) * t,
                       interaction[1] + (pushed[1] - interaction[1]) * t)
            self._arrow(interaction[0], interaction[1], current[0], current[1], SUCCESS)
            message = ("Step 3: the game push establishes the fishing position; "
                       "the macro does not add a fixed walk.")
        self._arrow(start[0], start[1], interaction[0], interaction[1], "#fb923c")
        canvas.create_oval(start[0] - 7, start[1] - 7, start[0] + 7, start[1] + 7,
                           fill="#60a5fa", outline="#dbeafe", width=2)
        canvas.create_oval(interaction[0] - 7, interaction[1] - 7,
                           interaction[0] + 7, interaction[1] + 7,
                           fill="#fb923c", outline="#ffedd5", width=2)
        canvas.create_oval(pushed[0] - 7, pushed[1] - 7, pushed[0] + 7, pushed[1] + 7,
                           fill=SUCCESS, outline="#d1fae5", width=2)
        if progress > 0:
            canvas.create_oval(current[0] - 4, current[1] - 4, current[0] + 4,
                               current[1] + 4, fill="#f8fafc", outline="")
        if self._offset < 0.5:
            message = ("Aligned lane: start at the visible edge, interact once, then let the "
                       "NPC push establish the fishing position.") if progress == 0 else message
        else:
            message += " An off-lane start can push to a different edge."
        self.status.configure(text=message)


class PreparationItem(ctk.CTkFrame):
    """Compact native-details style card with a short, finite height animation."""

    PRIORITIES = {
        "critical": ("CRITICAL · REQUIRED", "#fb7185", "#351b2a"),
        "high": ("HIGH IMPACT", "#fb923c", "#3b2418"),
        "important": ("IMPORTANT", "#fbbf24", "#382d17"),
        "helpful": ("HELPFUL", SUCCESS, "#143126"),
    }

    def __init__(self, master, number: int, title: str, priority: str,
                 description: str, steps: tuple[str, ...], warning: str,
                 success: str, image_key: str | None = None,
                 simulation: bool = False, expanded: bool = False) -> None:
        label, color, shade = self.PRIORITIES[priority]
        super().__init__(master, fg_color=BG_CARD, corner_radius=16,
                         border_width=1, border_color=BORDER)
        self._open = False
        self._anim_job = None
        self._arrow = None

        summary = ctk.CTkFrame(self, fg_color="transparent")
        summary.pack(fill="x", padx=10, pady=9)
        summary.grid_columnconfigure(2, weight=1)
        self.number = ctk.CTkLabel(summary, text=f"{number:02d}", width=29, height=27,
                                   corner_radius=8, fg_color=shade, text_color=color,
                                   font=ctk.CTkFont(size=11, weight="bold"))
        self.number.grid(row=0, column=0, padx=(0, 8))
        self.priority = ctk.CTkLabel(summary, text=label, text_color=color,
                                     font=ctk.CTkFont(size=10, weight="bold"))
        self.priority.grid(row=0, column=1, padx=(0, 10))
        self.title_button = ctk.CTkButton(
            summary, text=title, anchor="w", fg_color="transparent",
            hover_color="#1a304c", text_color=TEXT, height=30, corner_radius=8,
            font=ctk.CTkFont(size=14, weight="bold"), command=self.toggle)
        self.title_button.grid(row=0, column=2, sticky="ew")
        self.arrow_button = ctk.CTkButton(
            summary, text="⌄", width=30, height=30, fg_color=BG_CARD_RAISED,
            hover_color="#203858", text_color=color, corner_radius=8,
            font=ctk.CTkFont(size=16, weight="bold"), command=self.toggle)
        self.arrow_button.grid(row=0, column=3, padx=(8, 0))
        for widget in (self.number, self.priority):
            widget.bind("<Button-1>", lambda _event: self.toggle())

        self.body = ctk.CTkFrame(self, fg_color="#0b1728", corner_radius=12,
                                 border_width=1, border_color="#203858")
        ctk.CTkLabel(self.body, text=description, text_color="#c9dbea", anchor="w",
                     justify="left", wraplength=720,
                     font=ctk.CTkFont(size=12)).pack(fill="x", padx=15, pady=(14, 9))
        ctk.CTkLabel(self.body, text="DO THIS", text_color=color, anchor="w",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=15)
        for step in steps:
            ctk.CTkLabel(self.body, text=f"•  {step}", text_color="#dce8f5", anchor="w",
                         justify="left", wraplength=700,
                         font=ctk.CTkFont(size=11)).pack(fill="x", padx=15, pady=1)
        if warning:
            caution = ctk.CTkFrame(self.body, fg_color=shade, corner_radius=9)
            caution.pack(fill="x", padx=15, pady=(10, 7))
            ctk.CTkLabel(caution, text="AVOID", text_color=color, width=52, anchor="w",
                         font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(10, 4), pady=8)
            ctk.CTkLabel(caution, text=warning, text_color="#f6f2ef", anchor="w",
                         justify="left", wraplength=610,
                         font=ctk.CTkFont(size=11)).pack(fill="x", padx=(0, 10), pady=8)
        done = ctk.CTkFrame(self.body, fg_color="#102c25", corner_radius=9)
        done.pack(fill="x", padx=15, pady=(0, 12))
        ctk.CTkLabel(done, text="DONE LOOKS LIKE", text_color=SUCCESS, width=118, anchor="w",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkLabel(done, text=success, text_color="#d8f7e8", anchor="w",
                     justify="left", wraplength=570,
                     font=ctk.CTkFont(size=11)).pack(fill="x", padx=(0, 10), pady=8)
        image = load_image(image_key, width=520) if image_key else None
        if image is not None:
            ctk.CTkLabel(self.body, text="REFERENCE IMAGE", text_color="#83d8ff", anchor="w",
                         font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=15, pady=(2, 5))
            label_widget = ctk.CTkLabel(self.body, text="", image=image)
            label_widget.image = image
            label_widget.pack(anchor="w", padx=15, pady=(0, 11))
        if simulation:
            NPCPositionSimulation(self.body).pack(fill="x", padx=15, pady=(0, 14))

        if expanded:
            self.toggle(animate=False)

    def toggle(self, animate: bool = True) -> None:
        if self._anim_job is not None:
            try:
                self.after_cancel(self._anim_job)
            except Exception:                           # noqa: BLE001
                pass
            self._anim_job = None
        opening = not self._open
        self._open = opening
        self.arrow_button.configure(text="⌃" if opening else "⌄")
        self.configure(border_color="#365f88" if opening else BORDER)
        if not animate:
            if opening:
                self.body.pack(fill="x", padx=10, pady=(0, 10))
            else:
                self.body.pack_forget()
            return
        if opening:
            self.body.pack(fill="x", padx=10, pady=(0, 10))
            self.update_idletasks()
            target = max(1, self.body.winfo_reqheight())
            self.body.pack_propagate(False)
            self.body.configure(height=1)
            self._animate_height(1, target, 0, True)
        else:
            self.body.pack_propagate(False)
            self._animate_height(max(1, self.body.winfo_height()), 1, 0, False)

    def _animate_height(self, start: int, end: int, step: int, opening: bool) -> None:
        frames = 7
        fraction = min(1.0, (step + 1) / frames)
        # ease-out keeps the compact card responsive without a perpetual loop.
        eased = 1 - (1 - fraction) ** 2
        self.body.configure(height=max(1, int(start + (end - start) * eased)))
        if step + 1 < frames:
            self._anim_job = self.after(
                18, lambda: self._animate_height(start, end, step + 1, opening))
            return
        self._anim_job = None
        if opening:
            self.body.pack_propagate(True)
        else:
            self.body.pack_forget()
            self.body.pack_propagate(True)


# --------------------------------------------------------------------------
# calibration
# --------------------------------------------------------------------------

# Groups: (title, icon, color, entries)
# Entry:  (key, kind, config-holder, fields, label, description, image key)
#   kind "box" -> search area   : drag to move, drag the corner to resize
#   kind "dot" -> click point   : drag onto the button
# A box needs 4 fields (l, t, r, b); a dot needs one, holding an (x, y) pair.
CALIB_GROUPS = [
    ("Fishing", "🎣", "#22d3ee", [
        ("bar_search", "box", "detection",
         ("bar_search_left", "bar_search_top",
          "bar_search_right", "bar_search_bottom"), "Reel-bar search area",
         "Cover the reel minigame bar, plus a small margin all round — a "
         "finger's width above and below, a bit more at the sides. Crop it "
         "IN as far as you comfortably can. Everything the bot has ever "
         "mistaken for the reel bar sits outside it: your green health and "
         "energy bars on the left, the Power/Mastery strip on the right. "
         "Leaving them out is the single most useful thing on this screen.",
         "bar_search"),
        ("zone_track", "box", "detection",
         ("zone_track_left", "zone_track_top",
          "zone_track_right", "zone_track_bottom"), "Optional reel-track area",
         "Optional, and off until you tick the box below. Hug the INNER track "
         "tightly — the dark rail the zone slides along, including the thin "
         "progress strip under it — with just a hair of margin. When on, the "
         "bot finds and reads the bar from here instead of the Reel bar band, "
         "which pins the track width (the reel aims at fractions of that width, "
         "so a mis-measured width throws off the whole fight) and keeps the "
         "dock floor and water out of shot at night. The Reel bar band still "
         "does its main job; this is a focus assistant on top of it.",
         "zone_track"),
        ("bite", "box", "detection",
         ("bite_left", "bite_top", "bite_right", "bite_bottom"), "Bite-marker search area",
         "Cover where the pink “!” pops up over your head. Keep it snug around "
         "your character: if it reaches the player list in the top-right "
         "corner, that red row looks like a “!” and the bot bites at nothing.",
         "bite"),
        ("meter", "box", "detection",
         ("meter_left", "meter_top", "meter_right", "meter_bottom"),
         "Cast-charge search area",
         "Keep this one BIG — it is the opposite of the reel bar band. The "
         "thin green bar hangs next to your character rather than sitting at a "
         "fixed spot on screen, so it lands somewhere different on every cast: "
         "measured, it shifted by a tenth of the screen height between two "
         "casts a minute apart. A box drawn snugly around it catches some "
         "casts and misses others. Leave it as a wide central band — big "
         "enough to hold the meter wherever it turns up, but clear of the "
         "screen edges, because your health and energy bars are green too.",
         "meter"),
    ]),
    ("Catch popups", "💬", "#fb923c", [
        ("popup", "box", "dialog", ("left", "top", "right", "bottom"),
         "Catch-card fallback area",
         "Legacy centre-card area. Update 30's bottom card is found from its "
         "wide yellow header automatically; use 🎨 Catch-dialogue header if "
         "your screen renders that yellow unusually.", "popup"),
        ("learn", "box", "dialog",
         ("learn_left", "learn_top", "learn_right", "learn_bottom"),
         "Learn-button search area",
         "Cover the blue “Learn” button on the rare new-recipe note. That note "
         "never disappears on its own, so the bot has to spot it.", "learn"),
        ("learn_click", "dot", "dialog", ("learn_click",),
         "Learn-button click point",
         "Drop the dot in the middle of the “Learn” button.", "learn"),
    ]),
    ("Talking to the NPC", "🧑", "#60a5fa", [
        ("menu", "box", "shop",
         ("menu_left", "menu_top", "menu_right", "menu_bottom"),
         "NPC menu search area",
         "Cover the full NPC-menu envelope: all four root rows and the lower "
         "two-row bait page. Update 30's live detector searches inside this "
         "area, keeping terminal/HUD text from becoming fake menu rows.",
         "menu"),
        ("center", "dot", "shop", ("center",), "Interact click point",
         "Drop the dot on the “Interact” prompt — the middle of your screen "
         "while you are stood at the NPC.", "center"),
        ("menu_item1", "dot", "shop", ("menu_item1",), "Top menu-row fallback point",
         "Root: Shop. Shop page: Buy Bait. Bait page: Basic Bait. "
         "Reference picture slot: assets/gui/calib/menu_item1.png.", "menu_item1"),
        ("menu_item2", "dot", "shop", ("menu_item2",), "Second menu-row fallback point",
         "Root: Fishing Index. Shop page: Sell Fish. The role changes after "
         "a click; the live Update 30 stack is used first. Reference picture "
         "slot: assets/gui/calib/menu_item2.png.", "menu_item2"),
        ("menu_item3", "dot", "shop", ("menu_item3",), "Third menu-row fallback point",
         "Root page only: Job Stats. This is the missing third row in the "
         "four-button root menu. Reference picture slot: "
         "assets/gui/calib/menu_item3.png.", "menu_item3"),
        ("menu_last", "dot", "shop", ("menu_last",), "Bottom menu-row fallback point",
         "Root / Shop: Nevermind. Bait: Back. Confirmation: Nevermind. "
         "Reference picture slot: assets/gui/calib/menu_last.png.", "menu_last"),
    ]),
    ("Buying bait", "🪙", "#c084fc", [
        ("craft_btn", "box", "shop",
         ("craft_btn_left", "craft_btn_top", "craft_btn_right", "craft_btn_bottom"),
         "Craft-button search area",
         "Cover the yellow “Craft” button. The bot watches this to tell whether "
         "the craft window is open, so keep other windows from overlapping it.",
         "craft_btn"),
        ("craft_plus", "dot", "shop", ("craft_plus",), "Add-bait (+) click point",
         "Drop the dot on the blue “+” next to the bait count. One press = 10 "
         "more bait.", "craft_plus"),
        ("craft_button", "dot", "shop", ("craft_button",), "Craft confirmation click point",
         "Drop the dot in the middle of the yellow “Craft” button.", "craft_button"),
        ("craft_close", "dot", "shop", ("craft_close",), "Craft close recovery point",
         "Drop the dot on the red “Close” at the top-right of the craft window. "
         "Only used to back out if something goes wrong.", "craft_close"),
    ]),
]

# The canvas interaction is deliberately generic; these short, practical
# guides carry the item-specific knowledge a first-time user needs.  They are
# kept separate from CALIB_GROUPS so changing wording can never change a
# config holder, field name, coordinate rule, or detector.
CALIB_GUIDES = {
    "bar_search": {
        "purpose": "This is the region the macro scans to find the fishing reel.",
        "do": "Resize the box to include the whole reel bar and its thin progress strip, with a small margin around it.",
        "avoid": "Do not include the health bars, energy bars, player list, or the right-side mastery HUD.",
        "check": "Correct: only the reel UI is inside the box while a fish is being reeled.",
    },
    "zone_track": {
        "purpose": "Optional: this gives the macro a tighter view of the moving reel track.",
        "do": "Turn it on only if needed, then fit the box tightly around the dark rail and the thin progress strip below it.",
        "avoid": "Do not leave water, the dock, or large empty screen areas inside this optional box.",
        "check": "Correct: the whole moving track fits, but almost nothing outside the reel does.",
    },
    "bite": {
        "purpose": "This is where the macro looks for the pink bite marker above your character.",
        "do": "Move and resize the box so it covers the space where the pink exclamation mark appears.",
        "avoid": "Keep the top-right player list and any red HUD rows outside the box.",
        "check": "Correct: the marker appears inside the box during a bite; the player list does not.",
    },
    "meter": {
        "purpose": "This is the search area for the green cast-charge meter.",
        "do": "Use a broad central band that covers every place the small green meter can appear while charging a cast.",
        "avoid": "Do not make it tight around one frame, and avoid the green health or energy HUD at screen edges.",
        "check": "Correct: the entire cast meter fits even if it shifts slightly between casts.",
    },
    "popup": {
        "purpose": "This is a fallback search area for the catch card after a fish is landed.",
        "do": "Cover the middle of the catch card when it appears. Update 30 usually finds the yellow header automatically.",
        "avoid": "Do not include unrelated menus or a wide strip of the game world.",
        "check": "Correct: the catch card sits inside this box whenever the fallback is needed.",
    },
    "learn": {
        "purpose": "This region lets the macro recognise the rare recipe note's blue Learn button.",
        "do": "Resize the box around the entire blue Learn button, leaving a thin margin on every side.",
        "avoid": "Do not include the note text, other buttons, or the dark background around the note.",
        "check": "Correct: the blue button is fully visible and is the only button in the region.",
    },
    "learn_click": {
        "purpose": "This is the exact point the macro clicks to accept a recipe note.",
        "do": "Drag the point to the centre of the blue Learn button.",
        "avoid": "Do not place it on the button edge, its label, or the close area.",
        "check": "Correct: the point is comfortably inside the blue button, not touching any border.",
    },
    "menu": {
        "purpose": "This region contains the stacked Fisherman dialogue buttons.",
        "do": "Cover the complete button stack: all four root-menu rows and the shorter pages that replace it.",
        "avoid": "Do not include chat, the player list, or empty water beside the dialogue.",
        "check": "Correct: every visible dialogue button fits in the box, including the bottom Nevermind or Back row.",
    },
    "center": {
        "purpose": "This is the point used to interact with the Fisherman.",
        "do": "Stand at the NPC, show the Interact prompt, and drag the point to the middle of that prompt.",
        "avoid": "Do not use the middle of the screen unless it exactly matches the live Interact prompt.",
        "check": "Correct: the point lands on the prompt's active centre when you are in NPC range.",
    },
    "menu_item1": {
        "purpose": "Fallback click for the top visible dialogue row.",
        "do": "Place the point in the centre of the first row: Shop, Buy Bait, or Basic Bait depending on the page.",
        "avoid": "Do not place it on the row divider or assume it always has the same text.",
        "check": "Correct: it is centred in the top row with clear space above and below.",
    },
    "menu_item2": {
        "purpose": "Fallback click for the second visible dialogue row.",
        "do": "Place the point in the centre of row two: Fishing Index on root, Sell Fish on the Shop page.",
        "avoid": "Do not copy the top-row point; the rows are close but have different actions.",
        "check": "Correct: the point stays in the middle of the second row after the menu settles.",
    },
    "menu_item3": {
        "purpose": "Fallback click position for the root menu's third Job Stats row.",
        "do": "Open the four-row root menu and place the point in the centre of Job Stats.",
        "avoid": "Do not calibrate this against a two-row Shop or bait page, where this row does not exist.",
        "check": "Correct: the point is centred in the third row of the root menu only.",
    },
    "menu_last": {
        "purpose": "Fallback click for the bottom dialogue row: Nevermind or Back.",
        "do": "Place the point in the centre of the lowest visible button after the menu has finished falling into place.",
        "avoid": "Do not use a partly animated row or the Job Stats row above it.",
        "check": "Correct: the point is in Nevermind on root/Shop, and in Back on the bait page.",
    },
    "craft_btn": {
        "purpose": "This region confirms that the bait Craft window is open.",
        "do": "Resize the box around the yellow Craft button near the bottom of the bait window.",
        "avoid": "Do not cover the Craft window title, its red Close button, or other yellow game effects.",
        "check": "Correct: the yellow Craft button fills the region with a small, even margin.",
    },
    "craft_plus": {
        "purpose": "This point adds another ten bait to the craft quantity.",
        "do": "Drag the point to the centre of the blue plus button beside the bait count.",
        "avoid": "Do not place it on the minus button, bait icon, or the number display.",
        "check": "Correct: one manual click at this point would press only the blue plus button.",
    },
    "craft_button": {
        "purpose": "This is the final click that crafts the selected bait amount.",
        "do": "Drag the point to the centre of the large yellow Craft button.",
        "avoid": "Do not place it on the black bar around the button or on its bottom edge.",
        "check": "Correct: the point is safely inside the yellow button and away from its border.",
    },
    "craft_close": {
        "purpose": "This recovery point closes the Craft window if the normal path cannot continue.",
        "do": "Drag the point to the centre of the red Close button at the upper-right of the Craft window.",
        "avoid": "Do not use the window corner itself; it must be inside the red button.",
        "check": "Correct: the point would close the bait window with one click.",
    },
}

COLOR_GUIDES = {
    "track": ("Click the plain dark-grey reel rail", "Avoid the fish, zone, and green progress strip", "The selected sample matches only the empty reel background."),
    "chest": ("Click the gold tile directly behind a treasure chest", "Avoid the chest icon and the surrounding dark rail", "The swatch matches the amber tile, not the chest picture."),
    "progress": ("Click the bright green portion of the thin progress strip", "Avoid the larger green zone and any HUD bars", "The swatch matches the thin strip under the reel."),
    "zone": ("Click the green zone while the fish is inside it", "Avoid the fish tile and the dark track", "The swatch matches the green moving zone."),
    "zone_out": ("Click that same zone while it is grey", "Avoid grey scenery outside the reel", "The sample comes from the out-of-zone rail, not the dock."),
    "fish": ("Click the tile behind the fish while it is inside the zone", "Avoid the fish sprite itself", "The swatch comes from the square under the fish."),
    "fish_out": ("Click the fish tile while the fish is outside the zone", "Avoid the fish sprite and the grey zone", "The swatch comes from the changed tile colour."),
    "fish_tpl": ("Click the centre of the fish, then size the magenta crop around its tile", "Avoid leaving empty background inside the crop", "Green outline means the saved template is found on this screenshot."),
    "dialogue": ("Click a plain yellow part of the Update 30 catch-card header", "Avoid black letters and bright effects", "The swatch matches the wide yellow name strip."),
    "craft": ("Click a plain yellow part of the Craft button", "Avoid its text and the red Close button", "The swatch matches the yellow button used to confirm the craft window."),
}

# Some controls share a screenshot because the reference image explains their
# shared panel better than an empty optional slot. These images are visual aids
# only; detector input never reads them.
CALIB_IMAGE_ALIASES = {
    "dialogue": "popup",
}

GHOST = "#5b6169"


def _group_of(key: str):
    for title, icon, color, entries in CALIB_GROUPS:
        for e in entries:
            if e[0] == key:
                return title, icon, color, e
    return None, None, None, None


# Advanced color capture (Calibrate -> Advanced). Each key maps to the
# cfg.colors.cap_<key>_{on,bgr,tol} fields and to the mask below. Captures are
# unioned with the adaptive masks, so a sample can broaden detection without
# replacing the built-in mid-fight zone/fish handling.
COLOR_ITEMS = [
    ("track", "Reel bar background",
     "The dark grey track behind the fish. Click the empty dark part of the "
     "reel bar. This pins bar detection to your screen's exact grey — the thing "
     "that renders differently on different GPUs."),
    ("chest", "Treasure chest tile",
     "The gold / amber square a chest sits on. Click the tile right behind a "
     "chest. Chest colors were never tuned per machine, so this is the one "
     "that helps most."),
    ("progress", "Progress bar fill",
     "The bright green fill of the thin bar just under the reel bar. Click the "
     "lit green part while a fish is on."),
    ("zone", "Zone — green (in)",
     "The player zone when the fish is INSIDE it (green). Click the green part. "
     "Added to the adaptive detection, never a replacement."),
    ("zone_out", "Zone — grey (out)",
     "The SAME zone when the fish has escaped it (grey). Catch a frame where the "
     "fish is outside the zone and click the grey bar. Optional — the grey state "
     "is handled automatically too; capture it only if the auto handling misses "
     "your screen's grey."),
    ("fish", "Fish tile (in)",
     "The square the fish sprite sits on while it is INSIDE the zone. Click the "
     "tile background (not the fish itself). Added to the adaptive detection."),
    ("fish_out", "Fish tile (out)",
     "The SAME tile when the fish is OUTSIDE the zone — it renders a different "
     "color. Catch a frame with the fish outside and click its tile. This is "
     "the one that fixes 'the bot loses the fish when it runs'."),
    ("fish_tpl", "Fish image (template)",
     "Color-independent fallback: click the CENTRE of the fish, then size the "
     "box with the slider so it frames the fish tile. The bot then finds the "
     "fish by its PICTURE, so it works even when the tile color is unusual. "
     "Saves fish_template.png. Recapture it if you change your window size."),
    ("dialogue", "Catch-dialogue header (yellow)",
     "The wide yellow name strip on Update 30's bottom catch/dialogue card. "
     "Capture its plain yellow area (not the black text) if the macro misses "
     "a card after a catch. Reference picture slot: "
     "assets/gui/calib/dialogue.png."),
    ("craft", "Craft button (yellow)",
     "The yellow Craft button in the bait window. Capture its plain yellow "
     "area only if the macro says the Craft window never opened. Reference "
     "picture slot: assets/gui/calib/craft.png."),
]
COLOR_MASK = {
    "track": vision.track_mask,
    "chest": vision.chest_mask,
    "progress": vision.progress_mask,   # (img, c) — same call shape
    "zone": vision.zone_mask_tracking,  # tracker, not the strict locator
    "zone_out": vision.zone_mask_tracking,
    "fish": vision.fish_mask,
    "fish_out": vision.fish_mask,
    "dialogue": vision.dialogue_header_mask,
    "craft": vision.craft_button_mask,
}
COLOR_ACCENT = "#e879f9"


_BLANK_IMG = None
_CALIB_IMAGE_CACHE: dict[tuple[str, int, int], object] = {}


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
    """Return a small cached, rounded reference image for the guide panel."""
    if Image is None or not key:
        return None
    resolved = CALIB_IMAGE_ALIASES.get(key, key)
    path = ASSETS / "calib" / f"{resolved}.png"
    if not path.exists():
        return None
    # Include the file timestamp so a guide image the user replaces while the
    # Calibrate window is still open is refreshed on the next selection.
    # Keep just one rendered variant per path/width to avoid accumulating old
    # images during a calibration session.
    try:
        modified = path.stat().st_mtime_ns
    except OSError:
        return None
    cache_key = (str(path), width, modified)
    if cache_key in _CALIB_IMAGE_CACHE:
        return _CALIB_IMAGE_CACHE[cache_key]
    for stale_key in tuple(_CALIB_IMAGE_CACHE):
        if stale_key[:2] == cache_key[:2]:
            _CALIB_IMAGE_CACHE.pop(stale_key, None)
    try:
        with Image.open(path) as source:
            img = source.convert("RGBA")
        h = max(1, int(img.height * width / img.width))
        if img.width > width:
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            img.thumbnail((width, h), resampling)
        try:
            from PIL import ImageDraw
            mask = Image.new("L", img.size, 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                (0, 0, img.width - 1, img.height - 1),
                radius=min(16, img.height // 5), fill=255)
            img.putalpha(mask)
        except Exception:                           # noqa: BLE001
            pass
        rendered = ctk.CTkImage(light_image=img, dark_image=img,
                                size=(img.width, img.height))
        _CALIB_IMAGE_CACHE[cache_key] = rendered
        return rendered
    except Exception:                                  # noqa: BLE001
        return None


class Calibrator(ctk.CTkToplevel):
    """Line the bot's boxes and click points up with your own game UI.

    Positions are stored as fractions of the game window so they travel between
    machines — but the shipped numbers came from one particular layout. Pick an
    item on the left, drag it into place, save. Once per computer.
    """

    HANDLE = 14     # visible, bottom-right resize grip for a free box
    DOT_R = 10

    def __init__(self, master, cfg: Config) -> None:
        super().__init__(master)
        self.title("Calibrate")
        self.cfg = cfg
        self.geometry("1440x900")
        self.minsize(980, 640)
        self.configure(fg_color=APP_BG)
        self.master_app = master
        self.sel: str | None = None
        self.pick: str | None = None      # active Advanced-color element, if any
        self._tpl_center: tuple | None = None    # fish-template crop centre (game px)
        self._tpl_half = 34                       # crop half-size (game px)
        self.drag: tuple | None = None
        self.shapes: dict[str, dict] = {}
        self.scale = 1.0
        # Review state is deliberately session-only. It survives Re-shoot so a
        # user can refresh the game image without redoing their checklist, but
        # it never enters config.json and resets when this window is closed.
        self._reviewed: set[str] = set()
        self._color_reviewed: set[str] = set()
        self._guide_expanded = False
        self._guide_lines: list[int] = []
        self._interaction = "Choose a tool to begin."
        self._compact_calibration: bool | None = None
        # A practical first-run path. Optional regions and color capture stay
        # available but never block saving a working calibration.
        self._core_keys = (
            "bar_search", "bite", "meter", "popup", "learn", "learn_click",
            "menu", "center", "craft_btn", "craft_plus", "craft_button",
            "craft_close",
        )
        self._color_keys = tuple(key for key, _label, _desc in COLOR_ITEMS)
        # Set by shoot(). Until then there is nothing to draw against, and
        # every coordinate helper would raise on a missing attribute.
        self.win = None
        self.found = False
        self.bind("<Configure>", self._resize_calibration_layout, add=True)

        outer = ctk.CTkFrame(self, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=20, pady=18)
        outer.grid_columnconfigure(0, minsize=300)
        outer.grid_columnconfigure(1, weight=1)
        outer.grid_rowconfigure(1, weight=1)

        # ---- premium header ---------------------------------------------
        head = ctk.CTkFrame(outer, fg_color=BG_CARD, corner_radius=18,
                            border_width=1, border_color=BORDER)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 14))
        brand = ctk.CTkFrame(head, fg_color="transparent")
        brand.pack(side="left", padx=18, pady=12)
        ctk.CTkLabel(brand, text="✓", width=36, height=36, corner_radius=12,
                     fg_color=ACCENT, text_color="#07111f",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=11)
        ctk.CTkLabel(words, text="Calibration workspace", text_color=TEXT,
                     anchor="w", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(words, text="Place regions and click points on a fresh Roblox screenshot.",
                     text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(anchor="w")
        progress_area = ctk.CTkFrame(head, fg_color="transparent")
        progress_area.pack(side="right", padx=18, pady=13)
        self.progress_text = ctk.CTkLabel(progress_area, text="0 / 12 core tools reviewed",
                                          text_color="#b9d8eb", anchor="e",
                                          font=ctk.CTkFont(size=11, weight="bold"))
        self.progress_text.pack(anchor="e")
        self.progress = ctk.CTkProgressBar(progress_area, width=210, height=7,
                                           fg_color="#17243a", progress_color=SUCCESS)
        self.progress.pack(pady=(5, 0))
        self.progress.set(0)
        self.color_progress_text = ctk.CTkLabel(
            progress_area, text="0 / 10 colour samples reviewed",
            text_color="#d9b8ec", anchor="e",
            font=ctk.CTkFont(size=10, weight="bold"))
        self.color_progress_text.pack(anchor="e", pady=(8, 0))
        self.color_progress = ctk.CTkProgressBar(
            progress_area, width=210, height=5, fg_color="#17243a",
            progress_color=COLOR_ACCENT)
        self.color_progress.pack(pady=(4, 0))
        self.color_progress.set(0)

        # ---- left: grouped list -----------------------------------------
        left_shell = ctk.CTkFrame(outer, fg_color=BG_CARD, corner_radius=20,
                                  border_width=1, border_color=BORDER)
        left_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        nav_head = ctk.CTkFrame(left_shell, fg_color="transparent")
        nav_head.pack(fill="x", padx=16, pady=(16, 9))
        ctk.CTkLabel(nav_head, text="CALIBRATION MAP", text_color=ACCENT,
                     anchor="w", font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(nav_head, text="Choose one control at a time", text_color=TEXT,
                     anchor="w", font=ctk.CTkFont(size=15, weight="bold")).pack(anchor="w", pady=(2, 1))
        ctk.CTkLabel(nav_head, text="REGION = the macro looks here · CLICK = the macro presses here",
                     text_color=MUTED, anchor="w", justify="left",
                     font=ctk.CTkFont(size=10), wraplength=260).pack(anchor="w")
        left = ctk.CTkScrollableFrame(
            left_shell, fg_color=BG_SOFT, corner_radius=14,
            scrollbar_button_color="#345074", scrollbar_button_hover_color=ACCENT_HOVER)
        left.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        speed_scroll(left)
        self.buttons: dict[str, ctk.CTkButton] = {}
        self._button_labels: dict[str, str] = {}
        for title, icon, color, entries in CALIB_GROUPS:
            hdr = ctk.CTkFrame(left, fg_color="transparent")
            hdr.pack(fill="x", pady=(13, 4), padx=5)
            ctk.CTkLabel(hdr, text=title.upper(), anchor="w",
                         font=ctk.CTkFont(size=11, weight="bold"),
                         text_color=color).pack(side="left")
            for key, kind, _h, _f, label, _d, _i in entries:
                mark = "REGION" if kind == "box" else "CLICK"
                row = ctk.CTkButton(
                    left, text=f"{mark}   {label}", anchor="w", height=38,
                    corner_radius=10, fg_color="transparent", hover_color="#183353",
                    text_color="#d7e8f7", font=ctk.CTkFont(size=11),
                    command=lambda k=key: self.select(k))
                row.pack(fill="x", padx=4, pady=2)
                self.buttons[key] = row
                self._button_labels[key] = f"{mark}   {label}"

        # ---- Advanced: per-machine color capture -----------------------
        self.color_buttons: dict[str, ctk.CTkButton] = {}
        self._color_button_labels: dict[str, str] = {}
        hdr = ctk.CTkFrame(left, fg_color="transparent")
        hdr.pack(fill="x", pady=(16, 4), padx=5)
        ctk.CTkLabel(hdr, text="OPTIONAL COLOUR SAMPLES", anchor="w",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=COLOR_ACCENT).pack(side="left")
        for key, label, _desc in COLOR_ITEMS:
            row = ctk.CTkButton(
                left, text=f"SAMPLE   {label}", anchor="w", height=38,
                corner_radius=10, fg_color="transparent", hover_color="#3c2345",
                text_color="#e9d7f6", font=ctk.CTkFont(size=11),
                command=lambda k=key: self.select_color(k))
            row.pack(fill="x", padx=4, pady=2)
            self.color_buttons[key] = row
            self._color_button_labels[key] = f"SAMPLE   {label}"
        ctk.CTkLabel(left, text="Optional: use a colour sample only when a normal detector misses your screen.",
                     anchor="w", justify="left", text_color=MUTED,
                     font=ctk.CTkFont(size=10), wraplength=250).pack(fill="x", padx=9, pady=(7, 10))

        # ---- right: canvas + details ------------------------------------
        right = ctk.CTkFrame(outer, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew")
        right.grid_rowconfigure(1, weight=1)
        right.grid_columnconfigure(0, weight=1)

        bar = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=16,
                           border_width=1, border_color=BORDER)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, 9))
        context = ctk.CTkFrame(bar, fg_color="transparent")
        context.pack(side="left", fill="x", expand=True, padx=15, pady=9)
        ctk.CTkLabel(context, text="LIVE WORKSPACE", text_color=ACCENT, anchor="w",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
        self.hint = ctk.CTkLabel(context, text="1. Pick a control.  2. Align it on the screenshot.  3. Save when ready.",
                                 text_color=MUTED, anchor="w",
                                 font=ctk.CTkFont(size=12, weight="bold"))
        self.hint.pack(anchor="w", pady=(1, 0))
        # Calibrating against the wrong rectangle is silent and ruinous: every
        # number here is a fraction of the game window, so if we photographed
        # the whole desktop instead, the boxes you line up are stored against
        # the wrong size and the bot ends up searching empty screen.
        self.warn = ctk.CTkLabel(right, text="", text_color=DANGER,
                                 anchor="w", justify="left", wraplength=880,
                                 font=ctk.CTkFont(size=12, weight="bold"))
        ctk.CTkButton(bar, text="Save calibration", width=142, height=34,
                      fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#07111f",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.save).pack(side="right", padx=(7, 12), pady=9)
        ctk.CTkButton(bar, text="Re-shoot", width=90, height=34,
                      fg_color=BG_CARD_RAISED, hover_color="#203858",
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.shoot).pack(side="right", padx=(6, 0), pady=9)
        ctk.CTkButton(bar, text="Reset selected", width=112, height=34,
                      fg_color=BG_CARD_RAISED, hover_color="#4c2a38",
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self.reset_selected).pack(side="right", pady=9)

        canvas_shell = ctk.CTkFrame(right, fg_color="#050d1a", corner_radius=18,
                                    border_width=1, border_color=BORDER)
        canvas_shell.grid(row=1, column=0, sticky="nsew")
        canvas_shell.grid_rowconfigure(0, weight=1)
        canvas_shell.grid_columnconfigure(0, weight=1)
        self.canvas = ctk.CTkCanvas(canvas_shell, bg="#08111f", highlightthickness=0,
                                    bd=0, cursor="crosshair")
        self.canvas.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.canvas.bind("<Button-1>", self._down)
        self.canvas.bind("<B1-Motion>", self._move)
        self.canvas.bind("<ButtonRelease-1>", self._up)
        self.canvas.bind("<Motion>", self._hover)
        self.canvas.bind("<Leave>", lambda _e: self.canvas.configure(cursor="crosshair"))
        self.canvas.bind("<Configure>", lambda e: self._fit())

        det = ctk.CTkFrame(right, fg_color=BG_CARD, corner_radius=18,
                           border_width=1, border_color=BORDER)
        det.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        det.grid_columnconfigure(0, weight=1)
        self.d_eyebrow = ctk.CTkLabel(det, text="GUIDED CALIBRATION", anchor="w",
                                      text_color=ACCENT,
                                      font=ctk.CTkFont(size=10, weight="bold"))
        self.d_eyebrow.grid(row=0, column=0, sticky="ew", padx=16, pady=(13, 0))
        self.d_title = ctk.CTkLabel(det, text="Choose a control from the map", anchor="w",
                                    text_color=TEXT, font=ctk.CTkFont(size=17, weight="bold"))
        self.d_title.grid(row=1, column=0, sticky="ew", padx=16, pady=(2, 0))
        self.d_text = ctk.CTkLabel(
            det, text="Only the selected item can move. Its saved normalized position is shown below.",
            anchor="w", justify="left", wraplength=620, text_color=MUTED,
            font=ctk.CTkFont(size=12))
        self.d_text.grid(row=2, column=0, sticky="ew", padx=16, pady=(3, 8))
        self.d_do = self._guide_line(det, 3, "DO THIS", "Pick a tool, then use the screenshot as your workspace.", SUCCESS)
        self.d_avoid = self._guide_line(det, 4, "AVOID", "Do not move a control until the related game UI is visible.", WARNING)
        self.d_check = self._guide_line(det, 5, "CHECK", "Release after moving or resizing; the coordinate preview updates immediately.", ACCENT)
        self.coord_preview = ctk.CTkLabel(det, text="POSITION  ·  choose a control to inspect its saved values",
                                          text_color="#b9d8eb", anchor="w",
                                          font=ctk.CTkFont(family="Consolas", size=11))
        self.coord_preview.grid(row=6, column=0, sticky="ew", padx=16, pady=(6, 3))
        self.help_btn = ctk.CTkButton(det, text="Show detailed guide", width=142, height=28,
                                      fg_color=BG_CARD_RAISED, hover_color="#203858",
                                      border_width=1, border_color=BORDER, text_color=TEXT,
                                      font=ctk.CTkFont(size=11, weight="bold"),
                                      command=self._toggle_guide)
        self.help_btn.grid(row=7, column=0, sticky="w", padx=16, pady=(2, 12))
        self.image_shell = ctk.CTkFrame(det, fg_color="#0b1728", corner_radius=13,
                                         border_width=1, border_color="#285073")
        self.image_shell.grid(row=0, column=1, rowspan=8, sticky="nsew", padx=(8, 14), pady=14)
        ctk.CTkLabel(self.image_shell, text="REFERENCE EXAMPLE", text_color="#83d8ff",
                     font=ctk.CTkFont(size=9, weight="bold")).pack(anchor="w", padx=10, pady=(9, 3))
        self.d_img = ctk.CTkLabel(self.image_shell, text="Choose a tool\nto see an example.",
                                  justify="center", text_color=MUTED,
                                  font=ctk.CTkFont(size=11))
        self.d_img.pack(padx=10, pady=(0, 4))
        self.d_img_note = ctk.CTkLabel(self.image_shell, text="Match the highlighted game element, not the cursor.",
                                       text_color=MUTED, justify="left", wraplength=210,
                                       font=ctk.CTkFont(size=10))
        self.d_img_note.pack(anchor="w", padx=10, pady=(2, 9))
        self.guide_extra = ctk.CTkLabel(det, text="", anchor="w", justify="left",
                                        wraplength=740, text_color="#c6d8e8",
                                        font=ctk.CTkFont(size=11))
        self.guide_extra.grid(row=8, column=0, columnspan=2, sticky="ew", padx=16, pady=(0, 12))
        self.guide_extra.grid_remove()

        # Color-capture controls: shown only while an Advanced-color item is
        # picked. Grid-removed by default so the normal box/dot flow is unchanged.
        self.color_frame = ctk.CTkFrame(det, fg_color="transparent")
        self.color_frame.grid(row=9, column=0, columnspan=2, sticky="ew",
                               padx=16, pady=(0, 12))
        self.color_frame.grid_columnconfigure(1, weight=1)
        self.color_frame.grid_remove()
        self.swatch = ctk.CTkLabel(self.color_frame, text="", width=54,
                                   height=26, corner_radius=6, fg_color="#000000")
        self.swatch.grid(row=0, column=0, padx=(0, 10))
        self.swatch_txt = ctk.CTkLabel(self.color_frame, text="", anchor="w",
                                       text_color=MUTED)
        self.swatch_txt.grid(row=0, column=1, columnspan=2, sticky="w")
        ctk.CTkLabel(self.color_frame, text="Tolerance", anchor="w").grid(
            row=1, column=0, sticky="w", pady=(10, 0))
        self.tol_slider = ctk.CTkSlider(self.color_frame, from_=4, to=90,
                                        number_of_steps=86, command=self._on_tol)
        self.tol_slider.grid(row=1, column=1, sticky="ew", pady=(10, 0), padx=8)
        self.tol_val = ctk.CTkLabel(self.color_frame, text="", width=30)
        self.tol_val.grid(row=1, column=2, pady=(10, 0))
        ctk.CTkButton(self.color_frame, text="Reset to default", width=140,
                      fg_color="#3a3f45", hover_color="#4a5057",
                      command=self._reset_color).grid(row=2, column=1,
                                                       sticky="w", pady=(10, 0),
                                                       padx=8)

        # Zone-track opt-in: shown only while the "Zone track" box is selected.
        # A drawn-but-unchecked box stays inert (default off), matching the
        # color items' opt-in gate. Shares row 2 with color_frame — the two are
        # never shown at once (color = pick mode, this = a box selection).
        self.ztrack_frame = ctk.CTkFrame(det, fg_color="transparent")
        self.ztrack_frame.grid(row=9, column=0, columnspan=2, sticky="ew",
                               padx=16, pady=(0, 12))
        self.ztrack_frame.grid_remove()
        self.ztrack_var = ctk.BooleanVar(
            value=bool(self.cfg.detection.zone_track_on))
        ctk.CTkCheckBox(self.ztrack_frame, variable=self.ztrack_var,
                        command=self._on_ztrack_toggle,
                        text="Use this optional region to read the reel track. Turn it on only after the box tightly frames the rail and progress strip.").grid(row=0, column=0,
                                                          sticky="w")

        self._update_progress()
        self.after(40, self._resize_calibration_layout)
        self.after(250, self.shoot)

    def _on_ztrack_toggle(self) -> None:
        """Opt in/out of reading the bar from the Zone track box."""
        try:
            self.cfg.detection.zone_track_on = bool(self.ztrack_var.get())
            self._record_review("zone_track")
            self._set_interaction("Optional track region updated. Save when you are happy with it.")
        except Exception:                              # noqa: BLE001
            pass

    def _resize_calibration_layout(self, event=None) -> None:
        """Prioritize the editable screenshot when the window is compact."""
        if event is not None and event.widget is not self:
            return
        if not hasattr(self, "image_shell"):
            return
        compact = self.winfo_height() < 780 or self.winfo_width() < 1150
        if compact == self._compact_calibration:
            return
        self._compact_calibration = compact
        if compact:
            self.image_shell.grid_remove()
            self.d_text.grid_remove()
            self.d_avoid.grid_remove()
            self.d_check.grid_remove()
        else:
            self.image_shell.grid()
            self.d_text.grid()
            self.d_avoid.grid()
            self.d_check.grid()

    # -- guide, status and reset controls --------------------------------
    def _guide_line(self, master, row: int, heading: str, text: str, color: str):
        label = ctk.CTkLabel(master, text=f"{heading}  ·  {text}", anchor="w",
                             justify="left", wraplength=650, text_color="#c9dbea",
                             font=ctk.CTkFont(size=11))
        label.grid(row=row, column=0, sticky="ew", padx=16, pady=2)
        return label

    def _set_interaction(self, text: str, color: str = MUTED) -> None:
        self._interaction = text
        try:
            self.hint.configure(text=text, text_color=color)
        except Exception:                              # noqa: BLE001
            pass

    def _style_nav(self) -> None:
        """Keep selection and review status readable without changing actions."""
        for key, button in self.buttons.items():
            selected = key == self.sel
            reviewed = key in self._reviewed
            prefix = "✓  " if reviewed and not selected else ""
            button.configure(
                text=prefix + self._button_labels[key],
                fg_color="#173a59" if selected else ("#103529" if reviewed else "transparent"),
                hover_color="#203f62", text_color=TEXT if selected else "#d7e8f7")
        for key, button in self.color_buttons.items():
            selected = key == self.pick
            reviewed = key in self._color_reviewed
            prefix = "✓  " if reviewed and not selected else ""
            button.configure(
                text=prefix + self._color_button_labels[key],
                fg_color="#4a2454" if selected else ("#103529" if reviewed else "transparent"),
                hover_color="#5b3065" if selected else ("#174638" if reviewed else "#3c2345"),
                text_color="#f5eaff" if selected else "#e9d7f6")

    def _record_review(self, key: str | None = None) -> None:
        key = key or self.sel
        if key:
            if key in self._color_keys:
                self._color_reviewed.add(key)
            else:
                self._reviewed.add(key)
        self._update_progress()

    def _review_snapshot(self) -> tuple[set[str], set[str]]:
        """Return the checklist kept only for this open Calibrate window."""
        return self._reviewed.copy(), self._color_reviewed.copy()

    def _restore_review_snapshot(self, state: tuple[set[str], set[str]]) -> None:
        """Restore Re-shot review state without writing anything to Config."""
        self._reviewed, self._color_reviewed = state[0].copy(), state[1].copy()

    def _update_progress(self) -> None:
        if not hasattr(self, "progress"):
            return
        count = sum(key in self._reviewed for key in self._core_keys)
        total = len(self._core_keys)
        self.progress.set(count / total if total else 0)
        if count == total:
            text = "All core tools reviewed · ready to save"
            color = SUCCESS
        else:
            text = f"{count} / {total} core tools reviewed"
            color = "#b9d8eb"
        self.progress_text.configure(text=text, text_color=color)
        color_count = sum(key in self._color_reviewed for key in self._color_keys)
        color_total = len(self._color_keys)
        self.color_progress.set(color_count / color_total if color_total else 0)
        color_text = ("All optional colour samples reviewed"
                      if color_count == color_total else
                      f"{color_count} / {color_total} colour samples reviewed")
        self.color_progress_text.configure(
            text=color_text,
            text_color=SUCCESS if color_count == color_total else "#d9b8ec")
        self._style_nav()

    def _guide_for(self, key: str, kind: str, label: str, desc: str,
                   image_key: str, color: str, advanced: bool = False) -> None:
        """Populate the readable guide panel; no stored calibration is touched."""
        if advanced:
            do, avoid, check = COLOR_GUIDES.get(
                key, ("Click the exact colour the detector should recognise.",
                      "Avoid text, borders, and animated effects.",
                      "The swatch matches the intended game element."))
            purpose = f"Optional colour sample: {label}. It supplements the built-in detector for this computer."
            mode = "OPTIONAL COLOUR SAMPLE"
            extra = ("Click once on a plain, stable part of the target. The pink overlay shows the pixels that match. "
                     "Use Reset to return to the built-in colour. This does not move any click point or region.")
        else:
            guide = CALIB_GUIDES.get(key, {})
            purpose = guide.get("purpose", desc)
            do = guide.get("do", "Move the selected control onto the matching game element.")
            avoid = guide.get("avoid", "Do not include unrelated UI or game scenery.")
            check = guide.get("check", "Release once the control is centred and the preview values are correct.")
            mode = "DETECTION REGION" if kind == "box" else "CLICK POSITION"
            if kind == "box":
                extra = ("Move: drag anywhere inside the selected outline. Resize: drag the bright square grip in its bottom-right corner. "
                         "The light guide lines appear only while you drag. Releasing commits the exact displayed normalized values.")
            else:
                extra = ("Move: drag the large selected point onto the button centre. The ring is intentionally larger than the saved click point, "
                         "so it remains easy to grab without making the actual click less precise.")
        self.d_eyebrow.configure(text=mode, text_color=color)
        self.d_title.configure(text=label, text_color=TEXT)
        self.d_text.configure(text=purpose, text_color=MUTED)
        self.d_do.configure(text=f"DO THIS  ·  {do}")
        self.d_avoid.configure(text=f"AVOID  ·  {avoid}")
        self.d_check.configure(text=f"CHECK  ·  {check}")
        self.guide_extra.configure(
            text=f"WHAT THIS CONTROL DOES\n{purpose}\n\nDO THIS\n{do}\n\n"
                 f"AVOID\n{avoid}\n\nCHECK\n{check}\n\nHOW IT WORKS\n{extra}")
        self._guide_expanded = False
        self.guide_extra.grid_remove()
        self.help_btn.configure(text="Show detailed guide")
        img = _calib_image(image_key)
        if img is None:
            self.d_img.configure(image=_blank_image(), text="No reference image\nfor this control.")
            self.d_img_note.configure(text="Follow the Do this, Avoid, and Check instructions beside this panel.")
        else:
            try:
                self.d_img.configure(image=img, text="")
            except Exception:                         # noqa: BLE001
                pass
            self.d_img.image = img
            self.d_img_note.configure(text="Reference example: align to the named game element, not to the cursor shown in any image.")

    def _toggle_guide(self) -> None:
        self._guide_expanded = not self._guide_expanded
        if self._guide_expanded:
            self.guide_extra.grid()
            self.help_btn.configure(text="Hide detailed guide")
        else:
            self.guide_extra.grid_remove()
            self.help_btn.configure(text="Show detailed guide")

    def _update_coordinate_preview(self, key: str | None = None) -> None:
        if not hasattr(self, "coord_preview"):
            return
        key = key or self.sel
        if not key:
            return
        _title, _icon, _color, spec = _group_of(key)
        if not spec:
            return
        _k, kind, _holder, _fields, *_ = spec
        fracs = self._fracs(spec)
        if kind == "box":
            value = "SAVED REGION  ·  L {:.4f}   T {:.4f}   R {:.4f}   B {:.4f}".format(*fracs)
        else:
            value = "SAVED CLICK   ·  X {:.4f}   Y {:.4f}".format(*fracs)
        self.coord_preview.configure(text=value)

    def reset_selected(self) -> None:
        """Restore only the selected region/point to the shipped defaults."""
        if self.pick:
            self._reset_color()
            return
        if not self.sel:
            self._set_interaction("Choose a region or click point before using Reset selected.", WARNING)
            return
        _title, _icon, _color, spec = _group_of(self.sel)
        if not spec:
            return
        _key, _kind, holder, fields, *_ = spec
        target = getattr(self.cfg, holder)
        defaults = getattr(Config(), holder)
        for field in fields:
            setattr(target, field, getattr(defaults, field))
        if self.sel == "zone_track":
            self.cfg.detection.zone_track_on = False
            self.ztrack_var.set(False)
        self._reviewed.discard(self.sel)
        self._update_progress()
        self._update_coordinate_preview(self.sel)
        self._set_interaction("Selected control restored to the app default. It has not been saved yet.", WARNING)
        self.redraw()

    def _clear_guides(self) -> None:
        for line in self._guide_lines:
            try:
                self.canvas.delete(line)
            except Exception:                         # noqa: BLE001
                pass
        self._guide_lines.clear()

    def _show_guides(self, x0: float, y0: float, x1: float, y1: float) -> None:
        """Temporary alignment guides; they exist only during a user drag."""
        self._clear_guides()
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        width, height = self.canvas.winfo_width(), self.canvas.winfo_height()
        self._guide_lines = [
            self.canvas.create_line(cx, 0, cx, height, fill="#7dd3fc", dash=(3, 5)),
            self.canvas.create_line(0, cy, width, cy, fill="#7dd3fc", dash=(3, 5)),
        ]

    def _hover(self, ev) -> None:
        if not self.sel or self.sel not in self.shapes:
            self.canvas.configure(cursor="crosshair")
            return
        it = self.shapes[self.sel]
        for gid in it.get("grips", {}).values():
            x0, y0, x1, y1 = self.canvas.coords(gid)
            if x0 - 4 <= ev.x <= x1 + 4 and y0 - 4 <= ev.y <= y1 + 4:
                self.canvas.configure(cursor="sizing")
                return
        x0, y0, x1, y1 = self.canvas.coords(it["id"])
        pad = 16 if it["kind"] == "dot" else 8
        self.canvas.configure(cursor="fleur" if x0 - pad <= ev.x <= x1 + pad and y0 - pad <= ev.y <= y1 + pad else "crosshair")

    # -- screenshot -------------------------------------------------------
    def shoot(self) -> None:
        """Grab the game with our own windows hidden.

        Without this the tool photographs itself sitting on top of the very UI
        you are trying to line up against.
        """
        # Re-shoot changes only the screenshot. Keep the session checklist
        # intact, even if a redraw/selection handler runs while the windows
        # are temporarily hidden. This state is never written to Config.
        review_state = self._review_snapshot()
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
        self._restore_review_snapshot(review_state)
        self._update_progress()

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

    def _text(self, x: float, y: float, text: str, color: str) -> list:
        """Canvas text with a black halo — the game behind it is any color.

        Returns every id it made, each with its offset from the anchor, so the
        label can be dragged along with its shape instead of being redrawn.
        """
        font = ("Segoe UI", 10, "bold")
        ids = [(self.canvas.create_text(x + dx, y + dy, text=text,
                                        fill="#000000", anchor="w", font=font),
                dx, dy) for dx, dy in self._HALO]
        ids.append((self.canvas.create_text(x, y, text=text, fill=color,
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
        return tuple(getattr(obj, f) for f in fields)

    def redraw(self) -> None:
        self._clear_guides()
        self.canvas.delete("all")
        self.shapes.clear()
        # While an Advanced color is picked and captured, show the screenshot
        # with everything the bot would match tinted magenta — the live "what
        # the detector sees" preview. Otherwise the plain screenshot.
        base = getattr(self, "_tk_img", None)
        if self.pick and getattr(self.cfg.colors, f"cap_{self.pick}_on", False):
            prev = self._preview_tk()
            if prev is not None:
                base = prev
        if base is not None:
            self.canvas.create_image(0, 0, anchor="nw", image=base)
        if self.win is None:                       # no screenshot yet
            return

        for title, icon, color, entries in CALIB_GROUPS:
            for entry in entries:
                key, kind, _holder, fields, label = entry[:5]
                active = key == self.sel
                col = color if active else GHOST
                if kind == "box":
                    x0, y0, x1, y1 = self._box_px(*self._fracs(entry))
                    rid = self.canvas.create_rectangle(
                        x0, y0, x1, y1, outline=col, width=3 if active else 1,
                        dash=() if active else (3, 4))
                    grips: dict[str, int] = {}
                    tid = grip_label = None
                    if active:
                        # Only the selected item is labelled. Drawing all of
                        # them at once was unreadable clutter.
                        tid = self._text(x0 + 8, y0 + 13, f"{icon} {label}  ·  drag to move", col)
                        grips["corner"] = self.canvas.create_rectangle(
                            x1 - self.HANDLE, y1 - self.HANDLE, x1, y1,
                            outline="#ffffff", fill=col, width=2)
                        grip_label = self.canvas.create_text(
                            x1 - self.HANDLE / 2, y1 - self.HANDLE / 2,
                            text="↘", fill="#07111f", font=("Segoe UI", 9, "bold"))
                    self.shapes[key] = {"kind": "box", "id": rid, "label": tid,
                                        "grips": grips, "grip_label": grip_label,
                                        "entry": entry}
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

        if self.pick == "fish_tpl":
            self._draw_template_overlay()

    def _draw_template_overlay(self) -> None:
        """Magenta = the crop box you're capturing; green = where the saved
        template matches on this screenshot (the live 'does it find the fish')."""
        if self._tpl_center is not None and self.scale > 0:
            cx, cy = self._tpl_center
            hx = self._tpl_half
            x0 = (cx - hx) * self.scale
            y0 = (cy - hx) * self.scale
            x1 = (cx + hx) * self.scale
            y1 = (cy + hx) * self.scale
            self.canvas.create_rectangle(x0, y0, x1, y1,
                                         outline=COLOR_ACCENT, width=2)
        # where does the saved template match right now?
        if self.cfg.detection.fish_tpl_on and np is not None and self._shot:
            try:
                from bloxfish.config import CONFIG_PATH
                import cv2
                p = CONFIG_PATH.parent / "fish_template.png"
                tpl = cv2.imread(str(p)) if p.exists() else None
                if tpl is not None:
                    shot_bgr = np.asarray(self._shot)[:, :, ::-1]
                    m = vision.find_fish_template(shot_bgr, tpl,
                                                  self.cfg.detection.fish_tpl_thr)
                    if m is not None:
                        res = cv2.matchTemplate(shot_bgr, tpl,
                                                cv2.TM_CCOEFF_NORMED)
                        _mn, _mx, _ml, ml = cv2.minMaxLoc(res)
                        gx0, gy0 = ml[0] * self.scale, ml[1] * self.scale
                        gx1 = (ml[0] + tpl.shape[1]) * self.scale
                        gy1 = (ml[1] + tpl.shape[0]) * self.scale
                        self.canvas.create_rectangle(gx0, gy0, gx1, gy1,
                                                     outline="#22c55e", width=3)
            except Exception:                          # noqa: BLE001
                pass

    def _save_template(self) -> None:
        """Crop the screenshot around the picked centre and write it as the
        fish template next to config.json."""
        if np is None or self._shot is None or self._tpl_center is None:
            return
        try:
            import cv2
            from bloxfish.config import CONFIG_PATH
            arr = np.asarray(self._shot)[:, :, ::-1]     # BGR
            h, w = arr.shape[:2]
            cx, cy = self._tpl_center
            hx = self._tpl_half
            x0, y0 = max(0, cx - hx), max(0, cy - hx)
            x1, y1 = min(w, cx + hx), min(h, cy + hx)
            crop = arr[y0:y1, x0:x1]
            if crop.size == 0:
                return
            cv2.imwrite(str(CONFIG_PATH.parent / "fish_template.png"), crop)
            self.cfg.detection.fish_tpl_on = True
        except Exception:                              # noqa: BLE001
            pass

    # -- selection --------------------------------------------------------
    def select(self, key: str) -> None:
        """Make `key` the editable item. Must never raise: if this dies, the
        list stops responding and the tool looks frozen."""
        self.sel = key
        self.pick = None                       # leave color-pick mode
        self.color_frame.grid_remove()
        if key == "zone_track":
            self.ztrack_var.set(bool(self.cfg.detection.zone_track_on))
            self.ztrack_frame.grid()
        else:
            self.ztrack_frame.grid_remove()
        title, icon, color, spec = _group_of(key)
        if spec:
            _k, kind, _h, _f, label, desc, img_key = spec
            self._guide_for(key, kind, label, desc, img_key, color)
            self._update_coordinate_preview(key)
            self._set_interaction(
                "Selected a detection region — drag inside it to move, or drag the bright ↘ grip to resize."
                if kind == "box" else
                "Selected a click point — drag the large ring to the centre of the matching button.",
                color)
        self._style_nav()
        self.redraw()

    # -- Advanced: per-machine color capture ------------------------------
    def select_color(self, key: str) -> None:
        """Enter eyedropper mode for the color element `key`."""
        self.sel = None                        # turn off box/dot editing
        self.pick = key
        self.ztrack_frame.grid_remove()        # not a box selection
        label = next((l for k, l, _ in COLOR_ITEMS if k == key), key)
        desc = next((d for k, _, d in COLOR_ITEMS if k == key), "")
        self._guide_for(key, "color", label, desc, key, COLOR_ACCENT, advanced=True)
        if key == "fish_tpl":
            self._set_interaction(
                "Click the fish centre, then use the slider to frame its tile. Green outline means the template is found.",
                COLOR_ACCENT)
        else:
            self._set_interaction(
                "Click a plain part of the target. The magenta overlay previews the detector's colour match.",
                COLOR_ACCENT)
        self._style_nav()
        self.color_frame.grid()
        self._refresh_color_ui()
        self.redraw()

    def _sample_color(self, ev) -> None:
        """Read the color under the click and store it for the picked element."""
        if np is None or self._shot is None or not self.pick or self.scale <= 0:
            return
        arr = np.asarray(self._shot)               # RGB, H x W x 3
        h, w = arr.shape[:2]
        gx = int(ev.x / self.scale)
        gy = int(ev.y / self.scale)
        if self.pick == "fish_tpl":                # centre for the template crop
            self._tpl_center = (max(0, min(w - 1, gx)), max(0, min(h - 1, gy)))
            self._save_template()
            self._refresh_color_ui()
            self._record_review(self.pick)
            self._set_interaction("Fish template captured. Adjust the slider only if the magenta crop misses part of the fish.", SUCCESS)
            self.redraw()
            return
        gx = max(2, min(w - 3, gx))
        gy = max(2, min(h - 3, gy))
        patch = arr[gy - 2:gy + 3, gx - 2:gx + 3].reshape(-1, 3)
        rgb = np.median(patch, axis=0)             # median rejects an edge pixel
        bgr = (int(rgb[2]), int(rgb[1]), int(rgb[0]))    # detectors work in BGR
        setattr(self.cfg.colors, f"cap_{self.pick}_bgr", bgr)
        setattr(self.cfg.colors, f"cap_{self.pick}_on", True)
        self._refresh_color_ui()
        self._record_review(self.pick)
        self._set_interaction("Colour sample captured. Magenta shows the pixels the detector now sees.", SUCCESS)
        self.redraw()

    def _on_tol(self, v) -> None:
        if not self.pick:
            return
        if self.pick == "fish_tpl":                # slider sizes the crop box
            self._tpl_half = max(8, int(float(v)))
            self.tol_val.configure(text=str(self._tpl_half))
            if self._tpl_center is not None:
                self._save_template()
            self.redraw()
            return
        setattr(self.cfg.colors, f"cap_{self.pick}_tol", int(float(v)))
        self.tol_val.configure(text=str(int(float(v))))
        self.redraw()

    def _reset_color(self) -> None:
        if not self.pick:
            return
        if self.pick == "fish_tpl":
            self.cfg.detection.fish_tpl_on = False
            self._tpl_center = None
            self._color_reviewed.discard(self.pick)
            self._refresh_color_ui()
            self.hint.configure(text="Template off — the bot uses color only. "
                                     "Click the fish to capture again.")
            self._update_progress()
            self.redraw()
            return
        setattr(self.cfg.colors, f"cap_{self.pick}_on", False)
        self._color_reviewed.discard(self.pick)
        self._refresh_color_ui()
        self.hint.configure(text="Reset — back to the built-in color. Click the "
                                 "element to capture your own again.")
        self._update_progress()
        self.redraw()

    def _refresh_color_ui(self) -> None:
        """Sync the swatch, tolerance slider and caption to the stored values."""
        if not self.pick:
            return
        if self.pick == "fish_tpl":
            on = self.cfg.detection.fish_tpl_on
            self.swatch.configure(fg_color=COLOR_ACCENT if on else "#000000")
            self.swatch_txt.configure(
                text=("template saved — active (green box below = where it "
                      "matches now)") if on else
                     "no template yet — click the centre of the fish",
                text_color="#d7dade" if on else MUTED)
            self.tol_slider.set(self._tpl_half)
            self.tol_val.configure(text=str(self._tpl_half))
            return
        c = self.cfg.colors
        on = getattr(c, f"cap_{self.pick}_on", False)
        bgr = getattr(c, f"cap_{self.pick}_bgr", (0, 0, 0))
        tol = getattr(c, f"cap_{self.pick}_tol", 16)
        hexc = "#%02x%02x%02x" % (int(bgr[2]), int(bgr[1]), int(bgr[0]))  # ->RGB
        if on:
            self.swatch.configure(fg_color=hexc)
            self.swatch_txt.configure(
                text=f"sample {hexc} is active for this computer", text_color="#d7dade")
        else:
            self.swatch.configure(fg_color="#000000")
            self.swatch_txt.configure(
                text="using the built-in color — click the element to capture "
                     "your own", text_color=MUTED)
        self.tol_slider.set(tol)
        self.tol_val.configure(text=str(int(tol)))

    def _preview_tk(self):
        """Screenshot with the picked element's matched pixels tinted magenta."""
        if np is None or self._shot is None or self.pick not in COLOR_MASK:
            return None
        try:
            rgb = np.asarray(self._shot).copy()          # RGB
            mask = COLOR_MASK[self.pick](rgb[:, :, ::-1], self.cfg.colors)
            tint = np.array([236, 72, 249], np.uint8)    # COLOR_ACCENT in RGB
            rgb[mask] = (rgb[mask] // 2 + tint // 2)      # blend so shape shows
            prev = Image.fromarray(rgb)
            size = (max(1, int(prev.width * self.scale)),
                    max(1, int(prev.height * self.scale)))
            self._prev_ctk = ctk.CTkImage(light_image=prev, dark_image=prev,
                                          size=size)
            return self._prev_ctk._get_scaled_light_photo_image(size)
        except Exception:                              # noqa: BLE001
            return None

    # -- dragging (selected item only) -------------------------------------
    def _down(self, ev) -> None:
        if self.pick:                     # Advanced-color eyedropper mode
            self._sample_color(ev)
            return
        if not self.sel or self.sel not in self.shapes:
            return
        it = self.shapes[self.sel]
        for name, gid in it.get("grips", {}).items():
            gx0, gy0, gx1, gy1 = self.canvas.coords(gid)
            if gx0 - 4 <= ev.x <= gx1 + 4 and gy0 - 4 <= ev.y <= gy1 + 4:
                self.drag = (name, ev.x, ev.y)
                self._set_interaction("Resizing selected region — release to keep the displayed values.", ACCENT)
                return
        x0, y0, x1, y1 = self.canvas.coords(it["id"])
        # Dots are small targets, so allow a wide grab radius around them.
        pad = 16 if it["kind"] == "dot" else 8
        if x0 - pad <= ev.x <= x1 + pad and y0 - pad <= ev.y <= y1 + pad:
            self.drag = ("move", ev.x, ev.y)
            self._set_interaction(
                "Moving selected region — release to keep the displayed values."
                if it["kind"] == "box" else
                "Moving click point — place the centre inside the matching button, then release.", ACCENT)

    def _up(self, _ev=None) -> None:
        # Commit on release as well: a click that nudges by a pixel never fires
        # <B1-Motion>, and the edit would otherwise be dropped.
        if self.drag and self.sel:
            self._commit(self.sel)
            self._resync(self.sel)
            self._record_review(self.sel)
            self._set_interaction("Position updated. The saved normalized values below are now the source of truth.", SUCCESS)
        self.drag = None
        self._clear_guides()
        try:
            self.canvas.configure(cursor="crosshair")
        except Exception:                              # noqa: BLE001
            pass

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
        self.canvas.coords(it["id"], x0, y0, x1, y1)
        self.drag = (mode, ev.x, ev.y)
        self._commit(self.sel)
        self._resync(self.sel)
        x0, y0, x1, y1 = self.canvas.coords(it["id"])
        self._show_guides(x0, y0, x1, y1)

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
        self._update_coordinate_preview(key)

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
            corner = it.get("grips", {}).get("corner")
            if corner is not None:
                self.canvas.coords(corner, x1 - self.HANDLE,
                                   y1 - self.HANDLE, x1, y1)
            if it.get("grip_label") is not None:
                self.canvas.coords(it["grip_label"], x1 - self.HANDLE / 2,
                                   y1 - self.HANDLE / 2)
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
        count = sum(key in self._reviewed for key in self._core_keys)
        if count == len(self._core_keys):
            self._set_interaction("Calibration saved — all core tools were reviewed in this session.", SUCCESS)
        else:
            self._set_interaction(
                f"Calibration saved — {count} of {len(self._core_keys)} core tools reviewed here. You can return anytime.",
                SUCCESS)
        self.redraw()


# --------------------------------------------------------------------------
# Advanced cooldowns editor
# --------------------------------------------------------------------------

class CooldownEditor(ctk.CTkToplevel):
    """Edit every delay / reaction time, grouped, with each value's default
    beside it. Save writes ONLY the values changed from default (config.py keeps
    the rest tracking the code), so this can't freeze the app on old numbers."""

    COL_ACCENT = "#f0b23a"          # amber, distinct from the pink color panel

    def __init__(self, master, cfg) -> None:
        super().__init__(master)
        self.cfg = cfg
        self.title("Advanced cooldowns")
        self.geometry("640x720")
        self.minsize(560, 480)
        self.configure(fg_color="#141618")
        self.rows: dict = {}        # (section, field) -> (StringVar, default, is_int, dot)

        head = ctk.CTkFrame(self, fg_color="transparent")
        head.pack(fill="x", padx=18, pady=(16, 4))
        ctk.CTkLabel(head, text="Advanced cooldowns",
                     font=ctk.CTkFont(size=22, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(head, text="Every delay, timeout and reaction rate the bot "
                     "uses, in seconds unless noted. Change a value and press "
                     "Save — only what you change is written; everything else "
                     "keeps following the app's own updates. ↺ resets one row.",
                     font=ctk.CTkFont(size=12), text_color=MUTED, anchor="w",
                     justify="left", wraplength=580).pack(anchor="w", pady=(2, 0))

        body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=12, pady=8)
        speed_scroll(body)
        body.grid_columnconfigure(0, weight=1)
        r = 0
        # column header
        hdr = ctk.CTkFrame(body, fg_color="transparent")
        hdr.grid(row=r, column=0, sticky="ew", pady=(0, 2)); r += 1
        hdr.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(hdr, text="COOLDOWN", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, anchor="w").grid(row=0, column=0, sticky="w", padx=(10, 0))
        ctk.CTkLabel(hdr, text="VALUE", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, width=90).grid(row=0, column=1, padx=6)
        ctk.CTkLabel(hdr, text="DEFAULT", font=ctk.CTkFont(size=11, weight="bold"),
                     text_color=MUTED, width=96).grid(row=0, column=2, padx=6)
        ctk.CTkLabel(hdr, text="", width=34).grid(row=0, column=3)

        defaults = type(cfg)()
        for group, items in COOLDOWNS:
            sec = ctk.CTkLabel(body, text=group.upper(),
                               font=ctk.CTkFont(size=12, weight="bold"),
                               text_color=self.COL_ACCENT, anchor="w")
            sec.grid(row=r, column=0, sticky="ew", padx=10, pady=(12, 2)); r += 1
            for i, (section, field, label, unit) in enumerate(items):
                cur = getattr(getattr(cfg, section), field)
                dflt = getattr(getattr(defaults, section), field)
                is_int = isinstance(dflt, int) and not isinstance(dflt, bool)
                row = ctk.CTkFrame(body, fg_color="#1d1f22" if i % 2 else "#191b1e",
                                   corner_radius=8)
                row.grid(row=r, column=0, sticky="ew", pady=1); r += 1
                row.grid_columnconfigure(0, weight=1)
                dot = ctk.CTkLabel(row, text="", width=10, text_color=self.COL_ACCENT,
                                   font=ctk.CTkFont(size=15))
                dot.grid(row=0, column=0, sticky="w", padx=(6, 0))
                name = f"{label}" + (f"  ({unit})" if unit not in ("s", "×") else "")
                ctk.CTkLabel(row, text=name, anchor="w", justify="left",
                             font=ctk.CTkFont(size=13), wraplength=300).grid(
                    row=0, column=0, sticky="w", padx=(22, 4), pady=6)
                var = ctk.StringVar(value=self._fmt(cur, is_int))
                ent = ctk.CTkEntry(row, textvariable=var, width=84, justify="center")
                ent.grid(row=0, column=1, padx=6, pady=6)
                var.trace_add("write", lambda *a, k=(section, field): self._mark(k))
                ctk.CTkLabel(row, text=self._fmt(dflt, is_int) + (f" {unit}" if unit == "s" else ""),
                             width=96, text_color=MUTED,
                             font=ctk.CTkFont(size=12)).grid(row=0, column=2, padx=6)
                ctk.CTkButton(row, text="↺", width=30, height=26,
                              fg_color="#33383e", hover_color="#434952",
                              command=lambda k=(section, field): self._reset_one(k)).grid(
                    row=0, column=3, padx=(0, 8))
                self.rows[(section, field)] = (var, dflt, is_int, dot)

        foot = ctk.CTkFrame(self, fg_color="transparent")
        foot.pack(fill="x", padx=18, pady=(4, 16))
        self.msg = ctk.CTkLabel(foot, text="", text_color=MUTED,
                                font=ctk.CTkFont(size=12))
        self.msg.pack(side="left")
        ctk.CTkButton(foot, text="Save", width=110, height=38, fg_color=ACCENT,
                      hover_color="#268a5f", font=ctk.CTkFont(size=14, weight="bold"),
                      command=self._save).pack(side="right")
        ctk.CTkButton(foot, text="Reset all", width=90, height=38,
                      fg_color="#33383e", hover_color="#434952",
                      command=self._reset_all).pack(side="right", padx=(0, 8))
        for k in self.rows:
            self._mark(k)
        self.after(60, self.lift)

    @staticmethod
    def _fmt(v, is_int: bool) -> str:
        return str(int(v)) if is_int else f"{float(v):g}"

    def _mark(self, key) -> None:
        """Show an accent dot next to a row whose value differs from default."""
        var, dflt, is_int, dot = self.rows[key]
        try:
            changed = self._parse(var.get(), is_int) != dflt
        except ValueError:
            changed = True                      # invalid: flag it too
        dot.configure(text="●" if changed else "")

    @staticmethod
    def _parse(text: str, is_int: bool):
        text = text.strip()
        if text == "":
            raise ValueError("empty")
        value = float(text)
        if not math.isfinite(value):
            raise ValueError("not finite")
        return int(round(value)) if is_int else value

    def _reset_one(self, key) -> None:
        var, dflt, is_int, _dot = self.rows[key]
        var.set(self._fmt(dflt, is_int))

    def _reset_all(self) -> None:
        for key in self.rows:
            self._reset_one(key)
        self.msg.configure(text="All reset to defaults — press Save to apply.",
                           text_color=MUTED)

    def _save(self) -> None:
        bad = []
        parsed = {}
        for key, (var, dflt, is_int, _dot) in self.rows.items():
            try:
                v = self._parse(var.get(), is_int)
                if v < 0:                       # every cooldown is a duration/rate
                    raise ValueError("negative")
                parsed[key] = v
            except ValueError:
                bad.append(key)
        if bad:
            first = bad[0]
            self.msg.configure(
                text=f"{first[1]}: enter a non-negative number.",
                text_color="#ef476f")
            return
        for (section, field), v in parsed.items():
            setattr(getattr(self.cfg, section), field, v)
        try:
            self.cfg.save()
        except Exception as exc:                       # noqa: BLE001
            self.msg.configure(text=f"Save failed: {exc}", text_color="#ef476f")
            return
        n = sum(1 for k, (var, dflt, is_int, _d) in self.rows.items()
                if parsed[k] != dflt)
        self.msg.configure(
            text=f"✔ Saved to config.json ({n} changed from default).",
            text_color=ACCENT)


# --------------------------------------------------------------------------
# main app
# --------------------------------------------------------------------------

class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Blox Fruits Auto-Fisher")
        self.geometry("1080x740")
        self.minsize(780, 560)
        self.configure(fg_color=APP_BG)
        self.cfg = Config.load()
        self.engine: FishingEngine | None = None
        self.worker: threading.Thread | None = None
        # The preparation guide can be opened before the form is applied.
        # In that case no temporary bait amount has been chosen yet.
        self.bait: int | None = None
        self._quit = False
        self._form_draft: dict[str, object] | None = None
        self._form_cards: list[Card] = []
        self._compact_form: bool | None = None
        self._hotkeys_bound = False
        self.bind("<Configure>", self._resize_setup_layout, add=True)
        self._build_form()
        # Eight inexpensive 100 ms steps give the first launch a finished
        # feeling without a permanently running animation or startup delay.
        self.after(0, self._play_startup)

    # -- page 1: the form -------------------------------------------------
    def _build_form(self) -> None:
        self._page = "form"
        for w in self.winfo_children():
            w.destroy()
        self._form_cards = []
        self._compact_form = None
        draft = self._form_draft or {}

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=22, pady=20)

        # A real top bar makes the product identity and the current release
        # clear without consuming the vertical space needed by the form.
        head = ctk.CTkFrame(shell, fg_color=BG_CARD, corner_radius=18,
                            border_width=1, border_color=BORDER)
        head.pack(fill="x", pady=(0, 16))
        brand = ctk.CTkFrame(head, fg_color="transparent")
        brand.pack(side="left", padx=18, pady=13)
        ctk.CTkLabel(brand, text="F", width=36, height=36, corner_radius=12,
                     fg_color=ACCENT, text_color="#07111f",
                     font=ctk.CTkFont(size=19, weight="bold")).pack(side="left")
        brand_words = ctk.CTkFrame(brand, fg_color="transparent")
        brand_words.pack(side="left", padx=11)
        ctk.CTkLabel(brand_words, text="Blox Fruits Auto-Fisher", anchor="w",
                     text_color=TEXT, font=ctk.CTkFont(size=18, weight="bold")).pack(
                         anchor="w")
        ctk.CTkLabel(brand_words, text=f"SETUP WORKSPACE  ·  v{VERSION}",
                     anchor="w", text_color=MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
        head_actions = ctk.CTkFrame(head, fg_color="transparent")
        head_actions.pack(side="right", padx=16, pady=12)
        self.setup_state = ctk.CTkLabel(head_actions, text="●  Ready to configure",
                                        corner_radius=10, fg_color="#12372f",
                                        text_color="#87efc3", height=32,
                                        font=ctk.CTkFont(size=12, weight="bold"))
        self.setup_state.pack(side="left", padx=(0, 10))
        ctk.CTkButton(head_actions, text="Calibrate controls", width=154, height=32,
                      fg_color=BG_CARD_RAISED, hover_color="#21385b",
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._calibrate).pack(side="right")

        content = ctk.CTkFrame(shell, fg_color="transparent")
        content.pack(fill="both", expand=True)
        content.grid_columnconfigure(0, weight=5, minsize=420)
        content.grid_columnconfigure(1, weight=2, minsize=248)
        content.grid_rowconfigure(0, weight=1)

        body = ctk.CTkScrollableFrame(
            content, fg_color=BG_SOFT, corner_radius=20, border_width=1,
            border_color=BORDER, scrollbar_button_color="#345074",
            scrollbar_button_hover_color=ACCENT_HOVER)
        body.grid(row=0, column=0, sticky="nsew", padx=(0, 14))
        speed_scroll(body)
        body.grid_columnconfigure(0, weight=1)
        row = 0

        intro = ctk.CTkFrame(body, fg_color="transparent")
        intro.grid(row=row, column=0, sticky="ew", padx=18, pady=(18, 8)); row += 1
        ctk.CTkLabel(intro, text="SESSION SETUP", anchor="w", text_color=ACCENT,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(intro, text="Build a reliable fishing run.", anchor="w",
                     text_color=TEXT, font=ctk.CTkFont(size=25, weight="bold")).pack(
                         anchor="w", pady=(3, 2))
        ctk.CTkLabel(intro, text="Start with the essentials. Your saved choices are\n"
                     "kept exactly as before; advanced controls stay available when you need them.",
                     anchor="w", justify="left", text_color=MUTED,
                     font=ctk.CTkFont(size=12), wraplength=580).pack(anchor="w")
        steps = ctk.CTkFrame(body, fg_color="transparent")
        steps.grid(row=row, column=0, sticky="ew", padx=18, pady=(0, 12)); row += 1
        for label, active in (("1  Route", True), ("2  Bait plan", False),
                              ("3  Fishing cycle", False)):
            ctk.CTkLabel(steps, text=label, height=28, corner_radius=9,
                         fg_color="#17334f" if active else BG_CARD,
                         text_color=ACCENT if active else MUTED,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(
                             side="left", padx=(0, 7))

        def section(title: str, hint: str) -> None:
            nonlocal row
            label = ctk.CTkFrame(body, fg_color="transparent")
            label.grid(row=row, column=0, sticky="ew", padx=18, pady=(11, 5))
            ctk.CTkLabel(label, text=title.upper(), text_color=ACCENT,
                         font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
            ctk.CTkLabel(label, text=hint, text_color=MUTED, anchor="w",
                         font=ctk.CTkFont(size=12)).pack(anchor="w", pady=(1, 0))
            row += 1

        def add(card: Card) -> None:
            nonlocal row
            card.grid(row=row, column=0, sticky="ew", padx=18, pady=6)
            self._form_cards.append(card)
            row += 1

        # 1. NPC
        section("Route", "Tell the macro whether this session uses the mapped NPC.")
        c = Card(body, "Which NPC will you buy from?",
                 "Only the Fisherman is mapped. Pick “Don't buy” to fish on the "
                 "bait you already have.", "npc", 1)
        self.v_npc = ctk.StringVar(
            value=str(draft.get(
                "npc", "Fisherman" if self.cfg.shop.npc.lower().startswith("f")
                else "Don't buy")))
        ctk.CTkSegmentedButton(c.body, values=["Fisherman", "Don't buy"],
                               variable=self.v_npc, fg_color=BG_CARD_RAISED,
                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                               unselected_color=BG_CARD_RAISED,
                               unselected_hover_color="#203858").pack(fill="x")
        add(c)

        # 2. bait per purchase
        section("Bait plan", "Set the purchase amount and, optionally, your live stock.")
        step = max(1, self.cfg.shop.craft_step)
        c = Card(body, "How much bait per purchase?",
                 f"Must be a multiple of {step}. Each extra {step} is one more "
                 f"“+” click in the craft window. Don't choose more than 90 "
                 f"unless you have Conserve Bait.", "bait_amount", 2)
        bait_values = [str(step * n) for n in range(1, 10)]  # 10..90; game cap is 99
        selected_bait = str(self.cfg.shop.bait_per_purchase)
        if selected_bait not in bait_values:
            selected_bait = bait_values[-1]
        self.v_amount = ctk.StringVar(value=str(draft.get("amount", selected_bait)))
        ctk.CTkOptionMenu(c.body, values=bait_values,
                          variable=self.v_amount, fg_color=BG_CARD_RAISED,
                          button_color="#2b496c", button_hover_color=ACCENT_HOVER,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color="#203858").pack(fill="x")
        add(c)

        # 3. rod slot
        section("Fishing cycle", "Match the hotbar and choose when the macro should sell.")
        c = Card(body, "Which hotbar slot is your fishing rod in?",
                 "The bot stows and re-draws the rod around each bait trip — "
                 "that is what clears the post-catch stuck state.", "rod_slot", 3)
        self.v_rod = ctk.StringVar(value=str(draft.get("rod", self.cfg.rod_slot)))
        ctk.CTkSegmentedButton(c.body,
                               values=[str(d) for d in range(1, 10)] + ["0"],
                               variable=self.v_rod, fg_color=BG_CARD_RAISED,
                               selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
                               unselected_color=BG_CARD_RAISED,
                               unselected_hover_color="#203858").pack(fill="x")
        add(c)

        # 4. auto-sell
        c = Card(body, "Sell your fish every how many catches?",
                 "Choose 1 to test selling after every catch. 0 turns auto-selling "
                 "off. The NPC never buys favourited fish or your heaviest.",
                 "sell_every", 4)
        self.v_sell = ctk.StringVar(value=str(draft.get("sell", self.cfg.sell.every)))
        ctk.CTkOptionMenu(c.body,
                          values=["0", "1", "25", "50", "100", "150", "200"],
                          variable=self.v_sell, fg_color=BG_CARD_RAISED,
                          button_color="#2b496c", button_hover_color=ACCENT_HOVER,
                          dropdown_fg_color=BG_CARD, dropdown_hover_color="#203858").pack(fill="x")
        add(c)

        # 5. current bait (optional)
        c = Card(body, "How much bait do you have right now?",
                 "Optional — leave blank and the bot simply never stops to buy.",
                 "bait_now", 5)
        self.v_bait = ctk.StringVar(value=str(draft.get("bait", "")))
        ctk.CTkEntry(c.body, textvariable=self.v_bait,
                     placeholder_text="e.g. 80  (blank = don't track)",
                     fg_color="#091426", border_color=BORDER,
                     text_color=TEXT, placeholder_text_color=MUTED).pack(fill="x")
        add(c)

        # 6. slower fish trick
        section("Reliability", "Choose the trade-off that matches your machine and game behavior.")
        c = Card(body, "Slower fish trick",
                 "Turn this on if you always end up with a glitched fish in "
                 "your hand.\n\nNormally the rod is flicked off and on the "
                 "instant a fish lands, which skips the catch card entirely. "
                 "This waits 0.5 s before the flick and 0.5 s between the two "
                 "presses - slower per fish, but it avoids the glitch.",
                 "slow_flick", 6)
        self.v_slow = ctk.BooleanVar(value=bool(draft.get(
            "slow", self.cfg.timing.slow_rod_flick)))
        ctk.CTkSwitch(c.body, text="  Use the slower, safer flick",
                      variable=self.v_slow, progress_color=SUCCESS,
                      button_color="#cbd5e1", button_hover_color="#ffffff",
                      text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        add(c)

        # 7. faster bite reaction
        c = Card(body, "Faster bite reaction time",
                 "Turn this on if your computer is fast enough to keep up "
                 "with the program.\n\nThe bot normally takes about 0.8-1.5 s "
                 "to react once a fish bites. This polls the screen far "
                 "harder and drops the reaction padding, bringing it under "
                 "0.2 s. It uses more CPU, so leave it off on a slow machine.",
                 "fast_bite", 7)
        self.v_fast = ctk.BooleanVar(value=bool(draft.get(
            "fast", self.cfg.timing.fast_bite)))
        ctk.CTkSwitch(c.body, text="  React as fast as possible",
                      variable=self.v_fast, progress_color=SUCCESS,
                      button_color="#cbd5e1", button_hover_color="#ffffff",
                      text_color=TEXT, font=ctk.CTkFont(size=13, weight="bold")).pack(anchor="w")
        add(c)

        # 8. advanced cooldowns
        c = Card(body, "Advanced cooldowns",
                 "Fine-tune every delay, timeout and reaction rate the bot uses "
                 "— casting, biting, reeling, the catch, and talking to the NPC. "
                 "Each shows its default; only what you change is saved, so the "
                 "rest keep following app updates. For power users; the defaults "
                 "are fine for most.", "cooldowns", 8)
        ctk.CTkButton(c.body, text="⚙  Open cooldown editor", height=36,
                      fg_color=BG_CARD_RAISED, hover_color="#203858",
                      border_width=1, border_color=BORDER, text_color=TEXT,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._cooldowns).pack(anchor="w")
        add(c)

        # The summary does not poll or animate. It must still scroll on a
        # short/minimized window now that it contains the Calibration and
        # Preparation actions beneath the live profile values.
        profile = ctk.CTkScrollableFrame(
            content, fg_color=BG_CARD, corner_radius=20, border_width=1,
            border_color=BORDER, scrollbar_button_color="#345074",
            scrollbar_button_hover_color=ACCENT_HOVER)
        profile.grid(row=0, column=1, sticky="nsew")
        speed_scroll(profile)
        ctk.CTkLabel(profile, text="RUN PROFILE", text_color=ACCENT,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(
                         anchor="w", padx=18, pady=(18, 3))
        ctk.CTkLabel(profile, text="At a glance", text_color=TEXT,
                     font=ctk.CTkFont(size=20, weight="bold")).pack(
                         anchor="w", padx=18)
        ctk.CTkLabel(profile, text="This updates as you set up the session.",
                     text_color=MUTED, font=ctk.CTkFont(size=12)).pack(
                         anchor="w", padx=18, pady=(2, 13))
        self.profile_npc = self._profile_row(profile, "NPC route")
        self.profile_bait = self._profile_row(profile, "Purchase plan")
        self.profile_cycle = self._profile_row(profile, "Fishing cycle")
        self.profile_reaction = self._profile_row(profile, "Reaction mode")
        calibrate_note = ctk.CTkFrame(profile, fg_color="#0d2238", corner_radius=14,
                                      border_width=1, border_color="#1e4c6c")
        calibrate_note.pack(fill="x", padx=15, pady=(18, 9))
        ctk.CTkLabel(calibrate_note, text="CALIBRATION", text_color="#83d8ff",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(
                         anchor="w", padx=13, pady=(12, 2))
        ctk.CTkLabel(calibrate_note, text="Tune screen regions and click targets\n"
                     "without leaving this setup flow.", text_color="#c5d7e9", justify="left",
                     font=ctk.CTkFont(size=12), wraplength=220).pack(
                         anchor="w", padx=13, pady=(0, 10))
        ctk.CTkButton(calibrate_note, text="Open calibration  →", height=32,
                      fg_color="#173a59", hover_color="#21557d", text_color=TEXT,
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._calibrate).pack(fill="x", padx=11, pady=(0, 11))

        prep_note = ctk.CTkFrame(profile, fg_color="#102319", corner_radius=14,
                                  border_width=1, border_color="#236244")
        prep_note.pack(fill="x", padx=15, pady=(0, 9))
        ctk.CTkLabel(prep_note, text="PREPARATION", text_color="#86efac",
                     font=ctk.CTkFont(size=10, weight="bold")).pack(
                         anchor="w", padx=13, pady=(11, 2))
        ctk.CTkLabel(prep_note, text="Review the ten before-you-start checks\n"
                     "without applying this form.", text_color="#c5e7d3", justify="left",
                     font=ctk.CTkFont(size=12), wraplength=220).pack(
                         anchor="w", padx=13, pady=(0, 9))
        ctk.CTkButton(prep_note, text="Open preparation guide  →", height=32,
                      fg_color="#17563a", hover_color="#20714a", text_color="#ecfdf3",
                      font=ctk.CTkFont(size=12, weight="bold"),
                      command=self._open_preparation).pack(
                          fill="x", padx=11, pady=(0, 11))

        performance_note = ctk.CTkFrame(profile, fg_color="transparent")
        performance_note.pack(fill="x", padx=18, pady=(5, 16))
        ctk.CTkLabel(performance_note, text="PERFORMANCE, BY DESIGN", text_color=SUCCESS,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(performance_note, text="Idle UI does not redraw continuously. Guide images cache once; "
                     "Fast response intentionally uses more CPU.", text_color=MUTED,
                     justify="left", font=ctk.CTkFont(size=11), wraplength=230).pack(
                         anchor="w", pady=(3, 0))

        for var in (self.v_npc, self.v_amount, self.v_rod, self.v_sell,
                    self.v_slow, self.v_fast):
            var.trace_add("write", lambda *_: self._refresh_profile())
        self._refresh_profile()
        self.after(40, self._resize_setup_layout)

        foot = ctk.CTkFrame(shell, fg_color="transparent")
        foot.pack(fill="x", pady=(15, 0))
        self.err = ctk.CTkLabel(foot, text="", text_color=DANGER,
                                font=ctk.CTkFont(size=12, weight="bold"))
        self.err.pack(side="left")
        ctk.CTkLabel(foot, text="Saves your choices, then opens the pre-flight check.",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(
                         side="right", padx=(0, 13))
        self.continue_btn = ctk.CTkButton(
            foot, text="Continue to pre-flight  →", width=202, height=42,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, text_color="#07111f",
            font=ctk.CTkFont(size=13, weight="bold"), command=self._apply_form)
        self.continue_btn.pack(side="right")

    @staticmethod
    def _profile_row(master, label: str):
        row = ctk.CTkFrame(master, fg_color="transparent")
        row.pack(fill="x", padx=18, pady=5)
        ctk.CTkLabel(row, text=label.upper(), text_color=MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w")
        value = ctk.CTkLabel(row, text="", text_color=TEXT, anchor="w",
                             font=ctk.CTkFont(size=14, weight="bold"),
                             wraplength=220, justify="left")
        value.pack(anchor="w", pady=(1, 1))
        ctk.CTkFrame(row, height=1, fg_color=BORDER).pack(fill="x", pady=(7, 0))
        return value

    def _refresh_profile(self) -> None:
        """Reflect the current form values without mutating the configuration."""
        if not hasattr(self, "profile_npc"):
            return
        self.profile_npc.configure(
            text="Fisherman route" if self.v_npc.get() == "Fisherman"
            else "Fish with current bait")
        self.profile_bait.configure(text=f"{self.v_amount.get()} bait per visit")
        sell = self.v_sell.get()
        self.profile_cycle.configure(
            text="Auto-sell off" if sell == "0" else f"Sell every {sell} catch"
            + ("" if sell == "1" else "es"))
        reaction = "Fast response (higher CPU)" if self.v_fast.get() else "Balanced response"
        if self.v_slow.get():
            reaction += " · safer flick"
        self.profile_reaction.configure(text=reaction)

    def _resize_setup_layout(self, event=None) -> None:
        """Keep controls legible on modest displays by hiding only previews."""
        if event is not None and event.widget is not self:
            return
        if getattr(self, "_page", None) != "form":
            return
        compact = self.winfo_width() < 960
        if compact == self._compact_form:
            return
        self._compact_form = compact
        for card in self._form_cards:
            card.set_compact(compact)

    def _play_startup(self) -> None:
        """A finite, 0.8 s welcome animation; it leaves no idle work behind."""
        if self._quit or not self.winfo_exists():
            return
        try:
            self.attributes("-alpha", 0.15)
        except Exception:                           # noqa: BLE001
            pass
        splash = ctk.CTkFrame(self, fg_color="#07111f", corner_radius=0)
        splash.place(relx=0, rely=0, relwidth=1, relheight=1)
        center = ctk.CTkFrame(splash, fg_color="transparent")
        center.place(relx=0.5, rely=0.5, anchor="center")
        ctk.CTkLabel(center, text="F", width=54, height=54, corner_radius=18,
                     fg_color=ACCENT, text_color="#07111f",
                     font=ctk.CTkFont(size=28, weight="bold")).pack(pady=(0, 12))
        ctk.CTkLabel(center, text="Blox Fruits Auto-Fisher", text_color=TEXT,
                     font=ctk.CTkFont(size=21, weight="bold")).pack()
        ctk.CTkLabel(center, text=f"v{VERSION}", text_color=MUTED,
                     font=ctk.CTkFont(size=12)).pack(pady=(3, 17))
        progress = ctk.CTkProgressBar(center, width=230, height=7,
                                      fg_color="#17243a", progress_color=ACCENT)
        progress.pack()
        status = ctk.CTkLabel(center, text="Preparing your workspace", text_color=MUTED,
                              font=ctk.CTkFont(size=11))
        status.pack(pady=(8, 0))
        phrases = ("Preparing your workspace", "Loading saved configuration",
                   "Setting up controls", "Ready")

        def advance(tick: int = 0) -> None:
            if self._quit or not splash.winfo_exists():
                return
            progress.set((tick + 1) / 8)
            status.configure(text=phrases[min(tick // 2, len(phrases) - 1)])
            try:
                self.attributes("-alpha", min(1.0, 0.2 + (tick + 1) * 0.1))
            except Exception:                       # noqa: BLE001
                pass
            if tick < 7:
                self.after(100, lambda: advance(tick + 1))
                return
            try:
                self.attributes("-alpha", 1.0)
            except Exception:                       # noqa: BLE001
                pass
            splash.destroy()

        advance()

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

    def _open_preparation(self) -> None:
        """Open the checklist without applying or saving the setup form."""
        if getattr(self, "_page", None) != "form":
            return
        self._form_draft = {
            "npc": self.v_npc.get(),
            "amount": self.v_amount.get(),
            "rod": self.v_rod.get(),
            "sell": self.v_sell.get(),
            "bait": self.v_bait.get(),
            "slow": bool(self.v_slow.get()),
            "fast": bool(self.v_fast.get()),
        }
        self._build_checklist()

    def _cooldowns(self) -> None:
        win = getattr(self, "_cdedit", None)
        if win is not None and win.winfo_exists():
            win.deiconify()
            win.lift()
            win.focus_force()
            return
        self._cdedit = CooldownEditor(self, self.cfg)

    def _apply_form(self) -> None:
        # A quick double-click on Continue fires this twice; the first call
        # tears the page down, so the second would poke destroyed widgets and
        # crash the window. Ignore anything after the first.
        if getattr(self, "_page", "form") != "form":
            return
        raw = self.v_bait.get().strip()
        if raw and not raw.isdigit():
            self.err.configure(text="Bait must be a whole number, or blank.")
            return

        cfg = self.cfg
        cfg.shop.npc = "fisherman" if self.v_npc.get() == "Fisherman" else "none"
        cfg.shop.bait_per_purchase = int(self.v_amount.get())
        cfg.rod_slot = self.v_rod.get()
        cfg.sell.every = int(self.v_sell.get())
        cfg.sell.enabled = cfg.sell.every > 0
        cfg.timing.slow_rod_flick = bool(self.v_slow.get())
        cfg.timing.fast_bite = bool(self.v_fast.get())
        self.bait = int(raw) if raw else None
        self._form_draft = None
        try:
            # These are listed in Config.user_fields(); not saving here meant
            # every answer reverted at the next launch despite the UI/docs
            # presenting them as settings.
            cfg.save()
        except Exception as exc:                       # noqa: BLE001
            self.err.configure(text=f"Could not save config.json: {exc}")
            return
        self._build_checklist()

    def _commit_preparation_draft(self) -> bool:
        """Apply answers saved while the guide was opened from Setup.

        The guide is deliberately reachable before the user presses Continue.
        Finishing from that route must not quietly run with older saved values.
        """
        draft = self._form_draft
        if not draft:
            return True
        raw_bait = str(draft.get("bait", "")).strip()
        if raw_bait and not raw_bait.isdigit():
            self._prep_error.configure(
                text="Return to setup: bait on hand must be a whole number or blank.",
                text_color=DANGER)
            return False
        cfg = self.cfg
        cfg.shop.npc = "fisherman" if draft.get("npc") == "Fisherman" else "none"
        cfg.shop.bait_per_purchase = int(str(draft.get("amount", 90)))
        cfg.rod_slot = str(draft.get("rod", "5"))
        cfg.sell.every = int(str(draft.get("sell", 0)))
        cfg.sell.enabled = cfg.sell.every > 0
        cfg.timing.slow_rod_flick = bool(draft.get("slow", False))
        cfg.timing.fast_bite = bool(draft.get("fast", False))
        self.bait = int(raw_bait) if raw_bait else None
        try:
            cfg.save()
        except Exception as exc:                       # noqa: BLE001
            self._prep_error.configure(text=f"Could not save config.json: {exc}",
                                       text_color=DANGER)
            return False
        self._form_draft = None
        return True

    def _finish_preparation(self) -> None:
        """Leave the guide only after its pending Setup answers are committed."""
        if getattr(self, "_page", None) != "checklist":
            return
        if self._commit_preparation_draft():
            self._build_runner()

    # -- page 2: the checklist -------------------------------------------
    def _build_checklist(self) -> None:
        self._page = "checklist"
        for w in self.winfo_children():
            w.destroy()
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=22, pady=20)
        head = ctk.CTkFrame(shell, fg_color=BG_CARD, corner_radius=18,
                            border_width=1, border_color=BORDER)
        head.pack(fill="x", pady=(0, 13))
        brand = ctk.CTkFrame(head, fg_color="transparent")
        brand.pack(side="left", padx=18, pady=13)
        ctk.CTkLabel(brand, text="✓", width=36, height=36, corner_radius=12,
                     fg_color="#17563a", text_color="#ecfdf3",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side="left")
        words = ctk.CTkFrame(brand, fg_color="transparent")
        words.pack(side="left", padx=11)
        ctk.CTkLabel(words, text="Preparation guide", text_color=TEXT, anchor="w",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(words, text="Expand each item. Red and orange items protect the start sequence.",
                     text_color=MUTED, anchor="w",
                     font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(head, text="10 checks  ·  start only when ready", corner_radius=10,
                     fg_color="#0d2238", text_color="#b9d8eb", height=30,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(
                         side="right", padx=16, pady=15)

        legend = ctk.CTkFrame(shell, fg_color="#0d1728", corner_radius=13,
                               border_width=1, border_color="#203858")
        legend.pack(fill="x", pady=(0, 10))
        ctk.CTkLabel(legend, text="PRIORITY KEY", text_color=MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(
                         side="left", padx=(14, 11), pady=9)
        for label, color in (("CRITICAL · REQUIRED", "#fb7185"),
                             ("HIGH IMPACT", "#fb923c"),
                             ("IMPORTANT", "#fbbf24"),
                             ("HELPFUL", SUCCESS)):
            ctk.CTkLabel(legend, text=f"●  {label}", text_color=color,
                         font=ctk.CTkFont(size=10, weight="bold")).pack(
                             side="left", padx=(0, 13), pady=9)

        body = ctk.CTkScrollableFrame(
            shell, fg_color=BG_SOFT, corner_radius=18, border_width=1,
            border_color=BORDER, scrollbar_button_color="#345074",
            scrollbar_button_hover_color=ACCENT_HOVER)
        body.pack(fill="both", expand=True)
        speed_scroll(body, factor=3)
        body.grid_columnconfigure(0, weight=1)

        permission = (
            "Run this app with the same permission level as Roblox",
            "Windows can drop the macro's keyboard input when Roblox is elevated but this app is not.",
            ("If Roblox was opened as administrator, open this app the same way.",
             "Use an Administrator PowerShell/Terminal and run easy_run.py, or use the on-screen controls if a global hotkey is unavailable."),
            "Do not assume a visible app means it can send F2/F4 or reel input to an elevated Roblox window.",
            "F2/F4 and the on-screen Start control respond, and the macro can control the game.")
        if os.name != "nt":
            permission = (
                "Confirm your Linux input permission before starting",
                "The Linux launcher needs its configured input access before it can send controls to the game.",
                ("Run the supplied Linux setup once.", "Use the supported X11 session and confirm the launcher starts without an input-permission warning."),
                "Do not start a run if the launcher reports it cannot create input events.",
                "The launcher can register its controls and the on-screen Start button works.")

        items = [
            ("Stand at the NPC, right on the edge of the interaction range", "critical",
             "Your character must be at the outer edge of the Fisherman's interaction circle and lined up at 0°: directly in front of or directly behind the NPC, on one straight line through its centre. Do not start from a diagonal angle. This lets the game's push return you to the same edge instead of drifting sideways.",
             ("Face the Fisherman and wait until the Interact prompt is visible.",
              "Move to the outer boundary while staying on the NPC's straight centre line — imagine a line from the NPC's middle through your character. That is the 0° lane.",
              "Use only tiny adjustments. You should be close enough to interact but not deep inside the circle; keep the camera still, then start the macro."),
             "Too close can reopen dialogue while casting; too far makes the anchor fail. Starting even slightly diagonal means the game's outward push can land you on another edge. Repeating the same backward movement from that wrong edge compounds the angle until the macro can miss the NPC.",
             "F2 opens and closes the NPC dialogue once. After the game's push, you remain on the same straight 0° lane at the fishing position, so the rod can cast without reopening dialogue.",
             "check_range", True),
            (permission[0], "critical", permission[1], permission[2], permission[3], permission[4], None, False),
            ("Keep Roblox visible, focused, and unobstructed", "high",
             "The macro reads pixels from the Roblox window and sends input to it. A covered, minimized, resized, unfocused game—or a changed fullscreen/windowed mode—can make a correct calibration look wrong.",
             ("Keep Roblox open and in front; fullscreen or borderless is the safest starting view.",
              "Match the display mode exactly: calibrate in fullscreen, run in fullscreen. Calibrate in windowed mode, run in the same windowed mode.",
              "Use the same window size and display scaling that you used when calibrating.",
              "Close or move chat, browsers, editors, recording controls, and overlays away from the game window."),
             "A panel over a dialogue, reel bar, Craft window, or menu is read as if that game UI does not exist.",
             "Roblox is in the same fullscreen or windowed mode used for calibration, fills the expected view, has no covered detection areas, and is not minimized.",
             None, False),
            ("Enable Shift Lock Switch; leave the current lock OFF before F2", "high",
             "In Roblox Settings, the Shift Lock Switch option must be enabled. Before starting, leave the current cursor lock OFF; the macro turns it on only for fishing and verifies the cursor snap.",
             ("Turn off Roblox auto-run/running.", "Enable Shift Lock Switch in Roblox Settings.",
              "Leave the current Shift Lock OFF, then let the macro manage it while it talks to the NPC and fishes."),
             "Do not toggle these during a run. A manual toggle can make a free cursor look centred or rotate the camera at the wrong time.",
             "Auto-run is off, Shift Lock Switch is enabled, and the current lock is OFF before F2.",
             None, False),
            ("Calibrate after a display, DPI, or UI change", "high",
             "Click positions and search regions are saved as fractions of your game window, but their shipped defaults came from a different setup. Update 30 menus, resolution changes, and display scaling can all move what the macro sees.",
             ("Open Calibrate controls with Roblox visible at the size you will use.",
              "Confirm the NPC menu area covers the complete four-row root menu and the shorter Shop/Bait pages.",
              "Re-shoot each special UI—Craft window or recipe note—before positioning its matching control."),
             "Do not copy coordinates from another display or crop a region so tightly that it cuts off the target UI.",
             "The Calibrate overlays line up with the real game controls, and Save completes without changing unrelated settings.",
             None, False),
            (f"Equip the fishing rod and confirm hotbar slot {self.cfg.rod_slot}", "important",
             "The macro stows and re-draws the rod around NPC trips to clear the post-catch state. It can only do that if the selected setup slot is the actual fishing rod slot.",
             ("Equip the fishing rod before starting.", "Check the visible hotbar slot matches the value chosen in Setup.",
              "If you change rods or rearrange the hotbar, update Setup before the next run."),
             "A wrong slot can leave the macro holding the wrong item, so it cannot re-approach the NPC or clear a caught fish.",
             "The fishing rod is visibly equipped and its hotbar slot matches the setup value.",
             "check_rod", False),
            ("Remove visual distractions from detection areas", "important",
             "Screen detection can be confused by visuals that look like a bite, fish, zone, or chest. The common offenders are colorful cosmetics, auras, particles, and HUD content inside a too-wide detection box.",
             ("Use a plain avatar and avoid red/pink accessories or fruit auras near the character.",
              "Keep the bite-marker region away from the top-right player/bounty list.",
              "If night or unusual colors make the reel unreliable, use the optional colour samples or Zone track only after normal calibration."),
             "Do not place cosmetic effects, bright particles, or unrelated HUD elements inside the areas the macro watches.",
             "The detection regions contain the intended game element and little else; no constant aura crosses the avatar or reel view.",
             None, False),
            ("Choose bait, selling, and reaction settings deliberately", "important",
             "Your setup controls determine how often the macro visits the NPC and how hard it polls the screen. Configure them before the run instead of changing them mid-session.",
             ("Use a bait purchase amount within the game limit; 90 leaves room below the 99-bait cap.",
              "Set Sell every 1 only for testing; choose a larger number or 0 for normal runs.",
              "Use Fast response only when your computer remains responsive; Balanced response is the safer default."),
             "Do not expect the NPC to sell favourited fish or your heaviest fish; that is game behavior, not a failed click.",
             "The Run Profile matches your intended NPC route, bait plan, sell interval, rod slot, and reaction mode.",
             None, False),
            ("Hands off: keep the camera and game state stable", "important",
             "After the run begins, the macro assumes the camera, cursor, and visible game UI stay stable. Manual mouse movement changes the view it is aiming at.",
             ("Leave Roblox focused while the macro runs.", "Do not move the mouse, click the game, or open menus manually.",
              "Use F2 or the on-screen Start/Stop control to stop before changing position, settings, or focus."),
             "Do not start while another dialogue, catch card, Craft panel, or unrelated Roblox menu is already covering the expected screen state.",
             "The game view stays still and the macro is the only thing interacting with Roblox.",
             None, False),
            ("If the expected state is missed, stop and verify", "helpful",
             "The safe response to a missed menu, reel bar, or dialogue is to stop, inspect the visible state, then recalibrate or adjust the relevant setup. Do not keep manually forcing the run forward.",
             ("Stop with F2 if the macro is waiting on an obviously absent state.", "Use F8 to toggle the diagnostic overlay/log when collecting useful evidence.",
              "Check focus, overlays, NPC range, rod slot, and the relevant calibration region before trying again."),
             "Do not edit config.json blindly or keep clicking the game while the macro expects a different screen state.",
             "You can name the missed state and inspect the matching Calibrate control before the next run.",
             None, False),
            ("Final 20-second go / no-go check", "helpful",
             "Take one last pass before enabling Start. This protects against nearly every common setup failure without adding any background work during the run.",
             ("NPC edge and visible Interact prompt confirmed.", "Roblox focused, unobstructed, and at the calibrated display size.",
              "Auto-run and shift lock off; rod equipped; hotbar slot and plan verified.",
              "No bright aura/cosmetic crosses a detection area; you are ready to leave the mouse alone."),
             "If any red or orange item is not true yet, use Back and fix it before starting.",
             "Every required check is true. Tick the confirmation below only when you are ready for the macro to control the session.",
             None, False),
        ]
        self._prep_items = []
        for index, (title, priority, desc, steps, warning, success, image_key, simulation) in enumerate(items, 1):
            item = PreparationItem(body, index, title, priority, desc, steps, warning,
                                   success, image_key, simulation,
                                   expanded=index == 1)
            item.grid(row=index - 1, column=0, sticky="ew", padx=12, pady=5)
            self._prep_items.append(item)

        foot = ctk.CTkFrame(shell, fg_color=BG_CARD, corner_radius=16,
                             border_width=1, border_color=BORDER)
        foot.pack(fill="x", pady=(12, 0))
        ctk.CTkButton(foot, text="←  Back to setup", width=138, height=36,
                      fg_color=BG_CARD_RAISED, hover_color="#203858", border_width=1,
                      border_color=BORDER, text_color=TEXT,
                      command=self._build_form).pack(side="left", padx=12, pady=10)
        self.v_ready = ctk.BooleanVar(value=False)
        # CTkCheckBox deliberately has no `progress_color` here: that option
        # exists on switches/progress bars but is rejected by current CTk.
        ctk.CTkCheckBox(
            foot, text="I completed the required preparation checks",
            variable=self.v_ready,
            command=lambda: self.go.configure(
                state="normal" if self.v_ready.get() else "disabled"),
            text_color="#dce8f5", font=ctk.CTkFont(size=12, weight="bold")).pack(
                side="left", padx=10)
        self._prep_error = ctk.CTkLabel(foot, text="", text_color=DANGER,
                                        font=ctk.CTkFont(size=11))
        self._prep_error.pack(side="left", padx=(4, 8))
        self.go = ctk.CTkButton(foot, text="Finish  →", width=164, height=38,
                                fg_color=ACCENT, hover_color=ACCENT_HOVER,
                                text_color="#f8fbff", text_color_disabled="#f8fbff",
                                state="disabled",
                                font=ctk.CTkFont(size=13, weight="bold"),
                                command=self._finish_preparation)
        self.go.pack(side="right", padx=12, pady=10)

    # -- page 3: running --------------------------------------------------
    def _build_runner(self) -> None:
        if getattr(self, "_page", None) == "runner":
            return
        self._page = "runner"
        for w in self.winfo_children():
            w.destroy()

        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.pack(fill="both", expand=True, padx=22, pady=20)
        head = ctk.CTkFrame(shell, fg_color=BG_CARD, corner_radius=18,
                            border_width=1, border_color=BORDER)
        head.pack(fill="x", pady=(0, 12))
        title = ctk.CTkFrame(head, fg_color="transparent")
        title.pack(side="left", padx=18, pady=13)
        ctk.CTkLabel(title, text="RUN CONSOLE", text_color=ACCENT,
                     font=ctk.CTkFont(size=11, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(title, text="Fishing control terminal", text_color=TEXT,
                     font=ctk.CTkFont(size=21, weight="bold")).pack(anchor="w", pady=(2, 0))
        ctk.CTkLabel(title, text="Recent log only — capped to keep this page responsive.",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(anchor="w", pady=(2, 0))
        self.badge = ctk.CTkLabel(head, text="●  IDLE", text_color=MUTED,
                                  font=ctk.CTkFont(size=12, weight="bold"))
        self.badge.pack(side="right", padx=18)

        hotkeys = ctk.CTkFrame(shell, fg_color=BG_SOFT, corner_radius=14,
                                border_width=1, border_color=BORDER)
        hotkeys.pack(fill="x", pady=(0, 12))
        ctk.CTkLabel(hotkeys, text="CONTROLS", text_color=ACCENT,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(side="left", padx=(15, 10), pady=11)
        ctk.CTkLabel(hotkeys, text="F2: Start / Pause     F4: Stop     F8: Show hitboxes",
                     text_color="#dce8f5", font=ctk.CTkFont(size=12, weight="bold")).pack(side="left", pady=11)

        log_card = ctk.CTkFrame(shell, fg_color=BG_CARD, corner_radius=16,
                                 border_width=1, border_color=BORDER)
        log_card.pack(fill="both", expand=True)
        ctk.CTkLabel(log_card, text="LIVE LOG", text_color=MUTED,
                     font=ctk.CTkFont(size=10, weight="bold")).pack(anchor="w", padx=14, pady=(11, 4))
        self.logbox = ctk.CTkTextbox(log_card, fg_color="#08111f", text_color="#c7d7ea",
                                     border_width=0, corner_radius=10,
                                     font=ctk.CTkFont(family="Consolas", size=12),
                                     activate_scrollbars=True)
        self.logbox.pack(fill="both", expand=True, padx=10, pady=(0, 10))
        self._log_lines = 0
        self._log_limit = 1200

        foot = ctk.CTkFrame(shell, fg_color="transparent")
        foot.pack(fill="x", pady=(12, 0))
        ctk.CTkLabel(foot, text="F4 stops the current run; it does not close this window.",
                     text_color=MUTED, font=ctk.CTkFont(size=11)).pack(side="left")
        self.stop_btn = ctk.CTkButton(foot, text="Stop  (F4)", width=132, height=40,
                                      fg_color="#4b2235", hover_color="#713047",
                                      text_color="#ffe4e6", state="disabled",
                                      font=ctk.CTkFont(size=13, weight="bold"), command=self._stop)
        self.stop_btn.pack(side="right")
        self.btn = ctk.CTkButton(foot, text="Start  (F2)", width=150, height=40,
                                 fg_color=ACCENT, hover_color="#268a5f", text_color="#07111f",
                                 font=ctk.CTkFont(size=13, weight="bold"), command=self._toggle)
        self.btn.pack(side="right", padx=(0, 9))

        self.engine = FishingEngine(self.cfg, log=self._log)
        self.engine.bait_count = self.bait
        self._bind_hotkeys()
        self._log("Ready. Press Start (or F2) with Roblox focused.")

    def _log(self, *parts) -> None:
        line = " ".join(str(p) for p in parts)
        def append() -> None:
            try:
                if not self.logbox.winfo_exists():
                    return
                self.logbox.insert("end", line + "\n")
                self._log_lines += 1
                # Keep the terminal useful during long sessions without an
                # ever-growing Tk text buffer consuming memory and redraw time.
                if self._log_lines > self._log_limit:
                    drop = min(200, self._log_lines - self._log_limit)
                    self.logbox.delete("1.0", f"{drop + 1}.0")
                    self._log_lines -= drop
                self.logbox.see("end")
            except Exception:                          # noqa: BLE001
                pass
        try:
            self.after(0, append)
        except Exception:                          # noqa: BLE001
            pass

    def _bind_hotkeys(self) -> None:
        if self._hotkeys_bound:
            return
        last = [0.0]

        def toggle() -> None:
            now = time.perf_counter()
            if now - last[0] < 0.4:
                return
            last[0] = now
            # keyboard invokes this callback on its hook thread. Tk widgets
            # must only be changed on Tk's thread; calling _toggle() here could
            # paint "running" while its worker never actually started.
            self.after(0, self._toggle)

        def stop_key():
            self.after(0, self._stop)

        def debug_key():
            self.after(0, self._toggle_debug)

        # Windows: the `keyboard` library. It needs administrator rights for a
        # global hook; without them add_hotkey raises — which must not take the
        # window down, since the on-screen button does the same job.
        if sys.platform == "win32":
            try:
                import keyboard
                keyboard.add_hotkey(self.cfg.start_stop_key, toggle)
                keyboard.add_hotkey(self.cfg.quit_key, stop_key)
                keyboard.add_hotkey("f8", debug_key)
                self._hotkeys_bound = True
            except Exception as exc:                   # noqa: BLE001
                self._log(f"Hotkeys unavailable ({exc}) — use the button "
                          f"instead. Run as administrator if you want F2/F4.")
            return

        # Linux/macOS: pynput global hotkeys (X11). Falls back to the button.
        try:
            from pynput import keyboard as pk
            hk = pk.GlobalHotKeys({
                f"<{self.cfg.start_stop_key.lower()}>": toggle,
                f"<{self.cfg.quit_key.lower()}>": stop_key,
                "<f8>": debug_key,
            })
            hk.daemon = True
            hk.start()
            self._hotkeys = hk                         # keep a ref so it lives on
            self._hotkeys_bound = True
        except Exception as exc:                       # noqa: BLE001
            self._log(f"Hotkeys unavailable ({exc}) — use the button instead.")

    def _toggle_debug(self) -> None:
        """F8: show the platform hitbox overlay and start a diagnostic log."""
        from bloxfish.debug import DEBUG
        try:
            if DEBUG.enabled:
                path = DEBUG.disarm()
                self._log(f"[debug] OFF — saved {path}" if path else "[debug] OFF")
            else:
                # Windows uses the Tk renderer; the Linux launcher registers a
                # click-through X11 renderer before App is created.
                path = DEBUG.arm(overlay_parent=self, show_overlay=True)
                where = "hitboxes + log" if DEBUG.overlay_visible else "log"
                self._log(f"[debug] ON ({where}) — writing {path}")
        except Exception as exc:                       # noqa: BLE001
            self._log(f"[debug] could not toggle: {exc}")

    def _toggle(self) -> None:
        eng = self.engine
        if eng is None:
            return
        if eng.running or (self.worker and self.worker.is_alive()):
            self._stop()
            return
        if self.worker and self.worker.is_alive():
            self._log("[start] still starting — wait for the control-loop message")
            return
        self.badge.configure(text="●  STARTING", text_color=WARNING)
        self.btn.configure(text="Pause  (F2)")
        self.stop_btn.configure(state="normal")
        self._log("[start] queued")
        self.worker = threading.Thread(target=self._run, daemon=True)
        self.worker.start()
        self.after(50, self._show_started)

    def _show_started(self) -> None:
        """Promote 'starting' only after the engine has actually armed itself."""
        eng = self.engine
        if eng is not None and eng.running:
            self.badge.configure(text="●  RUNNING", text_color=SUCCESS)
        elif self.worker and self.worker.is_alive():
            self.after(50, self._show_started)

    def _run(self) -> None:
        try:
            self.engine.run()
        except Exception as exc:                       # noqa: BLE001
            self._log(f"[start] worker stopped: {exc!r}")
        finally:
            if self.engine.safety_stopped:
                self.after(0, lambda: (
                    self.badge.configure(text="●  SAFETY STOPPED", text_color=DANGER),
                    self.btn.configure(text="Start  (F2)"),
                    self.stop_btn.configure(state="disabled")))
            else:
                self.after(0, lambda: (self.badge.configure(text="●  IDLE", text_color=MUTED),
                                        self.btn.configure(text="Start  (F2)"),
                                        self.stop_btn.configure(state="disabled")))

    def _stop(self) -> None:
        """F4 and the red control only stop a run; closing remains explicit."""
        eng = self.engine
        if eng is None:
            return
        if eng.running or (self.worker and self.worker.is_alive()):
            eng.stop()
            self.badge.configure(text="●  STOPPING", text_color=WARNING)
            self.btn.configure(text="Start  (F2)")
            self.stop_btn.configure(state="disabled")
            self._log("[stop] requested by F4 / Stop control")

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
