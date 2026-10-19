"""Read-only Codex account adapter. No prompts, token generation or resets."""
import hashlib, json, os, queue, shutil, subprocess, threading, time, sys, ctypes
from pathlib import Path
from datetime import datetime
from version import VERSION

class QuotaFailure(RuntimeError):
    def __init__(self,message,code,phase='request',retryable=False):
        super().__init__(message);self.code=code;self.phase=phase;self.retryable=retryable

def classify_error(message,phase='request'):
    lower=message.lower()
    if any(s in lower for s in ('unauthorized','401','not authenticated','not logged','sign in','please log','token expired')):
        return QuotaFailure('Codex登入狀態已失效，請在Codex中重新登入。','authentication',phase)
    if any(s in lower for s in ('error sending request','timed out','timeout','connection','dns','tls','certificate','502','503','504','temporarily')):
        return QuotaFailure('網路連線暫時失敗，請檢查網路或代理後再試。','network',phase,True)
    return QuotaFailure('額度服務尚未回傳結果，請稍後再試。','service',phase)

def log_attempt(attempt,seconds,error=None,log_path=None):
    if log_path is False:return
    root=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    path=Path(log_path) if log_path else root/'app/额度查询日志.jsonl'
    event={'time':datetime.now().isoformat(timespec='seconds'),'attempt':attempt,'seconds':round(seconds,3),'result':'success' if error is None else error.code}
    if error:event['phase']=error.phase
    try:
        path.parent.mkdir(exist_ok=True)
        lines=path.read_text(encoding='utf-8').splitlines()[-199:] if path.exists() else []
        path.write_text('\n'.join(lines+[json.dumps(event,ensure_ascii=False)])+'\n',encoding='utf-8')
    except OSError:pass

def read_quota(timeout=20,cancel=None,attempts=2,log_path=None,include_identity=False):
    for attempt in range(1,attempts+1):
        started=time.monotonic()
        try:
            try:result=_read_quota_once(timeout,cancel,include_identity)
            except OSError:raise QuotaFailure('額度連線暫時中斷，請稍後再試。','connection_io','transport',True)
            log_attempt(attempt,time.monotonic()-started,log_path=log_path);return result
        except QuotaFailure as error:
            log_attempt(attempt,time.monotonic()-started,error,log_path)
            if not error.retryable or attempt==attempts or (cancel and cancel.is_set()):raise
            if cancel:
                if cancel.wait(.8):raise QuotaFailure('額度查詢已取消','cancelled')
            else:time.sleep(.8)

def find_codex():
    """Find Codex even when a startup process has a minimal PATH."""
    override = os.environ.get('BAILONG_CODEX', '').strip().strip('"')
    for candidate in (override, shutil.which('codex.exe'), shutil.which('codex')):
        if not candidate:
            continue
        resolved = Path(candidate).expanduser()
        if resolved.is_file():
            return str(resolved)
        located = shutil.which(candidate)
        if located:
            return located
    profile = Path(os.environ.get('USERPROFILE') or Path.home())
    local = Path(os.environ.get('LOCALAPPDATA') or profile / 'AppData' / 'Local')
    roots = [local / 'OpenAI' / 'Codex' / 'bin']
    candidates = []
    for folder in roots:
        if folder.is_dir():
            candidates.extend(folder.glob('*/codex.exe'))
            direct = folder / 'codex.exe'
            if direct.is_file():
                candidates.append(direct)
    candidates = [p for p in candidates if p.is_file()]
    if candidates:
        return str(max(candidates, key=lambda p: p.stat().st_mtime))
    from portable_support import installation_root
    bundled=installation_root()/'runtime/codex/codex.exe'
    if bundled.is_file():return str(bundled)
    raise RuntimeError('沒有找到 Codex，請確認已安裝，或設定 BAILONG_CODEX 指向 codex.exe。')

def _read_quota_once(timeout=20, cancel=None, include_identity=False):
    executable = find_codex()
    frozen_windows=sys.platform=='win32' and getattr(sys,'frozen',False)
    if frozen_windows:ctypes.windll.kernel32.SetDllDirectoryW(None)
    try:
        process = subprocess.Popen([executable, 'app-server'], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding='utf-8',
            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    finally:
        if frozen_windows:ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
    messages = queue.Queue()
    def receive():
        for line in process.stdout:
            try: messages.put(json.loads(line))
            except ValueError: pass
        messages.put(None)
    threading.Thread(target=receive, daemon=True).start()
    deadline = time.monotonic() + timeout
    def send(message):
        process.stdin.write(json.dumps(message)+'\n'); process.stdin.flush()
    def result(request_id):
        while time.monotonic() < deadline:
            if cancel and cancel.is_set():raise QuotaFailure('額度查詢已取消','cancelled')
            try: message = messages.get(timeout=min(.1,max(.01, deadline-time.monotonic())))
            except queue.Empty: continue
            phase='initialize' if request_id==1 else 'rate_limits'
            if message is None: raise QuotaFailure('額度連線暫時中斷，請稍後再試。','connection_closed',phase,True)
            if message.get('id') == request_id:
                if 'error' in message: raise classify_error(message['error'].get('message','額度讀取失敗'),phase)
                return message['result']
        raise QuotaFailure('額度載入逾時，請檢查網路或代理後再試。','timeout','initialize' if request_id==1 else 'rate_limits',True)
    try:
        send({'id':1,'method':'initialize','params':{'clientInfo':{'name':'bailong_pet','title':'白龍桌寵','version':VERSION}}})
        result(1); send({'method':'initialized'})
        send({'id':2,'method':'account/rateLimits/read'})
        limits=result(2)
        if include_identity:
            send({'id':3,'method':'account/read','params':{'refreshToken':False}})
            account=result(3).get('account') or {}
            identity=account.get('id') or account.get('accountId') or account.get('email')
            limits['_account_key']=hashlib.sha256((account.get('type','')+':'+str(identity)).encode()).hexdigest() if identity else None
        return limits
    finally:
        if process.poll() is None: process.terminate()
        try: process.wait(timeout=1)
        except subprocess.TimeoutExpired: process.kill();process.wait()
        process.stdin.close();process.stdout.close()

def quota_windows(data):
    buckets = data.get('rateLimitsByLimitId') or {}
    bucket = buckets.get('codex') or data.get('rateLimits')
    if not bucket: raise RuntimeError('目前帳號沒有可讀取的 Codex 額度。')
    windows=[]
    for key in ('primary','secondary'):
        window=bucket.get(key)
        if not window: continue
        minutes=window.get('windowDurationMins')
        name={300:'5小時',10080:'一週'}.get(minutes, f'{minutes}分鐘' if minutes else '額度區間')
        used=window.get('usedPercent')
        remaining=None if used is None else max(0,min(100,100-float(used)))
        windows.append({'name':name,'remaining':remaining,'resetsAt':window.get('resetsAt')})
    if not windows: raise RuntimeError('伺服器暫未提供額度區間。')
    return windows
