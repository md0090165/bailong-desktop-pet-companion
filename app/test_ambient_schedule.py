import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import test_random_events as fixtures
from random_events import DEFAULT_RULES,EventStore,rules_at,roll
from screen_privacy import PrivacyBlocked


class ScheduledStoreTests(unittest.TestCase):
    def test_40_to_80_minutes_scheduled_attempts_never_use_progressive_probability(self):
        rules=copy.deepcopy(DEFAULT_RULES)
        rules.update(interval_minutes=[40,80],event_probability_mode='scheduled',ambient_animation_seconds=60)
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory);(path/'RandomEvents.json').write_text(json.dumps(rules))
            loaded=rules_at(path);store=EventStore(path,'test',loaded)
            self.assertEqual(store.state['probability'],100)
            for success,kind in [(False,'privacy'),(True,'success'),(False,'generation')]:
                store.schedule(success,'fixed diagnostic',at=1000,result_kind=kind)
                self.assertTrue(3400<=store.state['next_at']<=5800)
                self.assertEqual(store.state['probability'],100)
            self.assertEqual(store.state['statistics']['privacy'],1)
            self.assertEqual(store.state['statistics']['generation'],1)
            self.assertEqual(store.state['statistics']['success'],1)
            self.assertEqual(store.state['statistics']['miss'],0)


class AmbientScheduleTests(unittest.TestCase):
    setUpClass=fixtures.ControllerTests.__dict__['setUpClass']
    setUp=fixtures.ControllerTests.setUp
    tearDown=fixtures.ControllerTests.tearDown
    wait_finished=fixtures.ControllerTests.wait_finished

    def enable_ambient(self):
        self.control.rules.update(ambient_animation_seconds=60,ambient_enabled=True)
        self.control.next_ambient=0;self.control.next_mono=float('inf')

    def test_ambient_is_independent_of_events_and_never_captures_or_calls_model(self):
        self.enable_ambient();self.control.store.state['enabled']=False
        with patch('event_controller.capture_screens') as capture:
            self.control.tick();self.pet.play.assert_called_once()
            capture.assert_not_called();self.connection.reply.assert_not_called()
            self.assertTrue(59<=self.control.next_ambient-time.monotonic()<=60)
            self.control.tick();self.assertEqual(self.pet.play.call_count,1)

    def test_ambient_defers_dragging_quiet_and_completion_notifications(self):
        from types import SimpleNamespace
        self.enable_ambient();self.pet.dragging=True
        self.control.tick();self.pet.play.assert_not_called()
        self.pet.dragging=False;self.pet.codex_monitor=SimpleNamespace(pending_completion=True,pending_low=None)
        self.control.tick();self.pet.play.assert_not_called()
        self.pet.codex_monitor.pending_completion=False
        self.window.relationship_store.state['quiet_until']=time.time()+1000
        self.control.tick();self.pet.play.assert_not_called()
        self.window.relationship_store.state['quiet_until']=0
        self.control.tick();self.pet.play.assert_called_once()

    def test_ambient_does_not_require_a_selected_model(self):
        self.enable_ambient();self.window.models.clear()
        self.control.tick();self.pet.play.assert_called_once();self.connection.reply.assert_not_called()

    def test_scheduled_event_passes_100_percent_and_privacy_failures_are_counted(self):
        self.control.rules.update(event_probability_mode='scheduled',interval_minutes=[40,80])
        self.control.store.state['probability']=20
        with patch('event_controller.roll',return_value=DEFAULT_RULES['events'][0]) as choose, \
             patch('event_controller.capture_screens',return_value=[b'fake']), \
             patch('event_controller.checked_images',side_effect=PrivacyBlocked('畫面可能含敏感資訊')):
            self.control.tick();self.wait_finished()
        self.assertEqual(choose.call_args.args[1],100)
        self.assertEqual(self.control.store.state['statistics']['privacy'],1)
        self.assertEqual(self.control.store.state['statistics']['miss'],0)
        self.connection.reply.assert_not_called()
        self.assertTrue(2399<=self.control.store.state['next_at']-time.time()<=4800)

    def test_switch_from_seconds_test_mode_rearms_hourly_schedule(self):
        state=copy.deepcopy(self.control.store.state)
        state['test_interval_seconds']=[20,40];state['probability']=60
        self.control.store.commit(state)
        self.control.rules.pop('test_interval_seconds',None)
        self.control.rules.update(event_probability_mode='scheduled',interval_minutes=[40,80])
        self.control.account=None;self.control.bind(self.window)
        self.assertTrue(2399<=self.control.store.state['next_at']-time.time()<=4800)
        self.assertNotIn('test_interval_seconds',self.control.store.state)
        self.assertEqual(self.control.store.state['probability'],100)

    def test_panel_displays_schedule_and_safe_diagnostic_counters(self):
        self.enable_ambient();self.control.rules.update(event_probability_mode='scheduled',interval_minutes=[40,80])
        self.control.show_panel();text=self.control.status.text()
        self.assertIn('40～80',text);self.assertIn('隱私攔截',text)
        self.assertIn('60 秒',text);self.assertTrue(self.control.ambient_enabled.isChecked())
        self.connection.reply.assert_not_called()


if __name__=='__main__':unittest.main()
