import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from character_context import (CharacterProfile, ConversationStore, ContextFailure,
                               empty_state, completed_turn, response_with_context,
                               project_root, SUMMARY_LIMIT)
from chat_connection import ConnectionFailure


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)
        self.worker = SimpleNamespace(cancel=threading.Event(), status=Mock(), delta=Mock())

    def test_profile_reload_is_readonly_and_failure_keeps_last_good_version(self):
        path = self.root / 'Character.md'
        path.write_text('你是白龍，喜歡研究星星。',encoding='utf-8-sig')
        profile = CharacterProfile(self.root)
        profile.reload()
        before = path.read_bytes()
        self.assertIn('喜歡研究星星',profile.instructions())
        self.assertEqual(path.read_bytes(),before)
        old_digest = profile.digest
        path.write_bytes(b'\xff')
        with self.assertRaises(ContextFailure):profile.reload()
        self.assertIn('喜歡研究星星',profile.text)
        self.assertEqual(profile.digest,old_digest)
        path.write_text('你是白龍，說話很俏皮。',encoding='utf-8')
        profile.reload()
        self.assertIn('很俏皮',profile.instructions())
        self.assertNotEqual(profile.digest,old_digest)

    def test_missing_empty_oversized_profiles_are_explicit_errors(self):
        profile = CharacterProfile(self.root)
        with self.assertRaises(ContextFailure):profile.reload()
        with self.assertRaises(ContextFailure):profile.instructions()
        for raw in (b'  ',b'a'*65537):
            profile.path.write_bytes(raw)
            with self.assertRaises(ContextFailure):profile.reload()

    def state(self, count):
        return {**empty_state(),'turns':[completed_turn('使用者內容'+str(i),'白龍回答'+str(i),'model','digest') for i in range(count)]}

    def test_restart_and_account_isolation(self):
        first = ConversationStore(self.root,'first')
        first.load();first.save(self.state(2))
        restored, warning = ConversationStore(self.root,'first').load()
        self.assertEqual(len(restored['turns']),2)
        self.assertEqual(warning,'')
        other, _ = ConversationStore(self.root,'second').load()
        self.assertEqual(other,empty_state())
        self.assertNotIn('first',first.path.name)

    def test_corruption_restores_backup_without_overwriting_it_with_bad_primary(self):
        store = ConversationStore(self.root,'first')
        store.load();store.save(self.state(1));store.save(self.state(2))
        store.path.write_text('{invalid',encoding='utf-8')
        restored = ConversationStore(self.root,'first')
        state, warning = restored.load()
        self.assertEqual(len(state['turns']),1)
        self.assertTrue(warning)
        restored.save(self.state(3))
        self.assertEqual(len(json.loads(restored.backup.read_text(encoding='utf-8'))['turns']),1)
        self.assertEqual(len(restored.load()[0]['turns']),3)

    def test_corrupt_files_remain_untouched_and_block_instead_of_resetting(self):
        store = ConversationStore(self.root,'first')
        store.path.parent.mkdir()
        store.path.write_text('broken',encoding='utf-8')
        with self.assertRaises(ContextFailure):store.load()
        self.assertEqual(store.path.read_text(encoding='utf-8'),'broken')

    def test_failed_atomic_save_keeps_previous_primary(self):
        store = ConversationStore(self.root,'first')
        store.load();store.save(self.state(1))
        with patch('character_context.os.replace',side_effect=OSError):
            with self.assertRaises(ContextFailure):store.save(self.state(2))
        self.assertEqual(len(ConversationStore(self.root,'first').load()[0]['turns']),1)

    def test_recap_then_reply_keeps_originals_and_uses_persona_only_for_reply(self):
        connection = Mock()
        connection.reply.side_effect=['使用者正在研究星空；這是歷史摘要。','今晚一起研究吧～']
        state = self.state(13)
        result = response_with_context(connection,'model',state,'接續聊聊','專屬白龍人設',self.worker)
        self.assertEqual(connection.reply.call_count,2)
        summary_call, reply_call = connection.reply.call_args_list
        self.assertIn('對話紀錄整理器',summary_call.kwargs['instructions'])
        self.assertEqual(reply_call.kwargs['instructions'],'專屬白龍人設')
        self.assertIn('歷史摘要',reply_call.args[1][0]['content'])
        self.assertEqual(len(reply_call.args[1]),14) # recap + 6 turns + newest user
        self.assertEqual(reply_call.args[1][-1]['content'],'接續聊聊')
        self.assertEqual(result['summary_until'],7)
        self.assertEqual(len(state['turns']),13)
        self.assertEqual(state['summary'],'')

    def test_short_conversation_never_creates_background_summary_request(self):
        connection = Mock();connection.reply.return_value='你好'
        result = response_with_context(connection,'model',self.state(2),'嗨','人設',self.worker)
        connection.reply.assert_called_once()
        self.assertEqual(len(connection.reply.call_args.args[1]),5)
        self.assertEqual(result['summary_until'],0)

    def test_cancellation_after_summary_prevents_chat_and_archive_changes(self):
        state=self.state(13)
        connection=Mock()
        def summarize(*args,**kwargs):
            self.worker.cancel.set();return '摘要'
        connection.reply.side_effect=summarize
        with self.assertRaises(ConnectionFailure):
            response_with_context(connection,'model',state,'嗨','人設',self.worker)
        self.assertEqual(connection.reply.call_count,1)
        self.assertEqual(state['summary_until'],0)

    def test_bad_summary_cannot_silently_drop_old_turns(self):
        for summary in ('','長'*(SUMMARY_LIMIT+1)):
            connection=Mock();connection.reply.return_value=summary
            state=self.state(13)
            with self.assertRaises(ContextFailure):
                response_with_context(connection,'model',state,'嗨','人設',self.worker)
            self.assertEqual(state['summary_until'],0)

    def test_frozen_executable_uses_editable_project_character(self):
        package=self.root/'release-work/stage3/dist/BailongPet'
        package.mkdir(parents=True)
        (self.root/'Start-BailongPet.cmd').write_text('@echo off')
        (self.root/'Character.md').write_text('最新人設',encoding='utf-8')
        (package/'Character.md').write_text('舊版攜帶人設',encoding='utf-8')
        with patch('character_context.sys.frozen',True,create=True),patch('character_context.sys.executable',str(package/'BailongPet.exe')):
            self.assertEqual(project_root(),self.root)


if __name__=='__main__':unittest.main()
