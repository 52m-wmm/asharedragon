from datetime import date
from typing import Literal
from fastapi import APIRouter,Depends,Query,HTTPException
from app.research.dragon_queries import Dragons
from app.providers.normalize import stock_code

from app.statistics.stock_scope import scope_context
router=APIRouter(prefix='/api/research/dragons',tags=['dragon research v0.5'],dependencies=[Depends(scope_context)])

def dataset(scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    try:
        d=Dragons()
        if scope!='legacy':
            from app.research.st_research_scope import apply
            d.rows,eligibility=apply(d.rows,scope=scope,whole_cycle=True)
            d.meta.update(research_scope=scope,eligibility=eligibility)
        else:d.meta['research_scope']='legacy'
        return d
    except FileNotFoundError as exc:raise HTTPException(503,'龙头研究快照未构建；请运行离线研究脚本。') from exc

def filters(mode:Literal['formal','diagnostic']='formal',start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),
            month:int|None=Query(None,ge=1,le=12),min_cap:float|None=Query(None,ge=0),max_cap:float|None=Query(None,ge=0),
            provider:Literal['akshare','tushare_proxy']|None=None,search:str=Query('',max_length=80),board_type:Literal['one_price','traded_range']|None=None,industry:str|None=None,theme:str|None=None):
    return dict(provider=provider,mode=mode,start_year=start_year,end_year=end_year,month=month,min_cap=min_cap,max_cap=max_cap,search=search,board_type=board_type,industry=industry,theme=theme)

