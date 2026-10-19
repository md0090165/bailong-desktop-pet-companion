import os,sys,json,tempfile,tomllib,unittest
from pathlib import Path
from unittest.mock import patch
from portable_support import credentials_folder,is_portable
from notify_setup import apply,replace_notify
from chat_connection import CredentialStore
from character_context import project_root
import quota

class PortableTests(unittest.TestCase):
    def setUp(self):self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def test_unmarked_installation_retains_existing_credential_location(self):
        self.assertIsNone(credentials_folder(self.root))
    def test_clean_portable_isolated_login_and_no_copied_global_accounts(self):
        (self.root/'portable.json').write_text('{}')
        with patch('portable_support.installation_root',return_value=self.root):
            store=CredentialStore();self.assertTrue(store.path.is_relative_to(self.root/'Private'))
            self.assertEqual(store.load()['accounts'],{});self.assertFalse(store.path.exists())
    def test_portable_auth_machine_separation(self):
        (self.root/'portable.json').write_text('{}')
        with patch.dict(os.environ,{'COMPUTERNAME':'MachineA'}):a=credentials_folder(self.root)
        with patch.dict(os.environ,{'COMPUTERNAME':'MachineB'}):b=credentials_folder(self.root)
        self.assertNotEqual(a,b)
    def test_portable_profile_stops_at_executable_directory(self):
        (self.root/'portable.json').write_text('{}')
        with patch.object(sys,'frozen',True,create=True),patch.object(sys,'executable',str(self.root/'BailongPet.exe')):
            self.assertEqual(project_root(),self.root)
    def test_bundled_codex_fallback_with_no_external_install(self):
        binary=self.root/'runtime/codex/codex.exe';binary.parent.mkdir(parents=True);binary.write_bytes(b'test')
        with patch.dict(os.environ,{'BAILONG_CODEX':'','LOCALAPPDATA':str(self.root/'empty'),'USERPROFILE':str(self.root/'empty')}),patch('quota.shutil.which',return_value=None),patch('portable_support.installation_root',return_value=self.root):
            self.assertEqual(quota.find_codex(),str(binary))
    def test_setup_preserves_existing_handler_and_all_other_settings(self):
        config=self.root/'user/config.toml';config.parent.mkdir();text='model="example"\nnotify = [\n "old.exe",\n "turn-ended",\n]\n[features]\nfoo=true\n';config.write_text(text)
        apply(self.root,config);new=tomllib.loads(config.read_text());self.assertEqual(new['features'],{'foo':True})
        self.assertEqual(new['notify'],[str(self.root/'CodexNotify.exe')])
        self.assertEqual(json.loads((self.root/'CodexIntegration/notify-forward.json').read_text()),['old.exe','turn-ended'])
        apply(self.root,config);apply(self.root,config,remove=True);self.assertEqual(tomllib.loads(config.read_text()),tomllib.loads(text))
        self.assertIn(text,[p.read_text() for p in config.parent.glob('*.bak')])
    def test_setup_empty_config_and_remove_restores_absent_setting(self):
        config=self.root/'user/config.toml';apply(self.root,config)
        self.assertEqual(tomllib.loads(config.read_text())['notify'],[str(self.root/'CodexNotify.exe')])
        apply(self.root,config,remove=True);self.assertNotIn('notify',tomllib.loads(config.read_text()))
    def test_remove_does_not_overwrite_a_newer_unrelated_handler(self):
        config=self.root/'config.toml';config.write_text('notify=["different.exe"]\n');before=config.read_bytes()
        apply(self.root,config,remove=True);self.assertEqual(config.read_bytes(),before)
    def test_setting_up_second_bailong_flattens_forwarding(self):
        first=self.root/'first';(first/'CodexIntegration').mkdir(parents=True)
        (first/'CodexIntegration/notify-forward.json').write_text('["original.exe"]')
        config=self.root/'config.toml';config.write_text('notify='+json.dumps([str(first/'CodexNotify.exe')]))
        second=self.root/'second';apply(second,config)
        self.assertEqual(json.loads((second/'CodexIntegration/notify-forward.json').read_text()),['original.exe'])
        apply(second,config,remove=True);self.assertEqual(tomllib.loads(config.read_text())['notify'],[str(first/'CodexNotify.exe')])
    def test_invalid_existing_config_preserved(self):
        config=self.root/'config.toml';config.write_text('notify=[');before=config.read_bytes()
        with self.assertRaises(ValueError):apply(self.root,config)
        self.assertEqual(config.read_bytes(),before)

if __name__=='__main__':unittest.main()
