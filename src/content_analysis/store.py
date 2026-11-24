import contextlib, hashlib, json, os, re, sqlite3, tempfile, sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode

ROOT = Path(__file__).resolve().parents[2]
DATA_ROOT = Path(os.environ.get('CONTENT_ANALYSIS_DATA', Path.home()/'.local/share/content-analysis')).expanduser()
CONFIG = {
    'reference_script': str(Path(__file__).with_name('video_reference.py')),
    'python': sys.executable,
    'whisper_cache': os.environ.get('CONTENT_ANALYSIS_WHISPER_CACHE', str(Path.home()/'.cache/huggingface/hub')),
    'max_full_scan_seconds': 120,
    'long_sample_seconds': 30,
    'max_download_bytes': 536870912,
}
def now(): return datetime.now(timezone.utc).isoformat(timespec='seconds')
def digest(value): return hashlib.sha256(value if isinstance(value,bytes) else value.encode()).hexdigest()
def file_hash(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for chunk in iter(lambda:f.read(1048576),b''): h.update(chunk)
    return h.hexdigest()
def canonical(source):
    if re.fullmatch(r'urn:content-analysis:(?:inventory|metrics|profile-run):[a-f0-9]{32}',source):return source
    p=urlsplit(source)
    if p.scheme not in ('http','https'):
        return 'local:sha256:'+file_hash(Path(source).expanduser())
    host=(p.hostname or '').lower().removeprefix('www.'); path=p.path.rstrip('/')
    if p.username or p.password: raise ValueError('Credential-bearing URLs are not supported')
    if host in ('youtu.be','youtube.com','m.youtube.com'):
        video=path.strip('/') if host=='youtu.be' else dict(parse_qsl(p.query)).get('v') or (path.split('/')[-1] if path.startswith(('/shorts/','/embed/')) else None)
        if video and re.fullmatch(r'[\w-]{11}',video): return 'https://youtube.com/watch?v='+video
    if host in ('twitter.com','x.com','mobile.twitter.com'):
        m=re.search(r'/status/(\d+)',path)
        if m:return 'https://x.com/i/status/'+m[1]
    if host=='instagram.com':
        m=re.search(r'/(?:p|reel|reels|tv)/([\w-]+)',path)
        if m:return 'https://instagram.com/p/'+m[1]
    if host.endswith('tiktok.com'):
        m=re.search(r'/@([^/]+)/(video|photo)/(\d+)',path)
        if m:return 'https://www.tiktok.com/@'+m[1].lower()+'/'+m[2]+'/'+m[3]
    if host.endswith('linkedin.com'):
        m=re.search(r'(?:activity[-:/]|ugcPost[-:])(\d+)',source)
        if m:return 'https://linkedin.com/feed/update/urn:li:activity:'+m[1]
    query=[(k,v) for k,v in parse_qsl(p.query) if not (k.lower().startswith('utm_') or k.lower() in ('si','igsh','fbclid','gclid','ref','s','t'))]
    return urlunsplit(('https',host+(':'+str(p.port) if p.port else ''),path or '/',urlencode(sorted(query)),''))
def channel(source):
    if source.startswith('urn:content-analysis:'):return 'analysis'
    host=(urlsplit(source).hostname or '').lower().removeprefix('www.')
    for key,domains in {'youtube':['youtube.com','youtu.be'],'x':['x.com','twitter.com'],'instagram':['instagram.com'],'tiktok':['tiktok.com'],'linkedin':['linkedin.com']}.items():
        if any(host==d or host.endswith('.'+d) for d in domains):return key
    return 'web' if host else 'local'
def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(dir=path.parent,prefix='.write-')
    try:
        with os.fdopen(fd,'w') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

class Store:
    def __init__(self,root=None,read_only=False):
        self.root=Path(root or os.environ.get('CONTENT_ANALYSIS_DATA') or DATA_ROOT).resolve();self.read_only=read_only
        if read_only:
            path=self.root/'index.sqlite'
            if not path.is_file():raise ValueError('Evidence database not found: '+str(path))
            wal=Path(str(path)+'-wal')
            # Never use immutable reads while committed data may live in a WAL.
            immutable='' if wal.exists() and wal.stat().st_size else '&immutable=1'
            self.db=sqlite3.connect(path.as_uri()+'?mode=ro'+immutable,uri=True,timeout=30);self.db.row_factory=sqlite3.Row
            self.db.execute('PRAGMA query_only=ON');return
        self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'index.sqlite',timeout=30);self.db.row_factory=sqlite3.Row
        self.db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS refs(id TEXT PRIMARY KEY,canonical TEXT UNIQUE NOT NULL,channel TEXT,title TEXT,status TEXT,revision INTEGER,updated TEXT,search TEXT);
        CREATE TABLE IF NOT EXISTS revisions(id TEXT,revision INTEGER,at TEXT,record TEXT,PRIMARY KEY(id,revision));
        CREATE TABLE IF NOT EXISTS aliases(canonical TEXT PRIMARY KEY,id TEXT);
        ''')
    def folder(self,rid):
        if not re.fullmatch(r'ca-[a-f0-9]{16}',rid):raise ValueError('Invalid reference ID')
        p=self.root/'refs'/rid
        if not self.read_only:p.mkdir(parents=True,exist_ok=True)
        return p
    def require_write(self):
        if self.read_only:raise PermissionError('This operation requires approved write access to '+str(self.root))
    @contextlib.contextmanager
    def lock(self,rid):
        self.require_write()
        import fcntl
        with open(self.folder(rid)/'.lock','a') as f:
            fcntl.flock(f,fcntl.LOCK_EX)
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
    def resolve(self,source):
        if re.fullmatch(r'ca-[a-f0-9]{16}',source):return source
        c=canonical(source);row=self.db.execute('SELECT id FROM refs WHERE canonical=? UNION SELECT id FROM aliases WHERE canonical=?',(c,c)).fetchone()
        return row['id'] if row else None
    def create(self,source,title=None):
        self.require_write()
        c=canonical(source);found=self.resolve(source)
        if found:return self.get(found),True
        rid='ca-'+digest(c)[:16]
        record={'id':rid,'canonical':c,'source':source,'channel':channel(source),'title':title or source,'author':None,'created_at':now(),'retrieval':{'status':'blocked','attempts':[]},'kind':'unknown','assets':[],'coverage':{'visual':[],'audio':[],'text':[],'method':'not inspected'},'evidence':[],'observations':[],'techniques':[],'corrections':[],'related':[],'revision':0,'semantic_status':'pending'}
        self.save(record);return record,False
    def save(self,record,expected=None):
        self.require_write()
        rid=record['id']; old=self.db.execute('SELECT revision FROM refs WHERE id=?',(rid,)).fetchone();rev=old['revision'] if old else 0
        if expected is not None and expected!=rev:raise ValueError('Revision conflict; reload before saving')
        record['revision']=rev+1;record['updated_at']=now();raw=json.dumps(record,ensure_ascii=False)
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO refs VALUES(?,?,?,?,?,?,?,?)',(rid,record['canonical'],record['channel'],record['title'],record['retrieval']['status'],record['revision'],record['updated_at'],raw))
            self.db.execute('INSERT INTO revisions VALUES(?,?,?,?)',(rid,record['revision'],record['updated_at'],raw))
        atomic_json(self.folder(rid)/'record.json',record)
        return record
    def get(self,rid):
        row=self.db.execute('SELECT search FROM refs WHERE id=?',(rid,)).fetchone()
        if not row:raise ValueError('Reference not found')
        return json.loads(row['search'])
    def get_revision(self,rid,revision):
        row=self.db.execute('SELECT record FROM revisions WHERE id=? AND revision=?',(rid,revision)).fetchone()
        if not row:raise ValueError('Reference revision not found')
        return json.loads(row['record'])
    def list(self,q=''):
        rows=self.db.execute("SELECT id,title,channel,status,revision,updated FROM refs WHERE search LIKE ? ESCAPE '\\' ORDER BY updated DESC",('%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%',)).fetchall()
        return [dict(x) for x in rows]
    def add_alias(self,source,rid):
        self.require_write()
        c=canonical(source)
        existing=self.resolve(source)
        if existing and existing!=rid:raise ValueError('Alias belongs to another reference; use explicit related links')
        with self.db:self.db.execute('INSERT OR REPLACE INTO aliases VALUES(?,?)',(c,rid))
    def history(self,rid):
        return [dict(x) for x in self.db.execute('SELECT revision,at FROM revisions WHERE id=? ORDER BY revision',(rid,))]
    def close(self):self.db.close()
