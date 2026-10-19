"""Logical account snapshots and rollback-safe local restore, separate from Google login."""
import copy,hashlib,json,os,re,shutil,tempfile,time,uuid
from datetime import datetime,timezone
from pathlib import Path
from google_drive_sync import SyncFailure,atomic_json,cancelled,MAX_BYTES
from character_context import ConversationStore,CharacterProfile,validate_state as validate_conversation
from long_memory import MemoryStore,validate as validate_memory
from relationship import RelationshipStore,validate_state as validate_relationship
from random_events import EventStore,DEFAULT_RULES

HEX=r'[a-f0-9]{32}'
SECTIONS=('character','conversation','memory','relationship','diary')
FOLDERS={'conversation':'Conversation','memory':'Memory','relationship':'Relationship','diary':'白龍的觀察日記'}

def canonical(data):return json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(data):return hashlib.sha256(canonical(data)).hexdigest()
def key_for(account):return hashlib.sha256(account.encode()).hexdigest()
def paths(root,key):
    root=Path(root)
    return {'character':root/'Character.md',**{s:root/f/(key+'.json') for s,f in FOLDERS.items()}}

def validate_data(data):
    try:
        if set(data)!=set(SECTIONS) or not isinstance(data['character'],str) or not data['character'].strip():raise ValueError()
        if len(data['character'].encode('utf-8'))>65536 or len(canonical(data))>MAX_BYTES:raise ValueError()
        if data['conversation'] is not None:validate_conversation(data['conversation'])
        if data['memory'] is not None:validate_memory(data['memory'])
        if data['relationship'] is not None:validate_relationship(data['relationship'])
        if data['diary'] is not None:
            with tempfile.TemporaryDirectory() as directory:
                store=EventStore(directory,'validation',DEFAULT_RULES)
                store.path.parent.mkdir();store.path.write_bytes(canonical(data['diary']))
                checked=EventStore(directory,'validation',DEFAULT_RULES)
                if checked.state!=data['diary']:raise ValueError()
        return data
    except Exception:raise SyncFailure('白龍紀錄不完整或格式無效；未套用這份資料。') from None

def validate_snapshot(snapshot):
    try:
        assert set(snapshot)=={'version','profile_id','revision','parents','updated_at','hash','data'}
        assert snapshot['version']==1 and re.fullmatch(HEX,snapshot['profile_id']) and re.fullmatch(HEX,snapshot['revision'])
        assert isinstance(snapshot['parents'],list) and len(snapshot['parents'])<=3
        assert all(isinstance(v,str) and re.fullmatch(HEX,v) and v!=snapshot['revision'] for v in snapshot['parents'])
        assert isinstance(snapshot['updated_at'],str);datetime.fromisoformat(snapshot['updated_at'])
        validate_data(snapshot['data']);assert snapshot['hash']==digest(snapshot['data'])
        return snapshot
    except Exception:raise SyncFailure('雲端紀錄格式或完整性檢查失敗；本機資料未變更。') from None

def summary(data):
    return ('對話 %d 輪 · 記憶 %d 則 · 日記 %d 筆' %
            (len((data['conversation'] or {}).get('turns',[])),len((data['memory'] or {}).get('items',[])),len((data['diary'] or {}).get('diaries',[]))))

def rows_valid(rows):
    selected=[]
    for row in rows:
        p=row.get('appProperties',{})
        if p.get('bailongSync')!='1':continue
        try:
            assert re.fullmatch(r'[A-Za-z0-9_-]+',row['id']) and re.fullmatch(HEX,p['profile']) and re.fullmatch(HEX,p['revision'])
            parents=[v for v in p.get('parents','').split(',') if v]
            assert len(parents)<=3 and all(re.fullmatch(HEX,v) for v in parents)
            selected.append(row)
        except (KeyError,ValueError,TypeError,AssertionError):raise SyncFailure('發現無法識別的白龍雲端版本，已停止同步以保留紀錄。') from None
    return selected

def heads(rows,profile):
    rows=[r for r in rows_valid(rows) if r['appProperties']['profile']==profile]
    parents={p for r in rows for p in r['appProperties'].get('parents','').split(',') if p}
    return sorted({r['appProperties']['revision'] for r in rows if r['appProperties']['revision'] not in parents})

def targets(root,key):
    result=[]
    for section,path in paths(root,key).items():
        result.append(path)
        if section!='character':result.append(path.with_suffix('.json.bak'))
    return result

def recover(root):
    root=Path(root);folder=root/'CloudSync';journal=folder/'pending-restore.json'
    if not journal.exists():return
    try:
        data=json.loads(journal.read_text(encoding='utf-8'))
        assert re.fullmatch(r'[a-f0-9]{64}',data['key']) and re.fullmatch(HEX,data['transaction'])
        backup=folder/'Transactions'/data['transaction'];known=targets(root,data['key'])
        assert isinstance(data['existed'],list) and len(data['existed'])==len(known) and all(type(v) is bool for v in data['existed'])
        for index,path in enumerate(known):
            if data['existed'][index]:
                path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.restore-tmp')
                shutil.copyfile(backup/str(index),temp);os.replace(temp,path)
            else:path.unlink(missing_ok=True)
        journal.unlink()
    except Exception:raise SyncFailure('上次同步中斷，無法自動還原本機紀錄。請保留 CloudSync/Transactions 備份並先修復。') from None

