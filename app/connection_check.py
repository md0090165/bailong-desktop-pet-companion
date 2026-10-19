"""Stage 1 verification window, independent of the pet UI."""
import sys
import threading
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt, QTimer, QTranslator
from PySide6.QtGui import QFontDatabase, QFont
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
                               QPushButton, QComboBox, QPlainTextEdit)

from chat_connection import ChatConnection, ConnectionFailure
from login_diagnostics import failure_message


class Operation(QThread):
    ready = Signal(object)
    failed = Signal(str)
    status = Signal(str)
    delta = Signal(str)

    def __init__(self, operation, parent=None):
        super().__init__(parent)
        self.operation = operation
        self.cancel = threading.Event()

    def run(self):
        try:
            result = self.operation(self)
            if not self.cancel.is_set():
                self.ready.emit(result)
        except ConnectionFailure as error:
            self.failed.emit(failure_message(error) if hasattr(error, 'login_phase') and error.code != 'cancelled' else str(error))
        except Exception as error:
            self.failed.emit(failure_message(error))


class ConnectionCheck(QWidget):
    def __init__(self, connection=None):
        super().__init__()
        self.connection = connection or ChatConnection()
        self.worker = None
        self.closing = False
        self.setWindowTitle('白龍桌寵 · ChatGPT 連線檢查（第 1 階段）')
        self.resize(640, 530)
        self.setStyleSheet('QWidget {font-family:"Microsoft JhengHei";font-size:14px;} QPushButton {padding:7px;}')
        layout = QVBoxLayout(self)
        label = QLabel('先登入 ChatGPT，再載入模型並測試一則回覆。\n這是連線驗收工具，人設、記憶及桌寵聊天窗會於後續階段加入。')
        label.setWordWrap(True)
        layout.addWidget(label)
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self.select_account)
        layout.addWidget(self.account)
        buttons = QHBoxLayout()
        self.login = QPushButton('Continue with ChatGPT')
        self.add_account = QPushButton('新增帳號／工作區')
        self.logout = QPushButton('登出目前帳號')
        for button in (self.login, self.add_account, self.logout):
            buttons.addWidget(button)
        layout.addLayout(buttons)
        self.login.clicked.connect(lambda: self.begin_login(False))
        self.add_account.clicked.connect(lambda: self.begin_login(True))
        self.logout.clicked.connect(lambda: self.start(lambda w: self.connection.logout(), self.logged_out))
        row = QHBoxLayout()
        self.models = QComboBox()
        self.models.setMinimumContentsLength(20)
        self.models.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.load_models = QPushButton('載入可用模型')
        row.addWidget(self.models, 1)
        row.addWidget(self.load_models)
        layout.addLayout(row)
        self.load_models.clicked.connect(lambda: self.start(lambda w: self.connection.models(w.cancel), self.models_ready))
        self.message = QPlainTextEdit('請用繁體中文簡短打個招呼。')
        self.message.setMaximumHeight(80)
        layout.addWidget(self.message)
        self.send = QPushButton('傳送測試訊息（使用帳號額度）')
        self.send.clicked.connect(self.test_reply)
        self.cancel = QPushButton('取消')
        self.cancel.setEnabled(False)
        self.cancel.clicked.connect(self.cancel_operation)
        row = QHBoxLayout()
        row.addWidget(self.send, 1)
        row.addWidget(self.cancel)
        layout.addLayout(row)
        self.output = QPlainTextEdit()
        self.output.setReadOnly(True)
        self.output.setPlaceholderText('回覆會顯示於此處。')
        layout.addWidget(self.output, 1)
        self.status = QLabel('尚未驗收：請登入並完成一輪文字收發。')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.controls = (self.account, self.login, self.add_account, self.logout,
                         self.models, self.load_models, self.send, self.message)
        self.refresh_accounts()

    def refresh_accounts(self):
        self.account.blockSignals(True)
        self.account.clear()
        try:
            for account in self.connection.accounts():
                label = account['label'] + ('' if account['signed_in'] else '（已登出）')
                self.account.addItem(label, account['id'])
                if account['active']:
                    self.account.setCurrentIndex(self.account.count() - 1)
        except ConnectionFailure as error:
            self.status.setText(str(error))
        self.account.blockSignals(False)

    def select_account(self):
        if self.account.currentData():
            try:
                self.connection.select(self.account.currentData())
                self.models.clear()
                self.status.setText('帳號已切換，請重新載入可用模型。')
            except ConnectionFailure as error:
                self.status.setText(str(error))

    def start(self, operation, completed):
        if self.worker is not None:
            return
        for control in self.controls:
            control.setEnabled(False)
        self.cancel.setEnabled(True)
        self.status.setText('處理中…')
        worker = Operation(operation, self)
        self.worker = worker
        worker.status.connect(self.status.setText)
        worker.delta.connect(self.append_delta)
        worker.ready.connect(completed)
        worker.failed.connect(self.status.setText)
        worker.finished.connect(self.finished)
        worker.start()

    def finished(self):
        worker = self.worker
        self.worker = None
        worker.deleteLater()
        for control in self.controls:
            control.setEnabled(True)
        self.cancel.setEnabled(False)
        if self.closing:
            self.close()

    def begin_login(self, new_account):
        self.start(lambda w: self.connection.login(w.cancel, w.status.emit, new_account), self.login_ready)

    def login_ready(self, result):
        self.models.clear()
        self.refresh_accounts()
        self.status.setText(result + ' 請載入可用模型。')

    def logged_out(self, result):
        self.models.clear()
        self.refresh_accounts()
        self.status.setText(result)

    def models_ready(self, models):
        self.models.clear()
        for model in models:
            self.models.addItem(model['name'], model['id'])
        self.status.setText(f'已載入 {len(models)} 個模型，請選擇後傳送測試訊息。' if models else
                            '此帳號尚未提供可用模型，請檢查授權或改用其他帳號。')

    def test_reply(self):
        model = self.models.currentData()
        message = self.message.toPlainText().strip()
        if not model or not message:
            self.status.setText('請先載入並選擇模型，再輸入測試訊息。')
            return
        self.output.clear()
        self.start(lambda w: self.connection.reply(model, [{'role': 'user', 'content': message}],
                   cancel=w.cancel, on_delta=w.delta.emit), self.reply_ready)

    def append_delta(self, delta):
        self.output.insertPlainText(delta)

    def reply_ready(self, reply):
        self.output.setPlainText(reply)
        self.status.setText('連線驗收成功：已完成 ChatGPT 帳號授權、模型選擇及一輪文字收發。')

    def cancel_operation(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText('正在取消，網路請求可能需要約 20 秒結束…')

    def closeEvent(self, event):
        if self.worker is not None:
            self.closing = True
            self.cancel_operation()
            event.ignore()
        else:
            event.accept()


def main():
    if '--offline-self-test' in sys.argv:
        import io
        import json
        import tempfile
        import time
        import jwt
        from cryptography.hazmat.primitives.asymmetric import rsa
        from chat_connection import CredentialStore, consume_stream
        with tempfile.TemporaryDirectory() as folder:
            store = CredentialStore(folder)
            state = store.load()
            store.save(state)
            assert store.load() == state
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        token = jwt.encode({'sub': 'offline-test', 'exp': time.time() + 60}, key, algorithm='RS256')
        assert jwt.decode(token, key.public_key(), algorithms=['RS256'])['sub'] == 'offline-test'
        events = [{'type': 'response.output_text.delta', 'delta': '測試'}, {'type': 'response.completed'}]
        stream = io.BytesIO(''.join('data: ' + json.dumps(e) + '\n\n' for e in events).encode())
        assert consume_stream(stream) == '測試'
        destination = sys.argv[sys.argv.index('--self-test-output') + 1]
        Path(destination).write_text(json.dumps({'passed': True, 'checks': [
            'Windows DPAPI 儲存與還原', 'RSA 簽章與驗證', '串流完成事件解析'], 'network_used': False},
            ensure_ascii=False, indent=2), encoding='utf-8')
        return 0
    app = QApplication(sys.argv)
    if QApplication.platformName() == 'offscreen':
        # The offscreen Qt backend does not enumerate installed Windows fonts.
        for name in ('msjh.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
        app.setFont(QFont('Microsoft JhengHei', 10))
    root = Path(sys._MEIPASS) if getattr(sys, 'frozen', False) else Path(__file__).parent / 'translations'
    app.qt_translator = QTranslator(app)
    if app.qt_translator.load(str(root / 'qtbase_zh_TW.qm')):
        app.installTranslator(app.qt_translator)
    socket = QLocalSocket()
    socket.connectToServer('bailong-chat-connection-check-v1')
    if socket.waitForConnected(150):
        return 0
    QLocalServer.removeServer('bailong-chat-connection-check-v1')
    server = QLocalServer()
    if not server.listen('bailong-chat-connection-check-v1'):
        return 1
    panel = ConnectionCheck()
    panel.show()
    if '--preview' in sys.argv:
        def preview():
            if '--preview-output' in sys.argv:
                destination = sys.argv[sys.argv.index('--preview-output') + 1]
                panel.grab().save(str(Path(destination)))
            panel.close()
        QTimer.singleShot(600, preview)
    return app.exec()


if __name__ == '__main__':
    sys.exit(main())
