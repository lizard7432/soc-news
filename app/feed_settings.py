"""User-managed feeds; only approved public publisher endpoints are fetched."""
import ipaddress
import socket
from urllib.parse import urlparse, urljoin

import feedparser
import httpx
from .source_policy import PUBLISHERS

FEED_HOSTS = (*PUBLISHERS, 'news.google.com', 'feeds.feedburner.com')
DEFAULT_SEARCH = ['資安', '網路安全', '駭客', '個資外洩', '勒索軟體', '漏洞', 'CVE', '零日', '修補', 'RCE', '網攻', '外洩', '入侵', '資料洩漏', '供應鏈攻擊', '網絡安全']

def validate_url(url):
    p = urlparse(url)
    host = (p.hostname or '').lower()
    if p.scheme not in ('http', 'https') or p.username or p.password or p.port not in (None, 80, 443):
        raise ValueError('請使用標準 HTTP／HTTPS RSS 網址，不可含帳密或自訂連接埠')
    if not any(host == h or host.endswith('.' + h) for h in FEED_HOSTS):
        raise ValueError('此網域尚未支援，請查看支援來源；新增訂閱不會自動授權未知媒體')
    return url

def read_feed(url, document=False):
    with httpx.Client(timeout=20, follow_redirects=False, trust_env=False, headers={'User-Agent': 'SOCNews/1.0 RSS Reader'}) as client:
        for _ in range(6):
            validate_url(url)
            p = urlparse(url)
            addresses = socket.getaddrinfo(p.hostname, p.port or (443 if p.scheme == 'https' else 80), type=socket.SOCK_STREAM)
            if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
                raise ValueError('不允許內網或本機來源')
            # Pin the connection to the checked IP; preserve Host and TLS SNI.
            ip = addresses[0][4][0]
            authority = '[' + ip + ']' if ':' in ip else ip
            pinned = p._replace(netloc=authority + (':' + str(p.port) if p.port else '')).geturl()
            with client.stream('GET', pinned, headers={'Host': p.netloc}, extensions={'sni_hostname': p.hostname}) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 4_000_000:
                        raise ValueError('訂閱內容超過 4 MB 上限')
                    chunks.append(chunk)
                if document:
                    return b''.join(chunks), url
                feed = feedparser.parse(b''.join(chunks))
                if not feed.version:
                    raise ValueError('這不是有效的 RSS／Atom 訂閱，請勿填入網站首頁')
                return feed
        raise ValueError('來源轉址次數過多')
