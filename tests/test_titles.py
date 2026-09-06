from app.main import clean_title

def test_publisher_suffix():
    assert clean_title('資安事件 - PChome Online 新聞') == '資安事件'
    assert clean_title('資安事件\u00a0- PChome Online 新聞') == '資安事件'
    assert clean_title('資安事件| 民視新聞網') == '資安事件'
    assert clean_title('資安事件 - 太報') == '資安事件'
    assert clean_title('資安事件 - 媒體名稱', 'Google 新聞') == '資安事件'

def test_preserve_substantive_title():
    assert clean_title('CVE-2026-1234 - 修補方法') == 'CVE-2026-1234 - 修補方法'

def test_multiple_publisher_tails():
    assert clean_title("事件 - 科技新聞 - PChome Online 新聞 - PChome", "Google 新聞") == "事件"

def test_author_and_category_suffixes():
    assert clean_title("資安事件-CY Research") == "資安事件"
    assert clean_title("資安事件 | 科技") == "資安事件"