@router.get('')
def listing(f:dict=Depends(filters),d:Dragons=Depends(dataset),min_height:int=Query(5,ge=1,le=10),include_unknown:bool=False,
            sort:Literal['max_height','first_board_date','circulating_market_cap','stock_code']='max_height',descending:bool=True,
            page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
    return d.listing(**f,min_height=min_height,include_unknown=include_unknown,sort=sort,descending=descending,page=page,page_size=page_size)

@router.get('/statistics')
def statistics(f:dict=Depends(filters),d:Dragons=Depends(dataset),min_height:int=Query(5,ge=1,le=10)):
    result=d.statistics(**f,min_height=min_height)
    if d.meta.get('eligibility'):
        from app.research.st_research_scope import separate_rates
        result['data']=separate_rates(result['data'],d.meta['eligibility'])
    return result

@router.get('/stock/{code}')
def stock(code:str,mode:Literal['formal','diagnostic']='formal',d:Dragons=Depends(dataset)):return d.stock(stock_code(code),mode)


@router.get('/diagnostics')
def diagnostics():
    from pathlib import Path
    import json
    path=Path.home()/'Datasets/asharedragon/research/dragon_diagnostics_v051/audit.json'
    if not path.exists():raise HTTPException(503,'请先运行本地龙头标签诊断审计。')
    return {'data':json.loads(path.read_text()),'meta':{'research_state':'diagnostic','formal_eligible':False,'price_scenarios_are_not_labels':True}}


def expanded_dataset():
    from app.research.dragon_coverage import DragonCoverage
    try:return DragonCoverage()
    except FileNotFoundError as exc:raise HTTPException(503,'v0.5.2 全区间研究审计尚未完成。') from exc


@router.get('/coverage')
def expanded_coverage(d=Depends(expanded_dataset)):
    return d.coverage()


@router.get('/expanded')
def expanded_listing(mode:Literal['formal','diagnostic','unknown']='formal',start_year:int=Query(2017,ge=2017,le=2026),
                     end_year:int=Query(2026,ge=2017,le=2026),min_height:int=Query(1,ge=1,le=10),
                     page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100),d=Depends(expanded_dataset),scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    return d.listing(mode,start_year,end_year,min_height,page,page_size,scope=scope)


@router.get('/expanded/stock/{code}')
def expanded_stock_days(code:str,start_date:date,end_date:date,d=Depends(expanded_dataset)):
    return d.stock_days(stock_code(code),start_date,end_date)


@router.get('/st-evidence')
def st_evidence_summary():
    from pathlib import Path
    import json
    root=Path.home()/'Datasets/asharedragon/research/dragon_v053'
    try:return {'data':json.loads((root/'summary.json').read_text()),'meta':{'counterfactual_is_not_production':True,'formal_labels_unchanged':True}}
    except FileNotFoundError as exc:raise HTTPException(503,'历史状态证据审计尚未完成。') from exc


@router.get('/historical-status/{code}')
def historical_status(code:str,trade_date:date):
    from pathlib import Path
    import pyarrow.parquet as pq
    from app.research.stock_status import status_at
    root=Path.home()/'Datasets/asharedragon/research/dragon_v053';canonical=stock_code(code)
    try:
        rows=pq.read_table(root/'historical_stock_status.parquet',filters=[('stock_code','=',canonical)]).to_pylist()
        aliases=pq.read_table(root/'security_aliases.parquet',filters=[('stock_code','=',canonical)]).to_pylist()
    except FileNotFoundError as exc:raise HTTPException(503,'历史状态证据时间轴尚未构建。') from exc
    active=[r for r in rows if r['valid_from']<=trade_date<=r['valid_to']]
    return {'data':status_at(canonical,trade_date,active,aliases),'evidence':active,
            'meta':{'name_is_not_join_key':True,'unknown_is_not_non_st':True,'interval_end_inclusive':True}}


@router.get('/trading-evidence')
def trading_evidence_audit(code:str|None=None):
    """Bounded v0.5.4 audit; never substitutes for formal whole-market labels."""
    from pathlib import Path
    import json
    root=Path.home()/'Datasets/asharedragon/research/dragon_v054'
    try:
        manifest=json.loads((root/'manifest.json').read_text())
        snapshot=root/manifest['dataset']
        summary=json.loads((snapshot/'summary.json').read_text())
        cases=json.loads((snapshot/'cases.json').read_text())
    except FileNotFoundError as exc:raise HTTPException(503,'交易证据分层审计尚未构建。') from exc
    if code is not None:
        try:canonical=stock_code(code)
        except ValueError as exc:raise HTTPException(422,'无效股票代码。') from exc
        cases=[c for c in cases if c['stock_code']==canonical]
    return {'summary':summary,'cases':cases,'meta':{'scope':'five_cases_and_693_audit',
        'formal_whole_market_eligible':False,'thresholds_are_lower_bounds':True,
        'retrospective_evidence_not_prediction':True,'version':manifest['version']}}


@router.get('/observed-streaks')
def observed_streaks(code:str|None=None,min_height:int=Query(3,ge=3,le=100),start_year:int=Query(2017,ge=2017,le=2026),
                     end_year:int=Query(2026,ge=2017,le=2026),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),
                     sort:Literal['observed_min_height','stock_code','first_observed_date']='observed_min_height',descending:bool=True,scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    from pathlib import Path
    import json
    root=Path.home()/'Datasets/asharedragon/research/dragon_facts'
    expanded=root.parent/'dragon_v056'
    if (expanded/'manifest.json').exists():root=expanded
    if start_year>end_year:raise HTTPException(422,'开始年份不能晚于结束年份。')
    canonical=stock_code(code) if code else None
    try:
        manifest=json.loads((root/'manifest.json').read_text());snapshot=(root/manifest['dataset']).resolve()
        if not snapshot.is_relative_to(root.resolve()):raise ValueError('Snapshot escaped research root')
        rows=json.loads((snapshot/'facts.json').read_text());summary=json.loads((snapshot/'summary.json').read_text())
    except FileNotFoundError as exc:raise HTTPException(503,'独立连板事实尚未构建。') from exc
    rows=[r for r in rows if r['observed_min_height']>=min_height and (not canonical or r['stock_code']==canonical)
          and start_year<=int(r['first_observed_date'][:4])<=end_year]
    eligibility=None
    if scope!='legacy':
        from app.research.st_research_scope import apply
        rows,eligibility=apply(rows,scope=scope,whole_cycle=True)
    rows.sort(key=lambda r:(r[sort],r['fact_id']),reverse=descending)
    cases=[c for c in summary.get('case_audits',[]) if not canonical or c['stock_code']==canonical]
    return {'data':rows[(page-1)*page_size:page*page_size],
        'meta':{'total':len(rows),'page':page,'page_size':page_size,'label_version':manifest['version'],
            'formal_statistics_eligible':False,'scope':summary.get('scope'),'research_scope':scope,'eligibility':eligibility,
            'date_filter_basis':'first_observed_date_not_first_board_date','case_audits':cases,
            'threshold_counts':summary.get('threshold_counts') if scope=='legacy' else {str(n):sum(r['observed_min_height']>=n for r in rows) for n in (3,5,7,10)},
            'threshold_fact_count':summary.get('threshold_fact_count') if scope=='legacy' else sum(sum(r['observed_min_height']>=n for n in (3,5,7,10)) for r in rows),
            'exact_cycles':summary.get('exact_cycles') if scope=='legacy' else sum(r['exact_max_streak'] is not None for r in rows),
            'lower_bound_only':summary.get('lower_bound_only') if scope=='legacy' else sum(r['exact_max_streak'] is None for r in rows),
            'retrospective_evidence_not_prediction':True,'built_at_utc':summary.get('built_at_utc')}}


def coverage_v056_snapshot():
    from pathlib import Path
    import json
    root=Path.home()/'Datasets/asharedragon/research/dragon_v056'
    try:
        m=json.loads((root/'manifest.json').read_text());p=(root/m['dataset']).resolve()
        if not p.is_relative_to(root.resolve()):raise ValueError('Snapshot escaped research root')
        return p,m
    except FileNotFoundError as exc:raise HTTPException(503,'v0.5.6 覆盖审计尚未构建。') from exc


@router.get('/coverage-v056')
def coverage_v056():
    import json
    p,m=coverage_v056_snapshot()
    return {'data':{k:json.loads((p/(k+'.json')).read_text()) for k in ('summary','years','monthly','recovery_47')},
        'meta':{'label_version':m['version'],'formal_year_statistics_eligible':False,
            'coverage_denominator':'classified stock-days / scanned unadjusted rows; not whole-market completeness',
            'unknown_is_failure':False}}


@router.get('/first-board-samples')
def first_board_samples(category:Literal['all','confirmed_success_ge5','confirmed_failure_below5','outcome_unknown','first_board_unknown']='all',
                        code:str|None=None,start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),
                        page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    import json
    from collections import Counter
    if start_year>end_year:raise HTTPException(422,'开始年份不能晚于结束年份。')
    p,m=coverage_v056_snapshot();rows=json.loads((p/'samples.json').read_text());canonical=stock_code(code) if code else None
    rows=[r for r in rows if (not canonical or r['stock_code']==canonical) and start_year<=int((r['first_board_date'] or r['first_observed_date'])[:4])<=end_year]
    eligibility=None
    if scope!='legacy':
        from app.research.st_research_scope import apply
        rows,eligibility=apply(rows,scope=scope)
    counts=dict(Counter(r['category'] for r in rows))
    rows=[r for r in rows if category=='all' or r['category']==category]
    rows.sort(key=lambda r:(r['first_board_date'] or r['first_observed_date'],r['stock_code']),reverse=True)
    return {'data':rows[(page-1)*page_size:page*page_size],'meta':{'total':len(rows),'page':page,'page_size':page_size,
        'category_counts':counts,'label_version':m['version'],'formal_year_statistics_eligible':False,
        'failure_window_sessions':5,'unknown_is_failure':False,'market_environment':'unknown','research_scope':scope,'eligibility':eligibility}}


@router.get('/feature-study')
def feature_study(start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),
                  month:int|None=Query(None,ge=1,le=12),board:Literal['main','chinext','star']|None=None,
                  regime:Literal['main_historical_10_or_5_special_unknown','chinext_pre_reform_10_or_5_special_unknown','chinext_post_reform_20_or_special_unknown','star_20_or_special_unknown']|None=None,scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    from app.research.feature_queries import query
    try:return query(start_year,end_year,month,board,regime,scope=scope)
    except FileNotFoundError as exc:raise HTTPException(503,'首板特征研究快照尚未生成。') from exc
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/study-v07')
def study_v07(mode:Literal['strict','exploratory']='exploratory',start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),
              month:int|None=Query(None,ge=1,le=12),board:Literal['main','chinext','star']|None=None,
              regime:Literal['main_historical_10_or_5_special_unknown','chinext_pre_reform_10_or_5_special_unknown','chinext_post_reform_20_or_special_unknown','star_20_or_special_unknown']|None=None,scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    from app.research.study_v07_queries import query
    try:return query(mode,start_year,end_year,month,board,regime,scope=scope)
    except FileNotFoundError as exc:raise HTTPException(503,'v0.7年度研究快照尚未生成。') from exc
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/study-v07/samples')
def study_v07_samples(category:Literal['all','success','failure','unknown']='all',start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),page:int=Query(1,ge=1),page_size:int=Query(20,ge=1,le=100),scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    from app.research.study_v07_queries import samples
    try:return samples(category,start_year,end_year,page,page_size,scope=scope)
    except FileNotFoundError as exc:raise HTTPException(503,'v0.7年度研究快照尚未生成。') from exc
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/seasonality-exploratory')
def seasonal_exploration(cohort:Literal['direct','reconstruction']='direct',grade:Literal['all','verified','diagnostic','unknown']='all',
                         start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),board:Literal['main','chinext','star']|None=None,
                         view:Literal['formal','exploratory']='exploratory',scope:Literal['all','confirmed_non_st','confirmed_st','unknown','non_st','legacy']='all'):
    from app.research.seasonality_queries import query
    try:return query(cohort,grade,start_year,end_year,board,view,scope=scope)
    except FileNotFoundError as exc:raise HTTPException(503,'探索性季节性快照尚未构建。') from exc
    except ValueError as exc:raise HTTPException(422,str(exc)) from exc


