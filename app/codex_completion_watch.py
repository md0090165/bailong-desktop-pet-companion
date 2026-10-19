"""Bounded, read-only fallback for local Codex task_complete event envelopes.

Only hashes and completion timestamps leave this module. Response messages are
never persisted, returned, logged or sent anywhere.
"""
import hashlib
import json
import os
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path


def completion_record(line, identities, since):
    if b'"task_complete"' not in line or b'"event_msg"' not in line:
        return None
    try:
        data=json.loads(line)
        payload=data.get('payload')
        if data.get('type')!='event_msg' or not isinstance(payload,dict) or payload.get('type')!='task_complete':return None
        stamp=datetime.fromisoformat(data['timestamp'].replace('Z','+00:00')).timestamp()
        if stamp<since or stamp>time.time()+60:return None
        turns=[payload.get('root_turn_id'),payload.get('turn_id')]
        keys=list(dict.fromkeys(hashlib.sha256((thread+'\0'+turn).encode()).hexdigest()
            for thread in identities for turn in turns if isinstance(turn,str) and 0<len(turn)<=256))
        return {'id':keys[0],'aliases':keys[1:],'at':stamp} if keys else None
    except (ValueError,TypeError,KeyError,AttributeError):return None


class CompletionWatch:
    def __init__(self,root=None):
        self.root=Path(root) if root else Path(os.environ.get('CODEX_HOME') or Path.home()/'.codex')/'sessions'
        self.since=time.time()-1;self.files={};self.next_scan=0;self.error=False
        try:self.discover(initial=True)
        except OSError:self.error=True

    def discover(self,initial=False):
        self.next_scan=time.monotonic()+5
        now=datetime.now(timezone.utc)
        candidates=[]
        for offset in (-1,0,1):
            day=now+timedelta(days=offset)
            folder=self.root/day.strftime('%Y/%m/%d')
            if folder.is_dir():
                candidates.extend(folder.glob('*.jsonl'))
        for path in sorted(candidates,key=lambda p:p.stat().st_mtime,reverse=True)[:64]:
            if path in self.files:continue
            try:
                with path.open('rb') as stream:
                    header=json.loads(stream.readline(65536))
                if header.get('type')!='session_meta':continue
                data=header.get('payload') or {}
                identities=list(dict.fromkeys(data[k] for k in ('session_id','id')
                    if isinstance(data.get(k),str) and 0<len(data[k])<=256))
                if not identities:continue
                self.files[path]={'position':path.stat().st_size if initial else 0,'tail':b'','identities':identities}
            except (OSError,ValueError,TypeError,AttributeError):continue
        if len(self.files)>64:
            keep=set(sorted(self.files,key=lambda p:p.stat().st_mtime if p.exists() else 0,reverse=True)[:64])
            self.files={p:value for p,value in self.files.items() if p in keep}

    def poll(self):
        records=[]
        try:
            if time.monotonic()>=self.next_scan:self.discover()
            for path,state in list(self.files.items()):
                try:
                    size=path.stat().st_size
                    if size==state['position']:continue
                    if size<state['position']:state.update(position=0,tail=b'')
                    with path.open('rb') as stream:
                        stream.seek(state['position']);chunk=stream.read(512*1024);state['position']=stream.tell()
                    lines=(state['tail']+chunk).split(b'\n');state['tail']=lines.pop()
                    if len(state['tail'])>2*1024*1024:state['tail']=b''
                    for line in lines:
                        record=completion_record(line,state['identities'],self.since)
                        if record:records.append(record)
                except OSError:continue
            self.error=False
        except OSError:self.error=True
        return records
