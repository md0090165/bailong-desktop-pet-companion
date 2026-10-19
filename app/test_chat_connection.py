import io
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch, Mock
from urllib.parse import urlencode

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from chat_connection import (ChatConnection, ConnectionFailure, CredentialStore, DIRECT_SCOPE,
    ISSUER, authorization_params, consume_stream, protect, service_error, token_record,
    validate_callback, validate_id_token)


class MemoryStore:
    def __init__(self, account=None):
        self.data = {'host_id': 'urn:uuid:host', 'active': 'issued' if account else None,
                     'accounts': {'issued': account} if account else {}}

    def load(self):
        return json.loads(json.dumps(self.data))

    def save(self, data):
        self.data = data


def stream(*events):
    return io.BytesIO(''.join('data: ' + json.dumps(event) + '\n\n' for event in events).encode())


class ConnectionTests(unittest.TestCase):
    def assert_failure(self, code, callback):
        with self.assertRaises(ConnectionFailure) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_pkce_and_returning_registration(self):
        params = authorization_params('host', 'http://127.0.0.1:1234/auth/callback', 'state', 'nonce', 'verifier')
        self.assertEqual(params['client_id'], 'dynamic_agent_client')
        self.assertEqual(params['code_challenge_method'], 'S256')
        self.assertNotIn('verifier', params.values())
        self.assertEqual(params['agent_name_hint'], 'Bailong Desktop Pet')
        returning = authorization_params('host', 'uri', 'state', 'nonce', 'verifier',
                                         {'client_id': 'issued', 'id_token': 'hint'})
        self.assertEqual(returning['client_id'], 'issued')
        self.assertNotIn('agent_name_hint', returning)
        self.assertEqual(returning['id_token_hint'], 'hint')

    def test_callback_rejects_state_missing_client_denial_and_duplicate(self):
        self.assert_failure('state', lambda: validate_callback('state=wrong&code=a&client_id=issued', 'right'))
        self.assert_failure('client', lambda: validate_callback('state=s&code=a', 's'))
        self.assert_failure('denied', lambda: validate_callback('state=s&error=access_denied', 's'))
        self.assert_failure('callback', lambda: validate_callback('state=s&state=s&code=a', 's'))
        self.assert_failure('client', lambda: validate_callback('state=s&code=a&client_id=other', 's', {'client_id': 'issued'}))
        self.assertEqual(validate_callback('state=s&code=a&client_id=issued', 's'), ('a', 'issued'))

    def test_jwt_signature_nonce_audience_expiry_and_subject(self):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        claims = {'iss': ISSUER, 'aud': 'issued', 'exp': time.time() + 60, 'sub': 'user', 'nonce': 'nonce'}
        def verify(values, nonce='nonce', subject='user'):
            encoded = jwt.encode(values, key, algorithm='RS256')
            return validate_id_token(encoded, 'issued', nonce, subject)
        with patch('chat_connection.jwt.PyJWKClient') as provider:
            provider.return_value.get_signing_key_from_jwt.return_value.key = key.public_key()
            self.assertEqual(verify(claims)['sub'], 'user')
            self.assert_failure('identity', lambda: verify(claims, nonce='wrong'))
            self.assert_failure('identity', lambda: verify(claims, subject='other'))
            self.assert_failure('identity', lambda: verify({**claims, 'aud': 'other'}))
            self.assert_failure('identity', lambda: verify({**claims, 'exp': time.time() - 1}))
            provider.return_value.get_signing_key_from_jwt.return_value.key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048).public_key()
            self.assert_failure('identity', lambda: verify(claims))

    def test_missing_plan_scope_rejected(self):
        self.assert_failure('permission', lambda: token_record({'scope': 'openid'}, 'issued', {'sub': 'u'}))

    def test_login_callback_exchange_and_save_only_verified_identity(self):
        store = MemoryStore()
        connection = ChatConnection(store)
        authorization = {}
        class FakeServer:
            server_port = 54321
            def __init__(server, address, handler):
                self.assertEqual(address, ('127.0.0.1', 0))
                server.handler = handler
            def __enter__(server):
                return server
            def __exit__(server, *args):
                pass
            def handle_request(server):
                handler = server.handler.__new__(server.handler)
                handler.path = '/auth/callback?' + urlencode({'state': authorization['state'][0],
                               'code': 'one-time-code', 'client_id': 'issued-new'})
                handler.send_response = Mock()
                handler.send_header = Mock()
                handler.end_headers = Mock()
                handler.wfile = io.BytesIO()
                handler.do_GET()
        def browser(url):
            from urllib.parse import urlparse, parse_qs
            authorization.update(parse_qs(urlparse(url).query))
            self.assertEqual(store.data['accounts'], {})
            self.assertTrue(store.data['host_id'])
            return True
        tokens = {'scope': DIRECT_SCOPE, 'access_token': 'access', 'refresh_token': 'refresh',
                  'id_token': 'signed-id', 'token_type': 'Bearer', 'expires_in': 3600}
        with patch('chat_connection.HTTPServer', FakeServer), patch('chat_connection.webbrowser.open', browser), \
             patch('chat_connection.request_json', return_value=tokens) as exchange, \
             patch('chat_connection.validate_id_token', return_value={'sub': 'verified-user', 'email': 'label'}) as verify:
            connection.login()
            fields = exchange.call_args.args[1]
            self.assertEqual(fields['client_id'], 'issued-new')
            self.assertEqual(fields['redirect_uri'], authorization['redirect_uri'][0])
            self.assertEqual(verify.call_args.args[2], authorization['nonce'][0])
        self.assertEqual(store.data['active'], 'issued-new')
        self.assertEqual(store.data['accounts']['issued-new']['subject'], 'verified-user')

    def test_cancel_login_exits_listener_without_account_or_token_exchange(self):
        cancel = threading.Event()
        cancel.set()
        server = Mock()
        server.server_port = 54321
        server.__enter__ = Mock(return_value=server)
        server.__exit__ = Mock(return_value=False)
        store = MemoryStore()
        with patch('chat_connection.HTTPServer', return_value=server), \
             patch('chat_connection.webbrowser.open', return_value=True), \
             patch('chat_connection.request_json') as exchange:
            self.assert_failure('cancelled', lambda: ChatConnection(store).login(cancel))
            exchange.assert_not_called()
            server.__exit__.assert_not_called()
        self.assertEqual(store.data['accounts'], {})

    def test_windows_encrypted_storage_atomic_and_host_stable(self):
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(folder)
            state = store.load()
            state['secret'] = 'a-secret-token-never-plaintext'
            store.save(state)
            self.assertNotIn(b'a-secret-token', store.path.read_bytes())
            self.assertEqual(store.load(), state)
            self.assertFalse(store.path.with_suffix('.tmp').exists())
            self.assertEqual(json.loads(protect(protect(b'{"ok": true}'), decrypt=True)), {'ok': True})

    def test_no_account_no_network(self):
        with patch('chat_connection.request_json') as request:
            self.assert_failure('authentication', lambda: ChatConnection(MemoryStore()).models())
            request.assert_not_called()

    def test_refresh_rotates_tokens_preserves_account(self):
        account = {'client_id': 'issued', 'subject': 'u', 'email': 'label', 'scope': DIRECT_SCOPE,
                   'access_token': 'old', 'refresh_token': 'rotate1', 'expires_at': 0}
        store = MemoryStore(account)
        connection = ChatConnection(store)
        with patch('chat_connection.request_json', return_value={'access_token': 'new',
                   'refresh_token': 'rotate2', 'token_type': 'Bearer', 'expires_in': 3600}) as request:
            self.assertEqual(connection._active_token(), 'new')
            self.assertEqual(store.data['accounts']['issued']['refresh_token'], 'rotate2')
            self.assertEqual(request.call_args.args[1]['client_id'], 'issued')
            self.assertEqual(connection._active_token(), 'new')
            self.assertEqual(request.call_count, 1)

    def test_catalog_filters_hidden_and_preserves_order(self):
        connection = ChatConnection(MemoryStore())
        with patch.object(connection, '_active_token', return_value='token'), patch('chat_connection.request_json',
             return_value={'models': [{'slug': 'a', 'visibility': 'list'}, {'slug': 'hidden'},
                                      {'slug': 'b', 'visibility': 'list', 'display_name': '模型 B'}]}):
            self.assertEqual(connection.models(), [{'id': 'a', 'name': 'a'}, {'id': 'b', 'name': '模型 B'}])

    def test_stream_completed_and_late_usage_error(self):
        delta = {'type': 'response.output_text.delta', 'delta': '你好'}
        seen = []
        self.assertEqual(consume_stream(stream(delta, {'type': 'response.completed'}), on_delta=seen.append), '你好')
        self.assertEqual(seen, ['你好'])
        self.assert_failure('usage_limit', lambda: consume_stream(stream(delta, {'type': 'response.failed',
             'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded'}}})))

    def test_stream_interrupted_incomplete_cancelled_and_timeout(self):
        self.assert_failure('interrupted', lambda: consume_stream(stream({'type': 'response.output_text.delta', 'delta': 'partial'})))
        self.assert_failure('incomplete', lambda: consume_stream(stream({'type': 'response.incomplete'})))
        cancel = threading.Event()
        cancel.set()
        self.assert_failure('cancelled', lambda: consume_stream(io.BytesIO(), cancel))
        self.assert_failure('timeout', lambda: consume_stream(io.BytesIO(), timeout=-1))
        self.assert_failure('empty', lambda: consume_stream(stream({'type': 'response.completed'})))

    def test_payload_has_no_tools_and_no_stored_conversation(self):
        connection = ChatConnection(MemoryStore())
        response = stream({'type': 'response.output_text.delta', 'delta': 'ok'}, {'type': 'response.completed'})
        with patch.object(connection, '_active_token', return_value='token'), patch('chat_connection.open_request', return_value=response) as request:
            self.assertEqual(connection.reply('selected', [{'role': 'user', 'content': 'hello'}]), 'ok')
            payload = json.loads(request.call_args.args[0].data)
            self.assertFalse(payload['store'])
            self.assertTrue(payload['stream'])
            self.assertNotIn('tools', payload)
            self.assertEqual(payload['model'], 'selected')

    def test_logout_clears_local_tokens_on_network_failure(self):
        account = {'client_id': 'issued', 'access_token': 'token', 'refresh_token': 'refresh',
                   'id_token': 'id', 'expires_at': 1, 'subject': 'u'}
        store = MemoryStore(account)
        with patch('chat_connection.request_json', side_effect=ConnectionFailure('network', 'network')):
            self.assertIn('尚未確認', ChatConnection(store).logout())
        self.assertEqual(store.data['accounts']['issued']['client_id'], 'issued')
        self.assertNotIn('access_token', store.data['accounts']['issued'])


if __name__ == '__main__':
    unittest.main()
