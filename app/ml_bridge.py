"""Optional advisory service. Never changes event or sharing state."""
import hashlib
import json
import os
import threading
import uuid
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup
from .relevance import security_relevant
from .feed_settings import read_feed
from .source_policy import allowed, article_text, language

LOCK = threading.Lock()
SYNC_LOCK = threading.Lock()


def init(c):
    c.executescript('''
    CREATE TABLE IF NOT EXISTS ml_articles(
      article_id INTEGER PRIMARY KEY REFERENCES articles(id), body TEXT,
      body_url TEXT, fetched_at TEXT, event_group TEXT, status TEXT,
      result TEXT, error TEXT, analyzed_at TEXT, service TEXT);
    CREATE TABLE IF NOT EXISTS ml_feedback(
      id INTEGER PRIMARY KEY, article_id INTEGER, label INTEGER, reason TEXT,
      created TEXT, status TEXT, service TEXT, remote_id INTEGER);
    ''')
    cols = {r[1] for r in c.execute('PRAGMA table_info(ml_feedback)')}
    for name, definition in [('kind',"TEXT DEFAULT 'relevance'"),('other_remote_id','INTEGER'),('relation','TEXT'),('request_id','TEXT'),('error','TEXT')]:
        if name not in cols:
            c.execute(f'ALTER TABLE ml_feedback ADD COLUMN {name} {definition}')
    for row in c.execute('SELECT id FROM ml_feedback WHERE request_id IS NULL').fetchall():
        c.execute('UPDATE ml_feedback SET request_id=? WHERE id=?',(str(uuid.uuid4()),row[0]))


def config(c, public=False):
    row = c.execute("SELECT value FROM settings WHERE key='ml'").fetchone()
    value = json.loads(row[0]) if row else {'enabled': False, 'url': '', 'token': ''}
    if public:
        return {'enabled': value['enabled'], 'url': value['url'],
                'token_set': bool(value['token']), 'mode': 'advisory', 'running': LOCK.locked()}
    return value


def validate_endpoint(url):
    p = urlparse(url)
    hosts = os.getenv('ML_ALLOWED_HOSTS', '127.0.0.1,localhost,article-learning').split(',')
    if (p.scheme not in ('http', 'https') or p.hostname not in [h.strip() for h in hosts]
            or p.username or p.password or p.query or p.fragment or p.path not in ('', '/')):
        raise ValueError('ML 位址須為管理員 ML_ALLOWED_HOSTS 核准的服務根網址')
    if p.port == 0:
        raise ValueError('無效連接埠')
    return url.rstrip('/')


