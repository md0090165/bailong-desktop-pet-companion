import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from PySide6.QtWidgets import QApplication
from character_context import completed_turn
from relationship import (RelationshipRules,RelationshipStore,RelationshipFailure,initial_state,
                          bias_animation,DEFAULT_RULES)
from reply_animation import AnimationCatalog
from relationship_window import RelationshipPanel
import test_chat_window as fixtures


def turn(message):return completed_turn(message,'離線回覆','test-model','profile')


class RelationshipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.rules=RelationshipRules(self.root);self.at=1000000000
        self.store=RelationshipStore(self.root,'first',self.rules);self.store.load()
        self.store.state=initial_state(self.rules,self.at)

    def test_first_success_changes_are_small_and_traced_to_user(self):
        original=turn('謝謝你，今天真的很幫忙。')
        self.store.learn([original],at=self.at)
        state=self.store.state;change=state['changes'][-1]
        self.assertAlmostEqual(state['closeness'],20.5)
        self.assertAlmostEqual(state['trust'],50.2)
        self.assertLessEqual(change['after']['valence']-change['before']['valence'],12)
        self.assertEqual(change['event'],'praise')
        self.assertEqual(change['source']['quote'],original['user'])
        self.assertEqual(change['source']['turn_number'],1)
        self.assertEqual(self.store.snapshot(self.at)['emotion'],'害羞')

    def test_rapid_chat_does_not_farm_long_term_scores(self):
        first=turn('謝謝你');second=turn('抱抱')
        self.store.learn([first],at=self.at)
        before=(self.store.state['closeness'],self.store.state['trust'])
        self.store.learn([first,second],at=self.at+4)
        self.assertEqual(before,(self.store.state['closeness'],self.store.state['trust']))
        self.assertIn('成長間隔',self.store.state['changes'][-1]['reason'])
        self.store.learn([first,second,turn('謝謝白龍')],at=self.at+91)
        self.assertGreater(self.store.state['closeness'],before[0])

    def test_daily_caps_and_numeric_ranges_are_enforced(self):
        turns=[]
        for i in range(70):
            turns.append(turn('謝謝你 '+str(i)))
            self.store.learn(turns,at=self.at+i*91)
        state=self.store.state
        self.assertAlmostEqual(state['closeness'],22.4)
        self.assertAlmostEqual(state['trust'],51.2)
        self.assertLessEqual(state['valence'],100)
        self.assertLessEqual(state['energy'],100)
        self.store.learn(turns+[turn('謝謝你，隔天再聊')],at=self.at+86400)
        self.assertGreater(self.store.state['closeness'],22.4)

    def test_scores_never_overflow_one_hundred(self):
        self.store.state.update(closeness=99.9,trust=99.9)
        self.store.learn([turn('抱抱')],at=self.at)
        self.assertEqual(self.store.state['closeness'],100)
        self.assertEqual(self.store.state['trust'],100)

    def test_time_relaxes_mood_without_eroding_relationship_or_writing_files(self):
        self.store.learn([turn('謝謝你')],at=self.at)
        raw=self.store.path.read_bytes();current=self.store.snapshot(self.at)
        half=self.store.snapshot(self.at+1800);later=self.store.snapshot(self.at+21600)
        self.assertAlmostEqual(half['valence'],current['valence']/2)
        self.assertLess(abs(later['energy']-50),abs(current['energy']-50))
        self.assertEqual(later['emotion'],'平靜')
        self.assertEqual(later['closeness'],current['closeness'])
        self.assertEqual(later['trust'],current['trust'])
        self.assertEqual(self.store.path.read_bytes(),raw)

    def test_preview_is_not_committed_before_reply_success(self):
        old=copy.deepcopy(self.store.state)
        preview=self.store.preview('抱抱',at=self.at)
        self.assertEqual(preview['emotion'],'親暱')
        self.assertGreater(preview['closeness'],20)
        self.assertEqual(self.store.state,old)
        self.assertFalse(self.store.path.exists())

    def test_existing_history_is_not_replayed_on_first_enable(self):
        original=turn('謝謝你')
        self.store.load([original])
        self.assertEqual(self.store.learn([original]),0)
        self.assertEqual(self.store.state['changes'],[])

    def test_restart_and_source_ids_prevent_duplicate_gains(self):
        original=turn('抱抱');self.store.learn([original],at=self.at)
        restored=RelationshipStore(self.root,'first');restored.load()
        before=copy.deepcopy(restored.state)
        self.assertEqual(restored.learn([original],at=self.at+1000),0)
        self.assertEqual(restored.state,before)

    def test_account_switch_does_not_share_state(self):
        self.store.learn([turn('抱抱')],at=self.at)
        other=RelationshipStore(self.root,'other');other.load()
        self.assertEqual(other.state['closeness'],20)
        self.assertEqual(other.state['changes'],[])
        self.assertNotEqual(other.path,self.store.path)

    def test_backward_clock_does_not_increase_mood_or_reset_daily_caps(self):
        self.store.learn([turn('謝謝你')],at=self.at)
        mood=self.store.snapshot(self.at)
        self.assertEqual(self.store.snapshot(self.at-600)['valence'],mood['valence'])
        self.store.learn([turn('你好')],at=self.at-600)
        self.assertEqual(self.store.state['mood_at'],self.at)
        self.assertEqual(self.store.state['closeness'],20.5)

    def test_quiet_request_is_respected_and_fresh_chat_can_resume(self):
        self.store.learn([turn('我要專心，請安靜。')],at=self.at)
        self.assertTrue(self.store.snapshot(self.at)['quiet'])
        self.assertEqual(self.store.snapshot(self.at)['mood_label'],'安靜陪伴')
        self.assertFalse(self.store.snapshot(self.at+1801)['quiet'])
        self.assertFalse(self.store.preview('我們聊聊',at=self.at+3)['quiet'])

    def test_examples_quotes_and_negation_do_not_trigger_affection_or_praise(self):
        for text in ('假設我說謝謝你','翻譯「喜歡你」','我不喜歡你','這句「謝謝你」怎麼說','```謝謝你```'):
            self.assertEqual(self.rules.classify(text)[0],'conversation',text)
        self.assertEqual(self.rules.classify('謝謝你幫我')[0],'praise')

    def test_closeness_and_trust_change_style_instructions(self):
        low=self.store.preview('你好',at=self.at)
        self.assertIn('逐步建立熟悉感',self.store.instructions(low))
        self.store.state.update(closeness=75,trust=80)
        high=self.store.preview('你好',at=self.at)
        instructions=self.store.instructions(high)
        self.assertIn('親暱與撒嬌',instructions)
        self.assertIn('仍尊重使用者自主',instructions)
        self.assertIn('核心人設與使用者當下要求優先',instructions)

    def test_mood_bias_only_changes_generic_fallbacks_not_explicit_model_actions(self):
        catalog=AnimationCatalog(self.root)
        decoded=catalog.decode('我們繼續聊聊。')
        view=self.store.preview('抱抱',at=self.at)
        result=bias_animation(decoded,view,catalog,'聊聊')
        self.assertEqual(result['animation_id'],'08')
        explicit=catalog.decode('{"text":"完成啦","emotion":"興奮","animation_id":"18"}')
        self.assertEqual(bias_animation(explicit,view,catalog,'完成'),explicit)

    def test_failed_atomic_write_keeps_last_good_state_and_can_retry(self):
        first=turn('謝謝你');self.store.learn([first],at=self.at)
        before=copy.deepcopy(self.store.state);raw=self.store.path.read_bytes();second=turn('抱抱')
        with patch('relationship.os.replace',side_effect=OSError):
            with self.assertRaises(RelationshipFailure):self.store.learn([first,second],at=self.at+100)
        self.assertEqual(self.store.state,before);self.assertEqual(self.store.path.read_bytes(),raw)
        self.assertEqual(self.store.learn([first,second],at=self.at+100),1)

    def test_corrupt_primary_recovers_backup_and_all_corrupt_files_are_preserved(self):
        first=turn('你好');self.store.learn([first],at=self.at)
        self.store.learn([first,turn('謝謝你')],at=self.at+100)
        self.store.path.write_text('broken',encoding='utf-8')
        restored=RelationshipStore(self.root,'first');restored.load()
        self.assertTrue(restored.warning)
        self.store.backup.write_text('broken too',encoding='utf-8')
        blocked=RelationshipStore(self.root,'first')
        with self.assertRaises(RelationshipFailure):blocked.load()
        self.assertFalse(blocked.ready)
        self.assertIsNone(blocked.preview('你好'))
        self.assertEqual(self.store.path.read_text(encoding='utf-8'),'broken')

    def test_invalid_rule_limits_fall_back_to_defaults(self):
        data=copy.deepcopy(DEFAULT_RULES);data['max_per_turn']['closeness']=100
        (self.root/'RelationshipRules.json').write_text(json.dumps(data),encoding='utf-8')
        rules=RelationshipRules(self.root)
        self.assertTrue(rules.warning)
        self.assertEqual(rules.data['max_per_turn']['closeness'],0.6)

    def test_change_history_is_bounded(self):
        turns=[]
        for i in range(104):
            turns.append(turn('你好 '+str(i)));self.store.learn(turns,at=self.at+i)
        self.assertEqual(len(self.store.state['changes']),100)
        self.assertEqual(len(self.store.state['processed_turn_ids']),104)


class ChatRelationshipTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:setattr(cls,'app',QApplication.instance() or QApplication([])))
    setUp=fixtures.ChatWindowTests.setUp
    tearDown=fixtures.ChatWindowTests.tearDown
    wait_finished=fixtures.ChatWindowTests.wait_finished

    def send(self,message,reply='我們一起聊聊。'):
        self.connection.reply.return_value=reply
        self.window.message.setPlainText(message);self.window.send_message();self.wait_finished()

    def test_current_user_event_influences_same_reply_and_persists_once(self):
        selected=Mock();self.window.reply_action.connect(selected)
        self.send('抱抱')
        self.assertEqual(self.window.relationship_store.state['changes'][-1]['event'],'affection')
        self.assertIn('本機角色關係與短期心情',self.connection.reply.call_args.kwargs['instructions'])
        self.assertIn('親暱',self.connection.reply.call_args.kwargs['instructions'])
        selected.assert_called_once_with('08','親暱')
        before=copy.deepcopy(self.window.relationship_store.state)
        self.window.save_conversation()
        self.assertEqual(self.window.relationship_store.state,before)
        self.connection.reply.assert_called_once()

    def test_failed_or_cancelled_response_cannot_commit_relationship_preview(self):
        from chat_connection import ConnectionFailure
        before=copy.deepcopy(self.window.relationship_store.state)
        self.connection.reply.side_effect=ConnectionFailure('network','中斷')
        self.window.message.setPlainText('抱抱');self.window.send_message();self.wait_finished()
        self.assertEqual(self.window.relationship_store.state,before)
        self.assertFalse(self.window.relationship_store.path.exists())

    def test_cancelled_completed_model_reply_cannot_commit_relationship(self):
        def cancelled(*args,**kwargs):
            kwargs['cancel'].set();return '抱抱～'
        before=copy.deepcopy(self.window.relationship_store.state)
        self.connection.reply.side_effect=cancelled
        self.window.message.setPlainText('抱抱');self.window.send_message();self.wait_finished()
        self.assertEqual(self.window.relationship_store.state,before)
        self.assertFalse(self.window.relationship_store.path.exists())

    def test_model_cannot_supply_relationship_gains(self):
        reply='{"text":"抱抱～","emotion":"親暱","animation_id":"08","closeness":100,"trust":100}'
        self.send('你好',reply)
        self.assertLessEqual(self.window.relationship_store.state['closeness'],20.6)
        self.assertLessEqual(self.window.relationship_store.state['trust'],50.3)

    def test_restart_restores_and_switching_account_isolates_relationship(self):
        self.send('謝謝你')
        restored=fixtures.ChatWindow(self.connection)
        self.assertEqual(restored.relationship_store.state,self.window.relationship_store.state)
        restored.shutdown();restored.deleteLater();self.app.processEvents()
        self.connection.accounts.return_value=[{'id':'second','label':'另一帳號','signed_in':True,'active':True}]
        self.window.refresh_accounts()
        self.assertEqual(self.window.relationship_store.state['closeness'],20)
        self.assertEqual(self.window.relationship_store.state['changes'],[])

    def test_panel_is_read_only_and_opening_it_does_not_invoke_model(self):
        self.send('謝謝你');self.connection.reply.reset_mock()
        self.window.open_relationship();self.app.processEvents()
        panel=self.window.relationship_window
        self.assertTrue(panel.isVisible());self.assertIn('受到肯定',panel.history.toPlainText())
        self.assertIn('謝謝你',panel.history.toPlainText())
        before=copy.deepcopy(self.window.relationship_store.state);panel.refresh()
        self.assertEqual(self.window.relationship_store.state,before)
        self.connection.reply.assert_not_called()
        self.window.message.setPlainText('不要在面板開啟時送出');self.window.send_message()
        self.connection.reply.assert_not_called()

    def test_quiet_request_overrides_animated_model_choice(self):
        selected=Mock();self.window.reply_action.connect(selected)
        self.send('我要專心，請安靜。','{"text":"好，我會待在旁邊。","emotion":"興奮","animation_id":"18"}')
        self.assertTrue(self.window.relationship_store.snapshot()['quiet'])
        self.assertEqual(selected.call_args.args[0],'idle')


if __name__=='__main__':unittest.main()