@router.get('/non-st-scope')
def non_st_summary(start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026)):
    from app.research.non_st_scope import bundle
    if start_year>end_year:raise HTTPException(422,'开始年份不能晚于结束年份')
    try:data=bundle()
    except FileNotFoundError as exc:raise HTTPException(503,'非ST研究资格快照尚未生成') from exc
    return {'data':{**data['summary.json'],'groups':[r for r in data['summary.json']['groups'] if start_year<=r['year']<=end_year]},
            'meta':{'archived_eligibility_audit':True,'research_scope':'non_st','historical_facts_unchanged':True,'unknown_is_failure':False}}


@router.get('/st-research-scope')
def st_research_summary(scope:Literal['all','confirmed_non_st','confirmed_st','unknown']='all',cohort:Literal['direct','reconstruction']='direct'):
    from pathlib import Path
    import json
    root=Path.home()/'Datasets/asharedragon/research/historical_st_view_v1'
    try:
        m=json.loads((root/'manifest.json').read_text())
        report=json.loads((root/m['dataset']/'summary.json').read_text())
    except FileNotFoundError as exc:raise HTTPException(503,'历史ST视图尚未构建') from exc
    from app.statistics.stock_scope import CHOICE
    if any(CHOICE.get()):
        from collections import Counter
        from app.research.st_research_scope import apply
        parent=root.parent/'dragon_seasonality_exploratory'/report['source_snapshot']
        sample=[r for r in json.loads((parent/'samples.json').read_text()) if r['cohort']==cohort]
        selected,eligibility=apply(sample,cohort,scope=scope)
        report['cohorts'][cohort][scope]=dict(outcomes=dict(Counter(r['outcome'] for r in selected)),**eligibility)
    return {'data':report['cohorts'][cohort][scope], 'meta':{'version':report['version'],'source_snapshot':report['source_snapshot'],
        'audit_693':report['audit_693'],'historical_facts_unchanged':True}}


@router.get('/seasonality-study-v1')
def seasonality_study_v1(cohort:Literal['direct','reconstruction']='direct',
    start_year:int=Query(2017,ge=2017,le=2026),end_year:int=Query(2026,ge=2017,le=2026),
    board:Literal['main','chinext','star']|None=None,
    regime:Literal['main_10','chinext_10','chinext_20','star_20']|None=None,
    st:Literal['all','confirmed_non_st','confirmed_st','unknown']='all',
    grade:Literal['all','verified','diagnostic','unknown']='all',height:int=Query(5,ge=1,le=10),
    month:int|None=Query(None,ge=1,le=12),page:int=Query(1,ge=1),page_size:int=Query(30,ge=1,le=100)):
    from app.research.seasonality_study import query
    if start_year>end_year:raise HTTPException(422,'开始年份不能晚于结束年份')
    try:return query(cohort=cohort,start_year=start_year,end_year=end_year,board=board,regime=regime,st=st,grade=grade,height=height,month=month,page=page,page_size=page_size)
    except FileNotFoundError as exc:raise HTTPException(503,'统一研究数据集未安装') from exc
