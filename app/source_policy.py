"""Fail closed: original article pages must be verified before sharing."""
import re
import json
import subprocess
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from urllib.parse import urlparse, urljoin
import httpx
from bs4 import BeautifulSoup
from opencc import OpenCC
from trafilatura import extract

S2T=OpenCC('s2t')
T2S=OpenCC('t2s')
# Direct publishers only. Google wrappers are discovery links, never evidence of language.
PUBLISHERS=('ithome.com.tw','twcert.org.tw','udn.com','taisounds.com','cna.com.tw',
            'ltn.com.tw','technews.tw','pnn.pts.org.tw','chinatimes.com','moneydj.com','news.pts.org.tw','news.pchome.com.tw',
            'tw.news.yahoo.com','today.line.me','techritual.com','ftvnews.com.tw','newtalk.tw','inside.com.tw','bnext.com.tw')
PUBLISHERS += ('stock.pchome.com.tw','gamereactor.cn','inkl.com','cmnews.com.tw','blocktempo.com','money-link.com.tw','wepro180.com','setn.com','megatime.com.tw','ctee.com.tw','stheadline.com','tradingkey.com','biggo.com.tw','mirrordaily.news','techbang.com','cmoney.tw','mirrormedia.mg','ebc.net.tw','moneyweekly.com.tw','sunmedia.tw','tw.stock.yahoo.com','enn.tw','fountmedia.io','i-meihua.com','lifenews.com.tw','ntdtv.com','cnyes.com','compotechasia.com','rti.org.tw','epochtimes.com.tw','nextapple.com','abmedia.io','tw.tradingview.com','techorange.com','cio.com.tw','billows.com.tw','taiwannews.com.tw')
AMBIGUOUS=set('台里只面系后干云范余于布斗准制占合伙松谷征咨沈周卷克划回困才借折旋板涂游秘吃')

def simplified_chars(text):
    return {c for c in text if '\u4e00'<=c<='\u9fff' and c not in AMBIGUOUS and S2T.convert(c)!=c}

# These hosts have a Traditional Chinese editorial edition. Multilingual hosts
# require an explicit Traditional Chinese path instead of trusting the whole site.
TRADITIONAL_HOSTS=tuple(h for h in PUBLISHERS if h not in
    ('gamereactor.cn','inkl.com','ntdtv.com','tradingkey.com','taiwannews.com.tw','today.line.me','tw.tradingview.com'))

def traditional_domain(url):
    p=urlparse(url);host=(p.hostname or '').lower()
    if any(host==h or host.endswith('.'+h) for h in TRADITIONAL_HOSTS): return True
    return ((host=='today.line.me' and p.path.startswith('/tw/')) or
            (host in ('www.tradingkey.com','tradingkey.com') and p.path.startswith('/zh-hant/')) or
            (host in ('www.ntdtv.com','ntdtv.com') and p.path.startswith('/b5/')) or
            (host in ('www.taiwannews.com.tw','taiwannews.com.tw') and p.path.startswith(('/ch/','/zh/'))))

def simplified_edition(url,lang=''):
    p=urlparse(url)
    return ('hans' in lang.lower() or lang.lower() in ('zh-cn','zh-sg') or
            bool(re.search(r'/(?:zh-cn|zh-hans|gb|cn)(?:/|$)',p.path,re.I)) or
            bool(re.search(r'(?:^|&)(?:lang|locale|language)=(?:zh-cn|zh-hans|gb)(?:&|$)',p.query,re.I)))

def language(text, lang='', url=''):
    if simplified_edition(url,lang):
        return 'rejected', '原文網址或頁面明確標示簡體版本'
    if traditional_domain(url):
        if len(re.findall(r'[\u4e00-\u9fff]',text))<40:
            return 'unverified','無法取得足夠的中文原文'
        return 'verified','繁體媒體網域確認：'+(urlparse(url).hostname or '')+'；不以單字差異攔截'
    chars=sorted(simplified_chars(text))
    if chars:
        examples=[]
        for char in chars[:3]:
            i=text.index(char)
            examples.append(text[max(0,i-7):i]+'【'+char+'】'+text[i+1:i+8])
        return 'rejected', '偵測到疑似簡體用字：'+ '、'.join(chars[:12])+'。原文片段：'+ ' / '.join(examples)+'。自動判斷可能誤判，暫緩分享。'
    if len(re.findall(r'[\u4e00-\u9fff]',text))<40:
        return 'unverified','無法取得足夠的中文原文'
    traditional={c for c in text if T2S.convert(c)!=c}
    if len(traditional)<3:
        return 'unverified','無法確認原文為繁體中文'
    return 'verified','已檢查原文頁面的繁體中文用字'

def allowed(url):
    p=urlparse(url)
    return p.scheme=='https' and p.port in (None,443) and not p.username and any(p.hostname==h or (p.hostname or '').endswith('.'+h) for h in PUBLISHERS)

