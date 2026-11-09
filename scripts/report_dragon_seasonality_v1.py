"""Reproduce v1 report exclusively from the immutable unified release; no network."""
from pathlib import Path
import csv,json
from app.research.seasonality_study import query,load_release,summary
OUT=Path('docs');ASSETS=OUT/'dragon_seasonality_study_v1';ASSETS.mkdir(exist_ok=True)

def fmt(x):return '—' if x is None else f'{x:.5g}' if isinstance(x,float) else str(x)
def pct(x):return '—' if x is None else f'{x*100:.3f}%'
def ci(x):return '—' if not x else ' ～ '.join(map(pct,x))
def table(headers,rows):return '\n'+' | '.join(headers)+'\n'+' | '.join(['---']*len(headers))+'\n'+'\n'.join(' | '.join(map(str,r)) for r in rows)+'\n'

def main():
    results={c:query(cohort=c,page_size=100) for c in ('direct','reconstruction')}
    if results['direct']['meta'].get('study_version')=='dragon-seasonality-study-v1.1':
        raise SystemExit('v1 report is archived. Use scripts/review_dragon_research_final.py for corrected v1.1 results.')
    rows,coverage,meta=load_release(results['direct']['meta']['snapshot'])
    for cohort,x in results.items():
        (ASSETS/(cohort+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2,allow_nan=False))
    # All year/month/regime/ST cells, including null no-sample cells, for audit and reuse.
    with (ASSETS/'monthly_strata.csv').open('w') as f:
        w=csv.writer(f);w.writerow(['cohort','year','month','regime','historical_st','first_boards','ge3','ge5','ge7','ge10','effective_n','unknown','rate','ci_low','ci_high'])
        for cohort in results:
            for year in range(2017,2027):
                for month in range(1,13):
                    for regime in ('main_10','chinext_10','chinext_20','star_20'):
                        for st in ('confirmed_non_st','confirmed_st','unknown'):
                            s=summary([r for r in rows if (r['cohort'],r['year'],r['month'],r['regime'],r['st'])==(cohort,year,month,regime,st)])
                            w.writerow([cohort,year,month,regime,st,s['first_boards'],*[s['heights'][str(k)] for k in (3,5,7,10)],s['effective_n'],s['unknown'],s['rate'],*(s['ci'] or [None,None])])
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    fig,axes=plt.subplots(2,2,figsize=(12,7),layout='constrained')
    for col,(cohort,x) in enumerate(results.items()):
        ms=x['months'];xs=np.arange(1,13);rates=np.array([r['rate']*100 if r['rate'] is not None else np.nan for r in ms]);low=np.array([r['ci'][0]*100 if r['ci'] else np.nan for r in ms]);hi=np.array([r['ci'][1]*100 if r['ci'] else np.nan for r in ms]);counts=[r['heights']['5'] if r['heights']['5'] is not None else np.nan for r in ms]
        axes[0,col].bar(xs,counts,color='#298e79');axes[0,col].set_title(cohort+' | observed >=5 starts');axes[0,col].set_ylabel('Cycles (missing left blank)')
        axes[1,col].errorbar(xs,rates,yerr=[np.maximum(0,rates-low),np.maximum(0,hi-rates)],fmt='o',capsize=3,color='#326ba8');axes[1,col].set_title('Known-outcome rate + 95% Wilson (IID reference)');axes[1,col].set_ylabel('Percent');axes[1,col].set_xlabel('Exact first-board start month')
        for ax in axes[:,col]:ax.set_xticks(xs);ax.grid(axis='y',alpha=.2)
    fig.savefig(ASSETS/'monthly.png',dpi=160);plt.close(fig)
    fig,ax=plt.subplots(figsize=(12,4),layout='constrained');matrix=np.array([r['heights']['5'] if r['heights']['5'] is not None else np.nan for r in results['direct']['cells']]).reshape(10,12)
    cmap=plt.get_cmap('YlGnBu').copy();cmap.set_bad('#eeeeee');im=ax.imshow(np.ma.masked_invalid(matrix),cmap=cmap,aspect='auto');ax.set_yticks(range(10),range(2017,2027));ax.set_xticks(range(12),range(1,13));ax.set_title('Independent evidence: >=5 by exact start year/month; gray = no samples')
    for y in range(10):
        for m in range(12):ax.text(m,y,'—' if np.isnan(matrix[y,m]) else str(int(matrix[y,m])),ha='center',va='center',fontsize=8)
    fig.colorbar(im,ax=ax);fig.savefig(ASSETS/'heatmap.png',dpi=160);plt.close(fig)
    d=results['direct'];s=d['summary']
    out=['# 龙头启动规律第一轮实证研究 v1\n',
    '研究日期：2026-11-08（用户时区）。数据截至2026-11-06，2026年非完整年度。',
    '\n## 结论\n',
    f"独立证据中有 **{s['first_boards']}** 个精确首板日期样本，已知结局 **{s['effective_n']}** 个：达到5板 **{s['success']}** 个、未达5板 **{s['failure']}** 个；unknown **{s['unknown']}** 个。样本内5板晋级率 **{pct(s['rate'])}**，Wilson 95%区间 **{ci(s['ci'])}**。这不是全市场晋级概率。",
    '3—5月、9—12月及两窗口合并后的样本内晋级率均低于其余月份，单侧检验不支持“更高”。这既不是季节规律被证实，也不能证明真实总体不存在季节效应；年份集中、历史ST缺失及非随机入样阻止正式推断。',
    '\n## 数据与预先固定口径\n',
    f"唯一数据源：只读统一研究快照 `{meta['snapshot']}`；版本 `{meta['version']}`。未新增数据源、重建数据模型或修改原始行情。",
    '独立队列：first_board_samples 的来源明确报告1板且无事件冲突的记录，连接 sample_labels / sample_cycles / cycles；额外保留已有 cycles 中已证实首板边界但不在 first_board_samples 中的2个周期。因此8267条事件首板 + 2条精确边界周期 = 8269条。标签保持原有76成功、5058失败。未知结局首板仍保留。',
    '重建诊断队列：统一库中的 legacy_cycles（dragon_v1归档），是日线规则诊断结合局部事件池门控的旧样本，不是旧seasonality接口的v0.5.2全区间诊断库，也不是新做全市场重建。保留其边界与诊断等级，不升级为独立事实。以股票代码+精确首板日期在队列内去重；两队列永不合并。',
    '启动月只取已确认或诊断重建的 first_board_date；不取最高价日期、首次观测日期或观察到高板的日期。独立队列还有2751条无精确首板日期的周期记录、诊断队列734条，无法定位到月份，单独披露且不加入分母。它们不等于另外2751/734只股票，也可能与事件首板关联，不能相加推算市场总数。',
    '≥3/5/7/10为累计“已观测达到”数量，不要求右边界闭合；不把未知最高高度视为1板失败。5板比例=success/(success+failure)，unknown不进入分母。无样本格用—/CSV空值，不绘制成0；有样本但未观察到高板可为0，仅为局部计数。',
    '主板10%、创业板改革前10%、改革后20%、科创板20%按现有board_context及首板日期分层。它们是制度背景，不是已验证特殊交易日限幅：主板ST不是10%普通状态，ST未知不能归为非ST；IPO等特殊状态仍可能混杂。历史ST来自统一stock_daily首板日is_st/st_quality，不是今天名称，也不宣称整个周期状态不变。',
    'CI：月度Wilson 95%为IID参考。整体及窗口差值另以股票代码聚类、1000次bootstrap（固定种子）评估重复周期；成功/失败至少各5个股票簇，否则留空。股票代码是统一库内身份代理，缺乏更细证券生命周期键仍是限制。',
    '检验方向事先固定为窗口更高；3—5、9—12、两者合并三项Fisher单侧检验构成BH族。5项特征的每股首次已知结局周期Mann–Whitney双侧检验另作BH族；样本不足不检验。没有搜索最优月份或训练模型。',
    '\n## 图表\n','![真实月度计数和置信区间](dragon_seasonality_study_v1/monthly.png)','![真实年月分布](dragon_seasonality_study_v1/heatmap.png)']
    for cohort,x in results.items():
        out+=['\n## '+('独立证据' if cohort=='direct' else '归档日线重建诊断')+'：月度统计\n']
        out+=[table(['月','首板','≥3','≥5','≥7','≥10','有效n','unknown','5板比例','Wilson 95%'],[[r['month'],fmt(r['first_boards']),*[fmt(r['heights'][str(k)]) for k in (3,5,7,10)],r['effective_n'],fmt(r['unknown']),pct(r['rate']),ci(r['ci'])] for r in x['months']])]
        out+=['\n### 逐年逐月完整表\n',table(['启动年月','首板','≥3','≥5','≥7','≥10','有效n','unknown','5板比例','Wilson 95%'],[[f"{r['year']}-{r['month']:02d}",fmt(r['first_boards']),*[fmt(r['heights'][str(k)]) for k in (3,5,7,10)],r['effective_n'],fmt(r['unknown']),pct(r['rate']),ci(r['ci'])] for r in x['cells']])]
        out+=['\n### 制度与历史ST分层\n',table(['制度背景','首板日ST','首板','有效n','unknown','比例','95%区间'],[[r['regime'],r['st'],fmt(r['first_boards']),r['effective_n'],fmt(r['unknown']),pct(r['rate']),ci(r['ci'])] for r in x['strata']])]
        out+=['\n### 窗口比较及敏感性\n',table(['窗口','窗口成功/有效n','其他成功/有效n','窗口率','其他率','差值','聚类95%差值区间','单侧p','BH q'],[[r['window'],f"{fmt(r['target']['success'])}/{r['target']['effective_n']}",f"{fmt(r['other']['success'])}/{r['other']['effective_n']}",pct(r['target']['rate']),pct(r['other']['rate']),pct(r['difference']),ci(r['stock_cluster_ci']),fmt(r['p_iid']),fmt(r['bh_q'])] for r in x['contrasts']])]
        out+=[table(['窗口','去2020差值','每股首周期差值','年份等权差值','年×制度×ST调整差值','共同层数/有效n','高结局覆盖差值'],[[r['window'],pct(r['without_2020']['difference']),pct(r['one_cycle_per_stock']['difference']),pct(r['balanced_year_difference']),pct(r['adjusted']['difference']),f"{r['adjusted']['strata']}/{r['adjusted']['effective_n']}",pct(r['coverage_restricted']['difference'])] for r in x['contrasts']])]
        out+=['\n逐年留一敏感性（差值）：',table(['窗口','剔除年份','差值','窗口有效n','其他有效n'],[[r['window'],z['year'],pct(z['difference']),z['target']['effective_n'],z['other']['effective_n']] for r in x['contrasts'] for z in r['leave_one_year_out']])]
        out+=['\n### 首板特征比较\n',table(['字段','成功n/中位数','失败n/中位数','已知结局字段缺失','Cliff δ','每股n 成功/失败','p','BH q'],[[r['label'],f"{r['success']['n']}/{fmt(r['success']['median'])}",f"{r['failure']['n']}/{fmt(r['failure']['median'])}",r['missing'],fmt(r['cliffs_delta']),str(r['one_stock_counts']),fmt(r['p_one_stock']),fmt(r['bh_q'])] for r in x['features']])]
    out+=['\n## 年份与覆盖分布\n',table(['年','日线行','已知目录stock-day覆盖','独立首板','独立有效n','独立unknown','独立涨停判定行'],[[y['year'],y['price_rows'],pct(y['known_catalogue_coverage']),fmt(d['years'][i]['first_boards']),d['years'][i]['effective_n'],fmt(d['years'][i]['unknown']),sum(c['limit_days'] for c in coverage if c['year']==y['year'])] for i,y in enumerate(meta['yearly'])])]
    out+=['\n## 解释、局限与下一步假设\n',
    f"2020年占独立首板 {pct(d['years'][3]['first_boards']/s['first_boards'])}，独立成功样本中74/76在2020年；2023及2024各1条是定向补证成功案例。剔除2020年后，3—5月及9—12月与补集的差值为0，但由极少且高度选择性的已知结局支撑；合并窗口缺少补集已知结局，差值不可估。这不是稳健的跨年验证。",
    '敏感性存在方向翻转：3—5月每股首周期差值+0.266个百分点、高结局覆盖子样本+0.299个百分点；合并窗口对应+0.045和+0.126个百分点，均与原始合并比例的负差值方向不同。不能因为任一子样本出现正差值就宣称规律成立，这反而提示构成不稳健。9月的100%仅由1个定向补证成功周期形成，必须连同n=1和宽区间阅读。',
    '首板基数已通过比例分母控制：高数量月份不等于高晋级率。年份等权只平均两侧均有有效样本的年份；共同层调整在年×制度×首板ST内比较，并以两侧有效n的较小者加权，仅作构成诊断。高覆盖分析限定已知结局比例≥当前年月中位数，不按成功率挑选。去2020、每股首周期及逐年留一结果均在上表；这些处理不能让局部样本变成随机样本。',
    f"缺失结局极端界限：独立队列 {ci(s['unknown_bounds'])}，远宽于条件Wilson区间；这只处理已入样首板的结局缺失，不涵盖未入样市场首板。高日线覆盖不能证明涨停标签覆盖，2017—2019、2021—2022及2025的空标签年份不能算0。2026尾部还有随访不足及12月未到来的右删失。",
    '首板特征取统一stock_daily同日原字段；不将EOD流通市值、成交额、换手率、封单金额当作盘中可用信息。首封时间换算为北京时间自午夜秒数，缺失保持空值。市值/换手等字段仅有诊断级字段质量；成功组/失败组分布和δ不能解释因果。特征比较还受年份、板块、IPO、入样方式和缺失模式混杂影响。',
    '探索性特征发现：成功组首封中位数09:57:20，失败组10:53:37.5；封单金额中位数约3879万元 vs 2586万元。每股首个已知结局周期检验后，首封与封单BH q分别约0.00070、0.00059；成交额约0.02793，市值及换手未通过0.05阈值。这些是条件样本关联，不能视作已验证交易规则。',
    '制度/ST限制：独立队列全部已知结局的首板日ST均未知；已确认非ST的448个首板全部unknown，已确认ST没有首板样本。20%制度样本也全部unknown，因此无法实际比较10%与20%或ST与非ST晋级率。',
    '最值得下一轮验证：①先固定完整非2020年份的独立首板母体和随访窗口，再复核预先指定的3—5/9—12月份对照；②在同年、同制度且历史ST已知的样本中，优先验证较早首封与较大封单金额的关联，市值作为未获支持的对照；③在同一股票内比较不同月份启动周期，区分股票固定特征与月份差异。本轮不扩源、不补分钟线、不训练预测模型。',
    '\n## 复现与交付\n',
    '绘图可选依赖：`matplotlib`（本次3.11.2，仅用于离线报告）；`PYTHONPATH=backend .venv/bin/python scripts/report_dragon_seasonality_v1.py`。统计核心 `backend/app/research/seasonality_study.py`；API `/api/research/dragons/seasonality-study-v1`；UI `/dragons` →「启动规律」，`/seasonality`复用同一组件。',
    '筛选：年份、板块、制度、历史ST、结局证据等级、连板高度。月份图与按钮筛列表；热力图筛年份+月份；股票跳已有个股页。晋级率分母不受高度或列表月份筛选影响。原始数量表保留所有门槛便于审计。覆盖背景明确标注不随板块/ST筛选。',
    '[分层月度CSV](dragon_seasonality_study_v1/monthly_strata.csv)含两队列×10年×12月×4制度×3历史ST的全部格子；[独立结果JSON](dragon_seasonality_study_v1/direct.json)、[诊断结果JSON](dragon_seasonality_study_v1/reconstruction.json)含完整敏感性与缺失分布。']
    acceptance=ASSETS/'acceptance.md'
    if acceptance.exists():out.append(acceptance.read_text())
    (OUT/'dragon_seasonality_study_v1.md').write_text('\n'.join(out)+'\n')
    print('Report written:',OUT/'dragon_seasonality_study_v1.md')
if __name__=='__main__':main()
