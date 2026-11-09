"""Read-only sample/processing audit and reproducible final study publication."""
from pathlib import Path
from collections import Counter
import csv,hashlib,json,sys
import duckdb,numpy,scipy,pyarrow
from app.research.unified import UnifiedResearch
from app.research.seasonality_study import query,load_release,VERSION,records

ROOT=Path(__file__).resolve().parents[1]
BASE=Path.home()/'Datasets/asharedragon/research'
OUT=ROOT/'docs/dragon_research_final_review'
OUT.mkdir(exist_ok=True)

def sha(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def save(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str,allow_nan=False)+'\n')
def pct(x):return '—' if x is None else f'{100*x:.4f}%'
def fmt(x):return '—' if x is None else str(x)
def table(headers,rows):return '\n|'+'|'.join(headers)+'|\n|'+'|'.join(['---']*len(headers))+'|\n'+'\n'.join('|'+'|'.join(map(fmt,r))+'|' for r in rows)+'\n'

def snapshot(name):
    manifest=BASE/name/'manifest.json';m=json.loads(manifest.read_text());p=BASE/name/m['dataset']
    for name,expected in m['files'].items():
        if sha(p/name)!=expected:raise ValueError(f'Checksum mismatch: {p/name}')
    return p,m


def build():
    before=json.loads((OUT/'before.json').read_text())
    p07,m07=snapshot('dragon_v07');p52,m52=snapshot('dragon_v052');p56,m56=snapshot('dragon_v056');pu,mu=snapshot('unified')
    source_hashes={k:v for k,v in json.loads((p07/'metadata.json').read_text())['input_sha256'].items() if '/processed/stocks/daily/' in k}
    for path,expected in source_hashes.items():assert sha(path)==expected,path
    assert len(source_hashes)==120
    v07=json.loads((p07/'samples.json').read_text());restored=json.loads((p07/'restored_240.json').read_text())
    restored_ids={r['cycle_id'] for r in restored};old_by={(r['stock_code'],r['first_board_date']):r for r in v07 if r['first_board_date']}
    baseline={(r['stock_code'],r['date']):r for r in before['transitions'] if r['date']}
    rows,_,meta=load_release(str(pu));direct=[r for r in rows if r['cohort']=='direct'];by={(r['stock_code'],r['first_board_date']):r for r in direct}
    differences=[]
    for r in v07:
        key=(r['stock_code'],r['first_board_date']);current=by.get(key);old=baseline.get(key)
        reason='same_dated_cycle_outcome' if current and current['outcome']==r['category'] else 'undated_cycle_not_assigned_to_start_month'
        if r['cycle_id'] in restored_ids:reason='v056_extra_five_session_gate_regression_fixed_using_existing_v07_outcome'
        differences.append(dict(cycle_id=r['cycle_id'],stock_code=r['stock_code'],first_board_date=r['first_board_date'],
            audit_year=r['year'],v07=r['category'],before=old['current'] if old else 'absent',
            final=current['outcome'] if current else 'not_in_dated_cohort',reason=reason))
    for r in direct:
        if (r['stock_code'],r['first_board_date']) not in old_by:
            differences.append(dict(cycle_id=r['cycle_id'],stock_code=r['stock_code'],first_board_date=r['first_board_date'],
                audit_year=r['year'],v07='not_in_dated_cohort',before=r['legacy_outcome'],final=r['outcome'],
                reason='source_reported_first_board_without_exact_linked_cycle; no new market source'))
    with (OUT/'sample_reconciliation.csv').open('w') as f:
        w=csv.DictWriter(f,fieldnames=list(differences[0]));w.writeheader();w.writerows(differences)
    save(OUT/'restored_240.json',[{'cycle_id':r['cycle_id'],'stock_code':r['stock_code'],'first_board_date':r['first_board_date'],
        'year':r['year'],'exact_max_streak':r['exact_max_streak'],'legacy_missing_observation_dates':r['legacy_missing_observation_dates'],
        'before':by[(r['stock_code'],r['first_board_date'])]['legacy_outcome'],'final':by[(r['stock_code'],r['first_board_date'])]['outcome']} for r in restored])
    study={cohort:query(cohort=cohort,page_size=100) for cohort in ('direct','reconstruction')}
    for cohort,x in study.items():save(OUT/(cohort+'.json'),x)
    save(OUT/'monthly_results.json',{c:x['cells'] for c,x in study.items()})
    audit56=json.loads((p56/'years.json').read_text());audit52=json.loads((p52/'summary.json').read_text())['yearly']
    with UnifiedResearch() as r:
        db=r.db
        db.execute(f"CREATE TEMP VIEW historical_scan AS SELECT * FROM read_parquet('{p52}/facts_*.parquet')")
        daily=records(db,"""SELECT d.year,count(*) unified_rows,count(d.close) price_rows,
            count(*) FILTER(WHERE f.stock_code IS NULL) no_historical_scan_key,
            count(*) FILTER(WHERE d.close IS NOT NULL AND f.price_present) original_prices_scanned,
            count(*) FILTER(WHERE d.close IS NOT NULL AND NOT f.price_present) supplementary_price_placeholders,
            count(*) FILTER(WHERE d.close IS NOT NULL AND f.stock_code IS NULL) unscanned_price_keys,
            count(*) FILTER(WHERE d.price_quality='verified') verified_prices,
            count(*) FILTER(WHERE d.close IS NULL) no_price_rows
            FROM stock_daily d LEFT JOIN historical_scan f USING(stock_code,trade_date) GROUP BY d.year ORDER BY d.year""")
        # All supplementary prices are suspension placeholders; no new valid traded bars hide in this difference.
        supplement=records(db,"""SELECT d.stock_code,CAST(d.trade_date AS VARCHAR) trade_date,d.price_quality,d.trade_status,d.volume,d.price_source,
            f.status historical_status,f.diagnostic_classification historical_diagnostic
            FROM stock_daily d JOIN historical_scan f USING(stock_code,trade_date)
            WHERE d.close IS NOT NULL AND NOT f.price_present ORDER BY d.trade_date,d.stock_code""")
        assert len(supplement)==84 and all(x['trade_status'] is False and x['volume'] is None and x['price_quality']=='diagnostic' for x in supplement)
        assert all(x['unscanned_price_keys']==0 and x['original_prices_scanned']==x['verified_prices'] for x in daily)
        save(OUT/'supplementary_suspension_prices.json',supplement)
        missing=records(db,"""SELECT d.stock_code,CAST(d.trade_date AS VARCHAR) trade_date,d.price_quality,d.event_id,d.is_limit_up
            FROM stock_daily d ANTI JOIN historical_scan f USING(stock_code,trade_date) ORDER BY d.trade_date,d.stock_code""")
        assert len(missing)==124 and all(x['price_quality']=='unknown' for x in missing)
        save(OUT/'event_only_outside_old_grid.json',missing)
        cases=records(db,"""SELECT c.cycle_id,c.stock_code,c.first_board_date,c.observed_min_height,c.exact_max_streak,
            c.cycle_end_date,c.complete_cycle_eligible,l.outcome,l.formal_eligible
            FROM cycles c JOIN labels l USING(cycle_id) WHERE
            (c.stock_code='603178.SH' AND c.first_board_date='2023-09-28') OR
            (c.stock_code='603580.SH' AND c.first_board_date='2024-03-05') ORDER BY c.stock_code""")
        for c in cases:
            c['current_study']=by[(c['stock_code'],c['first_board_date'])]
            c['old_price_rule_reasons']=records(db,f"""SELECT reason,diagnostic_reason,count(*) n FROM historical_scan
                WHERE stock_code='{c['stock_code']}' AND trade_date BETWEEN '{c['first_board_date']}' AND '{c['cycle_end_date']}' GROUP BY ALL""")
            c['legacy_ui_rows']=db.execute('SELECT count(*) FROM legacy_cycles WHERE stock_code=? AND first_board_date=?',[c['stock_code'],c['first_board_date']]).fetchone()[0]
        save(OUT/'cases.json',cases)
    for y in daily:
        audit=next(x for x in audit56 if x['year']==y['year']);v=[r for r in v07 if r['year']==y['year']];s=next(x for x in study['direct']['years'] if x['year']==y['year'])
        added=[r for r in direct if r['year']==y['year'] and (r['stock_code'],r['first_board_date']) not in old_by]
        y.update(scanned_v056=audit['scanned_stock_days'],scan_confirmed_up=audit['confirmed_up'],scan_confirmed_not_up=audit['confirmed_not_up'],scan_unknown=audit['unknown_days'],unknown_reasons=audit['unknown_reasons'],
            v07_cycles=len(v),undated_cycles=sum(r['first_board_date'] is None for r in v),exact_cycle_starts=sum(r['first_board_date'] is not None for r in v),
            added_event_starts=len(added),restored_short_cycles=sum(r['year']==y['year'] for r in restored),
            first_boards=s['first_boards'],effective_n=s['effective_n'],unknown=s['unknown'],ge5=s['heights']['5'],failure=s['failure'],
            formal_labels=0,legacy_diagnostic_first_boards=next(x for x in audit52 if x['year']==y['year'])['diagnostic_first_boards'])
        assert y['scanned_v056']==y['original_prices_scanned']
        assert y['scan_confirmed_up']+y['scan_confirmed_not_up']+y['scan_unknown']==y['scanned_v056']
        assert (y['first_boards'] or 0)==y['exact_cycle_starts']+y['added_event_starts']
        assert (y['first_boards'] or 0)==y['effective_n']+(y['unknown'] or 0)
    save(OUT/'yearly_funnel.json',daily)
    s=study['direct']['summary'];b=before['baseline_study']['summary'];restored_counts=Counter(r['exact_max_streak'] for r in restored)
    text=['# AShareDragon 第一轮研究最终样本与结果一致性验收\n',
        '验收日期：2026-11-08（America/Los_Angeles）；范围固定2017-01-01至2026-11-06，不含尚未发生的2026年剩余日期。',
        '\n## 验收结论\n',
        f"**发现并修复1处确定性结局版本回退**：统一发布器将v0.5.6五日窗口标签写入快照，上一轮研究直接使用该标签，导致v0.7已确认结束的240个短周期重新变为unknown。现行 `{VERSION}` 从同一统一快照的既有完整周期字段调用已验收的 `study_v07.cycle_outcome`，没有重算涨停、改原始标签文件或识别算法。",
        f"修复后：精确日期首板 **{s['first_boards']}**；成功 **{s['success']}**，失败 **{s['failure']}**，有效 **{s['effective_n']}**，unknown **{s['unknown']}**。晋级率 **{pct(s['rate'])}**，Wilson 95%区间 **{pct(s['ci'][0])}—{pct(s['ci'][1])}**。季节性仍为探索性，不获准发布正式总体结论。",
        '**全历史处理的精确回答：现有原始目标有效日线10,343,963条，2017—2026所有年份均已通过全量识别扫描，算法未运行的目标有效日线为0。不能把这句话扩展成“统一库每种记录都重新经过最新完整管道”：84条后来补入的停牌价格占位只有旧扫描的缺行情/停牌记录，没有按补入价格重跑旧规则；124条无日线的事件/属性记录不在旧日历网格，它们不是未处理的有效日线。已扫描也不等于证据充足或已经生成正式标签。**',
        '\n## v0.7 → 上轮v1 → 修复v1.1逐项对账\n',
        table(['总体','首板日期已定位','成功','失败','unknown','已知结局'],[
            ['v0.7全部周期',5558,76,5298,2935,5374],['v0.7精确日期子集',5558,76,5298,184,5374],
            ['上轮v1事件首板扩展总体',8269,b['success'],b['failure'],b['unknown'],b['effective_n']],
            ['修复后v1.1同一扩展总体',8269,s['success'],s['failure'],s['unknown'],s['effective_n']]]),
        'v0.7全部周期为8309条（第一列5558仅指日期可定位），unknown2935=首板日期未定位2751+日期已定位但结局未知184。当前研究仅按精确首板归月，因此2751条不放入月度分母，仍保留审计，不是失败或“无龙头”。',
        '统一first_board_samples另提供2711条来源明确报告首板、但尚未连接到精确边界周期的事件首板：2020年2206条，2026年505条。这是同一既有数据里的入样范围扩大，不是新增数据源或新增已验证周期；结局全部unknown。5558+2711=8269，184+2711=2895。事件首板8267条还不包含圣龙/艾艾两个公告边界周期，当前研究显式补入它们得到8269，避免遗漏。',
        '逐项迁移：76成功→76成功；5298失败中5058原样保留、240由unknown恢复失败；184已定位unknown仍unknown；2751未定位周期从月度视图排除；新增入样2711事件首板仍unknown。',
        '[逐周期对账CSV](dragon_research_final_review/sample_reconciliation.csv)覆盖8309个旧周期与2711个新增入样事件首板，每行列出代码、日期、v0.7/修复前/修复后结果及原因；[修复前冻结结果](dragon_research_final_review/before.json)保留原始差异。',
        '\n## 240个额外窗口样本：主样本与敏感性样本\n',
        '这240个不是额外发现的240个未知结局；它们已证实在5板以前结束，只缺周期结束后的无关观察日期。最高1/2/3板分别219/17/4个；缺额外1/2/3日分别202/23/15个；2020年238个、2026年2个。',
        'v0.6曾以71成功+5058失败=5129作主样本，71+5298=5369完整周期作敏感性。v0.7正式修复二元结局门槛：达到5板即成功（不强求未来终点），恢复5个成功片段及240个已结束失败，主研究有效样本变成76+5298=5374。这240个自v0.7起就是主样本，不能继续只放敏感性组。',
        f"v1.1 API显式输出 `restored_short_cycles=240`。旧额外五日窗口只作为排除这240条的敏感性对照：有效n={study['direct']['legacy_window_sensitivity']['effective_n']}，比例={pct(study['direct']['legacy_window_sensitivity']['rate'])}；这是有意改变研究样本，不是将240条再次标为unknown或失败未知。",
        '[240条逐项记录与原缺口日期](dragon_research_final_review/restored_240.json)。回归测试先复现240条全部退回unknown，再验证240条恢复且全部5558个日期已定位周期与v0.7逐项一致。',
        '\n## 每年识别处理覆盖与阶段损失\n',
        '“股票日”与“周期”是不同单位，下列两个漏斗不能直接相减。原始目标按Research/catalog的未复权行情定义，北交所及未采集行情不在已完成声明中。无价事件补充/目录缺口不算0行情。',
        table(['年','统一行数','原始有效日线已扫描','目标有效日线未运行','停牌价占位（未按新价格重跑）','无行情行','无旧扫描键（均无行情）'],[[r['year'],r['unified_rows'],r['original_prices_scanned'],r['unscanned_price_keys'],r['supplementary_price_placeholders'],r['no_price_rows'],r['no_historical_scan_key']] for r in daily]),
        '覆盖核验不是只比总数：将当前统一stock_daily股票+日期逐键与v0.5.2的facts_2017…facts_2026全量审计连接；所有当前有行情的键均存在旧扫描；其中84条旧price_present=false。全部84条现在trade_status=false、volume缺失、price_quality=diagnostic，不能作为新增有效交易日线。再逐年与v0.5.6扫描总数及输入快照哈希核对，原始有效日线逐年完全一致，且v0.7记录的120个原始行情分区哈希逐一匹配。',
        table(['年','扫描股票日','确认涨停日','确认非涨停日','证据不足unknown日','旧价格情景首板（不混入独立）'],[[r['year'],r['scanned_v056'],r['scan_confirmed_up'],r['scan_confirmed_not_up'],r['scan_unknown'],r['legacy_diagnostic_first_boards']] for r in daily]),
        table(['年','直接候选周期','起点未知排除','精确周期首板','另入样事件首板','研究首板','有效','unknown','≥5板','恢复失败','正式标签'],[[r['year'],r['v07_cycles'],r['undated_cycles'],r['exact_cycle_starts'],r['added_event_starts'],r['first_boards'],r['effective_n'],r['unknown'],r['ge5'],r['restored_short_cycles'],r['formal_labels']] for r in daily]),
        '每行满足：候选周期−起点未知=精确周期首板；精确周期首板+另入样事件首板=研究首板；研究首板=有效+unknown；有效=成功+失败。—表示没有研究样本，正式标签数0表示发布状态，不代表没有历史龙头。高度、ST、板块、等级等交互筛选是在上述母体上的研究排除，不是算法未运行。',
        '证据不足：2017、2025缺适用的已审查负向规则且无独立正向事件；2018、2019、2021、2022存在部分可确认非涨停日，却没有独立正向首板母体。2023/2024分别仅有圣龙/艾艾公告周期。2020来源覆盖集中；2026结局与边界证据很薄弱。各年的unknown原因和计数详见 [年度漏斗JSON](dragon_research_final_review/yearly_funnel.json)。因此2017—2019、2021—2022、2025并非未跑算法；2023—2024也不是全年未跑，只是独立正向事实局限于个案。所有年份均未达到完整年度正式独立证据发布门槛。',
        '\n## 圣龙与艾艾可见性\n',
        table(['股票','精确首板','已达/精确最高','v0.7','当前独立证据视图','全市场正式标签','旧诊断视图'],[[c['stock_code'],c['first_board_date'],f"{c['observed_min_height']}/{c['exact_max_streak']}",'success','verified success','未获准','无该周期'] for c in cases]),
        '访问 `/dragons` → 启动规律 → 独立证据 → 对应年份（2023/2024）→ 高度≥10、结局等级verified，分别可见603178.SH/603580.SH。历史ST均unknown，若选择“历史非ST”或“历史ST”会被研究属性筛选排除；这不否定其独立连板事实。',
        '旧全历史价格诊断v0.5.2中，两窗口被historical_st_missing阻断（严格路径同时有historical_trading_status_missing）；不能为了显示它们降低ST门槛。启动规律的“日线重建诊断（归档）”实际读取统一库legacy_cycles，只含早期2020/2026局部窗口，不是v0.5.2全历史扫描；所以2023/2024在该队列缺失是归档范围差异。独立证据通道已经显示，不能将已证实周期硬塞到诊断或冒充全市场正式标签。',
        '[逐案周期与旧规则原因](dragon_research_final_review/cases.json)。',
        '\n## unknown、零值与统计结果验收\n',
        'unknown记录未纳入失败分母；有效n=0时rate/CI为null、页面显示—。主样本恢复后2026年为2个已结束失败+513个unknown，显示0/2=0%是这两个已知周期的条件比例，不是把513个unknown计作失败，Wilson上界约65.76%。历史非ST为448首板、2有效、446 unknown；20%制度仍无有效结局，比例留空。',
        '有首板但尚无已证实高板时，数量表可显示“已证实达到门槛0条”，unknown仍另列；这个0不代表真实最终高板数为0。无首板样本的月/年显示—，不绘制为0。正式标签0、有效样本0、数据质量覆盖计数0均须读其明确分母，不能替代缺失值。',
        table(['窗口','主样本率差','单侧Fisher p','三项BH q','每股首周期率差','剔除2020率差'],[[r['window'],pct(r['difference']),f"{r['p_iid']:.6g}" if r['p_iid'] is not None else '—',f"{r['bh_q']:.6g}" if r['bh_q'] is not None else '—',pct(r['one_cycle_per_stock']['difference']),pct(r['without_2020']['difference'])] for r in study['direct']['contrasts']]),
        '修复后季节性主结论未变：不支持窗口更高的正式结论，部分敏感性仍翻转。旧v1文档与附件保留历史结果，并明确标注已被本次验收取代，禁止旧生成脚本用v1.1 API覆盖出混合口径报告。当前完整年月统计及特征敏感性见 [独立结果](dragon_research_final_review/direct.json)、[归档诊断](dragon_research_final_review/reconstruction.json)、[两队列120格年月统计](dragon_research_final_review/monthly_results.json)。',
        '\n## 固定版本、输入与复现\n',
        f"研究版本 `{VERSION}`；统一快照 `{pu.name}`（底层仍为research-final-v1）；v0.7对账快照 `{p07.name}`；全历史扫描v0.5.2 `{p52.name}`；v0.5.6 `{p56.name}`。底层统一labels仍保留76/5058/3175的历史发布事实，本研究的v0.7兼容口径在读取层明示，不声称已改写底层快照。",
        '`backend/app/research/seasonality_study_v1_lock.json` 固定输入manifest哈希、起止日期、门槛、月份窗口、种子和统计方法。API发现活动manifest变更即拒绝静默切换。归档release_lock.json固定所有输入文件预期哈希、相关代码哈希、库版本、验收附件哈希和默认筛选。统计输入先按队列/日期/代码排序，避免数据库无序返回改变bootstrap抽样顺序。',
        '复现：`PYTHONPATH=backend .venv/bin/python scripts/review_dragon_research_final.py`；冻结校验：`PYTHONPATH=backend .venv/bin/python scripts/review_dragon_research_final.py --verify`。审计只读文件/数据库，不访问行情接口、不写原始行情、不新增数据源、不改识别算法、不训练模型、不执行GitHub Push。']
    qa=OUT/'browser_acceptance.md'
    if qa.exists():text.append(qa.read_text())
    (ROOT/'docs/dragon_research_final_review.md').write_text('\n\n'.join(text)+'\n')
    codefiles=['backend/app/research/seasonality_study.py','backend/app/research/study_v07.py','backend/app/research/feature_study.py','backend/app/research/dragons.py','backend/app/research/seasonality_study_v1_lock.json','backend/app/api/dragons.py','frontend/src/components/SeasonalityExploration.tsx','frontend/src/components/Chart.tsx','frontend/src/components/Dragons.tsx','frontend/src/app/globals.css','backend/tests/test_seasonality_study.py','scripts/browser_dragon_final_review.mjs','scripts/review_dragon_research_final.py']
    inputs=dict(source_hashes)
    for p,m in [(pu,mu),(p07,m07),(p52,m52),(p56,m56)]:
        inputs[str(p.parent.parent/'manifest.json')]=sha(p.parent.parent/'manifest.json')
        inputs.update({str(p/k):v for k,v in m['files'].items()})
    lock=dict(study_version=VERSION,inputs=inputs,code={f:sha(ROOT/f) for f in codefiles},
        libraries=dict(numpy=numpy.__version__,scipy=scipy.__version__,duckdb=duckdb.__version__,pyarrow=pyarrow.__version__),
        artifacts={str(p.relative_to(ROOT)):sha(p) for p in OUT.iterdir() if p.is_file() and p.name!='release_lock.json'},
        parameters=dict(cohort='direct',start_year=2017,end_year=2026,board=None,regime=None,st='all',grade='all',height=5,month=None,page=1,page_size=100),
        results={c:sha(OUT/(c+'.json')) for c in study})
    save(OUT/'release_lock.json',lock)
    print(json.dumps(dict(summary=s,original_daily=sum(x['original_prices_scanned'] for x in daily),supplementary=84,no_old_scan_no_prices=124),ensure_ascii=False))


def verify():
    lock=json.loads((OUT/'release_lock.json').read_text())
    assert lock['libraries']==dict(numpy=numpy.__version__,scipy=scipy.__version__,duckdb=duckdb.__version__,pyarrow=pyarrow.__version__), 'Library version drift'
    for file,expected in lock['inputs'].items():assert sha(file)==expected,file
    for file,expected in lock['code'].items():assert sha(ROOT/file)==expected,file
    for file,expected in lock['artifacts'].items():assert sha(ROOT/file)==expected,file
    for cohort in ('direct','reconstruction'):
        expected=json.loads((OUT/(cohort+'.json')).read_text())
        actual=query(**{**lock['parameters'],'cohort':cohort})
        assert actual==expected,cohort+' result drift'
        assert sha(OUT/(cohort+'.json'))==lock['results'][cohort]
    print('PASS: frozen inputs, source code and recomputed results match')

if __name__=='__main__':verify() if '--verify' in sys.argv else build()
