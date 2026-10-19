"""Independent ChatGPT plan connection. No shell, file, or agent tools are exposed."""
import base64
import ctypes
import hashlib
import hmac
import json
import os
import secrets
import socket
import threading
import time
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlparse
from urllib.request import Request, urlopen

import jwt
from login_diagnostics import diagnose_login, phase

ISSUER = 'https://auth.openai.com'
AUTHORIZE = ISSUER + '/api/accounts/authorize'
TOKEN = ISSUER + '/api/accounts/oauth/token'
RESOURCE = 'https://api.openai.com/v1'
DIRECT_SCOPE = 'chatgpt.tokens.use.direct'
SCOPES = 'openid profile email offline_access resource.invoke ' + DIRECT_SCOPE


class ConnectionFailure(RuntimeError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def service_error(status=0, code=''):
    if status == 401 or code in ('invalid_grant', 'invalid_token'):
        return ConnectionFailure('authentication', '登入已失效，請重新使用 ChatGPT 登入。')
    if status == 429 or code in ('subscription_sharing_usage_limit_exceeded',
                                'subscription_sharing_usage_unavailable', 'insufficient_quota'):
        return ConnectionFailure('usage_limit', '目前帳號的可用額度不足或暫不可用，請查看 ChatGPT 使用量設定。')
    if status == 403:
        return ConnectionFailure('permission', '帳號尚未授權此應用，或沒有使用所選模型的權限。')
    return ConnectionFailure('service', '服務暫時無法完成請求，請稍後再試。')


def check_cancel(cancel):
    if cancel and cancel.is_set():
        raise ConnectionFailure('cancelled', '操作已取消。')


def open_request(request):
    try:
        return urlopen(request, timeout=20)
    except HTTPError as error:
        try:
            body = json.loads(error.read(65536))
            info = body.get('error', {})
            code = info.get('code', '') if isinstance(info, dict) else info
        except (ValueError, OSError):
            code = ''
        failure = service_error(error.code, code)
        failure.http_status = error.code
        # Only fixed OAuth identifiers are allowed into diagnostics; never server prose.
        failure.oauth_error = code if code in ('invalid_grant', 'invalid_token', 'invalid_client',
            'unauthorized_client', 'access_denied', 'invalid_request', 'invalid_scope',
            'unsupported_grant_type', 'temporarily_unavailable', 'server_error') else 'other'
        raise failure from None
    except (URLError, TimeoutError, OSError):
        raise ConnectionFailure('network', '連線失敗或逾時，請確認網路後再試。') from None


def request_json(url, fields=None, token=None, form=False):
    headers = {'Accept': 'application/json'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    data = None
    if fields is not None:
        data = (urlencode(fields) if form else json.dumps(fields)).encode('utf-8')
        headers['Content-Type'] = 'application/x-www-form-urlencoded' if form else 'application/json'
    with open_request(Request(url, data=data, headers=headers)) as response:
        try:
            return json.load(response)
        except ValueError:
            raise ConnectionFailure('protocol', '服務回傳的資料格式無法辨識。') from None


class _Blob(ctypes.Structure):
    _fields_ = [('size', ctypes.c_ulong), ('data', ctypes.POINTER(ctypes.c_ubyte))]


def protect(raw, decrypt=False):
    """Windows DPAPI, bound to the signed-in Windows user; never plaintext fallback."""
    if os.name != 'nt':
        raise ConnectionFailure('storage', '此版本的登入資料保護功能需要 Windows。')
    buffer = ctypes.create_string_buffer(raw)
    source = _Blob(len(raw), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    target = _Blob()
    if decrypt:
        ok = ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(source), None, None,
                                                     None, None, 1, ctypes.byref(target))
    else:
        ok = ctypes.windll.crypt32.CryptProtectData(ctypes.byref(source), 'Bailong ChatGPT',
                                                   None, None, None, 1, ctypes.byref(target))
    if not ok:
        raise ConnectionFailure('storage', '無法保護或讀取登入資料，請重新登入。')
    try:
        return ctypes.string_at(target.data, target.size)
    finally:
        ctypes.windll.kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        ctypes.windll.kernel32.LocalFree(target.data)


class CredentialStore:
    def __init__(self, folder=None):
        from portable_support import credentials_folder
        portable=credentials_folder() if folder is None else None
        self.folder = Path(folder) if folder else portable or Path(os.environ['LOCALAPPDATA']) / 'BailongDesktopPet' / 'chat'
        self.path = self.folder / 'accounts.dpapi'

    def load(self):
        if not self.path.exists():
            return {'host_id': 'urn:uuid:' + str(uuid.uuid4()), 'accounts': {}, 'active': None}
        try:
            return json.loads(protect(self.path.read_bytes(), decrypt=True))
        except (OSError, ValueError):
            raise ConnectionFailure('storage', '無法讀取登入資料，請檢查檔案存取權限。') from None

    def save(self, data):
        try:
            self.folder.mkdir(parents=True, exist_ok=True)
            raw = protect(json.dumps(data, ensure_ascii=False).encode('utf-8'))
            temporary = self.path.with_suffix('.tmp')
            temporary.write_bytes(raw)
            os.replace(temporary, self.path)
        except OSError as error:
            raise ConnectionFailure('storage_write', '無法儲存登入資料，請確認程式資料夾可寫入。') from error


def authorization_params(host_id, redirect_uri, state, nonce, verifier, account=None):
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode('ascii')).digest()).decode().rstrip('=')
    params = {'client_id': account['client_id'] if account else 'dynamic_agent_client',
              'ext_agent_host_id': host_id, 'response_type': 'code', 'redirect_uri': redirect_uri,
              'scope': SCOPES, 'resource': RESOURCE, 'state': state, 'nonce': nonce,
              'code_challenge_method': 'S256', 'code_challenge': challenge}
    if account:
        if account.get('id_token'):
            params['id_token_hint'] = account['id_token']
    else:
        params['agent_name_hint'] = 'Bailong Desktop Pet'
    return params


