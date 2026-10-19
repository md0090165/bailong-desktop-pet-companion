"""Account-isolated roleplay state with bounded, auditable local updates."""
import copy
import hashlib
import json
import math
import os
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from long_memory import turn_id
from reply_animation import EMOTIONS


DEFAULT_RULES = {
    'version':1,
    'initial':{'closeness':20.0,'trust':50.0},
    'baseline':{'valence':0.0,'energy':50.0},
    'max_per_turn':{'closeness':0.6,'trust':0.3,'valence':12.0,'energy':12.0},
    'daily_gain_cap':{'closeness':2.4,'trust':1.2},
    'growth_interval_seconds':90,
    'mood_half_life_minutes':30,
    'quiet_minutes':30,
    'events':{
        'quiet':{'label':'安靜陪伴','phrases':['請安靜','我要專心','需要專心','別打擾','不要黏','不用動作','不要播放動畫'],
                 'closeness':0.1,'trust':0.1,'valence':0,'energy':0,'emotion':'平靜'},
        'rest':{'label':'準備休息','phrases':['晚安','準備睡','要睡了','去睡覺','一起休息'],
                'closeness':0.2,'trust':0.1,'valence':2,'energy':-12,'emotion':'睏倦'},
        'comfort':{'label':'溫柔陪伴','phrases':['我很難過','我好難過','我不開心','壓力很大','我好累','我很累','我很傷心'],
                   'closeness':0.2,'trust':0.1,'valence':0,'energy':-4,'emotion':'安慰'},
        'praise':{'label':'受到肯定','phrases':['謝謝你','謝謝白龍','辛苦你了','你好厲害','好聰明','做得很好','很棒','謝謝','感謝你','你好棒','太厲害了','真厲害'],
                  'closeness':0.5,'trust':0.2,'valence':12,'energy':4,'emotion':'害羞'},
        'affection':{'label':'親切互動','phrases':['喜歡你','抱抱','貼貼','摸摸頭'],
                     'closeness':0.6,'trust':0.2,'valence':10,'energy':2,'emotion':'親暱'},
        'celebration':{'label':'分享好消息','phrases':['我完成了','我終於完成','成功了','通過了','我做到了'],
                       'closeness':0.3,'trust':0.1,'valence':10,'energy':8,'emotion':'開心'},
        'conversation':{'label':'日常交流','phrases':[],
                        'closeness':0.2,'trust':0.1,'valence':0,'energy':0,'emotion':'平靜'}
    }
}


class RelationshipFailure(RuntimeError):
    pass


def finite(value,low,high):
    return type(value) in (int,float) and math.isfinite(value) and low<=value<=high


def clamp(value,low,high):
    return max(low,min(high,value))


def local_day(at):
    return datetime.fromtimestamp(at,timezone.utc).astimezone().date().isoformat()


