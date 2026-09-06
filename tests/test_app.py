import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from app import main as m

@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(m, 'DATA', tmp_path)
    monkeypatch.setattr(m, 'MODEL', None)
    monkeypatch.setenv('SOC_PASSWORD','test-password-only')
    monkeypatch.setenv('SEMANTIC_ENABLED','false')
    m.init()

def add(url='https://example.com/a', title='台灣某醫院資安事件個資外洩', summary='醫院遭駭客入侵，病患資料外洩。'):
    with m.db() as c:
        result=m.ingest(c,title,summary,url,m.now(),'測試')
        # Unit fixtures explicitly simulate a previously verified original source.
        c.execute("UPDATE articles SET source_status='verified',original_published='2026-09-05T00:00:00+00:00',source_checked=?",(m.now(),))
        return result

def test_url_dedup():
    assert add('https://example.com/a?utm_source=x')
    assert not add('https://example.com/a?utm_source=y')
    with m.db() as c:
        assert c.execute('SELECT count(*) FROM articles').fetchone()[0]==1

def test_paraphrase_semantic_merge(monkeypatch):
    monkeypatch.setattr(m,'vector',lambda text:[0.8,0.6])
    add()
    add('https://example.com/b','駭客竊取台灣某醫院病患個資','同一家醫院的病患資料遭到駭客竊取。')
    with m.db() as c:
        assert c.execute('SELECT count(DISTINCT event_id) FROM articles').fetchone()[0]==1

def test_updated_numbers_not_merged(monkeypatch):
    monkeypatch.setattr(m,'vector',lambda text:[0.8,0.6])
    add(summary='台灣醫院個資外洩100筆')
    add('https://example.com/b',summary='台灣醫院個資外洩1000筆')
    with m.db() as c:
        rows=c.execute('SELECT * FROM events ORDER BY id').fetchall()
        assert len(rows)==2
        assert rows[1]['candidate']==rows[0]['id']

def test_merge_split_and_auth():
    add()
    add('https://example.com/b','台灣企業網路安全政策','資安治理與稽核')
    client=TestClient(m.app)
    assert client.get('/api/state').status_code==200
    client.auth=('soc','test-password-only')
    assert client.post('/api/events/2/merge',json={'target_id':1}).status_code==200
    assert client.post('/api/articles/2/split').status_code==200
    with m.db() as c:
        assert c.execute('SELECT event_id FROM articles WHERE id=2').fetchone()[0]!=1

def test_preview_export_and_cross_shift():
    add()
    with m.db() as c:
        c.execute("UPDATE events SET status='approved',created='2026-09-05T00:00:00+00:00',updated='2026-09-05T00:00:00+00:00'")
    body=m.Export(date='2026-09-05',shift=0,ids=[1])
    result=m.export(body,'test')
    assert result['text'].startswith('115年9月5日-日班-資安相關新聞列表：')
    assert result['ids']==[1]
    assert m.export(body,'test')['ids']==[1]
    body.confirm=True
    assert m.export(body,'test')['ids']==[1]
    assert m.export(body,'test')['ids']==[]

def test_excluded_and_future_never_export():
    add()
    with m.db() as c:
        c.execute("UPDATE events SET status='excluded'")
    body=m.Export(date='2099-01-01',shift=1,ids=[1],confirm=True)
    assert m.export(body,'test')['ids']==[]
    with m.db() as c:
        c.execute("UPDATE events SET status='approved'")
    body.date='2000-01-01'
    assert m.export(body,'test')['ids']==[]

def test_substantive_update_can_export_again():
    add()
    with m.db() as c:
        c.execute("UPDATE events SET status='approved',exported=?",(m.now(),))
    m.edit(1,m.Edit(title='台灣醫院外洩事件官方回應',status='approved',kind='update'),'test')
    result=m.export(m.Export(date='2026-09-05',shift=0,ids=[1]),'test')
    assert '【事件更新】' in result['text']

def test_source_failure_visible(monkeypatch):
    class Broken:
        def __init__(self,**kwargs): pass
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def get(self,url): raise RuntimeError('test source unavailable')
    monkeypatch.setattr(m.httpx,'Client',Broken)
    m.collect()
    with m.db() as c:
        rows=c.execute('SELECT * FROM runs').fetchall()
        assert len(rows)==len(m.DEFAULT_SOURCES) and all(r['status']=='error' for r in rows)
    assert not m.LOCK.locked()

def test_scope_flag_and_script_sanitizing():
    add(title='國外資安新聞',summary='<b>國外資安事件</b>')
    with m.db() as c:
        assert c.execute('SELECT kind FROM events').fetchone()[0]=='new'
    assert m.clean('<b>資安</b>')=='資安'

def test_custom_shift_boundary(monkeypatch):
    monkeypatch.setenv('SHIFT_REPORT_HOURS','7,15,23')
    monkeypatch.setenv('SHIFT_NAMES','日班,小夜班,大夜班')
    result=m.export(m.Export(date='2026-09-05',shift=2),'test')
    assert result['start']=='2026-09-05T15:00:00+08:00'
    assert result['end']=='2026-09-05T23:00:00+08:00'

