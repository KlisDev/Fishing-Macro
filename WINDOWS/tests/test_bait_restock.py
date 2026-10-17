"""Issue #55: separate the Craft button from the NPC's yellow name banner."""
from types import SimpleNamespace
import unittest
from unittest import mock

import numpy as np
import cv2

from bloxfish import shop, vision
from bloxfish.config import Colors, Config
from bloxfish.engine import FishingEngine
from test_current_dialogue import make_engine, root_menu
from test_npc_route_recovery import clock_for, page, scenery


class CraftButtonDetectionTests(unittest.TestCase):
    def test_long_fisherman_banner_is_not_a_craft_window(self):
        image = np.zeros((151,384,3),dtype=np.uint8)
        image[130:151,88:299] = (30,190,225)
        self.assertFalse(vision.craft_window_open(image))

    def test_real_button_survives_scales_and_a_top_cropped_search_band(self):
        for scale in (.5,1.,2.,3.):
            with self.subTest(scale=scale):
                h,w=round(151*scale),round(384*scale)
                image = np.zeros((h,w,3),dtype=np.uint8)
                image[:round(42*scale),round(118*scale):round(268*scale)] = (30,190,225)
                self.assertTrue(vision.craft_window_open(image))

    def test_horizontally_clipped_banner_cannot_mimic_a_compact_button(self):
        image = np.zeros((151, 384, 3), dtype=np.uint8)
        image[130:151, 88:299] = (30, 190, 225)
        for left, right in ((112, 272), (112, 320), (70, 240)):
            with self.subTest(crop=(left, right)):
                self.assertFalse(vision.craft_window_open(image[:, left:right]))

    def test_tight_crop_keeps_a_complete_button_with_side_margins(self):
        image = np.zeros((80, 160, 3), dtype=np.uint8)
        image[:42, 5:155] = (30, 190, 225)
        self.assertTrue(vision.craft_window_open(image))

    def test_thin_yellow_line_cannot_become_a_craft_button(self):
        image = np.zeros((151,384,3),dtype=np.uint8)
        image[100:102,50:350] = (30,190,225)
        self.assertFalse(vision.craft_window_open(image))

    def test_optional_color_sample_still_needs_button_shape(self):
        colors=Colors()
        colors.cap_craft_on=True
        colors.cap_craft_bgr=(180,100,60)
        colors.cap_craft_tol=5
        image=np.zeros((151,384,3),dtype=np.uint8)
        image[20:62,118:268]=colors.cap_craft_bgr
        self.assertTrue(vision.craft_window_open(image,c=colors))
        image[:]=0
        image[130:151,88:299]=colors.cap_craft_bgr
        self.assertFalse(vision.craft_window_open(image,c=colors))


class MenuSettleBudgetTests(unittest.TestCase):
    def test_menu_arriving_near_render_deadline_gets_full_settle_time(self):
        engine=make_engine(root_menu())
        engine.cfg.shop.menu_page_settle=.65
        signature=((150,120),(150,200))
        with clock_for(engine), mock.patch.object(shop,'_menu_page_signature',
                side_effect=lambda _engine: signature if engine.clock.now >= 1.0 else ()):
            self.assertTrue(shop.wait_for_menu_page(engine,'bait',1.2))
        self.assertGreaterEqual(engine.clock.now,1.65)
        self.assertLess(engine.clock.now,1.95)

    def test_wrong_and_continuously_moving_pages_still_time_out_without_clicks(self):
        for mode in ('wrong','moving'):
            with self.subTest(mode=mode):
                engine=make_engine(root_menu())
                signature=lambda _engine: ((150,100),(150,180),(150,260)) if mode=='wrong' else (
                    (150,100+int(engine.clock.now*50)),(150,180+int(engine.clock.now*50)))
                with clock_for(engine),mock.patch.object(shop,'_menu_page_signature',side_effect=signature):
                    self.assertFalse(shop.wait_for_menu_page(engine,'bait',1.2))
                engine.mouse.click_at.assert_not_called()
                self.assertLess(engine.clock.now,1.5)


