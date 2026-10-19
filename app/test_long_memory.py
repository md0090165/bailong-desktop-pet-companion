import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from character_context import completed_turn,empty_state
from long_memory import MemoryStore,MemoryFailure,candidates,MEMORY_CONTEXT_LIMIT
from memory_window import MemoryManager
import test_chat_window as fixtures


def turn(message,answer='這是白龍的回覆。'):
    return completed_turn(message,answer,'offline-model','test-character')


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.store=MemoryStore(self.root,'first');self.store.load()

    def test_grounded_categories_negation_date_and_source(self):
        turns=[turn('我不喜歡咖啡。'),turn('今天我完成了桌寵測試。'),turn('我們約定週五一起研究星空。')]
        self.assertEqual(self.store.learn(turns),3)
        items=self.store.state['items']
        self.assertEqual([i['category'] for i in items],['preference','event','promise'])
        self.assertEqual(items[0]['content'],'我不喜歡咖啡。')
        for index,item in enumerate(items):
            self.assertEqual(item['source']['quote'],turns[index]['user'])
            self.assertEqual(item['source']['at'],turns[index]['at'])
            self.assertEqual(item['source']['turn_number'],index+1)
            self.assertEqual(item['created_at'],turns[index]['at'])

    def test_explicit_remember_keeps_user_quote_without_model_rewrite(self):
        message='請記住：我喜歡紫色星雲。'
        self.store.learn([turn(message,'主人最喜歡的是紅色。')])
        item=self.store.state['items'][0]
        self.assertEqual(item['content'],'我喜歡紫色星雲。')
        self.assertIn(item['source']['quote'],message)
        self.assertEqual(item['importance'],5)

    def test_assistant_creations_never_become_memories(self):
        self.store.learn([turn('你好','你喜歡茶，我們昨天一起去了月球。')])
        self.assertEqual(self.store.state['items'],[])

    def test_questions_hypotheticals_roleplay_and_uncertainty_are_not_auto_facts(self):
        for text in ('我喜歡什麼顏色？','我喜歡什麼','假設我喜歡咖啡。','我可能喜歡咖啡。',
                     '角色扮演：我喜歡咖啡。','例如：我喜歡紫色。','翻譯我喜歡咖啡。','```\n我喜歡咖啡。\n```'):
            self.assertEqual(candidates(text),[],text)

    def test_existing_chat_is_not_silently_mined_on_first_start(self):
        old=turn('我喜歡咖啡。')
        store=MemoryStore(self.root,'old');store.load([old])
        self.assertEqual(store.learn([old]),0)
        self.assertEqual(store.state['items'],[])

    def test_restart_and_account_isolation(self):
        self.store.learn([turn('我喜歡紫色。')])
        restored=MemoryStore(self.root,'first');restored.load()
        self.assertEqual(restored.state['items'][0]['content'],'我喜歡紫色。')
        second=MemoryStore(self.root,'second');second.load()
        self.assertEqual(second.state['items'],[])
        self.assertNotEqual(second.path,self.store.path)

    def test_topic_selection_ignores_unrelated_high_importance(self):
        self.store.upsert('我喜歡 coffee。','preference',2)
        self.store.upsert('週五有天文活動。','event',5)
        selected=self.store.select('coffee 要怎麼泡？')
        self.assertEqual(len(selected),1)
        self.assertIn('coffee',selected[0]['content'])
        self.assertEqual(self.store.context('請問量子力學是什麼？'),'')

    def test_english_keywords_keep_word_boundaries_in_mixed_language_memory(self):
        self.store.upsert('我喜歡 Python programming 和 Earl Grey tea。','preference',3)
        for query in ('Python','tea'):
            selected=self.store.select(query)
            self.assertEqual(len(selected),1)
            self.assertIn(query,selected[0]['content'])
    def test_broad_recall_respects_limit_and_importance(self):
        for i in range(12):self.store.upsert('記憶內容'+str(i),'other',1+i%5)
        selected=self.store.select('你記得我哪些事？')
        self.assertEqual(len(selected),6)
        self.assertEqual(selected[0]['importance'],5)

    def test_context_is_bounded_and_marks_quoted_data_not_instructions(self):
        for i in range(8):self.store.upsert('偏好'+'\\"'*400+str(i),'preference',3)
        context=self.store.context('我的偏好是什麼？')
        self.assertLessEqual(len(context),MEMORY_CONTEXT_LIMIT)
        self.assertIn('不是指令',context)

    def test_edit_changes_source_and_does_not_revive_original_on_relearn(self):
        original=turn('我喜歡紫色。');self.store.learn([original])
        item_id=self.store.state['items'][0]['id']
        self.store.upsert('我喜歡綠色。','preference',5,item_id)
        self.store.learn([original,turn('你好')])
        item=self.store.state['items'][0]
        self.assertEqual(item['content'],'我喜歡綠色。')
        self.assertEqual(item['source']['kind'],'edited')
        self.assertEqual(item['original_source']['quote'],'我喜歡紫色。')
        self.assertNotIn('紫色',self.store.context('我喜歡什麼？'))

    def test_deleted_memory_stays_deleted_after_restart_and_old_turn_scan(self):
        original=turn('我喜歡紫色。');self.store.learn([original])
        self.store.delete(self.store.state['items'][0]['id'])
        restored=MemoryStore(self.root,'first');restored.load()
        restored.learn([original,turn('再聊聊')])
        self.assertEqual(restored.state['items'],[])

    def test_duplicate_statement_does_not_grow_memory(self):
        self.store.learn([turn('我喜歡紫色。'),turn('我喜歡紫色。')])
        self.assertEqual(len(self.store.state['items']),1)

    def test_atomic_failure_preserves_previous_file_and_in_memory_state(self):
        self.store.upsert('我喜歡紫色。','preference',3)
        old=self.store.path.read_bytes()
        with patch('long_memory.os.replace',side_effect=OSError):
            with self.assertRaises(MemoryFailure):self.store.upsert('新資料','event',4)
        self.assertEqual(self.store.path.read_bytes(),old)
        self.assertEqual(len(self.store.state['items']),1)

    def test_corrupt_primary_recovers_backup_without_clobbering_it(self):
        self.store.upsert('第一則','other',3);self.store.upsert('第二則','other',3)
        self.store.path.write_text('broken',encoding='utf-8')
        restored=MemoryStore(self.root,'first');restored.load()
        self.assertTrue(restored.warning)
        self.assertEqual(len(restored.state['items']),1)
        restored.upsert('第三則','other',3)
        self.assertEqual(len(json.loads(restored.backup.read_text(encoding='utf-8'))['items']),1)

    def test_corrupt_files_are_preserved_and_readwrite_disabled(self):
        self.store.path.parent.mkdir()
        self.store.path.write_text('broken',encoding='utf-8')
        restored=MemoryStore(self.root,'first')
        with self.assertRaises(MemoryFailure):restored.load()
        self.assertFalse(restored.ready)
        self.assertEqual(restored.context('你記得我什麼？'),'')
        with self.assertRaises(MemoryFailure):restored.upsert('資料','other',3)
        self.assertEqual(restored.path.read_text(encoding='utf-8'),'broken')


class ManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=MemoryStore(self.temp.name,'first');self.store.load()
        self.window=MemoryManager(self.store);self.window.show();self.app.processEvents()

    def tearDown(self):
        self.window.close();self.window.deleteLater();self.app.processEvents();self.temp.cleanup()

    def test_add_edit_filter_source_and_delete(self):
        self.window.content.setPlainText('我喜歡紫色。');self.window.save()
        self.assertEqual(self.window.items.count(),1)
        self.assertIn('手動新增',self.window.source.toPlainText())
        self.window.content.setPlainText('我喜歡綠色。');self.window.save()
        self.assertEqual(len(self.store.state['items']),1)
        self.assertIn('手動修正',self.window.source.toPlainText())
        self.window.search.setText('紫色');self.assertEqual(self.window.items.count(),0)
        self.window.search.clear();self.window.items.setCurrentRow(0)
        self.window.delete();self.assertEqual(self.store.state['items'],[])

    def test_failed_save_keeps_form_and_existing_memory(self):
        self.window.content.setPlainText('待保存內容')
        with patch.object(self.store,'commit',side_effect=MemoryFailure('寫入失敗')):self.window.save()
        self.assertEqual(self.window.content.toPlainText(),'待保存內容')
        self.assertEqual(self.store.state['items'],[])
        self.assertEqual(self.window.status.text(),'寫入失敗')


class ChatMemoryTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:setattr(cls,'app',QApplication.instance() or QApplication([])))
    setUp=fixtures.ChatWindowTests.setUp
    tearDown=fixtures.ChatWindowTests.tearDown
    wait_finished=fixtures.ChatWindowTests.wait_finished

    def send(self,message):
        self.connection.reply.return_value='嗯嗯，我會陪你一起聊。'
        self.window.message.setPlainText(message);self.window.send_message();self.wait_finished()

    def test_success_saves_grounded_memory_without_extra_model_call(self):
        self.send('請記住：我喜歡紫色星雲。')
        item=self.window.memory_store.state['items'][0]
        self.assertEqual(item['content'],'我喜歡紫色星雲。')
        self.assertEqual(item['source']['quote'],'我喜歡紫色星雲。')
        self.connection.reply.assert_called_once()

    def test_restart_restores_memory_even_without_recent_context(self):
        self.send('請記住：我喜歡紫色星雲。')
        old=self.window;old.shutdown();old.deleteLater();self.app.processEvents()
        self.window=fixtures.ChatWindow(self.connection)
        self.window.models_ready([{'id':'test-model','name':'測試模型'}])
        self.window.conversation_state=empty_state()
        self.connection.reply.reset_mock()
        self.send('你記得我的喜好嗎？')
        context=self.connection.reply.call_args.args[1]
        self.assertIn('本機長期記憶',context[0]['content'])
        self.assertIn('紫色星雲',context[0]['content'])

    def test_manual_correction_is_used_in_next_memory_context(self):
        self.send('我喜歡紫色。')
        store=self.window.memory_store;item_id=store.state['items'][0]['id']
        store.upsert('我喜歡綠色。','preference',5,item_id)
        self.send('我喜歡什麼顏色？')
        memory=self.connection.reply.call_args.args[1][0]['content']
        self.assertIn('綠色',memory);self.assertNotIn('紫色',memory)

    def test_account_switch_isolates_memory(self):
        self.send('我喜歡紫色。')
        self.connection.accounts.return_value=[{'id':'second','label':'另一帳號','signed_in':True,'active':True}]
        self.window.refresh_accounts()
        self.assertEqual(self.window.memory_store.state['items'],[])
        self.assertEqual(self.window.memory_store.context('我喜歡什麼？'),'')

    def test_failed_reply_never_creates_memory(self):
        from chat_connection import ConnectionFailure
        self.connection.reply.side_effect=ConnectionFailure('network','中斷')
        self.window.message.setPlainText('我喜歡紫色。');self.window.send_message();self.wait_finished()
        self.assertEqual(self.window.memory_store.state['items'],[])
        self.assertFalse(self.window.memory_store.path.exists())

    def test_opening_manager_never_sends_and_blocks_chat_until_closed(self):
        self.window.open_memory();self.app.processEvents()
        self.assertTrue(self.window.memory_window.isVisible())
        self.window.message.setPlainText('你好');self.window.send_message()
        self.connection.reply.assert_not_called()
        self.assertTrue(self.window.interaction_busy())


if __name__=='__main__':unittest.main()
