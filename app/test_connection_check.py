import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import time
import unittest
from unittest.mock import Mock
from PySide6.QtWidgets import QApplication
from chat_connection import ConnectionFailure
from connection_check import ConnectionCheck


class PanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.connection = Mock()
        self.connection.accounts.return_value = []
        self.panel = ConnectionCheck(self.connection)

    def tearDown(self):
        self.panel.close()
        self.panel.deleteLater()
        self.app.processEvents()

    def wait_finished(self):
        deadline = time.monotonic() + 3
        while self.panel.worker and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.01)
        self.assertIsNone(self.panel.worker)

    def test_open_never_sends_or_logs_in(self):
        self.connection.login.assert_not_called()
        self.connection.reply.assert_not_called()
        self.connection.models.assert_not_called()

    def test_select_model_and_send_succeeds(self):
        self.connection.reply.side_effect = lambda *a, **k: (k['on_delta']('你好'), '你好')[1]
        self.panel.models_ready([{'id': 'available', 'name': '可用模型'}])
        self.panel.test_reply()
        self.assertFalse(self.panel.send.isEnabled())
        self.wait_finished()
        self.assertEqual(self.panel.output.toPlainText(), '你好')
        self.assertIn('驗收成功', self.panel.status.text())
        self.assertTrue(self.panel.send.isEnabled())

    def test_failure_reenables_controls(self):
        self.connection.models.side_effect = ConnectionFailure('network', '網路失敗')
        self.panel.load_models.click()
        self.wait_finished()
        self.assertEqual(self.panel.status.text(), '網路失敗')
        self.assertTrue(self.panel.load_models.isEnabled())

    def test_close_cancels_and_waits_without_destroying_running_thread(self):
        self.panel.show()
        def operation(worker):
            worker.cancel.wait(2)
            raise ConnectionFailure('cancelled', '操作已取消。')
        self.panel.start(operation, lambda value: None)
        self.panel.close()
        self.assertTrue(self.panel.worker.cancel.is_set())
        self.wait_finished()
        self.app.processEvents()
        self.assertFalse(self.panel.isVisible())


if __name__ == '__main__':
    unittest.main()
