"""Minimal Google desktop OAuth/Drive app-data transport; no inference calls."""
import base64,hashlib,hmac,json,os,re,secrets,threading,time,webbrowser
from http.server import BaseHTTPRequestHandler,HTTPServer
from pathlib import Path
from urllib.parse import urlencode,parse_qs,urlparse,quote
from urllib.request import Request,urlopen
from urllib.error import HTTPError,URLError
from chat_connection import protect
from portable_support import credentials_folder

SCOPE='https://www.googleapis.com/auth/drive.appdata openid email'
MAX_BYTES=32*1024*1024
class SyncFailure(RuntimeError):pass

def cancelled(cancel):
    if cancel and cancel.is_set():raise SyncFailure('同步已取消，未套用新的紀錄。')

def atomic_json(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix(path.suffix+'.tmp')
    with tmp.open('w',encoding='utf-8') as f:json.dump(data,f,ensure_ascii=False,allow_nan=False);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)

def request(method,url,data=None,token=None,content_type='application/json',cancel=None):
    cancelled(cancel);headers={'Accept':'application/json'}
    if token:headers['Authorization']='Bearer '+token
    if data is not None:headers['Content-Type']=content_type
    try:
        with urlopen(Request(url,data=data,headers=headers,method=method),timeout=25) as response:
            raw=response.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise SyncFailure('同步檔案超過 32 MB，請先整理歷史資料。')
        cancelled(cancel)
        return json.loads(raw) if raw else {}
    except HTTPError as error:
        if error.code in (401,):raise SyncFailure('Google 登入已失效，請重新登入 Google。') from None
        if error.code==403:raise SyncFailure('Google Drive 拒絕存取。請確認 Drive API 已啟用、OAuth 範圍及測試使用者設定。') from None
        if error.code==429:raise SyncFailure('Google Drive 暫時限制請求，請稍後再試。') from None
        raise SyncFailure('Google Drive 無法完成同步，未確認完成的操作請重新比較。') from None
    except (URLError,TimeoutError,OSError):raise SyncFailure('無法連線 Google Drive，請確認網路後重試。') from None
    except (ValueError,TypeError):raise SyncFailure('Google Drive 回覆格式無效。') from None

def form(fields,cancel=None):
    return request('POST','https://oauth2.googleapis.com/token',urlencode(fields).encode(),content_type='application/x-www-form-urlencoded',cancel=cancel)

def callback_values(query,state):
    q=parse_qs(query)
    if len(q.get('state',[]))!=1 or not hmac.compare_digest(q['state'][0],state):raise SyncFailure('Google 授權狀態驗證失敗。')
    if 'error' in q:raise SyncFailure('Google 授權已取消或遭拒絕。')
    if len(q.get('code',[]))!=1:raise SyncFailure('Google 授權未提供有效代碼。')
    return q['code'][0]

