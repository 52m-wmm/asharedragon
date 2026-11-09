"""Research eligibility only. Historical market facts are never rewritten.

An event is evidence of a close, not evidence of non-ST. A certified ordinary
10% regime can imply non-ST only where the historical ST rate differed.
"""
from collections import Counter
from datetime import date
from functools import lru_cache
from pathlib import Path
import hashlib
import json
import re

VERSION='non-st-research-v1'
ROOT=Path.home()/'Datasets/asharedragon/research/dragon_non_st_v1'


def name_exclusion(name):
    # Only a dated historical attribute is passed here; never a join key.
    name=re.sub(r'\s+','',name or '').upper()
    name=re.sub(r'^(?:(?:XR|XD|DR))+','',name)
    if '退' in name:return 'delisting_period_confirmed'
    if name.startswith(('ST','*ST','SST','S*ST')):return 'risk_warning_confirmed'
    return None  # Absence of a marker does NOT establish non-ST.


def day_eligibility(code,day,observations,*,identity=None,normal_regime=None):
    ds=str(day);trusted=[r for r in observations if r.get('stock_code')==code and r.get('verification_status')=='source_observed'
        and r.get('security_id')==identity and identity is not None and str(r['valid_from'])<=ds<=str(r['valid_to'])]
    ids=sorted({r['evidence_id'] for r in trusted if r.get('evidence_id')})
    exclusions={name_exclusion(r.get('historical_name')) for r in trusted}-{None}
    risks={r.get('risk_warning_status') for r in trusted}-{None,'unknown'}
    if any(x!='none' for x in risks):exclusions.add('risk_warning_confirmed')
    proof=normal_regime or {}
    scoped=(proof.get('verified') is True and proof.get('stock_code')==code and proof.get('trade_date')==ds
            and bool(proof.get('evidence_ids')) and proof.get('normal_session') is True)
    # 20% growth-board and post-2026 reform main-board rates cannot distinguish ST.
    rate_implies_non_st=(scoped and proof.get('rate')==.10 and code.startswith(('00','60')) and ds<'2026-07-06')
    non_st=('none' in risks) or (scoped and proof.get('non_st_confirmed') is True) or rate_implies_non_st
    if scoped:ids=sorted(set(ids)|set(proof['evidence_ids']))
    if exclusions:
        return dict(status='excluded',reasons=sorted(exclusions),evidence_ids=ids,non_st_confirmed=False,
                    conflict=non_st,ordinary_session_confirmed=False)
    reasons=[]
    if identity is None:reasons.append('security_identity_unknown')
    if not non_st:reasons.append('historical_non_st_unproven')
    # tradestatus=1 proves trading, not absence of a delisting arrangement.
    ordinary=scoped and proof.get('not_delisting') is not False
    if not ordinary:reasons.append('non_delisting_period_unproven')
    return dict(status='unknown' if reasons else 'eligible',reasons=reasons,evidence_ids=ids,
                non_st_confirmed=non_st,ordinary_session_confirmed=ordinary,conflict=False)


def aggregate(days):
    if not days:return dict(status='unknown',reasons=['missing_eligibility_dates'],dates=[])
    status='excluded' if any(d['status']=='excluded' for d in days) else 'eligible' if all(d['status']=='eligible' for d in days) else 'unknown'
    return dict(status=status,reasons=sorted({r for d in days if d['status']!='eligible' for r in d['reasons']}),
                dates=[d for d in days if d['status']!='eligible'])


@lru_cache(maxsize=2)
def load(path,manifest):
    root=Path(path).resolve();m=json.loads(manifest);p=(root/m['dataset']).resolve()
    if not p.is_relative_to(root):raise ValueError('Invalid eligibility path')
    out={}
    for name in ('summary.json','samples.json','days.json','audit_693.json'):
        data=(p/name).read_bytes()
        if hashlib.sha256(data).hexdigest()!=m['files'][name]:raise ValueError('Eligibility checksum mismatch')
        out[name]=json.loads(data)
    out['by_sample']={(r['cohort'],r['cycle_id']):r for r in out['samples.json']}
    out['by_day']={(r['stock_code'],r['trade_date']):r for r in out['days.json']}
    return out


def bundle():return load(str(ROOT),(ROOT/'manifest.json').read_text())


def qualification(row,cohort='direct',*,whole_cycle=False,data=None):
    data=data or bundle()
    match=data['by_sample'].get((cohort,row.get('cycle_id',row.get('fact_id'))))
    if match:return match['cycle_eligibility' if whole_cycle else 'eligibility']
    progress=row.get('progress',[])
    dates=[str(r['trade_date']) for r in progress]
    if not dates:return dict(status='unknown',reasons=['eligibility_not_in_snapshot'],dates=[])
    return aggregate([data['by_day'].get((row['stock_code'],d),dict(status='unknown',reasons=['eligibility_not_in_snapshot'],trade_date=d)) for d in dates])


def apply(rows,cohort='direct',*,whole_cycle=False,keep_unknown=True,data=None):
    data=data or bundle();output=[];counts=Counter();reasons=Counter()
    for original in rows:
        q=qualification(original,cohort,whole_cycle=whole_cycle,data=data);counts[q['status']]+=1;reasons.update(q['reasons'])
        if q['status']=='excluded' or (q['status']=='unknown' and not keep_unknown):continue
        r={**original,'research_eligibility':q,'research_scope':VERSION}
        if q['status']=='unknown':
            r['historical_outcome']=r.get('outcome',r.get('category'))
            if 'category' in r:r['category']='unknown'
            if 'outcome' in r:r['outcome']='unknown' if isinstance(r['outcome'],str) else None
            if 'evidence_grade' in r:r['evidence_grade']='unknown'
            if 'observed_height' in r:r['observed_height']=0
            if 'exact_height' in r:r['exact_height']=None
        output.append(r)
    return output,dict(version=VERSION,total=len(rows),eligible=counts['eligible'],excluded=counts['excluded'],
        unknown=counts['unknown'],reasons=dict(reasons),unknown_is_failure=False,
        policy='ST/*ST and delisting dates excluded; direct event is not non-ST evidence; original facts preserved')
