"""Read only immutable study snapshots. Filter computations bounded by small local cohort."""
from functools import lru_cache
from pathlib import Path
import hashlib,json
from app.research.feature_study import analyze
ROOT=Path.home()/'Datasets/asharedragon/research/dragon_feature_v06'

@lru_cache(maxsize=4)
def load(path,manifest_text):
    manifest=json.loads(manifest_text);root=Path(path).resolve();snapshot=(root/manifest['dataset']).resolve()
    if not snapshot.is_relative_to(root):raise ValueError('Invalid study snapshot path')
    result={}
    for name in ('samples.json','metadata.json','statistics.json'):
        payload=(snapshot/name).read_bytes()
        if hashlib.sha256(payload).hexdigest()!=manifest['files'][name]:raise ValueError('Study snapshot checksum mismatch')
        result[name]=json.loads(payload)
    return result

@lru_cache(maxsize=16)
def filtered(snapshot,start_year,end_year,month,board,regime,path,manifest_text):
    data=load(path,manifest_text)
    return analyze(data['samples.json'],start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)

def query(start_year=2017,end_year=2026,month=None,board=None,regime=None,root=None,scope='legacy'):
    root=Path(root or ROOT);manifest=(root/'manifest.json').read_text();data=load(str(root),manifest)
    if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
    stats=data['statistics.json'] if (start_year,end_year,month,board,regime)==(2017,2026,None,None,None) else filtered(data['metadata.json']['snapshot'],start_year,end_year,month,board,regime,str(root),manifest)
    meta={k:v for k,v in data['metadata.json'].items() if k!='input_sha256'}
    if scope!='legacy':
        from app.research.st_research_scope import apply, separate_rates
        from app.research.feature_study import filter_rows
        rows=filter_rows(data['samples.json'],start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)
        rows,meta['eligibility']=apply(rows,scope=scope)
        stats=analyze(rows,start_year=start_year,end_year=end_year,month=month,board=board,regime=regime)
    if scope!='legacy':stats=separate_rates(stats,meta['eligibility'])
    meta['research_scope']=scope
    return dict(data=stats,meta=meta)
