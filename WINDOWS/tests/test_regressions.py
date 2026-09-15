from __future__ import annotations

import ast
import contextlib
import io
import json
import math
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from bloxfish.config import Config


class ConfigRegressionTests(unittest.TestCase):
    def test_npc_reacquisition_defaults_to_two_bounded_short_probes(self) -> None:
        cfg = Config()
        self.assertEqual(cfg.shop.walk_back_tap, 0.10)
        self.assertEqual(cfg.shop.max_approach_attempts, 2)
        self.assertEqual(cfg.shop.direct_dialog_timeout, 0.9)

    def test_menu_page_settle_default_covers_the_falling_row_animation(self) -> None:
        self.assertEqual(Config().shop.menu_page_settle, 0.65)

    def test_string_path_is_loaded_and_stale_tuning_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "alternate.json"
            path.write_text(json.dumps({
                "rod_slot": "7",
                "detection": {"bar_track_min_frac": 0.99},
            }), encoding="utf-8")

            cfg = Config.load(str(path))

        self.assertEqual(cfg.rod_slot, "7")
        self.assertEqual(cfg.detection.bar_track_min_frac,
                         Config().detection.bar_track_min_frac)

    def test_non_finite_numbers_are_rejected_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            path.write_text(
                '{"timing":{"cast_hold":NaN},'
                '"colors":{"cap_track_bgr":[NaN,1,2]}}',
                encoding="utf-8")

            cfg = Config.load(path)

        self.assertTrue(math.isfinite(cfg.timing.cast_hold))
        self.assertEqual(cfg.colors.cap_track_bgr,
                         Config().colors.cap_track_bgr)

    def test_save_is_sparse_and_accepts_a_string_path(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "nested" / "config.json"
            cfg = Config()
            cfg.rod_slot = "4"
            cfg.timing.cast_hold += 0.25
            cfg.shop.menu_item3 = (0.72, 0.63)
            cfg.detection.bar_track_min_frac = 0.99

            cfg.save(str(path))
            saved = json.loads(path.read_text(encoding="utf-8"))

            self.assertEqual(saved["rod_slot"], "4")
            self.assertIn("cast_hold", saved["timing"])
            self.assertEqual(saved["shop"]["menu_item3"], [0.72, 0.63])
            self.assertNotIn("bar_track_min_frac", saved["detection"])
            self.assertEqual(list(path.parent.glob(".config.json.*.tmp")), [])
            loaded = Config.load(path)
            self.assertEqual(loaded.rod_slot, "4")
            self.assertEqual(loaded.shop.menu_item3, (0.72, 0.63))


try:
    import cv2  # noqa: F401
    import numpy as np
    from bloxfish.capture import Rect
    from bloxfish.config import Colors
    from bloxfish import engine as engine_mod
    from bloxfish import shop
    from bloxfish.vision import (
        find_chest, find_falling_menu_buttons, update30_dialogue_present,
    )
    from PIL import Image as PILImage
    from tools import calibrate
    from easy_run import (
        ASSETS, App, CALIB_GROUPS, CALIB_IMAGE_ALIASES, COLOR_ITEMS, Calibrator,
        NPCPositionSimulation, PreparationItem, _group_of,
    )
except ImportError:
    cv2 = None


@unittest.skipIf(cv2 is None, "OpenCV is not installed in this interpreter")
class VisionRegressionTests(unittest.TestCase):
    def test_safety_watchdog_stops_and_releases_input_after_no_response(self) -> None:
        events = []

        class FakeMouse:
            def release(self):
                events.append("release")

        class FakeEngine:
            cfg = Config()
            running = True
            _stop = False
            _safety_stopped = False
            _last_response_at = 100.0
            _last_response_label = "a fish bite"
            mouse = FakeMouse()

            def log(self, line):
                events.append(line)

        engine = FakeEngine()
        engine.cfg.timing.response_timeout = 300.0
        with mock.patch.object(engine_mod.time, "perf_counter", return_value=400.0):
            self.assertFalse(engine_mod.FishingEngine._alive(engine))

        self.assertTrue(engine._stop)
        self.assertTrue(engine._safety_stopped)
        self.assertEqual(events[0], "release")
        self.assertIn("no confirmed game response", events[1])

    def test_startup_focuses_roblox_before_the_first_cycle(self) -> None:
        """A received F2 must hand foreground input back to Roblox first."""
        events = []

        class FakeMouse:
            def release(self):
                events.append("release")

        class FakeKeyboard:
            def tap(self, _scan, _hold=0.06):
                events.append("shift")

        class FakeEngine:
            cfg = Config()
            mouse = FakeMouse()
            keyboard = FakeKeyboard()
            running = False
            _stop = False

            def log(self, line):
                events.append(line)

            def _sleep(self, seconds):
                events.append(("sleep", seconds))

            def _note_response(self, label):
                events.append(("response", label))

            def _alive(self):
                return not self._stop

            def _cycle(self):
                events.append("cycle")
                self._stop = True

        engine = FakeEngine()
        engine.cfg.shop.enter_stance_on_start = False
        with mock.patch.object(engine_mod, "focus_game_window", return_value=True) as focus:
            engine_mod.FishingEngine._run_locked(engine)

        focus.assert_called_once_with(engine.cfg.window_title)
        self.assertLess(events.index("[start] Roblox focused — arming input"),
                        events.index("cycle"))
        self.assertIn(("sleep", 0.12), events)

    def test_update30_shop_click_uses_the_live_row(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for y in (450, 535):
            image[y:y + 22, 1380:1510] = 255

        class FakeMouse:
            def __init__(self) -> None:
                self.clicks = []

            def click_at(self, x, y) -> None:
                self.clicks.append((x, y))

        class FakeScreen:
            def grab(self, _rect):
                return image

        class FakeEngine:
            cfg = Config()
            window = Rect(100, 200, 1920, 1080)
            screen = FakeScreen()
            mouse = FakeMouse()

        engine = FakeEngine()
        engine.cfg.shop.menu_left = 0.0
        engine.cfg.shop.menu_top = 0.0
        engine.cfg.shop.menu_right = 1.0
        engine.cfg.shop.menu_bottom = 1.0
        shop.click_menu_item(engine, 0, "Shop")
        shop.click_menu_item(engine, -1, "Back")

        self.assertEqual(engine.mouse.clicks, [(1544, 660), (1544, 746)])

    def test_dark_menu_panels_win_over_a_white_cursor(self) -> None:
        """A cursor above the first row must not move Buy Bait's target."""
        image = np.full((348, 365, 3), (210, 170, 60), dtype=np.uint8)
        for top in (80, 163, 246):
            image[top:top + 75, :] = (30, 30, 30)
        # The cursor-like white blob reproduced from the failing video.
        image[35:50, 290:305] = 255

        buttons = find_falling_menu_buttons(image)

        self.assertEqual(len(buttons), 3)
        self.assertTrue(all(abs(button.x - image.shape[1] * 0.5) <= 1
                            for button in buttons))
        self.assertTrue(all(abs(button.y - expected) <= 3
                            for button, expected in zip(buttons,
                                                        (117, 200, 283))))

    def test_update30_named_actions_keep_page_roles_separate(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        for y in (450, 535, 620, 705):
            image[y:y + 22, 1380:1510] = 255

        class FakeMouse:
            def __init__(self) -> None:
                self.clicks = []

            def click_at(self, x, y) -> None:
                self.clicks.append((x, y))

        class FakeScreen:
            def grab(self, _rect):
                return image

        class FakeEngine:
            cfg = Config()
            window = Rect(100, 200, 1920, 1080)
            screen = FakeScreen()
            mouse = FakeMouse()

        engine = FakeEngine()
        engine.cfg.shop.menu_left = 0.0
        engine.cfg.shop.menu_top = 0.0
        engine.cfg.shop.menu_right = 1.0
        engine.cfg.shop.menu_bottom = 1.0
        shop.click_menu_action(engine, "root", "job_stats")
        shop.click_menu_action(engine, "shop", "sell_fish")
        shop.click_menu_action(engine, "bait", "back")

        # Same physical stack, three named page-local roles: third, second,
        # and bottom respectively. The route never depends on a stale Y value.
        self.assertEqual(engine.mouse.clicks,
                         [(1544, 830), (1544, 746), (1544, 916)])

    def test_live_menu_scan_stays_inside_calibrated_menu_area(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # White console/HUD text outside the calibrated envelope must not
        # become the first rows of the NPC menu.
        for y in (150, 235, 320):
            image[y:y + 22, 200:420] = 255
        for y in (450, 535, 620, 705):
            image[y:y + 22, 1380:1540] = 255

        class FakeScreen:
            def grab(self, rect):
                return image[rect.top:rect.bottom, rect.left:rect.right]

        class FakeEngine:
            cfg = Config()
            window = Rect(0, 0, 1920, 1080)
            screen = FakeScreen()

        engine = FakeEngine()
        engine.cfg.shop.menu_left = 0.65
        engine.cfg.shop.menu_top = 0.40
        engine.cfg.shop.menu_right = 0.95
        engine.cfg.shop.menu_bottom = 0.80
        buttons = shop._falling_menu_buttons(engine)

        self.assertEqual(len(buttons), 4)
        self.assertTrue(all(abs(y - expected) <= 1
                            for (_x, y), expected in zip(
                                buttons, (460, 545, 630, 715))))

    def test_npc_menu_yellow_name_strip_is_not_a_catch_popup(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        image[780:825, 700:1220] = (30, 180, 220)
        for y in (450, 535, 620, 705):
            image[y:y + 22, 1380:1540] = 255

        class FakeScreen:
            def grab(self, rect):
                return image[rect.top:rect.bottom, rect.left:rect.right]

        class FakeEngine:
            cfg = Config()
            window = Rect(0, 0, 1920, 1080)
            screen = FakeScreen()

        engine = FakeEngine()
        engine.cfg.shop.menu_left = 0.65
        engine.cfg.shop.menu_top = 0.40
        engine.cfg.shop.menu_right = 0.95
        engine.cfg.shop.menu_bottom = 0.80
        self.assertFalse(shop.popup_up(engine))

    def test_bait_exit_waits_for_root_before_nevermind(self) -> None:
        class FakeEngine:
            cfg = Config()

            def __init__(self) -> None:
                self.sleeps = []

            def _alive(self) -> bool:
                return True

            def _sleep(self, seconds: float) -> None:
                self.sleeps.append(seconds)

        engine = FakeEngine()
        engine.cfg.shop.before_leave = 0.0
        engine.cfg.shop.root_menu_settle = 0.0
        # The Update 30 stack check also requires a stable menu-page witness.
        # This unit test supplies exactly two samples per page, so it must not
        # inherit the real-world visual settle delay from Config().
        engine.cfg.shop.menu_page_settle = 0.0
        actions = []
        # visible bait page -> not root -> root after Back
        with mock.patch.object(shop, "menu_items", side_effect=[2, 2, 4, 4]), \
             mock.patch.object(shop, "in_dialogue", side_effect=[True, False]), \
             mock.patch.object(shop, "click_menu_action",
                               side_effect=lambda _e, p, a: actions.append((p, a))):
            self.assertTrue(shop.leave_dialogue(engine))

        self.assertEqual(actions, [("bait", "back"), ("root", "nevermind")])

    def test_closed_npc_menu_does_not_retry_nevermind_for_a_catch_card(self) -> None:
        """A catch/popup witness is not permission to click NPC buttons."""
        class FakeEngine:
            pass

        with mock.patch.object(shop, "menu_items", return_value=0), \
             mock.patch.object(shop, "popup_up", return_value=True):
            self.assertFalse(shop.in_dialogue(FakeEngine()))

    def test_post_sale_message_is_not_mistaken_for_a_confirm_menu(self) -> None:
        """The Fisherman's passive result strip is one panel, not a button page."""
        class FakeEngine:
            pass

        with mock.patch.object(shop, "menu_items", return_value=1):
            self.assertFalse(shop.in_dialogue(FakeEngine()))

    def test_post_sale_legacy_bands_do_not_keep_confirm_open(self) -> None:
        """Confirm uses the live stack, not a legacy count from the result strip."""
        class FakeEngine:
            pass

        with mock.patch.object(shop, "_falling_menu_buttons", return_value=[]), \
             mock.patch.object(shop, "menu_items", return_value=2):
            self.assertFalse(shop.action_menu_open(FakeEngine()))

    def test_update30_profile_ignores_legacy_menu_bands_after_close(self) -> None:
        """Current UI must not re-open dialogue from a stale legacy count."""
        class FakeEngine:
            cfg = Config()

        engine = FakeEngine()
        engine.cfg.colors.cap_dialogue_on = True
        with mock.patch.object(shop, "_falling_menu_buttons", return_value=[]), \
             mock.patch.object(shop, "menu_items", return_value=2):
            self.assertFalse(shop.in_dialogue(engine))

    def test_update30_profile_ignores_legacy_popup_panel(self) -> None:
        """Fishing scenery must not trigger the old blue-card popup detector."""
        class FakeScreen:
            def grab(self, _rect):
                return np.zeros((1080, 1920, 3), dtype=np.uint8)

        class FakeEngine:
            cfg = Config()
            window = object()
            screen = FakeScreen()

        engine = FakeEngine()
        engine.cfg.colors.cap_dialogue_on = True
        with mock.patch.object(shop, "_falling_menu_buttons", return_value=[]), \
             mock.patch.object(shop, "update30_dialogue_present", return_value=False), \
             mock.patch.object(shop, "dialogue_overlay_frac", return_value=0.61):
            self.assertFalse(shop.popup_up(engine))

    def test_npc_return_uses_two_bounded_short_adjustable_s_probes(self) -> None:
        self.assertEqual(Config().shop.walk_back_tap, 0.10)
        self.assertEqual(Config().shop.max_approach_attempts, 2)

    def test_modern_menu_witness_rejects_unaligned_nearby_highlights(self) -> None:
        """Two nearby water/cosmetic marks must not block a shop route."""
        roi = Rect(1324, 439, 365, 333)
        false_marks = [(1579, 495), (1504, 522)]
        root_rows = [(1390, 493), (1465, 577),
                     (1436, 660), (1454, 743)]

        self.assertEqual(shop._coherent_menu_stack(false_marks, roi), [])
        self.assertEqual(shop._coherent_menu_stack(root_rows, roi), root_rows)

    def test_npc_dialogue_tries_without_walking_before_two_s_probes(self) -> None:
        class FakeMouse:
            def move_to(self, *_point):
                pass

            def click_at(self, *_point):
                pass

        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, key, hold):
                self.taps.append((key, hold))

        class FakeEngine:
            cfg = Config()
            window = object()

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self._at_npc = False
                self._npc_repositioned = False

            def _alive(self):
                return True

            def _sleep(self, _seconds):
                pass

            def log(self, _line):
                pass

        engine = FakeEngine()
        with mock.patch.object(shop, "wait_popup_clear"), \
             mock.patch.object(shop, "set_rod"), \
             mock.patch.object(shop, "set_shift_lock"), \
             mock.patch.object(shop, "_abs", return_value=(0, 0)), \
             mock.patch.object(shop, "wait_for_menu_page", side_effect=[False, False, True]):
            self.assertTrue(shop.open_npc_dialogue(engine))

        self.assertEqual(engine.keyboard.taps,
                         [(shop.SC_S, 0.10), (shop.SC_S, 0.10)])
        self.assertTrue(engine._at_npc)
        self.assertTrue(engine._npc_repositioned)

    def test_partial_dialogue_after_first_probe_never_uses_second_s_probe(self) -> None:
        """A partial menu after probe one is still a no-movement boundary."""
        class FakeMouse:
            def move_to(self, *_point):
                pass

            def click_at(self, *_point):
                pass

        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, key, hold):
                self.taps.append((key, hold))

        class FakeEngine:
            cfg = Config()
            window = object()

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self._at_npc = False
                self._npc_repositioned = False
                self.lines = []

            def _alive(self):
                return True

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = FakeEngine()
        calls = iter(((False, False), (False, True)))

        def page_wait(_engine, _page, _timeout, *, saw_dialogue=None):
            result, saw = next(calls)
            if saw_dialogue is not None:
                saw_dialogue[0] = saw
            return result

        with mock.patch.object(shop, "wait_popup_clear"), \
             mock.patch.object(shop, "in_dialogue", return_value=False), \
             mock.patch.object(shop, "set_rod"), \
             mock.patch.object(shop, "set_shift_lock"), \
             mock.patch.object(shop, "_abs", return_value=(0, 0)), \
             mock.patch.object(shop, "wait_for_menu_page", side_effect=page_wait), \
             mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertFalse(shop.open_npc_dialogue(engine))

        self.assertEqual(engine.keyboard.taps, [(shop.SC_S, 0.10)])
        self.assertTrue(any("stopping further movement" in line
                            for line in engine.lines))

    def test_npc_dialogue_gives_up_after_two_true_no_dialogue_probes(self) -> None:
        """The extra recovery probe is bounded; it must never turn into a walk."""
        class FakeMouse:
            def move_to(self, *_point):
                pass

            def click_at(self, *_point):
                pass

        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, key, hold):
                self.taps.append((key, hold))

        class FakeEngine:
            cfg = Config()
            window = object()

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self._at_npc = False
                self._npc_repositioned = False

            def _alive(self):
                return True

            def _sleep(self, _seconds):
                pass

            def log(self, _line):
                pass

        engine = FakeEngine()
        with mock.patch.object(shop, "wait_popup_clear"), \
             mock.patch.object(shop, "in_dialogue", return_value=False), \
             mock.patch.object(shop, "set_rod"), \
             mock.patch.object(shop, "set_shift_lock"), \
             mock.patch.object(shop, "_abs", return_value=(0, 0)), \
             mock.patch.object(shop, "wait_for_menu_page", return_value=False), \
             mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertFalse(shop.open_npc_dialogue(engine))

        self.assertEqual(engine.keyboard.taps,
                         [(shop.SC_S, 0.10), (shop.SC_S, 0.10)])

    def test_visible_root_dialogue_is_reused_without_interact_or_s_movement(self) -> None:
        class FakeMouse:
            def __init__(self):
                self.moves = []
                self.clicks = []

            def move_to(self, *point):
                self.moves.append(point)

            def click_at(self, *point):
                self.clicks.append(point)

        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, *args):
                self.taps.append(args)

        class FakeEngine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self._shift_lock = False
                self._shift_lock_verified = True
                self._at_npc = False
                self._npc_repositioned = False
                self.lines = []

            def _alive(self):
                return True

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = FakeEngine()
        with mock.patch.object(shop, "wait_popup_clear"), \
             mock.patch.object(shop, "in_dialogue", return_value=True), \
             mock.patch.object(shop, "wait_for_menu_page", return_value=True), \
             mock.patch.object(shop, "set_shift_lock", return_value=True), \
             mock.patch.object(shop, "set_rod") as set_rod, \
             mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertTrue(shop.open_npc_dialogue(engine))

        self.assertEqual(engine.mouse.moves, [])
        self.assertEqual(engine.mouse.clicks, [])
        self.assertEqual(engine.keyboard.taps, [])
        set_rod.assert_not_called()
        self.assertTrue(any("no Interact click or S movement" in line
                            for line in engine.lines))

    def test_partial_dialogue_seen_during_direct_interact_never_uses_s_probe(self) -> None:
        """A falling root menu is a no-movement boundary, not a failed prompt."""
        class FakeMouse:
            def __init__(self):
                self.moves = []
                self.clicks = []

            def move_to(self, *point):
                self.moves.append(point)

            def click_at(self, *point):
                self.clicks.append(point)

        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, *args):
                self.taps.append(args)

        class FakeEngine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard()
                self._at_npc = False
                self._npc_repositioned = False
                self.lines = []

            def _alive(self):
                return True

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = FakeEngine()
        page_results = iter((False, True))

        def page_wait(_engine, _page, _timeout, *, saw_dialogue=None):
            result = next(page_results)
            if saw_dialogue is not None:
                saw_dialogue[0] = True
            return result

        with mock.patch.object(shop, "wait_popup_clear"), \
             mock.patch.object(shop, "in_dialogue", return_value=False), \
             mock.patch.object(shop, "action_menu_open", return_value=True), \
             mock.patch.object(shop, "set_shift_lock", return_value=True), \
             mock.patch.object(shop, "set_rod"), \
             mock.patch.object(shop, "_abs", return_value=(960, 540)), \
             mock.patch.object(shop, "wait_for_menu_page", side_effect=page_wait), \
             mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertTrue(shop.open_npc_dialogue(engine))

        self.assertEqual(engine.keyboard.taps, [])
        self.assertTrue(any("waiting without S movement" in line
                            for line in engine.lines))
        self.assertTrue(engine._at_npc)

    def test_windows_refuses_an_unconfirmed_shift_lock_before_fishing(self) -> None:
        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, *args):
                self.taps.append(args)

        class FakeMouse:
            def position(self):
                return (1450, 760)  # a free cursor, far from screen centre

        class FakeEngine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.keyboard = FakeKeyboard()
                self.mouse = FakeMouse()
                self._shift_lock = False
                self._shift_lock_verified = False
                self.lines = []

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = FakeEngine()
        with mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertFalse(shop.set_shift_lock(engine, True))

        self.assertEqual(engine.keyboard.taps, [(shop.SC_LSHIFT, 0.10)])
        self.assertFalse(engine._shift_lock)
        self.assertFalse(shop.fishing_shift_lock_ready(engine))
        self.assertTrue(any("refusing to fish" in line for line in engine.lines))

    def test_windows_shift_lock_requires_an_observed_cursor_snap(self) -> None:
        class FakeKeyboard:
            def __init__(self, mouse):
                self.mouse = mouse

            def tap(self, *_args):
                self.mouse.locked = True

        class FakeMouse:
            def __init__(self):
                self.locked = False

            def position(self):
                return (960, 552) if self.locked else (1450, 760)

        class FakeEngine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.mouse = FakeMouse()
                self.keyboard = FakeKeyboard(self.mouse)
                self._shift_lock = False
                self._shift_lock_verified = False

            def _sleep(self, _seconds):
                pass

            def log(self, _line):
                pass

        engine = FakeEngine()
        with mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertTrue(shop.set_shift_lock(engine, True))
            self.assertTrue(shop.fishing_shift_lock_ready(engine))

    def test_windows_rejects_a_cursor_that_was_already_centred_before_shift(self) -> None:
        class FakeKeyboard:
            def tap(self, *_args):
                pass

        class FakeMouse:
            def position(self):
                return (960, 552)

        class FakeEngine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.keyboard = FakeKeyboard()
                self.mouse = FakeMouse()
                self._shift_lock = False
                self._shift_lock_verified = False
                self.lines = []

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = FakeEngine()
        with mock.patch.object(shop, "interaction_safety_guard", return_value=True):
            self.assertFalse(shop.set_shift_lock(engine, True))

        self.assertFalse(engine._shift_lock)
        self.assertTrue(any("already centred" in line for line in engine.lines))

    def test_fishing_stance_is_idempotent_after_leaving_the_npc(self) -> None:
        """Re-entering fishing stance must never send a forward movement key."""
        class FakeKeyboard:
            def __init__(self) -> None:
                self.taps = []

            def tap(self, key, hold) -> None:
                self.taps.append((key, hold))

        class FakeEngine:
            cfg = Config()

            def __init__(self) -> None:
                self.keyboard = FakeKeyboard()
                self._at_npc = False

            def _sleep(self, _seconds) -> None:
                pass

        engine = FakeEngine()
        with mock.patch.object(shop, "set_shift_lock"):
            shop.enter_fishing_stance(engine)

        self.assertEqual(engine.keyboard.taps, [])

    def test_simultaneous_sale_and_restock_share_one_npc_visit(self) -> None:
        """A due sale plus bait trip must not leave and re-approach the NPC."""
        calls = []

        class FakeEngine:
            def _look_for_bar(self):
                return None

            def _needs_sell(self):
                return True

            def _needs_bait(self):
                return True

            def _sell_fish(self, *, stay_at_npc):
                calls.append(("sell", stay_at_npc))
                return True

            def _buy_bait(self):
                calls.append(("buy",))

            def _alive(self):
                return True

            def _do_cast(self):
                calls.append(("cast",))
                return False

        engine_mod.FishingEngine._cycle(FakeEngine())

        self.assertEqual(calls, [("sell", True), ("buy",), ("cast",)])

    def test_dialogue_reposition_skips_the_post_shop_forward_walk(self) -> None:
        class FakeKeyboard:
            def __init__(self):
                self.taps = []

            def tap(self, key, hold):
                self.taps.append((key, hold))

        class FakeEngine:
            cfg = Config()

            def __init__(self, repositioned):
                self.keyboard = FakeKeyboard()
                self._at_npc = True
                self._npc_repositioned = repositioned
                self.sleeps = []

            def _sleep(self, seconds):
                self.sleeps.append(seconds)

        after_dialogue = FakeEngine(True)
        initial_start = FakeEngine(False)
        with mock.patch.object(shop, "set_shift_lock"):
            shop.enter_fishing_stance(after_dialogue)
            shop.enter_fishing_stance(initial_start)

        # Roblox pushes the player forward when dialogue starts. Neither
        # post-shop cleanup nor the initial anchor adds a W movement.
        self.assertEqual(after_dialogue.keyboard.taps, [])
        self.assertEqual(initial_start.keyboard.taps, [])
        self.assertFalse(after_dialogue._at_npc)
        self.assertFalse(initial_start._at_npc)

    def test_third_calibrated_row_is_available_without_live_detection(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)

        class FakeMouse:
            def __init__(self) -> None:
                self.clicks = []

            def click_at(self, x, y) -> None:
                self.clicks.append((x, y))

        class FakeScreen:
            def grab(self, _rect):
                return image

        class FakeEngine:
            cfg = Config()
            window = Rect(100, 200, 1920, 1080)
            screen = FakeScreen()
            mouse = FakeMouse()

        engine = FakeEngine()
        shop.click_menu_action(engine, "root", "job_stats")
        self.assertEqual(engine.mouse.clicks, [(1538, 880)])

    def test_update30_menu_tracks_the_visible_button_stack(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # A scoreboard-like white row above the scan band must not become a
        # phantom first button. Each lower block stands in for a white label.
        image[350:370, 1700:1800] = 255
        for y in (450, 535, 620, 705):
            image[y:y + 22, 1380:1510] = 255

        buttons = find_falling_menu_buttons(image)

        self.assertEqual(len(buttons), 4)
        self.assertTrue(all(abs(button.y - expected) <= 1
                            for button, expected in zip(buttons,
                                                        (460, 545, 630, 715))))
        self.assertTrue(all(button.x > 1300 for button in buttons))

    def test_menu_overlay_label_does_not_become_a_phantom_first_row(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # The wide first band represents an on-screen calibration/debug label
        # above a real four-row menu. Its gap is intentionally unlike the
        # repeated 85px menu pitch below it.
        for y in (285, 450, 535, 620, 705):
            image[y:y + 22, 1380:1540] = 255

        buttons = find_falling_menu_buttons(image)

        self.assertEqual(len(buttons), 4)
        self.assertTrue(all(abs(button.y - expected) <= 1
                            for button, expected in zip(buttons,
                                                        (460, 545, 630, 715))))

    def test_update30_catch_card_uses_a_wide_lower_yellow_header(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        image[780:825, 700:1220] = (30, 180, 220)  # BGR warm-yellow glow
        self.assertTrue(update30_dialogue_present(image))

        craft_only = np.zeros_like(image)
        craft_only[680:725, 850:1050] = (30, 180, 220)
        self.assertFalse(update30_dialogue_present(craft_only))

    def test_fishing_progress_line_is_not_a_yellow_dialogue_header(self) -> None:
        image = np.zeros((1080, 1920, 3), dtype=np.uint8)
        # Compression can make this bright-green progress line look warm enough
        # for the old broad mask. It is not a multi-row catch/dialogue card.
        image[836:842, 520:1214] = (97, 255, 167)
        self.assertFalse(update30_dialogue_present(image))

    def test_calibrate_window_reports_current_bite_gates(self) -> None:
        class FakeScreen:
            def __init__(self) -> None:
                self.grabs = []

            def grab(self, rect) -> None:
                self.grabs.append(rect)

        screen = FakeScreen()
        output = io.StringIO()
        with mock.patch.object(
                calibrate, "find_game_window",
                return_value=(Rect(100, 200, 1920, 1080), True)):
            with contextlib.redirect_stdout(output):
                calibrate.cmd_window(Config(), screen)

        self.assertGreaterEqual(len(screen.grabs), 2)
        self.assertIn("min blob area", output.getvalue())
        self.assertIn("min max-dimension", output.getvalue())

    def test_chest_does_not_merge_separate_warm_blobs(self) -> None:
        image = np.zeros((10, 40, 3), dtype=np.uint8)
        image[:, 2:5] = (40, 170, 210)
        image[:, 16:20] = (40, 170, 210)
        self.assertIsNone(find_chest(image, Colors(), min_width=6))

    def test_chest_accepts_one_wide_contiguous_tile(self) -> None:
        image = np.zeros((10, 40, 3), dtype=np.uint8)
        image[:, 11:20] = (40, 170, 210)
        self.assertEqual(find_chest(image, Colors(), min_width=6),
                         (11.0, 19.0))


@unittest.skipIf(cv2 is None, "OpenCV is not installed in this interpreter")
class CalibratorInteractionRegressionTests(unittest.TestCase):
    """Exercise the real canvas -> normalized-coordinate path without Tk.

    The desktop window is visual only; these checks deliberately call the
    Calibrator's production commit/move/resync methods with a tiny canvas
    stand-in. That covers every normal region and click point at multiple DPI
    scales without reading a real screen or changing the user's config.
    """

    class Canvas:
        def __init__(self) -> None:
            self.items = {}
            self.next_id = 100

        def coords(self, item, *values):
            if values:
                self.items[item] = list(values)
            return list(self.items[item])

        def create_line(self, *_args, **_kwargs):
            self.next_id += 1
            self.items[self.next_id] = [0, 0, 0, 0]
            return self.next_id

        def delete(self, item) -> None:
            self.items.pop(item, None)

        @staticmethod
        def winfo_width() -> int:
            return 800

        @staticmethod
        def winfo_height() -> int:
            return 500

    def _view(self, scale: float):
        view = object.__new__(Calibrator)
        view.cfg = Config()
        view.win = Rect(0, 0, 1000, 600)
        view.scale = scale
        view.canvas = self.Canvas()
        view.shapes = {}
        view.sel = None
        view.pick = None
        view.drag = None
        view._guide_lines = []
        view._reviewed = set()
        view._color_reviewed = set()
        view._core_keys = ()
        view._color_keys = tuple(key for key, _label, _desc in COLOR_ITEMS)
        return view

    @staticmethod
    def _entry_fields(entry, cfg):
        _key, kind, holder, fields, *_ = entry
        source = getattr(cfg, holder)
        if kind == "box":
            return tuple(getattr(source, field) for field in fields)
        return tuple(getattr(source, fields[0]))

    def test_every_region_and_click_commits_correct_normalized_values_at_dpi_scales(self) -> None:
        for scale in (0.5, 1.0, 1.5):
            for _title, _icon, _color, entries in CALIB_GROUPS:
                for entry in entries:
                    key, kind, _holder, _fields, *_ = entry
                    view = self._view(scale)
                    item_id = f"shape-{key}"
                    if kind == "box":
                        coords = (80 * scale, 60 * scale, 520 * scale, 330 * scale)
                        expected = (0.08, 0.10, 0.52, 0.55)
                        shape = {"kind": "box", "id": item_id, "entry": entry,
                                 "grips": {}, "label": None}
                    else:
                        coords = (180 * scale, 132 * scale, 204 * scale, 156 * scale)
                        expected = (0.192, 0.24)
                        shape = {"kind": "dot", "id": item_id, "entry": entry,
                                 "r": 12, "label": None, "tick": None}
                    view.canvas.coords(item_id, *coords)
                    view.shapes[key] = shape
                    Calibrator._commit(view, key)
                    actual = self._entry_fields(entry, view.cfg)
                    self.assertEqual(actual, expected, key)

    def test_every_control_moves_and_each_region_resizes_then_saves_and_loads(self) -> None:
        view = self._view(1.0)
        for _title, _icon, _color, entries in CALIB_GROUPS:
            for entry in entries:
                key, kind, _holder, _fields, *_ = entry
                item_id = f"shape-{key}"
                if kind == "box":
                    coords = (70, 80, 390, 310)
                    shape = {"kind": "box", "id": item_id, "entry": entry,
                             "grips": {}, "grip_label": None, "label": None}
                else:
                    coords = (160, 130, 184, 154)
                    shape = {"kind": "dot", "id": item_id, "entry": entry,
                             "r": 12, "label": None, "tick": None}
                view.canvas.coords(item_id, *coords)
                view.shapes = {key: shape}
                view.sel = key
                before = self._entry_fields(entry, view.cfg)
                view.drag = ("move", 100, 100)
                Calibrator._move(view, SimpleNamespace(x=124, y=118))
                moved = self._entry_fields(entry, view.cfg)
                self.assertNotEqual(moved, before, f"{key} did not move")
                if kind == "box":
                    x0, y0, x1, y1 = view.canvas.coords(item_id)
                    view.drag = ("corner", x1, y1)
                    Calibrator._move(view, SimpleNamespace(x=x1 + 30, y=y1 + 22))
                    resized = self._entry_fields(entry, view.cfg)
                    self.assertGreater(resized[2], moved[2], f"{key} did not resize right")
                    self.assertGreater(resized[3], moved[3], f"{key} did not resize down")

        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "config.json"
            view.cfg.save(path)
            loaded = Config.load(path)
        for _title, _icon, _color, entries in CALIB_GROUPS:
            for entry in entries:
                self.assertEqual(self._entry_fields(entry, loaded),
                                 self._entry_fields(entry, view.cfg), entry[0])

    def test_reset_selected_restores_only_that_coordinate_to_the_shipped_default(self) -> None:
        view = self._view(1.0)
        _title, _icon, _color, entry = _group_of("menu")
        view.sel = "menu"
        view._reviewed = {"menu"}
        view.cfg.shop.menu_left = 0.12
        view.cfg.shop.menu_top = 0.18
        view.cfg.shop.menu_right = 0.44
        view.cfg.shop.menu_bottom = 0.66
        view.redraw = lambda: None
        Calibrator.reset_selected(view)
        defaults = Config().shop
        self.assertEqual((view.cfg.shop.menu_left, view.cfg.shop.menu_top,
                          view.cfg.shop.menu_right, view.cfg.shop.menu_bottom),
                         (defaults.menu_left, defaults.menu_top,
                          defaults.menu_right, defaults.menu_bottom))
        self.assertNotIn("menu", view._reviewed)

    def test_every_optional_colour_sample_uses_the_clicked_pixel_without_touching_coordinates(self) -> None:
        for key, _label, _desc in (item for item in COLOR_ITEMS if item[0] != "fish_tpl"):
            view = self._view(1.0)
            view.pick = key
            view._shot = PILImage.new("RGB", (20, 20), (31, 143, 219))
            view._refresh_color_ui = lambda: None
            view.redraw = lambda: None
            Calibrator._sample_color(view, SimpleNamespace(x=10, y=10))
            self.assertTrue(getattr(view.cfg.colors, f"cap_{key}_on"), key)
            self.assertEqual(getattr(view.cfg.colors, f"cap_{key}_bgr"),
                             (219, 143, 31), key)

        template = self._view(1.0)
        template.pick = "fish_tpl"
        template._shot = PILImage.new("RGB", (20, 20), (31, 143, 219))
        saved = []
        template._save_template = lambda: saved.append(template._tpl_center)
        template._refresh_color_ui = lambda: None
        template.redraw = lambda: None
        Calibrator._sample_color(template, SimpleNamespace(x=8, y=9))
        self.assertEqual(saved, [(8, 9)])

    def test_third_menu_reference_uses_its_own_matching_row_diagram(self) -> None:
        """Keep this guide independent from the legacy full-menu fallback."""
        self.assertNotIn("menu_item3", CALIB_IMAGE_ALIASES)
        path = ASSETS / "calib" / "menu_item3.png"
        self.assertTrue(path.is_file())

    def test_buying_bait_click_references_have_independent_slots(self) -> None:
        """User-provided Craft-window guides must never silently share an image."""
        expected_slots = {
            "craft_plus": "craft_plus",
            "craft_button": "craft_button",
            "craft_close": "craft_close",
        }
        entries = {
            entry[0]: entry
            for _title, _icon, _color, group_entries in CALIB_GROUPS
            for entry in group_entries
            if entry[0] in expected_slots
        }
        for key, slot in expected_slots.items():
            self.assertNotIn(key, CALIB_IMAGE_ALIASES)
            self.assertEqual(entries[key][-1], slot)

    def test_optional_craft_colour_sample_has_its_own_image_slot(self) -> None:
        self.assertNotIn("craft", CALIB_IMAGE_ALIASES)

    def test_review_checklists_survive_reshoot_state_but_reset_in_a_new_window(self) -> None:
        view = self._view(1.0)
        view._core_keys = ("menu", "craft_button")
        Calibrator._record_review(view, "menu")
        Calibrator._record_review(view, "craft")
        saved_session = Calibrator._review_snapshot(view)
        Calibrator._restore_review_snapshot(view, saved_session)
        self.assertEqual(view._reviewed, {"menu"})
        self.assertEqual(view._color_reviewed, {"craft"})

        new_window = self._view(1.0)
        self.assertEqual(new_window._reviewed, set())
        self.assertEqual(new_window._color_reviewed, set())

    def test_preparation_guide_preserves_the_unsaved_form_without_saving_config(self) -> None:
        app = object.__new__(App)
        app._page = "form"
        app.v_npc = SimpleNamespace(get=lambda: "Fisherman")
        app.v_amount = SimpleNamespace(get=lambda: "90")
        app.v_rod = SimpleNamespace(get=lambda: "5")
        app.v_sell = SimpleNamespace(get=lambda: "1")
        app.v_bait = SimpleNamespace(get=lambda: "80")
        app.v_slow = SimpleNamespace(get=lambda: True)
        app.v_fast = SimpleNamespace(get=lambda: False)
        opened = []
        app._build_checklist = lambda: opened.append(True)

        App._open_preparation(app)

        self.assertEqual(opened, [True])
        self.assertEqual(app._form_draft, {
            "npc": "Fisherman", "amount": "90", "rod": "5", "sell": "1",
            "bait": "80", "slow": True, "fast": False,
        })

    def test_preparation_details_toggle_without_idle_animation(self) -> None:
        class Body:
            def __init__(self):
                self.packed = False
                self.propagates = []

            def pack(self, **_kwargs):
                self.packed = True

            def pack_forget(self):
                self.packed = False

            def pack_propagate(self, value):
                self.propagates.append(value)

            def configure(self, **_kwargs):
                pass

            @staticmethod
            def winfo_reqheight():
                return 120

            @staticmethod
            def winfo_height():
                return 120

        class Arrow:
            def __init__(self):
                self.text = ""

            def configure(self, **kwargs):
                self.text = kwargs.get("text", self.text)

        item = object.__new__(PreparationItem)
        item._anim_job = None
        item._open = False
        item.body = Body()
        item.arrow_button = Arrow()
        item.configure = lambda **_kwargs: None
        item.update_idletasks = lambda: None

        PreparationItem.toggle(item, animate=False)
        self.assertTrue(item._open)
        self.assertTrue(item.body.packed)
        self.assertEqual(item.arrow_button.text, "⌃")
        PreparationItem.toggle(item, animate=False)
        self.assertFalse(item._open)
        self.assertFalse(item.body.packed)
        self.assertEqual(item.arrow_button.text, "⌄")

    def test_preparation_details_cancel_an_inflight_animation_before_toggling(self) -> None:
        class Body:
            def pack(self, **_kwargs):
                pass

            def pack_forget(self):
                pass

            def pack_propagate(self, _value):
                pass

            def configure(self, **_kwargs):
                pass

            @staticmethod
            def winfo_reqheight():
                return 120

            @staticmethod
            def winfo_height():
                return 120

        class Arrow:
            def configure(self, **_kwargs):
                pass

        item = object.__new__(PreparationItem)
        item._anim_job = "old-animation"
        item._open = False
        item.body = Body()
        item.arrow_button = Arrow()
        item.configure = lambda **_kwargs: None
        item.update_idletasks = lambda: None
        cancelled = []
        item.after_cancel = lambda job: cancelled.append(job)

        PreparationItem.toggle(item, animate=False)

        self.assertEqual(cancelled, ["old-animation"])
        self.assertIsNone(item._anim_job)

    def test_npc_simulation_geometry_keeps_every_marker_on_its_circle(self) -> None:
        center = (160.0, 120.0)
        for angle in (-math.pi / 2, -math.pi / 4, 0.0):
            point = NPCPositionSimulation._point(*center, 72.0, angle)
            self.assertAlmostEqual(math.dist(center, point), 72.0, places=6)

    def test_npc_simulation_stops_after_its_finite_playback(self) -> None:
        class Button:
            def __init__(self):
                self.text = ""

            def configure(self, **kwargs):
                self.text = kwargs.get("text", self.text)

        sim = object.__new__(NPCPositionSimulation)
        sim._playing = True
        sim._progress = 0.0
        sim._started_at = time.perf_counter() - 1.7
        sim.play_button = Button()
        sim.winfo_exists = lambda: True
        sim._draw_scene = lambda: None
        sim.after = lambda *_args: self.fail("finished simulation must not schedule another frame")

        NPCPositionSimulation._animate(sim)

        self.assertFalse(sim._playing)
        self.assertEqual(sim._progress, 1.0)
        self.assertEqual(sim.play_button.text, "Replay")

    def test_npc_simulation_does_not_override_customtkinter_draw_hook(self) -> None:
        self.assertNotIn("_draw", NPCPositionSimulation.__dict__)
        self.assertIn("_draw_scene", NPCPositionSimulation.__dict__)

    def test_finish_from_preparation_commits_the_unsaved_setup_draft(self) -> None:
        app = object.__new__(App)
        app._page = "checklist"
        app._form_draft = {
            "npc": "Fisherman", "amount": "90", "rod": "5", "sell": "1",
            "bait": "12", "slow": True, "fast": False,
        }
        app.cfg = Config()
        saved = []
        app.cfg.save = lambda: saved.append(True)
        app._prep_error = SimpleNamespace(configure=lambda **_kwargs: self.fail("valid draft must not show an error"))
        opened = []
        app._build_runner = lambda: opened.append(True)

        App._finish_preparation(app)

        self.assertEqual(saved, [True])
        self.assertEqual(opened, [True])
        self.assertEqual(app.cfg.shop.npc, "fisherman")
        self.assertEqual(app.cfg.shop.bait_per_purchase, 90)
        self.assertEqual(app.cfg.sell.every, 1)
        self.assertTrue(app.cfg.sell.enabled)
        self.assertEqual(app.bait, 12)
        self.assertIsNone(app._form_draft)

    def test_stop_control_does_not_close_the_app_or_restart_the_engine(self) -> None:
        class Engine:
            running = True

            def __init__(self):
                self.stops = 0

            def stop(self):
                self.stops += 1
                self.running = False

        class Widget:
            def __init__(self):
                self.calls = []

            def configure(self, **kwargs):
                self.calls.append(kwargs)

        app = object.__new__(App)
        app.engine = Engine()
        app.worker = None
        app.badge = Widget()
        app.btn = Widget()
        app.stop_btn = Widget()
        logged = []
        app._log = lambda *parts: logged.append(parts)

        App._stop(app)

        self.assertEqual(app.engine.stops, 1)
        self.assertEqual(logged, [("[stop] requested by F4 / Stop control",)])
        self.assertNotIn("_quit", app.__dict__)

    def test_runner_log_is_bounded_instead_of_growing_without_limit(self) -> None:
        class Logbox:
            def __init__(self):
                self.deleted = []

            @staticmethod
            def winfo_exists():
                return True

            def insert(self, *_args):
                pass

            def delete(self, *args):
                self.deleted.append(args)

            def see(self, *_args):
                pass

        app = object.__new__(App)
        app.logbox = Logbox()
        app._log_lines = 1200
        app._log_limit = 1200
        app.after = lambda _delay, callback: callback()

        App._log(app, "one more line")

        self.assertEqual(app._log_lines, 1200)
        self.assertEqual(app.logbox.deleted, [("1.0", "2.0")])

    def test_checkbox_calls_use_only_cross_version_customtkinter_options(self) -> None:
        """CTkCheckBox has no progress_color in the supported CTk releases."""
        source = Path(__file__).resolve().parents[1] / "easy_run.py"
        tree = ast.parse(source.read_text(encoding="utf-8"))
        checkbox_calls = [
            node for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "CTkCheckBox"
        ]
        self.assertGreater(len(checkbox_calls), 0)
        for call in checkbox_calls:
            self.assertNotIn("progress_color", {kw.arg for kw in call.keywords})



if __name__ == "__main__":
    unittest.main()
