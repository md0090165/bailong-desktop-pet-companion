"""Finite animation vocabulary; model output never supplies files or commands."""
import copy
import json
import time
import re
import unicodedata
from pathlib import Path

EMOTIONS = ('平靜', '開心', '好奇', '得意', '親暱', '害羞', '小生氣', '睏倦', '興奮', '俏皮', '安慰')


def entry(animation_id, name, tags, when, avoid, cooldown):
    return {'id': animation_id, 'name': name, 'tags': tags, 'use_when': when,
            'avoid_when': avoid, 'cooldown_seconds': cooldown}


DEFAULT_CONFIG = {'version': 1, 'global_cooldown_seconds': 4, 'animations': [
    entry('01','招手／打招呼',['開心', '好奇', '平靜', '俏皮'],'一般問候、友善回應日常話題、重新見面。','不要每一輪聊天都打招呼。',8),
    entry('04','靈感',['好奇', '興奮', '平靜', '開心'],'思考、解說、聊到新話題、點子或解法。','還沒有解法時不要假裝已經解決。',8),
    entry('05','得意',['得意', '開心', '俏皮'],'被稱讚、輕微自信或可愛的得意。','嚴肅困擾或使用者低落時避免炫耀。',8),
    entry('08','貼貼',['親暱', '安慰', '害羞', '平靜'],'使用者需要安慰、表達親近或請求抱抱。','不強迫親密，不打斷專心工作。',10),
    entry('09','傲嬌',['害羞', '親暱', '俏皮', '開心'],'受到誇獎、害羞、溫和嘴硬的玩笑。','避免真正貶低、冷落或責備使用者。',8),
    entry('13','小生氣',['小生氣', '俏皮'],'對難題或不順利的事表現輕微不滿。','不對使用者發怒，不在脆弱或嚴肅情境亂用。',10),
    entry('14','睏倦',['睏倦', '平靜'],'道晚安、談到休息、角色自然打哈欠。','不要因話題無趣而打哈欠。',12),
    entry('18','慶祝',['興奮', '開心', '得意'],'完成事情、好消息、祝賀使用者。','沒有完成的事不假裝已完成。',8),
    entry('22','眷屬印記',['親暱', '害羞'],'使用者明確提到眷屬印記或邀請角色進行該儀式。','一般示好不選此動作；不自行宣告關係升級或虛構約定。',45),
    entry('25','幼龍吐息',['俏皮', '興奮', '好奇'],'輕鬆玩笑、龍族話題、使用者邀請表演吐息。','不作為威脅，不用於嚴肅求助。',8),
]}


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


class ReplyFormatFailure(RuntimeError):
    pass


