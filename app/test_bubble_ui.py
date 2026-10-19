import sys
import unittest
from pathlib import Path; sys.path.insert(0, str(Path(__file__).parent))
from test_chat_window import ChatWindowTests
from chat_window import reply_duration
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

class BubbleTests(ChatWindowTests):
    def test_minimum_duration_and_expiry(self):
        self.assertEqual(reply_duration('嗨'),1500)
        self.assertEqual(reply_duration('好'*80),11000)
        self.window.bubble.present('嗨',self.window)
        QTest.qWait(1000)
        self.assertTrue(self.window.bubble.isVisible())
        QTest.qWait(700)
        self.assertFalse(self.window.bubble.isVisible())

    def test_only_textbox_and_settings_separate(self):
        self.assertTrue(self.window.message.isVisible())
        for widget in (self.window.account,self.window.models,self.window.transcript,self.window.send,self.window.clear):
            self.assertFalse(widget.isVisible())
        self.window.models_loaded_for=self.window.account.currentData()
        self.window.hide()
        self.window.show_parameters(self.window)
        self.assertFalse(self.window.isVisible())
        self.assertTrue(self.window.parameters.isVisible())
        self.assertTrue(self.window.account.isVisible())

    def test_long_plaintext_and_screen_bounds(self):
        text='<b>不要當作 HTML</b>'+'很長的內容。'*300
        self.window.bubble.present(text,self.window)
        self.app.processEvents()
        self.assertEqual(self.window.bubble.label.textFormat(),Qt.TextFormat.PlainText)
        self.assertTrue(self.window.screen().availableGeometry().contains(self.window.bubble.frameGeometry()))
        self.assertGreater(self.window.bubble.scroll.verticalScrollBar().maximum(),0)

    def test_escape_hides_input(self):
        QTest.keyClick(self.window.message,Qt.Key.Key_Escape)
        self.assertFalse(self.window.isVisible())

if __name__=='__main__':unittest.main()
