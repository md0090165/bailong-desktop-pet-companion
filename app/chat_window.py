"""Compact chat UI with read-only persona and persistent local conversation."""
import json
from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
                               QComboBox, QPlainTextEdit, QScrollArea)

from chat_connection import ChatConnection, ConnectionFailure
from gemini_connection import ModelServices
from gemini_settings import GeminiSettings
from reply_animation import AnimationCatalog, ReplyFormatFailure
from long_memory import MemoryStore, MemoryFailure
from memory_window import MemoryManager
from relationship import RelationshipStore, RelationshipFailure, bias_animation
from relationship_window import RelationshipPanel
from cloud_sync_window import SyncPanel
from cloud_sync_core import recover
from google_drive_sync import SyncFailure
from connection_check import Operation, ConnectionCheck
from character_context import (CharacterProfile, ConversationStore, ContextFailure,
                               project_root as find_project_root, empty_state,
                               turn_messages, response_with_context, completed_turn)


class MessageInput(QPlainTextEdit):
    submitted = Signal()
    escaped = Signal()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.escaped.emit()
            event.accept()
            return
        if (event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)
                and event.modifiers() == Qt.KeyboardModifier.NoModifier):
            self.submitted.emit()
            event.accept()
            return
        super().keyPressEvent(event)


class AccountSettings(ConnectionCheck):
    def __init__(self, connection):
        super().__init__(connection)
        self.setWindowTitle('白龍桌寵 · 帳號設定')
        self.layout().itemAt(0).widget().setText('管理白龍使用的 ChatGPT 帳號。\n完成設定後，關閉此視窗即可回到聊天。')
        for widget in (self.models, self.load_models, self.message, self.send, self.output):
            widget.hide()
        self.status.setText('登入資訊由 Windows 加密保存在這台電腦。')
        self.resize(570, 240)

    def login_ready(self, result):
        self.refresh_accounts()
        self.status.setText('登入成功。關閉設定視窗後即可選擇模型並聊天。')

    def select_account(self):
        super().select_account()
        self.status.setText('帳號已選擇，關閉設定視窗後回到聊天。')


def reply_duration(text):
    """Allow one second to orient, then eight visible characters per second."""
    return max(1500, min(2147483647, 1000 + 125 * sum(not c.isspace() for c in text)))


