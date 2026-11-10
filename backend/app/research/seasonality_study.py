"""Read-only v1 study of the unified release. No inference of new price labels."""
from collections import Counter, defaultdict
from datetime import date
from functools import lru_cache
import math
import json
import hashlib
from pathlib import Path

import numpy as np
from scipy.stats import fisher_exact, mannwhitneyu, false_discovery_control
from app.research.unified import UnifiedResearch
from app.research.queries import DEFAULT_ROOT
from app.research.feature_study import board_context, distribution, cliffs, rate_summary
from app.research.dragons import wilson
from app.research.study_v07 import cycle_outcome

VERSION = "dragon-seasonality-study-v1.1"

FEATURES = {'circulating_market_cap':'首板流通市值（元）', 'amount':'首板成交额（元）',
            'turnover_rate':'首板换手率（%）', 'first_seal_seconds':'首封时间（北京时间秒）', 'seal_amount':'封单金额（元）'}
WINDOWS = {'march_may': {3,4,5}, 'sep_dec': {9,10,11,12}, 'combined': {3,4,5,9,10,11,12}}


def records(db, sql):
    c = db.execute(sql)
    return [dict(zip([x[0] for x in c.description], r)) for r in c.fetchall()]


@lru_cache(maxsize=2)
def load_release(snapshot):
    with UnifiedResearch(dataset=str(Path(snapshot).relative_to(Path(DEFAULT_ROOT).resolve()/'research/unified'))) as release:
        db = release.db
        # Source-declared first boards plus exact-boundary cycles absent from the event sample table.
        direct = records(db, """SELECT s.stock_code, CAST(s.trade_date AS VARCHAR) first_board_date,
            s.sample_id cycle_id, coalesce(l.outcome,'unknown') outcome, coalesce(l.quality_status,'unknown') grade,
            c.observed_min_height height,c.exact_max_streak exact_height,c.complete_cycle_eligible,c.cycle_id source_cycle_id,s.is_st,s.st_quality,
            s.circulating_market_cap,s.amount,s.turnover_rate,s.first_limit_time,s.seal_amount,
            s.price_quality,s.valuation_quality,'event_first_board' start_basis
            FROM first_board_samples s LEFT JOIN sample_labels l USING(sample_id)
            LEFT JOIN sample_cycles sc USING(sample_id) LEFT JOIN cycles c USING(cycle_id)
            UNION ALL SELECT c.stock_code,c.first_board_date,c.cycle_id,l.outcome,l.quality_status,
            c.observed_min_height,c.exact_max_streak,c.complete_cycle_eligible,c.cycle_id,d.is_st,d.st_quality,
            d.circulating_market_cap,d.amount,d.turnover_rate,d.first_limit_time,d.seal_amount,
            d.price_quality,d.valuation_quality,'verified_cycle_boundary'
            FROM cycles c JOIN labels l USING(cycle_id)
            LEFT JOIN stock_daily d ON d.stock_code=c.stock_code AND d.trade_date=CAST(c.first_board_date AS DATE)
            WHERE c.first_board_date IS NOT NULL AND NOT EXISTS
            (SELECT 1 FROM sample_cycles sc WHERE sc.cycle_id=c.cycle_id)""")
        for r in direct:
            r['legacy_outcome'] = r['outcome']
            if r['source_cycle_id']:
                # Reuse the already accepted v0.7 rule on existing unified cycle facts.
                # An ended short cycle does not need unrelated later sessions.
                r['outcome'], r['outcome_reason'] = cycle_outcome(dict(
                    first_board_date=r['first_board_date'], observed_min_height=r['height'],
                    exact_max_streak=r['exact_height'], complete_cycle_eligible=r['complete_cycle_eligible']))
                r['grade'] = 'unknown' if r['outcome']=='unknown' else 'verified'
            r['restored_short_cycle'] = r['legacy_outcome']=='unknown' and r['outcome']=='failure'
        diagnostic = records(db, """SELECT c.stock_code,CAST(c.first_board_date AS VARCHAR) first_board_date,
            c.cycle_id, CASE WHEN c.research_state='diagnostic' AND c.observed_height>=5 THEN 'success'
            WHEN c.research_state='diagnostic' AND c.cycle_complete AND c.max_height<5 THEN 'failure' ELSE 'unknown' END outcome,
            c.research_state grade,c.observed_height height,c.max_height exact_height,d.is_st,d.st_quality,
            d.circulating_market_cap,d.amount,d.turnover_rate,d.first_limit_time,d.seal_amount,
            d.price_quality,d.valuation_quality,'legacy_reconstruction_boundary' start_basis
            FROM legacy_cycles c LEFT JOIN stock_daily d ON d.stock_code=c.stock_code AND d.trade_date=c.first_board_date
            WHERE c.first_board_date IS NOT NULL""")
        # Deduplicate provider copies only within the diagnostic cohort; contradictory outcomes remain unknown.
        grouped = defaultdict(list)
        for r in diagnostic: grouped[(r['stock_code'],r['first_board_date'])].append(r)
        diagnostic = []
        for group in grouped.values():
            r = dict(group[0])
            if len({x['outcome'] for x in group}) > 1:
                r.update(outcome='unknown',grade='unknown',height=None,exact_height=None)
            diagnostic.append(r)
        for cohort, rows in [('direct',direct),('reconstruction',diagnostic)]:
            for r in rows:
                day = date.fromisoformat(r['first_board_date'])
                r['year'],r['month'] = day.year,day.month
                r['board'],background = board_context(r['stock_code'],day)
                r['st'] = 'unknown' if r['st_quality']!='verified' or r['is_st'] is None else 'confirmed_st' if r['is_st'] else 'confirmed_non_st'
                # Regime is dated board background, not a claim of an ordinary-session numeric limit.
                r['regime'] = 'main_10' if r['board']=='main' else 'star_20' if r['board']=='star' else 'chinext_10' if day<date(2020,8,24) else 'chinext_20'
                r['cohort'] = cohort
                t=r.pop('first_limit_time')
                r['first_seal_seconds'] = t.hour*3600+t.minute*60+t.second if t else None
                r['height'] = r['height'] or 1
                r['security_id'] = r['stock_code']
        coverage = records(db,"""SELECT year,month,count(*) stock_days,count(close) price_days,
            count(*) FILTER(WHERE limit_quality='verified') limit_days,
            count(DISTINCT trade_date) trading_dates FROM stock_daily GROUP BY year,month ORDER BY year,month""")
        undated = {'direct':db.execute('SELECT count(*) FROM cycles WHERE first_board_date IS NULL').fetchone()[0],
                   'reconstruction':db.execute('SELECT count(*) FROM legacy_cycles WHERE first_board_date IS NULL').fetchone()[0]}
        return sorted(direct+diagnostic,key=lambda r:(r['cohort'],r['first_board_date'],r['stock_code'])), coverage, {**release.summary,'snapshot':snapshot,'undated':undated}


