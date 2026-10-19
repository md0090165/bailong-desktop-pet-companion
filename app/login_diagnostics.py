"""Allowlisted diagnostics: never serialize exception text, URLs, tokens or locals."""
import functools
import json
import os
import platform
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path

_state = threading.local()
BUILD = '2026-10-19-login-diagnostics-3'


def phase(value):
    _state.phase = value


def diagnose_login(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        phase('login_start')
        try:
            return function(*args, **kwargs)
        except Exception as error:
            error.login_phase = getattr(_state, 'phase', 'login_start')
            raise
        finally:
            phase('idle')
    return wrapped


def error_details(error, depth=0):
    # Deliberately omit str(error), repr(error), traceback source and filenames.
    result = {'exception': type(error).__name__}
    if depth >= 5:
        return result
    for name in ('errno', 'winerror', 'verify_code'):
        value = getattr(error, name, None)
        if isinstance(value, int):
            result[name] = value
    status = getattr(error, 'http_status', None)
    if isinstance(status, int):
        result['http_status'] = status
    code = getattr(error, 'oauth_error', None)
    if code in ('invalid_grant', 'invalid_token', 'invalid_client', 'unauthorized_client',
                'access_denied', 'invalid_request', 'invalid_scope', 'unsupported_grant_type',
                'temporarily_unavailable', 'server_error', 'other'):
        result['oauth_error'] = code
    reason = getattr(error, 'identity_reason', None)
    if reason in ('signing_key_download', 'token_format', 'signing_key_response',
                  'token_expired', 'token_not_yet_valid', 'audience_mismatch', 'issuer_mismatch',
                  'signature_invalid', 'algorithm_invalid', 'required_claim_missing',
                  'token_claim_invalid', 'nonce_mismatch', 'account_mismatch'):
        result['identity_reason'] = reason
    from urllib.error import HTTPError, URLError
    if isinstance(error, HTTPError):
        result['http_status'] = error.code
    cause = error.__cause__
    if cause is not None:
        result['cause'] = error_details(cause, depth + 1)
    elif isinstance(error, URLError) and isinstance(error.reason, BaseException):
        # URLError may hide TLS/DNS errors in .reason; string reasons are never recorded.
        result['reason'] = error_details(error.reason, depth + 1)
    return result


def save_report(report, root=None):
    from portable_support import installation_root
    candidates = [Path(root) / 'LoginDiagnostics' if root else installation_root() / 'LoginDiagnostics',
                  Path(os.environ.get('LOCALAPPDATA', tempfile.gettempdir())) / 'BailongDesktopPet' / 'LoginDiagnostics',
                  Path(tempfile.gettempdir()) / 'BailongDesktopPet-LoginDiagnostics']
    report = {'build': BUILD, 'utc': datetime.now(timezone.utc).isoformat(),
              'windows': platform.win32_ver()[0:2], 'python': platform.python_version(), **report}
    content = json.dumps(report, ensure_ascii=False, indent=2)
    name = 'login-' + datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json'
    for folder in candidates:
        try:
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / name
            with target.open('x', encoding='utf-8') as stream:
                stream.write(content)
            return target
        except OSError:
            continue
    return None


def failure_message(error):
    stage = getattr(error, 'login_phase', 'operation')
    report = save_report({'stage': stage, 'error': error_details(error)})
    if stage == 'token_exchange':
        if getattr(error, 'oauth_error', None) == 'invalid_grant':
            hint = '已收到登入回傳，但本次授權碼交換被拒絕。請關閉舊登入分頁，再按 Continue with ChatGPT 完成新一輪授權。'
        else:
            hint = '已收到登入回傳，但尚未成功換取登入憑證。請提供下列錯誤代碼及報告。'
    elif stage == 'identity_verify':
        reason = getattr(error, 'identity_reason', '')
        hints = {
            'signing_key_download': '無法取得登入驗證用的公開金鑰，請提供診斷報告以確認網路或 TLS 錯誤。',
            'account_mismatch': '瀏覽器登入帳號與所選帳號不同。請改用原帳號，或選「新增帳號／工作區」。',
            'nonce_mismatch': '本次登入驗證資料不符，請關閉舊登入分頁後重新登入。',
            'token_expired': '收到的登入憑證已超過有效時間，請重新登入並提供報告。',
            'token_not_yet_valid': '收到的登入憑證尚未到有效時間，請提供報告。',
        }
        hint = hints.get(reason, '登入憑證的身分驗證未通過，請提供錯誤代碼及報告。')
    elif stage == 'credential_load':
        hint = '讀取本機登入資料失敗。請勿刪除記憶資料，先執行登入環境檢查。'
    elif stage in ('credential_save', 'credential_commit'):
        hint = '儲存本機登入資料失敗。請確認已完整解壓縮，且程式資料夾可寫入。'
    elif stage == 'loopback_bind':
        hint = '無法建立本機登入回呼連線。請執行登入環境檢查。'
    elif stage == 'browser_launch':
        hint = '無法開啟預設瀏覽器，請檢查 Windows 預設瀏覽器設定。'
    else:
        hint = '操作失敗，請提供錯誤代碼與診斷報告以便確認原因。'
    code = stage + '/' + type(error).__name__
    if getattr(error, 'identity_reason', None):
        code += '/' + error.identity_reason
    if hasattr(error, 'http_status'):
        code += '/HTTP-' + str(error.http_status)
    if getattr(error, 'oauth_error', None):
        code += '/' + error.oauth_error
    return hint + '\n錯誤代碼：' + code + ('\n診斷報告：' + str(report) if report else '\n診斷報告無法寫入。')
