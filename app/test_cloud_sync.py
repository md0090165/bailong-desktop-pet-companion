import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
import copy,io,json,tempfile,threading,time,unittest,uuid
from pathlib import Path
from unittest.mock import Mock,patch
from urllib.error import HTTPError
from google_drive_sync import (SyncFailure,GoogleAuth,DriveClient,callback_values,request,SCOPE)
from cloud_sync_core import Repository,digest,summary,heads,validate_snapshot,recover
from character_context import ConversationStore,completed_turn,empty_state
from long_memory import MemoryStore
from cloud_sync_window import SyncPanel
from PySide6.QtWidgets import QApplication
import test_chat_window as fixtures

class FakeDrive:
    def __init__(self):self.files={};self.rows=[];self.creates=0
    def list(self,cancel=None):return copy.deepcopy(self.rows)
    def download(self,id,cancel=None):return copy.deepcopy(self.files[id])
    def create(self,snapshot,cancel=None):
        self.creates+=1;key='file_'+str(self.creates);self.files[key]=copy.deepcopy(snapshot)
        self.rows.append({'id':key,'createdTime':snapshot['updated_at'],'appProperties':{'bailongSync':'1','profile':snapshot['profile_id'],'revision':snapshot['revision'],'parents':','.join(snapshot['parents']),'updated':snapshot['updated_at']}})
        return {'id':key}

class CoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
        (self.root/'Character.md').write_text('你是白龍。',encoding='utf-8');self.repo=Repository(self.root,'account-A');self.client=FakeDrive();self.profile=uuid.uuid4().hex;self.google='google-hash'
    def fill(self,repo=None,text='請記住，我喜歡白色花朵。'):
        repo=repo or self.repo;store=ConversationStore(repo.root,repo.account)
        turns=[completed_turn(text,'我記住了。','fake','profile')];store.save({**empty_state(),'turns':turns})
        memory=MemoryStore(repo.root,repo.account);memory.load();memory.learn(turns)
    def snapshot(self):return self.repo.snapshot(self.repo.collect(),self.profile,[])
    def test_export_allowlist_excludes_credentials_and_screenshots(self):
        self.fill();(self.root/'Private').mkdir();(self.root/'Private/accounts.dpapi').write_bytes(b'SECRET')
        s=self.snapshot();self.assertNotIn('SECRET',json.dumps(s));self.assertEqual(set(s['data']),{'character','conversation','memory','relationship','diary'})
        self.assertIn('對話 1 輪',summary(s['data']))
    def test_same_character_moves_across_different_login_client_ids(self):
        self.fill();s=self.snapshot();other=self.root/'second';other.mkdir();(other/'Character.md').write_text('新白龍',encoding='utf-8')
        repo=Repository(other,'DIFFERENT-CLIENT-ID');repo.apply(s,digest(repo.collect()))
        self.assertEqual(repo.collect(),s['data']);repo.mark(self.google,s)
        self.assertEqual(repo.link(self.google)['profile_id'],self.profile)
        self.assertNotEqual(repo.key,self.repo.key)
    def test_upload_verifies_remote_and_records_sync_base(self):
        s=self.repo.upload(self.client,self.google,self.repo.collect(),self.profile,[])
        self.assertEqual(self.client.creates,1);self.assertEqual(self.repo.link(self.google)['hash'],s['hash'])
    def test_cloud_changed_after_comparison_is_not_overwritten(self):
        first=self.snapshot();self.client.create(first)
        with self.assertRaises(SyncFailure):self.repo.upload(self.client,self.google,self.repo.collect(),self.profile,[])
        self.assertEqual(self.client.creates,1)
    def test_local_changed_after_comparison_is_not_uploaded_or_replaced(self):
        old=self.repo.collect();cloud=self.snapshot();(self.root/'Character.md').write_text('已變更',encoding='utf-8')
        with self.assertRaises(SyncFailure):self.repo.upload(self.client,self.google,old,self.profile,[])
        with self.assertRaises(SyncFailure):self.repo.apply(cloud,digest(old))
        self.assertEqual((self.root/'Character.md').read_text(encoding='utf-8'),'已變更');self.assertEqual(self.client.creates,0)
    def test_download_backs_up_prior_local_and_updates_backups_together(self):
        self.fill();before=self.repo.collect();cloud=self.snapshot();cloud['data']['character']='雲端白龍';cloud['hash']=digest(cloud['data'])
        folder=self.repo.apply(cloud,digest(before));self.assertEqual(self.repo.collect(),cloud['data'])
        self.assertEqual(json.loads((folder/'snapshot-before.json').read_text(encoding='utf-8'))['data'],before)
        s=ConversationStore(self.root,self.repo.account);self.assertEqual(s.path.read_bytes(),s.backup.read_bytes())
    def test_none_cloud_sections_remove_current_account_only(self):
        self.fill();other=Repository(self.root,'other');self.fill(other);other_before=other.collect()
        clean=self.snapshot();clean['data'].update(conversation=None,memory=None,relationship=None,diary=None);clean['hash']=digest(clean['data'])
        self.repo.apply(clean,digest(self.repo.collect()));self.assertIsNone(self.repo.collect()['memory']);self.assertEqual(other.collect(),other_before)
    def test_cancel_before_apply_does_not_modify_local(self):
        before=self.repo.collect();cancel=threading.Event();cancel.set()
        with self.assertRaises(SyncFailure):self.repo.apply(self.snapshot(),digest(before),cancel)
        self.assertEqual(self.repo.collect(),before)
    def test_partial_restore_failure_rolls_back_original(self):
        self.fill();before=self.repo.collect();cloud=self.snapshot();cloud['data']['character']='雲端版本';cloud['hash']=digest(cloud['data'])
        import cloud_sync_core
        real=cloud_sync_core.os.replace;failed=[False]
        def fail_one(a,b):
            if str(a).endswith('.sync-tmp') and 'Memory' in str(b) and not failed[0]:failed[0]=True;raise OSError()
            return real(a,b)
        with patch('cloud_sync_core.os.replace',side_effect=fail_one):
            with self.assertRaises(SyncFailure):self.repo.apply(cloud,digest(before))
        self.assertTrue(failed[0]);self.assertEqual(self.repo.collect(),before);self.assertFalse((self.repo.folder/'pending-restore.json').exists())
    def test_recover_after_simulated_process_crash(self):
        self.fill();before=self.repo.collect();cloud=self.snapshot();cloud['data']['character']='雲端版本';cloud['hash']=digest(cloud['data'])
        import cloud_sync_core
        real=cloud_sync_core.os.replace
        def crash(a,b):
            if str(a).endswith('.sync-tmp') and 'Conversation' in str(b):raise KeyboardInterrupt()
            return real(a,b)
        with patch('cloud_sync_core.os.replace',side_effect=crash):
            with self.assertRaises(KeyboardInterrupt):self.repo.apply(cloud,digest(before))
        self.assertEqual((self.root/'Character.md').read_text(encoding='utf-8'),'雲端版本');recover(self.root);self.assertEqual(self.repo.collect(),before)
    def test_malformed_or_tampered_cloud_never_applied(self):
        before=self.repo.collect();snapshot=self.snapshot()
        for mutate in (lambda s:s['data'].update(extra='secret'),lambda s:s.update(hash='0'*64),lambda s:s.update(profile_id='../../x'),lambda s:s['data'].update(character='')):
            s=copy.deepcopy(snapshot);mutate(s)
            with self.assertRaises(SyncFailure):self.repo.apply(s,digest(before))
            self.assertEqual(self.repo.collect(),before)
    def test_immutable_history_and_concurrent_branches_are_preserved(self):
        first=self.snapshot();self.client.create(first)
        one=self.repo.snapshot(first['data'],self.profile,[first['revision']]);two=self.repo.snapshot(first['data'],self.profile,[first['revision']])
        self.client.create(one);self.client.create(two)
        self.assertEqual(heads(self.client.list(),self.profile),sorted([one['revision'],two['revision']]))
        last=self.repo.upload(self.client,self.google,self.repo.collect(),self.profile,heads(self.client.list(),self.profile))
        self.assertEqual(len(self.client.files),4);self.assertEqual(heads(self.client.list(),self.profile),[last['revision']])
    def test_other_google_identity_has_separate_binding(self):
        s=self.snapshot();self.repo.mark('google-1',s);self.assertEqual(self.repo.link('google-2'),{})

class AuthTransportTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.auth=GoogleAuth(self.root);self.auth.folder=self.root/'private';self.auth.client_path=self.auth.folder/'client.dpapi';self.auth.token_path=self.auth.folder/'token.dpapi'
    def test_import_requires_desktop_client_and_encrypts_at_rest(self):
        p=self.root/'input.json';p.write_text(json.dumps({'installed':{'client_id':'123-example.apps.googleusercontent.com','client_secret':'TEST-CLIENT-SECRET'}}))
        self.auth.import_client(p);self.assertNotIn(b'TEST-CLIENT-SECRET',self.auth.client_path.read_bytes());self.assertIsNotNone(self.auth.read(self.auth.client_path))
        p.write_text('{"web":{}}')
        with self.assertRaises(SyncFailure):self.auth.import_client(p)
    def test_reimport_can_repair_corrupt_encrypted_client(self):
        self.auth.folder.mkdir();self.auth.client_path.write_bytes(b'broken')
        p=self.root/'client.json';p.write_text(json.dumps({'installed':{'client_id':'123.apps.googleusercontent.com','client_secret':'replacement'}}))
        self.auth.import_client(p);self.assertEqual(self.auth.read(self.auth.client_path)['client_secret'],'replacement')
    def test_desktop_login_pkce_loopback_and_encrypted_refresh_token(self):
        from urllib.parse import urlparse,parse_qs,urlencode
        from urllib.request import urlopen
        self.auth.save(self.auth.client_path,{'client_id':'123.apps.googleusercontent.com','client_secret':'fake'})
        captured={};threads=[]
        def browser(url):
            q=parse_qs(urlparse(url).query);captured.update(q)
            def callback():
                with urlopen(q['redirect_uri'][0]+'?'+urlencode({'state':q['state'][0],'code':'fake-code'}),timeout=3) as r:r.read()
            t=threading.Thread(target=callback);t.start();threads.append(t);return True
        result={'access_token':'FAKE-ACCESS','refresh_token':'FAKE-REFRESH','expires_in':3600,'scope':'https://www.googleapis.com/auth/drive.appdata openid email'}
        with patch('google_drive_sync.webbrowser.open',side_effect=browser),patch('google_drive_sync.form',return_value=result) as form,patch('google_drive_sync.request',return_value={'sub':'fake-google','email':'test@example.invalid'}):
            self.assertEqual(self.auth.login(threading.Event()),'test@example.invalid')
        for t in threads:t.join(3)
        import hashlib,base64
        verifier=form.call_args.args[0]['code_verifier']
        expected=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        self.assertEqual(captured['code_challenge'],[expected]);self.assertEqual(captured['code_challenge_method'],['S256'])
        self.assertNotIn(b'FAKE-REFRESH',self.auth.token_path.read_bytes());self.assertEqual(self.auth.logged_in(),'test@example.invalid')
    def test_cancelled_login_never_opens_browser(self):
        cancel=threading.Event();cancel.set()
        with patch('google_drive_sync.webbrowser.open') as browser:
            with self.assertRaises(SyncFailure):self.auth.login(cancel)
            browser.assert_not_called()
    def test_bad_oauth_state_duplicate_code_and_denial(self):
        self.assertEqual(callback_values('state=s&code=a','s'),'a')
        for q in ('state=x&code=a','state=s&state=s&code=a','state=s&code=a&code=b','state=s&error=access_denied'):
            with self.assertRaises(SyncFailure):callback_values(q,'s')
    def test_expired_google_token_refreshes_without_new_inference(self):
        self.auth.save(self.auth.client_path,{'client_id':'client','client_secret':'secret'})
        self.auth.save(self.auth.token_path,{'access_token':'old','refresh_token':'refresh','expires_at':0,'sub':'google','email':'test@example.invalid'})
        with patch('google_drive_sync.form',return_value={'access_token':'new','expires_in':3600}) as form:
            self.assertEqual(self.auth.token(),'new');self.assertEqual(form.call_args.args[0]['grant_type'],'refresh_token')
        self.assertEqual(self.auth.read(self.auth.token_path)['refresh_token'],'refresh')
    def test_network_and_expired_authorization_do_not_leak_details(self):
        for code in (401,403,429,500):
            error=HTTPError('https://example.invalid/secret',code,'PRIVATE',{},None)
            with patch('google_drive_sync.urlopen',side_effect=error):
                with self.assertRaises(SyncFailure) as caught:request('GET','https://www.googleapis.com/drive/v3/files')
                self.assertNotIn('PRIVATE',str(caught.exception));self.assertNotIn('secret',str(caught.exception))
    def test_appdata_scope_and_upload_allowlist(self):
        client=DriveClient(Mock());client.auth.token.return_value='TOKEN';s={'profile_id':'a'*32,'revision':'b'*32,'parents':[],'updated_at':'2026-10-19T00:00:00+00:00','data':{'memory':'test'}}
        with patch('google_drive_sync.request',return_value={'files':[]}) as req:client.list();self.assertIn('appDataFolder',req.call_args.args[1])
        with patch('google_drive_sync.request',return_value={'id':'file'}) as req:
            client.create(s);self.assertIn(b'appDataFolder',req.call_args.args[2]);self.assertNotIn(b'TOKEN',req.call_args.args[2])
        self.assertNotIn('https://www.googleapis.com/auth/drive ',SCOPE)
    def test_cancelled_request_does_not_contact_google(self):
        cancel=threading.Event();cancel.set()
        with patch('google_drive_sync.urlopen') as call:
            with self.assertRaises(SyncFailure):request('GET','https://www.googleapis.com',cancel=cancel)
            call.assert_not_called()