class RelationshipRules:
    def __init__(self,root):
        self.path=Path(root)/'RelationshipRules.json'
        self.data=copy.deepcopy(DEFAULT_RULES);self.warning=''
        try:
            with self.path.open('rb') as source:raw=source.read(65537)
            if len(raw)>65536:raise ValueError('oversized rules')
            data=json.loads(raw.decode('utf-8-sig'))
            self.validate(data);self.data=data
        except FileNotFoundError:
            pass
        except (OSError,ValueError,KeyError,TypeError,AttributeError):
            self.warning='RelationshipRules.json 無法讀取，已使用內建關係規則。'

    @staticmethod
    def validate(data):
        if data['version']!=1:raise ValueError('unsupported rules')
        for key in ('closeness','trust'):
            if not finite(data['initial'][key],0,100):raise ValueError('bad initial')
            if not finite(data['daily_gain_cap'][key],0,10 if key=='closeness' else 5):raise ValueError('bad cap')
        for key,bound in (('closeness',2),('trust',1),('valence',20),('energy',20)):
            if not finite(data['max_per_turn'][key],0,bound):raise ValueError('bad per turn limit')
        if not finite(data['baseline']['valence'],-100,100) or not finite(data['baseline']['energy'],0,100):
            raise ValueError('bad mood baseline')
        for key,low,high in (('growth_interval_seconds',15,86400),('mood_half_life_minutes',1,1440),('quiet_minutes',1,120)):
            if not finite(data[key],low,high):raise ValueError('bad time setting')
        if not isinstance(data['events'],dict) or not set(DEFAULT_RULES['events'])<=set(data['events']):
            raise ValueError('missing events')
        for event in data['events'].values():
            if not isinstance(event['label'],str) or not 1<=len(event['label'])<=40:raise ValueError('bad label')
            if not isinstance(event['phrases'],list) or any(not isinstance(p,str) or not 1<=len(p)<=80 for p in event['phrases']):
                raise ValueError('bad phrases')
            if event['emotion'] not in EMOTIONS:raise ValueError('bad emotion')
            if not finite(event['closeness'],0,2) or not finite(event['trust'],0,1):raise ValueError('bad growth')
            if not finite(event['valence'],-20,20) or not finite(event['energy'],-20,20):raise ValueError('bad mood delta')

    def classify(self,message):
        # Quoted, hypothetical and example words are not direct emotional events.
        if any(word in message for word in ('假設','假如','如果','舉例','例如','翻譯','角色扮演','故事裡','小說中')):
            return 'conversation',''
        text=re.sub(r'```.*?```|「[^」]*」|『[^』]*』|"[^"\n]*"','',message,flags=re.S)
        priority=['quiet','rest','comfort','praise','affection','celebration']
        priority += [key for key in self.data['events'] if key not in priority and key!='conversation']
        for key in priority:
            for phrase in self.data['events'][key]['phrases']:
                for match in re.finditer(re.escape(phrase),text):
                    preceding=text[max(0,match.start()-4):match.start()]
                    if key not in ('quiet','comfort') and any(word in preceding for word in ('不','沒','不是','別')):
                        continue
                    return key,phrase
        return 'conversation',''


def initial_state(rules,at):
    data=rules.data
    return {'version':1,**data['initial'],**data['baseline'],'mood_at':at,
            'mood_emotion':'平靜','quiet_until':0,'last_gain_at':None,'last_interaction_at':None,
            'daily':{'day':local_day(at),'closeness':0.0,'trust':0.0},
            'processed_turn_ids':[],'changes':[]}


def validate_state(state):
    if not isinstance(state,dict) or state.get('version')!=1:raise ValueError('invalid state')
    for key,low,high in (('closeness',0,100),('trust',0,100),('valence',-100,100),('energy',0,100),
                         ('mood_at',0,1e12),('quiet_until',0,1e12)):
        if not finite(state.get(key),low,high):raise ValueError('invalid number')
    if state.get('mood_emotion') not in EMOTIONS:raise ValueError('invalid emotion')
    for key in ('last_gain_at','last_interaction_at'):
        if state.get(key) is not None and not finite(state[key],0,1e12):raise ValueError('invalid time')
    daily=state.get('daily')
    if not isinstance(daily,dict) or not isinstance(daily.get('day'),str):raise ValueError('invalid day')
    for key in ('closeness','trust'):
        if not finite(daily.get(key),0,100):raise ValueError('invalid daily growth')
    ids=state.get('processed_turn_ids');changes=state.get('changes')
    if not isinstance(ids,list) or any(not isinstance(v,str) or len(v)!=64 for v in ids):raise ValueError('invalid source ids')
    if not isinstance(changes,list) or len(changes)>100:raise ValueError('invalid history')
    for change in changes:
        if not isinstance(change,dict) or any(key not in change for key in ('at','event','reason','before','after','source')):
            raise ValueError('invalid change entry')
        if any(not isinstance(change[key],str) for key in ('at','event','reason')):raise ValueError('invalid change text')
        source=change['source']
        if (not isinstance(source,dict) or not isinstance(source.get('quote'),str) or len(source['quote'])>200 or
                type(source.get('turn_number')) is not int or source['turn_number']<1 or
                not isinstance(source.get('turn_id'),str) or len(source['turn_id'])!=64):
            raise ValueError('invalid change source')
        for values in (change['before'],change['after']):
            if not isinstance(values,dict):raise ValueError('invalid score snapshot')
            for key,low,high in (('closeness',0,100),('trust',0,100),('valence',-100,100),('energy',0,100)):
                if not finite(values.get(key),low,high):raise ValueError('invalid snapshot number')
    return state


