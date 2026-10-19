import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,io,json,random,tempfile,threading,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from datetime import datetime,timezone
from PIL import Image,ImageDraw,ImageFont
from PySide6.QtWidgets import QWidget,QApplication
from random_events import EventStore,DEFAULT_RULES,EventFailure,roll,weighted,rules_at
from screen_privacy import checked_images,sensitive,PrivacyBlocked,local_ocr
from relationship import RelationshipRules,initial_state,advance,view_state,validate_state
from character_context import completed_turn
from event_controller import EventController
import test_chat_window as fixtures

class RuleTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        self.store=EventStore(self.root,'test',DEFAULT_RULES)
    def test_probability_ramp_reset_and_persist(self):
        for expected in (40,60,80,100,100):
            self.store.schedule(False,'miss',at=1000)
            self.assertEqual(self.store.state['probability'],expected)
            self.assertTrue(2200<=self.store.state['next_at']<=3400)
        self.assertEqual(EventStore(self.root,'test',DEFAULT_RULES).state['probability'],100)
        self.store.schedule(True,'success');self.assertEqual(self.store.state['probability'],20)
    def test_miss_animation_distribution(self):
        rng=random.Random(5);counts=dict.fromkeys(('09','14','13'),0)
        for _ in range(100000):counts[weighted(list(DEFAULT_RULES['miss_animations'].items()),rng)]+=1
        for key,expected in DEFAULT_RULES['miss_animations'].items():self.assertLess(abs(counts[key]/1000-expected),.6)
    def test_event_distribution_and_guarantee(self):
        rng=random.Random(8);counts={e['id']:0 for e in DEFAULT_RULES['events']}
        for _ in range(100000):counts[roll(DEFAULT_RULES,100,None,0,rng)['id']]+=1
        for e in DEFAULT_RULES['events']:self.assertLess(abs(counts[e['id']]/1000-e['weight']),.4)
    def test_hidden_first_exact_thresholds_and_fallthrough(self):
        eligible={'closeness':60,'valence':20};rng=Mock();rng.random.side_effect=[.049]
        self.assertEqual(roll(DEFAULT_RULES,20,eligible,11,rng)['id'],'hidden');self.assertEqual(rng.random.call_count,1)
        rng.random.side_effect=[.05,.99];self.assertIsNone(roll(DEFAULT_RULES,20,eligible,11,rng))
        for view,count in ((eligible,10),({'closeness':59.9,'valence':20},11),({'closeness':60,'valence':19.9},11)):
            rng.random.reset_mock();rng.random.side_effect=[.99]
            self.assertIsNone(roll(DEFAULT_RULES,20,view,count,rng));self.assertEqual(rng.random.call_count,1)
    def test_normal_20_percent_frequency(self):
        rng=random.Random(17);hits=sum(roll(DEFAULT_RULES,20,None,0,rng) is not None for _ in range(100000))
        self.assertLess(abs(hits-20000),500)
    def test_invalid_rules_fail_closed(self):
        (self.root/'RandomEvents.json').write_text('{}')
        with self.assertRaises(EventFailure):rules_at(self.root)
    def test_diary_requires_substantive_user_responses(self):
        self.store.begin(DEFAULT_RULES['events'][0],'你在做什麼？',at=time.time()-1)
        def t(msg):return completed_turn(msg,'好的','fake','sha')
        self.store.observe([t('嗯'),t('好喔')]);self.assertEqual(len(self.store.state['diaries']),0)
        turns=[t('我正在做一個美術專案，打算把這隻角色的服裝改成白色，想呈現溫柔的風格。'),
               t('你說的紫色配件很好看，我也想加上一個小髮飾，明天有時間就繼續畫畫。')]
        self.store.observe(turns);self.assertEqual(len(self.store.state['diaries']),1)
        self.store.observe(turns);self.assertEqual(len(self.store.state['diaries']),1)
        self.store.observe(turns+[t('也許再加入一些銀色花紋會更精緻，我晚點會畫草圖試看看。')])
        self.assertEqual(len(self.store.state['diaries']),1);self.assertEqual(len(self.store.state['diaries'][0]['turns']),5)
        self.assertEqual(EventStore(self.root,'test',DEFAULT_RULES).state,self.store.state)
    def test_no_reply_no_diary_and_expired_session_not_attached(self):
        self.store.begin(DEFAULT_RULES['events'][0],'hello',at=100)
        self.assertEqual(self.store.context(at=2600),'')
        self.store.observe([completed_turn('這是一段很長的回答'*20,'reply','m','s')],at=2600)
        self.assertEqual(self.store.state['diaries'],[])
    def test_duplicate_spam_does_not_make_rich_diary(self):
        self.store.begin(DEFAULT_RULES['events'][0],'hello',at=time.time()-1)
        self.store.observe([completed_turn('同樣一句長句子重複很多次'*10,'reply'+str(i),'m','s') for i in range(5)])
        self.assertEqual(self.store.state['diaries'],[])
    def test_account_isolation_backup_and_failed_commit(self):
        self.store.schedule(False,'miss');self.store.schedule(False,'miss')
        self.assertEqual(EventStore(self.root,'other',DEFAULT_RULES).state['probability'],20)
        before=copy.deepcopy(self.store.state)
        with patch('random_events.os.replace',side_effect=OSError()):
            with self.assertRaises(EventFailure):self.store.schedule(True,'success')
        self.assertEqual(self.store.state,before)
        self.store.path.write_text('broken');self.assertEqual(EventStore(self.root,'test',DEFAULT_RULES).state['probability'],40)
        self.store.backup.write_text('broken')
        with self.assertRaises(EventFailure):EventStore(self.root,'test',DEFAULT_RULES)

