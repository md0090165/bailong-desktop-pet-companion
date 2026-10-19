"""Deterministic Codex completion/low-quota notifications, without LLM calls."""
import copy,hashlib,json,math,os,time
from pathlib import Path
from PySide6.QtCore import QObject,QThread,QTimer,Signal,Qt
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QLabel,QPushButton,QCheckBox
from quota import read_quota
from codex_bus import shared_inbox,installed_root
from codex_completion_watch import CompletionWatch
import threading

HUNGRY='嗚嗚，主人我肚子餓了'
DONE='主人我做好了喔'

def quota_key(data,at=None):
    at=time.time() if at is None else at
    account=data.get('_account_key');bucket=(data.get('rateLimitsByLimitId') or {}).get('codex') or data.get('rateLimits') or {}
    if not isinstance(account,str) or len(account)!=64:return None
    for name in ('primary','secondary'):
        w=bucket.get(name) or {};used=w.get('usedPercent');reset=w.get('resetsAt')
        if w.get('windowDurationMins')!=300:continue
        if type(used) not in (int,float) or not math.isfinite(used) or not 0<=used<=100:return None
        if type(reset) not in (int,float) or not math.isfinite(reset) or not at<reset<at+86400:return None
        return {'key':account+':'+str(int(reset)),'account':account,'remaining':100-used,'reset':reset}
    return None

class MonitorState:
    def __init__(self,root):
        self.folder=Path(root)/'CodexIntegration';self.path=self.folder/'state.json';self.backup=self.path.with_suffix('.json.bak')
        self.data={'version':1,'enabled':True,'seen':[],'alerted':{}};self.ready=True;self.valid=False;self.error=''
        if not self.path.exists() and not self.backup.exists():return
        for p in (self.path,self.backup):
            try:
                s=json.loads(p.read_text(encoding='utf-8'));assert s['version']==1 and type(s['enabled']) is bool
                assert isinstance(s['seen'],list) and len(s['seen'])<=512 and all(isinstance(v,str) and len(v)==64 for v in s['seen'])
                assert isinstance(s['alerted'],dict) and all(isinstance(k,str) and type(v) in (int,float) and math.isfinite(v) for k,v in s['alerted'].items())
                self.data=s;self.valid=p==self.path;return
            except (OSError,ValueError,AssertionError,KeyError,TypeError):pass
        self.ready=False;self.error='監看狀態及備份無法讀取，已暫停通知並保留原檔。'
    def save(self,data):
        if not self.ready:raise OSError('state unavailable')
        self.folder.mkdir(parents=True,exist_ok=True)
        def write(path,value):
            tmp=path.with_suffix('.tmp')
            with tmp.open('w',encoding='utf-8') as f:json.dump(value,f);f.flush();os.fsync(f.fileno())
            os.replace(tmp,path)
        if self.valid:write(self.backup,self.data)
        write(self.path,data);self.data=copy.deepcopy(data);self.valid=True

class QuotaPoll(QThread):
    ready=Signal(object);failed=Signal()
    def __init__(self,parent=None):super().__init__(parent);self.cancel=threading.Event()
    def run(self):
        try:self.ready.emit(read_quota(timeout=20,cancel=self.cancel,attempts=1,log_path=False,include_identity=True))
        except Exception:self.failed.emit()

