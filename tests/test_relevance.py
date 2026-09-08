from app.relevance import security_relevant
import pytest


@pytest.mark.parametrize('title', [
    '【編輯室札記】因應資安大海嘯 落實補丁作業',
    '社論：企業應重視網路安全',
    '【觀點／王小明】資安治理的下一步',
    '[專欄] AI時代的資安挑戰',
    '【教學】如何設定帳號密碼',
    '【廣編特輯】企業資安最佳選擇',
])
def test_explicit_non_news_labels_are_excluded(title):
    assert not security_relevant(title, 'Chrome修補CVE-2026-1234漏洞，企業需要關注資安。')


@pytest.mark.parametrize('title', [
    'Chrome發布緊急修補，修復CVE-2026-1234漏洞',
    '政府公布新版資安政策',
    '資安廠商發布新型防火牆',
    '駭客入侵媒體伺服器，竄改社論與評論內容',
    '【資安新聞】研究人員揭露伺服器漏洞',
])
def test_news_with_incidental_editorial_words_remains(title):
    assert security_relevant(title)


def test_entertainment_leaks_are_not_security():
    assert not security_relevant('吳慷仁不畏撞中國影視寒冬期！古裝劇組扮相外洩 角色設定曝光遭疑資源降級')
    assert not security_relevant('新手機外觀照片外洩')
    assert not security_relevant('電影駭客任務新片造型曝光')
    assert not security_relevant('道路出現漏洞，市府緊急修補')


def test_security_incidents_remain_included():
    assert security_relevant('醫院病歷外洩')
    assert security_relevant('伺服器遭駭客入侵')
    assert security_relevant('Chrome修補重大漏洞')
    assert security_relevant('資安治理政策更新')
    assert security_relevant('個資遭不當利用')


def test_stock_roundup_with_incidental_security_is_excluded():
    assert not security_relevant('加拿大AI股熱到發燙：從企業訓練、供應鏈到量子資安 誰才是下一支真正會賺錢的AI股？', '資安廠商受惠')
    assert not security_relevant('下一檔AI股看誰？從能源到資安的投資機會')


def test_financial_focus_is_excluded_even_for_security_companies():
    assert not security_relevant('AI資安需求升溫，CrowdStrike與Okta誰先把訂單變成獲利？')
    assert not security_relevant('AI引爆資安大戰！CrowdStrike(CRWD)狂飆80%，這3檔概念股迎爆發')
    assert not security_relevant('Zscaler財報超預期', '資安服務訂單成長')


def test_security_vendor_technical_news_remains():
    assert security_relevant("CrowdStrike發布資安防禦工具")
    assert security_relevant("Okta帳號系統漏洞修補")
