import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from chat_connection import ChatConnection, CredentialStore, ConnectionFailure
from login_diagnostics import diagnose_login, phase, failure_message, save_report
from login_environment_check import collect, probe_write, probe_dpapi, probe_loopback


class LoginDiagnosticTests(unittest.TestCase):
    def test_diagnostics_do_not_serialize_secrets_from_error_or_cause(self):
        @diagnose_login
        def fail():
            phase('credential_save')
            try:
                raise PermissionError(13, 'SECRET_TOKEN /private/user/path')
            except OSError as cause:
                raise ConnectionFailure('storage_write', 'SECRET_MESSAGE') from cause
        with tempfile.TemporaryDirectory() as directory:
            captured = []
            def save(value):
                captured.append(value)
                return save_report(value, root=directory)
            try:
                fail()
            except ConnectionFailure as error:
                with patch('login_diagnostics.save_report', side_effect=save):
                    message = failure_message(error)
            content = next((Path(directory) / 'LoginDiagnostics').glob('*.json')).read_text(encoding='utf-8')
            self.assertNotIn('SECRET', content)
            self.assertNotIn('/private/user/path', content)
            self.assertIn('credential_save', message)
            self.assertEqual(captured[0]['error']['cause']['errno'], 13)

    def test_pre_browser_store_failure_has_phase_and_no_browser(self):
        store = Mock()
        store.load.return_value = {'accounts': {}, 'active': None, 'host_id': 'test'}
        store.save.side_effect = PermissionError(13, 'secret')
        with patch('chat_connection.webbrowser.open') as browser:
            with self.assertRaises(PermissionError) as caught:
                ChatConnection(store).login()
            self.assertEqual(caught.exception.login_phase, 'credential_save')
            browser.assert_not_called()

    def test_bind_failure_classified_before_browser(self):
        store = Mock()
        store.load.return_value = {'accounts': {}, 'active': None, 'host_id': 'test'}
        with patch('chat_connection.HTTPServer', side_effect=OSError(10013, 'secret')), patch('chat_connection.webbrowser.open') as browser:
            with self.assertRaises(OSError) as caught:
                ChatConnection(store).login()
            self.assertEqual(caught.exception.login_phase, 'loopback_bind')
            browser.assert_not_called()

    def test_cancel_does_not_touch_store(self):
        store = Mock()
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(ConnectionFailure) as caught:
            ChatConnection(store).login(cancel=cancel)
        self.assertEqual(caught.exception.code, 'cancelled')
        store.load.assert_not_called()

    def test_write_permission_error_preserves_existing_login(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CredentialStore(directory)
            store.path.write_bytes(b'original encrypted login')
            with patch('chat_connection.protect', return_value=b'new encrypted login'), patch('chat_connection.os.replace', side_effect=PermissionError(13, 'secret')):
                with self.assertRaises(ConnectionFailure) as caught:
                    store.save({})
            self.assertEqual(caught.exception.code, 'storage_write')
            self.assertEqual(store.path.read_bytes(), b'original encrypted login')

    def test_environment_probe_preserves_accounts_and_requires_no_network(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'accounts.dpapi'
            path.write_bytes(b'do not read or replace')
            with patch('chat_connection.urlopen', side_effect=AssertionError('network used')):
                result = collect(directory)
            self.assertTrue(all(item['ok'] for item in result['checks'].values()), result)
            self.assertEqual(path.read_bytes(), b'do not read or replace')
            self.assertEqual(list(Path(directory).iterdir()), [path])

    def test_probe_failures_report_sanitized_errno_and_continue(self):
        with patch('login_environment_check.probe_write', side_effect=PermissionError(13, 'SECRET')), patch('login_environment_check.probe_dpapi') as dpapi, patch('login_environment_check.probe_loopback') as loopback:
            result = collect('ignored')
        self.assertEqual(result['checks']['credential_write']['errno'], 13)
        self.assertNotIn('SECRET', json.dumps(result))
        dpapi.assert_called_once()
        loopback.assert_called_once()


if __name__ == '__main__':
    unittest.main()
