"""Offline candidate bounds only. Never promotes derived prices to official facts."""
from decimal import Decimal,ROUND_HALF_UP
from app.analytics.rules import regular_rates,rounded_limit_price
VERSION='daily-limit-candidate-v1'

def bounds(reference,board,day,is_st):
 p=Decimal(str(reference))
 if not p.is_finite() or p<=0 or p!=p.quantize(Decimal('.01')):raise ValueError('Invalid reference')
 return [(r,rounded_limit_price(p,r),max(Decimal('.01'),min(p-Decimal('.01'),(p*(1-r)).quantize(Decimal('.01'),rounding=ROUND_HALF_UP)))) for r in regular_rates(board,day,is_st)]

def candidate_sql():
 # E1 evaluated supplies checked *same day* reference; never substitute lag(close).
 return """WITH context AS (
 SELECT e.stock_code,e.trade_date,e.stock_day_id,e.open,e.high,e.low,e.close,e.reference_price,e.reference_basis,
 e.price_source,e.pg_daily_id,e.supplement_raw_id,e.price_reason,e.identity_status,e.lifecycle_status,
 e.trade_status,e.price_conflict,e.event_conflict,e.status_conflict,s.listing_date,s.delisting_date,
 CASE WHEN starts_with(e.stock_code,'68') THEN 'star' WHEN starts_with(e.stock_code,'30') THEN 'chinext'
 WHEN starts_with(e.stock_code,'00') OR starts_with(e.stock_code,'60') THEN 'main' ELSE 'outside_scope' END board,
 c.idx-l.idx+1 listing_session,prev.trade_status previous_trade_status,prev.stock_code previous_stock_code,
 CASE WHEN e.price_reason<>'valid' THEN e.price_reason
 WHEN s.listing_date IS NULL OR e.trade_date<s.listing_date OR (s.delisting_date IS NOT NULL AND e.trade_date>s.delisting_date) THEN 'lifecycle_unknown_or_outside'
 WHEN c.idx IS NULL OR l.idx IS NULL THEN 'listing_calendar_unknown'
 WHEN c.idx-l.idx+1<=5 AND (starts_with(e.stock_code,'68') OR starts_with(e.stock_code,'30') AND s.listing_date>=DATE '2020-08-24' OR (starts_with(e.stock_code,'60') OR starts_with(e.stock_code,'00')) AND s.listing_date>=DATE '2023-04-10') THEN 'ipo_no_limit_first_five'
 WHEN e.trade_date=s.listing_date THEN 'legacy_ipo_special'
 WHEN s.delisting_date IS NOT NULL AND datediff('day',e.trade_date,s.delisting_date)<=60 THEN 'delisting_special_unverified'
 WHEN e.stock_code='600182.SH' THEN 'non_reform_status_unverified'
 WHEN prev.trade_status IS FALSE OR prev.stock_code IS NULL THEN 'resumption_or_prior_history_unverified'
 WHEN abs(e.open-round(e.open,2))>1e-8 OR abs(e.high-round(e.high,2))>1e-8 OR abs(e.low-round(e.low,2))>1e-8 THEN 'ohlc_not_on_tick'
 END exclusion
 FROM evaluated e LEFT JOIN src.stocks s USING(stock_code)
 LEFT JOIN listing_cal c ON c.trade_date=e.trade_date LEFT JOIN listing_cal l ON l.trade_date=s.listing_date
 LEFT JOIN src.stock_daily prev ON prev.stock_code=e.stock_code AND prev.trade_date=c.prev
 ), prices AS (
 SELECT c.*,r.rate,
 greatest(CAST(reference_price AS DECIMAL(20,2))+.01,round(CAST(reference_price AS DECIMAL(20,2))*(1+r.rate),2)) upper_price,
 greatest(.01,least(CAST(reference_price AS DECIMAL(20,2))-.01,round(CAST(reference_price AS DECIMAL(20,2))*(1-r.rate),2))) lower_price
 FROM context c LEFT JOIN rate_options r ON c.trade_date=r.trade_date AND c.board=r.board AND c.identity_status=r.identity_status
 ), scenarios AS (
 SELECT *,exclusion IS NULL AND rate IS NOT NULL AND high<=upper_price+.000001 AND low>=lower_price-.000001 compatible,
 abs(close-upper_price)<.000001 up_hit,abs(close-lower_price)<.000001 down_hit,
 abs(high-upper_price)<.000001 AND close<upper_price-.000001 broken_hit FROM prices
 ) SELECT stock_code,trade_date,first(stock_day_id) stock_day_id,first(open) open,first(high) high,first(low) low,first(close) close,
 first(reference_price) reference_price,first(reference_basis) reference_basis,first(price_source) price_source,
 first(pg_daily_id) pg_daily_id,first(supplement_raw_id) supplement_raw_id,first(board) board,first(identity_status) identity_status,
 first(lifecycle_status) lifecycle_status,first(listing_date) listing_date,first(delisting_date) delisting_date,first(listing_session) listing_session,
 first(price_reason) price_reason,first(trade_status) trade_status,bool_or(event_conflict) event_conflict,bool_or(status_conflict) status_conflict,
 count(rate) scenario_count,count(*) FILTER(WHERE compatible) compatible_scenarios,
 list(struct_pack(rate:=rate,upper_price:=upper_price,lower_price:=lower_price,compatible:=compatible) ORDER BY rate) scenarios,
 CASE WHEN count(*) FILTER(WHERE compatible)>0 THEN bool_or(compatible AND up_hit) END up_candidate,
 CASE WHEN count(*) FILTER(WHERE compatible)>0 THEN bool_or(compatible AND down_hit) END down_candidate,
 CASE WHEN count(*) FILTER(WHERE compatible)>0 THEN bool_or(compatible AND broken_hit) END broken_candidate,
 bool_and(compatible AND up_hit) up_all_scenarios,bool_and(compatible AND down_hit) down_all_scenarios,bool_and(compatible AND broken_hit) broken_all_scenarios,
 CASE WHEN first(exclusion) IS NOT NULL THEN first(exclusion) WHEN count(rate)=0 THEN 'outside_rule_scope'
 WHEN count(*) FILTER(WHERE compatible)=0 THEN 'ohlc_outside_all_rule_scenarios'
 WHEN count(rate)>1 THEN 'historical_st_unknown_5_or_10_scenarios' ELSE 'regular_session_rule_candidate' END candidate_reason
 FROM scenarios GROUP BY stock_code,trade_date"""
