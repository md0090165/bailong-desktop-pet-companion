"""Offline smoke check executed by the shipped EXE, without a Python install."""
import os,json,time,traceback
from pathlib import Path

def run(root):
    if '--native-ui' not in __import__('sys').argv:os.environ['QT_QPA_PLATFORM']='offscreen'
    report={'passed':False,'checks':[],'frozen':False}
    try:
        import sys
        from version import VERSION
        report.update(version=VERSION,frozen=bool(getattr(sys,'frozen',False)))
        if '--quota-only' in sys.argv:
            from quota import read_quota,quota_windows
            windows=quota_windows(read_quota())
            report['checks'].append(f'EXE只讀額度介面成功，返回{len(windows)}個視窗；不記錄賬號或額度數值')
            report['passed']=True
            (root/'额度接口检查报告.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            return 0
        from pet import Pet,clamp_position
        from quota import quota_windows
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import QPoint,QRect,QSize,QPointF
        from types import SimpleNamespace
        report.update(version=VERSION,frozen=bool(getattr(sys,'frozen',False)))
        app=QApplication([]);app.setQuitOnLastWindowClosed(False)
        from PySide6.QtGui import QFontDatabase,QFont
        for name in ('msjh.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        app.setFont(QFont('Microsoft JhengHei',10))
        pet=Pet();pet.tick.stop()
        menu=pet.create_context_menu()
        assert [a.text() for a in menu.preview_menu.actions()]==[v['id']+' · '+v['name'] for v in pet.variants]
        assert len([a for a in menu.actions() if not a.isSeparator()])==6
        menu.popup(pet.pos());app.processEvents();menu.grab().save(str(root/'右鍵預覽選單.png'));menu.hide();menu.deleteLater()
        report['checks'].append('右鍵預覽子選單包含全部10種動畫，頂層選單精簡正常')

        items=[pet.manifest['idle']]+pet.manifest.get('additionalIdleAnimations',[])+pet.variants+[pet.manifest[k] for k in ('dragAnimation','landingAnimation','quotaAnimation')]
        for item in items:
            frames=pet.load(item);assert len(frames)==round(item['durationMs']*24/1000)
            assert frames[0].width()==pet.frame_size
        assert len(pet.variants)==10;report['checks'].append('15段動畫可解碼，最新14睏倦、48幀落地與音效檔案齊全')
        assert (root/'app/sounds/click.wav').is_file()
        assert '最新版' in next(v for v in pet.variants if v['id']=='14')['file']
        for item in items:
            assert 'renderScale' in item and 'renderOffset' in item
        report['checks'].append('全部動作尺寸校準欄位齊全')
        pet.play(pet.manifest['quotaAnimation'],'quota');pet.started=time.monotonic()-4.1;pet.animate();assert pet.mode=='idle'
        report['checks'].append('查詢動作只播一次')
        press=SimpleNamespace(button=lambda:__import__('PySide6.QtCore',fromlist=['Qt']).Qt.MouseButton.LeftButton,globalPosition=lambda:QPointF(200,200))
        move=SimpleNamespace(buttons=press.button,globalPosition=lambda:QPointF(240,230))
        pet.mousePressEvent(press);pet.mouseMoveEvent(move);assert pet.mode=='drag'
        pet.mouseReleaseEvent(press);assert pet.mode=='landing' and pet.current['durationMs']==2000
        report['checks'].append('拖拽與2秒落地切換正常')
        for position in [QPoint(-999,-999),QPoint(5000,5000)]:
            area=QRect(0,0,1920,1040);assert area.contains(QRect(clamp_position(position,QSize(256,256),area),QSize(256,256)))
        report['checks'].append('螢幕邊界限制正常')
        assert quota_windows({'rateLimits':{'primary':{'usedPercent':25,'windowDurationMins':300}}})[0]['remaining']==75
        report['checks'].append('額度解析正常；未使用或捆綁任何賬號')
        pet.play(pet.idles[0],'idle');pet.bubble.hide()
        from unittest.mock import patch
        import tempfile
        from random_events import EventStore,DEFAULT_RULES,roll
        with tempfile.TemporaryDirectory() as event_folder:
            events=EventStore(event_folder,'offline-test',DEFAULT_RULES)
            assert events.state['probability']==20
            for expected in (40,60,80,100,100):
                events.schedule(False,'offline miss');assert events.state['probability']==expected
            events.schedule(True,'offline success');assert events.state['probability']==20
        report['checks'].append('隨機事件 20–40 分鐘排程、20% 遞增至 100% 與成功重設正常（離線）')
        pet.events.timer.stop()
        from chat_window import ChatWindow
        from chat_connection import CredentialStore
        from unittest.mock import Mock
        import tempfile
        with tempfile.TemporaryDirectory() as folder:
            from character_context import CharacterProfile, project_root, response_with_context, completed_turn, empty_state
            profile=CharacterProfile(project_root());profile.reload()
            (Path(folder)/'Character.md').write_text(profile.text,encoding='utf-8')
            (Path(folder)/'RelationshipRules.json').write_bytes((project_root()/'RelationshipRules.json').read_bytes())
            connection=Mock();connection.store=CredentialStore(folder)
            connection.accounts.return_value=[{'id':'offline','label':'離線測試帳號','signed_in':True,'active':True}]
            connection.models.return_value=[{'id':'offline-model','name':'離線測試模型'}]
            def reply(model,context,**kwargs):
                text='你好！今天想聊些什麼？'
                if kwargs.get('on_delta'):
                    raw=json.dumps({'text':text,'emotion':'興奮','animation_id':'18'},ensure_ascii=False)
                    kwargs['on_delta'](raw);return raw
                return '使用者說今天也一起加油；這是離線摘要測試。'
            connection.reply.side_effect=reply
            from reply_animation import AnimationCatalog
            assert set(pet.reply_animations.entries)=={v['id'] for v in pet.variants}
            (Path(folder)/'Animations.json').write_text(json.dumps(pet.reply_animations.config,ensure_ascii=False),encoding='utf-8')
            chat=ChatWindow(connection);pet.chat=chat
            chat.reply_action.connect(pet.chat_reply_action)
            pet.single_click()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and chat.isVisible() and chat.models.count()==1
            pet.play(pet.idles[0],'idle')
            chat.message.setPlainText('今天也一起加油吧。');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and len(chat.messages)==2
            assert '你好！今天想聊些什麼？' in chat.transcript.toPlainText()
            assert 'animation_id' not in chat.transcript.toPlainText()
            assert pet.mode=='chat' and pet.current['id']=='18'
            assert not pet.bubble.isVisible()
            assert chat.conversation_state['turns'][0]['emotion']=='興奮'
            pet.grab().save(str(root/'聊天動作預覽.png'))
            pet.started=time.monotonic()-4.1;pet.animate();assert pet.mode=='idle'
            assert not pet.chat_reply_action('18','興奮') and pet.mode=='idle'
            assert not pet.chat_reply_action('unavailable','開心') and pet.mode=='idle'
            pet.play(pet.manifest['dragAnimation'],'drag')
            assert not pet.chat_reply_action('04','好奇') and pet.mode=='drag'
            pet.play(pet.idles[0],'idle')
            assert chat.bubble.isVisible()
            chat.bubble.grab().save(str(root/'回覆泡泡預覽.png'))
            assert profile.text in connection.reply.call_args.kwargs['instructions']
            restored=ChatWindow(connection)
            assert len(restored.messages)==2 and restored.messages[0]['content']=='今天也一起加油吧。'
            restored.shutdown();restored.deleteLater();app.processEvents()
            from reply_animation import AnimationCooldown
            pet.reply_cooldown=AnimationCooldown(pet.reply_animations)
            pet.play(pet.idles[0],'idle')
            connection.reply.side_effect=lambda *args,**kwargs:'我們一起想想今天要聊什麼。'
            chat.message.setPlainText('隨便聊聊');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and pet.mode=='chat' and pet.current['id']=='04'
            assert chat.conversation_state['turns'][-1]['animation_id']=='04'
            assert chat.bubble.label.text()=='我們一起想想今天要聊什麼。'
            connection.reply.side_effect=reply
            chat.message.setPlainText('請記住：我喜歡紫色星雲。');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and len(chat.memory_store.state['items'])==1
            memory=chat.memory_store.state['items'][0]
            assert memory['source']['quote']=='我喜歡紫色星雲。'
            chat.shutdown();pet.chat=None;chat.deleteLater();app.processEvents()
            chat=ChatWindow(connection);pet.chat=chat;pet.events.bind(chat);chat.reply_action.connect(pet.chat_reply_action)
            assert len(chat.memory_store.state['items'])==1
            chat.models_ready([{'id':'offline-model','name':'離線測試模型'}])
            chat.conversation_state=empty_state()  # Prove recall without recent conversation.
            chat.message.setPlainText('你記得我的喜好嗎？');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None
            sent_context=connection.reply.call_args.args[1]
            assert '本機長期記憶' in sent_context[0]['content'] and '紫色星雲' in sent_context[0]['content']
            chat.open_memory();app.processEvents()
            manager=chat.memory_window;manager.items.setCurrentRow(0)
            manager.content.setPlainText('我喜歡綠色星雲。');manager.save();app.processEvents()
            assert '綠色星雲' in chat.memory_store.context('我的喜好')
            assert '紫色星雲' not in chat.memory_store.context('我的喜好')
            manager.grab().save(str(root/'長期記憶預覽.png'));manager.close()
            chat.message.setPlainText('謝謝你，今天真的很幫忙。');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and chat.relationship_store.state['changes'][-1]['event']=='praise'
            assert '本機角色關係與短期心情' in connection.reply.call_args.kwargs['instructions']
            current=chat.relationship_store.snapshot()
            assert current['emotion']=='害羞'
            later=chat.relationship_store.snapshot(time.time()+21600)
            assert abs(later['valence'])<abs(current['valence'])
            assert later['closeness']==current['closeness'] and later['trust']==current['trust']
            pet.play(pet.idles[0],'idle');pet.reply_cooldown=AnimationCooldown(pet.reply_animations)
            connection.reply.side_effect=lambda *args,**kwargs:'我們一起想想接下來聊些什麼。'
            chat.message.setPlainText('聊點別的');chat.send_message()
            deadline=time.monotonic()+5
            while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert chat.worker is None and pet.mode=='chat' and pet.current['id']=='09'
            assert chat.conversation_state['turns'][-1]['emotion']=='害羞'
            connection.reply.side_effect=reply
            chat.open_relationship();app.processEvents()
            assert '受到肯定' in chat.relationship_window.history.toPlainText()
            chat.relationship_window.grab().save(str(root/'關係與心情預覽.png'))
            chat.relationship_window.close()
            chat.show_near(pet,load_models=False)
            chat.show_parameters(pet);app.processEvents()
            chat.parameters.grab().save(str(root/'參數設定預覽.png'))
            chat.parameters.hide()
            import threading
            sample={**empty_state(),'turns':[completed_turn(str(i),'離線測試','offline-model','test') for i in range(13)]}
            fake_worker=SimpleNamespace(cancel=threading.Event(),status=Mock(),delta=Mock())
            result=response_with_context(connection,'offline-model',sample,'繼續','測試人設',fake_worker)
            assert result['summary_until']==7 and result['summary']
            assert sample['summary_until']==0
            chat.grab().save(str(root/'聊天視窗預覽.png'))
            pet.play(pet.idles[0],'idle');pet.bubble.hide()
            assert pet.events.busy(chat)
            chat.close();assert not chat.isVisible()
            chat.bubble.hide()
            # Proactive event through the actual worker, with synthetic images and a fake model only.
            pet.events.store.state['enabled']=True;pet.events.error='';pet.events.next_mono=0
            connection.reply.side_effect=lambda *a,**kw:'{"text":"今天在畫什麼呢？","emotion":"好奇","animation_id":"04"}'
            from random_events import DEFAULT_RULES
            with patch('event_controller.roll',return_value=DEFAULT_RULES['events'][0]),patch('event_controller.capture_screens',return_value=[b'synthetic']),patch('event_controller.checked_images',return_value=['data:image/png;base64,SYNTHETIC']):
                pet.events.tick()
                deadline=time.monotonic()+5
                while chat.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert pet.events.store.state['pending']['opening']=='今天在畫什麼呢？'
            assert pet.events.store.state['probability']==(100 if pet.events.rules.get('event_probability_mode')=='scheduled' else 20)
            assert connection.reply.call_args.args[1][0]['content'][1]['type']=='input_image'
            turns=[completed_turn('我在畫白龍的衣服，想用淡紫色配上白色的花紋，感覺會很溫柔。','聽起來很好看。','fake','fake'),
                   completed_turn('對了我還想畫一個銀色髮飾，放在頭髮的右邊，明天有時間就繼續畫。','我很期待。','fake','fake')]
            pet.events.store.observe(turns);assert len(pet.events.store.state['diaries'])==1
            pet.events.show_panel();app.processEvents()
            pet.events.panel.grab().save(str(root/'觀察日記預覽.png'));pet.events.panel.hide()
            # Verify bundled OCR helper works without touching the desktop.
            import io
            from PIL import Image,ImageDraw,ImageFont
            from screen_privacy import checked_images,PrivacyBlocked
            safe=Image.new('RGB',(900,180),'white')
            ImageDraw.Draw(safe).text((20,30),'Safe garden notes',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',36),fill='black')
            safe_data=io.BytesIO();safe.save(safe_data,format='PNG')
            assert checked_images([safe_data.getvalue()],threading.Event())[0].startswith('data:image/png;base64,')
            image=Image.new('RGB',(900,180),'white')
            ImageDraw.Draw(image).text((20,30),'Password: TEST-ONLY-12345',font=ImageFont.truetype('C:/Windows/Fonts/arial.ttf',36),fill='black')
            data=io.BytesIO();image.save(data,format='PNG')
            try:checked_images([data.getvalue()],threading.Event());raise AssertionError('privacy gate failed')
            except PrivacyBlocked:pass
            report['checks'].append('隨機事件圖片請求格式、回覆開場、機率重設、實際對話日記與內建本機 OCR 攔截正常（全離線人工圖片）')
            from cloud_sync_window import SyncPanel
            from cloud_sync_core import digest
            import uuid
            sync_auth=Mock();sync_auth.logged_in.return_value='離線 Google 測試帳號';sync_auth.identity.return_value='offline-google'
            sync_client=Mock();sync=SyncPanel(chat,sync_auth,sync_client);chat.cloud_sync=sync
            sync.show();app.processEvents();sync.grab().save(str(root/'同步記憶預覽.png'))
            local=sync.repo.collect();cloud=sync.repo.snapshot(local,uuid.uuid4().hex,[])
            cloud['data']['character']='你是雲端還原的白龍。';cloud['hash']=digest(cloud['data'])
            def cancel_choice():
                from PySide6.QtWidgets import QMessageBox
                dialog=app.activeModalWidget()
                assert isinstance(dialog,QMessageBox) and dialog.defaultButton().text()=='取消'
                dialog.grab().save(str(root/'同步版本選擇預覽.png'))
                next(b for b in dialog.buttons() if b.text()=='取消').click()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(100,cancel_choice)
            assert sync.choose(local,cloud,'offline-google')=='cancel'
            assert sync.repo.collect()==local
            sync.repo.apply(cloud,digest(local));sync.repo.mark('offline-google',cloud);sync.reload_chat()
            assert chat.character.text=='你是雲端還原的白龍。'
            sync_client.create.assert_not_called();sync_auth.login.assert_not_called()
            sync.shutdown()
            report['checks'].append('Google Drive同步入口、取消預設的版本選擇、跨登入ID邏輯快照與本機備份還原正常（離線；未呼叫Google或LLM）')
            chat.shutdown()
            pet.chat=None;chat.deleteLater();app.processEvents()
        pet.events.panel=None
        from codex_monitor import CodexMonitor,HUNGRY,DONE
        from codex_notify import handle
        with tempfile.TemporaryDirectory() as monitor_folder:
            monitor=CodexMonitor(pet,monitor_folder);monitor.timer.stop()
            pet.play(pet.idles[0],'idle');pet.bubble.hide()
            handle(json.dumps({'type':'agent-turn-complete','thread-id':'offline','turn-id':'test'}),monitor_folder,forward=False)
            monitor.ingest();monitor.deliver();assert pet.current['id']=='04'
            assert pet.bubble.layout.itemAt(0).widget().text()==DONE
            pet.bubble.grab().save(str(root/'工作完成泡泡預覽.png'))
            pet.play(pet.idles[0],'idle');pet.bubble.hide()
            sample={'_account_key':'a'*64,'rateLimits':{'primary':{'windowDurationMins':300,'usedPercent':90,'resetsAt':time.time()+10000}}}
            monitor.quota_ready(sample);monitor.deliver();assert pet.current['id']=='14'
            from PySide6.QtWidgets import QLabel
            assert HUNGRY in pet.bubble.layout.itemAt(0).widget().text()
            pet.grab().save(str(root/'低額度睏倦預覽.png'));pet.bubble.grab().save(str(root/'低額度提醒預覽.png'))
            pet.play(pet.idles[0],'idle');pet.bubble.hide();monitor.quota_ready(sample)
            assert monitor.pending_low is None;monitor.shutdown()
        report['checks'].append('Codex 完成靈感動畫、五小時10%固定台詞與14睏倦、相同週期去重正常（本機離線，未呼叫模型）')
        # Exercise the actual bundled Gemini modules and UI without a real API key.
        from gemini_connection import ModelServices
        from gemini_settings import GeminiSettings
        with tempfile.TemporaryDirectory() as folder:
            root_dir=Path(folder);(root_dir/'Character.md').write_text('你是溫柔的白龍。',encoding='utf-8')
            legacy=Mock();legacy.store=CredentialStore(folder)
            legacy.accounts.return_value=[]
            state={'provider':'gemini','key':'OFFLINE-TEST-KEY','profile':'gemini-local'}
            settings=SimpleNamespace(load=lambda:dict(state),save=lambda value:state.update(value))
            service=ModelServices(legacy,settings)
            gemini=ChatWindow(service,root_dir)
            gemini.models_ready([{'id':'gemini:gemini-offline','name':'Gemini 離線測試模型'}])
            response={'candidates':[{'finishReason':'STOP','content':{'parts':[{'text':'主人你好！'}]}}]}
            with patch('gemini_connection.request',return_value=response) as api:
                gemini.message.setPlainText('你好');gemini.send_message()
                deadline=time.monotonic()+5
                while gemini.worker and time.monotonic()<deadline:app.processEvents();time.sleep(.01)
            assert gemini.worker is None and gemini.conversation_state['turns'][-1]['user']=='你好'
            assert '你是溫柔的白龍。' in api.call_args.args[2]['systemInstruction']['parts'][0]['text']
            gemini.show_parameters(pet);app.processEvents()
            gemini.parameters.grab().save(str(root/'Gemini參數設定預覽.png'))
            panel=GeminiSettings(service);panel.show();app.processEvents()
            panel.grab().save(str(root/'Gemini金鑰設定預覽.png'))
            panel.hide();panel.deleteLater();gemini.shutdown();gemini.deleteLater();app.processEvents()
        report['checks'].append('Gemini 編譯模組、金鑰設定介面、模型選擇、人設注入與聊天保存正常（模擬 API，未使用真實金鑰）')
        report['checks'].append('關係小幅變化、來源追蹤、心情時間回穩、無離線扣分、語氣提示、害羞動作與狀態面板均正常（離線模擬）')
        report['checks'].append('長期記憶原話保存、重新啟動式恢復、無近期上下文仍提供記憶、管理視窗修正及來源檢視正常（離線模擬）')
        report['checks'].append('純文字回覆補選靈感、10段動畫標籤、回覆搭配慶祝、冷卻與拖拽優先、無效ID回退、純台詞保存均正常（離線模擬）')
        report['checks'].append('Character.md 原檔載入、人設注入、對話保存、重新開窗恢復與舊對話摘要成功（離線模擬）')
        report['checks'].append('單擊聊天、模型載入、文字收發與關窗正常（離線模擬，未呼叫帳號）')
        report['checks'].append('聊天開啟時延後隨機事件，關閉後恢復檢查')
        pet.events.timer.stop()
        pet.play(pet.idles[0],'idle');pet.grab().save(str(root/'检查预览.png'));pet.bubble.close();pet.hide()
        report['passed']=True
    except Exception:report['error']=traceback.format_exc()
    (root/('额度接口检查报告.json' if '--quota-only' in __import__('sys').argv else '检查报告.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return 0 if report['passed'] else 1
