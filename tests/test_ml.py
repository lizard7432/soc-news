import json
import pytest
from fastapi.testclient import TestClient
from app import main as m
from app import ml_bridge as ml

@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DATA', tmp_path)
    monkeypatch.setattr(m, 'MODEL', None)
    m.init()
    with m.db() as c:
        m.ingest(c, '醫院遭駭客入侵病患個資外洩', '醫院資安事件', 'https://www.ithome.com.tw/news/123', m.now(), '測試')
        c.execute("UPDATE articles SET source_status='verified'")
    return TestClient(m.app)

def enable(client):
    r=client.post('/api/ml/settings', json={'enabled':True,'url':'http://localhost:7891','token':'secret-test-token'})
    assert r.status_code==200
    assert 'secret-test-token' not in r.text

def test_advisory_body_feedback_and_restart(setup, monkeypatch):
    enable(setup)
    monkeypatch.setattr(ml,'fetch_body',lambda url:('真實正文測試內容'*30,url))
    calls=[]
    def request(cfg,path,payload=None):
        calls.append((path,payload))
        if path=='/v1/whoami':return {'role':'service','profile':'soc-news'}
        if path=='/v1/feedback':return {'saved':True}
        return {'article_id':7,'decision':'exclude','model_version':None,'duplicate_decision':'not_evaluated'}
    monkeypatch.setattr(ml,'request',request)
    with m.db() as c:before=dict(c.execute('SELECT * FROM events').fetchone())
    ml.run(m.db,m.now)
    with m.db() as c:
        assert dict(c.execute('SELECT * FROM events').fetchone())==before
    detail=setup.get('/api/ml/articles/1').json()
    assert detail['body']=='真實正文測試內容'*30
    assert detail['fetched_at']
    analysis=next(payload for path,payload in calls if path=='/v1/analyze')
    assert analysis['body']==detail['body']
    assert analysis['profile']=='soc-news'
    assert setup.post('/api/ml/articles/1/feedback',json={'label':0,'reason':'偏宣傳'}).status_code==200
    m.init()
    ml.run(m.db,m.now)
    assert len([x for x in calls if x[0]=='/v1/analyze'])==1
    assert setup.get('/api/ml/articles/1').json()['feedback'][0]['status']=='sent'
    assert setup.post('/api/ml/articles/1/feedback',json={'label':2,'reason':'重複'}).status_code==422

def test_disabled_failure_and_retry(setup, monkeypatch):
    def fail(url):raise ValueError('private detail')
    monkeypatch.setattr(ml,'fetch_body',fail)
    ml.run(m.db,m.now)
    assert setup.get('/api/ml/articles/1').status_code==404
    enable(setup)
    ml.run(m.db,m.now)
    detail=setup.get('/api/ml/articles/1').json()
    assert detail['body'] is None and detail['status']=='error'
    assert 'private detail' not in detail['error']
    monkeypatch.setattr(ml,'fetch_body',lambda url:('正文'*100,url))
    monkeypatch.setattr(ml,'request',lambda *args:{'article_id':8,'decision':'review'})
    ml.run(m.db,m.now)
    assert setup.get('/api/ml/articles/1').json()['status']=='done'

def test_endpoint_and_token_protection(setup):
    enable(setup)
    assert setup.post('/api/ml/settings',json={'enabled':True,'url':'http://169.254.169.254','token':'x'}).status_code==400
    assert setup.post('/api/ml/settings',json={'enabled':True,'url':'http://article-learning:7891'}).status_code==400
    assert setup.post('/api/ml/settings',json={'enabled':True,'url':'http://localhost:7891'}).status_code==200
    assert 'secret-test-token' not in setup.get('/api/audit').text
    assert 'secret-test-token' not in setup.get('/api/settings').text

def test_original_extraction(monkeypatch):
    monkeypatch.setattr(ml,'read_feed',lambda *args,**kw:(('<h1>新聞</h1><article>'+('醫院發生資安事件。'*30)+'</article>').encode(),'https://www.ithome.com.tw/news/1'))
    body,url=ml.fetch_body('https://www.ithome.com.tw/news/1')
    assert '醫院發生資安事件' in body
    with pytest.raises(ValueError):ml.fetch_body('http://localhost:7891')