def validate_callback(query, state, account=None):
    values = parse_qs(query)
    if any(len(items) != 1 for items in values.values()):
        raise ConnectionFailure('callback', '登入回傳資料重複，請重試。')
    if not hmac.compare_digest(values.get('state', [''])[0], state):
        raise ConnectionFailure('state', '登入驗證不符，請重新登入。')
    if 'error' in values:
        raise ConnectionFailure('denied', '登入或 ChatGPT 方案使用授權未完成。')
    client_id = values.get('client_id', [account['client_id'] if account else ''])[0]
    if not client_id or client_id == 'dynamic_agent_client' or (account and client_id != account['client_id']):
        raise ConnectionFailure('client', '登入應用識別不符，請重新登入。')
    code = values.get('code', [''])[0]
    if not code:
        raise ConnectionFailure('callback', '登入未回傳授權碼，請重試。')
    return code, client_id


def identity_failure(reason):
    failure = ConnectionFailure('identity', '無法驗證登入身分，請重新登入。')
    failure.identity_reason = reason
    return failure


def validate_id_token(encoded, client_id, nonce=None, expected_subject=None):
    try:
        key = jwt.PyJWKClient(ISSUER + '/.well-known/jwks.json', timeout=20).get_signing_key_from_jwt(encoded)
    except (jwt.PyJWKClientConnectionError, URLError, TimeoutError, OSError) as error:
        raise identity_failure('signing_key_download') from error
    except jwt.DecodeError as error:
        raise identity_failure('token_format') from error
    except (jwt.PyJWTError, ValueError) as error:
        raise identity_failure('signing_key_response') from error
    try:
        claims = jwt.decode(encoded, key.key, algorithms=['RS256'], issuer=ISSUER, audience=client_id,
                            options={'require': ['exp', 'iss', 'aud', 'sub']})
    except jwt.ExpiredSignatureError as error:
        raise identity_failure('token_expired') from error
    except jwt.ImmatureSignatureError as error:
        raise identity_failure('token_not_yet_valid') from error
    except jwt.InvalidAudienceError as error:
        raise identity_failure('audience_mismatch') from error
    except jwt.InvalidIssuerError as error:
        raise identity_failure('issuer_mismatch') from error
    except jwt.InvalidSignatureError as error:
        raise identity_failure('signature_invalid') from error
    except jwt.InvalidAlgorithmError as error:
        raise identity_failure('algorithm_invalid') from error
    except jwt.MissingRequiredClaimError as error:
        raise identity_failure('required_claim_missing') from error
    except (jwt.PyJWTError, ValueError) as error:
        raise identity_failure('token_claim_invalid') from error
    if nonce is not None and not hmac.compare_digest(str(claims.get('nonce', '')), nonce):
        raise identity_failure('nonce_mismatch')
    if expected_subject is not None and claims['sub'] != expected_subject:
        raise identity_failure('account_mismatch')
    return claims


def token_record(tokens, client_id, claims, previous=None):
    if DIRECT_SCOPE not in tokens.get('scope', '').split():
        raise ConnectionFailure('permission', '尚未授權使用 ChatGPT 方案，請重新登入並完成授權。')
    if not tokens.get('access_token') or tokens.get('token_type', '').lower() != 'bearer':
        raise ConnectionFailure('protocol', '服務未回傳可用登入憑證。')
    return {**(previous or {}), **tokens, 'client_id': client_id, 'subject': claims['sub'],
            'email': claims.get('email', (previous or {}).get('email', 'ChatGPT 帳號')),
            'expires_at': time.time() + int(tokens.get('expires_in', 3600))}


