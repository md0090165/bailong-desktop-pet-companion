import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,json,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication,QWidget
from codex_notify import completion,handle
from codex_monitor import CodexMonitor,MonitorState,HUNGRY,DONE,quota_key

def payload(turn='1',**extra):return {'type':'agent-turn-complete','thread-id':'thread','turn-id':turn,**extra}
def data(remaining=10,reset=None,account='a'*64):
    return {'_account_key':account,'rateLimits':{'secondary':{'windowDurationMins':300,'usedPercent':100-remaining,'resetsAt':reset or time.time()+10000},
                                              'primary':{'windowDurationMins':10080,'usedPercent':99,'resetsAt':time.time()+99999}}}

class BridgeTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
    def test_only_successful_completion_and_ids_required(self):
        self.assertIsNotNone(completion(payload()))
        for p in ({},payload(status='failed'),payload(status='interrupted'),payload(**{'turn-id':None}),payload(type='approval-requested')):
            self.assertIsNone(completion(p))
    def test_discards_message_text_and_forwards_exact_original(self):
        folder=self.root/'CodexIntegration';folder.mkdir();cmd=['original-notify.exe','turn-ended']
        (folder/'notify-forward.json').write_text(json.dumps(cmd))
        raw=json.dumps(payload(**{'last-assistant-message':'PRIVATE_TEST','input-messages':['PRIVATE_INPUT']}))
        with patch('codex_notify.subprocess.run') as run:self.assertTrue(handle(raw,self.root));self.assertEqual(run.call_args.args[0],cmd+[raw])
        text=next((folder/'inbox').glob('*.json')).read_text();self.assertNotIn('PRIVATE',text);self.assertEqual(set(json.loads(text)),{'id','at'})
    def test_previous_hook_failure_does_not_lose_event(self):
        folder=self.root/'CodexIntegration';folder.mkdir();(folder/'notify-forward.json').write_text('["original.exe"]')
        with patch('codex_notify.subprocess.run',side_effect=OSError()):self.assertTrue(handle(json.dumps(payload()),self.root))
    def test_bad_json_ignored(self):self.assertFalse(handle('broken',self.root,forward=False))

class MonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.pet=QWidget()
        self.pet.exiting=False;self.pet.mode='idle';self.pet.dragging=False;self.pet.quota_inflight=False;self.pet.chat=None
        self.pet.click_timer=Mock();self.pet.click_timer.isActive.return_value=False
        self.pet.bubble=Mock();self.pet.bubble.isVisible.return_value=False;self.pet.events=Mock();self.pet.events.panel=None
        self.pet.variants=[{'id':'04'},{'id':'14'}];self.pet.play=Mock();self.pet.say=Mock();self.pet.idle=Mock()
        self.monitor=CodexMonitor(self.pet,self.root);self.monitor.timer.stop()
    def tearDown(self):self.monitor.shutdown();self.pet.deleteLater();self.app.processEvents();self.tmp.cleanup()
    def test_five_hour_not_primary_and_exact_threshold(self):
        self.monitor.quota_ready(data(10));self.monitor.deliver()
        self.pet.play.assert_called_once_with({'id':'14'},'variant');self.pet.say.assert_called_once_with(HUNGRY,5000)
        self.assertEqual(len(self.monitor.state.data['alerted']),1)
    def test_above_threshold_no_notification(self):
        self.monitor.quota_ready(data(10.01));self.monitor.deliver();self.pet.play.assert_not_called()
    def test_same_account_window_only_once_including_restart(self):
        d=data();self.monitor.quota_ready(d);self.monitor.deliver()
        self.monitor.quota_ready(d);self.monitor.deliver();self.assertEqual(self.pet.play.call_count,1)
        restarted=CodexMonitor(self.pet,self.root);restarted.timer.stop();restarted.quota_ready(d);restarted.deliver()
        self.assertEqual(self.pet.play.call_count,1);restarted.shutdown()
    def test_new_window_and_account_can_remind(self):
        now=time.time();self.monitor.quota_ready(data(reset=now+5000));self.monitor.deliver()
        self.monitor.quota_ready(data(reset=now+10000));self.monitor.deliver()
        self.monitor.quota_ready(data(reset=now+10000,account='b'*64));self.monitor.deliver();self.assertEqual(self.pet.say.call_count,3)
    def test_unknown_identity_reset_nan_or_weekly_only_do_not_alert(self):
        for d in (data(account=None),data(reset=time.time()-1),data(remaining=float('nan')),{'_account_key':'a'*64,'rateLimits':{'primary':{'windowDurationMins':10080,'usedPercent':99}}}):
            self.assertIsNone(quota_key(d));self.monitor.quota_ready(d);self.monitor.deliver()
        self.pet.play.assert_not_called()
    def test_failed_query_never_creates_hunger(self):
        self.monitor.quota_failed();self.monitor.deliver();self.pet.play.assert_not_called()
    def test_busy_defers_then_plays_both_notifications_in_order(self):
        self.monitor.pending_completion=True;self.monitor.quota_ready(data());self.pet.dragging=True
        self.monitor.deliver();self.pet.play.assert_not_called()
        self.pet.dragging=False;self.monitor.deliver();self.assertEqual(self.pet.play.call_args.args[0]['id'],'14')
        self.assertTrue(self.monitor.pending_completion);self.monitor.deliver();self.assertEqual(self.pet.play.call_args.args[0]['id'],'04')
    def test_completion_duplicate_burst_and_old_events(self):
        for _ in range(3):handle(json.dumps(payload()),self.root,forward=False)
        self.monitor.ingest();self.assertEqual(len(self.monitor.state.data['seen']),1)
        self.monitor.deliver();self.assertEqual(self.pet.play.call_count,1);self.pet.say.assert_called_once_with(DONE,4000)
        self.monitor.ingest();self.monitor.deliver();self.assertEqual(self.pet.play.call_count,1)
        handle(json.dumps(payload('old')),self.root,forward=False);self.monitor.ingest(at=time.time()+600);self.monitor.deliver()
        self.assertEqual(self.pet.play.call_count,1)
    def test_failed_state_write_does_not_display_or_mark(self):
        self.monitor.quota_ready(data())
        with patch('codex_monitor.os.replace',side_effect=OSError()):self.monitor.deliver()
        self.pet.say.assert_not_called();self.assertEqual(self.monitor.state.data['alerted'],{})
    def test_recovered_quota_cancels_queued_hunger(self):
        reset=time.time()+5000;self.monitor.quota_ready(data(5,reset));self.monitor.quota_ready(data(70,reset));self.monitor.deliver()
        self.pet.play.assert_not_called()
    def test_disabled_drops_events_and_queues(self):
        self.monitor.toggle(False);handle(json.dumps(payload()),self.root,forward=False);self.monitor.tick()
        self.pet.play.assert_not_called();self.assertFalse(self.monitor.pending_completion)
        self.assertFalse(MonitorState(self.root).data['enabled'])
    def test_backup_restore_and_both_bad_stop(self):
        s=copy.deepcopy(self.monitor.state.data);self.monitor.state.save(s);s['enabled']=False;self.monitor.state.save(s)
        self.monitor.state.path.write_text('bad');self.assertTrue(MonitorState(self.root).data['enabled'])
        self.monitor.state.backup.write_text('bad');self.assertFalse(MonitorState(self.root).ready)
    def test_poll_is_readonly_and_cancelable(self):
        from codex_monitor import QuotaPoll
        with patch('codex_monitor.read_quota',return_value=data()) as read:
            w=QuotaPoll();w.run();self.assertTrue(read.call_args.kwargs['include_identity']);self.assertFalse(read.call_args.kwargs['log_path'])

if __name__=='__main__':unittest.main()
