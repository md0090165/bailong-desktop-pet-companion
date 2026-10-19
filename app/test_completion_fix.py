import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,hashlib,json,tempfile,time,unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch
import test_codex_monitor as fixtures
import test_random_events as event_fixtures
from codex_notify import handle
from codex_completion_watch import CompletionWatch
from codex_bus import shared_inbox
from random_events import EventStore,DEFAULT_RULES,interval_seconds,rules_at,EventFailure


def line(kind='task_complete',turn='test-turn',stamp=None):
    return (json.dumps({'type':'event_msg','timestamp':stamp or datetime.now(timezone.utc).isoformat(),
            'payload':{'type':kind,'turn_id':turn,'root_turn_id':'root-'+turn,
                       'last_agent_message':'PRIVATE ASSISTANT MESSAGE'}})+'\n').encode()


class CompletionFixTests(unittest.TestCase):
    setUpClass=fixtures.MonitorTests.__dict__['setUpClass']
    setUp=fixtures.MonitorTests.setUp
    tearDown=fixtures.MonitorTests.tearDown

    def test_other_install_bridge_reaches_shared_monitor_once(self):
        with patch.dict(os.environ,{'LOCALAPPDATA':str(self.root/'local')}):
            self.monitor.shared_inbox=shared_inbox()
            other=self.root/'other-install'
            raw=json.dumps(fixtures.payload(**{'last-assistant-message':'PRIVATE'}))
            self.assertTrue(handle(raw,other,forward=False,shared=True))
            self.monitor.ingest();self.monitor.deliver()
            self.pet.play.assert_called_once_with({'id':'04'},'variant')
            self.assertIsNotNone(self.monitor.last_completion_received)
            self.assertIsNotNone(self.monitor.last_completion_played)
            handle(raw,other,forward=False,shared=True);self.monitor.ingest();self.monitor.deliver()
            self.assertEqual(self.pet.play.call_count,1)
            self.assertNotIn('PRIVATE',self.monitor.state.path.read_text())

    def test_native_stream_ignores_history_then_accepts_new_complete_and_deduplicates_hook(self):
        base=self.root/'sessions';folder=base/datetime.now(timezone.utc).strftime('%Y/%m/%d');folder.mkdir(parents=True)
        file=folder/'rollout-test.jsonl'
        meta={'type':'session_meta','payload':{'session_id':'thread','id':'run-id'}}
        file.write_bytes((json.dumps(meta)+'\n').encode()+line(turn='old'))
        watch=CompletionWatch(base);self.assertEqual(watch.poll(),[])
        self.monitor.completion_watch=watch
        with file.open('ab') as stream:stream.write(line('turn_aborted'))
        self.monitor.ingest();self.assertFalse(self.monitor.pending_completion)
        with file.open('ab') as stream:stream.write(line())
        self.monitor.ingest();self.monitor.deliver()
        self.pet.play.assert_called_once_with({'id':'04'},'variant')
        hook={'type':'agent-turn-complete','thread-id':'thread','turn-id':'root-test-turn'}
        handle(json.dumps(hook),self.root,forward=False);self.monitor.ingest();self.monitor.deliver()
        self.assertEqual(self.pet.play.call_count,1)
        self.assertNotIn('PRIVATE',self.monitor.state.path.read_text())

    def test_native_event_preserves_partial_lines_and_handles_new_sessions(self):
        base=self.root/'sessions';folder=base/datetime.now(timezone.utc).strftime('%Y/%m/%d');folder.mkdir(parents=True)
        watch=CompletionWatch(base)
        file=folder/'new-session.jsonl'
        raw=line();cut=len(raw)//2
        file.write_bytes((json.dumps({'type':'session_meta','payload':{'id':'session'}})+'\n').encode()+raw[:cut])
        watch.next_scan=0;self.assertEqual(watch.poll(),[])
        with file.open('ab') as stream:stream.write(raw[cut:])
        events=watch.poll();self.assertEqual(len(events),1)
        self.assertEqual(set(events[0]),{'id','aliases','at'})
        self.assertNotIn('PRIVATE',json.dumps(events))

    def test_disabled_monitor_does_not_play_native_completion(self):
        self.monitor.toggle(False)
        event={'id':'c'*64,'aliases':['d'*64],'at':time.time()}
        self.monitor.accept_completion(event,time.time());self.monitor.deliver()
        self.pet.play.assert_not_called()


class TemporaryIntervalTests(unittest.TestCase):
    def test_seconds_override_and_normal_fallback(self):
        rules=copy.deepcopy(DEFAULT_RULES)
        self.assertEqual(interval_seconds(rules),[1200,2400])
        rules['test_interval_seconds']=[20,40]
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'RandomEvents.json').write_text(json.dumps(rules))
            loaded=rules_at(root);store=EventStore(root,'account',loaded)
            store.schedule(False,'miss',at=1000)
            self.assertTrue(1020<=store.state['next_at']<=1040)
            self.assertEqual(store.state['probability'],40)
            rules.pop('test_interval_seconds');self.assertEqual(interval_seconds(rules),[1200,2400])

    def test_invalid_seconds_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            for invalid in ([0,40],[40,20],[20,4000],[True,40]):
                rules=copy.deepcopy(DEFAULT_RULES);rules['test_interval_seconds']=invalid
                (root/'RandomEvents.json').write_text(json.dumps(rules))
                with self.assertRaises(EventFailure):rules_at(root)


class IntervalRearmTests(unittest.TestCase):
    setUpClass=event_fixtures.ControllerTests.__dict__['setUpClass']
    setUp=event_fixtures.ControllerTests.setUp
    tearDown=event_fixtures.ControllerTests.tearDown

    def test_starting_and_stopping_test_mode_rearms_without_erasing_diary_or_probability(self):
        self.control.store.state['probability']=60
        self.control.store.commit(self.control.store.state)
        self.control.rules['test_interval_seconds']=[20,40]
        self.control.account=None;self.control.bind(self.window)
        self.assertTrue(19<=self.control.store.state['next_at']-time.time()<=40)
        self.assertEqual(self.control.store.state['probability'],60)
        self.control.rules.pop('test_interval_seconds')
        self.control.account=None;self.control.bind(self.window)
        self.assertTrue(1199<=self.control.store.state['next_at']-time.time()<=2400)
        self.assertEqual(self.control.store.state['probability'],60)


if __name__=='__main__':unittest.main()
