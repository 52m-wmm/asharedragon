from datetime import date
from app.research.announcement_evidence import closing_ranges


def test_closing_statement_count_must_match_calendar_not_target_label():
    days=[date(2023,9,28),date(2023,10,9)]
    txt='证券代码：600000 公司股票自9月28日至10月9日2个交易日中2个交易日收盘价格涨停'
    result=closing_ranges(txt,date(2023,10,10),days)
    assert result[0]['stock_code']=='600000.SH' and result[0]['dates']==days
    assert closing_ranges(txt.replace('2个','3个'),date(2023,10,10),days)==[]


def test_ambiguous_and_intraday_not_expanded():
    days=[date(2024,3,5),date(2024,3,6)]
    txt='证券代码：000001 自2024年3月4日以来已连续2个交易日收盘价涨停'
    assert closing_ranges(txt,date(2024,3,7),days)==[]
    assert closing_ranges('证券代码：000001 3月5日至6日盘中涨停',date(2024,3,7),days)==[]
    txt='证券代码：000001 2024年3月6日公司股票以涨停价收盘，自3月5日至6日，已连续2个交易日涨停'
    assert closing_ranges(txt,date(2024,3,7),days)[0]['stated_sessions']==2


def test_explicit_historical_year_is_not_publication_year():
    days=[date(2022,9,28),date(2022,9,29)]
    txt='证券代码：600000 自2022年9月28日至9月29日2个交易日中2个交易日收盘价格涨停'
    assert closing_ranges(txt,date(2023,1,4),days)[0]['dates']==days
    assert closing_ranges(txt,date(2023,1,4),[date(2023,9,28),date(2023,9,29)])==[]
