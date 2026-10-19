import os
os.environ['QT_QPA_PLATFORM']='offscreen'
import tempfile,time,unittest,copy
from random_events import DEFAULT_RULES
from unittest.mock import Mock,patch
from pathlib import Path
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from pet import Pet
from chat_window import ChatWindow
from types import SimpleNamespace

class AutoAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        with patch('event_controller.rules_at',return_value=copy.deepcopy(DEFAULT_RULES)):
            cls.pet=Pet()
        cls.pet.tick.stop();cls.pet.events.timer.stop()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'Character.md').write_text('你是白龍。',encoding='utf-8')
        connection=Mock();connection.store=SimpleNamespace(folder=self.root)
        connection.accounts.return_value=[{'id':'fake','label':'fake','signed_in':True,'active':True}]
        self.chat=ChatWindow(connection);self.chat.models_ready([{'id':'fake-model','name':'fake'}])
        self.pet.chat=self.chat;self.pet.events.account=None;self.pet.events.bind(self.chat)
        self.pet.click_timer.stop();self.pet.bubble.hide();self.pet.bubble_timer.stop();self.pet.idle()
        self.pet.dragging=False;self.pet.quota_inflight=False
    def tearDown(self):
        self.chat.shutdown();self.chat.deleteLater();self.pet.chat=None;self.app.processEvents();self.tmp.cleanup()
    @classmethod
    def tearDownClass(cls):cls.pet.close()
    def test_first_trigger_not_early(self):
        with patch('event_controller.capture_screens') as capture:self.pet.events.tick();capture.assert_not_called()
        self.assertTrue(1198<self.pet.events.next_mono-time.monotonic()<2401)
    def test_miss_plays_requested_animation_once(self):
        self.pet.events.next_mono=0
        with patch('event_controller.roll',return_value=None),patch('event_controller.weighted',return_value='09'):
            self.pet.events.tick();self.pet.events.tick()
        self.assertEqual(self.pet.current['id'],'09');self.assertEqual(self.pet.mode,'variant')
        self.assertEqual(self.pet.events.store.state['probability'],40)
        self.pet.started=time.monotonic()-20;self.pet.animate();self.assertEqual(self.pet.mode,'idle')
    def test_busy_defers_without_probability_change(self):
        self.pet.events.next_mono=0
        for attr in ('dragging','quota_inflight'):
            setattr(self.pet,attr,True)
            with patch('event_controller.roll') as check:self.pet.events.tick();check.assert_not_called()
            setattr(self.pet,attr,False)
        self.assertEqual(self.pet.events.store.state['probability'],20)
    def test_manual_animation_keeps_deadline(self):
        deadline=self.pet.events.next_mono
        with patch.object(self.pet,'play_click_sound'):self.pet.choose(self.pet.variants[0])
        self.assertEqual(deadline,self.pet.events.next_mono)
    def test_actual_timer_signal(self):
        self.pet.events.next_mono=0
        with patch('event_controller.roll',return_value=None):
            self.pet.events.timer.start(10);QTest.qWait(60);self.pet.events.timer.stop()
        self.assertEqual(self.pet.mode,'variant');self.assertEqual(self.pet.events.store.state['probability'],40)
    def test_write_failure_stops_retry_loop(self):
        self.pet.events.next_mono=0
        with patch('random_events.os.replace',side_effect=OSError()),patch('event_controller.roll',return_value=None) as check:
            self.pet.events.tick();self.pet.events.tick();self.assertEqual(check.call_count,1)
        self.assertTrue(self.pet.events.error)

if __name__=='__main__':unittest.main()