def resolve_source(url):
    """Resolve Google wrappers with a bounded worker, then normal publisher redirects."""
    try:
        if urlparse(url).hostname == 'news.google.com':
            worker = subprocess.run([sys.executable, '-c',
                'import json,sys;from googlenewsdecoder import new_decoderv1;print(json.dumps(new_decoderv1(sys.argv[1])))', url],
                capture_output=True, text=True, timeout=25)
            result = json.loads(worker.stdout)
            if not result.get('status'):
                return None, 'Google 原始網址解析失敗，稍後重試'
            url = result['decoded_url']
            if url.startswith('http://') and allowed('https://'+url[7:]):
                url='https://'+url[7:]
        for _ in range(5):
            if url.startswith('http://') and allowed('https://'+url[7:]):
                url='https://'+url[7:]
            if not allowed(url):
                return None, '已解析網址，但媒體尚未支援原文檢查：' + (urlparse(url).hostname or '')
            with httpx.stream('GET', url, timeout=15, follow_redirects=False) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers['location'])
                    continue
                response.raise_for_status()
                return url, ''
        return None, '來源轉址次數過多'
    except subprocess.TimeoutExpired:
        return None, 'Google 原始網址解析逾時，稍後重試'
    except httpx.HTTPStatusError as exc:
        return url if allowed(url) else None, '媒體回應 HTTP '+str(exc.response.status_code)+'，未能讀取原文'
    except Exception as exc:
        return None, '原始網址解析失敗：' + type(exc).__name__

def article_text(soup, url=''):
    # Prefer articleBody over a broad article element that may include recommendations.
    for selector in ('[itemprop="articleBody"]','.field-name-body','.article-body','.article_body','.article-content','.article_wrap','.entry-content','article'):
        article=soup.select_one(selector)
        if article:
            for tag in article.select('script,style,nav,footer,aside,.related,.comments'):
                tag.decompose()
            text=article.get_text(' ',strip=True)
            if len(text)>=80: return text
    # Structured article bodies are from the source page, never RSS summaries.
    def bodies(obj):
        if isinstance(obj,dict):
            if isinstance(obj.get('articleBody'),str): yield obj['articleBody']
            for value in obj.values(): yield from bodies(value)
        elif isinstance(obj,list):
            for value in obj: yield from bodies(value)
    for tag in soup.select('script[type="application/ld+json"]'):
        try:
            for body in bodies(json.loads(tag.get_text())):
                if len(body)>=80: return BeautifulSoup(body,'html.parser').get_text(' ',strip=True)
        except (ValueError,TypeError): pass
    if not soup.select_one('h1'): return None
    return extract(str(soup),url=url,favor_precision=True,include_comments=False,include_tables=False)

def verify(url):
    if not allowed(url):
        return 'unverified','尚未確認媒體原始網址或來源不在允許清單'
    try:
        with httpx.Client(timeout=15,follow_redirects=False,headers={'User-Agent':'SOCNews/1.0 Source Language Verification'}) as client:
            for _ in range(5):
                if not allowed(url):
                    return 'unverified','轉址目的地尚未核准'
                with client.stream('GET',url) as response:
                    if response.is_redirect:
                        url=urljoin(url,response.headers['location'])
                        continue
                    response.raise_for_status()
                    chunks=[];size=0
                    for chunk in response.iter_bytes():
                        size+=len(chunk)
                        if size>3000000:
                            return 'unverified','原文頁面過大，尚未確認'
                        chunks.append(chunk)
                    html=b''.join(chunks).decode(response.encoding or 'utf-8',errors='replace')
                soup=BeautifulSoup(html,'html.parser')
                lang=(soup.html.get('lang','') if soup.html else '')
                text=article_text(soup, url)
                if not text:
                    return 'unverified','原文正文無法擷取（可能需 JavaScript 或付費登入）'
                return language(text,lang,url)
        return 'unverified','轉址次數過多'
    except httpx.HTTPStatusError as e:
        return 'unverified','媒體回應 HTTP '+str(e.response.status_code)+'，未能讀取原文'
    except Exception as e:
        return 'unverified','原文檢查失敗：'+type(e).__name__

def original_date(url, official_feed_time=None):
    """Only original datePublished with an explicit time and timezone. Never dateModified."""
    if not allowed(url):
        return None
    try:
        r=httpx.get(url,timeout=15,follow_redirects=False)
        r.raise_for_status()
        soup=BeautifulSoup(r.text,'html.parser')
        values=[]
        for tag in soup.select('meta[property="article:published_time"],meta[itemprop="datePublished"],time[itemprop="datePublished"]'):
            values.append(tag.get('content') or tag.get('datetime'))
        def walk(obj):
            if isinstance(obj,dict):
                if 'datePublished' in obj: values.append(obj['datePublished'])
                for val in obj.values(): walk(val)
            elif isinstance(obj,list):
                for val in obj: walk(val)
        for script in soup.select('script[type="application/ld+json"]'):
            try: walk(json.loads(script.string or script.get_text()))
            except (ValueError,TypeError): pass
        dates=[]
        for value in values:
            if not isinstance(value,str) or not re.search(r'\d{2}:\d{2}',value): continue
            try:
                value=re.sub(r'([+-]\d{2})(\d{2})$',r'\1:\2',value.strip())
                d=datetime.fromisoformat(value.replace('Z','+00:00'))
                if d.tzinfo is not None and d<=datetime.now(timezone.utc): dates.append(d.astimezone(timezone.utc))
            except ValueError: pass
        if dates:
            return min(dates).isoformat()
        # iThome's article displays a date only. Its own RSS supplies time;
        # use it only when it agrees with the article's original publication date.
        if (urlparse(url).hostname or '').endswith('ithome.com.tw') and official_feed_time:
            created=soup.select_one('.submitted .created')
            feed=datetime.fromisoformat(official_feed_time)
            if created and feed.tzinfo and created.get_text(strip=True)==feed.astimezone(ZoneInfo('Asia/Taipei')).strftime('%Y-%m-%d') and feed<=datetime.now(timezone.utc):
                return feed.astimezone(timezone.utc).isoformat()
        return None
    except Exception:
        return None
