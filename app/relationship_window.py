"""Read-only relationship state and per-interaction change history."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget,QVBoxLayout,QLabel,QProgressBar,QPlainTextEdit,QPushButton
from memory_window import local_date


class RelationshipPanel(QWidget):
    def __init__(self,store,parent=None):
        super().__init__(parent,Qt.WindowType.Window|Qt.WindowType.WindowStaysOnTopHint)
        self.store=store
        self.setWindowTitle('白龍桌寵 · 關係與心情')
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.resize(650,620)
        self.setStyleSheet('QWidget {font-family:"Microsoft JhengHei";font-size:14px;color:#352c44;background:#faf7fd;} QPlainTextEdit {background:white;border:1px solid #d7cbe2;border-radius:6px;padding:7px;} QProgressBar {border:1px solid #d7cbe2;border-radius:6px;background:#eee7f5;text-align:center;height:18px;} QProgressBar::chunk {background:#c5a9dd;} QPushButton {background:#eee4f6;border:1px solid #d7cbe2;border-radius:6px;padding:7px;}')
        layout=QVBoxLayout(self)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        self.bars={}
        for key,label,low,high in (('closeness','親密度',0,100),('trust','信任度',0,100),
                                    ('valence','短期愉悅',-100,100),('energy','短期活力',0,100)):
            layout.addWidget(QLabel(label))
            bar=QProgressBar();bar.setRange(low,high);self.bars[key]=bar;layout.addWidget(bar)
        info=QLabel('親密與信任緩慢累積；短期心情隨時間回穩。這些是角色演出狀態，離線不會扣親密或信任。')
        info.setWordWrap(True);layout.addWidget(info)
        self.limits=QLabel();self.limits.setWordWrap(True);layout.addWidget(self.limits)
        layout.addWidget(QLabel('最近互動變化（新到舊，最多 100 輪）'))
        self.history=QPlainTextEdit();self.history.setReadOnly(True);layout.addWidget(self.history,1)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        refresh=QPushButton('更新顯示');refresh.clicked.connect(self.refresh);layout.addWidget(refresh)
        self.refresh()

    def refresh(self,*args):
        view=self.store.snapshot()
        if not view:
            self.summary.setText('關係資料暫時無法讀取。')
            self.status.setText('已保留原檔，請檢查 Relationship 資料夾後重新啟動。')
            return
        self.summary.setText(f"相處狀態：{view['level']} · 心情：{view['mood_label']}")
        for key,bar in self.bars.items():
            bar.setValue(round(view[key]))
            bar.setFormat(f"{view[key]:+.1f}" if key=='valence' else f"{view[key]:.1f} / 100")
        rules=self.store.rules.data
        self.limits.setText(f"每日累積上限：親密 +{rules['daily_gain_cap']['closeness']:g}、信任 +{rules['daily_gain_cap']['trust']:g}；成長間隔 {rules['growth_interval_seconds']:g} 秒；心情偏移約每 {rules['mood_half_life_minutes']:g} 分鐘減半。")
        lines=[]
        for change in reversed(self.store.state['changes']):
            before,after=change['before'],change['after'];source=change['source']
            lines.append(f"{local_date(change['at'])} · {change.get('label',change['event'])} · 第 {source['turn_number']} 輪")
            lines.append(f"親密 {before['closeness']:.2f} → {after['closeness']:.2f}；信任 {before['trust']:.2f} → {after['trust']:.2f}")
            lines.append(f"短期愉悅 {before['valence']:+.1f} → {after['valence']:+.1f}；活力 {before['energy']:.1f} → {after['energy']:.1f}")
            lines.extend(['原因：'+change['reason'],'來源原話：'+source['quote'],''])
        text='\n'.join(lines) if lines else '尚無本階段的互動變化。成功收到回覆後才會更新。'
        if self.history.toPlainText()!=text:self.history.setPlainText(text)
        self.status.setText(self.store.warning or self.store.rules.warning or '顯示為本機計算的目前狀態；檢視與時間回穩不會呼叫模型。')
