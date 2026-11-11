from datetime import date

import duckdb
import pyarrow as pa
import pytest

from app.statistics.market_breadth import classification_sql, daily_sql, validate_daily


SCHEMA = pa.schema([
    ('stock_code', pa.string()), ('trade_date', pa.date32()),
    *[(k, pa.float64()) for k in ('open','high','low','close','pre_close','recovered_reference','prior_close')],
    *[(k, pa.bool_()) for k in ('is_st','trade_status','status_conflict','price_conflict',
                               'pg_reference_verified','prior_price_verified')],
    *[(k, pa.string()) for k in ('price_source','price_quality','st_quality','lifecycle_status')],
])


def stock(code='600001.SH', **changes):
    return dict(stock_code=code,trade_date=date(2026,11,9),open=10.,high=11.,low=9.,close=11.,
                pre_close=10.,recovered_reference=None,prior_close=None,is_st=None,trade_status=None,
                status_conflict=False,price_conflict=False,pg_reference_verified=True,
                prior_price_verified=False,price_source='akshare',price_quality='verified',
                st_quality='unknown',lifecycle_status='verified') | changes


def compute(items, calendar=None):
    with duckdb.connect() as db:
        db.register('input',pa.Table.from_pylist(items,schema=SCHEMA))
        db.register('calendar_slice',pa.table({'trade_date':calendar or sorted({r['trade_date'] for r in items})}))
        db.execute('CREATE TABLE evaluated AS '+classification_sql())
        evaluated=db.execute('SELECT stock_code,price_reason,direction,reference_basis FROM evaluated').fetchall()
        cursor=db.execute(daily_sql())
        result=[dict(zip([c[0] for c in cursor.description],r)) for r in cursor.fetchall()]
        validate_daily(result)
        return result,evaluated


def test_breadth_keeps_st_unknown_and_lifecycle_conflicts_and_excludes_bse():
    result,_=compute([
        stock(lifecycle_status='conflict'),
        stock('000001.SZ',close=9.,is_st=True,st_quality='verified'),
        stock('688001.SH',close=10.,is_st=False,st_quality='verified'),
        stock('600002.SH',pre_close=None,pg_reference_verified=False),
        stock('920001.BJ'),
    ])
    r=result[0]
    assert (r['observed_count'],r['up'],r['down'],r['flat'],r['price_unknown_count'])==(4,1,1,1,1)
    assert r['median_return']==0 and r['advancing_ratio']==pytest.approx(1/3)
    assert (r['non_st_confirmed_count'],r['st_confirmed_count'],r['st_unknown_count'])==(1,1,2)
    assert r['valid_st_unknown_count']==1
    assert r['lifecycle_conflict_valid_count']==r['lifecycle_conflict_observed_count']==1
    assert r['coverage_ratio'] is None and r['expected_universe_count'] is None


@pytest.mark.parametrize('changes,reason',[
    ({'pre_close':None,'prior_close':10.,'prior_price_verified':True},'reference_missing'),
    ({'pre_close':0.},'reference_invalid'),
    ({'pre_close':float('nan')},'reference_invalid'),
    ({'pre_close':float('inf')},'reference_invalid'),
    ({'pre_close':10.001},'reference_invalid'),
    ({'close':None},'missing_or_invalid_price'),
    ({'close':float('nan')},'missing_or_invalid_price'),
    ({'trade_status':False},'suspended'),
    ({'price_conflict':True},'price_conflict'),
    ({'price_quality':'diagnostic'},'missing_or_invalid_price'),
    ({'pg_reference_verified':False},'reference_unverified'),
])
def test_invalid_or_missing_never_becomes_flat_or_lag_filled(changes,reason):
    result,evaluated=compute([stock(**changes)])
    assert evaluated[0][1:3]==(reason,'unknown')
    assert result[0]['price_unknown_count']==1
    assert result[0]['up'] is result[0]['down'] is result[0]['flat'] is None
    assert result[0]['median_return'] is None


def test_reference_evidence_and_identity_are_independent():
    result,evaluated=compute([
        stock(pre_close=None,recovered_reference=10.,pg_reference_verified=False),
        stock('600002.SH',price_source='purchased_test',pg_reference_verified=False,
              prior_price_verified=True,prior_close=10.,is_st=False,st_quality='verified',status_conflict=True),
        stock('600003.SH',price_source='purchased_test',pg_reference_verified=False,
              prior_price_verified=True,prior_close=9.),
    ])
    assert [r[1] for r in evaluated]==['valid','valid','reference_unverified']
    assert result[0]['price_valid_count']==2 and result[0]['st_unknown_count']==3
    # An invalid non-null supplied reference is never overwritten by a recovery.
    _,evaluated=compute([stock(pre_close=0.,recovered_reference=10.)])
    assert evaluated[0][1]=='reference_invalid'


