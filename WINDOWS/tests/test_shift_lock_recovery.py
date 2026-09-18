"""Shift Lock and restart regressions; no screen capture or real input."""
from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys
import tempfile
import threading
from types import SimpleNamespace as NS, ModuleType
import unittest
from unittest import mock

from bloxfish import engine as engine_mod, inputs, shop
from bloxfish.capture import Rect
from bloxfish.config import Config
from easy_run import App


def fake_engine(window=Rect(0, 0, 1920, 1080)):
    # Bypass construction so tests never open capture/input backends.
    engine = object.__new__(engine_mod.FishingEngine)
    engine.cfg = Config()
    engine.window = window
    engine.mouse = mock.Mock(position=mock.Mock(return_value=(960, 540)))
    engine.keyboard = mock.Mock()
    engine.log = mock.Mock()
    engine._sleep = mock.Mock()
    engine._run_lock = threading.Lock()
    engine.running = False
    engine._stop = False
    engine._shift_lock = False
    engine._shift_lock_verified = False
    engine.stats = engine_mod.Stats()
    engine._cycle = mock.Mock(side_effect=engine.stop)
    return engine


class ShiftLockGeometryTests(unittest.TestCase):
    def test_snap_is_independent_of_interact_and_desktop_geometry(self):
        windows = [Rect(0, 0, 1920, 1080), Rect(0, 0, 2560, 1392),
                   Rect(8, 8, 1904, 1064), Rect(-2560, -120, 2560, 1440),
                   Rect(320, 180, 3840, 2160), Rect(-1100, 50, 1001, 751)]
        for platform in ('windows', 'sober-x11'):
            for window in windows:
                for target in ((.4979, .5107), (.4979, .67), (.8, .67)):
                    with self.subTest(platform=platform, window=window, target=target), \
                         mock.patch.dict(os.environ, {'BLOXFISH_PLATFORM': platform}):
                        engine = fake_engine(window)
                        engine.cfg.shop.center = target
                        centre = (window.left + round(window.width / 2),
                                  window.top + round(window.height / 2))
                        engine.mouse.position.side_effect = [
                            (window.left + int(window.width * .8),
                             window.top + int(window.height * .8)), centre, centre]
                        self.assertTrue(shop.set_shift_lock(engine, True))
                        self.assertTrue(shop.fishing_shift_lock_ready(engine))
                        engine.keyboard.tap.assert_called_once_with(shop.SC_LSHIFT, .10)
                        engine._sleep.assert_called_once_with(engine.cfg.shop.after_shift)
                        engine.mouse.move_to.assert_not_called()
                        self.assertEqual(engine.cfg.shop.center, target)
                        self.assertEqual(shop._shift_lock_geometry(engine)[2],
                                         max(24, int(min(window.width, window.height) * .03)))

    def test_failed_or_ambiguous_observations_refuse_to_fish(self):
        for observations in ([None, (960, 540)], [(1450, 760), None],
                             [(960, 540), (960, 540)],
                             [(1450, 760), (1450, 760)],
                             [OSError('query failed'), (960, 540)],
                             [(1450, 760), OSError('query failed')]):
            with self.subTest(observations=observations):
                engine = fake_engine()
                engine.mouse.position.side_effect = observations
                self.assertFalse(shop.set_shift_lock(engine, True))
                self.assertFalse(shop.fishing_shift_lock_ready(engine))
                self.assertFalse(engine._shift_lock)
                engine.keyboard.tap.assert_called_once_with(shop.SC_LSHIFT, .10)
                engine.mouse.move_to.assert_not_called()
                message = engine.log.call_args.args[0]
                for detail in ('Could not verify Shift Lock', 'before=', 'after=',
                               'expected=(960, 540)', 'tolerance=32px'):
                    self.assertIn(detail, message)

    def test_missing_cursor_method_fails_closed(self):
        engine = fake_engine()
        engine.mouse = NS()
        self.assertFalse(shop.set_shift_lock(engine, True))
        self.assertFalse(shop._shift_lock_centered(engine))

    def test_precast_rechecks_cursor_and_does_not_cast_if_lost(self):
        engine = fake_engine()
        engine.mouse.position.side_effect = [(1450, 760), (960, 540), (1450, 760)]
        self.assertTrue(shop.set_shift_lock(engine, True))
        self.assertFalse(engine._do_cast())
        self.assertTrue(engine._stop)
        self.assertFalse(engine._shift_lock_verified)
        engine.mouse.press.assert_not_called()

    def test_cursor_query_failure_after_verification_fails_closed(self):
        engine = fake_engine()
        engine.mouse.position.side_effect = [(1450, 760), (960, 540), OSError('lost')]
        self.assertTrue(shop.set_shift_lock(engine, True))
        self.assertFalse(shop.fishing_shift_lock_ready(engine))

    def test_saved_interact_is_still_the_click_target(self):
        engine = fake_engine(Rect(-1920, 20, 1920, 1080))
        engine.cfg.shop.center = (.72, .67)
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / 'config.json'
            engine.cfg.save(config)
            engine.cfg = Config.load(config)
        with mock.patch.object(shop, 'wait_popup_clear'), \
             mock.patch.object(shop, 'in_dialogue', return_value=False), \
             mock.patch.object(shop, 'set_rod'), \
             mock.patch.object(shop, 'wait_for_menu_page', return_value=True):
            self.assertTrue(shop.open_npc_dialogue(engine))
        self.assertEqual(engine.cfg.shop.center, (.72, .67))
        engine.mouse.click_at.assert_called_once_with(-538, 744)


