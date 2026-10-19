"""Configure a portable notification bridge without replacing unrelated settings."""
import json,os,shutil,tomllib,uuid
from pathlib import Path

def replace_notify(text,command):
    parsed=tomllib.loads(text);previous=parsed.get('notify',[])
    lines=text.splitlines(keepends=True);start=end=None
    for i,line in enumerate(lines):
        if line.lstrip().startswith('['):break
        if '=' in line and line.split('=',1)[0].strip()=='notify':
            start=i
            for j in range(i+1,len(lines)+1):
                try:
                    if tomllib.loads(''.join(lines[i:j])).get('notify')==previous:end=j;break
                except tomllib.TOMLDecodeError:pass
            break
    newline='\r\n' if '\r\n' in text else '\n'
    entry=('notify = '+json.dumps(command,ensure_ascii=False)+newline) if command is not None else ''
    if start is None:
        if previous:raise ValueError('無法安全定位現有通知設定。')
        updated=entry+text
    else:
        if end is None:raise ValueError('無法解析現有通知設定。')
        updated=''.join(lines[:start])+entry+''.join(lines[end:])
    expected=dict(parsed)
    if command is None:expected.pop('notify',None)
    else:expected['notify']=command
    assert tomllib.loads(updated)==expected
    return updated

def apply(root,config=None,remove=False):
    root=Path(root);config=Path(config) if config else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'config.toml'
    text=config.read_text(encoding='utf-8') if config.exists() else ''
    parsed=tomllib.loads(text);previous=parsed.get('notify',[]);command=[str(root/'CodexNotify.exe')]
    folder=root/'CodexIntegration';folder.mkdir(parents=True,exist_ok=True)
    if remove:
        if previous!=command:return '目前使用其他通知設定，未作變更。'
        saved=json.loads((folder/'notify-previous.json').read_text(encoding='utf-8'))
        updated=replace_notify(text,saved)
    else:
        if previous==command:return '已啟用此可攜版的完成通知。請重新啟動 Codex。'
        if not isinstance(previous,list) or any(not isinstance(v,str) for v in previous):raise ValueError('原通知設定無效。')
        forwarded=previous
        # Flatten another Bailong bridge while preserving its command for uninstall.
        if len(previous)==1 and Path(previous[0]).name.lower()=='codexnotify.exe':
            p=Path(previous[0]).parent/'CodexIntegration/notify-forward.json'
            if p.is_file():
                candidate=json.loads(p.read_text(encoding='utf-8'))
                if isinstance(candidate,list) and all(isinstance(v,str) for v in candidate):forwarded=candidate
        (folder/'notify-forward.json').write_text(json.dumps(forwarded,ensure_ascii=False),encoding='utf-8')
        (folder/'notify-previous.json').write_text(json.dumps(parsed.get('notify'),ensure_ascii=False),encoding='utf-8')
        updated=replace_notify(text,command)
    config.parent.mkdir(parents=True,exist_ok=True)
    if config.exists():shutil.copy2(config,config.with_name(config.name+'.bailong-'+uuid.uuid4().hex[:8]+'.bak'))
    tmp=config.with_name(config.name+'.bailong.tmp');tmp.write_text(updated,encoding='utf-8');os.replace(tmp,config)
    return ('已還原原本的通知設定。' if remove else '已啟用工作完成通知，並保留原通知功能。')+'請重新啟動 Codex。'
