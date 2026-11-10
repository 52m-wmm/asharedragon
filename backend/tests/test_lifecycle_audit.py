from app.services.lifecycle_audit import normalize,compare


def test_normalizes_exchange_dates_and_filters_b_shares():
 assert normalize('sz_delisted',{'证券代码':'200001'}) is None
 row=normalize('sh_delisted',{'公司代码':'600001','上市日期':'1998-01-22T00:00:00.000','暂停上市日期':'2009-12-29T00:00:00.000'})
 assert row['delisting_date']=='2009-12-29'


def test_conflicts_and_missing_evidence_never_overwrite():
 local=[{'stock_code':'600001.SH','listing_date':'1998-01-22','delisting_date':'2009-12-28'}, {'stock_code':'600002.SH','listing_date':'2000-01-01'}]
 ev=[normalize('sh_delisted',{'公司代码':'600001','上市日期':'1998-01-22','暂停上市日期':'2009-12-29'})]
 result=compare(local,ev)
 assert result['counts']=={'conflict':1,'missing_evidence':1}
 assert local[0]['delisting_date']=='2009-12-28'


def test_company_code_multiple_listing_dates_stays_conflict():
 local=[{'stock_code':'600001.SH','listing_date':'1998-01-22'}]
 ev=[normalize('sh_delisted',{'公司代码':'600001','上市日期':d,'暂停上市日期':'2009-12-29'}) for d in ('1998-01-22','1997-01-22')]
 assert compare(local,ev)['rows'][0]['status']=='conflict'
