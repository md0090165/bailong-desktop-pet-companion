"""Gemini REST adapter. Fixed HTTPS host, DPAPI credentials, no external tools."""
import base64
import json
import re
import threading
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from chat_connection import ChatConnection, CredentialStore, ConnectionFailure, check_cancel

BASE = 'https://generativelanguage.googleapis.com/v1beta/'
LOCAL_PROFILE = 'gemini-local'


class ProviderStore(CredentialStore):
    def __init__(self, folder):
        super().__init__(folder)
        self.path = self.folder / 'model-services.dpapi'

    def load(self):
        if not self.path.exists():
            return {'provider': 'chatgpt', 'key': '', 'profile': None}
        data = super().load()
        if data.get('provider') not in ('chatgpt', 'gemini'):
            raise ConnectionFailure('storage', '模型服務設定無法讀取，請重新設定。')
        return data


def gemini_error(status):
    messages = {
        400: ('input', 'Gemini 請求不被接受，請確認金鑰、模型及圖片支援。'),
        401: ('authentication', 'Gemini API Key 無效，請重新設定。'),
        403: ('permission', 'Gemini 拒絕存取，請確認 API Key 權限、服務啟用及所在地資格。'),
        404: ('model', 'Gemini 模型不存在或已下架，請重新載入模型。'),
        429: ('usage_limit', 'Gemini 額度或速率已達限制，請稍後再試，或在 Google AI Studio 查看用量。'),
    }
    code, message = messages.get(status, ('service', 'Gemini 服務暫時無法使用，請稍後再試。'))
    return ConnectionFailure(code, message)


def request(path, key, payload=None, cancel=None):
    check_cancel(cancel)
    if not key:
        raise ConnectionFailure('authentication', '請先在參數設定輸入 Gemini API Key。')
    headers = {'Accept': 'application/json', 'x-goog-api-key': key}
    data = None
    if payload is not None:
        headers['Content-Type'] = 'application/json'
        data = json.dumps(payload, ensure_ascii=False).encode('utf-8')
    try:
        # Key stays in a header, never a URL, diagnostic, conversation, or cloud snapshot.
        with urlopen(Request(BASE + path, data=data, headers=headers), timeout=45) as response:
            raw = response.read(8 * 1024 * 1024 + 1)
        check_cancel(cancel)
        if len(raw) > 8 * 1024 * 1024:
            raise ConnectionFailure('protocol', 'Gemini 回覆資料過長。')
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError()
        return result
    except HTTPError as error:
        raise gemini_error(error.code) from None
    except (URLError, TimeoutError, OSError):
        raise ConnectionFailure('network', 'Gemini 連線失敗或逾時，請確認網路後再試。') from None
    except (ValueError, UnicodeError):
        raise ConnectionFailure('protocol', 'Gemini 回傳資料格式無法辨識。') from None


def contents(messages):
    result = []
    for message in messages:
        role = {'user': 'user', 'assistant': 'model'}.get(message.get('role'))
        if role is None:
            raise ConnectionFailure('input', 'Gemini 對話角色格式不支援。')
        body = message.get('content')
        parts = []
        if isinstance(body, str):
            parts = [{'text': body}]
        elif isinstance(body, list):
            for part in body:
                if part.get('type') == 'input_text':
                    parts.append({'text': part['text']})
                elif part.get('type') == 'input_image':
                    match = re.fullmatch(r'data:(image/(?:png|jpeg|webp));base64,([A-Za-z0-9+/=]+)', part.get('image_url', ''))
                    if not match:
                        raise ConnectionFailure('input', 'Gemini 僅接收已通過本機檢查的內嵌圖片。')
                    try:
                        base64.b64decode(match[2], validate=True)
                    except ValueError:
                        raise ConnectionFailure('input', 'Gemini 圖片資料格式無效。') from None
                    parts.append({'inlineData': {'mimeType': match[1], 'data': match[2]}})
                else:
                    raise ConnectionFailure('input', 'Gemini 訊息內容格式不支援。')
        if not parts:
            raise ConnectionFailure('input', 'Gemini 訊息不可為空。')
        if result and result[-1]['role'] == role:
            result[-1]['parts'].extend(parts)
        else:
            result.append({'role': role, 'parts': parts})
    return result


