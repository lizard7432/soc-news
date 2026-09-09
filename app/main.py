import asyncio
import time
import json
import math
import os
import re
import sqlite3
import threading
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse, urlencode, urlunparse, parse_qsl
from zoneinfo import ZoneInfo

import feedparser
import httpx
from bs4 import BeautifulSoup
from fastapi import FastAPI, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from .feed_settings import DEFAULT_SEARCH, FEED_HOSTS, validate_url, read_feed
from . import ml_bridge
from .dedup import decision
from .relevance import security_relevant
from .source_policy import simplified_chars, verify, original_date, resolve_source, traditional_domain

TZ = ZoneInfo('Asia/Taipei')
DATA = Path(os.getenv('DATA_DIR', 'data'))
LOCK = threading.Lock()
MODEL = None
MODEL_STATUS = '尚未載入'
SECURITY = re.compile(r'資安|資訊安全|網路安全|駭客|勒索|個資|外洩|漏洞|惡意|釣魚|入侵|病歷|隱私|網攻|零日|修補|網絡安全|資料洩漏|RCE|DDoS|CVE-', re.I)
QUERY = '(資安 OR 網路安全 OR 駭客 OR 個資外洩 OR 勒索軟體) when:2d'
DEFAULT_SOURCES = [
    {'name': 'iThome RSS', 'url': 'https://www.ithome.com.tw/rss', 'enabled': True},
    {'name': 'Google 新聞・全球資安（繁體中文）', 'url': 'https://news.google.com/rss/search?' + urlencode({'q': QUERY, 'hl': 'zh-TW', 'gl': 'TW', 'ceid': 'TW:zh-Hant'}), 'enabled': True},
]

DEFAULT_SOURCES += [
    {'name': '科技報橘 RSS', 'url': 'https://techorange.com/feed/', 'enabled': True},
    {'name': '科技新報 RSS', 'url': 'https://technews.tw/feed/', 'enabled': True},
    *[{'name': '中央社・'+label, 'url': 'https://feeds.feedburner.com/rsscna/'+category, 'enabled': True}
      for label,category in [('科技','technology'),('國際','intworld'),('社會','social')]],
    *[{'name': 'Google 新聞・'+label, 'url': 'https://news.google.com/rss/search?'+urlencode({'q': query+' when:2d','hl':'zh-TW','gl':'TW','ceid':'TW:zh-Hant'}), 'enabled':True}
      for label,query in [('漏洞與修補','(漏洞 OR CVE OR 零日 OR 修補 OR RCE)'),
                          ('攻擊與外洩','(網攻 OR 外洩 OR 入侵 OR 資料洩漏 OR 供應鏈攻擊 OR 網絡安全)')]],
]

def clean_title(title, source=''):
    """Remove feed publisher attribution, preserving substantive title hyphens."""
    title = title.replace("【即時新聞】", "").strip()
    title = re.sub(r'\s*[-–—|｜]\s*(?:CY Research|科技|政治|產經|生活|國際|政治焦點|上市櫃|鏡報|數位時代|Newtalk|股市爆料同學會|財經焦點情報站|子敏左側交易)\s*$', '', title, flags=re.I)
    parts = re.split(r'\s+[-–—]\s+|\s*[|｜]\s*', title.strip())
    if len(parts) < 2:
        return title.strip()
    tail = parts[-1]
    publisher = re.search(r'新聞|新聞網|PChome|太報|中央社|自由時報|聯合報|iThome|TechNews|科技新報|INSIDE|資安人|Yahoo|LINE TODAY|MoneyDJ|鉅亨|\.(com|tw)\b', tail, re.I)
    if source.startswith('Google') or publisher:
        return clean_title(title[:title.rfind(tail)].rstrip(' -–—|｜\t\u00a0'))
    return title.strip()


def event_title(c, event):
    article = c.execute('SELECT source FROM articles WHERE event_id=? AND title=? LIMIT 1',
                        (event['id'], event['title'])).fetchone()
    return clean_title(event['title'], article['source'] if article else '')

def now():
    return datetime.now(timezone.utc).isoformat()