class HoveredMenuTests(unittest.TestCase):
    def test_hover_highlight_keeps_all_real_action_rows(self):
        for labels in (('Basic Bait','Back'),('Buy Bait','Sell Fish','Nevermind'),
                       ('Shop','Fishing Index','Job Stats','Nevermind')):
            for hovered in range(len(labels)):
                with self.subTest(labels=labels,hovered=hovered):
                    image=np.full((350,420,3),(190,130,65),dtype=np.uint8)
                    tops=list(range(30,271,80))[-len(labels):]
                    for index,(top,label) in enumerate(zip(tops,labels)):
                        image[top-3:top+68,15:405]=10
                        image[top:top+65,18:402]=(115,125,130) if index==hovered else 30
                        cv2.putText(image,label,(60,top+43),cv2.FONT_HERSHEY_SIMPLEX,
                                    .9,(255,255,255),2,cv2.LINE_AA)
                    self.assertEqual(len(vision.find_falling_menu_buttons(image)),len(labels))

    def test_flat_gray_scenery_and_empty_highlight_bands_are_not_action_rows(self):
        for empty_bands in (False,True):
            image=np.full((350,420,3),125,dtype=np.uint8)
            if empty_bands:
                image[:]=30
                for top in (190,270):
                    image[top:top+65]=125
            self.assertEqual(vision.find_falling_menu_buttons(image),[])

    def test_unbordered_gray_text_is_not_promoted_to_menu_evidence(self):
        image=np.full((350,420,3),125,dtype=np.uint8)
        for text,baseline in (('Basic Bait',233),('Back',313)):
            cv2.putText(image,text,(60,baseline),cv2.FONT_HERSHEY_SIMPLEX,
                        .9,(255,255,255),2,cv2.LINE_AA)
        self.assertEqual(vision.find_falling_menu_buttons(image),[])


class PurchaseRejectionTests(unittest.TestCase):
    def test_missing_button_before_any_click_never_credits_a_purchase(self):
        engine=make_engine(root_menu())
        click=mock.Mock()
        with clock_for(engine),mock.patch.object(shop,'craft_up',return_value=False):
            self.assertFalse(shop._confirm_craft_purchase(engine,click,tries=4,wait=.6))
        click.assert_not_called()

    def test_one_missing_frame_is_not_a_completed_purchase(self):
        engine=make_engine(root_menu())
        click=mock.Mock()
        reads=iter((True,False,True))
        def craft(_engine):
            return next(reads,True)
        with clock_for(engine),mock.patch.object(shop,'craft_up',side_effect=craft):
            self.assertFalse(shop._confirm_craft_purchase(engine,click,tries=1,wait=.6))
        click.assert_called_once_with()


