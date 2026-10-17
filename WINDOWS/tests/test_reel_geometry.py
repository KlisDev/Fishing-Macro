"""Issue #53: measure the rail, including its dim unfilled progress section."""
from types import SimpleNamespace
import unittest
from unittest import mock

import cv2
import numpy as np

from bloxfish.capture import Rect
from bloxfish.config import Config
from bloxfish.engine import FishingEngine
from bloxfish.vision import find_bar, find_bar_by_strip, progress_bar_present, read_progress


def reel_image(scale=1., grey=False, dim=True, fill=.62, gap=0):
    """A separate playfield and rounded two-tone rail over colored scenery."""
    image = np.full((230, 1100, 3), (135, 90, 55), dtype=np.uint8)
    image[65:156, 95:1005] = (8, 7, 9)
    image[70:130, 100:1000] = (34, 34, 34)
    image[73:127, 390:640] = (89, 89, 89) if grey else (16, 150, 21)
    image[73:127, 480:550] = (133, 153, 16)
    image[140:152, 100:1000] = (12, 18, 13) if dim else (19, 61, 25)
    image[140:152, 100:100+round(900*fill)] = (85, 240, 100)
    if gap:
        # A stale F8 rectangle intersects the progress strip at the old edge.
        image[65:156, 680:680+gap] = (238, 211, 34)
    if scale != 1.:
        image = cv2.resize(image, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_NEAREST)
    return image


class ReelGeometryTests(unittest.TestCase):
    def assert_full_width(self, image, scale=1., finder=find_bar, origin=(0, 0)):
        cfg = Config()
        if finder is find_bar:
            geo = finder(image, origin, cfg.colors, cfg.detection, image.shape[1])
        else:
            geo = finder(image, origin, cfg.colors, cfg.detection,
                         round(image.shape[1]*.18), round(image.shape[1]*.99))
        self.assertIsNotNone(geo)
        self.assertAlmostEqual(geo.x0-origin[0], 100*scale, delta=2*scale)
        self.assertAlmostEqual(geo.x1-origin[0], 1000*scale, delta=2*scale)
        self.assertIsNotNone(geo.prog)
        self.assertGreater(geo.prog.height, 2)
        return geo

    def test_dim_unfilled_rail_is_full_width_at_multiple_scales(self):
        for scale in (.75, 1., 2., 3.):
            for grey in (False, True):
                with self.subTest(scale=scale, grey=grey):
                    self.assert_full_width(reel_image(scale, grey=grey), scale)

    def test_filled_fraction_does_not_change_track_width(self):
        for fill in (.35, .5, .62, .9):
            with self.subTest(fill=fill):
                self.assert_full_width(reel_image(fill=fill))

    def test_small_debug_break_does_not_split_progress_at_high_dpi(self):
        self.assert_full_width(reel_image(scale=2., gap=6), 2.)

    def test_nearby_green_scenery_does_not_move_the_left_edge(self):
        image = reel_image(scale=2.)
        image[280:304, 174:180] = (85, 240, 100)
        self.assert_full_width(image, 2.)

    def test_progress_first_finder_also_measures_dim_full_rail(self):
        for grey in (False, True):
            with self.subTest(grey=grey):
                self.assert_full_width(reel_image(grey=grey), finder=find_bar_by_strip)

    def test_progress_stays_a_fraction_of_the_full_rail(self):
        image = reel_image(fill=.62)
        geo = self.assert_full_width(image)
        progress = read_progress(image[geo.prog.top:geo.prog.bottom,
                                       geo.prog.left:geo.prog.right])
        self.assertAlmostEqual(progress, .62, delta=.01)

    def test_known_dim_progress_strip_stays_live_below_half_fill(self):
        geo = self.assert_full_width(reel_image())
        for fill in (.2, .35, .5):
            with self.subTest(fill=fill):
                image = reel_image(fill=fill)
                crop = image[geo.prog.top:geo.prog.bottom,geo.prog.left:geo.prog.right]
                self.assertTrue(progress_bar_present(crop))

    def test_weak_green_alone_or_an_isolated_speck_cannot_keep_bar_alive(self):
        dim = np.full((12, 900, 3), (12,18,13), dtype=np.uint8)
        self.assertFalse(progress_bar_present(dim))
        dim[:, 15:25] = (85,240,100)
        self.assertFalse(progress_bar_present(dim))

    def test_offsets_and_crop_margins_do_not_change_width(self):
        image = reel_image()[40:180, 70:1030]
        cfg = Config()
        geo = find_bar(image, (-1830, 340), cfg.colors, cfg.detection, 1100)
        self.assertIsNotNone(geo)
        self.assertAlmostEqual(geo.x0, -1800, delta=2)
        self.assertAlmostEqual(geo.x1, -900, delta=2)

    def test_separated_green_scenery_does_not_bridge_into_a_rail(self):
        image = reel_image()
        image[135:160, 650:750] = (135, 90, 55)
        image[65:135, 650:750] = (135, 90, 55)
        cfg = Config()
        geo = find_bar(image, (0, 0), cfg.colors, cfg.detection, 1100)
        if geo is not None:
            self.assertLess(geo.width, 650)

    def test_a_wide_cyan_object_cannot_bridge_separate_progress_segments(self):
        image = reel_image()
        image[140:152, 680:780] = (238, 211, 34)
        cfg = Config()
        geo = find_bar(image, (0, 0), cfg.colors, cfg.detection, 1100)
        if geo is not None:
            self.assertLess(geo.width, 650)

    def test_dim_green_without_a_playfield_cannot_authorize_a_bar(self):
        cfg = Config()
        for only_dim in (False, True):
            image = reel_image()
            image[60:135] = (135, 90, 55)
            if only_dim:
                image[140:152, 100:1000] = (12, 18, 13)
            self.assertIsNone(find_bar(image, (0, 0), cfg.colors, cfg.detection, 1100))

    def test_widest_lock_is_full_even_if_every_early_frame_is_dim(self):
        cfg = Config()
        images = [reel_image(fill=fill) for fill in (.5, .52, .54, .56, .58)]
        geometries = [find_bar(image, (0, 0), cfg.colors, cfg.detection, 1100)
                      for image in images]
        engine = SimpleNamespace(cfg=cfg, _alive=lambda: True,
                                 _look_for_bar=mock.Mock(side_effect=geometries[1:]))
        locked = FishingEngine._acquire_widest(engine, geometries[0])
        self.assertAlmostEqual(locked.width, 900, delta=2)


if __name__ == '__main__':
    unittest.main()
