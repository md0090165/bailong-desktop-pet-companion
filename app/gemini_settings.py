"""Gemini configuration without putting credentials in the chat window."""
import webbrowser
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QLineEdit, QPushButton
from chat_connection import ConnectionFailure


class GeminiSettings(QWidget):
    def __init__(self, connection):
        super().__init__()
        self.connection = connection
        self.worker = None
        self.closing = False
        self.setWindowTitle('白龍桌寵 · Gemini 設定')
        self.resize(550, 340)
        layout = QVBoxLayout(self)
        description = QLabel('請使用自己的 Google AI Studio API Key。\n此金鑰與 Google Drive 的 OAuth JSON 不同。\n金鑰由 Windows 加密保存，不會加入對話或同步至 Google Drive。')
        description.setWordWrap(True)
        layout.addWidget(description)
        self.key = QLineEdit()
        self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText('貼上 Gemini API Key（不會顯示已保存的金鑰）')
        layout.addWidget(self.key)
        create = QPushButton('開啟 Google AI Studio 取得 API Key')
        create.clicked.connect(lambda: webbrowser.open('https://aistudio.google.com/apikey'))
        layout.addWidget(create)
        save = QPushButton('儲存 API Key')
        save.clicked.connect(self.save)
        layout.addWidget(save)
        remove = QPushButton('移除此機的 Gemini API Key')
        remove.clicked.connect(self.remove)
        layout.addWidget(remove)
        privacy = QLabel('免費額度依模型、地區與 Google 帳號資格而定，模型清單不保證每款都免費。\n免費服務可能使用輸入與回覆改善產品。聊天會傳送人設、相關記憶及對話；隨機事件亦會傳送通過本機檢查的桌面圖片。\n若只想聊天，可在隨機事件設定停用事件。請勿使用含敏感資料的內容。')
        privacy.setWordWrap(True)
        layout.addWidget(privacy)
        self.status = QLabel()
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.refresh_accounts()

    def refresh_accounts(self):
        try:
            self.status.setText('已保存 API Key。關閉後可重新載入模型，再傳送訊息測試。' if self.connection.has_key() else '尚未設定 API Key。')
        except ConnectionFailure as error:
            self.status.setText(str(error))

    def save(self):
        if not self.key.text().strip():
            self.status.setText('請先貼上 API Key；空白不會覆蓋原有金鑰。')
            return
        try:
            self.connection.save_key(self.key.text())
            self.key.clear()
            self.refresh_accounts()
        except ConnectionFailure as error:
            self.status.setText(str(error))

    def remove(self):
        try:
            self.connection.save_key('')
            self.key.clear()
            self.refresh_accounts()
        except ConnectionFailure as error:
            self.status.setText(str(error))

    def cancel_operation(self):
        pass
