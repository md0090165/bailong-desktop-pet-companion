import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication
from reply_animation import AnimationCatalog, AnimationCooldown, ReplyFormatFailure, DEFAULT_CONFIG
from pet import Pet
import test_chat_window as fixtures


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.catalog=AnimationCatalog(self.root)

    def test_all_ten_actions_have_tags_and_selection_instructions(self):
        self.assertEqual(len(self.catalog.entries),10)
        instructions=self.catalog.instructions()
        for item in self.catalog.entries.values():
            self.assertTrue(item['tags']);self.assertIn(item['id'],instructions)
            self.assertTrue(item['use_when']);self.assertTrue(item['avoid_when'])
        self.assertIn('眷屬印記',instructions)
        self.assertIn('idle',instructions)

    def test_valid_reply_separates_text_emotion_and_action(self):
        raw=json.dumps({'text':'完成啦～','emotion':'興奮','animation_id':'18'},ensure_ascii=False)
        result=self.catalog.decode(raw)
        self.assertEqual(result['text'],'完成啦～')
        self.assertEqual(result['animation_id'],'18')
        self.assertTrue(result['selection_valid'])
        self.assertEqual(self.catalog.decode('```json\n'+raw+'\n```'),result)

    def test_unknown_wrong_type_or_incompatible_tags_resolve_to_existing_actions(self):
        for emotion, animation_id in [('開心','../../arbitrary.exe'),('睏倦','18'),('不存在','01'),('開心',1),('開心',['01'])]:
            result=self.catalog.decode(json.dumps({'text':'仍然顯示台詞','emotion':emotion,'animation_id':animation_id}))
            self.assertEqual(result['text'],'仍然顯示台詞')
            self.assertIn(result['animation_id'],self.catalog.entries)
            self.assertTrue(result['selection_valid'])

    def test_plain_text_fallback_picks_a_natural_action(self):
        result=self.catalog.decode('你好～今天也加油。')
        self.assertEqual(result['text'],'你好～今天也加油。')
        self.assertEqual(result['animation_id'],'01')

    def test_incomplete_duplicate_or_missing_text_is_not_displayed_as_json(self):
        for raw in ('```json\n{"text":"還沒說完', '{"text":"還沒說完', '{"text":"一","text":"二","emotion":"興奮","animation_id":"18"}',
                    '{"emotion":"興奮","animation_id":"18"}', '{"text":3}', '[]'):
            with self.assertRaises(ReplyFormatFailure):self.catalog.decode(raw)

    def test_catalog_configuration_can_change_cooldowns_but_not_whitelist(self):
        config=json.loads(json.dumps(DEFAULT_CONFIG))
        config['animations'][0]['cooldown_seconds']=42
        path=self.root/'Animations.json';path.write_text(json.dumps(config),encoding='utf-8')
        self.assertEqual(AnimationCatalog(self.root).entries['01']['cooldown_seconds'],42)
        config['animations'][0]['id']='arbitrary.exe'
        path.write_text(json.dumps(config),encoding='utf-8')
        catalog=AnimationCatalog(self.root)
        self.assertTrue(catalog.warning)
        self.assertNotIn('arbitrary.exe',catalog.entries)

    def test_global_and_per_animation_cooldowns_only_start_when_played(self):
        gate=AnimationCooldown(self.catalog)
        self.assertTrue(gate.ready('18',100))
        gate.mark_played('18',100)
        self.assertFalse(gate.ready('04',103))
        self.assertTrue(gate.ready('04',104))
        self.assertFalse(gate.ready('18',107))
        self.assertTrue(gate.ready('18',108))
        self.assertFalse(gate.ready('unknown',1000))

    def test_numeric_names_aliases_and_extra_fields_are_accepted(self):
        for action in (4,'4','04','04 靈感','靈感','灵感','thinking',{'id':4}):
            result=self.catalog.decode(json.dumps({'text':'一起想想吧','mood':'neutral','animation':action,'extra':'可忽略'}))
            self.assertEqual(result['animation_id'],'04')
            self.assertTrue(result['selection_valid'])
        result=self.catalog.decode('{"reply":"一起聊聊～","emotion":"开心","animation_id":1}')
        self.assertEqual(result['animation_id'],'01')
        self.assertEqual(result['emotion'],'開心')

    def test_idle_and_missing_selection_are_filled_for_ordinary_chat(self):
        for fields in ({'emotion':'平靜','animation_id':'idle'},{},{'mood':'unknown','animation_id':'unknown'}):
            result=self.catalog.decode(json.dumps({'text':'我們一起想想怎麼做。',**fields}))
            self.assertEqual(result['animation_id'],'04')
            self.assertIn('補選',result['selection_note'])

    def test_explicit_quiet_request_stays_idle_even_when_model_selects_action(self):
        raw='{"text":"好，我會待在旁邊。","emotion":"開心","animation_id":"01"}'
        self.assertEqual(self.catalog.decode(raw,user_message='我要專心，請安靜')['animation_id'],'idle')
        self.assertEqual(self.catalog.decode('我會安靜陪你，不打擾你。')['animation_id'],'idle')

    def test_plain_reply_fallback_does_not_invent_rare_rituals(self):
        for text in ('我們一起聊點什麼吧','今天真開心～','晚安，去休息吧。','辛苦了，給你一個抱抱。'):
            result=self.catalog.decode(text)
            self.assertNotEqual(result['animation_id'],'22')
            self.assertIn(result['animation_id'],self.catalog.entries)


class PlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        catalog=AnimationCatalog(self.temp.name)
        self.pet=SimpleNamespace(exiting=False,reply_animations=catalog,reply_cooldown=AnimationCooldown(catalog),
            variants=[{'id':i['id'],'name':i['name']} for i in catalog.entries.values()],
            bubble=SimpleNamespace(is_quota_result=False,hide=Mock()),bubble_timer=Mock(),chat=None,
            mode='idle',dragging=False,quota_inflight=False,click_timer=Mock(),play=Mock(),idle=Mock())
        self.pet.click_timer.isActive.return_value=False
        self.mouse=patch('pet.QApplication.mouseButtons',return_value=Qt.MouseButton.NoButton)
        self.popup=patch('pet.QApplication.activePopupWidget',return_value=None)
        self.mouse.start();self.popup.start();self.addCleanup(self.mouse.stop);self.addCleanup(self.popup.stop)

    def test_valid_action_plays_once_without_manual_line_or_sound(self):
        self.assertTrue(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.play.assert_called_once_with(self.pet.variants[7],'chat')
        self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.assertEqual(self.pet.play.call_count,1)
        self.pet.bubble.hide.assert_called()

    def test_drag_landing_manual_quota_and_popup_take_priority(self):
        for mode in ('drag','landing','variant','quota','chat'):
            self.pet.mode=mode
            self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.mode='idle';self.pet.quota_inflight=True
        self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.quota_inflight=False
        with patch('pet.QApplication.activePopupWidget',return_value=object()):
            self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.play.assert_not_called()
        self.assertTrue(self.pet.reply_cooldown.ready('18'))

    def test_unknown_incompatible_idle_or_missing_asset_never_plays(self):
        for action,emotion in (('invalid','開心'),('idle','平靜')):
            self.assertFalse(Pet.chat_reply_action(self.pet,action,emotion))
        self.pet.variants=[]
        self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.play.assert_not_called()

    def test_playback_failure_returns_to_idle_and_does_not_consume_cooldown(self):
        self.pet.play.side_effect=OSError('bad asset')
        self.assertFalse(Pet.chat_reply_action(self.pet,'18','興奮'))
        self.pet.idle.assert_called_once()
        self.assertTrue(self.pet.reply_cooldown.ready('18'))

    def test_tag_mismatch_is_no_longer_a_playback_rejection(self):
        self.assertTrue(Pet.chat_reply_action(self.pet,'18','睏倦'))
        self.pet.play.assert_called_once()

    def test_same_action_cooldown_uses_a_similar_available_action(self):
        self.pet.reply_cooldown.mark_played('04',100)
        with patch('reply_animation.time.monotonic',return_value=105):
            self.assertTrue(Pet.chat_reply_action(self.pet,'04','平靜'))
        self.assertEqual(self.pet.play.call_args.args[0]['id'],'01')


class ChatActionTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls: setattr(cls,'app',QApplication.instance() or QApplication([])))
    setUp=fixtures.ChatWindowTests.setUp
    tearDown=fixtures.ChatWindowTests.tearDown
    wait_finished=fixtures.ChatWindowTests.wait_finished

    def reply(self,model,context,**kwargs):
        text=json.dumps({'text':'完成啦！一起慶祝吧～','emotion':'興奮','animation_id':'18'},ensure_ascii=False)
        kwargs['on_delta'](text)
        return text

    def test_complete_reply_emits_action_and_saves_only_clean_text_to_context(self):
        self.connection.reply.side_effect=self.reply
        selected=Mock();self.window.reply_action.connect(selected)
        self.window.message.setPlainText('我做完了！');self.window.send_message();self.wait_finished()
        selected.assert_called_once_with('18','興奮')
        self.assertEqual(self.window.bubble.label.text(),'完成啦！一起慶祝吧～')
        self.assertNotIn('animation_id',self.window.transcript.toPlainText())
        turn=self.window.conversation_state['turns'][0]
        self.assertEqual(turn['assistant'],'完成啦！一起慶祝吧～')
        self.assertEqual(turn['emotion'],'興奮')
        self.assertEqual(turn['animation_id'],'18')
        self.assertIn('回覆輸出格式',self.connection.reply.call_args.kwargs['instructions'])
        self.assertEqual(self.connection.reply.call_count,1)

    def test_invalid_id_keeps_text_and_selects_by_emotion(self):
        self.connection.reply.side_effect=lambda *a,**k: '{"text":"你好！","emotion":"開心","animation_id":"999"}'
        selected=Mock();self.window.reply_action.connect(selected)
        self.window.message.setPlainText('嗨');self.window.send_message();self.wait_finished()
        selected.assert_called_once_with('01','開心')
        self.assertEqual(self.window.messages[-1]['content'],'你好！')

    def test_incomplete_json_does_not_leak_or_save_or_play(self):
        self.connection.reply.side_effect=lambda *a,**k:'{"text":"未完成'
        selected=Mock();self.window.reply_action.connect(selected)
        self.window.message.setPlainText('保留輸入');self.window.send_message();self.wait_finished()
        selected.assert_not_called()
        self.assertEqual(self.window.messages,[])
        self.assertEqual(self.window.message.toPlainText(),'保留輸入')
        self.assertNotIn('{"text"',self.window.bubble.label.text())

    def test_cancelled_json_reply_never_saves_or_plays(self):
        def cancelled(model,context,**kwargs):
            kwargs['cancel'].set()
            return '{"text":"已取消","emotion":"興奮","animation_id":"18"}'
        self.connection.reply.side_effect=cancelled
        selected=Mock();self.window.reply_action.connect(selected)
        self.window.message.setPlainText('取消的輸入');self.window.send_message();self.wait_finished()
        selected.assert_not_called()
        self.assertEqual(self.window.messages,[])
        self.assertEqual(self.window.message.toPlainText(),'取消的輸入')

    def test_plain_model_reply_plays_without_a_second_inference_request(self):
        self.connection.reply.return_value='我們一起想想今天要聊什麼。'
        selected=Mock();self.window.reply_action.connect(selected)
        self.window.message.setPlainText('隨便聊聊');self.window.send_message();self.wait_finished()
        selected.assert_called_once_with('04','平靜')
        self.connection.reply.assert_called_once()
        self.assertEqual(self.window.messages[-1]['content'],'我們一起想想今天要聊什麼。')

    def test_restoring_history_never_replays_old_animations(self):
        self.connection.reply.side_effect=self.reply
        self.window.message.setPlainText('完成');self.window.send_message();self.wait_finished()
        restored=fixtures.ChatWindow(self.connection)
        selected=Mock();restored.reply_action.connect(selected)
        self.assertEqual(restored.conversation_state['turns'][0]['animation_id'],'18')
        selected.assert_not_called()
        self.assertFalse(restored.bubble.isVisible())
        restored.shutdown();restored.deleteLater();self.app.processEvents()


if __name__=='__main__':unittest.main()
