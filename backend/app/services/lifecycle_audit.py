"""Compare dated exchange lifecycle evidence without mutating the local catalogue."""
from collections import defaultdict,Counter
from datetime import date


def parse_date(value):
    if not value or str(value) in ('NaT','nan','None'):return None
    return date.fromisoformat(str(value)[:10]).isoformat()


def normalize(key,record):
    code=str(record.get('A股代码') or record.get('证券代码') or record.get('公司代码') or '').zfill(6)
    exchange='SH' if key.startswith('sh_') else 'SZ'
    if not (code.startswith(('60','688','689')) if exchange=='SH' else code.startswith(('00','30'))):return None
    return dict(stock_code=code+'.'+exchange,name=record.get('A股简称') or record.get('证券简称') or record.get('公司简称'),
        listing_date=parse_date(record.get('A股上市日期') or record.get('上市日期')),
        delisting_date=parse_date(record.get('终止上市日期') or record.get('暂停上市日期')) if key.endswith('delisted') else None,
        roster='delisted' if key.endswith('delisted') else 'active',source=key)


def compare(local,evidence):
    by_code=defaultdict(list)
    for row in evidence:by_code[row['stock_code']].append(row)
    local_map={r['stock_code']:r for r in local};results=[]
    for code in sorted(set(local_map)|set(by_code)):
        old=local_map.get(code);observed=by_code.get(code,[])
        local_listing=parse_date(old.get('listing_date')) if old else None
        local_delisting=parse_date(old.get('delisting_date')) if old else None
        listings={r['listing_date'] for r in observed if r['listing_date']}
        delistings={r['delisting_date'] for r in observed if r['delisting_date']}
        reasons=[];status='verified'
        if not observed:status='missing_evidence';reasons.append('本次名单无匹配；不推断股票不存在或已退市')
        elif not old:status='missing_local';reasons.append('交易所名单存在，本地目录未收录')
        elif len(listings)>1 or len(delistings)>1 or len({r['roster'] for r in observed})>1:
            status='conflict';reasons.append('名单内日期或在市状态冲突，需核对 A/B 股、公司代码与重新上市')
        else:
            for name,values,existing in [('上市日期',listings,local_listing),('退市日期',delistings,local_delisting)]:
                if values and existing and existing not in values:status='conflict';reasons.append(name+'与本地不一致')
                elif values and not existing:
                    if status!='conflict':status='missing_local'
                    reasons.append('本地缺'+name)
            if not listings:
                if status=='verified':status='missing_evidence'
                reasons.append('来源未返回上市日期')
            if any(r['roster']=='delisted' for r in observed) and not delistings:
                if status=='verified':status='missing_evidence'
                reasons.append('退市名单缺退市日期')
            if any(r['roster']=='active' for r in observed) and local_delisting:
                status='conflict';reasons.append('本地有退市日期但出现在在市名单，需核查重新上市')
        results.append(dict(stock_code=code,name=(old or {}).get('stock_name') or (observed[0]['name'] if observed else None),
            local_listing=local_listing,local_delisting=local_delisting,exchange_listing=sorted(listings),exchange_delisting=sorted(delistings),
            status=status,reasons=reasons,sources=[r['source'] for r in observed]))
    return dict(rows=results,counts=dict(Counter(r['status'] for r in results)))