class GoogleAuth:
    def __init__(self,root):
        root=Path(root);self.folder=(credentials_folder(root) or Path(os.environ['LOCALAPPDATA'])/'BailongDesktopPet/chat')/'GoogleDrive'
        self.client_path=self.folder/'client.dpapi';self.token_path=self.folder/'token.dpapi'
    def read(self,path):
        if not path.exists():return None
        try:
            data=json.loads(protect(path.read_bytes(),decrypt=True))
            if not isinstance(data,dict):raise ValueError()
            return data
        except Exception:raise SyncFailure('Google 本機登入資料無法讀取，請重新匯入 OAuth 設定並登入。') from None
    def save(self,path,data):
        try:
            self.folder.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
            tmp.write_bytes(protect(json.dumps(data).encode()));os.replace(tmp,path)
        except Exception:raise SyncFailure('無法安全保存 Google 登入資料，請確認 Windows 使用者及資料夾權限。') from None
    def import_client(self,path):
        try:
            raw=Path(path).read_bytes()
            if len(raw)>65536:raise ValueError()
            c=json.loads(raw)['installed']
            if not isinstance(c.get('client_id'),str) or not re.fullmatch(r'[A-Za-z0-9._-]+\.apps\.googleusercontent\.com',c['client_id']):raise ValueError()
            if not isinstance(c.get('client_secret'),str) or not c['client_secret']:raise ValueError()
            client={k:c[k] for k in ('client_id','client_secret')}
        except (OSError,ValueError,TypeError,KeyError):raise SyncFailure('請匯入 Google Cloud 下載的「桌面應用程式」OAuth JSON。') from None
        try:previous=self.read(self.client_path)
        except SyncFailure:previous=None
        self.save(self.client_path,client)
        if previous!=client:self.token_path.unlink(missing_ok=True)
    def logged_in(self):
        t=self.read(self.token_path)
        return t.get('email','Google 帳號') if t else ''
    def logout(self):self.token_path.unlink(missing_ok=True)
    def login(self,cancel):
        cancelled(cancel)
        client=self.read(self.client_path)
        if not client:raise SyncFailure('請先匯入 Google 桌面應用程式 OAuth 設定。')
        verifier=secrets.token_urlsafe(48);state=secrets.token_urlsafe(32)
        challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                if urlparse(self.path).path!='/oauth2callback':self.send_error(404);return
                try:code=callback_values(urlparse(self.path).query,state)
                except SyncFailure as e:
                    values=parse_qs(urlparse(self.path).query)
                    if values.get('state')==[state] and 'error' in values:received.append(e)
                    self.send_error(400,'Authorization was cancelled or invalid');return
                received.append(code);self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers()
                self.wfile.write('Google 登入完成，可以關閉此頁回到白龍。'.encode('utf-8'))
        server=HTTPServer(('127.0.0.1',0),Handler);server.timeout=.25
        redirect='http://127.0.0.1:'+str(server.server_port)+'/oauth2callback'
        params={'client_id':client['client_id'],'redirect_uri':redirect,'response_type':'code','scope':SCOPE,'state':state,
                'code_challenge':challenge,'code_challenge_method':'S256','access_type':'offline','prompt':'consent select_account'}
        try:
            if not webbrowser.open('https://accounts.google.com/o/oauth2/v2/auth?'+urlencode(params)):raise SyncFailure('無法開啟瀏覽器，請確認預設瀏覽器設定。')
            deadline=time.monotonic()+180
            while not received:
                cancelled(cancel)
                if time.monotonic()>deadline:raise SyncFailure('Google 登入逾時，請重試。')
                server.handle_request()
        finally:server.server_close()
        if isinstance(received[0],SyncFailure):raise received[0]
        result=form({'client_id':client['client_id'],'client_secret':client['client_secret'],'code':received[0],
                     'code_verifier':verifier,'redirect_uri':redirect,'grant_type':'authorization_code'},cancel)
        cancelled(cancel)
        if not result.get('access_token') or not result.get('refresh_token'):raise SyncFailure('Google 授權未提供離線同步權限，請重新登入並同意授權。')
        if 'https://www.googleapis.com/auth/drive.appdata' not in result.get('scope','').split():raise SyncFailure('尚未授權白龍的 Drive 應用程式資料空間。')
        identity=request('GET','https://openidconnect.googleapis.com/v1/userinfo',token=result['access_token'],cancel=cancel)
        if not isinstance(identity.get('sub'),str) or not identity['sub']:raise SyncFailure('無法確認 Google 帳號。')
        t={**result,'expires_at':time.time()+float(result.get('expires_in',3600)),'sub':identity['sub'],'email':identity.get('email','Google 帳號')}
        cancelled(cancel);self.save(self.token_path,t);return t['email']
    def token(self,cancel=None):
        client=self.read(self.client_path);token=self.read(self.token_path)
        if not client or not token:raise SyncFailure('請先登入 Google。')
        if token.get('expires_at',0)<time.time()+60:
            result=form({'client_id':client['client_id'],'client_secret':client['client_secret'],'refresh_token':token['refresh_token'],'grant_type':'refresh_token'},cancel)
            if not result.get('access_token'):raise SyncFailure('Google 登入已失效，請重新登入。')
            token={**token,**result,'expires_at':time.time()+float(result.get('expires_in',3600))};cancelled(cancel);self.save(self.token_path,token)
        return token['access_token']
    def identity(self):
        token=self.read(self.token_path)
        return hashlib.sha256(token['sub'].encode()).hexdigest() if token else None

class DriveClient:
    def __init__(self,auth):self.auth=auth
    def call(self,method,url,data=None,content_type='application/json',cancel=None):
        return request(method,url,data,self.auth.token(cancel),content_type,cancel)
    def list(self,cancel=None):
        files=[];page=None
        for _ in range(100):
            params={'spaces':'appDataFolder','q':"trashed = false and appProperties has { key='bailongSync' and value='1' }",
                    'pageSize':1000,'fields':'nextPageToken,files(id,name,createdTime,size,appProperties)'}
            if page:params['pageToken']=page
            result=self.call('GET','https://www.googleapis.com/drive/v3/files?'+urlencode(params),cancel=cancel)
            files.extend(result.get('files',[]));page=result.get('nextPageToken')
            if not page:return files
        raise SyncFailure('雲端版本數過多，請先整理雲端備份。')
    def download(self,file_id,cancel=None):
        if not re.fullmatch(r'[A-Za-z0-9_-]+',file_id):raise SyncFailure('雲端檔案識別碼無效。')
        return self.call('GET','https://www.googleapis.com/drive/v3/files/'+file_id+'?alt=media',cancel=cancel)
    def create(self,snapshot,cancel=None):
        # Append-only snapshots: concurrent devices cannot overwrite each other's history.
        boundary='bailong_'+secrets.token_hex(16)
        meta={'name':'bailong-'+snapshot['profile_id']+'-'+snapshot['revision']+'.json','parents':['appDataFolder'],
              'appProperties':{'bailongSync':'1','profile':snapshot['profile_id'],'revision':snapshot['revision'],
                               'parents':','.join(snapshot['parents']),'updated':snapshot['updated_at']}}
        body=('--'+boundary+'\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+json.dumps(meta)+'\r\n--'+boundary+
              '\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n'+json.dumps(snapshot,ensure_ascii=False,allow_nan=False)+'\r\n--'+boundary+'--\r\n').encode()
        if len(body)>MAX_BYTES:raise SyncFailure('同步檔案超過 32 MB，未上傳。')
        return self.call('POST','https://www.googleapis.com/upload/drive/v3/files?uploadType=multipart&fields=id',body,'multipart/related; boundary='+boundary,cancel)
