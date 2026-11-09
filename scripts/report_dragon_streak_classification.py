"""Publish classification audit from the frozen local derived snapshot."""
import json,hashlib,shutil
from pathlib import Path
from app.statistics.history_limit_queries import ROOT,dataset
OUT=Path('docs/dragon_streak_classification');OUT.mkdir(exist_ok=True)
labels={'ipo_initial':'IPO初始','non_ipo':'非IPO','unknown':'IPO归属unknown','turnover':'换手候选（非IPO子集）'}
def table(headers,rows):
 return '| '+' | '.join(headers)+' |\n|'+'|'.join(['---']*len(headers))+'|\n'+''.join('| '+' | '.join(str(x) for x in r)+' |\n' for r in rows)
with dataset() as (db,meta):
 s=meta['classification'];manifest=json.loads((ROOT/'classification_manifest.json').read_text());snapshot=ROOT/manifest['dataset']
 db.execute('CREATE TEMP VIEW enriched AS SELECT c.*,k.* EXCLUDE(stock_code,run_start) FROM cycles c JOIN cycle_classes k USING(stock_code,run_start)')
 for name,sql in {
  'yearly':"SELECT year(run_start) AS year,ipo_origin,count(*) cycles,count(*) FILTER(WHERE height>=3) ge3,count(*) FILTER(WHERE height>=5) ge5,count(*) FILTER(WHERE height>=7) ge7,count(*) FILTER(WHERE height>=10) ge10,count(*) FILTER(WHERE final_height IS NOT NULL) exact_cycles,count(*) FILTER(WHERE final_height IS NULL) incomplete_cycles,count(*) FILTER(WHERE status='confirmed') confirmed_cycles,count(*) FILTER(WHERE st_status='unknown') st_unknown_cycles,count(*) FILTER(WHERE cycle_identity_quality='possible_fragment') possible_fragments FROM enriched GROUP BY ALL ORDER BY 1,2",
  'all_classified_cycles': 'SELECT * FROM enriched ORDER BY stock_code,run_start',
  'possible_fragments': "SELECT * FROM enriched WHERE cycle_identity_quality='possible_fragment' ORDER BY stock_code,run_start"
 }.items():
  suffix='.csv.gz' if name=='all_classified_cycles' else '.csv'
  compression=",COMPRESSION GZIP" if suffix.endswith('.gz') else ''
  db.execute(f"COPY ({sql}) TO '{OUT}/{name}{suffix}' (HEADER,DELIMITER ','{compression})")
 for path in snapshot.glob('audit_*_ge10.csv'):shutil.copyfile(path,OUT/path.name)
 grouped=s['yearly'];trs=[dict(r,ipo_origin='turnover') for r in s['turnover']]
 count_table=table(['年份','类型','≥3','≥5','≥7','≥10'],[[r['year'],labels[r['ipo_origin']],*[r['ge'+str(h)] for h in [3,5,7,10]]] for r in sorted(grouped+trs,key=lambda r:(r['year'],r['ipo_origin']))])
 quality_table=table(['年份','类型','唯一观察段（含1/2板）','两侧边界完整','边界不完整','全段confirmed','ST未知段','可能片段'],[[r['year'],labels[r['ipo_origin']],r['cycles'],r['exact_cycles'],r['incomplete_cycles'],r['confirmed_cycles'],r['st_unknown_cycles'],r['possible_fragments']] for r in grouped])
 focus=[]
 for y in [2017,2018,2020,2023,2024,2026]:
  a={r['ipo_origin']:r for r in grouped if r['year']==y};focus.append([y,sum(x['ge10'] for x in a.values()),*[a.get(k,{}).get('ge10',0) for k in ['ipo_initial','non_ipo','unknown']]])
 focus_table=table(['年份','全量≥10','IPO初始','非IPO','IPO归属unknown'],focus)
 totals=db.execute("SELECT count(*),count(*) FILTER(WHERE final_height IS NOT NULL),count(*) FILTER(WHERE final_height IS NULL),count(*) FILTER(WHERE cycle_identity_quality='possible_fragment') FROM enriched").fetchone()
 other=db.execute("SELECT stock_code,listing_date,run_start,height,ipo_origin,ohlc_shape FROM enriched WHERE year(run_start)=2017 AND height>=10 AND ipo_origin<>'ipo_initial' ORDER BY stock_code").fetchall()
 other_table=table(['股票','上市日','观察起日','最低高度','IPO归属','形态'],other)
 files=['backend/app/statistics/streak_classification.py','backend/app/statistics/history_limit_queries.py','backend/app/api/history_limits.py','scripts/classify_dragon_streaks.py','scripts/report_dragon_streak_classification.py','frontend/src/components/HistoryLimits.tsx']
 (OUT/'snapshot_lock.json').write_text(json.dumps(dict(classification_version=s['version'],classification_snapshot=str(snapshot),statistics_snapshot=s['source_snapshot'],unified_snapshot=s['unified_snapshot'],sha256={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files},classification_sha256=hashlib.sha256((snapshot/'classifications.parquet').read_bytes()).hexdigest()),indent=2)+'\n')
 text=f'''# 历史连板周期分类审计

## 结论

2017年183段≥10板里，**179段（97.81%）属于上市初始限幅序列，1段为非IPO，3段IPO归属unknown**。它不是183个可直接比较的普通换手龙头周期。179段IPO初始中，84段全程一字OHLC，95段为一字与有价格区间的混合形态；不能把全部179段都称为“全程一字板”。

本次基于现有v2派生周期及正向股票日，只补充历史上市日、日历定位和这些正向日的可信OHLC；**没有重新识别全部原始日线、下载行情或改变原统计快照**。现有全量排行和各年3/5/7/10板总数保持不变。

## 周期唯一性与重复风险

全库 **{totals[0]:,}** 段，按 `(stock_code, run_start)` 唯一；重复键 **0**，重叠段 **0**，正向股票日期重复 **0**。每个正向交易日只属于一个连续段，段高度之和等于正向股票日数 **{s['positive_days']:,}**。不是按股票代码粗去重，同一股票不同起点保留独立观察记录；跨年段不按年份重复切段，按观察起日归年/月。

其中基础统计两侧边界完整 **{totals[1]:,}** 段，边界不完整 **{totals[2]:,}** 段。**{totals[3]:,}** 段与同股前一段之间没有已观察非涨停日，标记 `possible_fragment`，不能排除属于同一真实周期的不同片段。因此“117,611个唯一观察段”不等于“117,611个已证明彼此独立、完整的经济周期”。保留原事实，不跨unknown连接，也不静默删除片段。

`cycle_identity_quality`：`separated_start`表示起点有已观察非涨停分隔；`start_unknown`表示起点边界未确定；`possible_fragment`表示前段至本段之间缺少非涨停分隔。该标记单独展示，精确经济周期数量在缺口区间仍unknown。[可能片段逐段表](dragon_streak_classification/possible_fragments.csv)。

## 分类口径

分类是独立字段，IPO归属与ST、涨停证据等级、周期边界、OHLC形态互不替代。

- **IPO初始 `ipo_initial`**：既有上市日与交易所日历可定位，观察段从历史制度下首个常规限幅交易日开始；旧制一般从上市第2交易日起，若特殊首日已有可信涨停事件且连续段从首日开始，则保留该已确认首日。注册制科创板、2020-08-24后上市创业板、2023-04-10后上市主板的前5日无涨跌幅限制，锚点为第6交易日；前5日不能算成涨停连板。
- **非IPO `non_ipo`**：起点不在上述初始锚点，且上市后、当前起点以前存在已判非涨停日，已与IPO初始序列分隔。只确认IPO归属，不证明普通非ST、复牌或可交易状态。
- **归属unknown**：上市日/日历不可定位，或观察从初始锚点以后才开始，前缀缺失且无可用非涨停分隔。不能仅因“上市较近”就划入IPO，也不能自动归入非IPO。
- **换手连板候选**是非IPO的子集：段内所有正向日OHLC可信、每天high>low且成交量>0。不能证明换手率充分、封板可买、盘中可成交或非一字T板交易机会。混合一字段、OHLC缺失、成交量未知均不进入此子集。此处“候选”明确不是已验证换手策略样本。

旧IPO首日缺乏发行价/可信事件时，原unknown保持不变；把其第2日起的段归入IPO初始，**只是上市制度位置的上下文分类**，不补第1板、不补首板日期、不将原不完整边界改成完整。`classification_quality=catalogue_calendar_context`，不是独立事件验收通过。每个原有候选仍是 `reconstructed_candidate`。

一字板只使用已确认质量的OHLC相等形态（开高低收一致），真实涨停事件可支持涨停但不能在缺OHLC时证明全天一字。段内形态分为全部一字、全部有价格区间、混合和unknown。ST未知始终保留，不用当前目录简称回推历史ST。

规则日期沿用[已有规则来源及边界](historical_limit_rule_sources.md)，未新增数据源。原source冲突、特殊日unknown、首尾边界均沿用v2，不作新的行情推断。

## 2017年183段的逐段核验

IPO初始179段都属于原重建候选，首板边界未独立确认；历史ST未知标记保留。全程一字84段，混合95段。余下4段如下：

{other_table}

002836、300585上市于2016-12-29，603929上市于2016-12-30；2017-01-04才开始可用连续正向段，分别已经是上市第4、4、3交易日。缺少IPO初始前缀，故保留unknown，不能把它们强行补成完整IPO周期。601360的2012年上市日和较早非涨停记录支持“非IPO”；其17板为全一字OHLC，起点特殊状态仍不完整，**不称作已确认普通换手连板**。本口径2017年≥10板换手候选 **0**，并不证明市场历史上没有此类股票。

183行明细包含代码、上市日期、上市交易序号、起止、最低高度、OHLC一字/区间日数、分类原因、ST、证据状态、边界和可能片段标记：[2017逐段审计CSV](dragon_streak_classification/audit_2017_ge10.csv)。

## 指定年度同口径检查

{focus_table}

[2018明细](dragon_streak_classification/audit_2018_ge10.csv) · [2020明细](dragon_streak_classification/audit_2020_ge10.csv) · [2023明细](dragon_streak_classification/audit_2023_ge10.csv) · [2024明细](dragon_streak_classification/audit_2024_ge10.csv) · [2026明细](dragon_streak_classification/audit_2026_ge10.csv)。所有年度使用同一分类函数，差异仅来自历史IPO制度和已有证据覆盖，2026截至11月6日。

## 分类型年度3/5/7/10板数量

前三类互斥，逐年相加严格等于原全量数量；换手候选为非IPO子集，不可再相加。每个格子是唯一观察段数，门槛累计，≥10板同时满足≥3/5/7。

{count_table}

## 精确数量、边界与可信度

下表唯一段数量含1/2板，等于来源全部周期数量；“完整”仅表示原基础情景下两侧边界可定位，不等于涨停独立证据或普通非ST资格。unknown归属不代表该段涨停未知，ST未知也不等于IPO归属未知。

{quality_table}

全量可机读分类：[年度CSV](dragon_streak_classification/yearly.csv)、[全部周期逐段CSV](dragon_streak_classification/all_classified_cycles.csv.gz)。原日线unknown股票日数量不因本次分类而变化；本表unknown是IPO归属未知，不能与未知股票日混为一个分母。

## 页面与测试

现有 **http://127.0.0.1:3001/dragons → 历史连板排行** 新增“全部 / 排除IPO初始连板（保留unknown） / 换手连板候选”切换，结合年月、高度、confirmed范围使用；表格显示IPO归属、上市日期、OHLC形态、可能片段及ST。底部“分类型年度3/5/7/10板、完整边界与可信度”展示年度矩阵。全量排行榜与严格研究页面保留。

confirmed模式按原连续confirmed子段计数，分类继承其所属原观察段，不因子段起点晚于IPO日期而错误变成非IPO。每日图及期间质量摘要仍描述全量股票日，分类切换只筛选周期列表；全量年度摘要明确不受分类筛选影响。

浏览器实测：桌面2017年≥10板“全部183→排除IPO4→换手候选0”；移动390×844下2024年3月≥3板换手候选29段，含艾艾13板，点击进入对应65个交易日K线，页面无横向溢出。[桌面截图](dragon_streak_classification/desktop.png)、[移动截图](dragon_streak_classification/mobile.png)。

自动测试 **291 passed，9条已有依赖警告**；前端构建及TypeScript通过。回归验证历史IPO锚点、无涨跌幅限制日排除、未知前缀不强分、OHLC缺失、正成交量、代码日期去重、逐年分类计数守恒，以及2017年183→排除IPO4→换手候选0的筛选结果。

## 复现与限制

版本 `{s['version']}`，统计快照 `{s['source_snapshot']}`，分类快照 `{snapshot}`。分类文件独立保存，API校验其父统计快照匹配才加载；不会回写原周期和原行情。复现：

```sh
PYTHONPATH=backend .venv/bin/python scripts/classify_dragon_streaks.py
PYTHONPATH=backend .venv/bin/python scripts/report_dragon_streak_classification.py
PYTHONPATH=backend .venv/bin/pytest backend/tests scripts -q --disable-warnings
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build --prefix frontend
```

[版本与代码校验和](dragon_streak_classification/snapshot_lock.json)。未调用付费接口、未下载行情、未训练模型、未重构数据库、未执行GitHub Push。上市目录覆盖、源数据特殊状态和候选证据限制仍存在，分类是研究分层，不能当作已经发现可交易规律。
'''
 Path('docs/dragon_streak_classification.md').write_text(text)
 print(focus_table);print('unique / exact / incomplete / possible fragments',totals)