class ChatConnection:
    def __init__(self, store=None):
        self.store = store or CredentialStore()
        self.lock = threading.RLock()
        # Unverified registration ID only, held in RAM after an exchange failure.
        # It is not a signed-in account and must never overwrite persisted credentials.
        self._pending_registration = None

    def accounts(self):
        data = self.store.load()
        return [{'id': key, 'label': value.get('email', 'ChatGPT 帳號') + ' · ' + key[-8:],
                 'signed_in': bool(value.get('access_token')), 'active': data['active'] == key}
                for key, value in data['accounts'].items()]

    def select(self, account_id):
        data = self.store.load()
        if account_id not in data['accounts']:
            raise ConnectionFailure('authentication', '請先登入 ChatGPT。')
        data['active'] = account_id
        self.store.save(data)

    def logout(self):
        with self.lock:
            data = self.store.load()
            account = data['accounts'].get(data['active'])
            confirmed = False
            if account and account.get('refresh_token'):
                try:
                    metadata = request_json(ISSUER + '/.well-known/openid-configuration')
                    endpoint = metadata.get('revocation_endpoint', '')
                    if not endpoint.startswith(ISSUER + '/'):
                        raise ConnectionFailure('protocol', '無法確認登出服務端點。')
                    request = Request(endpoint, data=urlencode({'token': account['refresh_token'],
                                      'token_type_hint': 'refresh_token', 'client_id': account['client_id']}).encode(),
                                      headers={'Content-Type': 'application/x-www-form-urlencoded'})
                    with open_request(request):
                        confirmed = True
                except ConnectionFailure:
                    pass
            if account:
                for key in ('access_token', 'refresh_token', 'id_token', 'expires_at'):
                    account.pop(key, None)
                self.store.save(data)
            return ('已登出。' if confirmed else
                    '已清除本機登入。尚未確認遠端撤銷；可在 ChatGPT 設定中解除此應用。')

    @diagnose_login
    def login(self, cancel=None, on_status=None, new_account=False):
        with self.lock:
            check_cancel(cancel)
            phase('credential_load')
            data = self.store.load()
            phase('credential_save')
            self.store.save(data)  # Persist host identity before opening authorization.
            selected = data['accounts'].get(data['active']) if not new_account else None
            if not new_account and selected is None and self._pending_registration:
                selected = {'client_id': self._pending_registration}
            if new_account:
                self._pending_registration = None
            state, nonce, verifier = (secrets.token_urlsafe(48) for _ in range(3))
            received = []

            class Callback(BaseHTTPRequestHandler):
                def do_GET(handler):
                    parsed = urlparse(handler.path)
                    if parsed.path != '/auth/callback':
                        handler.send_error(404)
                        return
                    try:
                        result = validate_callback(parsed.query, state, selected)
                    except ConnectionFailure as error:
                        if error.code == 'state':
                            handler.send_error(400)
                            return
                        result = error
                    received.append(result)
                    handler.send_response(200)
                    handler.send_header('Content-Type', 'text/html; charset=utf-8')
                    handler.send_header('Cache-Control', 'no-store')
                    handler.send_header('Content-Security-Policy', "default-src 'none'")
                    handler.end_headers()
                    handler.wfile.write('<meta charset="utf-8"><p>已收到登入結果，請回到白龍桌寵連線檢查視窗。</p>'.encode('utf-8'))

                def log_message(self, *args):
                    pass  # Callback URLs contain authorization codes.

            phase('loopback_bind')
            with HTTPServer(('127.0.0.1', 0), Callback) as server:
                server.timeout = .2
                redirect_uri = f'http://127.0.0.1:{server.server_port}/auth/callback'
                params = authorization_params(data['host_id'], redirect_uri, state, nonce, verifier, selected)
                check_cancel(cancel)
                phase('browser_launch')
                if not webbrowser.open(AUTHORIZE + '?' + urlencode(params)):
                    raise ConnectionFailure('browser', '無法開啟預設瀏覽器，請確認 Windows 預設瀏覽器設定。')
                if on_status:
                    on_status('請在瀏覽器完成 ChatGPT 登入與授權。')
                phase('callback_wait')
                deadline = time.monotonic() + 180
                while not received:
                    check_cancel(cancel)
                    if time.monotonic() >= deadline:
                        raise ConnectionFailure('timeout', '登入等待逾時，請重新登入。')
                    server.handle_request()
            if isinstance(received[0], ConnectionFailure):
                raise received[0]
            check_cancel(cancel)
            code, client_id = received[0]
            phase('token_exchange')
            try:
                tokens = request_json(TOKEN, {'grant_type': 'authorization_code', 'client_id': client_id,
                                      'code': code, 'code_verifier': verifier, 'redirect_uri': redirect_uri,
                                      'resource': RESOURCE}, form=True)
            except ConnectionFailure as error:
                if getattr(error, 'oauth_error', None) == 'invalid_grant':
                    self._pending_registration = client_id
                raise
            phase('identity_verify')
            claims = validate_id_token(tokens.get('id_token', ''), client_id, nonce,
                                       selected.get('subject') if selected else None)
            phase('token_record')
            account = token_record(tokens, client_id, claims)
            check_cancel(cancel)
            data['accounts'][client_id] = account
            data['active'] = client_id
            phase('credential_commit')
            self.store.save(data)
            self._pending_registration = None
            return '登入成功，已取得 ChatGPT 方案使用授權。'

    def _active_token(self):
        data = self.store.load()
        account = data['accounts'].get(data['active'])
        if not account or not account.get('access_token'):
            raise ConnectionFailure('authentication', '請先使用 ChatGPT 登入。')
        if time.time() >= account['expires_at'] - 60:
            if not account.get('refresh_token'):
                raise ConnectionFailure('authentication', '登入已過期，請重新登入。')
            tokens = request_json(TOKEN, {'grant_type': 'refresh_token', 'client_id': account['client_id'],
                                  'refresh_token': account['refresh_token'], 'resource': RESOURCE}, form=True)
            claims = {'sub': account['subject'], 'email': account.get('email', '')}
            if tokens.get('id_token'):
                claims = validate_id_token(tokens['id_token'], account['client_id'], expected_subject=account['subject'])
            # OAuth permits omitted scope on refresh; the previous grant remains active.
            tokens.setdefault('scope', account['scope'])
            data['accounts'][data['active']] = token_record(tokens, account['client_id'], claims, account)
            self.store.save(data)
            account = data['accounts'][data['active']]
        return account['access_token']

    def models(self, cancel=None):
        with self.lock:
            check_cancel(cancel)
            result = request_json(RESOURCE + '/models', token=self._active_token())
            check_cancel(cancel)
            return [{'id': item['slug'], 'name': item.get('display_name', item['slug'])}
                    for item in result.get('models', []) if item.get('visibility') == 'list']

    def reply(self, model, messages, instructions='請使用繁體中文簡短回覆。', cancel=None, on_delta=None):
        with self.lock:
            check_cancel(cancel)
            if not model or not messages:
                raise ConnectionFailure('input', '請選擇模型並輸入訊息。')
            payload = {'model': model, 'input': messages, 'instructions': instructions,
                       'store': False, 'stream': True}
            request = Request(RESOURCE + '/responses', data=json.dumps(payload).encode('utf-8'), headers={
                'Authorization': 'Bearer ' + self._active_token(), 'Content-Type': 'application/json',
                'Accept': 'text/event-stream'})
            with open_request(request) as response:
                return consume_stream(response, cancel, on_delta)


