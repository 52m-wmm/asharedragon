from functools import lru_cache
from pathlib import Path
from app.research.feature_queries import load
from app.research.study_v07 import study_statistics
ROOT=Path.home()/'Datasets/asharedragon/research/dragon_v07'

@lru_cache(maxsize=12)
def filtered(path,manifest,mode,start_year,end_year,month,board,regime):
    data=load(path,manifest)
    return study_statistics(data['samples.json'],mode,start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)

def query(mode='exploratory',start_year=2017,end_year=2026,month=None,board=None,regime=None,scope='legacy'):
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    manifest=(ROOT/'manifest.json').read_text();data=load(str(ROOT),manifest)
    stats=data['statistics.json'][mode] if (start_year,end_year,month,board,regime)==(2017,2026,None,None,None) else filtered(str(ROOT),manifest,mode,start_year,end_year,month,board,regime)
    import json,hashlib
    m=json.loads(manifest);payload=(ROOT/m['dataset']/'years.json').read_bytes()
    if hashlib.sha256(payload).hexdigest()!=m['files']['years.json']:raise ValueError('Annual audit checksum mismatch')
    meta={k:v for k,v in data['metadata.json'].items() if k not in ('input_sha256','raw_hashes')};years=json.loads(payload)
    if scope!='legacy':
        from app.research.st_research_scope import apply, separate_rates
        from app.research.feature_study import filter_rows
        selected=filter_rows(data['samples.json'],start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)
        rows,meta['eligibility']=apply(selected,scope=scope)
        stats=study_statistics(rows,mode,start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)
        for y in years:
            group=[r for r in rows if r['year']==y['year']]
            y.update(success=sum(r['category']=='success' for r in group),failure=sum(r['category']=='failure' for r in group),
                unknown=len([r for r in group if r['category']=='unknown']),unknown_candidates=sum(r['category']=='unknown' for r in group),
                complete_cycles=sum(r['category']!='unknown' and r.get('complete_cycle') for r in group),
                confirmed_first_boards=sum(r['category']!='unknown' for r in group),local_candidates=len(group),
                local_resolved_fraction=sum(r['category']!='unknown' for r in group)/len(group) if group else None,
                unknown_candidate_fraction=sum(r['category']=='unknown' for r in group)/len(group) if group else None,
                restored_failure=0,restored_success=0,primary_blocker='see_original_fact_audit_ST_is_separate_attribute',
                historical_coverage_only=True)
    if scope!='legacy':stats=separate_rates(stats,meta['eligibility'])
    meta['research_scope']=scope
    return dict(data=stats,years=years,meta=meta)

def samples(category='all',start_year=2017,end_year=2026,page=1,page_size=20,scope='legacy'):
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    data=load(str(ROOT),(ROOT/'manifest.json').read_text());rows=[r for r in data['samples.json'] if start_year<=r['year']<=end_year]
    eligibility=None
    if scope!='legacy':
        from app.research.st_research_scope import apply, separate_rates
        rows,eligibility=apply(rows,scope=scope)
    rows=[r for r in rows if category=='all' or r['category']==category]
    rows.sort(key=lambda r:(r['first_board_date'] or r['first_observed_date'],r['stock_code']))
    return dict(data=rows[(page-1)*page_size:page*page_size],meta=dict(total=len(rows),snapshot=data['metadata.json']['snapshot'],version=data['metadata.json']['version'],research_scope=scope,eligibility=eligibility))
