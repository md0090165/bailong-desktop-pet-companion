import base64
import copy
import io
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

from gemini_connection import (ModelServices, ProviderStore, LOCAL_PROFILE, BASE,
                               contents, request, ConnectionFailure)


class FakeStore:
    def __init__(self):
        self.value = {'provider': 'chatgpt', 'key': 'K' * 32, 'profile': None}
    def load(self): return copy.deepcopy(self.value)
    def save(self, value): self.value = copy.deepcopy(value)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        self.store = FakeStore()
        self.chat = Mock()
        self.chat.accounts.return_value = [{'id': 'old-account', 'label': '舊紀錄', 'active': True, 'signed_in': True}]
        self.service = ModelServices(self.chat, self.store)

    def test_chatgpt_unchanged(self):
        self.assertEqual(self.service.accounts(), self.chat.accounts.return_value)
        self.service.models()
        self.chat.models.assert_called_once()
        self.service.reply('old-model', [{'role': 'user', 'content': '你好'}])
        self.chat.reply.assert_called_once()

    def test_existing_profile_and_local_only_profile(self):
        self.service.set_provider('gemini')
        self.assertEqual(next(a['id'] for a in self.service.accounts() if a['active']), 'old-account')
        self.service.select(LOCAL_PROFILE)
        self.assertEqual(next(a['id'] for a in self.service.accounts() if a['active']), LOCAL_PROFILE)
        self.chat.select.assert_not_called()
        self.chat.accounts.return_value = []
        self.assertTrue(self.service.accounts()[0]['active'])

    def test_key_validation_removal(self):
        self.service.save_key(' A' + 'B' * 30 + ' ')
        self.assertTrue(self.service.has_key())
        with self.assertRaises(ConnectionFailure): self.service.save_key('invalid key')
        self.service.save_key('')
        self.assertFalse(self.service.has_key())

    def test_auth_keys_are_opaque_accept_periods_and_long_values(self):
        for key in ('AIza' + 'a' * 35, 'AQ.' + 'aB0_-' * 16, 'AQ.' + 'aB0_-.' * 200):
            self.service.save_key('  ' + key + '\r\n')
            self.assertEqual(self.store.load()['key'], key)

    def test_reject_header_injection_urls_and_non_ascii_without_overwriting_key(self):
        original = self.store.load()['key']
        for key in ('short', 'AQ.' + 'a' * 20 + '\r\nX-Test:bad', 'AQ.' + 'a' * 20 + ' space',
                    'https://aistudio.google.com/apikey', 'AQ.' + 'a' * 20 + '全形', 'a' * 8193):
            with self.assertRaises(ConnectionFailure): self.service.save_key(key)
            self.assertEqual(self.store.load()['key'], original)

    @patch('gemini_connection.urlopen')
    def test_long_auth_key_reaches_header_unchanged_never_url(self, call):
        key = 'AQ.' + 'aB0_-.' * 200
        self.service.save_key(key)
        self.service.set_provider('gemini')
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"models":[]}'
        call.return_value = response
        self.assertEqual(self.service.models(), [])
        req = call.call_args.args[0]
        self.assertEqual(req.get_header('X-goog-api-key'), key)
        self.assertNotIn(key, req.full_url)

    @patch('gemini_connection.request')
    def test_model_pagination_and_filtering(self, call):
        self.service.set_provider('gemini')
        call.side_effect = [
            {'models': [
                {'name': 'models/gemini-a', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/gemini-image', 'supportedGenerationMethods': ['generateContent']},
                {'name': 'models/embedding', 'supportedGenerationMethods': ['embedContent']}], 'nextPageToken': 'next'},
            {'models': [{'name': 'models/gemini-b', 'displayName': '測試 B', 'supportedGenerationMethods': ['generateContent']}]}]
        self.assertEqual([x['id'] for x in self.service.models()], ['gemini:gemini-a', 'gemini:gemini-b'])
        self.assertIn('pageToken=next', call.call_args_list[1].args[0])

    def test_text_and_checked_images(self):
        data = base64.b64encode(b'png').decode()
        converted = contents([
            {'role': 'user', 'content': '背景'}, {'role': 'user', 'content': '你好'},
            {'role': 'assistant', 'content': '主人'},
            {'role': 'user', 'content': [{'type': 'input_text', 'text': '桌面'}, {'type': 'input_image', 'image_url': 'data:image/png;base64,' + data}]}])
        self.assertEqual([c['role'] for c in converted], ['user', 'model', 'user'])
        self.assertEqual(converted[-1]['parts'][-1]['inlineData']['data'], data)
        with self.assertRaises(ConnectionFailure):
            contents([{'role': 'user', 'content': [{'type': 'input_image', 'image_url': 'https://example.com/a.png'}]}])

    @patch('gemini_connection.request')
    def test_reply_persona_context_animation_and_thought_filter(self, call):
        self.service.set_provider('gemini')
        call.return_value = {'candidates': [{'finishReason': 'STOP', 'content': {'parts': [
            {'thought': True, 'text': 'private thought'}, {'text': '{"text":"主人好","animation_id":"04"}'}]}}]}
        delta = Mock()
        result = self.service.reply('gemini:gemini-a', [{'role': 'user', 'content': '記憶與對話'}], instructions='角色核心', on_delta=delta)
        self.assertNotIn('private', result)
        self.assertEqual(call.call_args.args[2]['systemInstruction']['parts'][0]['text'], '角色核心')
        delta.assert_called_once_with(result)
        self.chat.reply.assert_not_called()

    @patch('gemini_connection.request')
    def test_incomplete_blocked_empty_and_cancellation(self, call):
        self.service.set_provider('gemini')
        for response in ({'candidates': [{'finishReason': 'MAX_TOKENS'}]}, {'promptFeedback': {'blockReason': 'SAFETY'}},
                         {'candidates': [{'finishReason': 'STOP', 'content': {'parts': []}}]}):
            call.return_value = response
            with self.assertRaises(ConnectionFailure): self.service.reply('gemini:gemini-a', [{'role': 'user', 'content': 'x'}])
        self.service.set_provider('chatgpt')
        with self.assertRaises(ConnectionFailure): self.service.reply('gemini:gemini-a', [])

    @patch('gemini_connection.urlopen')
    def test_http_request_header_secret_and_safe_errors(self, call):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = b'{"models":[]}'
        call.return_value = response
        request('models', 'SECRET')
        req = call.call_args.args[0]
        self.assertEqual(req.full_url, BASE + 'models')
        self.assertEqual(req.get_header('X-goog-api-key'), 'SECRET')
        for status in (400, 401, 403, 404, 429, 500):
            call.side_effect = HTTPError(BASE, status, 'SECRET', {}, io.BytesIO(b'SECRET'))
            with self.assertRaises(ConnectionFailure) as error: request('models', 'SECRET')
            self.assertNotIn('SECRET', str(error.exception))
        call.reset_mock()
        cancel = threading.Event(); cancel.set()
        with self.assertRaises(ConnectionFailure): request('models', 'SECRET', cancel=cancel)
        call.assert_not_called()

    def test_real_dpapi_roundtrip_no_plaintext(self):
        with tempfile.TemporaryDirectory() as folder:
            store = ProviderStore(folder)
            self.assertEqual(store.load()['provider'], 'chatgpt')
            store.save({'provider': 'gemini', 'key': 'SECRET-TEST-KEY', 'profile': LOCAL_PROFILE})
            self.assertNotIn(b'SECRET-TEST-KEY', store.path.read_bytes())
            self.assertEqual(store.load()['key'], 'SECRET-TEST-KEY')


class UITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_switch_provider_preserves_conversation_and_separate_model_preferences(self):
        from chat_window import ChatWindow
        from character_context import ConversationStore, empty_state, completed_turn
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'Character.md').write_text('你是白龍。', encoding='utf-8')
            chat = Mock()
            chat.store.folder = root
            chat.accounts.return_value = [{'id': 'old-account', 'label': '舊紀錄', 'active': True, 'signed_in': True}]
            state = empty_state(); state['turns'].append(completed_turn('請記住', '記得了', 'old-model', 'digest'))
            ConversationStore(root, 'old-account').save(state)
            service = ModelServices(chat, FakeStore())
            window = ChatWindow(service, root)
            window.models_ready([{'id': 'old-model', 'name': '舊模型'}])
            window.provider.setCurrentIndex(1)
            self.assertEqual(window.session_account, 'old-account')
            self.assertEqual(len(window.conversation_state['turns']), 1)
            window.models_ready([{'id': 'gemini:gemini-a', 'name': 'Gemini'}])
            self.assertEqual(window.preferences()['old-account'], 'gemini:gemini-a')
            window.provider.setCurrentIndex(0)
            self.assertEqual(window.preferences()['old-account'], 'old-model')
            window.shutdown(); window.deleteLater(); self.app.processEvents()


if __name__ == '__main__': unittest.main()
