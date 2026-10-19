import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlparse

from chat_connection import ChatConnection, ConnectionFailure, DIRECT_SCOPE, open_request
from login_diagnostics import error_details
from test_chat_connection import MemoryStore


class RecoveryTests(unittest.TestCase):
    def test_http_failure_retains_only_allowlisted_error_identifier(self):
        for code, expected in [('invalid_grant', 'invalid_grant'), ('SECRET-TOKEN', 'other')]:
            response = HTTPError('https://example.invalid/SECRET', 400, 'SECRET', {},
                                 io.BytesIO(json.dumps({'error': code, 'error_description': 'SECRET'}).encode()))
            with patch('chat_connection.urlopen', side_effect=response):
                with self.assertRaises(ConnectionFailure) as caught:
                    open_request(None)
            details = error_details(caught.exception)
            self.assertEqual(details['http_status'], 400)
            self.assertEqual(details['oauth_error'], expected)
            self.assertNotIn('SECRET', json.dumps(details))

    def test_invalid_grant_retry_keeps_issued_id_and_uses_fresh_pkce_code_and_state(self):
        store = MemoryStore()
        connection = ChatConnection(store)
        attempts = []
        current = []

        class FakeServer:
            def __init__(self, address, handler):
                self.handler = handler
                self.server_port = 12345
                current[:] = [self]
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def handle_request(self):
                request = self.handler.__new__(self.handler)
                request.path = '/auth/callback?' + urlencode({'state': attempts[-1]['state'][0],
                    'client_id': 'issued-registration', 'code': 'fresh-' + str(len(attempts))})
                request.send_response = lambda *args: None
                request.send_header = lambda *args: None
                request.end_headers = lambda: None
                request.wfile = io.BytesIO()
                request.do_GET()

        def browser(url):
            attempts.append(parse_qs(urlparse(url).query))
            return True

        error = ConnectionFailure('authentication', 'generic')
        error.http_status = 400
        error.oauth_error = 'invalid_grant'
        tokens = {'scope': DIRECT_SCOPE, 'access_token': 'fake', 'token_type': 'bearer', 'id_token': 'fake'}
        with patch('chat_connection.HTTPServer', FakeServer), patch('chat_connection.webbrowser.open', browser), \
             patch('chat_connection.request_json', side_effect=[error, tokens]) as exchange, \
             patch('chat_connection.validate_id_token', return_value={'sub': 'verified'}) as verify:
            with self.assertRaises(ConnectionFailure) as caught:
                connection.login()
            self.assertEqual(caught.exception.login_phase, 'token_exchange')
            self.assertEqual(store.data['accounts'], {})
            connection.login()
        self.assertEqual(attempts[0]['client_id'], ['dynamic_agent_client'])
        self.assertEqual(attempts[1]['client_id'], ['issued-registration'])
        for field in ('state', 'nonce', 'code_challenge'):
            self.assertNotEqual(attempts[0][field], attempts[1][field])
        first, second = (call.args[1] for call in exchange.call_args_list)
        self.assertNotEqual(first['code'], second['code'])
        self.assertNotEqual(first['code_verifier'], second['code_verifier'])
        self.assertIsNone(verify.call_args.args[3])
        self.assertIsNone(connection._pending_registration)
        self.assertEqual(store.data['active'], 'issued-registration')


if __name__ == '__main__':
    unittest.main()
