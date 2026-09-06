"""Conservative event matching: semantic similarity plus shared event facts."""
import re
from datetime import datetime
from decimal import Decimal

def headline(title):
    # Publisher labels and editorial punctuation aren't event facts.
    title = re.split(r'\s[-–—|｜]\s|\|', title)[0]
    title = re.sub(r'^【[^】]+】', '', title)
    return re.sub(r'[\s「」『』!！:：，,。?？]', '', title).replace('臺', '台')

def facts(text):
    result = {}
    for value, scale, unit in re.findall(r'(\d+(?:\.\d+)?)\s*(萬|億)?\s*(筆|人|戶|個帳號|元|萬元|萬)?', text):
        if not scale and not unit:
            continue  # Dates, listicle numbers and unrelated version numbers aren't counts.
        n = Decimal(value) * {'萬':10000, '億':100000000}.get(scale, 1)
        key = unit or 'amount'
        if key == '元':
            key = 'amount'
        result.setdefault(key, set()).add(str(n.normalize()))
    cves = set(re.findall(r'CVE-\d{4}-\d+', text, re.I))
    if cves:
        result['cve'] = {x.upper() for x in cves}
    return result

def conflict(a, b):
    fa, fb = facts(a), facts(b)
    return any(fa[k].isdisjoint(fb[k]) for k in fa.keys() & fb.keys())

def actions(text):
    groups = {
        'fine':r'罰單|重罰|遭罰|開罰|裁罰|罰款',
        'drone':r'空拍|無人機',
        'breach':r'外洩|洩漏|竊取|遭竊',
        'attack':r'入侵|遭駭|駭客攻擊|網攻',
        'ransom':r'勒索|加密勒贖',
        'patch':r'修補|修復漏洞',
    }
    return {key for key, pattern in groups.items() if re.search(pattern, text)}

def same_subject(a, b):
    a, b = headline(a), headline(b)
    prefix = ''
    for x, y in zip(a, b):
        if x != y:
            break
        prefix += x
    # Require a concrete shared leading subject; broad geography/category isn't enough.
    if any(prefix.startswith(x) for x in ('台灣','中國','全球','資安','研究人員','駭客','網路','最新','獨家','快訊')):
        return False
    return len(prefix) >= 3 and bool(re.search(r'[\u4e00-\u9fffA-Za-z]', prefix))

def decision(title, summary, published, previous, score, semantic, threshold):
    a, b = headline(title), headline(previous['title'])
    old_summary = previous['summary'] or ''
    if published and previous['published']:
        if abs((datetime.fromisoformat(published)-datetime.fromisoformat(previous['published'])).total_seconds()) > 72*3600:
            return False, '發布時間相隔超過72小時'
    if conflict(a+' '+summary, b+' '+old_summary):
        return False, '數量、金額或CVE存在衝突，保留審核'
    progress = r'完成修補|已修復|新增受害|受害範圍擴大|已恢復|恢復營運|調查結果|官方回應|首次證實'
    pa, pb = set(re.findall(progress,a)), set(re.findall(progress,b))
    if pa != pb:
        return False, '具體進展不同，保留審核'
    if a == b:
        return True, '同一標題，僅媒體或標點不同'
    shared_actions = actions(a) & actions(b)
    fa, fb = facts(a), facts(b)
    shared_facts = any(fa[k] & fb[k] for k in fa.keys() & fb.keys())
    if same_subject(a,b) and shared_actions and (shared_facts or len(shared_actions)>=2) and score >= (0.76 if semantic else 0.25):
        return True, '主體、事件行為及關鍵事實一致；合併改寫報導'
    if shared_facts and shared_actions and not same_subject(a,b):
        return False, '金額或數量相同，但無法確認為同一主體'
    if score >= threshold:
        return True, '內容高度相似且未發現事實衝突'
    return False, '相似度不足或缺少共同事件事實'
