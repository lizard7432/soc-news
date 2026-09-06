from app.source_policy import language,traditional_domain

def test_trusted_traditional_domain_does_not_reject_single_character():
    text='這是臺灣資訊安全新聞，比较難被發現，企業應持續檢查網路設備與資料保護。'*4
    assert language(text,'zh-TW','https://www.ithome.com.tw/news/1')[0]=='verified'
    assert language(text,'zh-CN','https://www.ithome.com.tw/news/1')[0]=='rejected'
    assert language(text,'','https://www.ithome.com.tw/zh-cn/news/1')[0]=='rejected'

def test_domain_is_not_tld_or_substring_trust():
    assert not traditional_domain('https://ithome.com.tw.evil.test/')
    assert not traditional_domain('https://unknown.tw/')
    assert not traditional_domain('https://www.ntdtv.com/gb/2026/09/06/test')
    assert traditional_domain('https://today.line.me/tw/v3/article/1')
    assert not traditional_domain('https://today.line.me/cn/v3/article/1')