class ModelServices:
    def __init__(self, chatgpt=None, settings=None):
        self.chatgpt = chatgpt or ChatConnection()
        self.store = self.chatgpt.store
        self.settings = settings or ProviderStore(self.store.folder)
        self.lock = threading.RLock()

    @property
    def provider(self):
        return self.settings.load()['provider']

    def set_provider(self, provider):
        if provider not in ('chatgpt', 'gemini'):
            raise ConnectionFailure('input', '未知的模型服務。')
        with self.lock:
            state = self.settings.load()
            state['provider'] = provider
            if provider == 'gemini' and not state.get('profile'):
                try:
                    state['profile'] = next((a['id'] for a in self.chatgpt.accounts() if a['active']), LOCAL_PROFILE)
                except ConnectionFailure:
                    state['profile'] = LOCAL_PROFILE
            self.settings.save(state)

    def save_key(self, key):
        key = key.strip()
        # Keys are opaque credentials: auth keys may contain periods and be much
        # longer than legacy standard keys. Only enforce safe header transport,
        # not a guessed prefix, alphabet, or legacy length. Google verifies validity.
        if key and (not 20 <= len(key) <= 8192 or
                    any(not 33 <= ord(char) <= 126 for char in key) or
                    key.lower().startswith(('http://', 'https://'))):
            raise ConnectionFailure('input', '請貼上完整 API Key（金鑰本身，不是網頁網址），中間不可含空白、換行或全形字元。')
        with self.lock:
            state = self.settings.load()
            state['key'] = key
            self.settings.save(state)

    def has_key(self):
        return bool(self.settings.load().get('key'))

    def accounts(self):
        if self.provider == 'chatgpt':
            return self.chatgpt.accounts()
        state = self.settings.load()
        # Existing record IDs stay stable: changing model providers never migrates or overwrites memory.
        try:
            existing = self.chatgpt.accounts()
        except ConnectionFailure:
            existing = []
        active = state.get('profile') or next((a['id'] for a in existing if a['active']), LOCAL_PROFILE)
        if active not in {a['id'] for a in existing} and active != LOCAL_PROFILE:
            existing.append({'id': active, 'label': '既有白龍紀錄'})
        return [dict(a, signed_in=True, active=a['id'] == active) for a in existing] + [
            {'id': LOCAL_PROFILE, 'label': 'Gemini · 個人白龍紀錄', 'signed_in': True, 'active': active == LOCAL_PROFILE}]

    def select(self, account_id):
        if self.provider == 'chatgpt':
            return self.chatgpt.select(account_id)
        if account_id not in {a['id'] for a in self.accounts()}:
            raise ConnectionFailure('input', '請選擇有效的白龍紀錄。')
        with self.lock:
            state = self.settings.load()
            state['profile'] = account_id
            self.settings.save(state)

    def models(self, cancel=None):
        if self.provider == 'chatgpt':
            return self.chatgpt.models(cancel)
        key = self.settings.load().get('key', '')
        result, token, seen = [], '', set()
        for _ in range(20):
            page = request('models?' + urlencode({'pageSize': 100, **({'pageToken': token} if token else {})}), key, cancel=cancel)
            for item in page.get('models', []):
                name = item.get('name', '')
                slug = name.removeprefix('models/')
                if (name.startswith('models/gemini-') and re.fullmatch(r'[A-Za-z0-9._-]+', slug)
                        and 'generateContent' in item.get('supportedGenerationMethods', [])
                        and not any(x in slug.lower() for x in ('image', 'tts', 'robotics', 'computer-use'))):
                    result.append({'id': 'gemini:' + slug, 'name': item.get('displayName', slug)})
            token = page.get('nextPageToken', '')
            if not token:
                return result
            if token in seen:
                break
            seen.add(token)
        raise ConnectionFailure('protocol', 'Gemini 模型清單分頁異常，請重試。')

    def reply(self, model, messages, instructions='請使用繁體中文簡短回覆。', cancel=None, on_delta=None):
        if self.provider == 'chatgpt':
            if model.startswith('gemini:'):
                raise ConnectionFailure('model', '服務已變更，請重新選擇模型。')
            return self.chatgpt.reply(model, messages, instructions=instructions, cancel=cancel, on_delta=on_delta)
        if not re.fullmatch(r'gemini:gemini-[A-Za-z0-9._-]+', model or ''):
            raise ConnectionFailure('model', '請重新載入並選擇 Gemini 模型。')
        body = {'contents': contents(messages), 'systemInstruction': {'parts': [{'text': instructions}]}}
        response = request('models/' + model[7:] + ':generateContent', self.settings.load().get('key', ''), body, cancel)
        candidates = response.get('candidates', [])
        if response.get('promptFeedback', {}).get('blockReason'):
            raise ConnectionFailure('blocked', 'Gemini 未接受這次內容，請換個說法。')
        candidate = candidates[0] if candidates else {}
        if candidate.get('finishReason') not in ('STOP',):
            raise ConnectionFailure('incomplete', 'Gemini 回覆遭中止或未完成，請縮短訊息或換個說法。')
        text = ''.join(p.get('text', '') for p in candidate.get('content', {}).get('parts', []) if not p.get('thought'))
        if not text.strip():
            raise ConnectionFailure('empty', 'Gemini 未回傳文字，請重試或換個模型。')
        check_cancel(cancel)
        if on_delta:
            on_delta(text)
        return text
