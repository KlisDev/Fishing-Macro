"""Issue #38: translucent root rows must remain detectable and settle safely."""
from types import SimpleNamespace
import unittest
from unittest import mock

import cv2
import numpy as np

from bloxfish import shop, vision
from bloxfish.config import Config
from bloxfish.capture import Rect


class NPCMenuDetectionTests(unittest.TestCase):
    def test_opposite_sides_of_the_search_area_are_not_a_stack(self):
        self.assertEqual(shop._coherent_menu_stack([(193, 15), (478, 129)],
                         Rect(0, 0, 525, 345)), [])

    def test_four_labels_recover_root_when_dark_backdrop_merges_last_panels(self):
        image = np.full((350, 420, 3), 240, dtype=np.uint8)
        for top, bottom in ((30, 95), (110, 175), (190, 340)):
            image[top:bottom, 15:330] = 30
        for text, baseline in zip(('Shop', 'Fishing Index', 'Job Stats', 'Nevermind'),
                                  (73, 153, 233, 313)):
            cv2.putText(image, text, (60, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        # Sky/water remains bright beside the fading panels.
        self.assertEqual(len(vision._find_menu_panels(image)), 2)
        rows = vision.find_falling_menu_buttons(image)
        self.assertEqual(len(rows), 4)
        for row, y in zip(rows, (63, 143, 223, 303)):
            self.assertLess(abs(row.y - y), 5)
            self.assertTrue(15 < row.x < 330)

    def test_bright_water_and_cursor_without_panels_do_not_form_a_menu(self):
        image = np.full((350, 420, 3), (235, 180, 100), dtype=np.uint8)
        for y in (45, 125, 205, 285):
            image[y:y + 16, 40:320] = 255
        self.assertEqual(vision.find_falling_menu_buttons(image), [])

    def test_plain_dark_bands_are_not_menu_buttons(self):
        image = np.full((350, 420, 3), 120, dtype=np.uint8)
        for top in (50, 170):
            image[top:top + 40, :] = 30
        self.assertEqual(len(vision._find_menu_panels(image)), 2)
        self.assertEqual(vision.find_falling_menu_buttons(image), [])
        # A single pointer on a band cannot turn the backdrop into a menu.
        image[60:78, 200:213] = 255
        self.assertEqual(vision.find_falling_menu_buttons(image), [])

    def test_one_label_does_not_promote_other_empty_panels_to_menu_rows(self):
        image = np.full((350, 420, 3), 120, dtype=np.uint8)
        for top in (30, 110, 190, 270):
            image[top:top + 65, :] = 30
        cv2.putText(image, 'Shop', (60, 73), cv2.FONT_HERSHEY_SIMPLEX,
                    .9, (255, 255, 255), 2, cv2.LINE_AA)
        self.assertEqual(len(vision._find_menu_panels(image)), 4)
        self.assertEqual(vision.find_falling_menu_buttons(image), [])

    def test_real_short_and_full_pages_survive_ui_scale_changes(self):
        pages = (('Basic Bait', 'Back'),
                 ('Buy Bait', 'Sell Fish', 'Back'),
                 ('Shop', 'Fishing Index', 'Job Stats', 'Nevermind'))
        for labels in pages:
            image = np.full((350, 420, 3), 120, dtype=np.uint8)
            tops = list(range(30, 271, 80))[-len(labels):]
            for top, label in zip(tops, labels):
                image[top:top + 65, 15:405] = 30
                cv2.putText(image, label, (60, top + 43),
                            cv2.FONT_HERSHEY_SIMPLEX, .9,
                            (255, 255, 255), 2, cv2.LINE_AA)
            for scale in (.75, 1.0, 1.5, 2.0):
                with self.subTest(rows=len(labels), scale=scale):
                    resized = cv2.resize(image, None, fx=scale, fy=scale)
                    rows = vision.find_falling_menu_buttons(resized)
                    self.assertEqual(len(rows), len(labels))
                    for row, top in zip(rows, tops):
                        self.assertTrue(15 * scale < row.x < 405 * scale)
                        self.assertTrue(top * scale < row.y < (top + 65) * scale)

    def test_pointer_extension_does_not_hide_nevermind(self):
        image = np.full((345, 525, 3), 210, dtype=np.uint8)
        # A continuous dark backing means the panel-only scan has no stack.
        image[25:330, 15:400] = 30
        for y in (40, 120, 200, 280):
            image[y:y + 22, 60:270] = 255
        image[301:336, 240:250] = 255  # pointer touching the final white label
        self.assertEqual(len(vision.find_falling_menu_buttons(image)), 4)

    def test_clipped_mastery_hud_words_are_not_npc_rows(self):
        image = np.full((320, 409, 3), (230, 180, 70), dtype=np.uint8)
        image[:, 255:] = 30
        for text, x, baseline in (
                ('Mastery 207', 306, 89), ('BUDDHA', 334, 164),
                ('Shift', 263, 247), ('Heavenly Impact', 263, 318)):
            cv2.putText(image, text, (x, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        for scale in (.75, 1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                resized = cv2.resize(image, None, fx=scale, fy=scale)
                self.assertEqual(vision.find_falling_menu_buttons(resized), [])

    def test_clipped_hud_does_not_hide_a_complete_menu_beside_it(self):
        image = np.full((350, 720, 3), (230, 180, 70), dtype=np.uint8)
        # A whole NPC stack shares the capture with the edge of the ability
        # HUD. Some label bands overlap vertically, so rejecting the whole
        # capture or every band containing a clipped glyph would lose rows.
        image[25:340, 15:475] = 30
        image[:, 565:] = 30
        for text, baseline in zip(('Shop', 'Fishing Index', 'Job Stats', 'Nevermind'),
                                  (73, 153, 233, 313)):
            cv2.putText(image, text, (60, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        for text, x, baseline in (
                ('Mastery 207', 617, 89), ('BUDDHA', 645, 164),
                ('Shift', 574, 247), ('Heavenly Impact', 574, 318)):
            cv2.putText(image, text, (x, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        for scale in (.75, 1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                rows = vision.find_falling_menu_buttons(
                    cv2.resize(image, None, fx=scale, fy=scale))
                self.assertEqual(len(rows), 4)
                self.assertTrue(all(row.x < 475 * scale for row in rows))

    def test_partial_hud_mask_does_not_exempt_other_clipped_words(self):
        image = np.full((320, 409, 3), (230, 180, 70), dtype=np.uint8)
        image[:, 255:] = 30
        # A translucent upper area means the persistent dark column detects
        # only a thin right sliver. Masking that sliver must not disable the
        # existing clipped-glyph check for the rest of the HUD.
        image[:130, 255:384] = 150
        for text, x, baseline in (
                ('Mastery 207', 306, 89), ('BUDDHA', 334, 164),
                ('Shift', 263, 247), ('Heavenly Impact', 267, 318)):
            cv2.putText(image, text, (x, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        for scale in (.75, 1.0, 1.5, 2.0):
            with self.subTest(scale=scale):
                resized = cv2.resize(image, None, fx=scale, fy=scale)
                self.assertEqual(vision.find_falling_menu_buttons(resized), [])

    def test_bright_water_at_right_edge_keeps_complete_labels(self):
        image = np.full((350, 420, 3), 245, dtype=np.uint8)
        image[25:340, 15:380] = 30
        for text, baseline in zip(('Shop', 'Fishing Index', 'Job Stats', 'Nevermind'),
                                  (73, 153, 233, 313)):
            cv2.putText(image, text, (60, baseline), cv2.FONT_HERSHEY_SIMPLEX,
                        .9, (255, 255, 255), 2, cv2.LINE_AA)
        self.assertEqual(len(vision.find_falling_menu_buttons(image)), 4)

    def test_signature_tolerates_only_small_nonaccumulating_jitter(self):
        base = ((100, 100), (160, 180), (130, 260), (145, 340))
        jitter = tuple((x + 6, y + 2) for x, y in base)
        self.assertTrue(shop._same_menu_page_signature(base, jitter))
        self.assertFalse(shop._same_menu_page_signature(base, base[:3]))
        self.assertFalse(shop._same_menu_page_signature(base, ('legacy', 4)))
        self.assertFalse(shop._same_menu_page_signature(base,
                         tuple((x, y + 5) for x, y in base)))
        self.assertFalse(shop._same_menu_page_signature(base,
                         tuple((x + 12, y) for x, y in base)))

    def run_page_wait(self, signature_at):
        clock = [0.0]
        engine = SimpleNamespace(cfg=Config(), _alive=lambda: True)
        def sleep(seconds):
            clock[0] += seconds
        with mock.patch.object(shop, '_menu_page_signature',
                               side_effect=lambda _engine: signature_at(clock[0])), \
                mock.patch.object(shop.time, 'perf_counter', side_effect=lambda: clock[0]), \
                mock.patch.object(shop.time, 'sleep', side_effect=sleep):
            result = shop.wait_for_menu_page(engine, 'root', 1.2)
        return result, clock[0]

    def test_complete_page_with_compression_jitter_can_settle(self):
        base = ((100, 100), (160, 180), (130, 260), (145, 340))
        result, elapsed = self.run_page_wait(lambda t:
            tuple((x + (int(t * 100) % 2) * 6, y) for x, y in base))
        self.assertTrue(result)
        self.assertGreaterEqual(elapsed, .65)

    def test_partial_or_continuously_falling_page_cannot_settle(self):
        base = ((100, 100), (160, 180), (130, 260), (145, 340))
        self.assertFalse(self.run_page_wait(lambda _t: base[:3])[0])
        self.assertFalse(self.run_page_wait(lambda t:
            tuple((x, y + int(t * 40)) for x, y in base))[0])


if __name__ == '__main__':
    unittest.main()
