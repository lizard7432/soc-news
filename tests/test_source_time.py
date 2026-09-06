import httpx
from app import source_policy as policy

def page(monkeypatch,html):
    monkeypatch.setattr(policy.httpx,'get',lambda url,**kwargs:httpx.Response(200,text=html,request=httpx.Request('GET',url)))

def test_modified_time_cannot_make_old_news_new(monkeypatch):
    page(monkeypatch,'<script type="application/ld+json">{"datePublished":"2020-01-01T09:00:00+08:00","dateModified":"2026-09-06T10:00:00+08:00"}</script>')
    assert policy.original_date('https://www.ithome.com.tw/news/1')=='2020-01-01T01:00:00+00:00'

def test_date_only_without_official_feed_is_unverified(monkeypatch):
    page(monkeypatch,'<div class="submitted"><span class="created">2020-01-01</span></div>')
    assert policy.original_date('https://www.ithome.com.tw/news/1') is None
    assert policy.original_date('https://www.ithome.com.tw/news/1','2020-01-01T01:00:00+00:00')=='2020-01-01T01:00:00+00:00'

def test_new_feed_date_cannot_override_old_original_date(monkeypatch):
    page(monkeypatch,'<div class="submitted"><span class="created">2020-01-01</span></div>')
    assert policy.original_date('https://www.ithome.com.tw/news/1','2026-09-05T01:00:00+00:00') is None


def test_timezone_without_colon(monkeypatch):
    page(monkeypatch,'<meta property="article:published_time" content="2026-09-06T10:40:29+0800">')
    assert policy.original_date('https://www.stheadline.com/test')=='2026-09-06T02:40:29+00:00'
