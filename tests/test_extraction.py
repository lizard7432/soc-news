from bs4 import BeautifulSoup
from app.source_policy import article_text,language,allowed

def test_article_body_excludes_sidebar():
    body='這是臺灣資訊安全新聞，企業應檢查網路設備並修補漏洞，保護使用者資料與帳號。'*4
    soup=BeautifulSoup('<html><article><nav>简体导航</nav><div class="article_body">'+body+'</div><aside>简体推荐</aside></article></html>','html.parser')
    text=article_text(soup)
    assert text==body
    assert language(text)[0]=='verified'

def test_jsonld_original_body():
    import json
    body='這是臺灣資訊安全新聞，企業應檢查網路設備並修補漏洞，保護使用者資料與帳號。'*4
    soup=BeautifulSoup('<script type="application/ld+json">'+json.dumps({'@type':'NewsArticle','articleBody':body})+'</script>','html.parser')
    assert article_text(soup)==body

def test_navigation_is_not_an_article():
    assert not article_text(BeautifulSoup('<nav>新聞首頁政治生活科技</nav>','html.parser'))

def test_supported_media_still_restricts_destination():
    assert allowed('https://www.setn.com/News.aspx?NewsID=1')
    assert not allowed('https://www.setn.com.evil.test/')


def test_traditional_shared_characters_do_not_reject():
    from app.source_policy import simplified_chars
    assert not simplified_chars('秘密存取，游離在既有資安管理之外')
    assert '较' in simplified_chars('比較與比较')

def test_language_reason_contains_evidence():
    status,reason=language('這是臺灣資訊安全新聞，比较難被發現。'*5)
    assert status=='rejected'
    assert '【较】' in reason
