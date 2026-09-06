import json
from types import SimpleNamespace
import httpx
from app import source_policy as p


def test_google_resolution(monkeypatch):
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps({'status':True,'decoded_url':'https://news.pts.org.tw/article/123'})))
    monkeypatch.setattr(p.httpx, 'stream', lambda *a, **k: Stream('https://news.pts.org.tw/article/123'))
    assert p.resolve_source('https://news.google.com/rss/articles/test') == ('https://news.pts.org.tw/article/123','')

class Stream:
    def __init__(self,url): self.url=url
    def __enter__(self): return httpx.Response(200,request=httpx.Request('GET',self.url))
    def __exit__(self,*args): pass


def test_decoder_cannot_redirect_to_internal_host(monkeypatch):
    monkeypatch.setattr(p.subprocess, 'run', lambda *a, **k: SimpleNamespace(stdout=json.dumps({'status':True,'decoded_url':'https://127.0.0.1/private'})))
    assert p.resolve_source('https://news.google.com/rss/articles/test')[0] is None


def test_decoder_timeout(monkeypatch):
    def timeout(*a,**k): raise p.subprocess.TimeoutExpired('decode',25)
    monkeypatch.setattr(p.subprocess,'run',timeout)
    assert '逾時' in p.resolve_source('https://news.google.com/rss/articles/test')[1]
