"""In-memory screenshots; local Windows OCR before any network call."""
import base64, io, json, os, re, subprocess, sys, time, unicodedata
from pathlib import Path
from PIL import Image

class PrivacyBlocked(RuntimeError):pass

def sensitive(text):
    text=unicodedata.normalize('NFKC',text).lower()
    compact=re.sub(r'\s+','',text)
    words=('密碼','密码','帳號','账号','帳戶','账户','驗證碼','验证码','信用卡','身分證','身份证',
           '私鑰','私钥','助記詞','助记词','銀行','银行','病歷','病历','機密','机密','password','passwd',
           'passphrase','secret','apikey','api_key','accesstoken','access_token','refreshtoken','refresh_token',
           'authorization:','bearer','privatekey','seedphrase','recoverycode','verificationcode',
           'username','signin','login','登入','登錄','登录','bitwarden','1password','keepass','信用卡','薪資')
    return any(w in compact for w in words) or bool(re.search(
        r'[\w.+-]+@[\w.-]+\.[a-z]{2,}|sk-[a-z0-9_-]{12,}|eyj[a-z0-9_-]{15,}|(?:\d[ -]?){12,19}|[a-z][12]\d{8}|09\d{8}',compact))

def capture_screens():
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QByteArray,QBuffer,QIODevice
    screens=QApplication.screens()
    if not screens or len(screens)>12:raise PrivacyBlocked('螢幕擷取不可用')
    images=[]
    for screen in screens:
        pixmap=screen.grabWindow(0)
        if pixmap.isNull():raise PrivacyBlocked('螢幕擷取不完整')
        data=QByteArray();buf=QBuffer(data);buf.open(QIODevice.OpenModeFlag.WriteOnly)
        if not pixmap.save(buf,'PNG'):raise PrivacyBlocked('螢幕擷取失敗')
        images.append(bytes(data));buf.close()
    return images

def local_ocr(tiles,cancel):
    resource=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent))/'privacy_ocr.ps1'
    exe=Path(os.environ.get('SystemRoot','C:/Windows'))/'System32/WindowsPowerShell/v1.0/powershell.exe'
    payload=json.dumps({'images':[base64.b64encode(b).decode('ascii') for b in tiles]})
    proc=subprocess.Popen([str(exe),'-NoProfile','-NonInteractive','-ExecutionPolicy','Bypass','-File',str(resource)],
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,creationflags=subprocess.CREATE_NO_WINDOW)
    started=time.monotonic();first=True
    try:
        while True:
            if cancel.is_set() or time.monotonic()-started>60:raise PrivacyBlocked('敏感資訊檢查已取消或逾時')
            try:
                out,_=proc.communicate(payload.encode('utf-8') if first else None,timeout=.25);break
            except subprocess.TimeoutExpired:first=False
        result=json.loads(out.decode('utf-8-sig'))
        if proc.returncode or not result.get('ok') or len(result['texts'])!=len(tiles):raise ValueError()
        return result['texts']
    except (OSError,ValueError,KeyError):raise PrivacyBlocked('本機敏感資訊辨識不可用') from None
    finally:
        if proc.poll() is None:proc.kill();proc.communicate()

def checked_images(images,cancel,ocr=local_ocr):
    if not images:raise PrivacyBlocked('沒有快照')
    tiles=[];decoded=[];counts=[]
    for raw in images:
        im=Image.open(io.BytesIO(raw)).convert('RGB');decoded.append(im);n=0
        # Inspect original pixels, with overlap, before shrinking images for the model.
        for y in range(0,im.height,1600):
            for x in range(0,im.width,1600):
                tile=im.crop((x,y,min(x+1800,im.width),min(y+1800,im.height)))
                b=io.BytesIO();tile.save(b,format='PNG');tiles.append(b.getvalue());n+=1
        counts.append(n)
    if len(tiles)>48:raise PrivacyBlocked('快照過大，無法完整檢查')
    if cancel.is_set():raise PrivacyBlocked('已取消')
    texts=ocr(tiles,cancel)
    if len(texts)!=len(tiles):raise PrivacyBlocked('檢查不完整')
    index=0
    for count in counts:
        screen_text='\n'.join(texts[index:index+count]);index+=count
        if not screen_text.strip():raise PrivacyBlocked('無法可靠辨識畫面文字')
        if sensitive(screen_text):raise PrivacyBlocked('畫面可能含敏感資訊')
    if cancel.is_set():raise PrivacyBlocked('已取消')
    urls=[]
    for im in decoded:
        im.thumbnail((1600,1600));out=io.BytesIO();im.save(out,format='PNG')
        urls.append('data:image/png;base64,'+base64.b64encode(out.getvalue()).decode('ascii'))
    return urls
