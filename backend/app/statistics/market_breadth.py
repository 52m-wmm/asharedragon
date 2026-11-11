"""Observed-sample EOD breadth. No universe inference, promotion or signal model."""

VERSION = 'observed-market-breadth-v1'
SCOPE = '沪深 A 股可观测价格样本（包含 ST 和身份未知；非全市场覆盖承诺）'


def classification_sql():
    """Input has one row per observed stock/session plus same-day reference evidence.

    A previous close can corroborate a supplied reference, never fill a missing one.
    Lifecycle conflicts are annotations only: no listing/delisting filter is applied.
    """
    return """
    WITH refs AS (
      SELECT *,
        regexp_full_match(stock_code, '(60|68)[0-9]{4}\\.SH|(00|30)[0-9]{4}\\.SZ') in_scope,
        CASE WHEN status_conflict OR st_quality IS DISTINCT FROM 'verified' THEN 'unknown'
          WHEN is_st IS TRUE THEN 'confirmed_st'
          WHEN is_st IS FALSE THEN 'confirmed_non_st' ELSE 'unknown' END identity_status,
        CASE WHEN pre_close IS NULL AND recovered_reference IS NOT NULL
          THEN recovered_reference ELSE pre_close END reference_price,
        CASE WHEN pre_close IS NULL AND recovered_reference IS NOT NULL THEN 'raw_close_change_pct_ohlc_checked'
          WHEN pre_close IS NOT NULL AND pg_reference_verified THEN 'same_response_reference_pct_checked'
          WHEN pre_close IS NOT NULL AND price_source LIKE 'purchased_%'
            AND prior_price_verified AND abs(pre_close-prior_close)<1e-8
            THEN 'source_reference_adjacent_session_checked'
          ELSE 'unverified' END reference_basis
      FROM input
    ), judged AS (
      SELECT *, CASE
        WHEN NOT in_scope THEN 'outside_scope'
        WHEN trade_status IS FALSE THEN 'suspended'
        WHEN price_conflict THEN 'price_conflict'
        WHEN price_quality IS DISTINCT FROM 'verified' OR close IS NULL OR open IS NULL
          OR high IS NULL OR low IS NULL OR NOT (isfinite(close) AND isfinite(open)
          AND isfinite(high) AND isfinite(low)) OR least(close,open,high,low)<=0
          OR high<greatest(open,close,low) OR low>least(open,close)
          THEN 'missing_or_invalid_price'
        WHEN abs(close-round(close,2))>1e-8 THEN 'price_not_on_tick'
        WHEN reference_price IS NULL THEN 'reference_missing'
        WHEN NOT isfinite(reference_price) OR reference_price<=0
          OR abs(reference_price-round(reference_price,2))>1e-8
          THEN 'reference_invalid'
        WHEN reference_basis='unverified' THEN 'reference_unverified'
        ELSE 'valid' END price_reason
      FROM refs
    ) SELECT *,
      CASE WHEN price_reason='valid' THEN
        CAST(close AS DECIMAL(20,2))/CAST(reference_price AS DECIMAL(20,2))-1 END daily_return,
      CASE WHEN price_reason='valid' THEN
        CASE WHEN CAST(close AS DECIMAL(20,2))>CAST(reference_price AS DECIMAL(20,2)) THEN 'up'
          WHEN CAST(close AS DECIMAL(20,2))<CAST(reference_price AS DECIMAL(20,2)) THEN 'down'
          ELSE 'flat' END ELSE 'unknown' END direction
    FROM judged
    """