def view_state(state,rules,at):
    data=rules.data;elapsed=max(0,at-state['mood_at'])
    factor=2**(-elapsed/(data['mood_half_life_minutes']*60))
    view={key:state[key] for key in ('closeness','trust')}
    for key in ('valence','energy'):
        baseline=data['baseline'][key]
        view[key]=baseline+(state[key]-baseline)*factor
    active=max(abs(view[key]-data['baseline'][key]) for key in ('valence','energy'))>=3
    view['quiet']=at<state['quiet_until']
    view['emotion']=state['mood_emotion'] if active else '平靜'
    view['mood_label']='安靜陪伴' if view['quiet'] else view['emotion']
    if view['quiet']:view['emotion']='平靜'
    value=view['closeness']
    view['level']='相處起步' if value<30 else '逐漸熟悉' if value<60 else '親近夥伴' if value<80 else '默契夥伴'
    return view


def advance(state,rules,message,at):
    state=copy.deepcopy(state);data=rules.data;before=view_state(state,rules,at)
    event,matched=rules.classify(message);effect=data['events'][event]
    at=max(at,state['mood_at'])
    if local_day(at)>state['daily']['day']:
        state['daily']={'day':local_day(at),'closeness':0.0,'trust':0.0}
    reason=[]
    can_grow=state['last_gain_at'] is None or at-state['last_gain_at']>=data['growth_interval_seconds']
    grew=False
    for key in ('closeness','trust'):
        delta=min(effect[key],data['max_per_turn'][key],max(0,data['daily_gain_cap'][key]-state['daily'][key]),100-state[key])
        if not can_grow:delta=0
        state[key]=round(state[key]+delta,6);state['daily'][key]=round(state['daily'][key]+delta,6)
        grew=grew or delta>0
    if grew:state['last_gain_at']=at
    if not can_grow:reason.append('距上次累積未滿成長間隔，親密與信任暫不增加')
    elif not grew:reason.append('已達每日上限或數值上限')
    for key,low,high in (('valence',-100,100),('energy',0,100)):
        delta=clamp(effect[key],-data['max_per_turn'][key],data['max_per_turn'][key])
        state[key]=clamp(before[key]+delta,low,high)
    state['mood_at']=at;state['last_interaction_at']=at
    if event=='quiet':
        state.update(**data['baseline'],mood_emotion='平靜',quiet_until=at+data['quiet_minutes']*60)
    else:
        state['quiet_until']=0
        if event!='conversation':state['mood_emotion']=effect['emotion']
    after=view_state(state,rules,at)
    return state,{'event':event,'label':effect['label'],'matched':matched,'before':before,'after':after,
                  'reason':'；'.join(reason) or '依互動規則小幅累積'}


