"""Offline pre-browser checks using synthetic data only; no account reads or LLM."""
import json
import os
import sys
import tempfile
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler

from chat_connection import CredentialStore, protect
from login_diagnostics import error_details, save_report


def probe_write(folder):
    # Mirrors mkdir -> write -> replace without touching any existing credentials.
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='login-probe-', dir=folder) as directory:
        source = Path(directory) / 'probe.tmp'
        target = Path(directory) / 'probe.dpapi'
        source.write_bytes(b'Bailong synthetic write probe')
        os.replace(source, target)
        if target.read_bytes() != b'Bailong synthetic write probe':
            raise OSError('probe mismatch')


def probe_dpapi():
    value = os.urandom(64)
    if protect(protect(value), decrypt=True) != value:
        raise OSError('DPAPI roundtrip failed')


def probe_loopback():
    with HTTPServer(('127.0.0.1', 0), BaseHTTPRequestHandler):
        pass


def collect(folder=None):
    # Choosing the same namespace as the pet is essential for portable permissions.
    folder = folder if folder is not None else CredentialStore().folder
    results = {}
    for key, operation in [('credential_write', lambda: probe_write(folder)),
                           ('windows_dpapi', probe_dpapi), ('loopback_bind', probe_loopback)]:
        try:
            operation()
            results[key] = {'ok': True}
        except Exception as error:
            results[key] = {'ok': False, **error_details(error)}
    return {'kind': 'offline_login_environment', 'checks': results}


def main():
    print('白龍桌寵：登入前環境檢查\n不讀取帳密、不連接 ChatGPT、不消耗模型額度。\n')
    result = collect()
    labels = {'credential_write': '登入資料夾寫入與更名', 'windows_dpapi': 'Windows 加密保護',
              'loopback_bind': '本機登入回呼連線'}
    for key, value in result['checks'].items():
        print(labels[key] + '：' + ('通過' if value['ok'] else '失敗 ' + json.dumps(value, ensure_ascii=False)))
    root = Path(sys.executable).parent if getattr(sys, 'frozen', False) else None
    report = save_report(result, root=root)
    print('\n診斷報告：' + str(report or '無法寫入'))
    print('請將此視窗結果或 JSON 報告提供給協助排查的人。')
    if sys.stdin and sys.stdin.isatty():
        try:
            input('\n按 Enter 關閉視窗。')
        except EOFError:
            pass
    return 0 if all(item['ok'] for item in result['checks'].values()) else 1


if __name__ == '__main__':
    raise SystemExit(main())
