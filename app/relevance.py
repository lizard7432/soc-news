"""Require security context; a generic leak or intrusion is not enough."""
import re

STRONG = re.compile(r'資安|資訊安全|網路安全|網絡安全|網攻|零日|勒索軟體|惡意程式|惡意軟體|個資|資料外洩|資料洩漏|DDoS|CVE-\d|\bRCE\b', re.I)
ACTION = re.compile(r'外洩|洩漏|漏洞|駭客|入侵|釣魚|勒索|修補|竊取|盜用')
DIGITAL = re.compile(r'帳號|帳密|密碼|憑證|伺服器|資料庫|軟體|韌體|程式|系統|路由器|防火牆|病歷|客戶資料|用戶資料|使用者資料|電腦|惡意連結|電子郵件|API|Chrome|Windows|Linux|WordPress|GitHub', re.I)

# Match editorial labels, not incidental mentions in incident reporting.
NON_NEWS_LABEL = re.compile(
    r'^\s*(?:[【\[［「]\s*(?:編輯室札記|編輯手記|社論|評論|專欄|觀點|投書|教學|懶人包|廣編特輯|業配|工商服務)'
    r'(?:\s*[】\]］」]|[：:／/｜|][^】\]］」]*[】\]］」])'
    r'|(?:編輯室札記|編輯手記|社論|評論|專欄|觀點|投書|教學|懶人包|廣編特輯|業配|工商服務)\s*[：:／/｜|])'
)


# The drama name alone is not evidence of a cybersecurity subject.
DRAMA_NAME = re.compile(r'零日攻擊|zero\s*day', re.I)
SCREEN_CONTEXT = re.compile(r'影集|台劇|臺劇|電視劇|戲劇|劇組|劇情|演員|主演|男主|女主|金鐘|報獎|收視|首播|首映|預告|卡司|製作人|編劇|高橋一生')
FICTION_CONTEXT = re.compile(r'劇中|劇情|飾演|扮演|預告|虛構')
REAL_INCIDENT = re.compile(r'CVE-\d|漏洞|修補|遭駭|遭入侵|個資外洩|資料外洩|資料洩漏|帳號.*(?:劫持|盜用)|勒索軟體|DDoS', re.I)

def drama_coverage(title, summary):
    if not DRAMA_NAME.search(title+'。'+summary):
        return False
    if not SCREEN_CONTEXT.search(title+'。'+summary):
        return False
    # A real platform incident can mention a drama; fictional plot descriptions cannot override the filter.
    clauses = re.split(r'[。！？!?；;\n]', title)
    for part in clauses:
        clean = DRAMA_NAME.sub('',part)
        if not FICTION_CONTEXT.search(clean) and REAL_INCIDENT.search(clean) and re.search(r'平台|串流|網站|伺服器|帳號|個資|資料|CVE-|Chrome|Windows|漏洞|修補',clean,re.I):
            return False
    return True


def security_relevant(title, summary=''):
    if drama_coverage(title, summary):
        return False
    if NON_NEWS_LABEL.search(title):
        return False
    if re.search(r'概念股|飆股|潛力股|股價|股市|選股|誰.*獲利|訂單.*獲利|財報|利潤率|殖利率|目標價', title):
        return False
    # Stock-picking roundups must have security as their leading subject.
    # A security reference later in a list of unrelated sectors is insufficient.
    lead = re.split(r'[：:，,！!？?]', re.sub(r'^【[^】]*】', '', title), maxsplit=1)[0]
    stock_picking = re.search(r'下一[支檔]|概念股|選股|AI股|飆股|潛力股|哪[支檔].*股', title, re.I)
    security_subject = re.search(r'資安|資訊安全|網路安全|網絡安全|CrowdStrike|Okta|Palo Alto|Zscaler|Fortinet|趨勢科技|Check Point|SentinelOne', lead, re.I)
    if stock_picking and not security_subject:
        return False
    text=title+'。'+summary
    if STRONG.search(text): return True
    return any(ACTION.search(part) and DIGITAL.search(part)
               for part in re.split(r'[。！？!?\n]',text))
