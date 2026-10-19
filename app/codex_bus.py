"""Per-Windows-user inbox containing only hashed completion IDs and timestamps."""
import os
from pathlib import Path

def shared_inbox():
    return Path(os.environ.get('LOCALAPPDATA') or Path.home()/'AppData/Local')/'BailongDesktopPet/CodexNotifications/inbox'

def installed_root(root):
    root=Path(root)
    return (root/'portable.json').is_file() or (root/'Start-BailongPet.cmd').is_file()
