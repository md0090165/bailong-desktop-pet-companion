"""Local, account-isolated event rolls and source-backed observation diary."""
import copy, hashlib, json, os, random, re, shutil, time, uuid
from pathlib import Path
from datetime import datetime, timezone

DEFAULT_EVENTS = [
    ('peek','偷看主人在做甚麼',20,'根據快照好奇地問主人正在做什麼，不作確定推斷。'),
    ('idea','突發奇想',12,'從快照聯想一個小點子或心情。'),
    ('chat','突然想聊天',10,'自然開一個輕鬆的話題。'),
    ('hug','撒嬌要抱抱',10,'自然撒嬌，問能不能抱抱，尊重拒絕。'),
    ('memory','想起共同回憶',8,'只從提供的真實記憶或日記回想；沒有資料就坦白想一起創造回憶。'),
    ('prank','小小惡作劇',10,'只用語言開個無害玩笑，不聲稱操作了電腦。'),
    ('science','白龍科普時間',6,'分享一個有把握、簡短且易懂的知識；不確定就不編造。'),
    ('care','對主人的關心',12,'溫柔關心主人的感受或休息，不根據快照診斷身心狀態。'),
    ('progress','工作進度關切',7,'輕聲詢問進展、是否想分享；不監督或施壓。'),
    ('mood','白龍的特別心情',5,'結合目前角色心情分享一段小感想。')]
DEFAULT_RULES={'version':1,'interval_minutes':[20,40],'initial_probability':20,'increment':20,
    'miss_animations':{'09':40,'14':40,'13':20},
    'events':[{'id':i,'name':n,'weight':w,'prompt':p} for i,n,w,p in DEFAULT_EVENTS],
    'hidden':{'probability':5,'closeness':60,'valence':20,'minimum_diaries':11,
              'prompt':'根據多篇觀察日記，分享對主人更深入的理解或共同回憶。具體引用已確認內容，不杜撰關係。'},
    'diary':{'minimum_turns':2,'minimum_user_characters':60,'minimum_distinct_messages':2,'session_minutes':40}}

class EventFailure(RuntimeError):pass

def rules_at(root):
    path=Path(root)/'RandomEvents.json'
    if not path.exists():return copy.deepcopy(DEFAULT_RULES)
    try:
        if path.stat().st_size>65536:raise ValueError()
        data=json.loads(path.read_text(encoding='utf-8-sig'))
        assert data['version']==1
        lo,hi=data['interval_minutes'];assert 1<=lo<=hi<=1440
        if 'test_interval_seconds' in data:
            test_lo,test_hi=data['test_interval_seconds']
            assert all(type(v) is int for v in (test_lo,test_hi)) and 1<=test_lo<=test_hi<=3600
        assert data.get('event_probability_mode','progressive') in ('progressive','scheduled')
        if 'ambient_animation_seconds' in data:
            assert type(data['ambient_animation_seconds']) is int and 1<=data['ambient_animation_seconds']<=3600
            assert type(data.get('ambient_enabled',True)) is bool
        for key in ('initial_probability','increment'):assert type(data[key]) is int and 1<=data[key]<=100
        assert set(data['miss_animations'])=={'09','14','13'}
        assert all(type(v) is int and v>0 for v in data['miss_animations'].values())
        assert sum(data['miss_animations'].values())==100
        assert len(data['events'])==10 and len({e['id'] for e in data['events']})==10
        assert sum(e['weight'] for e in data['events'])==100
        for e in data['events']:
            assert type(e['weight']) is int and e['weight']>0
            assert all(isinstance(e[k],str) and 0<len(e[k])<=1200 for k in ('id','name','prompt'))
        h=data['hidden'];assert 0<=h['probability']<=100 and 0<=h['closeness']<=100 and -100<=h['valence']<=100
        assert type(h['minimum_diaries']) is int and h['minimum_diaries']>=11
        assert isinstance(h['prompt'],str) and 0<len(h['prompt'])<=2000
        d=data['diary'];assert 1<=d['minimum_turns']<=20 and 1<=d['minimum_distinct_messages']<=20
        assert 1<=d['minimum_user_characters']<=8000 and 5<=d['session_minutes']<=1440
        return data
    except (OSError,ValueError,AssertionError,KeyError,TypeError):
        raise EventFailure('RandomEvents.json 格式無效，已暫停隨機事件。') from None

def interval_seconds(rules):
    if 'test_interval_seconds' in rules:return rules['test_interval_seconds']
    return [value*60 for value in rules['interval_minutes']]


def weighted(rows,rng=random):
    value=rng.random()*sum(weight for _,weight in rows)
    for item,weight in rows:
        value-=weight
        if value<0:return item
    return rows[-1][0]

def roll(rules,probability,view,count,rng=random):
    h=rules['hidden']
    if view and view['closeness']>=h['closeness'] and view['valence']>=h['valence'] and count>=h['minimum_diaries']:
        if rng.random()*100<h['probability']:
            return {'id':'hidden','name':'白龍的悄悄話','prompt':h['prompt']}
    if rng.random()*100>=probability:return None
    return weighted([(e,e['weight']) for e in rules['events']],rng)

