"""Portable installation marker and account storage isolation."""
import hashlib,os,sys
from pathlib import Path

def installation_root():
    return Path(sys.executable).resolve().parent if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[1]

def is_portable(root=None):return ((Path(root) if root else installation_root())/'portable.json').is_file()

def credentials_folder(root=None):
    root=Path(root) if root else installation_root()
    if not is_portable(root):return None
    identity=os.environ.get('COMPUTERNAME','')+'\0'+os.environ.get('USERNAME','')+'\0'+str(Path.home())
    key=hashlib.sha256(identity.encode()).hexdigest()[:24]
    return root/'Private'/'ChatGPT'/key
