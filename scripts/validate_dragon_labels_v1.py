"""Finite read-only validation. Never publishes labels or changes research manifests.

Run with PYTHONPATH=backend:scripts .venv/bin/python scripts/validate_dragon_labels_v1.py.
The 15 predicted-negative controls are frozen before inspecting reference truth.
Only selected price windows are loaded; old raw archives are not re-scanned.
"""
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
import csv
import hashlib
import json
import re
import subprocess

import pyarrow as pa
import pyarrow.parquet as pq

from app.research.queries import Research
from app.research.stock_status import status_at
from app.research.limit_rules import reconstruct, score
from app.research.trading_evidence import trading_evidence, limit_evidence, streak_fields
from app.research.dragons import cycles
from app.providers.normalize import stock_code

BASE = Path.home() / 'Datasets/asharedragon/research'
OUT = BASE / 'dragon_label_validation_v1'
P52 = BASE / 'dragon_v052/snapshots/20261107T142031408652Z'
P54 = BASE / 'dragon_v054/snapshots/20261108T020719951202Z'
P56 = BASE / 'dragon_v056/snapshots/20261108T044422935371Z'
PS = BASE / 'dragon_seasonality_exploratory/snapshots/c3355c56960ddd49fab8'


def read(path):
    return json.loads(path.read_text())


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save(path, obj):
    path.write_text(json.dumps(obj, ensure_ascii=False, default=str, indent=2, allow_nan=False))


def confusion(rows):
    """Unknown truth/prediction cannot silently enter TN or FN denominators."""
    known = [r for r in rows if r['truth'] is not None and r['prediction'] is not None]
    tp = sum(r['truth'] and r['prediction'] for r in known)
    fp = sum(not r['truth'] and r['prediction'] for r in known)
    fn = sum(r['truth'] and not r['prediction'] for r in known)
    tn = sum(not r['truth'] and not r['prediction'] for r in known)
    return dict(n=len(rows), truth_unknown=sum(r['truth'] is None for r in rows),
                prediction_unknown=sum(r['prediction'] is None for r in rows),
                known_positive_abstentions=sum(r['truth'] is True and r['prediction'] is None for r in rows),
                tp=tp, fp=fp, fn=fn, tn=tn,
                precision=tp/(tp+fp) if tp+fp else None,
                conditional_recall=tp/(tp+fn) if tp+fn else None,
                positive_capture=tp/sum(r['truth'] is True for r in rows) if any(r['truth'] is True for r in rows) else None)


def sample_year(window):
    # A warm-up boundary on Dec 31 must not move a Jan first board into last year.
    return window['selected']['year'] if 'selected' in window else window['start'].year


