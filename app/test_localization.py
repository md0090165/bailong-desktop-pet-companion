import json
import os
import unittest
from pathlib import Path

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
from PySide6.QtCore import QTranslator
from PySide6.QtWidgets import QApplication, QLabel
from pet import Bubble, ASSETS
from quota import quota_windows, classify_error

app = QApplication.instance() or QApplication([])

class LocalizationTests(unittest.TestCase):
    def test_bundled_standard_dialog_translation(self):
        translator = QTranslator()
        self.assertTrue(translator.load(str(Path(__file__).parent/'translations/qtbase_zh_TW.qm')))
        self.assertFalse(translator.isEmpty())

    def test_manifest_display_names_and_dialogue_are_traditional(self):
        manifest = json.loads((ASSETS/'素材清单.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['character'], '白龍娘')
        self.assertEqual(len(manifest['variants']), 10)
        for item in manifest['variants']:
            self.assertTrue((ASSETS/item['playbackFile']).is_file())
            for word in ['主人来','龙','庆祝','灵感','额度','贴贴']:
                self.assertNotIn(word, item['name']+' '+item['line'])

    def test_quota_text_and_unknown_duration(self):
        windows = quota_windows({'rateLimits': {'primary': {'usedPercent': 100, 'windowDurationMins': 300}, 'secondary': {'usedPercent': 0, 'windowDurationMins': 60}}})
        self.assertEqual([w['name'] for w in windows], ['5小時', '60分鐘'])
        bubble = Bubble()
        try:
            bubble.quota(windows)
            text = '\n'.join(label.text() for label in bubble.findChildren(QLabel))
            self.assertIn('Codex 剩餘額度', text)
            self.assertIn('剩餘 0%', text)
            self.assertNotIn('剩余', text)
        finally:
            bubble.close()

    def test_error_messages_are_localized(self):
        self.assertIn('登入', str(classify_error('401 unauthorized')))
        self.assertIn('網路連線', str(classify_error('connection timeout')))

if __name__ == '__main__':
    unittest.main()