class ReplyBubble(QWidget):
    def __init__(self, parent):
        super().__init__(parent, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint |
                         Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.setStyleSheet('QScrollArea {background:rgba(239,226,250,225);border:1px solid #cbb4de;border-radius:14px;} QScrollArea > QWidget > QWidget {background:transparent;}')
        self.label = QLabel()
        self.label.setTextFormat(Qt.TextFormat.PlainText)
        self.label.setWordWrap(True)
        self.label.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.label.setStyleSheet('font-family:"Microsoft JhengHei";font-size:14px;color:#392749;background:transparent;padding:12px;')
        self.scroll.setWidget(self.label)
        layout.addWidget(self.scroll)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.hide)

    def present(self, text, anchor):
        area = anchor.screen().availableGeometry()
        width = min(340, area.width())
        self.label.setText(text)
        height = max(60, self.label.heightForWidth(width - 22) + 6)
        self.resize(width, min(height, max(60, area.height() // 2)))
        rect = anchor.frameGeometry()
        x = max(area.left(), min(rect.center().x() - width // 2, area.right() - width + 1))
        y = rect.top() - self.height() - 10
        if y < area.top():
            y = rect.bottom() + 10
        y = max(area.top(), min(y, area.bottom() - self.height() + 1))
        self.move(x, y)
        self.show()
        self.raise_()
        self.scroll.verticalScrollBar().setValue(0)
        self.timer.start(reply_duration(text))


class ChatWindow(QWidget):
    reply_action = Signal(str, str)

    def __init__(self, connection=None, project_root=None):
        super().__init__(None, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.project_root = Path(project_root) if project_root else (
            find_project_root() if connection is None else Path(connection.store.folder))
        self.connection = connection or ModelServices()
        self.cloud_sync=None;self.cloud_recovery_error=''
        try:recover(self.project_root)
        except SyncFailure as e:self.cloud_recovery_error=str(e)
        self.character = CharacterProfile(self.project_root)
        self.animations = AnimationCatalog(self.project_root)
        self.event_controller = None
        self.relationship_store = None
        self.relationship_window = None
        self.relationship_error = ''
        self.memory_store = None
        self.memory_window = None
        self.memory_error = ''
        self.conversation = None
        self.conversation_state = empty_state()
        self.history_error = ''
        self.history_dirty = False
        self.pending_model = None
        self.pending_character = None
        self.worker = None
        self.settings = None
        self.settings_were_open = False
        self.models_loaded_for = None
        self.messages = []
        self.pending_message = None
        self.session_account = None
        self.setWindowTitle('與白龍聊天')
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(350, 86)
        self.anchor = None
        self.parameters = QWidget(self, Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.parameters.setWindowTitle('白龍桌寵 · 參數設定')
        self.parameters.resize(520, 270)
        self.parameters.setStyleSheet('QWidget {font-family:"Microsoft JhengHei";font-size:14px;color:#352c44;background:#faf7fd;} QComboBox,QPushButton {padding:7px;background:#eee4f6;border:1px solid #d7cbe2;border-radius:6px;}')
        outer_settings=QVBoxLayout(self.parameters)
        settings_scroll=QScrollArea();settings_scroll.setWidgetResizable(True)
        settings_body=QWidget();settings_scroll.setWidget(settings_body);outer_settings.addWidget(settings_scroll)
        settings_layout = QVBoxLayout(settings_body);self.settings_layout=settings_layout
        self.provider = QComboBox()
        self.provider.addItem('ChatGPT · 使用帳號方案', 'chatgpt')
        self.provider.addItem('Gemini · 使用自己的 API Key', 'gemini')
        self.provider.setVisible(isinstance(self.connection, ModelServices))
        if isinstance(self.connection, ModelServices):
            self.provider.setCurrentIndex(self.provider.findData(self.connection.provider))
        self.provider.currentIndexChanged.connect(self.change_provider)
        settings_layout.addWidget(self.provider)
        settings_layout.addWidget(QLabel('白龍紀錄／帳號（切換服務可沿用相同紀錄）'))
        self.account = QComboBox()
        self.account.currentIndexChanged.connect(self.select_account)
        settings_layout.addWidget(self.account)
        self.settings_button = QPushButton('管理帳號／登入 ChatGPT')
        self.settings_button.clicked.connect(self.open_settings)
        settings_layout.addWidget(self.settings_button)
        settings_layout.addWidget(QLabel('回覆模型'))
        row = QHBoxLayout()
        self.models = QComboBox()
        self.models.currentIndexChanged.connect(self.save_model)
        self.refresh = QPushButton('重新載入模型')
        self.refresh.clicked.connect(self.load_models)
        row.addWidget(self.models, 1)
        row.addWidget(self.refresh)
        settings_layout.addLayout(row)
        self.cancel = QPushButton('取消目前請求')
        self.cancel.clicked.connect(self.cancel_operation)
        self.cancel.setEnabled(False)
        settings_layout.addWidget(self.cancel)
        self.status = QLabel('請選擇帳號與模型後開始聊天。')
        self.status.setWordWrap(True)
        settings_layout.addWidget(self.status)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.message = MessageInput(self)
        self.message.setAccessibleName('訊息輸入')
        self.message.setStyleSheet('QPlainTextEdit {font-family:"Microsoft JhengHei";font-size:14px;color:#392749;background:rgba(231,215,247,210);border:1px solid rgba(184,153,209,200);border-radius:14px;padding:10px;selection-background-color:#b99ad1;}')
        self.message.setPlaceholderText('想跟白龍說什麼？ Enter 送出')
        self.message.setToolTip('Enter 送出 · Shift＋Enter 換行 · Esc 取消或收起\n帳號與模型請至白龍右鍵 → 參數設定')
        self.message.submitted.connect(self.send_message)
        self.message.escaped.connect(self.escape_input)
        layout.addWidget(self.message)
        # Internal session transcript only; no conversation viewer in this stage.
        self.transcript = QPlainTextEdit(self)
        self.transcript.hide()
        self.clear = QPushButton(self)
        self.clear.hide()
        self.send = QPushButton(self)
        self.send.hide()
        settings_layout.addWidget(QLabel('角色核心 · Character.md'))
        self.character_status = QLabel()
        self.character_status.setWordWrap(True)
        self.character_status.setToolTip(str(self.character.path))
        settings_layout.addWidget(self.character_status)
        self.reload_character_button = QPushButton('重新載入人設')
        self.reload_character_button.clicked.connect(self.reload_character)
        settings_layout.addWidget(self.reload_character_button)
        self.history_status = QLabel()
        self.history_status.setWordWrap(True)
        settings_layout.addWidget(self.history_status)
        self.animation_status = QLabel(self.animations.warning or '回覆動作：已啟用，依心情選擇 10 種動作或待機。')
        self.animation_status.setWordWrap(True)
        settings_layout.addWidget(self.animation_status)
        self.memory_status = QLabel('登入後可使用此帳號的長期記憶。')
        self.memory_status.setWordWrap(True)
        settings_layout.addWidget(self.memory_status)
        self.memory_button = QPushButton('管理長期記憶')
        self.memory_button.clicked.connect(self.open_memory)
        settings_layout.addWidget(self.memory_button)
        self.relationship_status = QLabel('登入後可查看關係與心情。')
        self.relationship_status.setWordWrap(True)
        settings_layout.addWidget(self.relationship_status)
        self.relationship_button = QPushButton('查看關係與心情')
        self.relationship_button.clicked.connect(self.open_relationship)
        settings_layout.addWidget(self.relationship_button)
        self.cloud_sync_button=QPushButton('同步記憶 · Google Drive')
        self.cloud_sync_button.clicked.connect(self.open_cloud_sync)
        settings_layout.addWidget(self.cloud_sync_button)
        self.parameters.resize(560, 700)
        self.controls = (self.provider, self.cloud_sync_button, self.relationship_button, self.memory_button, self.reload_character_button, self.account, self.models, self.refresh, self.settings_button, self.clear, self.send)
        self.bubble = ReplyBubble(self)
        self.reload_character(announce=False)
        self.refresh_accounts()
        self.relationship_timer=QTimer(self)
        self.relationship_timer.setInterval(15000)
        self.relationship_timer.timeout.connect(self.update_relationship_status)
        self.relationship_timer.start()

    def open_cloud_sync(self):
        if self.worker:return
        if self.cloud_recovery_error:self.notify(self.cloud_recovery_error);return
        if not self.session_account:self.notify('請先選擇白龍紀錄，再選擇要同步的版本。');return
        if self.history_dirty:
            try:self.save_conversation()
            except ContextFailure as error:self.notify(str(error));return
        if self.cloud_sync and self.cloud_sync.repo.account!=self.session_account:
            if self.cloud_sync.worker:return
            self.cloud_sync.shutdown();self.cloud_sync.deleteLater();self.cloud_sync=None
        if self.cloud_sync is None:self.cloud_sync=SyncPanel(self)
        self.cloud_sync.begin()

    def reload_character(self, checked=False, announce=True):
        if self.worker:
            return
        try:
            text = self.character.reload()
            self.character_status.setText(text)
            if announce:
                self.status.setText('人設已重新載入，下次傳送即生效。')
        except ContextFailure as error:
            suffix = '（繼續使用上次成功載入的人設）' if self.character.text else ''
            self.character_status.setText(str(error) + suffix)
            if announce:
                self.status.setText(str(error) + suffix)

    def restore_conversation(self, account):
        self.conversation = None
        self.conversation_state = empty_state()
        self.history_error = ''
        self.history_dirty = False
        self.messages = []
        self.transcript.clear()
        if not account:
            self.history_status.setText('登入後會恢復此帳號的近期對話。')
            self.restore_memory(None)
            self.restore_relationship(None)
            if self.event_controller:self.event_controller.bind(self)
            return
        self.conversation = ConversationStore(self.project_root, account)
        try:
            self.conversation_state, warning = self.conversation.load()
            self.messages = turn_messages(self.conversation_state['turns'][-6:])
            for item in self.messages:
                self.append_text(('你：' if item['role'] == 'user' else '白龍：') + '\n' + item['content'] + '\n\n')
            self.update_history_status(warning)
        except ContextFailure as error:
            self.history_error = str(error)
            self.history_status.setText(self.history_error)
        self.restore_memory(account)
        self.restore_relationship(account)
        if self.event_controller:self.event_controller.bind(self)

    def update_history_status(self, warning=''):
        count = len(self.conversation_state['turns'])
        self.history_status.setText(warning or f'本機已保存 {count} 輪對話；較早內容會依需要摘要。')

    def save_conversation(self):
        if self.conversation:
            self.conversation.save(self.conversation_state)
        self.history_dirty = False
        self.update_history_status()
        self.learn_memory()
        self.learn_relationship()
        if self.event_controller:self.event_controller.observe(self.conversation_state["turns"])

    def restore_memory(self, account):
        if self.memory_window:
            self.memory_window.close();self.memory_window.deleteLater();self.memory_window=None
        self.memory_store=None;self.memory_error=''
        if account:
            self.memory_store=MemoryStore(self.project_root,account)
            try:
                self.memory_store.load(self.conversation_state['turns'])
            except MemoryFailure as error:
                self.memory_error=str(error)
        self.update_memory_status()

    def update_memory_status(self):
        if self.memory_error:
            self.memory_status.setText(self.memory_error)
        elif self.memory_store:
            self.memory_status.setText(self.memory_store.warning or
                f"長期記憶：{len(self.memory_store.state['items'])} 則，依話題選取。")
        else:
            self.memory_status.setText('登入後可使用此帳號的長期記憶。')

    def learn_memory(self):
        if not self.memory_store or not self.memory_store.ready or self.history_dirty:
            return
        try:
            self.memory_store.learn(self.conversation_state['turns'])
            self.memory_error=''
        except MemoryFailure as error:
            self.memory_error=str(error)
        self.update_memory_status()

    def memory_changed(self):
        if self.memory_store and self.memory_store.ready:
            self.memory_error=''
        self.update_memory_status()

    def open_memory(self):
        if self.worker:return
        if not self.memory_store:
            self.notify('請先登入帳號，再管理長期記憶。');return
        self.learn_memory()
        if self.memory_window is None:
            self.memory_window=MemoryManager(self.memory_store,self)
            self.memory_window.changed.connect(self.memory_changed)
        self.memory_window.refresh()
        self.memory_window.show();self.memory_window.raise_();self.memory_window.activateWindow()

    def restore_relationship(self,account):
        if self.relationship_window:
            self.relationship_window.close();self.relationship_window.deleteLater();self.relationship_window=None
        self.relationship_store=None;self.relationship_error=''
        if account:
            self.relationship_store=RelationshipStore(self.project_root,account)
            try:self.relationship_store.load(self.conversation_state['turns'])
            except RelationshipFailure as error:self.relationship_error=str(error)
        self.update_relationship_status()

    def update_relationship_status(self):
        store=self.relationship_store
        if self.relationship_error:self.relationship_status.setText(self.relationship_error)
        elif store and store.ready:
            view=store.snapshot()
            self.relationship_status.setText(store.warning or store.rules.warning or
                f"親密 {view['closeness']:.1f} · 信任 {view['trust']:.1f} · 心情 {view['mood_label']}")
        else:self.relationship_status.setText('登入後可查看關係與心情。')
        if self.relationship_window and self.relationship_window.isVisible():self.relationship_window.refresh()

    def learn_relationship(self):
        if not self.relationship_store or not self.relationship_store.ready or self.history_dirty:return
        try:
            self.relationship_store.learn(self.conversation_state['turns'])
            self.relationship_error=''
        except RelationshipFailure as error:self.relationship_error=str(error)
        self.update_relationship_status()

    def open_relationship(self):
        if self.worker:return
        if not self.relationship_store:
            self.notify('請先登入帳號，再查看關係與心情。');return
        self.learn_relationship()
        if self.relationship_window is None:self.relationship_window=RelationshipPanel(self.relationship_store,self)
        self.relationship_window.refresh();self.relationship_window.show()
        self.relationship_window.raise_();self.relationship_window.activateWindow()

    def escape_input(self):
        if self.worker:
            self.cancel_operation()
        else:
            self.hide()

    def notify(self, text):
        self.status.setText(text)
        self.bubble.present(text, self.anchor or self)

    def show_parameters(self, pet):
        self.anchor = pet
        area=pet.screen().availableGeometry()
        self.parameters.resize(560,min(780,max(400,area.height()-80)))
        self.parameters.show()
        self.parameters.raise_()
        self.parameters.activateWindow()
        if not self.worker and self.models_loaded_for != self.account.currentData():
            self.load_models()

    def change_provider(self):
        if not isinstance(self.connection, ModelServices) or self.worker:
            return
        try:
            if self.history_dirty:
                self.save_conversation()
            self.connection.set_provider(self.provider.currentData())
            self.refresh_accounts()
            self.models_loaded_for = None
            self.models.clear()
            self.status.setText('已切換服務。請設定帳號或 API Key，再載入模型。')
        except (ConnectionFailure, ContextFailure) as error:
            self.provider.blockSignals(True)
            self.provider.setCurrentIndex(self.provider.findData(self.connection.provider))
            self.provider.blockSignals(False)
            self.notify(str(error))

    def preferences(self):
        try:
            values = json.loads((self.connection.store.folder / 'chat-preferences.json').read_text(encoding='utf-8'))
            if isinstance(self.connection, ModelServices) and self.connection.provider == 'gemini':
                return {k[7:]: v for k, v in values.items() if k.startswith('gemini:')}
            return {k: v for k, v in values.items() if not k.startswith('gemini:')}
        except (OSError, ValueError, AttributeError):
            return {}

    def save_model(self):
        if not self.models.currentData() or not self.account.currentData():
            return
        try:
            folder = self.connection.store.folder
            folder.mkdir(parents=True, exist_ok=True)
            path = folder / 'chat-preferences.json'
            try: preferences = json.loads(path.read_text(encoding='utf-8'))
            except (OSError, ValueError): preferences = {}
            prefix = 'gemini:' if isinstance(self.connection, ModelServices) and self.connection.provider == 'gemini' else ''
            preferences[prefix + self.account.currentData()] = self.models.currentData()
            path = folder / 'chat-preferences.json'
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(preferences), encoding='utf-8')
            temporary.replace(path)
        except (OSError, AttributeError):
            pass

    def refresh_accounts(self):
        if isinstance(self.connection, ModelServices):
            self.settings_button.setText('設定 Gemini API Key' if self.connection.provider == 'gemini' else '管理帳號／登入 ChatGPT')
        self.account.blockSignals(True)
        self.account.clear()
        try:
            accounts = self.connection.accounts()
            active = next((a for a in accounts if a['active']), None)
            for account in accounts:
                self.account.addItem(account['label'] + ('' if account['signed_in'] else '（已登出）'), account['id'])
            if active:
                self.account.setCurrentIndex(self.account.findData(active['id']))
            elif accounts:
                self.account.setCurrentIndex(-1)
        except ConnectionFailure as error:
            self.status.setText(str(error))
        self.account.blockSignals(False)
        account_id = self.account.currentData()
        if account_id != self.session_account:
            self.messages.clear()
            self.transcript.clear()
            self.message.clear()
            self.session_account = account_id
            self.models_loaded_for = None
            self.models.clear()
            self.restore_conversation(account_id)

    def select_account(self):
        account_id = self.account.currentData()
        if not account_id or self.worker:
            return
        try:
            if self.history_dirty:
                self.save_conversation()
            self.connection.select(account_id)
            self.refresh_accounts()
            self.load_models()
        except (ConnectionFailure, ContextFailure) as error:
            self.account.blockSignals(True)
            self.account.setCurrentIndex(self.account.findData(self.session_account))
            self.account.blockSignals(False)
            self.notify(str(error))

    def show_near(self, pet, load_models=True):
        self.anchor = pet
        if not self.isVisible():
            screen = pet.screen()
            area = screen.availableGeometry()
            rect = pet.frameGeometry()
            x = rect.left() - self.width() - 12
            if x < area.left():
                x = rect.right() + 12
            x = max(area.left(), min(x, area.right() - self.width() + 1))
            y = max(area.top(), min(rect.center().y() - self.height() // 2, area.bottom() - self.height() + 1))
            self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()
        self.message.setFocus()
        if load_models and not self.worker and self.models_loaded_for != self.account.currentData():
            self.load_models()

    def load_models(self):
        if self.worker:
            return
        if not self.account.currentData():
            self.notify('請在白龍右鍵 → 參數設定，選擇服務並完成帳號或 API Key 設定。')
            return
        self.start(lambda w: self.connection.models(w.cancel), self.models_ready, '正在載入可用模型…')

    def models_ready(self, models):
        selected = self.preferences().get(self.account.currentData())
        self.models.blockSignals(True)
        self.models.clear()
        for model in models:
            self.models.addItem(model['name'], model['id'])
        index = self.models.findData(selected)
        if index >= 0:
            self.models.setCurrentIndex(index)
        self.models.blockSignals(False)
        self.save_model()
        self.models_loaded_for = self.account.currentData() if models else None
        self.status.setText('準備好了。傳送訊息會使用目前帳號的額度。' if models else
                            '此帳號尚未提供可用模型，請檢查帳號授權。')

    def start(self, operation, completed, status):
        if self.worker:
            return
        self.status.setText(status)
        for control in self.controls:
            control.setEnabled(False)
        self.cancel.setEnabled(True)
        self.message.setPlaceholderText(status + '（Esc 取消）')
        worker = Operation(operation, self)
        self.worker = worker
        worker.ready.connect(completed)
        worker.status.connect(self.operation_status)
        worker.failed.connect(self.failed)
        worker.delta.connect(self.append_delta)
        worker.finished.connect(self.finished)
        worker.start()

    def operation_status(self, text):
        self.status.setText(text)
        self.message.setPlaceholderText(text + '（Esc 取消）')

    def append_text(self, text):
        self.transcript.moveCursor(QTextCursor.MoveOperation.End)
        self.transcript.insertPlainText(text)
        self.transcript.ensureCursorVisible()

    def append_delta(self, delta):
        if self.pending_message is not None and self.worker and not self.worker.cancel.is_set():
            # Wait for validated text; never render streamed JSON metadata.
            self.status.setText('白龍正在回覆…')

    def send_message(self):
        if self.cloud_recovery_error:self.notify(self.cloud_recovery_error);return
        if self.cloud_sync and (self.cloud_sync.isVisible() or self.cloud_sync.worker):return
        if self.worker or (self.settings and self.settings.isVisible()) or (self.memory_window and self.memory_window.isVisible()) or (self.relationship_window and self.relationship_window.isVisible()):
            return
        message = self.message.toPlainText().strip()
        model = self.models.currentData()
        if not message:
            return
        if not model:
            self.notify('請在白龍右鍵 → 參數設定，載入並選擇模型。')
            return
        if len(message) > 8000:
            self.notify('這則訊息超過 8,000 字元，請分段傳送。')
            return
        try:
            relationship_view=self.relationship_store.preview(message) if self.relationship_store else None
            relationship_prompt=self.relationship_store.instructions(relationship_view) if self.relationship_store else ''
            instructions = self.character.instructions() + relationship_prompt + self.animations.instructions()
            if self.history_error:
                raise ContextFailure(self.history_error)
            if self.history_dirty:
                self.save_conversation()
        except ContextFailure as error:
            self.notify(str(error))
            return
        self.pending_message = message
        self.pending_model = model
        self.pending_character = self.character.digest
        self.message.clear()
        self.append_text(('\n\n' if self.transcript.toPlainText() else '') + '你：\n' + message + '\n\n白龍：\n')
        state = self.conversation_state
        memory_context = self.memory_store.context(message) if self.memory_store else ''
        if self.event_controller:
            event_context=self.event_controller.context()
            if event_context:memory_context+='\n'+event_context
        def operation(worker):
            try:
                result = response_with_context(self.connection, model, state, message, instructions, worker, memory_context=memory_context)
                decoded = self.animations.decode(result['reply'], user_message=message)
                decoded = bias_animation(decoded,relationship_view,self.animations,message)
                return {**result, **decoded, 'reply': decoded['text']}
            except (ContextFailure, ReplyFormatFailure) as error:
                raise ConnectionFailure('context', str(error)) from None
        self.start(operation, self.reply_ready, '等待白龍回覆…')

    def reply_ready(self, result):
        if self.pending_message is None:
            return
        if self.worker and self.worker.cancel.is_set():
            self.failed('回覆已取消。')
            return
        reply = result['reply']
        self.conversation_state = {
            'version': 1,
            'turns': self.conversation_state['turns'] + [completed_turn(
                self.pending_message, reply, self.pending_model, self.pending_character,
                emotion=result['emotion'], animation_id=result['animation_id'])],
            'summary': result['summary'], 'summary_until': result['summary_until']}
        self.messages = turn_messages(self.conversation_state['turns'][-6:])
        self.pending_message = None
        self.history_dirty = True
        warning = ''
        try:
            self.save_conversation()
        except ContextFailure as error:
            warning = str(error)
            self.history_status.setText(warning)
        warning = warning or self.memory_error or self.relationship_error
        self.status.setText(warning or '回覆完成，對話已保存在本機。')
        self.append_text(reply)
        self.bubble.present(reply + ('\n\n〔' + warning + '〕' if warning else ''), self.anchor or self)
        name = self.animations.entries.get(result['animation_id'],{}).get('name','待機')
        suffix = '（'+result['selection_note']+'）' if result.get('selection_note') else ''
        self.animation_status.setText(f"本輪心情：{result['emotion']} · 動作：{name}{suffix}")
        self.reply_action.emit(result['animation_id'], result['emotion'])

    def failed(self, error):
        if self.pending_message is not None:
            self.append_text('\n〔此則回覆未完成〕')
            if not self.message.toPlainText().strip():
                self.message.setPlainText(self.pending_message)
            self.pending_message = None
        self.notify(error)

    def finished(self):
        worker = self.worker
        if self.pending_message is not None:
            self.failed('回覆已取消。')
        self.worker = None
        worker.deleteLater()
        for control in self.controls:
            control.setEnabled(True)
        self.cancel.setEnabled(False)
        self.message.setPlaceholderText('想跟白龍說什麼？ Enter 送出')
        if worker.cancel.is_set():
            self.status.setText('操作已取消。')

    def cancel_operation(self):
        if self.worker:
            self.worker.cancel.set()
            self.status.setText('正在取消，等待目前網路請求結束…')

    def clear_conversation(self):
        if self.worker:
            return
        self.messages.clear()
        self.transcript.clear()
        self.conversation_state = empty_state()
        self.history_dirty = True
        try:
            self.save_conversation()
            self.status.setText('已清除近期對話與摘要。')
        except ContextFailure as error:
            self.notify(str(error))

    def open_settings(self):
        if self.worker:
            return
        if self.history_dirty:
            try:
                self.save_conversation()
            except ContextFailure as error:
                self.notify(str(error))
                return
        gemini = isinstance(self.connection, ModelServices) and self.connection.provider == 'gemini'
        if self.settings is not None and isinstance(self.settings, GeminiSettings) != gemini:
            self.settings.hide()
            self.settings.deleteLater()
            self.settings = None
        if self.settings is None:
            self.settings = GeminiSettings(self.connection) if gemini else AccountSettings(
                self.connection.chatgpt if isinstance(self.connection, ModelServices) else self.connection)
            self.settings.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.settings.closing = False
        self.settings.refresh_accounts()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()
        self.settings_were_open = True

    def sync_settings(self):
        if self.settings_were_open and self.settings and not self.settings.isVisible() and not self.settings.worker:
            self.settings_were_open = False
            self.refresh_accounts()
            self.models_loaded_for = None
            self.models.clear()
            if self.isVisible() or self.parameters.isVisible():
                self.load_models()

    def interaction_busy(self):
        return bool(self.cloud_sync and (self.cloud_sync.isVisible() or self.cloud_sync.worker)) or self.isVisible() or self.parameters.isVisible() or self.bubble.isVisible() or bool(self.memory_window and self.memory_window.isVisible()) or bool(self.relationship_window and self.relationship_window.isVisible()) or self.worker is not None or bool(self.settings and
            (self.settings.isVisible() or self.settings.worker is not None))

    def shutdown(self):
        if self.cloud_sync:self.cloud_sync.shutdown()
        if self.event_controller:self.event_controller.shutdown()
        self.relationship_timer.stop()
        if self.relationship_window:self.relationship_window.hide()
        self.bubble.timer.stop()
        self.bubble.hide()
        self.parameters.hide()
        if self.memory_window:self.memory_window.hide()
        self.cancel_operation()
        self.hide()
        if self.settings:
            self.settings.cancel_operation()
            self.settings.hide()

    def workers_running(self):
        return bool(self.cloud_sync and self.cloud_sync.worker) or self.worker is not None or bool(self.settings and self.settings.worker is not None)

    def closeEvent(self, event):
        self.cancel_operation()
        self.hide()
        event.ignore()