def daily_sql():
    """calendar_slice includes sessions without observations; zero != unavailable."""
    return """
    WITH counts AS (
      SELECT trade_date,count(*) observed_count,
        count(*) FILTER(WHERE close IS NOT NULL) price_observed_count,
        count(*) FILTER(WHERE price_reason='valid') price_valid_count,
        count(*) FILTER(WHERE price_reason<>'valid') price_unknown_count,
        count(*) FILTER(WHERE direction='up') up,
        count(*) FILTER(WHERE direction='down') down,
        count(*) FILTER(WHERE direction='flat') flat,
        median(daily_return) median_return,
        count(*) FILTER(WHERE identity_status='confirmed_non_st') non_st_confirmed_count,
        count(*) FILTER(WHERE identity_status='confirmed_st') st_confirmed_count,
        count(*) FILTER(WHERE identity_status='unknown') st_unknown_count,
        count(*) FILTER(WHERE identity_status='confirmed_non_st' AND price_reason='valid') valid_non_st_count,
        count(*) FILTER(WHERE identity_status='confirmed_st' AND price_reason='valid') valid_st_count,
        count(*) FILTER(WHERE identity_status='unknown' AND price_reason='valid') valid_st_unknown_count,
        count(*) FILTER(WHERE trade_status IS FALSE) suspended_count,
        count(*) FILTER(WHERE trade_status IS NULL OR status_conflict) trade_status_unknown_count,
        count(*) FILTER(WHERE lifecycle_status='conflict') lifecycle_conflict_observed_count,
        count(*) FILTER(WHERE lifecycle_status='conflict' AND price_reason='valid') lifecycle_conflict_valid_count
      FROM evaluated WHERE in_scope GROUP BY trade_date
    ) SELECT c.trade_date,
      coalesce(o.observed_count,0) observed_count,
      coalesce(o.price_observed_count,0) price_observed_count,
      coalesce(o.price_valid_count,0) price_valid_count,
      coalesce(o.price_unknown_count,0) price_unknown_count,
      CASE WHEN o.price_valid_count>0 THEN o.up END up,
      CASE WHEN o.price_valid_count>0 THEN o.down END down,
      CASE WHEN o.price_valid_count>0 THEN o.flat END flat,
      o.up::DOUBLE/nullif(o.price_valid_count,0) advancing_ratio,o.median_return,
      o.price_valid_count::DOUBLE/nullif(o.observed_count,0) observed_valid_ratio,
      coalesce(o.non_st_confirmed_count,0) non_st_confirmed_count,
      coalesce(o.st_confirmed_count,0) st_confirmed_count,
      coalesce(o.st_unknown_count,0) st_unknown_count,
      coalesce(o.valid_non_st_count,0) valid_non_st_count,
      coalesce(o.valid_st_count,0) valid_st_count,
      coalesce(o.valid_st_unknown_count,0) valid_st_unknown_count,
      coalesce(o.suspended_count,0) suspended_count,
      coalesce(o.trade_status_unknown_count,0) trade_status_unknown_count,
      coalesce(o.lifecycle_conflict_observed_count,0) lifecycle_conflict_observed_count,
      coalesce(o.lifecycle_conflict_valid_count,0) lifecycle_conflict_valid_count,
      NULL::DOUBLE coverage_ratio,NULL::BIGINT expected_universe_count,
      'unknown' universe_status,'historical_universe_and_suspensions_unverified' coverage_reason,
      CASE WHEN o.price_valid_count>0 THEN 'observed_sample'
        WHEN coalesce(o.observed_count,0)>0 THEN 'no_valid_prices' ELSE 'no_observations' END status
    FROM calendar_slice c LEFT JOIN counts o USING(trade_date) ORDER BY c.trade_date
    """


def validate_daily(rows):
    for r in rows:
        assert sum(r[k] or 0 for k in ('up', 'down', 'flat')) == r['price_valid_count']
        assert r['price_valid_count'] + r['price_unknown_count'] == r['observed_count']
        assert sum(r[k] for k in ('non_st_confirmed_count', 'st_confirmed_count', 'st_unknown_count')) == r['observed_count']
        assert sum(r[k] for k in ('valid_non_st_count', 'valid_st_count', 'valid_st_unknown_count')) == r['price_valid_count']
        assert r['coverage_ratio'] is None and r['expected_universe_count'] is None
        assert r['universe_status'] == 'unknown'
        if not r['price_valid_count']:
            assert all(r[k] is None for k in ('up', 'down', 'flat', 'median_return', 'advancing_ratio'))
