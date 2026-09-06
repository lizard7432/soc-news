from app.dedup import decision

TITLES = [
    '鍾明軒「墾丁空拍」慘收33萬罰單 軍事粉專：證實中國資安入侵',
    '鍾明軒墾丁空拍遭重罰33萬 軍事粉專警示「中國資安風險」 - 太報',
    '鍾明軒核三廠空拍遭罰33萬！軍事粉專：中國資安滲透台灣| 民視新聞網',
]

def check(a,b,score=.83,date='2026-09-05T01:00:00+00:00'):
    return decision(a,'',date,{'title':b,'summary':'','published':'2026-09-05T00:00:00+00:00'},score,True,.94)[0]

def test_user_paraphrases_all_orders():
    for a in TITLES:
        for b in TITLES:
            assert check(a,b)

def test_changed_fine_is_not_duplicate():
    assert not check(TITLES[0].replace('33萬','66萬'),TITLES[1],.98)

def test_different_person_not_duplicate():
    assert not check(TITLES[0].replace('鍾明軒','王小明'),TITLES[1],.99)

def test_distinct_later_occurrence():
    assert not check(TITLES[0],TITLES[1],.99,'2026-09-12T00:00:00+00:00')

def test_real_followup_is_retained():
    assert not check('甲公司個資外洩事件調查結果：新增受害1000人','甲公司個資外洩100人',.99)

def test_editorial_number_does_not_create_update():
    assert check(TITLES[0]+'點1關鍵', TITLES[1])
