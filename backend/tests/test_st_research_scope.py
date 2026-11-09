from copy import deepcopy
from app.research.st_research_scope import apply, trading_candidates, status, separate_rates


def fixture():
    days={('600001.SH','2020-01-02'):dict(non_st_confirmed=True,evidence_ids=['baostock:1']),
          ('600002.SH','2020-01-02'):dict(reasons=['risk_warning_confirmed'],evidence_ids=['dated:2'])}
    return {'by_sample':{},'by_day':days}


def test_unknown_st_never_erases_confirmed_fact_or_outcome():
    rows=[dict(stock_code=f'60000{i}.SH',first_board_date='2020-01-02',outcome='success',observed_height=5,exact_height=7,evidence_grade='verified',regime='main') for i in (1,2,3)]
    before=deepcopy(rows)
    selected,meta=apply(rows,data=fixture())
    assert rows==before
    assert [r['outcome'] for r in selected]==['success']*3
    assert [r['historical_st']['status'] for r in selected]==['confirmed_non_st','confirmed_st','unknown']
    assert len(meta['groups'])==3 and not meta['pooled_rate_allowed']
    for scope in ('confirmed_non_st','confirmed_st','unknown'):
        result,_=apply(rows,scope=scope,data=fixture());assert len(result)==1
        assert result[0]['exact_height']==7


def test_strict_non_st_excludes_missing_window_and_retains_fact_in_all():
    row=dict(stock_code='600001.SH',first_board_date='2020-01-02',outcome='failure',progress=[{'trade_date':'2020-01-02'},{'trade_date':'2020-01-03'}])
    assert not apply([row],scope='confirmed_non_st',data=fixture())[0]
    result,_=apply([row],data=fixture())
    assert result[0]['outcome']=='failure' and result[0]['historical_st']['first_date_status']=='confirmed_non_st'
    assert result[0]['historical_st']['status']=='unknown'


def test_conflict_and_delisting_are_not_non_st_inferences():
    assert status(dict(non_st_confirmed=True,conflict=True))=='unknown'
    assert status(dict(reasons=['delisting_period_confirmed']))=='unknown'


def test_trading_defaults_fail_closed_without_research_fact_mutation():
    rows=[dict(stock_code='600001.SH',trade_date='2020-01-02',stock_name='正常名称')]
    assert trading_candidates(rows,data=fixture())[0]==[]
    d=fixture();d['by_day'][('600001.SH','2020-01-02')]['status']='eligible'
    assert trading_candidates(rows,data=d)[0]==rows
    for name in ('ST样本','*ST样本','退市样本'):
        assert not trading_candidates([{**rows[0],'stock_name':name}],data=d)[0]


def test_pooled_rates_are_suppressed_counts_and_archive_untouched():
    stats={'summary':{'success':76,'promotion_rate':.014},'months':[{'rate':.01,'success':2}]}
    before=deepcopy(stats)
    result=separate_rates(stats,{'pooled_rate_allowed':False,'groups':[]})
    assert result['summary']['success']==76 and result['summary']['promotion_rate'] is None
    assert result['months'][0]['rate'] is None and stats==before


def test_absent_status_archive_keeps_all_facts_as_st_unknown(monkeypatch):
    import app.research.st_research_scope as module
    def missing():raise FileNotFoundError('no ST archive')
    monkeypatch.setattr(module,'bundle',missing)
    row=dict(stock_code='600001.SH',first_board_date='2020-01-02',outcome='success',exact_height=5)
    rows,meta=module.apply([row])
    assert rows[0]['outcome']=='success' and rows[0]['exact_height']==5
    assert rows[0]['historical_st']['status']=='unknown'
    assert module.apply([row],scope='confirmed_non_st')[0]==[]


def test_same_st_status_different_regimes_are_not_pooled():
    rows=[dict(stock_code='600001.SH',first_board_date='2020-01-02',outcome='failure',regime=r) for r in ('main_10','star_20')]
    _,meta=apply(rows,data=fixture())
    assert len(meta['groups'])==2 and not meta['pooled_rate_allowed']
