"""Independent IPO origin and OHLC morphology; never rewrites streak facts."""
VERSION='streak-classification-v1'

def classify_origin(listing_index,start_index,registered,prior_negative,day_one_confirmed=False):
 if listing_index is None or start_index is None or start_index<listing_index:return 'unknown','listing_or_calendar_unknown'
 age=start_index-listing_index+1
 if registered and age<=5:return 'unknown','no_limit_ipo_period'
 if age==(6 if registered else 2):return 'ipo_initial','first_regular_limit_session'
 if not registered and age==1 and day_one_confirmed:return 'ipo_initial','confirmed_special_first_day'
 if prior_negative:return 'non_ipo','observed_negative_separates_ipo'
 return 'unknown','ipo_prefix_not_resolved'

def morphology(total,valid,one_price,ranged,positive_volume):
 shape='unknown' if valid!=total else 'one_price_all' if one_price==total else 'traded_range_all' if ranged==total else 'mixed'
 return shape,shape=='traded_range_all' and positive_volume==total
