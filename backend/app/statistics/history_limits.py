"""Basic EOD statistics, deliberately independent of academic label eligibility."""
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from app.analytics.rules import regular_rates, rounded_limit_price, MAIN_ST_REFORM, CHINEXT_REFORM
from app.analytics.reconstruction import board_for

VERSION='historical-limit-statistics-v2.2'


def rates(code,day,is_st):
    return [float(r) for r in regular_rates(board_for(code),day,is_st)] if not code.endswith('.BJ') else []


def price(reference,rate):
    return rounded_limit_price(reference,rate)


def classification_sql():
    """Input: one row per code/session, original source pre_close never lag-filled.
    Source day evidence can confirm facts without formal research eligibility.
    SQL decimals implement the same tick calculation as price().
    """
    return f"""
    WITH context AS (
      SELECT *, CASE WHEN starts_with(stock_code,'68') THEN 'star'
         WHEN starts_with(stock_code,'30') THEN 'chinext'
         WHEN starts_with(stock_code,'00') OR starts_with(stock_code,'60') THEN 'main' ELSE 'other' END board,
         CASE WHEN is_st IS TRUE THEN 'confirmed_st' WHEN is_st IS FALSE THEN 'confirmed_non_st' ELSE 'unknown' END st_status,
         lag(session_index) OVER w previous_index,lag(close) OVER w previous_close,
         lag(trade_status) OVER w previous_trade_status
      FROM input WINDOW w AS (PARTITION BY stock_code ORDER BY trade_date)
    ), rules AS (
      SELECT *, CASE WHEN board='star' OR (board='chinext' AND trade_date>=DATE '{CHINEXT_REFORM.isoformat()}') THEN 20
           WHEN board='main' AND trade_date>=DATE '{MAIN_ST_REFORM.isoformat()}' THEN 10
           WHEN is_st IS TRUE THEN 5 ELSE 10 END rate_percent,
        (is_st IS NULL AND (board='main' AND trade_date<DATE '{MAIN_ST_REFORM.isoformat()}' OR board='chinext' AND trade_date<DATE '{CHINEXT_REFORM.isoformat()}')) ambiguous_st,
        CASE WHEN board='other' OR session_index IS NULL THEN 'outside_board_or_calendar'
          WHEN listing_date IS NULL OR trade_date<listing_date OR (delisting_date IS NOT NULL AND trade_date>delisting_date) THEN 'lifecycle_unknown_or_outside'
          WHEN (board='star' OR board='chinext' AND listing_date>=DATE '{CHINEXT_REFORM.isoformat()}' OR board='main' AND listing_date>=DATE '2023-04-10')
            AND listing_session BETWEEN 1 AND 5 THEN 'ipo_no_limit_first_five'
          WHEN (stock_code='000995.SZ' AND trade_date=DATE '2020-12-16') OR (stock_code='000792.SZ' AND trade_date=DATE '2021-08-10') THEN 'known_resumption_no_limit'
          WHEN event_conflict OR price_conflict OR status_conflict THEN 'source_conflict'
          WHEN trade_status IS FALSE THEN 'suspended'
        END hard_unknown,
        CASE WHEN trade_date=listing_date THEN 'legacy_ipo_special_limit'
          WHEN delisting_date IS NOT NULL AND datediff('day',trade_date,delisting_date)<=60 THEN 'delisting_special_period'
          WHEN stock_code='600182.SH' THEN 'non_reform_special_status_unresolved'
          WHEN previous_trade_status IS FALSE OR (previous_index IS NOT NULL AND session_index<>previous_index+1) THEN 'gap_or_resumption_special_unknown'
          WHEN previous_index IS NULL AND session_index>0 AND trade_date>listing_date THEN 'missing_prior_history'
          WHEN price_quality<>'verified' THEN 'missing_or_invalid_daily'
          WHEN pre_close IS NULL OR pre_close<=0 OR NOT isfinite(pre_close) OR abs(pre_close-round(pre_close,2))>1e-8 THEN 'reference_missing_or_invalid'
          WHEN previous_index=session_index-1 AND previous_close IS NOT NULL AND abs(pre_close-previous_close)>.011 THEN 'ex_reference_unverified'
        END rebuild_unknown
      FROM context
    ), limits AS (
      SELECT *,greatest(CAST(pre_close AS DECIMAL(20,2))+.01,round(CAST(pre_close AS DECIMAL(20,2))*(1+rate_percent::DECIMAL(5,2)/100),2)) derived_price,
        greatest(CAST(pre_close AS DECIMAL(20,2))+.01,round(CAST(pre_close AS DECIMAL(20,2))*1.05,2)) st_price
      FROM rules
    ), judged AS (
      SELECT *, CASE
        WHEN hard_unknown IS NOT NULL THEN NULL
        WHEN is_limit_up IS NOT NULL AND limit_quality='verified' THEN is_limit_up
        WHEN official_limit_up_price>0 AND price_quality='verified' AND high<=official_limit_up_price+.000001 THEN abs(close-official_limit_up_price)<.000001
        WHEN official_limit_up_price>0 THEN NULL
        WHEN rebuild_unknown IS NOT NULL THEN NULL
        WHEN high>derived_price+.000001 THEN NULL
        ELSE abs(close-derived_price)<.000001 OR (ambiguous_st AND high<=st_price+.000001 AND abs(close-st_price)<.000001)
      END up,
      CASE WHEN hard_unknown IS NOT NULL THEN hard_unknown
        WHEN is_limit_up IS NOT NULL AND limit_quality='verified' THEN 'direct_event_or_existing_day_evidence'
        WHEN official_limit_up_price>0 AND price_quality='verified' AND high<=official_limit_up_price+.000001 THEN 'trusted_historical_limit_price'
        WHEN official_limit_up_price>0 THEN 'official_limit_price_conflict'
        WHEN rebuild_unknown IS NOT NULL THEN rebuild_unknown
        WHEN high>derived_price+.000001 THEN 'price_exceeds_rule_special_unknown'
        WHEN ambiguous_st THEN 'st_unknown_5_or_10_scenario'
        WHEN st_status='unknown' THEN 'regime_candidate_st_still_unknown'
        ELSE 'dated_regime_candidate' END reason
      FROM limits
    ) SELECT stock_code,stock_name,trade_date,session_index,open,high,low,close,pre_close,board,st_status,up,
      CASE WHEN up IS NULL THEN 'unknown' WHEN reason IN ('direct_event_or_existing_day_evidence','trusted_historical_limit_price') THEN 'confirmed' ELSE 'reconstructed_candidate' END status,
      reason, CASE WHEN reason='trusted_historical_limit_price' THEN official_limit_up_price
        WHEN reason='direct_event_or_existing_day_evidence' THEN NULL
        WHEN ambiguous_st AND abs(close-st_price)<.000001 AND high<=st_price+.000001 THEN st_price ELSE derived_price END limit_price,
      CASE WHEN ambiguous_st AND abs(close-st_price)<.000001 AND high<=st_price+.000001 THEN 5 ELSE rate_percent END candidate_rate_percent,
      CASE WHEN up IS NULL OR price_quality<>'verified' OR open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL
          OR NOT (isfinite(open) AND isfinite(high) AND isfinite(low) AND isfinite(close))
          OR low<=0 OR low>least(open,close) OR high<greatest(open,close) THEN 'unknown'
        WHEN up IS TRUE AND abs(high-close)>.000001 THEN 'unknown'
        WHEN up IS TRUE AND abs(low-close)<.000001 THEN 'one_price'
        WHEN up IS TRUE AND abs(open-close)<.000001 AND low<close THEN 't_shape'
        WHEN up IS TRUE THEN 'other_close_up'
        WHEN up IS FALSE AND hard_unknown IS NULL AND
          ((official_limit_up_price>0 AND abs(high-official_limit_up_price)<.000001 AND close<official_limit_up_price)
           OR (official_limit_up_price IS NULL AND rebuild_unknown IS NULL AND NOT ambiguous_st AND abs(high-derived_price)<.000001 AND close<derived_price))
          THEN 'touch_unsealed'
        ELSE 'not_limit_up' END daily_shape,
      (price_quality='verified') daily_present,event_source,consecutive_limit_days vendor_height,
      CASE WHEN reference_recovered THEN 'raw_close_minus_change_pct_checked' WHEN pre_close>0 THEN 'source_same_day_pre_close_not_lag_fill' ELSE 'missing' END reference_basis
    FROM judged
    """


