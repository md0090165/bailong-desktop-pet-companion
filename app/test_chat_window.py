import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget
from chat_connection import ConnectionFailure
from chat_window import ChatWindow


class ChatWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.connection = Mock()
        self.connection.store = SimpleNamespace(folder=Path(self.folder.name))
        self.connection.accounts.return_value = [{'id': 'test-account', 'label': '測試帳號',
                                                   'signed_in': True, 'active': True}]
        (Path(self.folder.name) / 'Character.md').write_text('你是溫柔的白龍少女。使用繁體中文。',encoding='utf-8')
        self.window = ChatWindow(self.connection)
        self.window.models_ready([{'id': 'test-model', 'name': '測試模型'}])
        self.window.show()

    def tearDown(self):
        self.window.shutdown()
        if self.window.worker:
            self.wait_finished()
        if self.window.settings:
            self.window.settings.deleteLater()
        self.window.deleteLater()
        self.app.processEvents()
        self.folder.cleanup()

    def wait_finished(self):
        deadline = time.monotonic() + 3
        while self.window.worker and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.window.worker)

    def reply(self, model, context, **kwargs):
        kwargs['on_delta']('你好，')
        kwargs['on_delta']('今天想聊什麼？')
        return '你好，今天想聊什麼？'

    def test_enter_sends_shift_enter_adds_newline_and_context_continues(self):
        self.connection.reply.side_effect = self.reply
        self.window.message.setPlainText('你好')
        QTest.keyClick(self.window.message, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertIn('\n', self.window.message.toPlainText())
        self.connection.reply.assert_not_called()
        QTest.keyClick(self.window.message, Qt.Key.Key_Return)
        self.wait_finished()
        self.assertIn('白龍：\n你好，今天想聊什麼？', self.window.transcript.toPlainText())
        self.assertEqual(len(self.window.messages), 2)
        self.window.message.setPlainText('繼續')
        self.window.send_message()
        self.wait_finished()
        self.assertEqual(len(self.connection.reply.call_args.args[1]), 3)

    def test_failed_partial_reply_never_enters_context_and_can_retry(self):
        def fail(*args, **kwargs):
            kwargs['on_delta']('尚未完成')
            raise ConnectionFailure('network', '連線中斷')
        self.connection.reply.side_effect = fail
        self.window.message.setPlainText('請回答')
        self.window.send_message()
        self.wait_finished()
        self.assertEqual(self.window.messages, [])
        self.assertEqual(self.window.message.toPlainText(), '請回答')
        self.assertIn('此則回覆未完成', self.window.transcript.toPlainText())
        self.assertTrue(self.window.send.isEnabled())

    def test_close_hides_chat_cancels_and_keeps_thread_alive_until_finished(self):
        def delayed(*args, **kwargs):
            kwargs['cancel'].wait(2)
            raise ConnectionFailure('cancelled', '操作已取消')
        self.connection.reply.side_effect = delayed
        self.window.message.setPlainText('等一下')
        self.window.send_message()
        self.window.close()
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.worker.cancel.is_set())
        self.wait_finished()
        self.assertEqual(self.window.messages, [])

    def test_blank_message_no_model_and_double_send_do_not_duplicate_requests(self):
        self.window.send_message()
        self.connection.reply.assert_not_called()
        self.window.models.clear()
        self.window.message.setPlainText('你好')
        self.window.send_message()
        self.connection.reply.assert_not_called()
        self.window.models_ready([{'id': 'test-model', 'name': '測試模型'}])
        self.connection.reply.side_effect = self.reply
        self.window.send_message()
        self.window.send_message()
        self.wait_finished()
        self.assertEqual(self.connection.reply.call_count, 1)

    def test_account_switch_clears_context_before_model_loading(self):
        self.window.messages = [{'role': 'user', 'content': '另一個帳號不能看到'}]
        self.window.transcript.setPlainText('私人內容')
        self.connection.accounts.return_value = [{'id': 'second', 'label': '第二帳號',
                                                 'signed_in': True, 'active': True}]
        self.window.refresh_accounts()
        self.assertEqual(self.window.messages, [])
        self.assertEqual(self.window.transcript.toPlainText(), '')
        self.assertEqual(self.window.models.count(), 0)

    def test_open_loads_catalog_but_never_sends_message(self):
        self.window.models_loaded_for = None
        self.connection.models.return_value = [{'id': 'test-model', 'name': '測試模型'}]
        pet = QWidget()
        self.window.show_near(pet)
        self.wait_finished()
        self.connection.models.assert_called_once()
        self.connection.reply.assert_not_called()
        self.connection.login.assert_not_called()
        pet.deleteLater()

    def test_model_error_restores_controls(self):
        self.connection.models.side_effect = ConnectionFailure('authentication', '請重新登入')
        self.window.load_models()
        self.wait_finished()
        self.assertEqual(self.window.status.text(), '請重新登入')
        self.assertTrue(self.window.refresh.isEnabled())

    def test_model_preference_and_successful_conversation_are_saved(self):
        self.window.models_ready([{'id': 'first', 'name': '第一'}, {'id': 'second', 'name': '第二'}])
        self.window.models.setCurrentIndex(1)
        self.assertEqual(self.window.preferences()['test-account'], 'second')
        self.window.models_ready([{'id': 'first', 'name': '第一'}, {'id': 'second', 'name': '第二'}])
        self.assertEqual(self.window.models.currentData(), 'second')
        self.connection.reply.side_effect = self.reply
        self.window.message.setPlainText('只在本次視窗保存')
        self.window.send_message()
        self.wait_finished()
        self.assertEqual(sorted(p.name for p in Path(self.folder.name).iterdir()), ['Character.md', 'Conversation', 'Memory', 'Relationship', 'chat-preferences.json'])

    def test_context_is_bounded_and_clear_resets_session(self):
        from character_context import completed_turn
        self.window.conversation_state['turns'] = [completed_turn(str(i),'回答','model','digest') for i in range(10)]
        self.connection.reply.side_effect = self.reply
        self.window.message.setPlainText('新訊息')
        self.window.send_message()
        self.wait_finished()
        self.assertEqual(len(self.connection.reply.call_args.args[1]), 21)
        self.assertEqual(len(self.window.messages), 12)
        self.window.clear_conversation()
        self.assertEqual(self.window.messages, [])
        self.assertEqual(self.window.transcript.toPlainText(), '')

    def test_hidden_window_with_pending_reply_still_blocks_auto_animation(self):
        self.assertTrue(self.window.interaction_busy())
        self.window.hide()
        self.assertFalse(self.window.interaction_busy())
        self.window.worker = Mock()
        self.assertTrue(self.window.interaction_busy())
        self.window.worker = None

    def test_profile_reload_applies_next_turn_without_changing_file_or_history(self):
        self.connection.reply.side_effect=self.reply
        path=Path(self.folder.name)/'Character.md'
        self.window.message.setPlainText('你好');self.window.send_message();self.wait_finished()
        self.assertIn('溫柔的白龍',self.connection.reply.call_args.kwargs['instructions'])
        path.write_text('你是白龍，稱呼使用者為星星夥伴。',encoding='utf-8')
        before=path.read_bytes()
        self.window.reload_character()
        self.window.message.setPlainText('再聊聊');self.window.send_message();self.wait_finished()
        self.assertIn('星星夥伴',self.connection.reply.call_args.kwargs['instructions'])
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(self.window.messages),4)

    def test_restart_restores_context_without_sending_inference(self):
        self.connection.reply.side_effect=self.reply
        self.window.message.setPlainText('我喜歡紫色');self.window.send_message();self.wait_finished()
        self.connection.reply.reset_mock()
        restored=ChatWindow(self.connection)
        self.assertEqual(len(restored.messages),2)
        self.assertEqual(restored.messages[0]['content'],'我喜歡紫色')
        self.connection.reply.assert_not_called()
        restored.shutdown();restored.deleteLater();self.app.processEvents()

    def test_no_valid_profile_blocks_inference_and_keeps_input(self):
        self.window.character.text=''
        self.window.message.setPlainText('不要丟失')
        self.window.send_message()
        self.connection.reply.assert_not_called()
        self.assertEqual(self.window.message.toPlainText(),'不要丟失')
        self.assertIn('Character.md',self.window.status.text())


    def test_unsaved_reply_is_preserved_and_account_switch_waits_for_save(self):
        from character_context import ContextFailure
        self.connection.reply.side_effect=self.reply
        self.window.conversation.save=Mock(side_effect=ContextFailure('測試寫入失敗'))
        self.window.message.setPlainText('不能遺失這輪對話')
        self.window.send_message();self.wait_finished()
        self.assertTrue(self.window.history_dirty)
        self.assertEqual(len(self.window.conversation_state['turns']),1)
        self.window.account.addItem('第二帳號','second')
        self.window.account.setCurrentIndex(1)
        self.connection.select.assert_not_called()
        self.assertEqual(self.window.account.currentData(),'test-account')
        self.assertEqual(len(self.window.messages),2)
        self.window.conversation.save.side_effect=None
        self.window.message.setPlainText('重試保存後繼續')
        self.window.send_message();self.wait_finished()
        self.assertFalse(self.window.history_dirty)
        self.assertEqual(len(self.window.conversation_state['turns']),2)

if __name__ == '__main__':
    unittest.main()
