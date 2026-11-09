from pathlib import Path
from functools import lru_cache
import json,hashlib
from app.research.feature_queries import load
from app.research.seasonality_exploratory import statistics
ROOT=Path.home()/'Datasets/asharedragon/research/dragon_seasonality_exploratory'

@lru_cache(maxsize=16)
def filtered(path,manifest,cohort,grade,start_year,end_year,board,view):
    return statistics(load(path,manifest)['samples.json'],cohort,grade,start_year,end_year,board,view)

def query(cohort='direct',grade='all',start_year=2017,end_year=2026,board=None,view='exploratory',scope='legacy'):
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    manifest=(ROOT/'manifest.json').read_text();bundle=load(str(ROOT),manifest);m=json.loads(manifest)
    payload=(ROOT/m['dataset']/'years.json').read_bytes()
    if hashlib.sha256(payload).hexdigest()!=m['files']['years.json']:raise ValueError('Annual coverage checksum mismatch')
    stats=bundle['statistics.json'][cohort] if (grade,start_year,end_year,board,view)==('all',2017,2026,None,'exploratory') else filtered(str(ROOT),manifest,cohort,grade,start_year,end_year,board,view)
    meta={k:v for k,v in bundle['metadata.json'].items() if k!='input_sha256'}
    years=[y for y in json.loads(payload) if start_year<=y['year']<=end_year]
    if scope!='legacy':
        from app.research.st_research_scope import apply, separate_rates
        rows=[r for r in bundle['samples.json'] if r['cohort']==cohort and start_year<=r['audit_year']<=end_year and (board is None or r['board']==board) and (grade=='all' or r['evidence_grade']==grade)]
        rows,meta['eligibility']=apply(rows,cohort,scope=scope)
        stats=statistics(rows,cohort,grade,start_year,end_year,board,view)
        # Annual fact coverage is unchanged by the ST attribute filter.
    if scope!='legacy':stats=separate_rates(stats,meta['eligibility'])
    meta['research_scope']=scope
    return dict(data=stats,years=years,meta=meta)