@contextmanager
def db():
    DATA.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATA / 'soc.db', timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def init():
    with db() as c:
        c.execute('PRAGMA journal_mode=WAL')
        c.executescript('''
        CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,title TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'pending', created TEXT NOT NULL, updated TEXT NOT NULL, exported TEXT, candidate INTEGER, score REAL DEFAULT 0, kind TEXT DEFAULT 'new');
        CREATE TABLE IF NOT EXISTS articles(id INTEGER PRIMARY KEY,event_id INTEGER NOT NULL REFERENCES events(id),url TEXT UNIQUE NOT NULL,title TEXT NOT NULL,summary TEXT,published TEXT, collected TEXT NOT NULL,source TEXT NOT NULL,vector TEXT);
        CREATE TABLE IF NOT EXISTS runs(id INTEGER PRIMARY KEY,started TEXT,finished TEXT,source TEXT,status TEXT,count INTEGER,error TEXT);
        CREATE TABLE IF NOT EXISTS audit(id INTEGER PRIMARY KEY,at TEXT,actor TEXT,action TEXT,detail TEXT);
        CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY,value TEXT);
        CREATE TABLE IF NOT EXISTS short_links(url TEXT PRIMARY KEY,short_url TEXT NOT NULL);
        ''')
        c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('sources', json.dumps(DEFAULT_SOURCES)))
        c.execute('INSERT OR IGNORE INTO settings VALUES (?,?)', ('keywords', json.dumps({'search': DEFAULT_SEARCH, 'exclude': []})))
        sources=json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        for source in sources:
            if source['name']=='Google 新聞・台灣資安':
                source.update(name=DEFAULT_SOURCES[1]['name'],url=DEFAULT_SOURCES[1]['url'])
        known={source['url'] for source in sources}
        sources.extend(dict(source) for source in DEFAULT_SOURCES if source['url'] not in known)
        c.execute("UPDATE settings SET value=? WHERE key='sources'",(json.dumps(sources),))
        c.execute("UPDATE events SET kind='new' WHERE kind='review_scope'")
        columns={r[1] for r in c.execute('PRAGMA table_info(articles)')}
        for name,definition in [('source_status',"TEXT NOT NULL DEFAULT 'unverified'"),('source_reason',"TEXT DEFAULT '尚未檢查原文'"),('source_checked','TEXT'),('title_vector','TEXT'),('original_published','TEXT'),('resolved_url','TEXT')]:
            if name not in columns:
                c.execute(f'ALTER TABLE articles ADD COLUMN {name} {definition}')
        if 'original_published' not in columns:
            c.execute('UPDATE articles SET source_checked=NULL')
        if 'resolved_url' not in columns:
            c.execute("UPDATE articles SET source_checked=NULL WHERE source_status IN ('unverified','rejected')")
        policy_version='2026-09-06-domain-v4'
        previous=c.execute("SELECT value FROM settings WHERE key='source_policy_version'").fetchone()
        if not previous or previous[0]!=policy_version:
            c.execute("UPDATE articles SET source_checked=NULL WHERE source_status IN ('unverified','rejected')")
            c.execute("INSERT OR REPLACE INTO settings VALUES ('source_policy_version',?)",(policy_version,))
        ml_bridge.init(c)
        c.execute('CREATE TABLE IF NOT EXISTS distinct_events(a INTEGER,b INTEGER,reason TEXT,PRIMARY KEY(a,b))')

def user():
    return 'internal-user'

def audit(c, actor, action, detail):
    c.execute('INSERT INTO audit(at,actor,action,detail) VALUES (?,?,?,?)', (now(), actor, action, json.dumps(detail, ensure_ascii=False)))

def clean(s):
    return BeautifulSoup(s or '', 'html.parser').get_text(' ', strip=True)[:6000]

def canonical(url):
    p = urlparse(url)
    if p.scheme not in ('http', 'https') or not p.hostname:
        raise ValueError('非 HTTP(S) 新聞連結')
    return urlunparse((p.scheme, p.netloc.lower(), p.path, '', urlencode([(k,v) for k,v in parse_qsl(p.query) if not k.startswith('utm_') and k not in ('fbclid','gclid')]), ''))

def lexical(a, b):
    def grams(t):
        t = re.sub(r'\W', '', t.lower().replace('臺', '台').replace('駭客', '黑客').replace('洩漏', '外洩'))
        return {t[i:i+2] for i in range(len(t)-1)}
    x, y = grams(a), grams(b)
    return len(x & y) / max(1, len(x | y))

def similarity(a, b):
    return sum(x*y for x,y in zip(a,b)) / max(1e-12, math.sqrt(sum(x*x for x in a)*sum(y*y for y in b)))

def vector(text):
    if MODEL is None:
        return None
    return next(iter(MODEL.embed([text[:3000]]))).tolist()

def load_model():
    global MODEL, MODEL_STATUS
    if os.getenv('SEMANTIC_ENABLED', 'true').lower() != 'true':
        MODEL_STATUS = '已停用語意模型；只做文字比對，改寫報導需人工確認'
        return
    try:
        MODEL_STATUS = '下載／載入本機多語語意模型中'
        from fastembed import TextEmbedding
        MODEL = TextEmbedding(model_name='sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2', cache_dir=os.getenv('MODEL_CACHE', 'models'), threads=2)
        MODEL_STATUS = '本機多語語意模型已啟用'
    except Exception as e:
        MODEL_STATUS = '語意模型載入失敗；已降級文字比對：' + type(e).__name__

