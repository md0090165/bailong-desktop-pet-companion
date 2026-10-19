"""Manual memory sync UI; every differing version requires an explicit choice."""
import uuid
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QLabel,QPushButton,QComboBox,QFileDialog,QMessageBox,QPlainTextEdit
from connection_check import Operation
from chat_connection import ConnectionFailure
from google_drive_sync import GoogleAuth,DriveClient,SyncFailure,cancelled
from cloud_sync_core import Repository,digest,summary,heads,rows_valid,validate_snapshot

GUIDE='''第一次設定 Google Drive 同步
1. 開啟 https://console.cloud.google.com/ ，建立或選擇一個專案。
2. 在「API 和服務 → 程式庫」啟用 Google Drive API。
3. 在 Google Auth Platform 設定應用程式名稱、聯絡信箱與同意畫面。
4. 個人測試可選 External／測試模式，將自己的 Google 信箱加入測試使用者。
5. 在「Clients／用戶端」建立「Desktop app／桌面應用程式」OAuth 用戶端。
6. 下載 OAuth JSON，回到此視窗按「匯入 OAuth 設定」，再按「登入 Google」。
7. 每一台電腦使用同一份 OAuth JSON，並登入同一個 Google 帳號。

權限：Google Drive 應用程式專屬資料，以及辨識 Google 帳號的基本資訊。
白龍不要求存取整個 Google Drive，不會上傳登入權杖、快照或程式素材。
Google 的應用程式資料空間不會顯示為一般 Drive 資料夾。
若授權被拒絕，確認 Drive API、桌面用戶端類型及測試使用者設定。
測試模式下授權可能需定期重新登入；不要在聊天中貼上 OAuth JSON 或權杖。

換電腦：先在舊電腦同步，關閉舊白龍；在新電腦登入並選原雲端版本，使用雲端紀錄。
此版採手動同步、一次一台使用，不在背景自動覆寫。
'''

