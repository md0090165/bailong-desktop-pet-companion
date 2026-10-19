"""Minimal local completion bridge; forwards the existing notify command unchanged."""
import hashlib,json,os,subprocess,sys,time,uuid
from pathlib import Path

def root_path():
    start=Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]
    for p in (start,*start.parents):
        if (p/'Start-BailongPet.cmd').is_file():return p
    return start

def completion(payload,at=None):
    if not isinstance(payload,dict) or payload.get('type')!='agent-turn-complete':return None
    if payload.get('status','completed')!='completed':return None
    thread=payload.get('thread-id') or payload.get('thread_id');turn=payload.get('turn-id') or payload.get('turn_id')
    if not isinstance(thread,str) or not thread or not isinstance(turn,str) or not turn:return None
    return {'id':hashlib.sha256((thread+'\0'+turn).encode()).hexdigest(),'at':time.time() if at is None else at}

def handle(raw,root,forward=True,shared=False):
    root=Path(root);folder=root/'CodexIntegration'
    if forward:
        try:
            command=json.loads((folder/'notify-forward.json').read_text(encoding='utf-8'))
            if isinstance(command,list) and command and all(isinstance(v,str) for v in command):
                frozen_windows=sys.platform=='win32' and getattr(sys,'frozen',False)
                if frozen_windows:
                    import ctypes
                    ctypes.windll.kernel32.SetDllDirectoryW(None)
                try:
                    subprocess.run(command+[raw],timeout=8,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                finally:
                    if frozen_windows:ctypes.windll.kernel32.SetDllDirectoryW(sys._MEIPASS)
        except (OSError,ValueError,subprocess.TimeoutExpired):pass
    try:
        event=completion(json.loads(raw))
        if not event:return False
        queues=[folder/'inbox']
        if shared:
            from codex_bus import shared_inbox
            queues.append(shared_inbox())
        written=False
        for queue in queues:
            try:
                queue.mkdir(parents=True,exist_ok=True)
                target=queue/(uuid.uuid4().hex+'.json');temp=target.with_suffix('.tmp')
                temp.write_text(json.dumps(event),encoding='utf-8');os.replace(temp,target);written=True
            except OSError:continue
        return written
    except (OSError,ValueError,TypeError):return False

if __name__=='__main__':
    if '--test-completion' in sys.argv:
        ok=handle(json.dumps({'type':'agent-turn-complete','thread-id':'bailong-offline-test',
                             'turn-id':uuid.uuid4().hex}),root_path(),forward=False,shared=True)
        sys.exit(0 if ok else 1)
    elif '--setup-notify' in sys.argv or '--remove-notify' in sys.argv:
        import ctypes
        from notify_setup import apply
        try:
            message=apply(root_path(),remove='--remove-notify' in sys.argv)
            ctypes.windll.user32.MessageBoxW(None,message,'白龍桌寵 · Codex 通知設定',64)
        except Exception:
            ctypes.windll.user32.MessageBoxW(None,'無法更新通知設定。請確認 Codex 設定檔格式與寫入權限。','白龍桌寵',16)
            sys.exit(1)
    elif len(sys.argv)>2 and sys.argv[1]=='--self-test':
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            ok=handle(json.dumps({'type':'agent-turn-complete','thread-id':'offline','turn-id':'test'}),folder,forward=False)
            events=list((Path(folder)/'CodexIntegration/inbox').glob('*.json'))
            Path(sys.argv[2]).write_text(json.dumps({'passed':ok and len(events)==1,'frozen':bool(getattr(sys,'frozen',False))}),encoding='utf-8')
    elif len(sys.argv)>1:handle(sys.argv[-1],root_path(),shared=True)

