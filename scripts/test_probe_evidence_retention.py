import json
from probe_tushare import preserve_evidence


def test_private_archive_keeps_codes_and_all_rows_but_not_credentials(tmp_path):
    rows=[dict(ts_code='002432.SZ',trade_date='20210101',type='ST',token='secret') for _ in range(7)]
    p=preserve_evidence(tmp_path,'stock_st',{'trade_date':'20210101'},rows,'secret')
    data=json.loads(p.read_text())
    assert len(data['records'])==7 and data['records'][0]['ts_code']=='002432.SZ'
    assert data['records'][0]['token']=='[REDACTED]'
