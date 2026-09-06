from app.source_policy import traditional_domain, language


def test_tradingview_edition_does_not_guarantee_syndicated_language():
    url = 'https://tw.tradingview.com/news/panews:98342d73aacdf:0/'
    assert not traditional_domain(url)
    assert language('谷歌修复Chrome浏览器高危漏洞，已被黑客野外利用', url=url)[0] == 'rejected'
    assert language('研究人員揭露資訊安全漏洞，企業應更新軟體並檢查帳號權限，避免惡意程式竊取資料與憑證。' * 2, url=url)[0] == 'verified'
