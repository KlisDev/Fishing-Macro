from __future__ import annotations

import ast
import importlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock


WINDOWS = Path(__file__).resolve().parents[1]
PROJECT = WINDOWS.parent
LINUX = PROJECT / "LINUX"
for path in (str(WINDOWS), str(LINUX)):
    if path not in sys.path:
        sys.path.insert(0, path)

from bloxfish import engine as engine_mod
from bloxfish import shop as shop_mod
from bloxfish.config import Config
from bloxfish.debug import DebugBus
from linux_runtime import linux_environment
from preflight_linux import validate_sober_x11
from find_window_linux import _find_sober_window, focus_game_window
from x11_overlay import X11OverlayRenderer


class LinuxProfileTests(unittest.TestCase):
    def test_linux_context_isolated_from_the_windows_runtime(self) -> None:
        config = LINUX / "profiles" / "sober.json"
        env = linux_environment(config)
        self.assertEqual(Path(env["BLOXFISH_CONFIG_PATH"]), config.resolve())
        self.assertEqual(Path(env["BLOXFISH_RUNTIME_ROOT"]), config.parent.resolve())
        self.assertEqual(Path(env["BLOXFISH_GUI_ASSETS"]), LINUX / "assets" / "gui")
        self.assertEqual(env["BLOXFISH_PLATFORM"], "sober-x11")

    def test_config_module_honours_an_explicit_linux_profile_before_import(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = Path(td) / "sober.json"
            code = (
                "import json,sys; sys.path.insert(0,sys.argv[1]); "
                "import bloxfish.config as c; "
                "print(json.dumps({'config':str(c.CONFIG_PATH),'root':str(c.RUNTIME_ROOT)}))"
            )
            env = os.environ.copy()
            env.update(linux_environment(config))
            result = subprocess.run(
                [sys.executable, "-c", code, str(WINDOWS)], env=env,
                capture_output=True, text=True, check=True)
            reported = json.loads(result.stdout)
        self.assertEqual(Path(reported["config"]), config.resolve())
        self.assertEqual(Path(reported["root"]), config.parent.resolve())

    def test_linux_assets_are_an_independent_complete_copy(self) -> None:
        windows_assets = WINDOWS / "assets" / "gui"
        linux_assets = LINUX / "assets" / "gui"
        source = {path.relative_to(windows_assets) for path in windows_assets.rglob("*")
                  if path.is_file()}
        copied = {path.relative_to(linux_assets) for path in linux_assets.rglob("*")
                  if path.is_file()}
        self.assertTrue(source)
        self.assertTrue(source <= copied)

    def test_engine_diagnostics_use_the_active_config_parent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            from bloxfish import config as config_mod
            custom_config = Path(td) / "sober.json"
            fake = SimpleNamespace(cfg=SimpleNamespace(capture_dir=None))
            with mock.patch.object(config_mod, "CONFIG_PATH", custom_config):
                output = engine_mod.FishingEngine._out_dir(fake, "diag")
            self.assertEqual(output, Path(td) / "diag")
            self.assertTrue(output.is_dir())

    def test_linux_imports_current_shared_gui_fixes_with_its_own_profile(self) -> None:
        code = (
            "import sys,json; sys.path.insert(0,sys.argv[1]); "
            "from linux_runtime import configure_linux_runtime; configure_linux_runtime(); "
            "import easy_run; from bloxfish import config,engine,shop,vision,routes,terminal_setup; "
            "import numpy as np; from types import SimpleNamespace; "
            "from bloxfish.capture import Rect; "
            "scene=np.full((350,420,3),100,dtype=np.uint8); "
            "scene[50:80,20:400]=150; scene[170:200,20:400]=150; "
            "dark=scene.copy(); dark[50:90,:]=30; dark[170:210,:]=30; "
            "banner=np.zeros((151,384,3),dtype=np.uint8); "
            "banner[130:151,88:299]=(30,190,225); "
            "cfg=config.Config(); cfg.shop.menu_left=cfg.shop.menu_top=0.; "
            "cfg.shop.menu_right=cfg.shop.menu_bottom=1.; "
            "probe=SimpleNamespace(cfg=cfg,window=Rect(0,0,420,350),"
            "screen=SimpleNamespace(grab=lambda _rect:scene)); "
            "print(json.dumps({'gui':easy_run.__file__,'version':config.VERSION,"
            "'assets':str(easy_run.ASSETS),'config':str(config.CONFIG_PATH),"
            "'engine_shop':engine.shop_mod.__file__,"
            "'sample_enabled':cfg.colors.cap_dialogue_on,"
            "'obsolete_count':vision.menu_button_count(scene),"
            "'dark_panel_rows':len(vision.find_falling_menu_buttons(dark)),"
            "'banner_is_craft':vision.craft_window_open(banner),"
            "'current_count':shop.menu_items(probe),'dialogue':shop.in_dialogue(probe),"
            "'signature':shop._menu_page_signature(probe),"
            "'modules':[m.__file__ for m in (engine,shop,vision,routes,terminal_setup)]}))"
        )
        result = subprocess.run([sys.executable, '-B', '-c', code, str(LINUX)],
                                capture_output=True, text=True, check=True)
        report = json.loads(result.stdout)
        from bloxfish.config import VERSION
        self.assertEqual(report['version'], VERSION)
        self.assertEqual(Path(report['gui']), WINDOWS / 'easy_run.py')
        self.assertEqual(Path(report['assets']), LINUX / 'assets' / 'gui')
        self.assertEqual(Path(report['config']), LINUX / 'config.json')
        self.assertEqual(Path(report['engine_shop']), WINDOWS / 'bloxfish' / 'shop.py')
        self.assertFalse(report['sample_enabled'])
        self.assertGreaterEqual(report['obsolete_count'], 2)
        self.assertEqual(report['dark_panel_rows'], 0)
        self.assertFalse(report['banner_is_craft'])
        self.assertEqual(report['current_count'], 0)
        self.assertFalse(report['dialogue'])
        self.assertEqual(report['signature'], [])
        for source in report['modules']:
            self.assertEqual(Path(source).parent, WINDOWS / 'bloxfish')


class LinuxTerminalSetupTests(unittest.TestCase):
    """Exercise the real launcher and shared prompts with fake game devices."""

    def launch(self, replies, *, dev=False, live_worker=False):
        import run_linux
        cfg = Config()
        engine = SimpleNamespace(cfg=cfg, bait_count=None, running=False,
                                 safety_stopped=False, run=mock.Mock(),
                                 stop=mock.Mock(), close=mock.Mock())
        worker = mock.Mock()
        worker.is_alive.return_value = live_worker
        if live_worker:
            engine.run.side_effect = lambda: setattr(engine, 'running', True)
        worker.start.side_effect = engine.run
        native_open = open
        opened = []

        def tracked_open(*args, **kwargs):
            file = native_open(*args, **kwargs)
            opened.append(file)
            return file

        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'sober.json'
            argv = ['run_linux.py', '--now', '--config', str(profile)]
            if dev:
                argv.append('--dev')
            with mock.patch.object(run_linux.sys, 'platform', 'linux'), \
                    mock.patch.object(run_linux.sys, 'argv', argv), \
                    mock.patch('linux_runtime.configure_linux_runtime') as context, \
                    mock.patch('preflight_linux.run_preflight', return_value=True), \
                    mock.patch.object(run_linux, '_patch_backends'), \
                    mock.patch('bloxfish.config.CONFIG_PATH', profile), \
                    mock.patch.object(Config, 'load', return_value=cfg), \
                    mock.patch.object(Config, 'save') as save, \
                    mock.patch.object(engine_mod, 'FishingEngine', return_value=engine), \
                    mock.patch.object(run_linux.threading, 'Thread', return_value=worker), \
                    mock.patch.object(run_linux.time, 'sleep',
                                      side_effect=KeyboardInterrupt if live_worker else None), \
                    mock.patch.object(run_linux, '_install_hotkeys') as hotkeys, \
                    mock.patch('builtins.open', side_effect=tracked_open), \
                    mock.patch('builtins.input', side_effect=replies), \
                    mock.patch('builtins.print') as output:
                result = run_linux.main()
                context.assert_called_once_with(str(profile))
                save.assert_not_called()
                hotkeys.assert_not_called()  # --now still requires setup
                lines = '\n'.join(str(call.args[0]) for call in output.call_args_list if call.args)
            captures = list(Path(folder).glob('capture_*/run.log'))
            if dev:
                self.assertEqual(len(captures), 1)
                self.assertFalse(profile.exists())  # no configuration writes
                self.assertEqual(len(opened), 1)
                self.assertTrue(opened[0].closed)
        self.assertEqual(result, 0)
        engine.close.assert_called_once()
        return cfg, engine, worker, lines

    def test_fish_only_answers_reach_linux_engine_before_now_start(self) -> None:
        cfg, engine, worker, lines = self.launch(['n', '5', '80', ''])
        from bloxfish.routes import npc_route_enabled
        self.assertEqual(cfg.shop.npc, 'none')
        self.assertEqual(cfg.rod_slot, '5')
        self.assertEqual(engine.bait_count, 80)
        self.assertFalse(npc_route_enabled(cfg, engine.bait_count))
        engine.run.assert_called_once()
        worker.start.assert_called_once()
        self.assertIn('Fish-only: choose your fishing position', lines)
        self.assertNotIn('will restock at', lines)

    def test_linux_buying_gets_the_initial_bait_count_and_purchase_settings(self) -> None:
        cfg, engine, _worker, lines = self.launch(['f', '90', '5', '0', '80', ''])
        from bloxfish.routes import buying_enabled, selling_enabled
        self.assertEqual(cfg.shop.bait_per_purchase, 90)
        self.assertEqual(engine.bait_count, 80)
        self.assertTrue(buying_enabled(cfg, engine.bait_count))
        self.assertFalse(selling_enabled(cfg))
        engine.run.assert_called_once()
        self.assertIn('will restock at', lines)

    def test_blank_count_with_no_selling_is_manual_fishing_on_linux(self) -> None:
        cfg, engine, _worker, lines = self.launch(['f', '', '5', '0', '', ''])
        from bloxfish.routes import npc_route_enabled
        self.assertIsNone(engine.bait_count)
        self.assertFalse(npc_route_enabled(cfg, engine.bait_count))
        self.assertIn('No automatic toggles or checks', lines)

    def test_linux_setup_abort_closes_resources_without_arming_input(self) -> None:
        _cfg, engine, worker, _lines = self.launch(['n', '5', '80', 'n'], dev=True)
        engine.run.assert_not_called()
        engine.stop.assert_not_called()
        worker.start.assert_not_called()

    def test_linux_terminal_waits_for_the_worker_before_closing_devices(self) -> None:
        _cfg, engine, worker, _lines = self.launch(['n', '5', '80', ''], live_worker=True)
        engine.stop.assert_called_once()
        worker.join.assert_called_once_with(timeout=3.0)


class LinuxPreflightTests(unittest.TestCase):
    @staticmethod
    def _imports(_name: str):
        return object()

    def test_x11_sober_preflight_passes_without_creating_input(self) -> None:
        report = validate_sober_x11(
            gui=True, environ={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
            exists=lambda _path: True, access=lambda _path, _mode: True,
            importer=self._imports, frame_probe=lambda _title: (True, False))
        self.assertTrue(report.ok)
        self.assertEqual(report.warnings, [])

    def test_wayland_and_missing_uinput_are_rejected_before_window_probe(self) -> None:
        probe = mock.Mock(return_value=(True, False))
        report = validate_sober_x11(
            environ={"XDG_SESSION_TYPE": "wayland", "DISPLAY": ":0"},
            exists=lambda _path: False, access=lambda _path, _mode: False,
            importer=self._imports, frame_probe=probe)
        self.assertFalse(report.ok)
        self.assertTrue(any("X11" in item for item in report.errors))
        self.assertTrue(any("uinput" in item for item in report.errors))
        probe.assert_not_called()

    def test_blank_sober_capture_warns_but_does_not_block(self) -> None:
        report = validate_sober_x11(
            environ={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
            exists=lambda _path: True, access=lambda _path, _mode: True,
            importer=self._imports, frame_probe=lambda _title: (True, True))
        self.assertTrue(report.ok)
        self.assertTrue(any("black" in item for item in report.warnings))

    def test_missing_sober_window_is_an_actionable_error(self) -> None:
        report = validate_sober_x11(
            environ={"XDG_SESSION_TYPE": "x11", "DISPLAY": ":0"},
            exists=lambda _path: True, access=lambda _path, _mode: True,
            importer=self._imports, frame_probe=lambda _title: (False, False))
        self.assertFalse(report.ok)
        self.assertTrue(any("No Sober window" in item for item in report.errors))


class LinuxBackendTests(unittest.TestCase):
    def test_sober_profile_enables_the_shared_interaction_safety_guard(self) -> None:
        with mock.patch.dict(os.environ, {"BLOXFISH_PLATFORM": "sober-x11"}):
            self.assertTrue(shop_mod.interaction_safety_guard())
            self.assertTrue(shop_mod.windows_interaction_guard())

    def test_sober_uses_the_confirmed_coherent_menu_filter(self) -> None:
        """The confirmed Windows false-positive fix is now part of Sober parity."""
        roi = SimpleNamespace(left=1324, top=439, width=365, height=333)
        marks = [SimpleNamespace(x=255, y=56), SimpleNamespace(x=180, y=83)]
        engine = SimpleNamespace(screen=SimpleNamespace(grab=lambda _rect: None))
        with mock.patch.dict(os.environ, {"BLOXFISH_PLATFORM": "sober-x11"}), \
            mock.patch.object(shop_mod, "_menu_roi", return_value=roi), \
             mock.patch.object(shop_mod, "find_falling_menu_buttons", return_value=marks):
            self.assertEqual(shop_mod._falling_menu_buttons(engine), [])

    def test_sober_refuses_a_free_cursor_when_shift_lock_does_not_snap(self) -> None:
        class Keyboard:
            def __init__(self):
                self.taps = []

            def tap(self, *args):
                self.taps.append(args)

        class Mouse:
            def position(self):
                return 1450, 760

        class Engine:
            cfg = Config()
            window = SimpleNamespace(left=0, top=0, width=1920, height=1080)

            def __init__(self):
                self.keyboard = Keyboard()
                self.mouse = Mouse()
                self._shift_lock = False
                self._shift_lock_verified = False
                self.lines = []

            def _sleep(self, _seconds):
                pass

            def log(self, line):
                self.lines.append(line)

        engine = Engine()
        with mock.patch.dict(os.environ, {"BLOXFISH_PLATFORM": "sober-x11"}):
            self.assertFalse(shop_mod.set_shift_lock(engine, True))

        self.assertEqual(engine.keyboard.taps, [(shop_mod.SC_LSHIFT, 0.10)])
        self.assertFalse(engine._shift_lock)
        self.assertTrue(any("refusing to fish" in line for line in engine.lines))

    def test_sober_finder_prefers_the_largest_visible_sober_window(self) -> None:
        class Window:
            def __init__(self, ident, x, y, w, h, name="", cls=None, children=()):
                self.id, self._geo = ident, (x, y, w, h)
                self._name, self._cls, self._children = name, cls, list(children)

            def query_tree(self):
                return SimpleNamespace(children=self._children)

            def get_wm_class(self):
                return self._cls

            def get_wm_name(self):
                return self._name

            def get_geometry(self):
                return SimpleNamespace(width=self._geo[2], height=self._geo[3])

            def translate_coords(self, _root, _x, _y):
                return SimpleNamespace(x=self._geo[0], y=self._geo[1])

        small = Window(2, 20, 30, 800, 600, "Sober", ("sober", "Sober"))
        large = Window(3, 40, 50, 1600, 900, "Roblox through Sober", ("sober", "Sober"))
        root = Window(1, 0, 0, 1920, 1080, children=(small, large))
        display = SimpleNamespace(screen=lambda: SimpleNamespace(root=root))

        found_root, found = _find_sober_window(display, "Roblox")

        self.assertIs(found_root, root)
        self.assertIs(found, large)

    def test_backend_patch_replaces_every_engine_platform_seam(self) -> None:
        backend = importlib.import_module("_backend")
        import bloxfish.capture as capture
        import bloxfish.debug as debug_mod

        fake_mouse = type("Mouse", (), {})
        fake_keyboard = type("Keyboard", (), {})
        fake_window = lambda *_args: None
        fake_focus = lambda *_args: True
        fake_overlay = lambda _parent: object()
        inputs = types.ModuleType("inputs_linux")
        inputs.Mouse, inputs.Keyboard = fake_mouse, fake_keyboard
        finder = types.ModuleType("find_window_linux")
        finder.find_game_window, finder.focus_game_window = fake_window, fake_focus
        overlay = types.ModuleType("x11_overlay")
        overlay.create_x11_overlay = fake_overlay
        original = (engine_mod.Mouse, engine_mod.Keyboard, engine_mod.find_game_window,
                    engine_mod.focus_game_window, capture.find_game_window,
                    debug_mod.DEBUG._overlay_factory)
        try:
            with mock.patch.dict(sys.modules, {
                "inputs_linux": inputs, "find_window_linux": finder,
                "x11_overlay": overlay,
            }):
                backend.patch()
            self.assertIs(engine_mod.Mouse, fake_mouse)
            self.assertIs(engine_mod.Keyboard, fake_keyboard)
            self.assertIs(engine_mod.find_game_window, fake_window)
            self.assertIs(engine_mod.focus_game_window, fake_focus)
            self.assertIs(capture.find_game_window, fake_window)
            self.assertIs(debug_mod.DEBUG._overlay_factory, fake_overlay)
        finally:
            (engine_mod.Mouse, engine_mod.Keyboard, engine_mod.find_game_window,
             engine_mod.focus_game_window, capture.find_game_window,
             debug_mod.DEBUG._overlay_factory) = original

    def test_focus_request_requires_x11_focus_confirmation(self) -> None:
        window = SimpleNamespace(id=22, configure=mock.Mock(),
                                 set_input_focus=mock.Mock())
        display = SimpleNamespace(sync=mock.Mock(), close=mock.Mock(),
                                  get_input_focus=lambda: SimpleNamespace(focus=window))
        x = SimpleNamespace(Above=1, RevertToParent=2, CurrentTime=3)
        with mock.patch("find_window_linux._find_sober_window", return_value=(object(), window)):
            focused = focus_game_window("Sober", display_factory=lambda: display,
                                        x_constants=x)
        self.assertTrue(focused)
        window.configure.assert_called_once_with(stack_mode=x.Above)
        window.set_input_focus.assert_called_once_with(x.RevertToParent, x.CurrentTime)
        display.close.assert_called_once()

    def test_focus_request_fails_closed_when_x11_focus_stays_elsewhere(self) -> None:
        window = SimpleNamespace(id=22, configure=mock.Mock(),
                                 set_input_focus=mock.Mock())
        other = SimpleNamespace(id=99)
        display = SimpleNamespace(sync=mock.Mock(), close=mock.Mock(),
                                  get_input_focus=lambda: SimpleNamespace(focus=other))
        x = SimpleNamespace(Above=1, RevertToParent=2, CurrentTime=3)
        ticks = iter((0.0, 0.4))
        with mock.patch("find_window_linux._find_sober_window", return_value=(object(), window)):
            focused = focus_game_window("Sober", display_factory=lambda: display,
                                        x_constants=x, now=lambda: next(ticks),
                                        pause=lambda _seconds: None)
        self.assertFalse(focused)


class LinuxDebugTests(unittest.TestCase):
    def test_registered_overlay_factory_is_used_only_when_gui_requests_it(self) -> None:
        class Overlay:
            def __init__(self, parent):
                self.parent = parent
                self.closed = False

            def close(self):
                self.closed = True

        bus = DebugBus()
        created = []
        bus.register_overlay_factory(lambda parent: created.append(Overlay(parent)) or created[-1])
        with tempfile.TemporaryDirectory() as td:
            bus.arm(overlay_parent="gui", out_dir=Path(td), show_overlay=True)
            self.assertTrue(bus.overlay_visible)
            self.assertEqual(created[0].parent, "gui")
            bus.disarm()
        self.assertTrue(created[0].closed)

    def test_overlay_failure_safely_falls_back_to_log_only(self) -> None:
        bus = DebugBus()
        bus.register_overlay_factory(lambda _parent: (_ for _ in ()).throw(
            RuntimeError("Shape unavailable")))
        with tempfile.TemporaryDirectory() as td:
            log = bus.arm(overlay_parent="gui", out_dir=Path(td), show_overlay=True)
            self.assertFalse(bus.overlay_visible)
            self.assertTrue(Path(log).is_file())
            bus.disarm()

    def test_shape_rectangles_uses_the_python_xlib_request_order(self) -> None:
        calls = []
        window = SimpleNamespace(shape_rectangles=lambda *args: calls.append(args))
        shape = SimpleNamespace(SO=SimpleNamespace(Set=7))
        rectangles = [(1, 2, 3, 4)]

        X11OverlayRenderer._shape_rectangles(window, shape, 9, rectangles)

        self.assertEqual(calls, [(7, 9, 0, 0, 0, rectangles)])

    def test_x11_overlay_skips_pixels_inside_registered_detector_regions(self) -> None:
        renderer = object.__new__(X11OverlayRenderer)
        renderer._lock = threading.Lock()
        renderer._boxes = {"menu search": ((20, 20, 100, 80), 9e99)}
        renderer._dots = {"interact": (50, 50, 9e99)}
        renderer._marks = {"fish": (30, 80, 40, 9e99)}

        primitives, labels = renderer._primitives()

        protected = (20, 20, 100, 80)
        self.assertTrue(primitives)  # the outer hitbox frame remains visible
        self.assertFalse(any(
            p[0] < protected[0] + protected[2] and protected[0] < p[0] + p[2]
            and p[1] < protected[1] + protected[3] and protected[1] < p[1] + p[3]
            for p in primitives))
        self.assertFalse(any(
            p[0] < protected[0] + protected[2] and protected[0] < p[0] + p[2]
            and p[1] < protected[1] + protected[3] and protected[1] < p[1] + p[3]
            for p in labels))

    def test_linux_terminal_keeps_windows_cli_flag_parity(self) -> None:
        tree = ast.parse((LINUX / "run_linux.py").read_text(encoding="utf-8"))
        flags = {
            node.args[0].value for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument" and node.args
            and isinstance(node.args[0], ast.Constant)
        }
        self.assertTrue({"--debug", "--now", "--diag", "--record", "--dev", "--config"} <= flags)

    def test_linux_launchers_configure_context_before_shared_or_backend_imports(self) -> None:
        for name in ("easy_run_linux.py", "run_linux.py"):
            source = (LINUX / name).read_text(encoding="utf-8")
            configured = source.index("configure_linux_runtime(")
            if name == "easy_run_linux.py":
                self.assertLess(configured, source.index("from _backend import patch"))
                self.assertLess(configured, source.index("import easy_run"))
            else:
                self.assertLess(configured, source.rindex("_patch_backends()"))
                self.assertLess(configured, source.index("import bloxfish.engine"))


if __name__ == "__main__":
    unittest.main()