def build():
    old_manifests = {str(p): sha(p) for p in BASE.glob('*/manifest.json')}
    parent_manifest = read(BASE/'dragon_v054/manifest.json')
    assert sha(P54/'announcement_evidence.json') == parent_manifest['files']['announcement_evidence.json']
    samples = read(PS/'samples.json')
    old_cycles = {r['cycle_id']: r for r in read(P52/'cycles.json')}
    selection = read(OUT/'selection.json')
    windows = []
    grouped = defaultdict(list)
    for r in pq.read_table(BASE/'dragon_v053/case_missing_fields.parquet').to_pylist():
        grouped[r['stock_code']].append(r['trade_date'])
    for code, dates in sorted(grouped.items()):
        windows.append(dict(id=code, stock_code=code, start=min(dates), end=max(dates), kind='case'))
    with Research() as db:
        calendar = [r['trade_date'] for r in db.query('select trade_date from calendar order by 1')]
        idx = {d: i for i, d in enumerate(calendar)}
        for r in selection['selected']:
            c = old_cycles[r['cycle_id']]
            start = date.fromisoformat(r['first_board_date'])
            windows.append(dict(id=r['cycle_id'], stock_code=r['stock_code'],
                                start=date.fromisoformat(c['left_boundary_date']),
                                end=max(date.fromisoformat(c['right_boundary_date']), calendar[idx[start]+4]),
                                kind='predicted_negative', selected=r))
        keys = sorted({(w['stock_code'], d) for w in windows for d in calendar if w['start'] <= d <= w['end']})
        db.db.register('validation_keys', pa.Table.from_pylist([dict(stock_code=c, trade_date=d) for c, d in keys]))
        selected_bars = db.query('select d.* from daily d join validation_keys using(stock_code,trade_date)')
        bars = {(r['stock_code'], r['trade_date']): r for r in selected_bars}
        assert len(bars)==len(selected_bars), 'Duplicate selected stock-date prices'
        if len(bars) != len(keys):
            print('Missing selected bars:', len(keys)-len(bars))
        db.db.read_parquet(str(P52/'facts_*.parquet')).create_view('old_facts')
        old = {(r['stock_code'], r['trade_date']): r for r in db.query('select f.* from old_facts f join validation_keys using(stock_code,trade_date)')}
        db.db.read_parquet(str(P56/'stock_day_evidence.parquet')).create_view('direct_facts')
        direct = {(r['stock_code'], r['trade_date']): r for r in db.query('select f.* from direct_facts f join validation_keys using(stock_code,trade_date)')}
        stocks = {r['stock_code']: r for r in db.query('select * from stocks')}
        # Cached derived facts only. Counts identify the FIRST diagnostic blocker.
        diagnostic_reasons = db.query('select year(trade_date) as "year",diagnostic_reason,count(*) n from old_facts where diagnostic_classification=\'unknown\' group by 1,2 order by 1,n desc')
    aliases = pq.read_table(BASE/'dragon_v053/security_aliases.parquet').to_pylist()
    states = pq.read_table(BASE/'dragon_v053/historical_stock_status.parquet').to_pylist()
    rules = read(BASE/'dragon_v056/rules/manifest.json')
    for r in rules:
        assert sha(Path(r['file'])) == r['sha256']
    events = defaultdict(list)
    for notice in read(P54/'announcement_evidence.json'):
        for claim in notice['claims']:
            for ds in claim['dates']:
                events[claim['stock_code'], date.fromisoformat(ds)].append(dict(
                    stock_code=claim['stock_code'], trade_date=date.fromisoformat(ds), is_limit_up=True,
                    trusted=True, evidence_id='issuer_notice:'+notice['pdf_sha256'],
                    observed_at_utc=notice['reviewed_at_utc']))
    # Limited new reference: an explicit SINGLE closing statement, not a rule-derived label.
    # The regex reads code/date from the reviewed source, never from the case's expected outcome.
    source = read(OUT/'sources/manifest.json')['kelai_0206']
    assert sha(OUT/'sources/kelai_0206.pdf') == source['sha256']
    extracted = subprocess.run(['pdftotext',str(OUT/'sources/kelai_0206.pdf'),'-'],capture_output=True,text=True,check=True,timeout=10).stdout
    content = re.sub(r'\s+', '', extracted)
    code = stock_code(re.search(r'证券代码[：:](\d{6})', content)[1])
    for m in re.finditer(r'(\d{4})年(\d+)月(\d+)日，公司当日股票价格以涨停价收盘', content):
        day = date(*map(int, m.groups()))
        if not events[code, day]:
            events[code, day].append(dict(stock_code=code, trade_date=day, is_limit_up=True, trusted=True,
                evidence_id='issuer_notice:'+source['sha256'], observed_at_utc=source['fetched_at_utc']))
    all_rows = []
    for code, day in keys:
        bar = bars.get((code, day)); f = old.get((code, day), {}); st = stocks.get(code, {})
        status = status_at(code, day, [x for x in states if x['stock_code']==code], [x for x in aliases if x['stock_code']==code])
        trading = trading_evidence(code, day, bar, trusted=True, is_session=True, raw_trade_status=f.get('trade_status'),
            listing_date=st.get('listing_date'), delisting_date=st.get('delisting_date'), lifecycle_trusted=status['security_id'] is not None)
        listed = st.get('listing_date')
        evidence = dict(listing_date=listed, delisting_date=st.get('delisting_date'),
            listing_session=idx[day]-idx[listed]+1 if listed in idx else None,
            is_st=status['is_st'], special_status='unknown', reference_verified=f.get('reference_verified', False),
            conflict=status['verification_status'] in ('conflict', 'identity_ambiguous'))
        for rule in rules:
            if rule['start']<=str(day)<=rule['end'] and any(code.startswith(p) for p in rule['prefixes']):
                evidence.update(daily_upper_bound_or_no_limit=True, rule_evidence_id=rule['id'], rule_reviewed_at_utc=rule['reviewed_at_utc'])
                break
        proof = limit_evidence(bar, evidence, trading, events.get((code, day), []))
        # A mechanism probe only: change unknown ST to non-ST, retain explicit diagnostic assumptions.
        # This output is never truth, a repair, or a published label.
        cf = reconstruct(bar, {**evidence, 'trade_status':True if trading['state']=='traded_observed' else None,
                               'is_st':False if status['is_st'] is None else status['is_st']}, diagnostic=True)
        all_rows.append(dict(stock_code=code, security_id=status['security_id'], trade_date=day,
            close=bar.get('close') if bar else None, high=bar.get('high') if bar else None,
            pre_close=bar.get('pre_close') if bar else None, price_source=bar.get('source') if bar else None,
            trading_state=trading['state'], is_st=status['is_st'], status_evidence_ids=status['evidence_ids'],
            special_state='unknown', official_limit_price=None, official_limit_available=f.get('official_limit_available',False),
            reference_verified=f.get('reference_verified',False),
            old_diagnostic=f.get('diagnostic_classification','unknown'), old_diagnostic_reason=f.get('diagnostic_reason'),
            old_diagnostic_limit=f.get('diagnostic_limit_price'), old_assumptions=f.get('assumptions',[]),
            existing_direct=direct.get((code,day),{}).get('status','unknown'),
            reviewed_status=proof['status'], reviewed_reason=proof['basis'], rule_reason=proof['rule_reason'],
            evidence_ids=proof['evidence_ids'], independent_positive_reference=bool(events.get((code,day))),
            counterfactual_classification=cf['classification'], counterfactual_limit=float(cf['limit_price']) if cf['limit_price'] is not None else None,
            counterfactual_reason=cf['reason'], counterfactual_assumptions=cf['assumptions']))
    results = []
    for w in windows:
        rows = [r for r in all_rows if r['stock_code']==w['stock_code'] and w['start']<=r['trade_date']<=w['end']]
        days = [d for d in calendar if w['start']<=d<=w['end']]
        facts = {r['trade_date']:dict(status=r['reviewed_status'], evidence_ids=r['evidence_ids']) for r in rows}
        runs = streak_fields(w['stock_code'], days, facts)
        cf_runs = cycles(w['stock_code'], days, {r['trade_date']:dict(classification=r['counterfactual_classification'],in_target=True) for r in rows}, {}, require_event=False)
        start = w.get('selected',{}).get('first_board_date')
        if w['kind']=='case':
            matches = [r for r in samples if r['cohort']=='reconstruction' and r['stock_code']==w['stock_code'] and r['first_board_date'] and str(w['start'])<=r['first_board_date']<=str(w['end'])]
            truth = True if any(x['fields']['observed_streak_ge_5']['value'] is True for x in runs) else None
            prediction = True if any(x['outcome']=='success' for x in matches) else False if matches and all(x['outcome']=='failure' for x in matches) else None
        else:
            truth = None
            for run in runs:
                if str(run['fields']['first_board_date']['value'])==start:
                    truth = run['fields']['observed_streak_ge_5']['value']
            prediction = False
        board = w.get('selected',{}).get('board','main')
        first_state = next((r['is_st'] for r in rows if str(r['trade_date'])==start), None)
        regime = ('main_5_if_regular' if first_state is True else 'main_10_if_regular' if first_state is False else 'main_5_or_10_unresolved') if board=='main' else board+'_20_if_regular'
        results.append(dict(**w, board=board, regime=regime, truth=truth, prediction=prediction, runs=runs,
            price_rows=sum(r['close'] is not None for r in rows), sessions=len(days),
            st_known=sum(r['is_st'] is not None for r in rows), official_limits=sum(r['official_limit_available'] for r in rows),
            diagnostic_reasons=dict(Counter(r['old_diagnostic_reason'] for r in rows)),
            reviewed_counts=dict(Counter(r['reviewed_status'] for r in rows)),
            unknown_dates=[str(r['trade_date']) for r in rows if r['reviewed_status']=='unknown'],
            counterfactual_runs=[dict(first=c['first_board_date'],observed=c['observed_height'],exact=c['max_height']) for c in cf_runs]))
    funnels = {}
    for cohort in ('direct','reconstruction'):
        r = [x for x in samples if x['cohort']==cohort]
        dated = [x for x in r if x['first_board_date']]
        funnels[cohort] = dict(candidates=len(r), securities=len({x['stock_code'] for x in r}),
            dated=len(dated), undated=len(r)-len(dated), outcomes=dict(Counter(x['outcome'] for x in dated)),
            dated_exact=sum(x['exact_height'] is not None for x in dated),
            all_observed_ge5=sum(x['observed_height']>=5 for x in r),
            undated_observed_ge5=sum(x['observed_height']>=5 for x in r if not x['first_board_date']),
            success_boards=dict(Counter(x['board'] for x in r if x['outcome']=='success')),
            year_board=[dict(year=y,board=b,**dict(Counter(x['outcome'] for x in r if x['audit_year']==y and x['board']==b))) for y in range(2017,2027) for b in ('main','chinext','star')])
    metrics = []
    for year in range(2021,2026):
        for board in ('main','chinext','star'):
            group = [r for r in results if sample_year(r)==year and r['board']==board]
            metrics.append(dict(year=year,board=board,**confusion(group)))
    regime_metrics = []
    for year, regime in sorted({(sample_year(r),r['regime']) for r in results}):
        regime_metrics.append(dict(year=year,regime=regime,**confusion([r for r in results if sample_year(r)==year and r['regime']==regime])))
    daily_truth = {(r['stock_code'],r['trade_date']):True for r in all_rows if r['independent_positive_reference']}
    daily_predictions = {(r['stock_code'],r['trade_date']):dict(classification=r['old_diagnostic']) for r in all_rows}
    summary = dict(funnels=funnels, diagnostic_unknown_reasons=diagnostic_reasons, metrics=metrics,regime_metrics=regime_metrics,
        daily_reference_score=score(daily_predictions,daily_truth), cycle_score=confusion(results),
        selected_stock_days=len(all_rows), candidate_successes=[r for r in samples if r['cohort']=='reconstruction' and r['outcome']=='success'],
        warning='Case-control validation, not market accuracy. Direct input evidence is not an independent holdout for the direct engine.')
    inputs = [PS/'samples.json',P52/'cycles.json',P54/'announcement_evidence.json',OUT/'selection.json',
              BASE/'dragon_v053/historical_stock_status.parquet',BASE/'dragon_v053/security_aliases.parquet',OUT/'sources/manifest.json',Path(__file__),
              BASE/'calendar.parquet',BASE/'stocks.parquet',BASE/'dragon_v056/rules/manifest.json']
    inputs += [Path('backend/app/research')/(n+'.py') for n in ('trading_evidence','limit_rules','dragons','stock_status','queries')]
    inputs += list((OUT/'sources').glob('*.pdf'))
    outputs = OUT/'results'
    outputs.mkdir(exist_ok=True)
    for name,obj in [('summary',summary),('windows',results),('stock_days',all_rows)]:save(outputs/(name+'.json'),obj)
    save(outputs/'window_inputs.json',dict(bars=list(bars.values()),old_facts=list(old.values()),existing_direct=list(direct.values()),
        selected_calendar=[str(d) for d in calendar if any(w['start']<=d<=w['end'] for w in windows)]))
    with (outputs/'stock_days.csv').open('w') as f:
        writer=csv.DictWriter(f,fieldnames=list(all_rows[0]));writer.writeheader();writer.writerows(all_rows)
    assert all(sha(Path(p))==h for p,h in old_manifests.items()), 'Existing research manifest changed'
    save(outputs/'validation_manifest.json',dict(input_sha256={str(p):sha(p) for p in inputs},
        preserved_manifests=old_manifests, outputs={p.name:sha(p) for p in outputs.iterdir() if p.name!='validation_manifest.json'},
        no_production_publication=True))
    print(json.dumps(dict(funnels=funnels, cycle_score=summary['cycle_score'],daily=summary['daily_reference_score']),ensure_ascii=False))


if __name__=='__main__':
    build()
