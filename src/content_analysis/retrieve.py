"""Read-only retrieval. No cookies, browser storage, model APIs or paid endpoints."""
import importlib.util, ipaddress, json, re, shutil, socket, subprocess, queue, threading, uuid
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlencode
from urllib.request import Request, build_opener, HTTPRedirectHandler
from .store import CONFIG, now, file_hash

UA='ContentAnalysis/1.0 (personal read-only reference study)'
class RetrievalError(RuntimeError):pass

def public_url(url,dns_timeout=8):
    p=urlsplit(url)
    if p.scheme not in ('http','https') or not p.hostname or p.username or p.password or p.port not in (None,80,443):raise RetrievalError('Only public HTTP(S) source URLs are supported')
    responses=queue.Queue(maxsize=1)
    def resolve():
        try:responses.put(socket.getaddrinfo(p.hostname,p.port or (443 if p.scheme=='https' else 80),type=socket.SOCK_STREAM))
        except OSError as error:responses.put(error)
    threading.Thread(target=resolve,daemon=True).start()
    try:addresses=responses.get(timeout=dns_timeout)
    except queue.Empty:raise RetrievalError('DNS lookup timed out for '+p.hostname) from None
    if isinstance(addresses,OSError):raise RetrievalError('DNS lookup blocked or unavailable for '+p.hostname) from None
    for entry in addresses:
        if not ipaddress.ip_address(entry[4][0]).is_global:raise RetrievalError('Private network sources are not supported')
    return url
class Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        public_url(newurl)
        if req.get_header('Authorization') and urlsplit(req.full_url).netloc!=urlsplit(newurl).netloc:raise RetrievalError('Authenticated redirect refused')
        return super().redirect_request(req,fp,code,msg,headers,newurl)
def fetch(url,limit=12_000_000,token=None):
    public_url(url); headers={'User-Agent':UA}
    if token:headers['Authorization']='Bearer '+token
    try:
        with build_opener(Redirect()).open(Request(url,headers=headers),timeout=25) as r:
            data=r.read(limit+1)
            if len(data)>limit:raise RetrievalError('Source exceeds configured retrieval limit')
            return data,r.headers.get_content_type()
    except HTTPError as e:
        code=None
        if token:
            try:code=json.load(e).get('error',{}).get('code')
            except Exception:pass
        raise RetrievalError('HTTP '+str(e.code)+('; Graph code '+str(code) if token else '')) from None
    except (URLError,OSError):raise RetrievalError('Network access failed for '+urlsplit(url).hostname) from None