class RestockRouteTests(unittest.TestCase):
    def run_restock(self, *, cancel_quantity=False, fail_exit=False):
        def npc_page(labels):
            image=page(labels)
            # The real name banner is long/thin and sits inside the calibrated
            # Craft-button search band even when the CRAFT dialog has closed.
            image[245:250,180:240]=(30,190,225)
            return image
        craft=scenery()
        craft[218:242,180:240]=(30,190,225)
        images={
            'root':npc_page(('Shop','Fishing Index','Job Stats','Nevermind')),
            'shop':npc_page(('Buy Bait','Sell Fish','Nevermind')),
            'bait':npc_page(('Basic Bait','Back')),
            'craft':craft,'closed':scenery(),
        }
        engine=make_engine(images['root'])
        engine.cfg.shop.npc='fisherman'
        engine.cfg.shop.bait_per_purchase=90
        engine.bait_count=1
        engine.stats=SimpleNamespace(purchases=0)
        engine._note_response=mock.Mock()
        alive=[True]
        engine._alive=lambda:alive[0]
        engine.stop=mock.Mock(side_effect=lambda:alive.__setitem__(0,False))
        state=['root']
        actions=[]
        plus=shop._abs(engine.window,engine.cfg.shop.craft_plus)
        confirm=shop._abs(engine.window,engine.cfg.shop.craft_button)
        def clicked(x,y):
            current=state[0]
            if current=='craft':
                self.assertIn((x,y),(plus,confirm))
                if (x,y)==plus:
                    actions.append('plus')
                    if cancel_quantity:
                        alive[0]=False
                    return
                actions.append('craft')
                dest='shop' if fail_exit else 'bait'
            else:
                rows=shop._falling_menu_buttons(engine)
                if current=='root':
                    second_visit=actions.count('root')>0
                    chosen=-1 if second_visit else 0
                    dest='closed' if second_visit else 'shop'
                elif current=='shop':
                    chosen,dest=0,'bait'
                else:
                    second_visit=actions.count('bait')>0
                    chosen=-1 if second_visit else 0
                    dest='root' if second_visit else 'craft'
                self.assertEqual((x,y),rows[chosen])
                actions.append(current)
            state[0]=dest
            engine.screen.image=images[dest]
        engine.mouse.click_at.side_effect=clicked
        with clock_for(engine),mock.patch.object(shop,'enter_fishing_stance',return_value=True):
            FishingEngine._buy_bait(engine)
        return engine,state[0],actions

    def test_full_restock_uses_real_craft_detection_and_credits_exactly_once(self):
        engine,state,actions=self.run_restock()
        self.assertEqual(state,'closed')
        self.assertEqual(actions,['root','shop','bait']+['plus']*8+['craft','bait','root'])
        self.assertEqual(engine.bait_count,91)
        self.assertEqual(engine.stats.purchases,1)
        engine.stop.assert_not_called()

    def test_cancellation_during_quantity_does_not_send_remaining_plus_or_craft(self):
        engine,state,actions=self.run_restock(cancel_quantity=True)
        self.assertEqual(state,'craft')
        self.assertEqual(actions,['root','shop','bait','plus'])
        self.assertEqual(engine.bait_count,1)
        self.assertEqual(engine.stats.purchases,0)

    def test_confirmed_purchase_is_credited_even_when_exit_remains_unverified(self):
        engine,state,actions=self.run_restock(fail_exit=True)
        self.assertEqual(state,'shop')
        self.assertEqual(actions.count('craft'),1)
        self.assertEqual(engine.bait_count,91)
        self.assertEqual(engine.stats.purchases,1)
        engine.stop.assert_called_once_with()


class PurchaseConfirmationTests(unittest.TestCase):
    def test_a_real_craft_click_and_sustained_closure_credit_once(self):
        engine=make_engine(root_menu())
        visible=[True]
        click=mock.Mock(side_effect=lambda: visible.__setitem__(0,False))
        with clock_for(engine),mock.patch.object(shop,'craft_up',side_effect=lambda _:visible[0]):
            self.assertTrue(shop._confirm_craft_purchase(engine,click,tries=4,wait=.6))
        click.assert_called_once_with()

    def test_capture_errors_do_not_count_as_closed_and_never_repeat_a_blind_click(self):
        engine=make_engine(root_menu())
        click=mock.Mock()
        def craft(_engine):
            if click.called:
                raise OSError('capture unavailable')
            return True
        with clock_for(engine),mock.patch.object(shop,'craft_up',side_effect=craft):
            self.assertFalse(shop._confirm_craft_purchase(engine,click,tries=4,wait=.6))
        click.assert_called_once_with()

    def test_missed_click_retries_only_while_craft_is_still_visible(self):
        engine=make_engine(root_menu())
        visible=[True]
        def clicked():
            if click.call_count==2:
                visible[0]=False
        click=mock.Mock(side_effect=clicked)
        with clock_for(engine),mock.patch.object(shop,'craft_up',side_effect=lambda _:visible[0]):
            self.assertTrue(shop._confirm_craft_purchase(engine,click,tries=4,wait=.6))
        self.assertEqual(click.call_count,2)

    def test_cancellation_stops_craft_retries(self):
        engine=make_engine(root_menu())
        engine._alive=lambda: engine.clock.now<.12
        click=mock.Mock()
        with clock_for(engine),mock.patch.object(shop,'craft_up',return_value=True):
            self.assertFalse(shop._confirm_craft_purchase(engine,click,tries=4,wait=.6))
        click.assert_called_once_with()


if __name__ == '__main__':
    unittest.main()