def consume_stream(response, cancel=None, on_delta=None, timeout=120):
    deadline = time.monotonic() + timeout
    parts, data_lines = [], []
    try:
        while True:
            check_cancel(cancel)
            if time.monotonic() >= deadline:
                raise ConnectionFailure('timeout', '回覆等待逾時，請稍後再試。')
            raw = response.readline(1024 * 1024 + 1)
            if len(raw) > 1024 * 1024:
                raise ConnectionFailure('protocol', '回覆資料過長，已停止接收。')
            if not raw:
                raise ConnectionFailure('interrupted', '回覆連線中斷，尚未收到完整回覆。')
            line = raw.decode('utf-8').rstrip('\r\n')
            if line.startswith('data:'):
                data_lines.append(line[5:].lstrip())
            elif not line and data_lines:
                event = json.loads('\n'.join(data_lines))
                data_lines.clear()
                kind = event.get('type')
                if kind == 'response.output_text.delta':
                    delta = event.get('delta', '')
                    parts.append(delta)
                    if on_delta:
                        on_delta(delta)
                elif kind == 'response.completed':
                    text = ''.join(parts)
                    if not text.strip():
                        raise ConnectionFailure('empty', '模型未回傳文字，請重試或選擇其他模型。')
                    return text
                elif kind in ('response.failed', 'error'):
                    error = event.get('response', {}).get('error') or event.get('error') or event
                    raise service_error(code=error.get('code', ''))
                elif kind == 'response.incomplete':
                    raise ConnectionFailure('incomplete', '回覆未完成，請稍後再試。')
    except (socket.timeout, TimeoutError):
        raise ConnectionFailure('timeout', '回覆等待逾時，請稍後再試。') from None
    except (OSError, UnicodeError, ValueError):
        raise ConnectionFailure('interrupted', '回覆連線中斷或格式異常，請重試。') from None