class PrivacyTests(unittest.TestCase):
    def png(self,text='Safe garden notes',size=(900,180)):
        im=Image.new('RGB',size,'white');ImageDraw.Draw(im).text((20,30),text,font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',36),fill='black')
        b=io.BytesIO();im.save(b,format='PNG');return b.getvalue()
    def test_sensitive_patterns(self):
        for text in ('帳 號 user','密 碼 123','password: abc','someone@example.com','API_KEY=abc',
                     '驗證碼 123456','4111 1111 1111 1111','Bearer abcdef','A123456789'):
            self.assertTrue(sensitive(text),text)
        self.assertFalse(sensitive('今天畫了一朵花，希望你喜歡'))
    def test_any_screen_blocks_all(self):
        with self.assertRaises(PrivacyBlocked):checked_images([self.png(),self.png()],threading.Event(),lambda t,c:['garden','password abc'])
    def test_empty_partial_cancel_fail_closed(self):
        for texts in ([''],[]):
            with self.assertRaises(PrivacyBlocked):checked_images([self.png()],threading.Event(),lambda t,c:texts)
        cancel=threading.Event();cancel.set()
        with self.assertRaises(PrivacyBlocked):checked_images([self.png()],cancel,Mock())
    def test_full_resolution_tiles_before_resize(self):
        ocr=Mock(side_effect=lambda tiles,c:['safe']*len(tiles))
        urls=checked_images([self.png(size=(3840,2160))],threading.Event(),ocr)
        self.assertEqual(len(ocr.call_args.args[0]),6);self.assertEqual(len(urls),1)
    def test_native_ocr_synthetic_only(self):
        safe=checked_images([self.png()],threading.Event());self.assertTrue(safe[0].startswith('data:image/png;base64,'))
        with self.assertRaises(PrivacyBlocked):checked_images([self.png('Password: TEST-ONLY-12345')],threading.Event())

class LongTermRelationshipTests(unittest.TestCase):
    def test_simulated_240_days_and_restarts(self):
        with tempfile.TemporaryDirectory() as folder:
            rules=RelationshipRules(folder);state=initial_state(rules,1700000000);rng=random.Random(81)
            for day in range(240):
                for i in range(40):
                    at=1700000000+day*86400+i*91
                    old=copy.deepcopy(state)
                    state,_=advance(state,rules,rng.choice(['謝謝你','抱抱','晚安','我很難過','你好','請安靜']),at)
                    validate_state(state)
                    self.assertLessEqual(state['closeness']-old['closeness'],.600001)
                    self.assertLessEqual(state['daily']['closeness'],2.400001)
                    self.assertLessEqual(state['daily']['trust'],1.200001)
                    if i%7==0:state=json.loads(json.dumps(state)) # Restart-equivalent serialization
            view=view_state(state,rules,at+365*86400)
            self.assertEqual(view['closeness'],state['closeness']);self.assertEqual(view['trust'],state['trust'])
            self.assertAlmostEqual(view['valence'],0);self.assertAlmostEqual(view['energy'],50)

class ControllerTests(unittest.TestCase):
    setUpClass=fixtures.ChatWindowTests.__dict__['setUpClass']
    wait_finished=fixtures.ChatWindowTests.wait_finished
    def setUp(self):
        fixtures.ChatWindowTests.setUp(self)
        self.pet=QWidget();self.pet.exiting=False;self.pet.mode='idle';self.pet.dragging=False;self.pet.quota_inflight=False
        self.pet.click_timer=Mock();self.pet.click_timer.isActive.return_value=False
        self.pet.bubble=Mock();self.pet.bubble.isVisible.return_value=False
        self.pet.variants=[{'id':i} for i in ('09','14','13')];self.pet.play=Mock()
        self.pet.ensure_chat=lambda:self.window
        self.control=EventController(self.pet,self.window.project_root);self.control.timer.stop();self.control.bind(self.window)
        self.window.hide();self.window.bubble.hide();self.control.next_mono=0
    def tearDown(self):
        self.control.shutdown();fixtures.ChatWindowTests.tearDown(self);self.pet.deleteLater();self.app.processEvents()
    def run_event(self,blocked=False):
        self.connection.reply.return_value='{"text":"今天在畫什麼？","emotion":"好奇","animation_id":"04"}'
        patches=[patch('event_controller.roll',return_value=DEFAULT_RULES['events'][0]),
                 patch('event_controller.capture_screens',return_value=[b'fake']),
                 patch('event_controller.checked_images',side_effect=PrivacyBlocked('敏感資訊') if blocked else None,return_value=['data:image/png;base64,FAKE'])]
        with patches[0],patches[1],patches[2]:self.control.tick();self.wait_finished()
    def test_proactive_one_call_and_followup_context(self):
        self.run_event();self.assertEqual(self.connection.reply.call_count,1)
        self.assertEqual(self.control.store.state['probability'],20)
        self.assertIn('今天在畫什麼',self.control.context());self.assertEqual(self.window.conversation_state['turns'],[])
        self.assertEqual(self.connection.reply.call_args.args[1][0]['content'][1]['type'],'input_image')
        self.window.bubble.hide();self.window.message.setPlainText('我正在畫白龍的衣服與髮飾，你覺得這樣好看嗎？');self.window.send_message();self.wait_finished()
        self.assertIn('今天在畫什麼',str(self.connection.reply.call_args.args[1]))
    def test_privacy_abort_zero_network_and_ramp(self):
        self.run_event(True);self.connection.reply.assert_not_called()
        self.assertEqual(self.control.store.state['probability'],40);self.pet.play.assert_called_once()
        self.assertIsNone(self.control.store.state['pending'])
    def test_busy_and_disabled_never_capture(self):
        with patch('event_controller.capture_screens') as cap:
            self.window.show();self.control.tick();cap.assert_not_called()
            self.window.hide();self.control.store.state['enabled']=False;self.control.tick();cap.assert_not_called()
        self.assertEqual(self.control.store.state['probability'],20)
    def test_model_failure_not_success(self):
        self.connection.reply.side_effect=RuntimeError('private server error')
        self.run_event();self.assertEqual(self.control.store.state['probability'],40)
        self.assertNotIn('private',self.control.store.path.read_text(encoding='utf-8'))
    def test_account_switch_drops_prior_context(self):
        self.run_event();self.window.session_account='another';self.control.bind(self.window)
        self.assertEqual(self.control.context(),'');self.assertEqual(self.control.store.state['diaries'],[])
    def test_cancelled_response_ramps_once_and_no_diary(self):
        def cancel(*a,**kw):kw['cancel'].set();return 'cancelled'
        self.connection.reply.side_effect=cancel
        self.run_event()
        self.assertEqual(self.control.store.state['probability'],40)
        self.assertIsNone(self.control.store.state['pending']);self.assertFalse(self.control.inflight)
    def test_quiet_defers_without_consuming_probability(self):
        self.window.relationship_store.state['quiet_until']=time.time()+1000
        with patch('event_controller.roll') as check:self.control.tick();check.assert_not_called()
        self.assertEqual(self.control.store.state['probability'],20)
    def test_success_write_failure_never_shows_unsaved_event(self):
        with patch('random_events.os.replace',side_effect=OSError()):self.run_event()
        self.assertFalse(self.window.bubble.isVisible());self.assertIsNone(self.control.store.state['pending'])
        self.assertTrue(self.control.error)
    def test_panel_no_network(self):
        self.control.show_panel();self.assertIn('20%',self.control.status.text());self.connection.reply.assert_not_called()
        self.assertTrue(self.control.diary.isReadOnly())

if __name__=='__main__':unittest.main()