def test_decimal_direction_small_return_and_day_isolation():
    items=[stock(close=10.),stock('600002.SH',trade_date=date(2026,11,10),
           open=1000.,high=1000.01,low=1000.,close=1000.01,pre_close=1000.)]
    result,_=compute(items,[date(2026,11,9),date(2026,11,10),date(2026,11,11)])
    assert result[0]['flat']==1 and result[0]['up']==0
    assert result[1]['up']==1 and result[1]['flat']==0
    assert result[1]['median_return']==pytest.approx(.00001)
    assert result[2]['status']=='no_observations' and result[2]['up'] is None
    assert result[2]['advancing_ratio'] is None and result[2]['observed_count']==0


def test_same_input_deterministic_and_median_ignores_unknown():
    items=[stock(),stock('600002.SH',close=10.),stock('600003.SH',pre_close=None)]
    a,_=compute(items);b,_=compute(list(reversed(items)))
    assert a==b
    assert a[0]['median_return']==pytest.approx(.05)


def test_real_join_uses_previous_session_and_matching_same_day_recovery():
    from scripts.build_market_breadth import calculate
    from app.statistics.reference_recovery import SOURCE
    with duckdb.connect() as db:
        # Exercise the production joins, including month boundaries and a missing session.
        items=[stock(price_source='purchased_test',trade_date=date(2026,10,30),close=10.),
               stock(price_source='purchased_test',trade_date=date(2026,11,2)),
               stock('600002.SH',price_source=SOURCE,pre_close=None),
               stock('600003.SH',price_source=SOURCE,pre_close=None),
               stock('600004.SH',price_source=SOURCE,pre_close=0.)]
        table=pa.Table.from_pylist(items,schema=SCHEMA).drop([
            'prior_close','prior_price_verified','pg_reference_verified','recovered_reference','lifecycle_status'])
        table=table.append_column('pg_daily_id',pa.array([None]*len(items),type=pa.int64()))
        db.register('fixture',table)
        db.execute('CREATE SCHEMA src')
        db.execute('CREATE TABLE src.stock_daily AS SELECT * FROM fixture')
        db.execute("CREATE TABLE cal(trade_date DATE,prev DATE)")
        db.execute("INSERT INTO cal VALUES ('2026-10-30','2026-10-29'),('2026-11-02','2026-10-30'),('2026-11-09','2026-11-06')")
        db.execute('CREATE TABLE recovered_refs(stock_code VARCHAR,trade_date DATE,recovered_reference DOUBLE,raw_close DOUBLE,raw_open DOUBLE,raw_high DOUBLE,raw_low DOUBLE)')
        db.execute("INSERT INTO recovered_refs VALUES ('600002.SH','2026-11-09',10,11,10,11,9),('600003.SH','2026-11-09',10,12,10,12,9),('600004.SH','2026-11-09',10,11,10,11,9)")
        db.execute('CREATE TABLE pg_daily(id BIGINT,adjustment VARCHAR,ingestion_status VARCHAR,provider VARCHAR,stock_code VARCHAR,trade_date DATE,pre_close DOUBLE,close DOUBLE,open DOUBLE,high DOUBLE,low DOUBLE,pct_change DOUBLE)')
        db.execute('CREATE TABLE lifecycle(stock_code VARCHAR,status VARCHAR)')
        daily,_=calculate(db,date(2026,11,2),date(2026,11,9))
        assert daily[0]['up']==1  # The supplied reference is corroborated across month end.
        assert daily[1]['up']==1 and daily[1]['price_unknown_count']==2
        reasons=dict(db.execute('SELECT stock_code,price_reason FROM evaluated WHERE trade_date=DATE \'2026-11-09\'').fetchall())
        assert reasons=={'600002.SH':'valid','600003.SH':'reference_missing','600004.SH':'reference_invalid'}
        db.execute("UPDATE cal SET prev='2026-10-29' WHERE trade_date='2026-11-02'")
        daily,_=calculate(db,date(2026,11,2),date(2026,11,2))
        assert daily[0]['up'] is None  # Do not fall back to the nearest observed close.
