"""Report only: query existing classified segments; never rebuild raw daily data."""
from pathlib import Path
import csv,json,hashlib
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from app.statistics.non_ipo_study import study,VERSION
from app.statistics.history_limit_queries import ROOT
OUT=Path('docs/non_ipo_dragon_study');OUT.mkdir(exist_ok=True)
s=study();years=s['yearly'];months=s['months'];q=s['quality']
def csvfile(name,items):
 with (OUT/name).open('w') as f:
  w=csv.DictWriter(f,fieldnames=list(items[0]));w.writeheader();w.writerows(items)
def table(head,rows):return '| '+' | '.join(head)+' |\n|'+'|'.join(['---']*len(head))+'|\n'+''.join('| '+' | '.join(str(v) for v in r)+' |\n' for r in rows)
csvfile('annual.csv',years);csvfile('launch_months_ge5.csv',months)
(OUT/'result.json').write_text(json.dumps(s,ensure_ascii=False,indent=2,default=str)+'\n')
fig,ax=plt.subplots(figsize=(10,4.5));x=list(range(1,13));ax.bar([i-.19 for i in x],[r['confirmed'] for r in months],width=.38,label='Confirmed positive-event segments',color='#329c80');ax.bar([i+.19 for i in x],[r['candidate'] for r in months],width=.38,label='Rule-reconstructed candidates',color='#c89843');ax.set(xticks=x,xlabel='First-board launch month (trusted start only)',ylabel='Unique observation segments',title='2017–2026 non-IPO streaks ≥5: exploratory launch distribution');ax.legend(fontsize=8);ax.spines[['top','right']].set_visible(False);fig.tight_layout();fig.savefig(OUT/'launch_months_ge5.svg');fig.savefig(OUT/'launch_months_ge5.png',dpi=150);plt.close(fig)
annual=table(['年','≥3板','≥5板','≥7板','≥10板'],[[r['year'],*[r['ge'+str(h)] for h in [3,5,7,10]]] for r in years])
evidence=table(['年','≥3 确认/候选','≥5 确认/候选','≥7 确认/候选','≥10 确认/候选'],[[r['year'],*[f"{r['confirmed_ge'+str(h)]} / {r['candidate_ge'+str(h)]}" for h in [3,5,7,10]]] for r in years])
coverage=table(['年','≥5非IPO段','月图有效起点','起点unknown','IPO排除','IPO归属unknown','边界完整/总数','完整率'],[[r['year'],r['high_samples'],r['high_trusted_start'],r['high_start_unknown'],r['high_ipo_excluded'],r['high_ipo_unknown'],f"{r['high_complete']} / {r['high_samples']}",f"{r['complete_rate']:.1%}" if r['complete_rate'] is not None else '—'] for r in years])
monthly=table(['启动月份','独立确认正向段','规则重建候选段','有效起点合计'],[[r['month'],r['confirmed'],r['candidate'],r['n']] for r in months])
files=['backend/app/statistics/non_ipo_study.py','backend/app/api/history_limits.py','frontend/src/components/NonIpoStudy.tsx','frontend/src/components/Dragons.tsx','scripts/report_non_ipo_dragon_study.py']
lock=dict(version=VERSION,statistics_manifest=json.loads((ROOT/'manifest.json').read_text()),classification_manifest=json.loads((ROOT/'classification_manifest.json').read_text()),sha256={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files})
(OUT/'snapshot_lock.json').write_text(json.dumps(lock,indent=2)+'\n')
text=f'''# 非IPO高连板股票研究：2017—2026

## 结果与边界

基于现有 **117,611** 个唯一连板观察段，以 `(stock_code, run_start)` 为唯一键。本轮只增加只读汇总查询和现有启动规律Tab内的研究结果，不修改涨停识别、IPO分类算法、原始行情或现有分类快照，不重算原始日线。

主样本严格要求 `ipo_origin=non_ipo`，既不纳入IPO初始，也不把IPO归属unknown当成非IPO。全量数据继续保留在历史连板排行。

默认≥5板主样本 **1,740段**；其中可信起点 **1,633段**、起点unknown **107段**。起点可信按现有基础统计 `first_board_date` 非空定义；它是可定位的基础情景起点，不能升级为严格学术独立边界证据。

边界不完整 **178段**，历史ST未知 **1,738段**，可能与前段属于同一真实周期的片段 **25段**。这些属性可重叠，不可相加作排除总数。只有观察段键去重得到证明，缺口两侧经济周期是否独立仍可能unknown。所有段可交易性均未验证；IPO排除不产生普通非ST或可成交资格。

## 2017—2026年度非IPO高连板统计

数量是唯一观察段，非股票去重数；不同周期可以来自同一股票。门槛为累计≥，不能相加。边界不完整的段使用已观察最低高度达到门槛，不把它伪装成最终高度。按观察段起日归年；起点unknown段只进入年度观察统计，不进入启动月份图。

{annual}

独立确认正向段与规则重建候选的分解：

{evidence}

“独立确认”在本报告仅指整段正向涨停事件均为confirmed；首尾非涨停边界仍可能来自规则推导。候选段含任一重建正向日即归候选，不混入confirmed。没有独立正向记录的年度，确认数量0表示此本地队列没有已确认段，不能解释为全市场没有涨停或高连板。

## 启动月份分布图

![非IPO≥5板启动月份分布](non_ipo_dragon_study/launch_months_ge5.svg)

仅纳入 `first_board_date` 非空且IPO归属已识别为非IPO的≥5板段，按精确首板日期归月，绝不使用最高价月份或未知边界的观察起点代替。图中两类证据分列，月度有效分母为1,633；107个起点unknown不进入月份。

{monthly}

在这个选择后队列中，11月179、3月170、2月158段较多，9月90段较少。这些只是覆盖不均和选择条件下的描述计数，**不能推断季节性胜率或全市场正式规律**。年度涨停基数、独立证据覆盖、重复股票、特殊状态和不同年份长度未进行因果或显著性校正；2026年只截至11月6日。无有效样本时月图留空，0只表示当前已纳入队列没有样本。

## 年度有效样本与未知

质量表门槛为默认≥5板，与主图一致。IPO排除和IPO归属unknown来自同年同高度原始队列，在主样本外单列；“边界完整”是基础统计情景下首尾可定位，不等于ST/可交易性或独立证据完整。

{coverage}

原始未知股票日数量不因本研究而改变；本表unknown分别表示IPO归属或首板边界未知，不能与未知股票日混用。API与页面切换≥3/5/7/10时，年度质量列跟随所选门槛，四档年度计数始终同时展示。年度表始终展示十年，年月筛选联动图表和样本列表，证据筛选作用于全部研究汇总。

## 已确认案例复核

- **圣龙股份603178.SH**：非IPO，首板 **2023-09-28**，末次涨停 **2023-10-25**，**14板**；段内正向事件confirmed。9月月图可下钻到该股票；不会因为最高价在其他月份而移出9月。
- **艾艾精工603580.SH**：非IPO，首板 **2024-03-05**，末次涨停 **2024-03-21**，**13板**；段内正向事件confirmed。3月下钻可见。
- 两例历史ST仍按源字段保留unknown，不因非IPO和涨停证据confirmed而变成历史非ST或普通可交易股票。
- 反例：2017年非IPO≥10板有1段601360.SH，但首板边界未知；年度计数保留1，月图不放入其观察起点11月。这用于防止以观察首日冒充精确启动日期。

## 现有UI接入

访问 **http://127.0.0.1:3001/dragons → 启动规律**，主视图为“非IPO高连板 · 启动月份研究”。原独立证据季节性研究保留在下方展开区，历史连板排行、分类切换与原始事实均保留。

支持年份、高度、正向事件证据和启动月份筛选；点击月份柱联动下方股票表，股票表显示精确首板日期或unknown、完整最高高度或≥最低高度、ST、可交易性unknown、可能片段及启动K线链接。选定月份后只出现该月可信起点；清空月份恢复含起点unknown的年度观察列表。

浏览器实际验证：桌面点击2023年9月柱，列表联动圣龙14板并进入2023-09-28启动K线；390×844移动端点击2024年3月柱，联动艾艾13板并进入2024-03-05启动K线。移动端页面宽度与scrollWidth均390，无页面横向溢出，宽表格在容器内滚动。[桌面月图](non_ipo_dragon_study/desktop_chart.png)、[移动月图](non_ipo_dragon_study/mobile_chart.png)。

API：`/api/history-limits/non-ipo-study?height=5&cohort=all`。`cohort=confirmed/candidate`用于分开查看；不会用confirmed子段重新切割原周期来增加主样本周期数。

## 测试与复现

自动测试 **294 passed，9条已有依赖警告**；前端生产构建和TypeScript通过。新增回归检查117,611来源段分类守恒、确认/候选分解、可信起点月图总和、unknown不伪造月份、空证据图留空及圣龙/艾艾的准确启动日期与高度。

```sh
PYTHONPATH=backend .venv/bin/python scripts/report_non_ipo_dragon_study.py
PYTHONPATH=backend .venv/bin/pytest backend/tests scripts -q --disable-warnings
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build --prefix frontend
```

版本 `{VERSION}`；来源统计 `{s['source']['statistics']}`、分类 `{s['source']['classification']}`，源路径 `{s['source']['snapshot']}`。[快照锁与代码校验和](non_ipo_dragon_study/snapshot_lock.json)、[年度CSV](non_ipo_dragon_study/annual.csv)、[月度CSV](non_ipo_dragon_study/launch_months_ge5.csv)、[完整默认查询结果](non_ipo_dragon_study/result.json)。图表与页面使用相同只读研究查询，CSV/JSON未填造缺失字段。

未下载或新增数据源、未调用付费接口、未训练模型、未修改原始行情或底层算法、未执行GitHub Push。
'''
Path('docs/non_ipo_dragon_study.md').write_text(text)
print(annual);print(coverage)
