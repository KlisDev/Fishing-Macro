"""F8 must never intercept the GUI clicks it is illustrating (issue #38)."""
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from bloxfish.debug import DebugBus, OverlayRenderer


class DebugFallbackTests(unittest.TestCase):
    def test_unavailable_overlay_keeps_click_diagnostics(self):
        bus = DebugBus()
        bus.register_overlay_factory(mock.Mock(side_effect=RuntimeError('passthrough failed')))
        with tempfile.TemporaryDirectory() as directory:
            path = bus.arm(out_dir=Path(directory), show_overlay=True)
            self.assertFalse(bus.overlay_visible)
            bus.click('root.nevermind', 1475, 730)
            bus.disarm()
            log = path.read_text(encoding='utf-8')
            self.assertIn('passthrough failed', log)
            self.assertIn('root.nevermind', log)
            self.assertIn('x=1475', log)


@unittest.skipUnless(sys.platform == 'win32' and os.environ.get('BLOXFISH_TEST_GUI') == '1',
                     'requires opt-in Windows/Tk GUI tests')
class WindowsOverlayTests(unittest.TestCase):
    def setUp(self):
        import ctypes
        from ctypes import wintypes
        import tkinter as tk
        self.root = tk.Tk()
        self.root.geometry('400x240+60+60')
        self.root.title('Isolated F8 regression test')
        self.root.configure(bg='#205080')
        self.root.attributes('-topmost', True)
        self.root.update()
        self.user32 = ctypes.WinDLL('user32', use_last_error=True)
        for name, args, result in (
            ('GetParent', [wintypes.HWND], wintypes.HWND),
            ('GetAncestor', [wintypes.HWND, wintypes.UINT], wintypes.HWND),
            ('GetWindowLongW', [wintypes.HWND, ctypes.c_int], wintypes.LONG),
            ('SetWindowLongW', [wintypes.HWND, ctypes.c_int, wintypes.LONG], wintypes.LONG),
            ('WindowFromPoint', [wintypes.POINT], wintypes.HWND),
        ):
            function = getattr(self.user32, name)
            function.argtypes, function.restype = args, result
        self.overlay = None

    def tearDown(self):
        if self.overlay is not None:
            self.overlay.close()
        self.root.destroy()

    def pump(self):
        for _ in range(6):
            self.root.update()
            time.sleep(.02)

    def test_colored_click_marker_passes_native_hit_test_to_underlying_window(self):
        from ctypes import wintypes
        x, y = self.root.winfo_rootx() + 180, self.root.winfo_rooty() + 120
        underlying = self.user32.GetParent(self.root.winfo_id())
        self.overlay = OverlayRenderer(self.root)
        self.overlay.dot('root.nevermind', x, y)
        self.pump()
        style = self.user32.GetWindowLongW(self.overlay._hwnd, -20)
        self.assertEqual(style & 0x08080020, 0x08080020)
        self.assertTrue(self.overlay.canvas.find_all())
        # Native Windows hit testing, including an opaque marker pixel. No
        # mouse movement/click or keyboard input is injected by this test.
        hit = self.user32.WindowFromPoint(wintypes.POINT(x, y))
        self.assertEqual(self.user32.GetAncestor(hit, 2), underlying)
        # Reproduce the old missing-TRANSPARENT style with the same marker:
        # Windows now targets the overlay itself. Restore before continuing.
        try:
            self.user32.SetWindowLongW(self.overlay._hwnd, -20, style & ~0x20)
            self.pump()
            intercepted = self.user32.WindowFromPoint(wintypes.POINT(x, y))
            self.assertEqual(self.user32.GetAncestor(intercepted, 2), self.overlay._hwnd)
        finally:
            self.overlay._passthrough()
        self.overlay.win.withdraw()
        self.overlay.win.deiconify()
        self.pump()
        self.assertEqual(self.user32.GetWindowLongW(self.overlay._hwnd, -20)
                         & 0x08080020, 0x08080020)

    def test_failed_passthrough_destroys_window_before_log_only_fallback(self):
        bus = DebugBus()
        before = set(self.root.winfo_children())
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(OverlayRenderer, '_passthrough',
                                  side_effect=RuntimeError('native setup rejected')):
            bus.arm(self.root, Path(directory), show_overlay=True)
            self.assertFalse(bus.overlay_visible)
            self.assertEqual(set(self.root.winfo_children()), before)
            bus.disarm()

    def test_nested_reel_frames_and_markers_do_not_paint_into_search_capture(self):
        import ctypes
        from ctypes import wintypes
        from bloxfish.capture import Rect
        self.user32.GetDC.argtypes = [wintypes.HWND]
        self.user32.GetDC.restype = wintypes.HDC
        self.user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
        self.user32.ReleaseDC.restype = ctypes.c_int
        gdi = ctypes.WinDLL('gdi32', use_last_error=True)
        gdi.GetPixel.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
        gdi.GetPixel.restype = wintypes.DWORD
        self.user32.LogicalToPhysicalPointForPerMonitorDPI.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
        self.user32.LogicalToPhysicalPointForPerMonitorDPI.restype = wintypes.BOOL
        x, y = self.root.winfo_rootx()+180, self.root.winfo_rooty()+120
        wrapper = self.user32.GetParent(self.root.winfo_id())
        self.pump()
        dc = self.user32.GetDC(None)
        def pixel(px, py):
            # Tk/window hit testing uses logical coordinates on an unaware
            # test process; the screen DC's pixels are physical at 200% DPI.
            point = wintypes.POINT(px, py)
            self.assertTrue(self.user32.LogicalToPhysicalPointForPerMonitorDPI(
                wrapper, ctypes.byref(point)))
            return gdi.GetPixel(dc, point.x, point.y)
        try:
            before = pixel(x, y)
            self.assertEqual(before, 0x805020)  # owned test window's blue background
            self.overlay = OverlayRenderer(self.root)
            self.overlay.box('reel bar band', Rect(x-100, y-70, 240, 140))
            # This smaller frame's right edge and its click dot are inside
            # the larger capture, like a stale partial reel measurement.
            self.overlay.box('reel bar', Rect(x-80, y-40, 76, 80))
            self.overlay.dot('old click', x, y)
            self.pump()
            for dx, dy in ((0,0), (-1,0), (1,0), (0,10), (0,-10)):
                self.assertEqual(pixel(x+dx, y+dy), before)
        finally:
            self.user32.ReleaseDC(None, dc)

    def test_close_cancels_refresh_and_repeated_f8_leaves_no_windows(self):
        before = set(self.root.winfo_children())
        for _ in range(3):
            overlay = OverlayRenderer(self.root)
            self.assertIsNotNone(overlay._tick_id)
            overlay.close()
            self.assertIsNone(overlay._tick_id)
            self.assertFalse(overlay._alive)
        self.pump()
        self.assertEqual(set(self.root.winfo_children()), before)


if __name__ == '__main__':
    unittest.main()
