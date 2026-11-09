"""ST attributes filter research views; they never determine limit-up truth.

The existing dated, identity-resolved status audit is reused without its
ordinary-session eligibility gate. No prices or label snapshots are written.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from app.research.non_st_scope import bundle, name_exclusion

VERSION = 'historical-st-view-v1'
BIAS = '局部证据样本；历史ST未知不是非ST。各ST状态及制度背景分别统计，不代表全市场晋级率。'


def available_evidence():
    try:return bundle()
    except FileNotFoundError:return {'by_sample': {}, 'by_day': {}}


def status(day):
    if day.get('conflict'): return 'unknown'
    if 'risk_warning_confirmed' in day.get('reasons', []): return 'confirmed_st'
    if day.get('non_st_confirmed'): return 'confirmed_non_st'
    return 'unknown'


def attribute(row, cohort='direct', data=None, whole_cycle=False):
    data = data if data is not None else available_evidence()
    sample = data['by_sample'].get((cohort, row.get('cycle_id', row.get('fact_id'))), {})
    dates = sample.get('observed_dates' if whole_cycle else 'required_dates', [])
    if not dates: dates = [str(p['trade_date']) for p in row.get('progress', [])]
    day = row.get('first_board_date') or row.get('first_observed_date') or sample.get('first_observed_date')
    if not dates and day: dates = [str(day)]
    evidence = [data['by_day'].get((row['stock_code'], str(d)), {}) for d in dates]
    states = [status(d) for d in evidence]
    first = data['by_day'].get((row['stock_code'], str(day or (dates[0] if dates else ''))), {})
    # Strict non-ST means every observed outcome date is evidenced non-ST.
    window = states[0] if states and len(set(states)) == 1 else 'unknown'
    return dict(status=window, first_date_status=status(first), window_status=window, dates=dates,
                evidence_ids=sorted({i for d in evidence for i in d.get('evidence_ids', [])}),
                source='identity_resolved_local_historical_status', version=VERSION,
                basis='observed_outcome_dates; mixed_or_missing_status_is_unknown', transition_or_gap=len(set(states)) > 1,
                strict_non_st_eligible=bool(states) and all(s == 'confirmed_non_st' for s in states))


def outcome(r):
    if r.get('category') in ('success', 'failure', 'unknown'): return r['category']
    v = r.get('outcome')
    if v in ('success', 'failure', 'unknown'): return v
    if v == 1: return 'success'
    if v == 0: return 'failure'
    return 'unknown'


def apply(rows, cohort='direct', *, scope='all', whole_cycle=False, keep_unknown=True, data=None):
    data = data if data is not None else available_evidence()
    scope = 'confirmed_non_st' if scope == 'non_st' else scope
    result = []
    for r in rows:
        a = attribute(r, cohort, data, whole_cycle)
        if scope == 'confirmed_non_st' and not a['strict_non_st_eligible']: continue
        if scope in ('confirmed_st', 'unknown') and a['status'] != scope: continue
        result.append({**r, 'historical_st': a})
    groups = defaultdict(list)
    for r in result:
        a = r['historical_st']
        groups[(a['window_status'], r.get('regime', 'unknown'))].append(r)
    summaries = []
    for (st, regime), group in sorted(groups.items()):
        c = Counter(outcome(r) for r in group); n = c['success'] + c['failure']
        summaries.append(dict(st_status=st, regime=regime, samples=len(group), success=c['success'], failure=c['failure'],
            unknown=c['unknown'], effective_n=n, promotion_rate=c['success']/n if n else None,
            evidence_grades=dict(Counter(str(r.get('evidence_grade', r.get('research_state', 'unspecified'))) for r in group)),
            sources=sorted({i.split(':')[0] for r in group for i in r['historical_st']['evidence_ids']}),
            strict_non_st=st == 'confirmed_non_st', selection_bias=BIAS))
    return result, dict(version=VERSION, scope=scope, total=len(rows), selected=len(result),
        status_counts=dict(Counter(r['historical_st']['status'] for r in result)), groups=summaries,
        facts_unchanged=True, unknown_is_failure=False, selection_bias=BIAS,
        pooled_rate_allowed=len(groups) == 1, strict_non_st=scope == 'confirmed_non_st')


def separate_rates(stats, meta):
    """Keep counts and original snapshots; suppress pooled comparisons in new views."""
    result = deepcopy(stats)
    if meta['pooled_rate_allowed']: return result
    def walk(x):
        if isinstance(x, dict):
            for key in list(x):
                if key in {'promotion_rate', 'wilson_iid_reference', 'promotion_ci', 'ci_stock', 'ci_date', 'rate', 'ci', 'wilson_ci', 'cluster_bootstrap_ci', 'missing_outcome_bounds',
                           'observation_month_scenario_bounds', 'all_undated_worst_case_bounds'}:
                    x[key] = None
                else: walk(x[key])
        elif isinstance(x, list):
            for y in x: walk(y)
    walk(result)
    result['grouped_st_regime_rates'] = meta['groups']
    result['pooled_rate_note'] = BIAS
    return result


def trading_candidates(rows, data=None):
    try: data = data if data is not None else available_evidence()
    except FileNotFoundError: data = {'by_day': {}}
    selected = []; reasons = Counter()
    for r in rows:
        q = data['by_day'].get((r['stock_code'], str(r['trade_date'])), {})
        veto = name_exclusion(r.get('stock_name'))  # dated event attribute; never identity
        if veto: reasons[veto] += 1
        elif q.get('status') != 'eligible' or status(q) != 'confirmed_non_st': reasons['eligibility_unknown'] += 1
        else: selected.append(r)
    return selected, dict(purpose='trading', input_count=len(rows), eligible=len(selected), excluded_reasons=dict(reasons),
        warning='交易候选默认排除ST、退市整理期及资格未知；空候选不表示没有涨停事实。')
