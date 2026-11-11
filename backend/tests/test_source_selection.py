"""Source-policy regression: no network, writes or active snapshot needed."""
import duckdb
import pytest
from scripts.finalize_research import select_daily_prices


@pytest.fixture
def db():
    with duckdb.connect() as c:
        c.execute('''CREATE TABLE raw_daily(stock_code VARCHAR,trade_date DATE,open DOUBLE,high DOUBLE,
          low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,pre_close DOUBLE,source VARCHAR)''')
        c.execute('''CREATE TABLE pg_stock_daily(stock_code VARCHAR,trade_date DATE,open DOUBLE,high DOUBLE,
          low DOUBLE,close DOUBLE,volume DOUBLE,amount DOUBLE,pre_close DOUBLE,provider VARCHAR,
          id BIGINT,raw_id BIGINT,adjustment VARCHAR,ingestion_status VARCHAR,fetched_at TIMESTAMP)''')
        yield c


def add(c, source, day='2026-11-09', *, close=10, ref=10, volume=100, amount=1000,
        high=11, low=9, id=1, adjustment='none', status='success'):
    values=['600537.SH',day,10,high,low,close,volume,amount,ref,source]
    if source in ('akshare','baostock'):
        c.execute('INSERT INTO pg_stock_daily VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  values+[id,100+id,adjustment,status,'2026-11-10'])
    else:c.execute('INSERT INTO raw_daily VALUES (?,?,?,?,?,?,?,?,?,?)',values)


def select(c):
    def export(name,sql):c.execute('CREATE TABLE '+name+' AS '+sql)
    select_daily_prices(c,export)
    return c.sql('SELECT source,close,pre_close FROM prices').fetchall()


@pytest.mark.parametrize('day,expected',[('2017-01-03','purchased_test'),('2026-03-13','purchased_test'),
 ('2026-03-16','akshare'),('2026-11-09','akshare')])
def test_date_policy(db,day,expected):
    for src in ['purchased_test','akshare','baostock']:add(db,src,day)
    assert select(db)==[(expected,10,10)]


def test_pg_overlap_is_compared_before_preference(db):
    add(db,'akshare');add(db,'baostock',close=10.01)
    assert select(db)==[]
    assert db.sql('SELECT candidates,ohlc_conflict FROM price_conflicts').fetchall()[0]==(2,True)
    assert db.sql('SELECT count(*) FROM price_candidates').fetchall()[0][0]==2


def test_reference_only_conflict_is_quarantined(db):
    add(db,'akshare');add(db,'baostock',ref=9.5)
    assert select(db)==[]
    assert db.sql('SELECT ohlc_conflict,reference_conflict FROM price_conflicts').fetchall()[0]==(False,True)


@pytest.mark.parametrize('bad', [{'close':0},{'close':float('nan')},{'high':float('inf')},
 {'volume':-1},{'ref':-1}])
def test_invalid_primary_falls_back_but_remains_evidence(db,bad):
    add(db,'akshare',**bad);add(db,'baostock')
    assert select(db)==[('baostock',10,10)]
    assert db.sql('SELECT count(*) FROM price_candidates WHERE NOT selectable').fetchall()[0][0]==1


def test_missing_primary_uses_baostock(db):
    add(db,'baostock');assert select(db)==[('baostock',10,10)]


def test_latest_revision_per_provider_not_latest_provider(db):
    add(db,'akshare',close=9.5,id=1);add(db,'akshare',id=2);add(db,'baostock',id=3)
    assert select(db)==[('akshare',10,10)]
    assert db.sql('SELECT count(*) FROM pg_price_candidates').fetchall()[0][0]==2


def test_incompatible_adjustment_and_failed_ingestion_not_compared(db):
    add(db,'akshare');add(db,'baostock',close=9.5,adjustment='qfq');add(db,'baostock',close=9.4,id=2,status='failed')
    assert select(db)==[('akshare',10,10)]
    assert db.sql('SELECT candidates FROM price_conflicts').fetchall()[0][0]==1


def test_single_source_missing_reference_is_not_fabricated(db):
    add(db,'akshare',ref=None);assert select(db)==[('akshare',10,None)]
    assert db.sql('SELECT candidate_reason FROM prices').fetchall()[0][0]=='reference_missing_pending_D2'


def test_volume_difference_is_explicit_not_price_conflict(db):
    add(db,'akshare');add(db,'baostock',volume=101,amount=1010)
    assert select(db)==[('akshare',10,10)]
    assert db.sql('SELECT volume_conflict,amount_conflict,ohlc_conflict FROM price_conflicts').fetchall()[0]==(True,True,False)


def test_invalid_only_has_no_selected_price(db):
    add(db,'baostock',close=0);assert select(db)==[]
    assert db.sql('SELECT count(*) FROM price_candidates').fetchall()[0][0]==1


def test_no_majority_vote(db):
    add(db,'purchased_test','2026-03-13');add(db,'baostock','2026-03-13');add(db,'akshare','2026-03-13',close=10.01)
    assert select(db)==[]


def test_existing_akshare_file_wins_equal_provider_pg_copy(db):
    add(db,'akshare_em_unadjusted_test');add(db,'akshare')
    assert select(db)==[('akshare_em_unadjusted_test',10,10)]


def test_bad_reference_does_not_hide_otherwise_valid_ohlc_conflict(db):
    add(db,'akshare',ref=-1,close=10.01);add(db,'baostock')
    assert select(db)==[]
    assert db.sql('SELECT ohlc_conflict FROM price_conflicts').fetchall()[0][0]


def test_repair_output_cannot_overwrite_existing_directory(tmp_path):
    from scripts.build_source_repair import build
    with pytest.raises(ValueError,match='new directory'):
        build(tmp_path/'absent_parent',tmp_path)


def test_full_builder_defaults_to_candidate_only():
    import inspect
    from scripts.finalize_research import build
    assert inspect.signature(build).parameters['candidate_only'].default is True
