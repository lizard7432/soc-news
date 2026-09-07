import json
import pytest
from fastapi.testclient import TestClient
from app import main as m
from app import feed_settings as f

@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DATA', tmp_path)
    monkeypatch.setattr(m, 'MODEL', None)
    m.init()
    return TestClient(m.app)

def test_sources_persist_and_duplicate(client):
    body = {'name':'測試訂閱', 'url':'https://www.ithome.com.tw/rss/test'}
    assert client.post('/api/sources', json=body).status_code == 200
    assert client.post('/api/sources', json=body).status_code == 409
    assert client.post('/api/sources/toggle', json={'url':body['url'], 'enabled':False}).status_code == 200
    m.init()
    assert next(s for s in client.get('/api/settings').json()['sources'] if s['url']==body['url'])['enabled'] is False

@pytest.mark.parametrize('url', ['http://127.0.0.1/rss','http://localhost/rss','file:///tmp/rss','https://ithome.com.tw.evil.com/rss','http://user:password@ithome.com.tw/rss','http://ithome.com.tw:8000/rss'])
def test_reject_unsafe_sources(client, url):
    assert client.post('/api/sources', json={'name':'拒絕', 'url':url}).status_code == 400

def test_keywords_apply_and_persist(client):
    body={'kind':'exclude','term':'測試醫院'}
    assert client.post('/api/keywords', json=body).status_code == 200
    m.init()
    with m.db() as c:
        assert not m.ingest(c,'測試醫院遭駭客入侵','病患個資外洩','https://www.ithome.com.tw/a',m.now(),'test')
    assert '測試醫院' in client.get('/api/settings').json()['keywords']['exclude']
    assert client.post('/api/keywords',json={**body,'remove':True}).status_code==200
    with m.db() as c:
        assert m.ingest(c,'測試醫院遭駭客入侵','病患個資外洩','https://www.ithome.com.tw/a',m.now(),'test')

def test_search_query_uses_saved_keywords(client, monkeypatch):
    with m.db() as c:
        c.execute("UPDATE settings SET value=? WHERE key='keywords'",(json.dumps({'search':['測試漏洞'],'exclude':[]}),))
        c.execute("UPDATE settings SET value=? WHERE key='sources'",(json.dumps([{'name':'Google','url':'https://news.google.com/rss/search?q=old','enabled':True}]),))
    seen=[]
    def read(url):
        seen.append(url)
        return type('Feed',(),{'entries':[]})()
    monkeypatch.setattr(m,'read_feed',read)
    monkeypatch.setattr(m,'policy_review',lambda:None)
    m.collect()
    from urllib.parse import parse_qs,urlparse
    assert '測試漏洞' in parse_qs(urlparse(seen[0]).query)['q'][0]
    with m.db() as c:
        c.execute("UPDATE settings SET value=? WHERE key='keywords'",(json.dumps({'search':[],'exclude':[]}),))
    seen.clear()
    m.collect()
    assert seen==[]

def test_private_dns_blocked(monkeypatch):
    monkeypatch.setattr(f.socket,'getaddrinfo',lambda *a,**k:[(2,1,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError, match='內網'):
        f.read_feed('https://www.ithome.com.tw/rss')

def test_cross_origin_settings_blocked(client):
    r=client.post('/api/keywords',json={'kind':'search','term':'test'},headers={'Origin':'https://evil.example'})
    assert r.status_code==403
