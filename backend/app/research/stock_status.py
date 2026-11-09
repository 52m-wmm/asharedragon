"""Dated evidence and security identity. Names are attributes, NEVER join keys."""
from datetime import date
from uuid import uuid5,NAMESPACE_URL
from app.providers.normalize import stock_code,day


def security_id(code,listed):
    if listed is None:return None
    return str(uuid5(NAMESPACE_URL,f'asharedragon:listing:{stock_code(code)}:{listed}'))


def resolve_security(code,when,aliases):
    code=stock_code(code)
    rows=[r for r in aliases if r['stock_code']==code and r['valid_from']<=when and (r['valid_to'] is None or when<=r['valid_to'])]
    ids={r['security_id'] for r in rows if r['security_id']}
    return next(iter(ids)) if len(ids)==1 else None


def risk_from_name(name, *, complete_interval=False):
    text=(name or '').upper().replace(' ','')
    # Keep historical special markers explicit; none implies a universal rate.
    if text.startswith(('S*ST','SST')):return 'legacy_st'
    if text.startswith('*ST'):return 'star_st'
    if text.startswith('ST'):return 'st'
    if text.startswith(('S','PT','N','C','XR','XD','DR','退')) or '退' in text:return 'unknown'
    return 'none' if text and complete_interval else 'unknown'


def name_intervals(records, *, pagination_complete=False, asof=None, source='tushare_namechange', fetched_at=None):
    """Caller must prove pagination separately. Input filters on announcement dates
    cannot prove effective-date history. Gaps and overlaps NEVER become non-ST.
    """
    rows=[]
    for record in records:
        start=day(record['start_date']);end=day(record['end_date']) if record.get('end_date') else asof
        if end is None or end<start:raise ValueError('Unbounded or invalid effective interval')
        rows.append(dict(stock_code=stock_code(record['ts_code']),valid_from=start,valid_to=end,historical_name=record['name'],
            ann_date=day(record['ann_date']) if record.get('ann_date') else None,risk_warning_status=risk_from_name(record['name'],complete_interval=pagination_complete),
            trading_status='unknown',evidence_source=source,evidence_quality='complete_source_interval' if pagination_complete else 'partial_history',
            fetched_at=fetched_at,verification_status='source_observed' if pagination_complete else 'diagnostic',evidence_id=record.get('evidence_id')))
    for r in rows:
        overlap=any(o is not r and o['stock_code']==r['stock_code'] and max(o['valid_from'],r['valid_from'])<=min(o['valid_to'],r['valid_to']) for o in rows)
        if overlap:r.update(risk_warning_status='unknown',verification_status='conflict',evidence_quality='overlapping_intervals')
    return rows


def status_at(code,when,rows,aliases):
    code=stock_code(code);sid=resolve_security(code,when,aliases)
    lifetimes=[r for r in aliases if r['stock_code']==code]
    outside=None
    if lifetimes and when<min(r['valid_from'] for r in lifetimes):outside='not_listed'
    elif lifetimes and all(r['valid_to'] is not None for r in lifetimes) and when>max(r['valid_to'] for r in lifetimes):outside='delisted'
    eligible=[r for r in rows if r['stock_code']==code and r['valid_from']<=when<=r['valid_to'] and r.get('security_id')==sid] if sid else []
    risk={r['risk_warning_status'] for r in eligible if r['risk_warning_status']!='unknown'}
    # ST subtype distinctions are retained; they are not source conflicts.
    directions={v=='none' for v in risk};trading={r['trading_status'] for r in eligible if r['trading_status']!='unknown'}
    conflict=len(directions)>1 or len(trading)>1 or any(r['verification_status']=='conflict' for r in eligible)
    return dict(stock_code=code,security_id=sid,trade_date=when,
        risk_warning_status='unknown' if conflict or not risk else next(iter(risk)) if len(risk)==1 else 'risk_warning',
        is_st=None if conflict or not risk else not next(iter(directions)),
        trading_status=outside or ('unknown' if conflict or len(trading)!=1 else next(iter(trading))),
        verification_status='catalogue_lifecycle' if outside else 'identity_ambiguous' if sid is None else 'conflict' if conflict else 'source_observed' if eligible else 'unknown',
        evidence_ids=sorted({r['evidence_id'] for r in eligible if r.get('evidence_id')}))


def pagination_complete(pages, *, declared_total=None, documented_cap=None):
    """Fail closed if the endpoint's pagination contract is unproven.
    Pages are (offset, records); repeated pages, gaps and capped terminal pages
    without an independently reported total do not establish completeness.
    """
    import json
    expected=0;fingerprints=set()
    for offset,records in pages:
        if offset!=expected:return False
        fingerprint=json.dumps(records,sort_keys=True,default=str)
        if records and fingerprint in fingerprints:return False
        fingerprints.add(fingerprint);expected+=len(records)
    if not pages:return False
    if declared_total is not None:return expected==declared_total
    return documented_cap is not None and len(pages[-1][1])<documented_cap
