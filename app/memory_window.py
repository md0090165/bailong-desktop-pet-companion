"""Local memory editor; opening and editing never invokes a model."""
from datetime import datetime
from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QLabel,QLineEdit,
                               QListWidget,QListWidgetItem,QComboBox,QPlainTextEdit,
                               QSpinBox,QPushButton)
from long_memory import CATEGORIES, MemoryFailure


def local_date(value):
    try:return datetime.fromisoformat(value).astimezone().strftime('%Y/%m/%d %H:%M')
    except (ValueError,TypeError):return value

class MemoryManager(QWidget):
    changed=Signal()

    def __init__(self,store,parent=None):
        super().__init__(parent,Qt.WindowType.Window|Qt.WindowType.WindowStaysOnTopHint)
        self.store=store;self.current_id=None
        self.setWindowTitle('白龍桌寵 · 長期記憶')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(660,700)
        self.setStyleSheet('QWidget {font-family:"Microsoft JhengHei";font-size:14px;color:#352c44;background:#faf7fd;} QLineEdit,QPlainTextEdit,QListWidget,QComboBox,QSpinBox {background:white;border:1px solid #d7cbe2;border-radius:5px;padding:5px;} QPushButton {background:#eee4f6;border:1px solid #d7cbe2;border-radius:6px;padding:7px;}')
        layout=QVBoxLayout(self)
        title=QLabel('管理此帳號的長期記憶。修正後下一則聊天生效，原始聊天紀錄會保留。')
        title.setWordWrap(True);layout.addWidget(title)
        row=QHBoxLayout()
        self.filter=QComboBox();self.filter.addItem('全部分類','')
        for key,name in CATEGORIES.items():self.filter.addItem(name,key)
        self.search=QLineEdit();self.search.setPlaceholderText('搜尋記憶內容')
        row.addWidget(self.filter);row.addWidget(self.search,1);layout.addLayout(row)
        self.items=QListWidget();self.items.setMinimumHeight(150);layout.addWidget(self.items,1)
        self.source=QPlainTextEdit();self.source.setReadOnly(True);self.source.setMaximumHeight(125)
        self.source.setPlaceholderText('選擇一則記憶，查看日期及來源原話。');layout.addWidget(self.source)
        layout.addWidget(QLabel('記憶內容'))
        self.content=QPlainTextEdit();self.content.setMaximumHeight(100)
        self.content.setPlaceholderText('輸入要保留或修正的內容（最多 1,000 字元）');layout.addWidget(self.content)
        row=QHBoxLayout();row.addWidget(QLabel('分類'))
        self.category=QComboBox()
        for key,name in CATEGORIES.items():self.category.addItem(name,key)
        row.addWidget(self.category);row.addWidget(QLabel('重要程度'))
        self.importance=QSpinBox();self.importance.setRange(1,5);self.importance.setValue(3)
        row.addWidget(self.importance);row.addStretch();layout.addLayout(row)
        row=QHBoxLayout()
        self.new_button=QPushButton('新增記憶');self.save_button=QPushButton('保存內容');self.delete_button=QPushButton('刪除此則')
        for button in (self.new_button,self.save_button,self.delete_button):row.addWidget(button)
        layout.addLayout(row)
        self.status=QLabel(store.warning or '自動記憶只參考你的明確陳述，不取用白龍創作的經歷。')
        self.status.setWordWrap(True);layout.addWidget(self.status)
        self.search.textChanged.connect(self.refresh)
        self.filter.currentIndexChanged.connect(self.refresh)
        self.items.currentItemChanged.connect(self.load_item)
        self.new_button.clicked.connect(self.new)
        self.save_button.clicked.connect(self.save)
        self.delete_button.clicked.connect(self.delete)
        self.refresh()
        if not store.ready:
            for widget in (self.new_button,self.save_button,self.delete_button,self.content,self.category,self.importance):widget.setEnabled(False)
            self.status.setText('長期記憶無法讀取，已保留原檔。請檢查 Memory 資料夾後重新啟動。')

    def refresh(self,*args):
        selected=self.current_id
        query=self.search.text().strip().casefold();category=self.filter.currentData()
        self.items.blockSignals(True);self.items.clear();restore=None
        for record in sorted(self.store.state['items'],key=lambda item:item['updated_at'],reverse=True):
            if category and record['category']!=category:continue
            if query and query not in record['content'].casefold():continue
            label=f"[{CATEGORIES[record['category']]} · 重要 {record['importance']}] {record['content'][:90]}"
            item=QListWidgetItem(label);item.setData(Qt.ItemDataRole.UserRole,record['id']);self.items.addItem(item)
            if record['id']==selected:restore=item
        self.items.blockSignals(False)
        if restore:self.items.setCurrentItem(restore)
        else:self.new()

    def load_item(self,current,previous=None):
        if not current:return
        item_id=current.data(Qt.ItemDataRole.UserRole)
        record=next((item for item in self.store.state['items'] if item['id']==item_id),None)
        if not record:return
        self.current_id=item_id
        self.content.setPlainText(record['content'])
        self.category.setCurrentIndex(self.category.findData(record['category']))
        self.importance.setValue(record['importance'])
        source=record['source']
        name={'user_message':'你的聊天陳述','manual':'手動新增','edited':'手動修正'}[source['kind']]
        lines=[f"建立：{local_date(record['created_at'])}（本機時間）",f"更新：{local_date(record['updated_at'])}（本機時間）",f"來源：{name}"]
        if source.get('turn_number'):lines.append(f"來源輪次：第 {source['turn_number']} 輪")
        lines.append('來源原話：'+source['quote'])
        if record.get('original_source'):lines.append('修正前來源：'+record['original_source']['quote'])
        self.source.setPlainText('\n'.join(lines))

    def new(self,*args):
        self.current_id=None;self.items.setCurrentRow(-1)
        self.content.clear();self.source.clear();self.category.setCurrentIndex(0);self.importance.setValue(3)

    def save(self,*args):
        try:
            item_id=self.store.upsert(self.content.toPlainText(),self.category.currentData(),self.importance.value(),self.current_id)
            self.current_id=item_id;self.refresh()
            self.status.setText('已保存，下一則聊天會依話題參考這則記憶。');self.changed.emit()
        except MemoryFailure as error:self.status.setText(str(error))

    def delete(self,*args):
        if not self.current_id:
            self.status.setText('請先選擇要刪除的記憶。');return
        try:
            self.store.delete(self.current_id);self.current_id=None;self.refresh()
            self.status.setText('已刪除長期記憶；原始聊天紀錄仍保留。');self.changed.emit()
        except MemoryFailure as error:self.status.setText(str(error))