def ingest(c, title, summary, url, published, source):
    # No conversion: original Simplified Chinese is not eligible.
    if len(re.findall(r'[\u4e00-\u9fff]',title)) < 4:
        return False
    if not traditional_domain(url) and urlparse(url).hostname!='news.google.com' and simplified_chars(title+' '+summary):
        return False
    url = canonical(url)
    if c.execute('SELECT 1 FROM articles WHERE url=?', (url,)).fetchone():
        return False
    text = title + ' ' + summary
    if excluded(c, title, summary) or not security_relevant(title,summary):
        return False
    v = vector(text)
    rows = c.execute('''SELECT a.*,e.exported,e.status FROM articles a JOIN events e ON e.id=a.event_id
        WHERE a.collected>=? ORDER BY a.id DESC LIMIT 500''', ((datetime.now(timezone.utc)-timedelta(days=14)).isoformat(),)).fetchall()
    best, score = None, 0.0
    matching = None
    matching_score = 0.0
    matching_reason = ''
    for row in rows:
        s = similarity(v, json.loads(row['vector'])) if v and row['vector'] else lexical(text, row['title']+' '+row['summary'])
        semantic = bool(v and row['vector'])
        allowed, reason = decision(title,summary,published,row,s,semantic,float(os.getenv('AUTO_MERGE_THRESHOLD','0.94')) if semantic else 0.90)
        if allowed and row['status'] != 'excluded' and s > matching_score:
            matching, matching_score, matching_reason = row, s, reason
        if s > score:
            best, score = row, s
    if matching is not None:
        event_id = matching['event_id']
        audit(c, 'collector', 'auto_merge', {'event_id': event_id, 'url': url, 'score': matching_score, 'reason': matching_reason})
    else:
        candidate = best['event_id'] if best is not None and score >= (0.82 if v else 0.28) else None
        stamp = now()
        cur = c.execute('INSERT INTO events(title,status,created,updated,candidate,score,kind) VALUES (?,?,?,?,?,?,?)',
            (title, 'pending', stamp, stamp, candidate, score if candidate else 0, 'review_update' if candidate else 'new'))
        event_id = cur.lastrowid
    c.execute('INSERT INTO articles(event_id,url,title,summary,published,collected,source,vector) VALUES (?,?,?,?,?,?,?,?)',
        (event_id,url,title,summary,published,now(),source,json.dumps(v) if v else None))
    return True