def request(cfg, path, payload=None):
    base = validate_endpoint(cfg['url'])
    with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
        headers = {'Authorization': 'Bearer ' + cfg['token']}
        r = client.get(base + path, headers=headers) if payload is None else client.post(base + path, json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


def fetch_body(url):
    if not allowed(url):
        raise ValueError('原文媒體尚未支援')
    raw, final = read_feed(url, document=True)
    if not allowed(final):
        raise ValueError('原文轉址媒體尚未支援')
    soup = BeautifulSoup(raw, 'html.parser')
    text = article_text(soup, final)
    if not text or len(text) < 80 or len(text) > 200000:
        raise ValueError('未取得有效正文')
    if language(text, url=final)[0] != 'verified':
        raise ValueError('正文繁體版本未確認')
    return text, final


def run(db, now):
    if not LOCK.acquire(blocking=False):
        return
    try:
        with db() as c:
            cfg = config(c)
            if not cfg['enabled']:
                return
            rows = [dict(r) for r in c.execute('''SELECT a.* FROM articles a
                LEFT JOIN ml_articles m ON m.article_id=a.id
                WHERE a.source_status='verified' AND
                (m.status IS NULL OR m.status!='done' OR m.service!=?)
                ORDER BY COALESCE(m.analyzed_at, ''), a.id DESC''', (cfg['url'],))]
        with db() as c:
            setting=c.execute("SELECT value FROM settings WHERE key='keywords'").fetchone()
            terms=json.loads(setting[0]).get('exclude',[]) if setting else []
        rows=[a for a in rows if security_relevant(a['title'],a['summary'] or '') and not any(t.casefold() in (a['title']+' '+(a['summary'] or '')).casefold() for t in terms)]
        for a in rows[:20]:
            with db() as c:
                if config(c) != cfg:
                    break
                existing = c.execute('SELECT * FROM ml_articles WHERE article_id=?', (a['id'],)).fetchone()
            try:
                identity = request(cfg, '/v1/whoami')
                profile = identity.get('profile') or 'soc'
                if existing and existing['body']:
                    body, target = existing['body'], existing['body_url']
                    group = existing['event_group']
                else:
                    body, target = fetch_body(a['resolved_url'] or a['url'])
                    # Content-stable identity; existing SOC merges are not ground truth.
                    group = 'soc-content-' + hashlib.sha256((a['title'].strip()+'\n'+body.strip()).encode()).hexdigest()
                    with db() as c:
                        c.execute('''INSERT OR REPLACE INTO ml_articles
                            (article_id,body,body_url,fetched_at,event_group,status) VALUES(?,?,?,?,?,?)''',
                            (a['id'], body, target, now(), group, 'pending'))
                result = request(cfg, '/v1/analyze', {'profile': profile, 'client_id': 'soc-news', 'analysis_task':'dedup',
                    'title': a['title'], 'body': body, 'event_group': group})
                if result.get('decision') not in ('keep', 'exclude', 'review') or type(result.get('article_id')) is not int:
                    raise ValueError('無效分析結果')
                with db() as c:
                    c.execute('''UPDATE ml_articles SET status='done',result=?,error=NULL,analyzed_at=?,service=? WHERE article_id=?''',
                              (json.dumps(result), now(), cfg['url'], a['id']))
            except Exception as error:
                with db() as c:
                    c.execute('INSERT OR IGNORE INTO ml_articles(article_id) VALUES(?)', (a['id'],))
                    c.execute("UPDATE ml_articles SET status='error',error=?,analyzed_at=? WHERE article_id=?",
                              ('擷取或分析失敗：'+type(error).__name__, now(), a['id']))
        with db() as c:
            pending = [dict(r) for r in c.execute("SELECT * FROM ml_feedback WHERE status='pending' AND service=? ORDER BY id", (cfg['url'],))]
        for item in pending:
            try:
                with db() as c:
                    if config(c) != cfg:
                        break
                reply = request(cfg, '/v1/feedback', {'article_id': item['remote_id'], 'label': item['label'], 'reason': item['reason'],
                    'kind':item['kind'] or 'relevance','other_article_id':item['other_remote_id'],'relation':item['relation'],'request_id':item['request_id']})
                if reply.get('saved') is not True:
                    raise ValueError('回饋未儲存')
                with db() as c:
                    c.execute("UPDATE ml_feedback SET status='sent',error=NULL WHERE id=?", (item['id'],))
            except Exception as error:
                with db() as c:
                    message = ('HTTP '+str(error.response.status_code)) if isinstance(error,httpx.HTTPStatusError) else type(error).__name__
                    c.execute('UPDATE ml_feedback SET error=? WHERE id=?',(message,item['id']))
                break
        sync_results(db, now, cfg)
    finally:
        LOCK.release()


def sync_results(db, now, cfg):
    if not cfg.get('enabled') or not SYNC_LOCK.acquire(blocking=False): return
    try:
        _sync_results(db, now, cfg)
    finally:
        SYNC_LOCK.release()


def _sync_results(db, now, cfg):
    """Refresh completed analyses in bounded oldest-first batches, including async indexing."""
    with db() as c:
        rows = [dict(r) for r in c.execute("SELECT * FROM ml_articles WHERE status='done' AND service=? ORDER BY CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END, CASE WHEN json_extract(result,'$.duplicate_decision') IN ('indexing','unavailable','not_evaluated') OR json_extract(result,'$.duplicate_decision') IS NULL THEN 0 ELSE 1 END,analyzed_at,article_id LIMIT 20",(cfg['url'],))]
    for row in rows:
        try:
            result = json.loads(row['result'])
            remote = request(cfg, '/v1/article?id='+str(result['article_id'])+'&client_id=soc-news')
            semantic = remote.get('semantic')
            if not isinstance(semantic,dict) or 'duplicate_decision' not in semantic:
                continue
            result.update({k:v for k,v in semantic.items() if k.startswith('duplicate_')})
            with db() as c:
                if config(c) != cfg: return
                c.execute("UPDATE ml_articles SET result=?,analyzed_at=?,error=NULL WHERE article_id=? AND service=?",(json.dumps(result),now(),row['article_id'],cfg['url']))
        except Exception as error:
            with db() as c:
                c.execute('UPDATE ml_articles SET error=?,analyzed_at=? WHERE article_id=? AND service=?',('模型結果同步失敗：'+type(error).__name__,now(),row['article_id'],cfg['url']))
            if isinstance(error,httpx.HTTPStatusError) and error.response.status_code in (403,404):
                continue
            break