class AnimationCatalog:
    def __init__(self, root):
        self.path = Path(root) / 'Animations.json'
        self.warning = ''
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        try:
            with self.path.open('rb') as source:
                raw = source.read(65537)
            if len(raw) > 65536:
                raise ValueError('too large')
            config = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=unique_object)
            self.validate(config)
            self.config = config
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            self.warning = 'Animations.json 無法讀取，已使用內建動作標籤。'
        self.entries = {item['id']: item for item in self.config['animations']}

    @staticmethod
    def validate(config):
        if config['version'] != 1 or type(config['global_cooldown_seconds']) is not int or not 4 <= config['global_cooldown_seconds'] <= 120:
            raise ValueError('invalid version or cooldown')
        items = config['animations']
        if not isinstance(items,list) or len(items) != 10:
            raise ValueError('expected ten actions')
        if {i['id'] for i in items} != {i['id'] for i in DEFAULT_CONFIG['animations']}:
            raise ValueError('unknown action id')
        for item in items:
            if (not isinstance(item['tags'],list) or not item['tags'] or
                    any(tag not in EMOTIONS for tag in item['tags']) or
                    type(item['cooldown_seconds']) is not int or not 8 <= item['cooldown_seconds'] <= 600):
                raise ValueError('invalid tags or cooldown')
            for key in ('name','use_when','avoid_when'):
                if not isinstance(item[key],str) or not 0 < len(item[key]) <= 400:
                    raise ValueError('invalid description')

    def instructions(self):
        compact = [{k:item[k] for k in ('id','name','tags','use_when','avoid_when')}
                   for item in self.entries.values()]
        return ('\n\n【回覆輸出格式與動畫選擇】\n'
                '保持上方角色口吻，最終輸出一個 JSON 物件，不附加 Markdown 或說明。'
                '格式：{"text":"給使用者看的繁體中文回覆","emotion":"好奇","animation_id":"04"}。'
                'text 是完整台詞。emotion 建議使用：' + '、'.join(EMOTIONS) + '。'
                'animation_id 從下表選一個動作 ID。一般日常對話也應優先搭配可見動作，'
                '不必等到特別事件才動；心情標籤是參考，不要求完全吻合。'
                '友善閒聊可選 01，思考或解說可選 04，被誇獎可選 05 或 09，'
                '安慰可選 08，輕鬆玩笑可選 25。沒有明確偏好時可選 04 作自然思考反應。'
                '只有使用者明確要求安靜、不播放動作或不適合動作的沉重情境才選 idle。'
                '不要自行宣告關係升級；22 仍只用於使用者明確談到眷屬印記的情境。'
                '避免用小生氣責備使用者，不用吐息威脅。動作由程式決定是否播放，'
                '不要保證已播放或宣稱已操作電腦。\n可選動作：'
                + json.dumps(compact,ensure_ascii=False))

    def accepts(self, animation_id, emotion):
        # Tags guide selection but do not veto an existing animation.
        return (isinstance(animation_id,str) and isinstance(emotion,str) and emotion in EMOTIONS and
                (animation_id == 'idle' or animation_id in self.entries))

    def normalize_id(self, value):
        if isinstance(value,dict):
            value=value.get('id',value.get('name'))
        if type(value) is int:
            value=str(value)
        if not isinstance(value,str):
            return None
        value=unicodedata.normalize('NFKC',value).strip().lower()
        if any(part in value for part in ('..','\\','://')):
            return None
        if value in ('idle','none','待機','待机','無','无','不動','不动'):
            return 'idle'
        number=re.fullmatch(r'(?:animation[_ -]?)?(?:id[:= ]*)?(\d{1,2})(?:[ :、-]+[^/\\]*)?',value)
        if number:
            candidate=number.group(1).zfill(2)
            if candidate in self.entries:return candidate
        aliases={'招手':'01','打招呼':'01','招手/打招呼':'01','靈感':'04','灵感':'04','思考':'04',
                 '得意':'05','貼貼':'08','贴贴':'08','抱抱':'08','傲嬌':'09','傲娇':'09',
                 '小生氣':'13','小生气':'13','睏倦':'14','困倦':'14','打哈欠':'14',
                 '慶祝':'18','庆祝':'18','眷屬印記':'22','眷属印记':'22','幼龍吐息':'25','幼龙吐息':'25',
                 'wave':'01','greeting':'01','idea':'04','thinking':'04','proud':'05','hug':'08',
                 'shy':'09','angry':'13','sleepy':'14','celebrate':'18','celebration':'18','breath':'25'}
        for item in self.entries.values():
            aliases[unicodedata.normalize('NFKC',item['name']).lower()]=item['id']
        return aliases.get(value)

    @staticmethod
    def normalize_emotion(value,text):
        if isinstance(value,str):
            value=unicodedata.normalize('NFKC',value).strip().lower()
            if value in EMOTIONS:return value
            aliases={'happy':'開心','开心':'開心','joy':'開心','curious':'好奇','curiosity':'好奇',
                     'proud':'得意','affectionate':'親暱','亲昵':'親暱','温柔':'親暱','溫柔':'親暱',
                     'shy':'害羞','angry':'小生氣','小生气':'小生氣','sleepy':'睏倦','困倦':'睏倦',
                     'excited':'興奮','兴奋':'興奮','playful':'俏皮','reassuring':'安慰','comfort':'安慰',
                     'neutral':'平靜','calm':'平靜','平静':'平靜','開朗':'開心','溫暖':'親暱'}
            if value in aliases:return aliases[value]
        for words,emotion in ((('晚安','打哈欠','想睡','休息吧'),'睏倦'),
                              (('抱抱','辛苦了','難過','難受','傷心','別自責'),'安慰'),
                              (('完成啦','完成了','恭喜','太棒','成功了'),'興奮'),
                              (('害羞','才、才不是','才不是'),'害羞'),
                              (('嘿嘿','欸嘿','開玩笑','吐息'),'俏皮'),
                              (('你好','早安','很高興','開心'),'開心')):
            if any(word in text for word in words):return emotion
        return '平靜'

    @staticmethod
    def wants_quiet(text,user_message=''):
        return any(word in user_message for word in ('請安靜','安靜待','不用動作','不要動','不播放動畫',
                   '不要播放','別打擾','需要專心','我要專心','先別動')) or any(
                   word in text for word in ('安靜陪','不打擾你','安靜待在','乖乖待在旁邊'))

    def fallback_action(self,emotion):
        return {'平靜':'04','開心':'01','好奇':'04','得意':'05','親暱':'08','害羞':'09',
                '小生氣':'13','睏倦':'14','興奮':'18','俏皮':'25','安慰':'08'}.get(emotion,'04')

    def alternatives(self,animation_id,emotion):
        choices={'平靜':['04','01'],'開心':['01','05','18','25'],'好奇':['04','01','25'],
                 '得意':['05','09','18'],'親暱':['08','09','01'],'害羞':['09','08','05'],
                 '小生氣':['13','04'],'睏倦':['14'],'興奮':['18','04','25','05'],
                 '俏皮':['25','09','05','01'],'安慰':['08','01']}
        return [value for value in choices.get(emotion,['04','01'])
                if value != animation_id and value in self.entries]

    def select(self,text,emotion,animation_id,user_message='',plain=False):
        mood=self.normalize_emotion(emotion,text)
        chosen=self.normalize_id(animation_id)
        note=''
        if self.wants_quiet(text,user_message):
            chosen='idle';note='安靜陪伴'
        elif chosen is None or chosen=='idle':
            chosen=self.fallback_action(mood);note='依語氣補選'
        elif chosen != animation_id or mood != emotion:
            note='已辨識動作／心情別名'
        if plain and chosen!='idle':note='純文字回覆，依語氣補選'
        return {'text':text,'emotion':mood,'animation_id':chosen,
                'selection_valid':True,'selection_note':note}

    def decode(self, raw, user_message=''):
        if not isinstance(raw,str) or not raw.strip():
            raise ReplyFormatFailure('白龍沒有收到完整台詞，請再試一次。')
        candidate = raw.strip().lstrip('\ufeff')
        if candidate.startswith('```'):
            lines = candidate.splitlines()
            if lines[0].strip().lower() == '```json' and (len(lines)<2 or lines[-1].strip() != '```'):
                raise ReplyFormatFailure('這次回覆格式不完整，請再傳送一次。')
            if lines[-1].strip() == '```' and lines[0].strip().lower() in ('```json','```'):
                candidate = '\n'.join(lines[1:-1]).strip()
        try:
            result = json.loads(candidate, object_pairs_hook=unique_object)
        except ValueError:
            if candidate.startswith(('{','[')):
                raise ReplyFormatFailure('這次回覆格式不完整，請再傳送一次。') from None
            return self.select(raw.strip(),None,None,user_message,plain=True)
        if not isinstance(result,dict):
            raise ReplyFormatFailure('這次回覆沒有完整台詞，請再傳送一次。')
        text=result.get('text',result.get('reply',result.get('message')))
        if not isinstance(text,str) or not text.strip():
            raise ReplyFormatFailure('這次回覆沒有完整台詞，請再傳送一次。')
        emotion=result.get('emotion',result.get('mood'))
        animation_id=result.get('animation_id',result.get('animation',result.get('action')))
        return self.select(text.strip(),emotion,animation_id,user_message)



class AnimationCooldown:
    def __init__(self, catalog):
        self.catalog = catalog
        self.last_played = float('-inf')
        self.per_action = {}

    def ready(self, animation_id, now=None):
        now = time.monotonic() if now is None else now
        item = self.catalog.entries.get(animation_id)
        return bool(item and now - self.last_played >= self.catalog.config['global_cooldown_seconds'] and
                    now - self.per_action.get(animation_id,float('-inf')) >= item['cooldown_seconds'])

    def mark_played(self, animation_id, now=None):
        now = time.monotonic() if now is None else now
        self.last_played = now
        self.per_action[animation_id] = now