def test_pair_feedback_outbox_retry(setup,monkeypatch):
    enable(setup)
    with m.db() as c:
        m.ingest(c,'另一家公司資安漏洞遭利用','駭客攻擊','https://www.ithome.com.tw/news/456',m.now(),'test')
        c.execute("UPDATE articles SET source_status='verified'")
        for a in c.execute('SELECT id FROM articles').fetchall():
            c.execute("INSERT INTO ml_articles(article_id,status,result,service) VALUES(?,'done',?,?)",(a[0],json.dumps({'article_id':a[0]+100}),'http://localhost:7891'))
    r=setup.post('/api/ml/articles/1/feedback',json={'kind':'relation','other_article_id':2,'relation':'update','reason':'新增修補資訊'})
    assert r.status_code==200
    sent=[]
    def fail(cfg,path,payload=None):
        if path == "/v1/feedback": sent.append(payload)
        raise ValueError('offline')
    monkeypatch.setattr(ml,'request',fail)
    ml.run(m.db,m.now)
    with m.db() as c:
        row=dict(c.execute('SELECT * FROM ml_feedback').fetchone())
        assert row['status']=='pending' and row['label'] is None and row['error']
    def success(cfg,path,payload=None):
        if path == "/v1/feedback": sent.append(payload)
        return {'saved':True}
    monkeypatch.setattr(ml,'request',success)
    ml.run(m.db,m.now)
    assert sent[0]['request_id']==sent[1]['request_id']
    assert sent[1]['other_article_id']==102
    with m.db() as c:assert c.execute('SELECT status FROM ml_feedback').fetchone()[0]=='sent'


def test_async_semantic_sync_preserves_relevance(setup, monkeypatch):
    enable(setup)
    with m.db() as c:
        c.execute("INSERT INTO ml_articles(article_id,status,result,service) VALUES(1,'done',?,'http://localhost:7891')",(json.dumps({'article_id':101,'decision':'keep','duplicate_decision':'indexing'}),))
        cfg=ml.config(c)
    monkeypatch.setattr(ml,'request',lambda *args:{'semantic':{'duplicate_decision':'duplicate','duplicate_of':99,'decision':'exclude'}})
    ml.sync_results(m.db,m.now,cfg)
    with m.db() as c:
        result=json.loads(c.execute('SELECT result FROM ml_articles').fetchone()[0])
    assert result['decision']=='keep'
    assert result['duplicate_decision']=='duplicate' and result['duplicate_of']==99


def test_ml_still_applies_local_exclusion(setup):
    enable(setup)
    with m.db() as c:
        c.execute("UPDATE settings SET value=? WHERE key='keywords'",(json.dumps({'search':['資安'],'exclude':['概念股']}),))
        m.ingest(c,'資安概念股營收與投資獲利','一般投資新聞','https://www.ithome.com.tw/news/998',m.now(),'test')
        assert not c.execute("SELECT 1 FROM articles WHERE url LIKE '%998'").fetchone()


def test_sync_forbidden_does_not_block_next_article(setup, monkeypatch):
    import httpx
    enable(setup)
    with m.db() as c:
        m.ingest(c,"另一家公司資安漏洞遭利用","駭客攻擊","https://www.ithome.com.tw/news/999",m.now(),"test")
        for i in (1,2):
            c.execute("INSERT INTO ml_articles(article_id,status,result,service) VALUES(?,'done',?,'http://localhost:7891')",(i,json.dumps({'article_id':i,'duplicate_decision':'indexing'})))
        cfg=ml.config(c)
    def remote(cfg,path,payload=None):
        if 'id=1&' in path:
            response=httpx.Response(403,request=httpx.Request('GET','http://localhost'))
            raise httpx.HTTPStatusError('forbidden',request=response.request,response=response)
        return {'semantic':{'duplicate_decision':'unique'}}
    monkeypatch.setattr(ml,'request',remote)
    ml.sync_results(m.db,m.now,cfg)
    with m.db() as c:
        assert c.execute('SELECT error FROM ml_articles WHERE article_id=1').fetchone()[0]
        assert json.loads(c.execute('SELECT result FROM ml_articles WHERE article_id=2').fetchone()[0])['duplicate_decision']=='unique'