def module_at(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class Document(HTMLParser):
    def __init__(self):super().__init__();self.skip=0;self.parts=[];self.meta={};self.title='';self.in_title=False
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag in ('script','style','nav','footer','header','noscript'):self.skip+=1
        if tag=='meta' and a.get('content'):self.meta[a.get('property') or a.get('name')]=a['content']
        if tag=='title':self.in_title=True
        if not self.skip and tag in ('p','div','h1','h2','h3','li','br','article','section','blockquote'):self.parts.append('\n')
    def handle_endtag(self,tag):
        if tag in ('script','style','nav','footer','header','noscript'):self.skip=max(0,self.skip-1)
        if tag=='title':self.in_title=False
        if not self.skip and tag in ('p','div','li','h1','h2','h3'):self.parts.append('\n')
    def handle_data(self,data):
        if self.in_title:self.title+=data
        elif not self.skip:self.parts.append(data)
    def text(self):return '\n\n'.join(line.strip() for line in ''.join(self.parts).splitlines() if line.strip())

class LinkedInImages(HTMLParser):
    """Read attached images only inside the requested public activity card."""
    def __init__(self,activity):
        super().__init__();self.activity=activity;self.active=0;self.image_depth=0;self.urls=[];self.hidden=False;self.matched=False;self.image_text=[]
    def handle_starttag(self,tag,attrs):
        a=dict(attrs)
        if tag=='article':
            if self.active:self.active+=1
            elif a.get('data-activity-urn')=='urn:li:activity:'+self.activity:self.active=1;self.matched=True
        if not self.active:return
        if tag=='ul':
            if self.image_depth:self.image_depth+=1
            elif a.get('data-test-id')=='feed-images-content':self.image_depth=1
        if self.image_depth:
            if tag=='img':
                url=a.get('data-delayed-url') or a.get('src')
                if url:self.urls.append(url)
            if re.search(r'\+\s*\d|more images',a.get('aria-label',''),re.I):self.hidden=True
    def handle_endtag(self,tag):
        if tag=='ul' and self.image_depth:self.image_depth-=1
        if tag=='article' and self.active:self.active-=1
    def handle_data(self,data):
        if self.image_depth:
            self.image_text.append(data)
            if re.search(r'\+\s*\d',data):self.hidden=True
def asset(path,role,provenance):
    return {'path':str(Path(path).resolve()),'role':role,'sha256':file_hash(path),'provenance':provenance}
def save_media(url,folder,index=0):
    raw,ct=fetch(url,CONFIG['max_download_bytes']);ext='.mp4' if ct.startswith('video/') else '.jpg' if ct.startswith('image/') else '.bin'
    if ext=='.bin':raise RetrievalError('Unexpected media response type')
    p=folder/('media-'+str(index)+ext);p.write_bytes(raw);return p

def meta_discovery(handle,limit=50,interactive_auth=False):
    if not isinstance(handle,str) or not re.fullmatch(r'[A-Za-z0-9_.]{1,30}',handle):raise RetrievalError('Meta discovery needs a valid author handle')
    if type(limit) is not int or not 1<=limit<=50:raise RetrievalError('Meta inventory limit must be 1–50')
    public_url('https://graph.facebook.com/')
    import os
    token = os.environ.get('CONTENT_ANALYSIS_META_ACCESS_TOKEN')
    account_id = os.environ.get('CONTENT_ANALYSIS_META_ACCOUNT_ID', '')
    api_version = os.environ.get('CONTENT_ANALYSIS_META_API_VERSION', '')
    if not token or not re.fullmatch(r'\d+', account_id) or not re.fullmatch(r'v\d+\.\d+', api_version):
        raise RetrievalError('Authorized Meta access requires CONTENT_ANALYSIS_META_ACCESS_TOKEN, CONTENT_ANALYSIS_META_ACCOUNT_ID and CONTENT_ANALYSIS_META_API_VERSION')
    fields='business_discovery.username('+handle+'){username,media_count,media.limit('+str(limit)+'){id,caption,permalink,timestamp,media_type,media_url,children.limit(100){id,media_type,media_url}}}'
    d=json.loads(fetch('https://graph.facebook.com/'+api_version+'/'+account_id+'?'+urlencode({'fields':fields}),token=token)[0])
    discovery=d.get('business_discovery') or {};page=discovery.get('media') or {}
    if str(discovery.get('username','')).casefold()!=handle.casefold():raise RetrievalError('Meta profile response identity could not be established')
    return page.get('data',[]),discovery.get('media_count'),not bool(page.get('paging',{}).get('next'))

def retrieve(record,folder,handle=None):
    source=record['source'];ch=record['channel'];run_id=uuid.uuid4().hex
    result={'status':'blocked','attempts':[],'run_id':run_id,'started_at':now(),'fresh_full_media_verified':False};assets=[];text='';title=None;author=None
    provenance=lambda method:{'method':method,'retrieved_at':now(),'source':record['canonical'],'run_id':run_id,'fresh_retrieval':True}
    download_folder=folder/('download-'+run_id)
    def attempt(method,fn):
        try:
            value=fn();result['attempts'].append({'at':now(),'method':method,'ok':True});return value
        except Exception as e:
            # Avoid persisting signed URLs, tokens, subprocess output or arbitrary response bodies.
            msg=str(e) if isinstance(e,RetrievalError) else type(e).__name__
            result['attempts'].append({'at':now(),'method':method,'ok':False,'reason':msg});return None
    def media(url,i=0):
        public_folder=download_folder/'public';public_folder.mkdir(parents=True,exist_ok=True)
        p=attempt('public media GET',lambda:save_media(url,public_folder,i))
        if p:assets.append(asset(p,'media',{**provenance('public HTTP'),'media_index':i}))
    if ch=='local':
        p=Path(source).expanduser().resolve();target=folder/('source'+p.suffix.lower())
        if not target.exists() or file_hash(target)!=file_hash(p):shutil.copy2(p,target)
        assets.append(asset(target,'media' if p.suffix.lower() in ('.mp4','.mov','.webm','.mkv','.mp3','.wav','.m4a') else 'source',{'method':'local copy','original':str(p),'retrieved_at':now()}))
        if p.suffix.lower() in ('.txt','.md','.html','.htm'):
            text=p.read_text();
            if p.suffix.lower() in ('.html','.htm'):d=Document();d.feed(text);text=d.text();title=d.title
        result.update(status='full_media' if assets[0]['role']=='media' else 'text_only');return result,assets,text,title,author
    if ch=='x':
        postid=re.search(r'/status/(\d+)',record['canonical'])[1]
        d=attempt('public FxTwitter post JSON',lambda:json.loads(fetch('https://api.fxtwitter.com/status/'+postid)[0]))
        if d and d.get('tweet'):
            t=d['tweet'];text=t.get('text','');author=t.get('author',{}).get('screen_name');title=text[:100]
            result['source_identity_verified']=str(t.get('id',''))==postid
            items=t.get('media',{}).get('all')
            if items is None:items=t.get('media',{}).get('videos',[])+t.get('media',{}).get('photos',[])
            result['expected_media_count']=len(items)
            result['expected_assets']=[{'index':i,'type':v.get('type'),'duration_seconds':v.get('duration')} for i,v in enumerate(items)]
            result['asset_order_established']=len(items)==1 or 'all' in t.get('media',{})
            result['source_metadata']={'provider':'FxTwitter public post JSON','post_id':str(t.get('id')),'author':author}
            for i,v in enumerate(items):
                if v.get('url'):media(v['url'],i)
    if ch=='instagram':
        def meta():
            posts,_,_=meta_discovery(handle,limit=50)
            from .store import canonical
            for p in posts:
                if p.get('permalink') and canonical(p['permalink'])==record['canonical']:return p
            raise RetrievalError('Specific post absent from bounded Meta window (50); not proof that it is unavailable')
        d=attempt('Authorized read-only Meta discovery (50 posts)',meta)
        if d:
            text=d.get('caption','');author=handle;title=text[:100];items=d.get('children',{}).get('data') or [d]
            result['source_identity_verified']=True
            for i,p in enumerate(items):
                if p.get('media_url'):media(p['media_url'],i)
            result['expected_media_count']=len(items)
            result['asset_order_established']=True
            result['source_metadata']={'provider':'Meta Graph','post_id':d.get('id'),'media_type':d.get('media_type'),'published_at':d.get('timestamp')}
    if ch=='linkedin':
        def linkedin_images():
            activity=re.search(r'activity:(\d+)',record['canonical'])[1]
            raw,_=fetch(source);parser=LinkedInImages(activity);parser.feed(raw.decode('utf-8',errors='replace'))
            if not parser.matched:raise RetrievalError('Requested LinkedIn activity absent from public document')
            return parser
        d=attempt('LinkedIn public source-bound attached images',linkedin_images)
        if d and d.urls:
            result['source_identity_verified']=True
            result['expected_media_count']=len(d.urls) if not d.hidden and len(d.urls)<5 else None
            result['asset_order_established']=True
            result['source_metadata']={'provider':'LinkedIn public activity card','post_id':record['canonical'].rsplit(':',1)[-1],'visible_image_count':len(d.urls),'hidden_assets':d.hidden}
            for i,url in enumerate(d.urls):media(url,i)
    if ch in ('youtube','x','instagram','tiktok','linkedin') and not any(a['role']=='media' for a in assets):
        def ytdlp():
            public_url(source)
            target=download_folder/'ytdlp';target.mkdir(parents=True,exist_ok=False)
            fmt='bv*[ext=mp4][vcodec^=avc]+ba[ext=m4a]/bv*+ba/b' if ch=='youtube' else 'best[ext=mp4]/best'
            cmd=[CONFIG['python'],'-m','yt_dlp','--no-playlist','--no-progress','--no-warnings','--socket-timeout','15','--retries','0','--max-filesize',str(CONFIG['max_download_bytes']),'--write-info-json','-f',fmt,'-o',str(target/'download.%(ext)s'),source]
            if ch=='youtube':
                from .media import reference
                cmd.extend(['--ffmpeg-location',reference().ffmpeg_bin(),'--merge-output-format','mp4'])
            p=subprocess.run(cmd,capture_output=True,text=True,timeout=150)
            if p.returncode:raise RetrievalError('yt-dlp could not retrieve full media; no authentication or cookies used')
            mediafiles=[p for p in target.glob('download.*') if p.suffix in ('.mp4','.webm','.mkv','.mov')]
            if not mediafiles:raise RetrievalError('yt-dlp returned no playable full media')
            return mediafiles
        files=attempt('yt-dlp public individual post',ytdlp)
        for i,p in enumerate(files or []):assets.append(asset(p,'media',{**provenance('yt-dlp'),'media_index':i}))
        ip=download_folder/'ytdlp'/'download.info.json'
        if ip.exists():
            d=json.loads(ip.read_text());title=title or d.get('title');author=author or d.get('uploader');text=text or d.get('description','')
            from .store import canonical
            if d.get('webpage_url'):result['source_identity_verified']=canonical(d['webpage_url'])==record['canonical']
            if ch=='youtube':
                result['expected_media_count']=1
                result['expected_duration_seconds']=d.get('duration')
            if ch=='instagram' and d.get('extractor_key')=='Instagram' and d.get('_type','video')=='video':result['expected_media_count']=1
            if d.get('duration') is not None:result['expected_duration_seconds']=d['duration']
            result['source_metadata']={'provider':'yt-dlp '+str(d.get('extractor_key')),'post_id':d.get('id'),'media_type':d.get('_type','video'),'published_at':d.get('upload_date')}
            if result.get('expected_media_count')==1:result['asset_order_established']=True
            # yt-dlp output can contain expiring CDN URLs; retain only selected metadata.
            ip.unlink()
    if not text and not assets:
        raw=attempt('public document GET',lambda:fetch(source))
        if raw:
            body,ct=raw
            if ct.startswith(('video/','audio/')):
                p=folder/('source.mp4' if ct.startswith('video/') else 'source.mp3');p.write_bytes(body);assets.append(asset(p,'media',provenance('HTTP')))
                result.update(expected_media_count=1,source_identity_verified=True)
            else:
                d=Document();d.feed(body.decode('utf-8',errors='replace'));title=d.meta.get('og:title') or d.title;author=d.meta.get('author');text=d.text()
                if ch in ('instagram','linkedin','tiktok','youtube','x'):
                    text=d.meta.get('og:description') or d.meta.get('description') or ''
                    result['limitation']='Public metadata excerpt only; not the full post or media'
                elif len(text)<80 or any(w in (title or '').lower() for w in ('access denied','just a moment','sign in','log in')):text=''
    count=sum(a['role']=='media' for a in assets)
    result['status']='full_media' if count else 'text_only' if text else 'blocked'
    expected_count=result.get('expected_media_count')
    if isinstance(expected_count,int) and expected_count>count:result['status']='partial'
    return result,assets,text,title,author