class CodexMonitor(QObject):
    def __init__(self,pet,root):
        super().__init__(pet);self.pet=pet;self.state=MonitorState(root);self.worker=None;self.pending_completion=False;self.completion_until=0
        self.pending_low=None;self.current_account=None;self.next_poll=time.monotonic()+3;self.last_poll=0
        self.shared_inbox=shared_inbox() if installed_root(root) and '--self-test' not in __import__('sys').argv else None
        self.completion_watch=CompletionWatch() if self.shared_inbox else None
        self.last_completion_received=None;self.last_completion_played=None
        self.status='每 3 分鐘檢查 Codex 五小時額度；等待完成通知。';self.panel=None;self.stopping=False
        self.timer=QTimer(self);self.timer.setInterval(1000);self.timer.timeout.connect(self.tick);self.timer.start()
        if QApplication.platformName()=='offscreen' or '--self-test' in __import__('sys').argv:self.timer.stop()
    def bind(self,chat):
        if hasattr(chat,'codex_monitor_button'):return
        chat.codex_monitor_button=QPushButton('Codex 工作完成與額度提醒')
        chat.codex_monitor_button.clicked.connect(self.show_panel);chat.settings_layout.addWidget(chat.codex_monitor_button)
        chat.parameters.resize(540,740)
    def accept_completion(self,event,at):
        key=event['id'];stamp=event['at'];aliases=event.get('aliases',[])
        keys=list(dict.fromkeys([key]+aliases))
        if (not all(isinstance(k,str) and len(k)==64 for k in keys) or len(keys)>8
            or type(stamp) not in (int,float) or not math.isfinite(stamp)):raise ValueError()
        seen=self.state.data['seen'];duplicate=any(k in seen for k in keys)
        if not all(k in seen for k in keys):
            s=copy.deepcopy(self.state.data);s['seen']=(s['seen']+[k for k in keys if k not in seen])[-512:];self.state.save(s)
        if not duplicate and self.state.data['enabled'] and 0<=at-stamp<=300:
            self.pending_completion=True;self.completion_until=at+300;self.next_poll=min(self.next_poll,time.monotonic())
            self.last_completion_received=stamp

    def ingest(self,at=None):
        at=time.time() if at is None else at;folder=self.state.folder/'inbox'
        folders=[folder]+([self.shared_inbox] if self.shared_inbox else [])
        for path in [p for f in folders if f.exists() for p in sorted(f.glob('*.json'))[:100]]:
            try:
                if path.stat().st_size>1024:raise ValueError()
                self.accept_completion(json.loads(path.read_text(encoding='utf-8')),at)
                path.unlink()
            except (ValueError,KeyError,TypeError):
                try:path.unlink()
                except OSError:pass
            except OSError:self.status='完成通知暫時無法保存，稍後重試。';break
        if self.completion_watch:
            for event in self.completion_watch.poll():
                try:self.accept_completion(event,at)
                except (OSError,ValueError,TypeError,KeyError):self.status='完成事件暫時無法保存，稍後重試。'

    def tick(self):
        if self.stopping or self.pet.exiting or not self.state.ready:return
        self.ingest()
        if not self.state.data['enabled']:return
        if time.monotonic()>=self.next_poll and time.monotonic()-self.last_poll>=15 and not self.worker and not self.pet.quota_inflight:
            self.poll()
        self.deliver()
        if self.panel and self.panel.isVisible():self.refresh_panel()
    def poll(self):
        if self.worker or self.stopping:return
        self.last_poll=time.monotonic();self.next_poll=self.last_poll+180
        w=QuotaPoll(self);self.worker=w;w.ready.connect(self.quota_ready);w.failed.connect(self.quota_failed)
        w.finished.connect(self.poll_finished);w.start()
    def quota_ready(self,data):
        if self.stopping or not self.state.data['enabled'] or (self.worker and self.worker.cancel.is_set()):return
        view=quota_key(data)
        if not view:
            self.pending_low=None;self.current_account=None;self.status='尚無可確認帳號及重設時間的五小時額度；本輪不提醒。';return
        self.current_account=view['account'];self.status=f"Codex 五小時額度剩餘 {view['remaining']:g}%（帳號獨立於白龍聊天）。"
        self.pending_low=view if view['remaining']<=10 and view['key'] not in self.state.data['alerted'] else None
    def quota_failed(self):
        self.pending_low=None
        self.status='暫時查不到 Codex 額度；不將查詢失敗視為額度不足。'
    def poll_finished(self):
        w=self.worker;self.worker=None
        if w:w.deleteLater()
    def busy(self):
        chat=self.pet.chat
        return (self.pet.mode!='idle' or self.pet.dragging or self.pet.quota_inflight or self.pet.click_timer.isActive()
            or self.pet.bubble.isVisible() or QApplication.activePopupWidget() is not None
            or QApplication.mouseButtons()!=Qt.MouseButton.NoButton or bool(chat and chat.interaction_busy())
            or bool(self.panel and self.panel.isVisible()) or bool(self.pet.events.panel and self.pet.events.panel.isVisible()))
    def deliver(self):
        if self.pending_completion and self.completion_until and time.time()>self.completion_until:self.pending_completion=False
        if self.busy():return
        low=self.pending_low
        if low and low['reset']<=time.time():self.pending_low=None;low=None
        ident='14' if low else '04' if self.pending_completion else None
        if ident is None:return
        item=next((v for v in self.pet.variants if v['id']==ident),None)
        if not item:return
        if low:
            s=copy.deepcopy(self.state.data);s['alerted'][low['key']]=time.time()
            s['alerted']=dict(sorted(s['alerted'].items(),key=lambda x:x[1])[-128:])
            try:self.state.save(s)
            except OSError:self.status='提醒狀態尚未保存，稍後重試。';return
        try:
            self.pet.play(item,'variant')
            if low:self.pet.say(HUNGRY,5000);self.pending_low=None
            else:self.pet.say(DONE,4000);self.pending_completion=False;self.last_completion_played=time.time()
        except Exception:
            self.pet.idle();self.status='提醒動畫暫時無法播放。'
    def show_panel(self):
        chat=self.pet.ensure_chat()
        if self.panel is None:
            self.panel=QWidget(chat,Qt.WindowType.Window);self.panel.setWindowTitle('Codex 工作完成與額度提醒')
            self.panel.setWindowModality(Qt.WindowModality.ApplicationModal);self.panel.resize(540,250)
            layout=QVBoxLayout(self.panel);self.enabled=QCheckBox('啟用工作完成動畫與額度提醒');layout.addWidget(self.enabled)
            self.enabled.toggled.connect(self.toggle);self.label=QLabel();self.label.setWordWrap(True);layout.addWidget(self.label)
            label=QLabel('工作完成：04 靈感＋「主人我做好了喔」\n五小時額度 ≤10%：14 睏倦＋「'+HUNGRY+'」\n固定通知不呼叫模型、不拍快照、不寫入日記。\n首次安裝完成通知後，請重新啟動 Codex。')
            label.setWordWrap(True);layout.addWidget(label)
            test=QPushButton('測試工作完成通知（不使用模型）');test.clicked.connect(self.test_completion);layout.addWidget(test)
        self.refresh_panel();self.panel.show();self.panel.raise_()
    def test_completion(self):
        import subprocess
        helper=self.state.folder.parent/'CodexNotify.exe'
        try:
            result=subprocess.run([str(helper),'--test-completion'],timeout=10,
                stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            if result.returncode:raise OSError()
            self.ingest()
            self.status='測試通知已送出，關閉設定及聊天視窗後，閒置時播放 04 靈感。'
        except (OSError,subprocess.TimeoutExpired):
            self.status='無法執行完成通知橋接程式，請確認 CodexNotify.exe 完整存在。'
        self.refresh_panel()

    def refresh_panel(self):
        if not self.panel:return
        self.enabled.blockSignals(True);self.enabled.setChecked(self.state.data['enabled']);self.enabled.blockSignals(False)
        self.enabled.setEnabled(self.state.ready);self.label.setText(self.state.error or self.status+'\n最後接收完成通知：'+
            (time.strftime('%H:%M:%S',time.localtime(self.last_completion_received)) if self.last_completion_received else '尚未收到')+
            ' · 最後播放：'+(time.strftime('%H:%M:%S',time.localtime(self.last_completion_played)) if self.last_completion_played else '尚未播放'))
    def toggle(self,enabled):
        s=copy.deepcopy(self.state.data);s['enabled']=enabled
        try:self.state.save(s)
        except OSError:self.status='無法保存監看設定。';self.refresh_panel();return
        self.pending_completion=False;self.pending_low=None
        if not enabled and self.worker:self.worker.cancel.set()
        if enabled:self.next_poll=time.monotonic()
    def shutdown(self):
        self.stopping=True;self.timer.stop()
        if self.worker:self.worker.cancel.set()
        if self.panel:self.panel.hide()