def streak_sql():
    # Keep unknowns and missing calendar dates as boundaries. Do not filter by year before windows.
    return """
    WITH boundaries AS (
      SELECT *,lag(up) OVER w prior_up,lag(session_index) OVER w prior_index,lead(up) OVER w next_up,lead(session_index) OVER w next_index
      FROM days WINDOW w AS (PARTITION BY stock_code ORDER BY session_index)
    ), positives AS (
      SELECT *,session_index-row_number() OVER (PARTITION BY stock_code ORDER BY session_index) segment
      FROM boundaries WHERE up IS TRUE
    ), mixed AS (
      SELECT *,row_number() OVER w height,min(trade_date) OVER w run_start,
        first_value(prior_up) OVER w IS FALSE AND first_value(prior_index) OVER w=first_value(session_index) OVER w-1 exact_start,
        CASE WHEN count(*) FILTER(WHERE status='reconstructed_candidate') OVER w>0 THEN 'reconstructed_candidate' ELSE 'confirmed' END chain_status
      FROM positives WINDOW w AS (PARTITION BY stock_code,segment ORDER BY session_index ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW)
    ), confirmed_groups AS (
      SELECT stock_code,trade_date,session_index,session_index-row_number() OVER(PARTITION BY stock_code ORDER BY session_index) segment
      FROM days WHERE up IS TRUE AND status='confirmed'
    ), confirmed AS (
      SELECT stock_code,trade_date,row_number() OVER w confirmed_height,min(trade_date) OVER w confirmed_run_start
      FROM confirmed_groups WINDOW w AS (PARTITION BY stock_code,segment ORDER BY session_index)
    ) SELECT m.*,(next_up IS FALSE AND next_index=session_index+1) exact_end,CASE WHEN exact_start THEN run_start END first_board_date,c.confirmed_height,c.confirmed_run_start
      FROM mixed m LEFT JOIN confirmed c USING(stock_code,trade_date)
    """