class RelationshipStore:
    def __init__(self,root,account,rules=None):
        key=hashlib.sha256(account.encode('utf-8')).hexdigest()
        self.path=Path(root)/'Relationship'/(key+'.json');self.backup=self.path.with_suffix('.json.bak')
        self.rules=rules or RelationshipRules(root)
        self.state=initial_state(self.rules,time.time());self.ready=False;self.primary_valid=False;self.warning=''

    def load(self,existing_turns=()):
        if not self.path.exists() and not self.backup.exists():
            self.state=initial_state(self.rules,time.time())
            self.state['processed_turn_ids']=[turn_id(turn) for turn in existing_turns]
            self.ready=True;return
        for path in (self.path,self.backup):
            try:
                state=validate_state(json.loads(path.read_text(encoding='utf-8')))
                self.state=state;self.ready=True;self.primary_valid=path==self.path
                self.warning='' if self.primary_valid else '關係主檔無法讀取，已恢復上次備份。'
                return
            except (OSError,ValueError,KeyError,TypeError):pass
        self.ready=False
        raise RelationshipFailure('關係檔及備份無法讀取，已保留原檔；本次暫停關係更新，聊天仍可繼續。')

    def commit(self,state):
        if not self.ready:raise RelationshipFailure('關係狀態尚未正確載入，無法保存。')
        validate_state(state);temporary=self.path.with_suffix('.json.tmp')
        try:
            self.path.parent.mkdir(parents=True,exist_ok=True)
            with temporary.open('w',encoding='utf-8',newline='\n') as output:
                json.dump(state,output,ensure_ascii=False,indent=2,allow_nan=False);output.flush();os.fsync(output.fileno())
            if self.primary_valid and self.path.exists():
                backup_temp=self.backup.with_suffix('.bak.tmp')
                shutil.copyfile(self.path,backup_temp);os.replace(backup_temp,self.backup)
            os.replace(temporary,self.path)
        except OSError:
            raise RelationshipFailure('關係變化尚未保存，請確認 Relationship 資料夾可寫入；下次聊天或查看狀態時重試。') from None
        self.state=state;self.primary_valid=True;self.warning=''

    def snapshot(self,at=None):
        return view_state(self.state,self.rules,time.time() if at is None else at) if self.ready else None

    def preview(self,message,at=None):
        if not self.ready:return None
        at=time.time() if at is None else at
        state,_=advance(self.state,self.rules,message,at)
        return view_state(state,self.rules,max(at,state['mood_at']))

    def learn(self,turns,at=None):
        if not self.ready:return 0
        at=time.time() if at is None else at
        state=copy.deepcopy(self.state);seen=set(state['processed_turn_ids']);count=0
        for index,turn in enumerate(turns,1):
            fingerprint=turn_id(turn)
            if fingerprint in seen:continue
            try:source_at=datetime.fromisoformat(turn['at']).timestamp()
            except (ValueError,TypeError):source_at=at
            effective_at=max(state['mood_at'],min(at,source_at))
            state,change=advance(state,self.rules,turn['user'],effective_at)
            change.update(at=datetime.fromtimestamp(effective_at,timezone.utc).isoformat(),
                          source={'turn_id':fingerprint,'turn_number':index,'quote':turn['user'][:200]})
            state['changes']=(state['changes']+[change])[-100:]
            state['processed_turn_ids'].append(fingerprint);seen.add(fingerprint);count+=1
        if count:self.commit(state)
        return count

    def instructions(self,view):
        if not view:return ''
        close=view['closeness'];trust=view['trust']
        style=('親切自然地交流，逐步建立熟悉感，不刻意冷淡。' if close<30 else
               '可多一些熟悉的玩笑，接續已確認的共同話題。' if close<60 else
               '可自然表現較多親暱與撒嬌，但不強迫親近、不自行宣告新關係。')
        if trust<40:style+='遇到不確定資訊時多確認，不作無根據的假設。'
        elif trust>=70:style+='可以自然承接已確認的記憶，仍尊重使用者自主。'
        if view['quiet']:style+='本輪以安靜陪伴為主，回覆簡短並選 idle。'
        data={key:round(view[key],2) for key in ('closeness','trust','valence','energy')}
        data.update(level=view['level'],mood=view['mood_label'])
        return ('\n\n【本機角色關係與短期心情】\n'+json.dumps(data,ensure_ascii=False)+'\n'+style+
                '此為角色演出狀態，只調整語氣與動作，不在日常回覆主動報分數。'
                '核心人設與使用者當下要求優先。狀態不代表真實生理或心理需求，'
                '不要要求使用者照顧數值，也不因離線使使用者愧疚。當前心情可用作動畫參考，'
                '明確話題與本輪回覆情緒優先於延續心情。')


def bias_animation(decoded,view,catalog,user_message):
    if not view:return decoded
    if view['quiet']:
        return {**decoded,'emotion':'平靜','animation_id':'idle','selection_note':'安靜陪伴'}
    if (decoded['emotion']=='平靜' and '補選' in decoded.get('selection_note','') and
            view['emotion']!='平靜'):
        result=catalog.select(decoded['text'],view['emotion'],None,user_message)
        result['selection_note']='依當前心情補選'
        return result
    return decoded