def summary(rows, height=5):
    n=len(rows); counts=Counter(r['outcome'] for r in rows); known=counts['success']+counts['failure']
    return dict(first_boards=n or None, success=counts['success'] if n else None,
        failure=counts['failure'] if n else None, unknown=counts['unknown'] if n else None, effective_n=known,
        rate=counts['success']/known if known else None, ci=wilson(counts['success'],known) if known else None,
        known_fraction=known/n if n else None,
        unknown_bounds=[counts['success']/n,(counts['success']+counts['unknown'])/n] if n else None,
        heights={str(k):sum(r['height']>=k for r in rows) if n else None for k in (3,5,7,10)},
        leaders=sum(r['height']>=height for r in rows) if n else None)


def contrast(rows, window, bootstrap=False):
    groups=[[r for r in rows if (r['month'] in window)==inside] for inside in (True,False)]
    a,b=[summary(g) for g in groups]
    diff=a['rate']-b['rate'] if a['rate'] is not None and b['rate'] is not None else None
    p=float(fisher_exact([[a['success'],a['failure']],[b['success'],b['failure']]],alternative='greater').pvalue) if diff is not None else None
    result=dict(target=a,other=b,difference=diff,p_iid=p,stock_cluster_ci=None)
    if bootstrap:
        known=[r for r in rows if r['outcome']!='unknown']; by=defaultdict(lambda:np.zeros(4))
        for r in known:
            offset=0 if r['month'] in window else 2
            by[r['stock_code']][offset]+=r['outcome']=='success';by[r['stock_code']][offset+1]+=1
        ar=np.array(list(by.values())); ds=[];rng=np.random.default_rng(20261108)
        # Avoid spurious zero-width intervals from sparse outcome clusters.
        if all(len({r['stock_code'] for r in g if r['outcome']==o})>=5 for g in groups for o in ('success','failure')):
            for _ in range(1000):
                s=ar[rng.integers(0,len(ar),len(ar))].sum(axis=0)
                if s[1] and s[3]: ds.append(s[0]/s[1]-s[2]/s[3])
            if ds:result['stock_cluster_ci']=list(map(float,np.quantile(ds,[.025,.975])))
    return result