class CursorBackendTests(unittest.TestCase):
    def test_windows_query_failure_is_not_a_zero_position(self):
        with mock.patch.object(inputs, '_user32') as user32:
            user32.GetCursorPos.return_value = 0
            with self.assertRaises(OSError):
                inputs.Mouse().position()

    def test_linux_query_failure_closes_display_and_never_creates_input(self):
        evdev = ModuleType('evdev')
        evdev.UInput, evdev.AbsInfo, evdev.ecodes = mock.Mock(), mock.Mock(), mock.Mock()
        xlib = ModuleType('Xlib')
        display = mock.Mock()
        display.screen.side_effect = RuntimeError('X11 query failed')
        xlib.display = NS(Display=mock.Mock(return_value=display))
        path = Path(__file__).resolve().parents[2] / 'LINUX' / 'inputs_linux.py'
        spec = importlib.util.spec_from_file_location('_test_linux_cursor', path)
        backend = importlib.util.module_from_spec(spec)
        with mock.patch.dict(sys.modules, {'evdev': evdev, 'Xlib': xlib}):
            spec.loader.exec_module(backend)
            mouse = object.__new__(backend.Mouse)
            with self.assertRaises(OSError):
                mouse.position()
        display.close.assert_called_once()
        evdev.UInput.assert_not_called()


class RunRecoveryTests(unittest.TestCase):
    def assert_idle(self, engine):
        self.assertFalse(engine.running)
        self.assertFalse(engine._run_lock.locked())
        self.assertFalse(engine._shift_lock_verified)
        engine.mouse.release.assert_called()

    def test_rejected_start_can_restart_and_run_a_cycle(self):
        for anchor_enabled in (True, False):
            with self.subTest(anchor_enabled=anchor_enabled):
                engine = fake_engine()
                engine.cfg.shop.enter_stance_on_start = anchor_enabled
                helper = 'establish_fishing_anchor' if anchor_enabled else 'enter_fishing_stance'
                with mock.patch.object(engine_mod, 'focus_game_window', return_value=True), \
                     mock.patch.object(shop, helper, side_effect=[False, True]):
                    engine.run()
                    self.assert_idle(engine)
                    engine._cycle.assert_not_called()
                    engine.run()
                    self.assert_idle(engine)
                    engine._cycle.assert_called_once()

    def test_startup_exception_and_cancellation_clean_up(self):
        for failure in ('exception', 'cancel'):
            with self.subTest(failure=failure):
                engine = fake_engine()
                def startup(_engine):
                    if failure == 'exception':
                        raise RuntimeError('startup failed')
                    engine.stop()  # same entry point as the GUI's F4 control
                    return True
                with mock.patch.object(engine_mod, 'focus_game_window', return_value=True), \
                     mock.patch.object(shop, 'establish_fishing_anchor', side_effect=startup):
                    if failure == 'exception':
                        with self.assertRaises(RuntimeError):
                            engine.run()
                    else:
                        engine.run()
                self.assert_idle(engine)
                engine._cycle.assert_not_called()

    def test_real_anchor_shift_failure_cleans_up_and_next_attempt_succeeds(self):
        engine = fake_engine()
        engine.cfg.shop.center = (.4979, .67)
        with mock.patch.object(engine_mod, 'focus_game_window', return_value=True), \
             mock.patch.object(shop, 'open_npc_dialogue', return_value=True), \
             mock.patch.object(shop, 'leave_dialogue', return_value=True), \
             mock.patch.object(shop, 'set_rod'):
            engine.mouse.position.side_effect = [(1450, 760), (1450, 760)]
            engine.run()
            self.assert_idle(engine)
            engine._cycle.assert_not_called()
            engine.mouse.position.side_effect = [(1450, 760), (960, 540)]
            engine.run()
            self.assert_idle(engine)
            engine._cycle.assert_called_once()

    def test_cleanup_error_still_resets_state_and_releases_lock(self):
        engine = fake_engine()
        engine.mouse.release.side_effect = OSError('release failed')
        with mock.patch.object(engine, '_run_locked') as run:
            run.side_effect = lambda: setattr(engine, 'running', True)
            with self.assertRaises(OSError):
                engine.run()
        self.assertFalse(engine.running)
        self.assertFalse(engine._run_lock.locked())

    def test_duplicate_start_cannot_clean_up_the_active_worker(self):
        engine = fake_engine()
        engine.running = engine._shift_lock_verified = True
        engine._run_lock.acquire()
        try:
            engine.run()
            self.assertTrue(engine.running)
            self.assertTrue(engine._shift_lock_verified)
            self.assertTrue(engine._run_lock.locked())
            engine.mouse.release.assert_not_called()
        finally:
            engine._run_lock.release()

    def test_gui_returns_to_idle_and_next_start_launches_worker(self):
        engine = fake_engine()
        app = NS(engine=engine, worker=None, badge=mock.Mock(), btn=mock.Mock(),
                 stop_btn=mock.Mock(), _log=mock.Mock(), _stop=mock.Mock(),
                 _show_started=mock.Mock(), after=lambda _delay, fn: fn())
        app._run = lambda: App._run(app)
        with mock.patch.object(engine_mod, 'focus_game_window', return_value=True), \
             mock.patch.object(shop, 'establish_fishing_anchor', return_value=False):
            App._run(app)
        self.assert_idle(engine)
        self.assertEqual(app.badge.configure.call_args.kwargs['text'], '●  IDLE')
        with mock.patch('easy_run.threading.Thread') as worker:
            App._toggle(app)
            worker.return_value.start.assert_called_once()
        app._stop.assert_not_called()


if __name__ == '__main__':
    unittest.main()