def test_user_report_times_and_overnight_date():
    expected=[('日班','2026-09-06T03:00:00+08:00','2026-09-06T11:00:00+08:00'),
              ('小夜班','2026-09-06T11:00:00+08:00','2026-09-06T19:00:00+08:00'),
              ('大夜班','2026-09-06T19:00:00+08:00','2026-09-07T03:00:00+08:00')]
    for i,(name,start,end) in enumerate(expected):
        result=m.export(m.Export(date='2026-09-06',shift=i),'test')
        assert result['start']==start and result['end']==end
        assert result['text'].startswith(f'115年9月6日-{name}-資安相關新聞列表：')

def test_september_fifth_3am_belongs_to_fourth():
    result=m.export(m.Export(date='2026-09-04',shift=2),'test')
    assert result['end']=='2026-09-05T03:00:00+08:00'
    assert result['text'].startswith('115年9月4日-大夜班-資安相關新聞列表：')

def test_global_chinese_and_traditional_output():
    assert not add(title='美国企业信息安全漏洞造成资料泄漏',summary='黑客攻击美国企业的服务器，导致客户数据泄漏。')
    assert add(title='美國企業資訊安全漏洞造成資料外洩',summary='駭客攻擊美國企業伺服器，造成客戶資料外洩。')
    assert not add('https://example.com/en',title='US company suffers data breach',summary='Cybersecurity attack')
    with m.db() as c:
        row=c.execute('SELECT title FROM articles').fetchone()
        assert '美国' not in row['title'] and '美國' in row['title']
        assert c.execute('SELECT kind FROM events').fetchone()[0]=='new'

def test_cross_site_write_blocked():
    client=TestClient(m.app)
    client.auth=('soc','test-password-only')
    assert client.post('/api/collect',headers={'Origin':'https://untrusted.example'}).status_code==403
    assert client.get('/').headers['x-frame-options']=='DENY'

def test_no_login_required(monkeypatch):
    monkeypatch.delenv('SOC_PASSWORD',raising=False)
    client=TestClient(m.app)
    assert client.get('/').status_code==200
    assert 'id="report"' in client.get('/').text
    assert client.get('/api/state').status_code==200
    assert client.post('/api/login',json={}).status_code==404

@pytest.mark.parametrize('published,expected',[
    ('2026-09-05T18:59:59+00:00',False),
    ('2026-09-05T19:00:00+00:00',False),
    ('2026-09-05T19:00:01+00:00',True),
    ('2026-09-06T03:00:00+00:00',True),
    ('2026-09-06T03:00:01+00:00',False),
    ('2026-01-01T00:00:00+00:00',False),
    (None,False),
])
def test_strict_day_window(published,expected,monkeypatch):
    class FixedDate(datetime):
        @classmethod
        def now(cls,tz=None):
            return datetime(2026,9,7,tzinfo=timezone.utc)
    monkeypatch.setattr(m,'datetime',FixedDate)
    add()
    with m.db() as c:
        c.execute("UPDATE events SET status='approved'")
        c.execute('UPDATE articles SET original_published=?',(published,))
    result=m.export(m.Export(date='2026-09-06',shift=0,ids=[1]),'test')
    assert bool(result['ids'])==expected

def test_automatic_export_needs_no_human_approval():
    add()
    assert m.state('test')['events'][0]['automatic'] is True
    body=m.Export(date='2026-09-05',shift=0)
    assert m.export(body,'test')['ids']==[1]
    with m.db() as c:
        c.execute('UPDATE events SET candidate=99')
    assert m.export(body,'test')['ids']==[]
    with m.db() as c:
        c.execute("UPDATE events SET candidate=NULL,status='excluded'")
    assert m.export(body,'test')['ids']==[]

def test_source_upgrade_preserves_disabled_sources():
    import json
    with m.db() as c:
        sources=json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
        sources[0]['enabled']=False
        c.execute("UPDATE settings SET value=? WHERE key='sources'",(json.dumps(sources[:1]),))
    m.init()
    m.init()
    with m.db() as c:
        sources=json.loads(c.execute("SELECT value FROM settings WHERE key='sources'").fetchone()[0])
    assert len(sources)==len(m.DEFAULT_SOURCES)
    assert sources[0]['enabled'] is False
    assert len({s['url'] for s in sources})==len(m.DEFAULT_SOURCES)


def test_custom_range_cross_shift_and_boundaries():
    add()
    body=m.Export(date='2026-09-06',shift=1,start='2026-09-05T07:59:00',end='2026-09-05T08:00:00')
    result=m.export(body,'test')
    assert result['ids']==[1]
    assert '小夜班' in result['text']
    body.start=body.end
    with pytest.raises(Exception) as exc:
        m.export(body,'test')
    assert exc.value.status_code==400
    body.end=datetime.fromisoformat('2026-09-05T09:00:00+08:00')
    assert m.export(body,'test')['ids']==[]


def test_custom_range_requires_both_endpoints():
    client=TestClient(m.app)
    assert client.post('/api/export',json={'date':'2026-09-06','shift':0,'start':'2026-09-05T03:00:00'}).status_code==400