def adjusted_contrast(rows,window):
    """Common-support year x regime x historical-ST fixed-stratum difference; no causal claim."""
    strata=defaultdict(list)
    for r in rows:strata[(r['year'],r['regime'],r['st'])].append(r)
    valid=[]
    for key,rr in sorted(strata.items()):
        c=contrast(rr,window)
        if c['difference'] is not None:valid.append((key,c))
    weights=[min(c['target']['effective_n'],c['other']['effective_n']) for _,c in valid]
    return dict(strata=len(valid), effective_n=sum(c['target']['effective_n']+c['other']['effective_n'] for _,c in valid),
        difference=float(np.average([c['difference'] for _,c in valid],weights=weights)) if valid else None,
        method='common year/regime/ST strata; weight=min(target_n,other_n); descriptive')


def analyze(rows, coverage, meta, *, cohort='direct', start_year=2017,end_year=2026,board=None,regime=None,st='all',grade='all',height=5,month=None,page=1,page_size=30):
    selected=[r for r in rows if r['cohort']==cohort and start_year<=r['year']<=end_year
        and (not board or r['board']==board) and (not regime or r['regime']==regime)
        and (st=='all' or r['st']==st) and (grade=='all' or r['grade']==grade)]
    months=[dict(month=m,**summary([r for r in selected if r['month']==m],height)) for m in range(1,13)]
    cells=[dict(year=y,month=m,**summary([r for r in selected if r['year']==y and r['month']==m],height)) for y in range(start_year,end_year+1) for m in range(1,13)]
    first={}
    for r in sorted(selected,key=lambda x:(x['first_board_date'],x['cycle_id'])):first.setdefault(r['stock_code'],r)
    known_first={}
    for r in sorted(selected,key=lambda x:(x['first_board_date'],x['cycle_id'])):
        if r['outcome']!='unknown':known_first.setdefault(r['stock_code'],r)
    tests=[]
    # Coverage restriction is descriptive and selected without reference to success rate.
    fractions=[x['known_fraction'] for x in cells if x['known_fraction'] is not None]
    cutoff=float(np.median(fractions)) if fractions else None
    covered={(x['year'],x['month']) for x in cells if cutoff is not None and x['known_fraction'] is not None and x['known_fraction']>=cutoff}
    for name,window in WINDOWS.items():
        yearly=[dict(year=y,**contrast([r for r in selected if r['year']==y],window)) for y in range(start_year,end_year+1)]
        diffs=[x['difference'] for x in yearly if x['difference'] is not None]
        tests.append(dict(window=name,**contrast(selected,window,True),bh_q=None,
            without_2020=contrast([r for r in selected if r['year']!=2020],window),
            one_cycle_per_stock=contrast(list(first.values()),window),
            balanced_year_difference=float(np.mean(diffs)) if diffs else None,
            coverage_restricted=contrast([r for r in selected if (r['year'],r['month']) in covered],window),
            coverage_cutoff=cutoff, adjusted=adjusted_contrast(selected,window),years=yearly,
            leave_one_year_out=[dict(year=y,**contrast([r for r in selected if r['year']!=y],window)) for y in sorted({r['year'] for r in selected})]))
    tested=[t for t in tests if t['p_iid'] is not None]
    if tested:
        for t,q in zip(tested,false_discovery_control([t['p_iid'] for t in tested])):t['bh_q']=float(q)
    features=[]
    for key,label in FEATURES.items():
        known=[r for r in selected if r['outcome']!='unknown']
        def values(rr,o):return [r[key] for r in rr if r['outcome']==o and r.get(key) is not None and math.isfinite(r[key])]
        a,b=[values(known,o) for o in ('success','failure')]
        ax,bx=[values(known_first.values(),o) for o in ('success','failure')]
        p=float(mannwhitneyu(ax,bx,alternative='two-sided').pvalue) if min(len(ax),len(bx))>=5 else None
        features.append(dict(feature=key,label=label,success=distribution(a),failure=distribution(b),
            missing=len(known)-len(a)-len(b),unknown_outcome=len(selected)-len(known),cliffs_delta=cliffs(a,b),
            p_one_stock=p,bh_q=None,one_stock_counts=[len(ax),len(bx)],
            quality='EOD retrospective; market cap/turnover diagnostic; no certified PIT'))
    tested=[t for t in features if t['p_one_stock'] is not None]
    if tested:
        for t,q in zip(tested,false_discovery_control([t['p_one_stock'] for t in tested])):t['bh_q']=float(q)
    listing=sorted([r for r in selected if r['height']>=height and (month is None or r['month']==month)],key=lambda r:(-r['height'],r['first_board_date'],r['stock_code']))
    strata=[]
    for key in sorted({(r['regime'],r['st']) for r in selected}):
        strata.append(dict(regime=key[0],st=key[1],**summary([r for r in selected if (r['regime'],r['st'])==key],height)))
    known=[{**r,'outcome':int(r['outcome']=='success')} for r in selected if r['outcome']!='unknown']
    return dict(summary=summary(selected,height),legacy_window_sensitivity=summary([r for r in selected if not r.get('restored_short_cycle')],height),restored_short_cycles=sum(r.get('restored_short_cycle',False) for r in selected),months=months,cells=cells,contrasts=tests,features=features,strata=strata,
        years=[dict(year=y,**summary([r for r in selected if r['year']==y],height)) for y in range(start_year,end_year+1)],
        rate_uncertainty=rate_summary(known),grade_counts=dict(Counter(r['grade'] for r in selected)),
        samples=listing[(page-1)*page_size:page*page_size],total=len(listing),page=page,page_size=page_size,
        coverage=[c for c in coverage if start_year<=c['year']<=end_year],
        meta=dict(study_version=VERSION,outcome_policy='v0.7 confirmed cycle outcome; archived unified labels unchanged',snapshot=meta['snapshot'],data_end=meta['data_end'],cohort=cohort,formal_eligible=False,
            undated_all_cohort=meta['undated'][cohort],undated_scope='entire snapshot, not current filters; cannot assign start month',
            grade_basis='5-board outcome evidence, not feature quality',height_filter_basis='counts and list only; rate denominator unchanged',
            coverage_scope='unfiltered stock-day context, not event population completeness',
            st_basis='historical first-board day; mixed/unknown is not non-ST',
            warning='局部证据探索性结果；缺失月份留空，unknown不计失败。制度为板块日期背景，非特殊交易日限幅证明。'))


def validate_release(snapshot):
    lock=json.loads(Path(__file__).with_name('seasonality_study_v1_lock.json').read_text())
    path=Path(snapshot)
    if (path.parent.name!='snapshots' or 'snapshots/'+path.name!=lock['unified_dataset']
        or hashlib.sha256((path/'manifest.json').read_bytes()).hexdigest()!=lock['unified_manifest_sha256']):
        raise ValueError('Frozen study input changed; explicit version review required')
    return lock


def query(**filters):
    lock=json.loads(Path(__file__).with_name('seasonality_study_v1_lock.json').read_text())
    snapshot=str(Path(DEFAULT_ROOT).resolve()/'research/unified'/lock['unified_dataset'])
    validate_release(snapshot)
    rows,coverage,meta=load_release(snapshot)
    return analyze(rows,coverage,meta,**filters)