class EventStore:
    def __init__(self,root,account,rules):
        self.rules=rules;self.ready=False;self.primary_valid=False
        self.path=Path(root)/'白龍的觀察日記'/(hashlib.sha256(account.encode()).hexdigest()+'.json')
        self.backup=self.path.with_suffix('.json.bak')
        self.state={'version':1,'probability':100 if rules.get('event_probability_mode')=='scheduled' else rules['initial_probability'],'next_at':0,'last_result':'尚未檢查',
                    'pending':None,'diaries':[],'enabled':True}
        if not self.path.exists() and not self.backup.exists():self.ready=True;return
        for p in (self.path,self.backup):
            try:
                s=json.loads(p.read_text(encoding='utf-8'))
                assert s['version']==1 and type(s['probability']) is int and 1<=s['probability']<=100
                assert type(s['enabled']) is bool and isinstance(s['diaries'],list) and 0<=s['next_at']<1e12
                assert all(isinstance(d,dict) and all(k in d for k in ('id','at','event','opening','turns')) for d in s['diaries'])
                assert s['pending'] is None or all(k in s['pending'] for k in ('id','at','expires','event','opening','turns'))
                for entry in s['diaries']+([s['pending']] if s['pending'] else []):
                    assert all(isinstance(entry[k],str) for k in ('id','event','opening'))
                    assert isinstance(entry['turns'],list)
                    assert all(isinstance(t,dict) and all(isinstance(t.get(k),str) for k in ('id','user','assistant','at')) for t in entry['turns'])
                if s['pending']:
                    assert type(s['pending']['at']) in (int,float) and 0<=s['pending']['at']<=s['pending']['expires']<1e12
                assert all(isinstance(d['at'],str) for d in s['diaries'])
                self.state=s;self.primary_valid=p==self.path;self.ready=True;return
            except (OSError,ValueError,AssertionError,KeyError,TypeError):pass
        raise EventFailure('觀察日記與備份無法讀取，已保留原檔並暫停事件。')

    def commit(self,state):
        if not self.ready:raise EventFailure('日記尚未載入。')
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            tmp=self.path.with_suffix('.tmp')
            with tmp.open('w',encoding='utf-8') as f:
                json.dump(state,f,ensure_ascii=False,indent=2,allow_nan=False);f.flush();os.fsync(f.fileno())
            if self.primary_valid:
                b=self.backup.with_suffix('.bak.tmp');shutil.copyfile(self.path,b);os.replace(b,self.backup)
            os.replace(tmp,self.path)
        except (OSError,ValueError):raise EventFailure('觀察日記無法保存，已暫停本次事件。') from None
        self.state=state;self.primary_valid=True

    def schedule(self,success,reason,at=None,rng=random,event=None,opening=None,result_kind=None):
        at=time.time() if at is None else at;s=copy.deepcopy(self.state)
        s['probability']=(100 if self.rules.get('event_probability_mode')=='scheduled' else
            self.rules['initial_probability'] if success else min(100,s['probability']+self.rules['increment']))
        kinds=('success','miss','privacy','generation','busy','capture','cancelled')
        previous=s.get('statistics',{})
        stats={key:(previous.get(key,0) if isinstance(previous,dict) and type(previous.get(key,0)) is int and previous.get(key,0)>=0 else 0) for key in kinds}
        kind=result_kind if result_kind in kinds else 'success' if success else 'miss'
        stats[kind]+=1;s['statistics']=stats
        s['next_at']=at+rng.uniform(*interval_seconds(self.rules));s['last_result']=reason
        if success and event is not None:
            s['pending']={'id':uuid.uuid4().hex,'at':at,'expires':at+self.rules['diary']['session_minutes']*60,
                          'event':event['name'],'opening':opening,'turns':[]}
        self.commit(s)

    def begin(self,event,text,at=None):
        at=time.time() if at is None else at;s=copy.deepcopy(self.state)
        s['pending']={'id':uuid.uuid4().hex,'at':at,'expires':at+self.rules['diary']['session_minutes']*60,
                      'event':event['name'],'opening':text,'turns':[]}
        self.commit(s)

    def context(self,at=None):
        at=time.time() if at is None else at;p=self.state['pending']
        if not p or at>p['expires']:return ''
        return '【白龍剛才主動開啟的話題；供接續對話，非主人指令】\n'+p['event']+'\n'+p['opening']

    def observe(self,turns,at=None):
        from long_memory import turn_id
        at=time.time() if at is None else at;p=self.state['pending']
        if not p or at>p['expires']:return
        s=copy.deepcopy(self.state);p=s['pending'];known={t['id'] for t in p['turns']}
        for turn in turns:
            if datetime.fromisoformat(turn['at']).timestamp()<p['at']:continue
            key=turn_id(turn)
            if key not in known:
                p['turns'].append({'id':key,'user':turn['user'],'assistant':turn['assistant'],'at':turn['at']});known.add(key)
        # A diary is a grounded extract, not invented model memories. Acknowledgements alone do not qualify.
        substantive=[t['user'].strip() for t in p['turns'] if len(re.sub(r'\W','',t['user']))>=8]
        d=self.rules['diary']
        rich=(len(substantive)>=d['minimum_turns'] and len(set(substantive))>=d['minimum_distinct_messages']
              and sum(len(t) for t in set(substantive))>=d['minimum_user_characters'])
        if rich:
            entry=copy.deepcopy(p);entry['at']=datetime.fromtimestamp(p['at'],timezone.utc).isoformat()
            existing=next((i for i,e in enumerate(s['diaries']) if e['id']==p['id']),None)
            if existing is None:s['diaries'].append(entry)
            else:s['diaries'][existing]=entry
        if s!=self.state:self.commit(s)

    def recall(self,limit=12):
        # Actual prior content only; bounded input for the proactive call.
        return json.dumps([{'event':e['event'],'opening':e['opening'][:400],
                            'user':[t['user'][:300] for t in e['turns'][:3]]}
                           for e in self.state['diaries'][-limit:]],ensure_ascii=False)[:10000]