class SyncPanel(QWidget):
    def __init__(self,chat,auth=None,client=None):
        super().__init__(chat,Qt.WindowType.Window)
        self.chat=chat;self.repo=Repository(chat.project_root,chat.session_account)
        self.auth=auth or GoogleAuth(chat.project_root);self.client=client or DriveClient(self.auth)
        self.worker=None;self.rows=[];self.applied=False;self.last_cancelled=False;self.auto_compare=False;self.guide=None;self.last_error=''
        self.setWindowTitle('白龍 · 同步記憶 · Google Drive');self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(650,450);self.setStyleSheet('QWidget{background:#f8f2fc;color:#392b4b;font-family:Microsoft JhengHei;font-size:14px;} QPushButton,QComboBox{padding:7px;background:#eee4f6;border:1px solid #cdb8dd;border-radius:5px;}')
        layout=QVBoxLayout(self);label=QLabel('手動同步到 Google Drive，一次一台電腦使用。\n包含人設、對話、長期記憶、關係心情與觀察日記。不同版本由你選擇，不自動覆蓋。')
        label.setWordWrap(True);layout.addWidget(label)
        self.account_label=QLabel();self.account_label.setWordWrap(True);layout.addWidget(self.account_label)
        row=QHBoxLayout();self.import_button=QPushButton('匯入 OAuth 設定');self.login_button=QPushButton('登入 Google');self.logout_button=QPushButton('登出')
        for b in (self.import_button,self.login_button,self.logout_button):row.addWidget(b)
        layout.addLayout(row)
        self.help_button=QPushButton('第一次設定說明');layout.addWidget(self.help_button)
        self.versions=QComboBox();self.versions.setToolTip('選擇此 Google 帳號中要接續的白龍版本；每台電腦須使用相同 OAuth 用戶端。');layout.addWidget(self.versions)
        row=QHBoxLayout();self.check_button=QPushButton('檢查雲端版本');self.sync_button=QPushButton('比較並同步記憶');row.addWidget(self.check_button);row.addWidget(self.sync_button);layout.addLayout(row)
        self.status=QLabel('請先匯入 Google 桌面 OAuth 設定並登入。');self.status.setWordWrap(True);layout.addWidget(self.status)
        self.cancel_button=QPushButton('取消目前操作');self.cancel_button.setEnabled(False);layout.addWidget(self.cancel_button)
        layout.addStretch()
        self.controls=(self.import_button,self.login_button,self.logout_button,self.help_button,self.check_button,self.sync_button,self.versions)
        self.import_button.clicked.connect(self.import_client);self.login_button.clicked.connect(self.login)
        self.logout_button.clicked.connect(self.logout);self.help_button.clicked.connect(self.show_guide)
        self.check_button.clicked.connect(self.check);self.sync_button.clicked.connect(self.compare);self.cancel_button.clicked.connect(self.cancel)
        self.update_account()
    def begin(self):
        self.update_account();self.show();self.raise_()
        if self.worker:return
        try:logged_in=self.auth.logged_in()
        except SyncFailure as e:self.status.setText(str(e));return
        if logged_in:self.auto_compare=True;self.check()
    def compare_when_ready(self):
        if not self.isVisible() or self.last_cancelled:return
        if self.worker:QTimer.singleShot(5,self.compare_when_ready);return
        self.compare()
    def update_account(self):
        try:self.account_label.setText('Google：'+(self.auth.logged_in() or '尚未登入'))
        except SyncFailure as e:self.account_label.setText(str(e))
    def show_guide(self):
        if self.guide is None:
            self.guide=QWidget(self,Qt.WindowType.Window);self.guide.setWindowTitle('Google Drive 同步設定步驟');self.guide.resize(680,570)
            l=QVBoxLayout(self.guide);text=QPlainTextEdit();text.setReadOnly(True);text.setPlainText(GUIDE);l.addWidget(text)
        self.guide.show();self.guide.raise_()
    def import_client(self):
        path,_=QFileDialog.getOpenFileName(self,'選擇 Google 桌面 OAuth JSON','','JSON (*.json)')
        if not path:return
        try:self.auth.import_client(path);self.rows=[];self.versions.clear();self.status.setText('OAuth 設定已匯入。請登入 Google；其他電腦須匯入同一份 JSON。')
        except (SyncFailure,OSError) as e:self.status.setText(str(e) if isinstance(e,SyncFailure) else '無法保存 OAuth 設定。')
        self.update_account()
    def login(self):self.start(lambda w:self.auth.login(w.cancel),self.login_ready,'請在瀏覽器登入 Google 並同意白龍同步權限。')
    def login_ready(self,result):self.update_account();self.status.setText('Google 登入完成。請按「檢查雲端版本」。')
    def logout(self):
        if self.worker:return
        try:self.auth.logout();self.rows=[];self.versions.clear();self.update_account();self.status.setText('已登出 Google，本機與雲端紀錄均保留。')
        except OSError:self.status.setText('無法清除 Google 本機登入。')
    def start(self,operation,ready,status):
        if self.worker:return
        self.status.setText(status);self.last_error='';self.applied=False;self.last_cancelled=False
        for c in self.controls:c.setEnabled(False)
        self.cancel_button.setEnabled(True)
        def guarded(worker):
            try:return operation(worker)
            except SyncFailure as e:raise ConnectionFailure('sync',str(e)) from None
            except OSError:raise ConnectionFailure('sync','同步資料無法寫入，請確認資料夾權限；原紀錄與備份已保留。') from None
        w=Operation(guarded,self);self.worker=w;w.ready.connect(ready);w.failed.connect(self.failed);w.finished.connect(self.finished);w.start()
    def failed(self,text):
        self.last_error=text;self.status.setText(text)
        if (self.repo.folder/'pending-restore.json').exists():self.chat.cloud_recovery_error=text
    def finished(self):
        w=self.worker;self.worker=None;self.last_cancelled=w.cancel.is_set()
        if self.applied:
            self.reload_chat();self.status.setText(self.last_error or '已套用雲端白龍紀錄；原本機版本已保存於 CloudSync/Transactions。')
        elif w.cancel.is_set():self.status.setText('操作已取消。若取消時雲端寫入已開始，請重新檢查版本確認結果。')
        for c in self.controls:c.setEnabled(True)
        self.cancel_button.setEnabled(False);w.deleteLater()
    def cancel(self):
        if self.worker:self.worker.cancel.set();self.status.setText('正在取消；若已開始套用，會先完成或還原本機資料。')
    def check(self):self.start(lambda w:rows_valid(self.client.list(w.cancel)),self.checked,'正在讀取 Google Drive 白龍版本…')
    def checked(self,rows):
        self.rows=rows;self.versions.clear()
        self.versions.addItem('建立新的白龍雲端紀錄',None)
        try:preferred=self.repo.link(self.auth.identity()).get('profile_id')
        except SyncFailure as e:self.failed(str(e));return
        chosen=0
        for row in sorted(rows,key=lambda r:r.get('createdTime',''),reverse=True):
            p=row['appProperties'];latest=p['revision'] in heads(rows,p['profile'])
            title=(p.get('updated') or row.get('createdTime','未知時間'))+' · '+p['profile'][-8:]+(' · 最新' if latest else ' · 歷史')
            self.versions.addItem(title,row)
            if preferred==p['profile'] and latest and not chosen:chosen=self.versions.count()-1
        self.versions.setCurrentIndex(chosen)
        self.status.setText(f'讀取完成，共 {len(rows)} 個雲端版本。請選擇原本的白龍版本，或建立新紀錄，再按比較。')
        automatic=self.auto_compare;self.auto_compare=False
        if automatic and (chosen or not rows):QTimer.singleShot(0,self.compare_when_ready)
    def compare(self):
        if self.worker:return
        try:
            local=self.repo.collect();google=self.auth.identity()
            if not google:raise SyncFailure('請先登入 Google。')
            row=self.versions.currentData()
            if self.versions.count()==0:raise SyncFailure('請先檢查雲端版本。')
            expected_heads=heads(self.rows,row['appProperties']['profile']) if row else []
        except SyncFailure as e:self.status.setText(str(e));return
        def load(w):
            cloud=validate_snapshot(self.client.download(row['id'],w.cancel)) if row else None
            if cloud and (cloud['profile_id']!=row['appProperties']['profile'] or cloud['revision']!=row['appProperties']['revision']):raise SyncFailure('雲端版本識別不一致，請重新檢查。')
            return local,cloud,google,expected_heads
        self.start(load,self.compared,'正在比較本機與雲端紀錄…')
    def choose(self,local,cloud,google):
        link=self.repo.link(google);dirty=link.get('hash')!=digest(local)
        box=QMessageBox(self);box.setWindowTitle('選擇要使用的白龍紀錄')
        text=('本機有尚未同步的紀錄。\n' if dirty else '本機與所選雲端版本不同。\n')+'\n本機：'+summary(local)
        text+='\n雲端：'+(summary(cloud['data'])+'\n雲端時間：'+cloud['updated_at'] if cloud else '尚未建立這份紀錄')
        box.setText(text);box.setInformativeText('使用本機：新增雲端版本，保留雲端歷史。\n使用雲端：先備份本機，再完整套用所選版本。')
        upload=box.addButton('使用本機 → 上傳',QMessageBox.ButtonRole.AcceptRole)
        download=box.addButton('使用雲端 → 套用本機',QMessageBox.ButtonRole.DestructiveRole) if cloud else None
        cancel=box.addButton('取消',QMessageBox.ButtonRole.RejectRole);box.setDefaultButton(cancel);box.exec()
        return 'upload' if box.clickedButton()==upload else 'download' if download and box.clickedButton()==download else 'cancel'
    def compared(self,result):
        # Operation.finished must run before starting another worker or presenting the chooser.
        QTimer.singleShot(0,lambda:self.resolve(result))
    def resolve(self,result):
        if not self.isVisible() or self.last_cancelled:return
        if self.worker:QTimer.singleShot(5,lambda:self.resolve(result));return
        local,cloud,google,expected_heads=result
        if self.chat.session_account!=self.repo.account:self.status.setText('聊天帳號已切換，請重新開啟同步視窗。');return
        if cloud and digest(local)==cloud['hash']:
            try:self.repo.mark(google,cloud);self.status.setText('本機與雲端完全相同，已確認同步。')
            except (OSError,SyncFailure) as e:self.failed(str(e))
            return
        try:choice=self.choose(local,cloud,google)
        except SyncFailure as e:self.failed(str(e));return
        if choice=='cancel':self.status.setText('已取消，沒有覆蓋任何紀錄。');return
        if choice=='upload':
            profile=cloud['profile_id'] if cloud else uuid.uuid4().hex
            self.start(lambda w:self.repo.upload(self.client,google,local,profile,expected_heads,w.cancel),self.uploaded,'正在新增雲端備份版本…')
        else:
            def download(w):
                current=validate_snapshot(self.client.download(self.versions.currentData()['id'],w.cancel))
                if current!=cloud:raise SyncFailure('雲端檔案已改變，請重新比較。')
                self.repo.apply(cloud,digest(local),w.cancel);self.applied=True
                self.repo.mark(google,cloud);return cloud
            self.start(download,lambda r:None,'正在備份本機並套用雲端紀錄…')
    def uploaded(self,snapshot):self.status.setText('記憶已同步到 Google Drive。雲端歷史版本已保留。請在另一台使用前先關閉這台白龍。')
    def reload_chat(self):
        if self.chat.session_account!=self.repo.account:return
        if self.chat.event_controller:self.chat.event_controller.account=None
        self.chat.reload_character(announce=False);self.chat.restore_conversation(self.repo.account)
    def shutdown(self):
        self.cancel();self.hide()
        if self.guide:self.guide.hide()
    def closeEvent(self,event):self.shutdown();event.ignore()