class SyncUITests(unittest.TestCase):
    setUpClass=fixtures.ChatWindowTests.__dict__['setUpClass']
    wait_finished=fixtures.ChatWindowTests.wait_finished
    def setUp(self):
        fixtures.ChatWindowTests.setUp(self);self.auth=Mock();self.auth.logged_in.return_value='Google 測試帳號';self.auth.identity.return_value='google';self.client=FakeDrive()
        self.panel=SyncPanel(self.window,self.auth,self.client);self.window.cloud_sync=self.panel;self.panel.show()
    def tearDown(self):
        self.panel.shutdown()
        deadline=time.monotonic()+3
        while self.panel.worker and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        self.panel.deleteLater();self.window.cloud_sync=None;fixtures.ChatWindowTests.tearDown(self)
    def wait_sync(self):
        deadline=time.monotonic()+4
        while self.panel.worker and time.monotonic()<deadline:self.app.processEvents();time.sleep(.01)
        for _ in range(5):self.app.processEvents();time.sleep(.01)
        self.assertIsNone(self.panel.worker)
    def test_open_sync_with_known_profile_automatically_compares_and_asks(self):
        repo=self.panel.repo;cloud=repo.snapshot(repo.collect(),uuid.uuid4().hex,[]);repo.mark('google',cloud)
        cloud['data']['character']='另一台更新的人設';cloud['hash']=digest(cloud['data']);self.client.create(cloud)
        with patch.object(self.panel,'choose',return_value='cancel') as choose:
            self.panel.begin();self.wait_sync();self.wait_sync();choose.assert_called_once()
        self.assertNotEqual(repo.collect()['character'],cloud['data']['character'])
    def test_without_google_login_open_only_shows_setup(self):
        self.auth.logged_in.return_value=''
        with patch.object(self.client,'list') as listing:self.panel.begin();listing.assert_not_called()
        self.assertIsNone(self.panel.worker)
    def test_parameters_contains_sync_and_guide(self):
        self.assertIn('同步記憶',self.window.cloud_sync_button.text());self.panel.show_guide();self.assertTrue(self.panel.guide.isVisible());self.connection.reply.assert_not_called()
    def test_changed_versions_always_ask_and_cancel_leaves_both_untouched(self):
        repo=self.panel.repo;local=repo.collect();cloud=repo.snapshot(local,uuid.uuid4().hex,[]);cloud['data']['character']='雲端白龍';cloud['hash']=digest(cloud['data'])
        self.client.create(cloud);self.panel.checked(self.client.list());self.panel.versions.setCurrentIndex(1)
        with patch.object(self.panel,'choose',return_value='cancel') as choose:self.panel.compare();self.wait_sync();choose.assert_called_once()
        self.assertEqual(repo.collect(),local);self.assertEqual(self.client.creates,1);self.connection.reply.assert_not_called()
    def test_upload_selected_local_keeps_cloud_history(self):
        self.panel.checked([])
        with patch.object(self.panel,'choose',return_value='upload'):self.panel.compare();self.wait_sync();self.wait_sync()
        self.assertEqual(self.client.creates,1);self.assertIn('記憶已同步',self.panel.status.text());self.connection.reply.assert_not_called()
    def test_download_selected_cloud_reloads_character_and_context(self):
        cloud=self.panel.repo.snapshot(self.panel.repo.collect(),uuid.uuid4().hex,[]);cloud['data']['character']='你是雲端版本的白龍。';cloud['hash']=digest(cloud['data'])
        self.client.create(cloud);self.panel.checked(self.client.list());self.panel.versions.setCurrentIndex(1)
        with patch.object(self.panel,'choose',return_value='download'):self.panel.compare();self.wait_sync();self.wait_sync()
        self.assertEqual(self.window.character.text,'你是雲端版本的白龍。');self.connection.reply.assert_not_called()
    def test_identical_data_does_not_ask_or_create_duplicate_version(self):
        cloud=self.panel.repo.snapshot(self.panel.repo.collect(),uuid.uuid4().hex,[]);self.client.create(cloud);self.panel.checked(self.client.list());self.panel.versions.setCurrentIndex(1)
        with patch.object(self.panel,'choose') as choose:self.panel.compare();self.wait_sync();choose.assert_not_called()
        self.assertEqual(self.client.creates,1)
    def test_open_sync_panel_blocks_chat_and_background_interaction(self):
        self.window.message.setPlainText('你好');self.window.send_message();self.connection.reply.assert_not_called();self.assertTrue(self.window.interaction_busy())
    def test_pending_failed_restore_blocks_chat_until_recovery(self):
        self.panel.repo.folder.mkdir();(self.panel.repo.folder/'pending-restore.json').write_text('{}')
        self.panel.failed('還原待修復');self.panel.hide();self.window.message.setPlainText('你好');self.window.send_message()
        self.assertEqual(self.window.cloud_recovery_error,'還原待修復');self.connection.reply.assert_not_called()
    def test_hidden_closed_panel_does_not_show_delayed_choice(self):
        local=self.panel.repo.collect();self.panel.hide()
        with patch.object(self.panel,'choose') as choose:self.panel.resolve((local,None,'google',[]));choose.assert_not_called()

if __name__=='__main__':unittest.main()
