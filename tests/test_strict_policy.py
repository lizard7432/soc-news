import pytest
from fastapi import HTTPException
from app import main as m
from app.source_policy import language, verify

@pytest.fixture(autouse=True)
def isolated(tmp_path,monkeypatch):
    monkeypatch.setattr(m,'DATA',tmp_path)
    monkeypatch.setattr(m,'MODEL',None)
    m.init()

def seed():
    with m.db() as c:
        m.ingest(c,'美國企業資料外洩資安事件','企業遭受駭客入侵','https://www.ithome.com.tw/news/1',m.now(),'test')
        c.execute("UPDATE events SET status='approved'")

def test_simplified_original_rejected_despite_traditional_headline():
    assert language('网络安全公司发布数据泄漏调查报告，企业用户密码遭黑客窃取。'*4,'zh-TW')[0]=='rejected'
    assert language('資訊安全公司發布資料外洩調查報告，企業使用者密碼遭駭客竊取。'*4,'zh-Hant')[0]=='verified'

def test_google_wrapper_is_not_evidence():
    assert verify('https://news.google.com/rss/articles/anything')[0]=='unverified'

def test_unverified_source_cannot_be_exported_or_approved():
    seed()
    assert m.export(m.Export(date='2099-01-01',shift=0,ids=[1]),'test')['ids']==[]
    with pytest.raises(HTTPException):
        m.edit(1,m.Edit(title='美國企業資料外洩資安事件',status='approved'),'test')

def test_duplicate_gate_even_for_previously_approved_event():
    seed()
    with m.db() as c:
        c.execute("UPDATE articles SET source_status='verified',source_checked=?",(m.now(),))
        c.execute('UPDATE events SET candidate=2')
    assert m.export(m.Export(date='2099-01-01',shift=0,ids=[1]),'test')['ids']==[]
    with pytest.raises(HTTPException):
        m.edit(1,m.Edit(title='美國企業資料外洩資安事件',status='approved'),'test')

def test_language_rejection_is_not_overridden_by_distinct_reason():
    seed()
    with pytest.raises(HTTPException):
        m.edit(1,m.Edit(title='美國企業資料外洩資安事件',status='approved',distinct_reason='這是另一家企業遭到攻擊，與之前的事件不同'),'test')