def policy_review():
    with db() as c:
        rows=[dict(r) for r in c.execute("SELECT id,url,published,source,resolved_url FROM articles WHERE source_checked IS NULL OR source_checked<?",((datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(),))]
    checked=[]
    for row in rows:
        resolved=row['resolved_url']
        resolution_reason=''
        if not resolved and urlparse(row['url']).hostname=='news.google.com':
            resolved,resolution_reason=resolve_source(row['url'])
        target=resolved or row['url']
        status,reason=('unverified',resolution_reason) if resolution_reason else verify(target)
        published=original_date(target,row['published'] if row['source']=='iThome RSS' else None) if status=='verified' else None
        if status=='verified' and published is None:
            reason+='；發布時間未能確認，禁止分享'
        checked.append((status,reason,now(),published,resolved,row['id']))
    # Title-only vectors prevent long summaries from hiding paraphrased titles.
    with db() as c:
        c.executemany('UPDATE articles SET source_status=?,source_reason=?,source_checked=?,original_published=?,resolved_url=? WHERE id=?',checked)
        rows=[dict(r) for r in c.execute('SELECT * FROM articles ORDER BY id DESC LIMIT 500')]
        for row in rows:
            if not row['title_vector'] and MODEL is not None:
                from .dedup import headline
                v=vector(headline(row['title']))
                row['title_vector']=json.dumps(v)
                c.execute('UPDATE articles SET title_vector=? WHERE id=?',(row['title_vector'],row['id']))
        active={r['id']:dict(r) for r in c.execute("SELECT * FROM events WHERE status!='excluded'")}
        for i,a in enumerate(rows):
            for b in rows[i+1:]:
                newer,older=max(a['event_id'],b['event_id']),min(a['event_id'],b['event_id'])
                if newer==older or newer not in active or older not in active:
                    continue
                if c.execute('SELECT 1 FROM distinct_events WHERE a=? AND b=?',(older,newer)).fetchone():
                    continue
                if a['published'] and b['published'] and abs((datetime.fromisoformat(a['published'])-datetime.fromisoformat(b['published'])).total_seconds())>7*86400:
                    continue
                from .dedup import headline
                score=similarity(json.loads(a['title_vector']),json.loads(b['title_vector'])) if a['title_vector'] and b['title_vector'] else 0
                lexical_score=lexical(headline(a['title']),headline(b['title']))
                # Broad detection is intentional: uncertain matches are held for review, not merged.
                if score>=.72 or lexical_score>=.23:
                    e=active[newer]
                    if not e['candidate'] or score>e['score']:
                        c.execute("UPDATE events SET candidate=?,score=?,kind='review_update' WHERE id=?",(older,max(score,lexical_score),newer))
                        e['candidate'],e['score']=older,max(score,lexical_score)

def safe_article(c,event_id,start=None,end=None):
    rows=c.execute("SELECT * FROM articles WHERE event_id=? AND source_status='verified' AND original_published IS NOT NULL AND source_checked>=? ORDER BY original_published,id",(event_id,(datetime.now(timezone.utc)-timedelta(hours=24)).isoformat())).fetchall()
    for row in rows:
        if excluded(c, row['title'], row['summary'] or '') or not security_relevant(row['title'],row['summary'] or ''): continue
        cfg = ml_bridge.config(c)
        if cfg['enabled']:
            analysis = c.execute("SELECT result FROM ml_articles WHERE article_id=? AND status='done' AND service=?",(row['id'],cfg['url'])).fetchone()
            verdict = json.loads(analysis[0]) if analysis and analysis[0] else {}
            if verdict.get('duplicate_decision') != 'unique': continue
        elif excluded(c, row['title'], row['summary'] or '') or not security_relevant(row['title'],row['summary'] or ''): continue
        published=datetime.fromisoformat(row['original_published'])
        if published>datetime.now(timezone.utc): continue
        if start is not None and not (start<published<=end): continue
        result=dict(row)
        result['url']=row['resolved_url'] or row['url']
        return result
    return None


def collect():
    if not LOCK.acquire(blocking=False):
        return
    try:
        with db() as c:
            sources = json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
            keywords = json.loads(c.execute("SELECT value FROM settings WHERE key='keywords'").fetchone()[0])
        google_done = False
        for src in sources:
            if not src['enabled']:
                continue
            started, count, error = now(), 0, ''
            try:
                url = src['url']
                if urlparse(url).hostname == 'news.google.com':
                    if google_done or not keywords['search']:
                        continue
                    google_done = True
                    query = '(' + ' OR '.join(chr(34)+k+chr(34) for k in keywords['search']) + ') when:2d'
                    url = 'https://news.google.com/rss/search?' + urlencode({'q': query, 'hl': 'zh-TW', 'gl': 'TW', 'ceid': 'TW:zh-Hant'})
                feed = read_feed(url)
                if not feed.entries:
                    raise ValueError('來源未回傳新聞項目，請檢查 RSS 格式或來源狀態')
                with db() as c:
                    for item in feed.entries[:150]:
                        if not item.get('link'):
                            continue
                        published = None
                        t = item.get('published_parsed')
                        if t:
                            published = datetime(*t[:6], tzinfo=timezone.utc).isoformat()
                            if datetime.fromisoformat(published) < datetime.now(timezone.utc)-timedelta(days=3):
                                continue
                        count += int(ingest(c, clean(item.get('title','')), clean(item.get('summary','')), item.link, published, src['name']))
            except Exception as e:
                error = str(e)[:400]
                count = 0  # The source transaction rolls back on failure.
            with db() as c:
                c.execute('INSERT INTO runs(started,finished,source,status,count,error) VALUES (?,?,?,?,?,?)', (started, now(),src['name'],'error' if error else 'ok',count,error))
        policy_review()
    finally:
        LOCK.release()

async def scheduler():
    await asyncio.to_thread(load_model)
    while True:
        try:
            await asyncio.to_thread(collect)
        except Exception as e:
            with db() as c:
                c.execute('INSERT INTO runs(started,finished,source,status,count,error) VALUES (?,?,?,?,?,?)',(now(),now(),'排程','error',0,str(e)[:400]))
        # Wall-clock hour boundaries in Taipei; restart performs catch-up immediately.
        t = datetime.now(TZ)
        next_hour = t.replace(minute=0,second=0,microsecond=0)+timedelta(hours=1)
        await asyncio.sleep((next_hour-t).total_seconds())

async def ml_scheduler():
    while True:
        try:
            await asyncio.to_thread(ml_bridge.run, db, now)
        except Exception:
            pass
        await asyncio.sleep(60)

async def ml_result_scheduler():
    while True:
        try:
            with db() as c:
                cfg = ml_bridge.config(c)
            await asyncio.to_thread(ml_bridge.sync_results, db, now, cfg)
        except Exception:
            pass
        await asyncio.sleep(10)

@asynccontextmanager
async def lifespan(app):
    init()
    shifts()
    task = asyncio.create_task(scheduler())
    ml_task = asyncio.create_task(ml_scheduler())
    result_task = asyncio.create_task(ml_result_scheduler())
    yield
    task.cancel()
    ml_task.cancel()
    result_task.cancel()
    await asyncio.gather(task, ml_task, result_task, return_exceptions=True)

app = FastAPI(title='資安新聞交班台', lifespan=lifespan)

@app.middleware('http')
async def browser_protection(request, call_next):
    if request.method not in ('GET','HEAD','OPTIONS'):
        origin=request.headers.get('origin')
        if request.headers.get('sec-fetch-site')=='cross-site' or (origin and urlparse(origin).netloc != request.headers.get('host')):
            return JSONResponse({'detail':'不接受跨站操作'},status_code=403)
    response=await call_next(request)
    response.headers['X-Content-Type-Options']='nosniff'
    response.headers['X-Frame-Options']='DENY'
    response.headers['Referrer-Policy']='no-referrer'
    response.headers['Cache-Control']='no-store'
    response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    return response

@app.get('/health')
def health():
    return {'ok': True}

@app.get('/')
def home():
    return FileResponse(Path(__file__).with_name('index.html'))

@app.get('/api/state')
def state(actor=Depends(user)):
    with db() as c:
        events = [dict(r) for r in c.execute('SELECT * FROM events WHERE EXISTS (SELECT 1 FROM articles WHERE event_id=events.id) ORDER BY id DESC LIMIT 300')]
        for e in events:
            e['articles'] = [dict(r) for r in c.execute('SELECT id,title,url,summary,published,collected,source,source_status,source_reason,original_published,resolved_url FROM articles WHERE event_id=? ORDER BY id', (e['id'],))]
            e['title']=event_title(c,e)
            for article in e['articles']:
                ml = c.execute('SELECT status,result,error,analyzed_at,service FROM ml_articles WHERE article_id=?',(article['id'],)).fetchone()
                article['ml'] = dict(ml) if ml else None
                if article['ml'] and article['ml']['result']:
                    article['ml']['result'] = json.loads(article['ml']['result'])
                article['discovery_url']=article['url']
                article['url']=article['resolved_url'] or article['url']
                article['title']=clean_title(article['title'],article['source'])
            source=safe_article(c,e['id'])
            e['share_published']=source['original_published'] if source else None
            e['share_url']=source['url'] if source else None
            e['safe_to_share']=bool(source and e['status']!='excluded' and not e['candidate'])
            e['automatic']=e['status']=='pending' and e['safe_to_share']
            if e['automatic']: e['status']='approved'
        runs = [dict(r) for r in c.execute('SELECT * FROM runs ORDER BY id DESC LIMIT 20')]
    return {'events': events, 'runs': runs, 'collecting': LOCK.locked(), 'model': MODEL_STATUS,
            'shifts': shifts(), 'now': datetime.now(TZ).isoformat()}

@app.post('/api/collect')
async def trigger(actor=Depends(user)):
    if LOCK.locked():
        raise HTTPException(409, '蒐集中，請稍候')
    asyncio.create_task(asyncio.to_thread(collect))
    return {'ok': True}

class Edit(BaseModel):
    status: str
    title: str = Field(min_length=1, max_length=500)
    kind: str = 'new'
    distinct_reason: str = Field(default='',max_length=1000)

@app.patch('/api/events/{event_id}')
def edit(event_id: int, body: Edit, actor=Depends(user)):
    if body.status not in ('pending','approved','excluded') or body.kind not in ('new','update','review_update','review_scope'):
        raise HTTPException(400,'狀態錯誤')
    with db() as c:
        old = c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
        if not old:
            raise HTTPException(404,'事件不存在')
        if body.status=='approved' and (not safe_article(c,event_id)):
            raise HTTPException(400,'原始來源或發布時間尚未確認，不能列入交班')
        if old['candidate'] and body.status=='approved':
            if len(body.distinct_reason.strip())<10:
                raise HTTPException(400,'疑似重複：請合併事件，或填寫至少10字的不同事件／實質更新理由')
            a,b=sorted([event_id,old['candidate']])
            c.execute('INSERT OR REPLACE INTO distinct_events VALUES (?,?,?)',(a,b,body.distinct_reason.strip()))
            c.execute('UPDATE events SET candidate=NULL WHERE id=?',(event_id,))
        updated = now() if body.kind == 'update' and old['kind'] != 'update' else old['updated']
        exported = None if body.kind == 'update' and old['kind'] != 'update' else old['exported']
        c.execute('UPDATE events SET title=?,status=?,kind=?,updated=?,exported=? WHERE id=?',(body.title,body.status,body.kind,updated,exported,event_id))
        audit(c,actor,'edit',{'before':dict(old),'after':body.model_dump()})
    return {'ok':True}

class Merge(BaseModel):
    target_id: int

@app.post('/api/events/{event_id}/merge')
def merge(event_id: int, body: Merge, actor=Depends(user)):
    if event_id == body.target_id:
        raise HTTPException(400,'不可合併至自己')
    with db() as c:
        if c.execute('SELECT count(*) FROM events WHERE id IN (?,?)',(event_id,body.target_id)).fetchone()[0] != 2:
            raise HTTPException(404,'事件不存在')
        ids = [r[0] for r in c.execute('SELECT id FROM articles WHERE event_id=?',(event_id,))]
        c.execute('UPDATE articles SET event_id=? WHERE event_id=?',(body.target_id,event_id))
        c.execute('UPDATE events SET candidate=? WHERE candidate=?',(body.target_id,event_id))
        c.execute('UPDATE events SET candidate=NULL WHERE id=? AND candidate=?',(body.target_id,body.target_id))
        audit(c,actor,'merge',{'from':event_id,'to':body.target_id,'articles':ids})
    return {'ok':True}

@app.post('/api/articles/{article_id}/split')
def split(article_id: int, actor=Depends(user)):
    with db() as c:
        a = c.execute('SELECT * FROM articles WHERE id=?',(article_id,)).fetchone()
        if not a:
            raise HTTPException(404,'新聞不存在')
        stamp=now()
        cur=c.execute('INSERT INTO events(title,created,updated) VALUES (?,?,?)',(a['title'],stamp,stamp))
        c.execute('UPDATE articles SET event_id=? WHERE id=?',(cur.lastrowid,article_id))
        audit(c,actor,'split',{'article':article_id,'from':a['event_id'],'to':cur.lastrowid})
    return {'ok':True}

def shifts():
    hours = [int(v) for v in os.getenv('SHIFT_REPORT_HOURS','11,19,3').split(',')]
    names = os.getenv('SHIFT_NAMES','日班,小夜班,大夜班').split(',')
    if len(hours)!=3 or len(names)!=3 or len(set(hours))!=3 or not all(0<=h<24 for h in hours) or any((hours[(i+1)%3]-hours[i])%24!=8 for i in range(3)):
        raise ValueError('SHIFT_REPORT_HOURS 必須是依班別順序、間隔8小時的三個產出時刻；SHIFT_NAMES 必須是三個班名')
    return [{'hour':h,'start_hour':(h-8)%24,'day_offset':1 if h<8 else 0,'name':n} for h,n in zip(hours,names)]

class Export(BaseModel):
    date: str
    shift: int = Field(ge=0,le=2)
    ids: list[int] = Field(default_factory=list, max_length=300)
    confirm: bool = False
    start: datetime | None = None
    end: datetime | None = None

@app.post('/api/export')
def export(body: Export, actor=Depends(user)):
    shift=shifts()[body.shift]
    try:
        day=datetime.strptime(body.date,'%Y-%m-%d').replace(tzinfo=TZ)
    except ValueError:
        raise HTTPException(400,'日期格式錯誤')
    end=day.replace(hour=shift['hour'])+timedelta(days=shift['day_offset'])
    start=end-timedelta(hours=8)
    if (body.start is None) != (body.end is None):
        raise HTTPException(400,'請同時設定開始與結束時間')
    if body.start is not None:
        start=body.start.replace(tzinfo=TZ) if body.start.tzinfo is None else body.start.astimezone(TZ)
        end=body.end.replace(tzinfo=TZ) if body.end.tzinfo is None else body.end.astimezone(TZ)
        if start>=end:
            raise HTTPException(400,'結束時間必須晚於開始時間')
    lines=[f'{day.year-1911}年{day.month}月{day.day}日-{shift["name"]}-資安相關新聞列表：','']
    included=[]
    with db() as c:
        for event_id in dict.fromkeys(body.ids or [r[0] for r in c.execute("SELECT id FROM events WHERE status!='excluded' AND exported IS NULL ORDER BY id DESC")]):
            e=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
            if not e or e['status']=='excluded' or e['exported'] or e['candidate']:
                continue
            a=safe_article(c,event_id,start,end)
            if not a:
                continue
            lines.extend([('【事件更新】' if e['kind']=='update' else '')+event_title(c,e),a['url'],''])
            included.append(event_id)
        if not included:
            lines.append('本班尚無通過自動檢查且尚未交班的新聞。')
        content='\n'.join(lines).strip()+'\n'
        if body.confirm and included:
            stamp=now()
            c.executemany('UPDATE events SET exported=? WHERE id=?',[(stamp,i) for i in included])
            audit(c,actor,'export',{'date':body.date,'shift':shift['name'],'ids':included,'text':content,'start':start.isoformat(),'end':end.isoformat()})
    return {'text':content,'ids':included,'start':start.isoformat(),'end':end.isoformat()}

@app.get('/api/audit')
def history(actor=Depends(user)):
    with db() as c:
        return [dict(r) for r in c.execute('SELECT * FROM audit ORDER BY id DESC LIMIT 200')]


def excluded(c, title, summary):
    row = c.execute("SELECT value FROM settings WHERE key='keywords'").fetchone()
    terms = json.loads(row[0])['exclude'] if row else []
    return any(term.casefold() in (title+' '+summary).casefold() for term in terms)

@app.get('/api/settings')
def get_settings():
    with db() as c:
        result = {r['key']: json.loads(r['value']) for r in c.execute("SELECT * FROM settings WHERE key IN ('sources','keywords')")}
    result['supported_hosts'] = FEED_HOSTS
    return result

class SourceInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    url: str = Field(min_length=1, max_length=2000)

@app.post('/api/sources/test')
def test_source(body: SourceInput):
    try:
        feed = read_feed(body.url.strip())
        return {'count': len(feed.entries), 'titles': [clean(x.get('title','')) for x in feed.entries[:3]]}
    except Exception as e:
        raise HTTPException(400, str(e)[:300])

@app.post('/api/sources')
def add_source(body: SourceInput):
    name, url = body.name.strip(), body.url.strip()
    if not name: raise HTTPException(400, '請填寫來源名稱')
    try: validate_url(url)
    except ValueError as e: raise HTTPException(400, str(e))
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        sources = json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        if any(x['url'] == url or x['name'] == name for x in sources):
            raise HTTPException(409, '此來源名稱或網址已存在')
        if len(sources) >= 50: raise HTTPException(400, '最多50個來源')
        sources.append({'name': name, 'url': url, 'enabled': True})
        c.execute("UPDATE settings SET value=? WHERE key='sources'", (json.dumps(sources),))
        audit(c, 'internal-user', 'add_source', {'name': name, 'url': url})
    return {'ok': True}

class SourceToggle(BaseModel):
    url: str
    enabled: bool

@app.post('/api/sources/toggle')
def toggle_source(body: SourceToggle):
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        sources = json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        match = next((x for x in sources if x['url'] == body.url), None)
        if match is None: raise HTTPException(404, '找不到來源')
        match['enabled'] = body.enabled
        c.execute("UPDATE settings SET value=? WHERE key='sources'", (json.dumps(sources),))
        audit(c, 'internal-user', 'toggle_source', body.model_dump())
    return {'ok': True}

class KeywordChange(BaseModel):
    kind: str
    term: str = Field(min_length=1, max_length=40)
    remove: bool = False

@app.post('/api/keywords')
def change_keyword(body: KeywordChange):
    term = body.term.strip()
    if body.kind not in ('search','exclude') or not term or any(x in term for x in ['"', chr(10), chr(13)]):
        raise HTTPException(400, '請填寫有效關鍵字（最多40字，不含雙引號或換行）')
    with db() as c:
        c.execute('BEGIN IMMEDIATE')
        keywords = json.loads(c.execute("SELECT value FROM settings WHERE key='keywords'").fetchone()[0])
        terms = keywords[body.kind]
        if body.remove:
            keywords[body.kind] = [x for x in terms if x.casefold() != term.casefold()]
        elif not any(x.casefold() == term.casefold() for x in terms):
            if len(terms) >= 50: raise HTTPException(400, '每組最多50個關鍵字')
            terms.append(term)
        c.execute("UPDATE settings SET value=? WHERE key='keywords'", (json.dumps(keywords),))
        audit(c, 'internal-user', 'keyword_change', body.model_dump())
    return {'ok': True}


SHORT_LOCK = threading.Lock()
SHORT_RETRY_AFTER = {'spoo.me':0.0,'is.gd':0.0}

class ShortenInput(BaseModel):
    ids: list[int] = Field(max_length=50)

@app.post('/api/short-links')
def short_links(body: ShortenInput):
    global SHORT_RETRY_AFTER
    if not SHORT_LOCK.acquire(blocking=False):
        raise HTTPException(409, '正在產生短網址，請稍後再試')
    try:
        links, failed, errors = {}, 0, []
        with db() as c:
            urls=[]
            for event_id in dict.fromkeys(body.ids):
                event=c.execute('SELECT * FROM events WHERE id=?',(event_id,)).fetchone()
                if not event or event['exported'] or event['status']=='excluded' or event['candidate']: continue
                article=safe_article(c,event_id)
                if article and len(article['url'])>80: urls.append(article['url'])
        deadline=time.monotonic()+25
        with httpx.Client(timeout=5, follow_redirects=False) as client:
            for url in dict.fromkeys(urls):
                with db() as c:
                    cached=c.execute('SELECT short_url FROM short_links WHERE url=?',(url,)).fetchone()
                if cached:
                    links[url]=cached[0]; continue
                reasons=[]
                for provider,endpoint in [('spoo.me','https://spoo.me/'),('is.gd','https://is.gd/create.php')]:
                    if time.monotonic()>deadline:
                        reasons.append('整批處理時間已到'); break
                    if time.monotonic()<SHORT_RETRY_AFTER[provider]:
                        reasons.append(provider+' 暫時冷卻中'); continue
                    try:
                        payload={'url':url}
                        if provider=='is.gd': payload['format']='json'
                        r=client.post(endpoint,data=payload,headers={'Accept':'application/json'})
                        r.raise_for_status()
                        data=r.json()
                        if data.get('errorcode'):
                            if str(data['errorcode'])=='3': SHORT_RETRY_AFTER[provider]=time.monotonic()+60
                            raise ValueError('服務拒絕網址，代碼 '+str(data['errorcode']))
                        short=data.get('short_url','')
                        if short.startswith('http://'+provider+'/'): short='https://'+short[7:]
                        if not re.fullmatch(r'https://'+re.escape(provider)+r'/[A-Za-z0-9_-]+',short):
                            raise ValueError('服務回傳格式不符')
                        with db() as c:
                            c.execute('INSERT OR REPLACE INTO short_links VALUES (?,?)',(url,short))
                        links[url]=short
                        break
                    except Exception as error:
                        if isinstance(error,httpx.HTTPStatusError):
                            code=error.response.status_code
                            reason='HTTP '+str(code)
                            if code==429 or code>=500: SHORT_RETRY_AFTER[provider]=time.monotonic()+60
                        elif isinstance(error,httpx.TimeoutException):
                            reason='連線逾時'; SHORT_RETRY_AFTER[provider]=time.monotonic()+60
                        elif isinstance(error,httpx.RequestError):
                            reason='連線失敗'; SHORT_RETRY_AFTER[provider]=time.monotonic()+60
                        elif isinstance(error,ValueError): reason=str(error) if str(error).startswith('服務') else '回應不是有效 JSON'
                        else: reason='處理失敗'
                        reasons.append(provider+'：'+reason)
                if url not in links:
                    failed+=1
                    errors.append({'url':url,'reason':'；'.join(reasons)})
        return {'links':links,'failed':failed,'errors':errors}
    finally:
        SHORT_LOCK.release()

class MLSettings(BaseModel):
    enabled: bool = False
    url: str = Field(default='', max_length=500)
    token: str | None = Field(default=None, max_length=1000)

@app.get('/api/ml/settings')
def ml_settings():
    with db() as c:
        return ml_bridge.config(c, public=True)

@app.post('/api/ml/settings')
def save_ml(body: MLSettings):
    try:
        url = ml_bridge.validate_endpoint(body.url) if body.url else ''
    except ValueError as error:
        raise HTTPException(400, str(error))
    with db() as c:
        old = ml_bridge.config(c)
        token = body.token if body.token is not None else (old['token'] if old['url'] == url else '')
        if body.enabled and (not url or not token):
            raise HTTPException(400, '啟用前請填寫 ML 位址與 Token；更換位址須重新輸入 Token')
        c.execute("INSERT OR REPLACE INTO settings VALUES('ml',?)", (json.dumps({'enabled': body.enabled, 'url': url, 'token': token}),))
        audit(c, 'internal-user', 'ml_settings', {'enabled': body.enabled, 'url': url})
    return ml_settings()

@app.post('/api/ml/test')
def test_ml():
    with db() as c:
        cfg = ml_bridge.config(c)
    try:
        # Authenticated endpoint; /health alone cannot verify the token.
        result = ml_bridge.request(cfg, '/v1/dashboard?profile=soc&client_id=soc-news')
        if not isinstance(result, dict):
            raise ValueError('無效回應')
    except Exception as error:
        raise HTTPException(502, 'ML 連線或授權失敗：'+type(error).__name__)
    return {'ok': True}

@app.post('/api/ml/run')
async def run_ml():
    with db() as c:
        if not ml_bridge.config(c)['enabled']:
            raise HTTPException(400, '請先啟用 ML 分析')
    if ml_bridge.LOCK.locked():
        raise HTTPException(409, '分析中，請稍後查看結果')
    asyncio.create_task(asyncio.to_thread(ml_bridge.run, db, now))
    return {'ok': True}

@app.get('/api/ml/articles/{article_id}')
def ml_article(article_id: int):
    with db() as c:
        row = c.execute('SELECT * FROM ml_articles WHERE article_id=?', (article_id,)).fetchone()
        if not row:
            raise HTTPException(404, '尚未擷取正文')
        result = dict(row)
        result['feedback'] = [dict(r) for r in c.execute('SELECT label,reason,created,status,kind,relation,other_remote_id,error FROM ml_feedback WHERE article_id=? ORDER BY id DESC', (article_id,))]
        cfg = ml_bridge.config(c)
        result['pair_candidates'] = [dict(r) for r in c.execute("SELECT a.id,a.title FROM articles a JOIN ml_articles m ON m.article_id=a.id WHERE m.status='done' AND m.service=? AND a.id!=? ORDER BY a.id DESC",(cfg['url'],article_id))]
    result['remote_feedback'] = None
    if row['status']=='done' and row['service']==cfg['url'] and cfg['enabled']:
        try:
            remote = json.loads(row['result'])['article_id']
            result['remote_feedback'] = ml_bridge.request(cfg, '/v1/article?id='+str(remote)+'&client_id=soc-news')
            semantic = result['remote_feedback'].get('semantic')
            if isinstance(semantic,dict) and 'duplicate_decision' in semantic:
                current = json.loads(row['result'])
                current.update({k:v for k,v in semantic.items() if k.startswith('duplicate_')})
                with db() as c:
                    if ml_bridge.config(c) == cfg:
                        c.execute('UPDATE ml_articles SET result=?,analyzed_at=?,error=NULL WHERE article_id=? AND service=?',(json.dumps(current),now(),article_id,cfg['url']))
                result['result'] = json.dumps(current)

        except Exception as error:
            result['sync_error'] = '最新歷史讀取失敗：'+type(error).__name__
    return result

class MLFeedback(BaseModel):
    label: int | None = Field(default=None, ge=0, le=1, strict=True)
    reason: str = Field(min_length=1, max_length=2000)
    kind: str = 'relevance'
    other_article_id: int | None = None
    relation: str | None = None

@app.post('/api/ml/articles/{article_id}/feedback')
def ml_feedback(article_id: int, body: MLFeedback):
    if body.kind not in ('relevance','relation') or (body.kind=='relevance' and body.label is None):
        raise HTTPException(400,'請選擇有效回饋種類與標記')
    with db() as c:
        cfg = ml_bridge.config(c)
        row = c.execute('SELECT * FROM ml_articles WHERE article_id=?', (article_id,)).fetchone()
        if not cfg['enabled'] or not row or row['status'] != 'done' or row['service'] != cfg['url']:
            raise HTTPException(400, '請先完成目前 ML 服務的分析')
        remote = json.loads(row['result'])['article_id']
        other_remote = None
        if body.kind=='relation':
            other = c.execute("SELECT result FROM ml_articles WHERE article_id=? AND status='done' AND service=?",(body.other_article_id,cfg['url'])).fetchone()
            if not other or body.other_article_id==article_id or body.relation not in ('duplicate','update','different'):
                raise HTTPException(400,'請選另一篇已分析文章及有效關係')
            other_remote=json.loads(other['result'])['article_id']
            if other_remote==remote:
                raise HTTPException(400,'兩篇指向同一 ML 文章，無須再標記配對')
        c.execute('''INSERT INTO ml_feedback(article_id,label,reason,created,status,service,remote_id,kind,other_remote_id,relation,request_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)''',
                  (article_id, body.label if body.kind=='relevance' else None, body.reason, now(), 'pending', cfg['url'], remote,body.kind,other_remote,body.relation,str(ml_bridge.uuid.uuid4())))
    return {'queued': True}