class Repository:
    def __init__(self,root,account):
        self.root=Path(root);self.account=account;self.key=key_for(account);self.folder=self.root/'CloudSync';self.links_path=self.folder/'links.json'
    def collect(self):
        try:
            profile=CharacterProfile(self.root);profile.reload();locations=paths(self.root,self.key)
            data={'character':profile.path.read_text(encoding='utf-8-sig')}
            convo=ConversationStore(self.root,self.account);data['conversation']=convo.load()[0] if convo.path.exists() or convo.backup.exists() else None
            turns=(data['conversation'] or {}).get('turns',[])
            for section,cls in (('memory',MemoryStore),('relationship',RelationshipStore)):
                store=cls(self.root,self.account)
                if store.path.exists() or store.backup.exists():store.load(turns);data[section]=copy.deepcopy(store.state)
                else:data[section]=None
            p=locations['diary'];data['diary']=EventStore(self.root,self.account,DEFAULT_RULES).state if p.exists() or p.with_suffix('.json.bak').exists() else None
            return copy.deepcopy(validate_data(data))
        except SyncFailure:raise
        except Exception:raise SyncFailure('本機白龍紀錄無法完整讀取，請先確認資料檔與備份。') from None
    def links(self):
        if not self.links_path.exists():return {}
        try:
            data=json.loads(self.links_path.read_text(encoding='utf-8'))
            if not isinstance(data,dict):raise ValueError()
            for k,v in data.items():
                if not isinstance(k,str) or not isinstance(v,dict) or not re.fullmatch(HEX,v.get('profile_id','')) or not re.fullmatch(HEX,v.get('revision','')) or not re.fullmatch(r'[a-f0-9]{64}',v.get('hash','')):raise ValueError()
            return data
        except Exception:raise SyncFailure('本機同步對應檔無法讀取，請保留 CloudSync 資料夾並先修復。') from None
    def link(self,google):return self.links().get(self.key+':'+google,{})
    def mark(self,google,snapshot):
        data=self.links();data[self.key+':'+google]={'profile_id':snapshot['profile_id'],'revision':snapshot['revision'],'hash':snapshot['hash'],'synced_at':datetime.now(timezone.utc).isoformat()}
        atomic_json(self.links_path,data)
    def snapshot(self,data,profile,parents):
        if len(parents)>3:raise SyncFailure('雲端存在超過三個分歧版本，請先保留備份並整理分歧。')
        return validate_snapshot({'version':1,'profile_id':profile,'revision':uuid.uuid4().hex,'parents':parents,
               'updated_at':datetime.now(timezone.utc).isoformat(),'hash':digest(data),'data':copy.deepcopy(data)})
    def upload(self,client,google,data,profile,expected_heads,cancel=None):
        cancelled(cancel)
        if digest(self.collect())!=digest(data):raise SyncFailure('比較後本機紀錄已變更，請重新按同步記憶比較。')
        if heads(client.list(cancel),profile)!=expected_heads:raise SyncFailure('另一台電腦已更新雲端紀錄，請重新比較後選擇。')
        snapshot=self.snapshot(data,profile,expected_heads)
        result=client.create(snapshot,cancel);cancelled(cancel)
        confirmed=validate_snapshot(client.download(result['id'],cancel))
        if confirmed!=snapshot:raise SyncFailure('雲端寫入確認失敗，請重新比較，勿假定同步完成。')
        self.mark(google,snapshot);return snapshot
    def apply(self,snapshot,expected_hash,cancel=None):
        validate_snapshot(snapshot);cancelled(cancel)
        if digest(self.collect())!=expected_hash:raise SyncFailure('比較後本機紀錄已變更，未覆蓋；請重新比較。')
        data=snapshot['data'];tx=uuid.uuid4().hex;backup=self.folder/'Transactions'/tx;backup.mkdir(parents=True)
        known=targets(self.root,self.key);existed=[p.exists() for p in known]
        for index,path in enumerate(known):
            if path.exists():shutil.copyfile(path,backup/str(index))
        atomic_json(backup/'snapshot-before.json',{'saved_at':datetime.now(timezone.utc).isoformat(),'data':self.collect()})
        cancelled(cancel)
        atomic_json(self.folder/'pending-restore.json',{'version':1,'key':self.key,'transaction':tx,'existed':existed})
        try:
            # From here finish or roll back before observing cancellation; no partial state is exposed.
            for section,path in paths(self.root,self.key).items():
                value=data[section];destinations=[path] if section=='character' else [path,path.with_suffix('.json.bak')]
                for destination in destinations:
                    if value is None:destination.unlink(missing_ok=True);continue
                    destination.parent.mkdir(parents=True,exist_ok=True);temp=destination.with_name(destination.name+'.sync-tmp')
                    temp.write_bytes(value.encode('utf-8') if section=='character' else canonical(value));os.replace(temp,destination)
            (self.folder/'pending-restore.json').unlink()
        except Exception:
            recover(self.root);raise SyncFailure('套用雲端紀錄失敗，已還原原本的本機資料。') from None
        return backup
