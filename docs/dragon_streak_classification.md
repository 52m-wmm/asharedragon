# 历史连板周期分类审计

## 结论

2017年183段≥10板里，**179段（97.81%）属于上市初始限幅序列，1段为非IPO，3段IPO归属unknown**。它不是183个可直接比较的普通换手龙头周期。179段IPO初始中，84段全程一字OHLC，95段为一字与有价格区间的混合形态；不能把全部179段都称为“全程一字板”。

本次基于现有v2派生周期及正向股票日，只补充历史上市日、日历定位和这些正向日的可信OHLC；**没有重新识别全部原始日线、下载行情或改变原统计快照**。现有全量排行和各年3/5/7/10板总数保持不变。

## 周期唯一性与重复风险

全库 **117,611** 段，按 `(stock_code, run_start)` 唯一；重复键 **0**，重叠段 **0**，正向股票日期重复 **0**。每个正向交易日只属于一个连续段，段高度之和等于正向股票日数 **160,953**。不是按股票代码粗去重，同一股票不同起点保留独立观察记录；跨年段不按年份重复切段，按观察起日归年/月。

其中基础统计两侧边界完整 **114,347** 段，边界不完整 **3,264** 段。**250** 段与同股前一段之间没有已观察非涨停日，标记 `possible_fragment`，不能排除属于同一真实周期的不同片段。因此“117,611个唯一观察段”不等于“117,611个已证明彼此独立、完整的经济周期”。保留原事实，不跨unknown连接，也不静默删除片段。

`cycle_identity_quality`：`separated_start`表示起点有已观察非涨停分隔；`start_unknown`表示起点边界未确定；`possible_fragment`表示前段至本段之间缺少非涨停分隔。该标记单独展示，精确经济周期数量在缺口区间仍unknown。可能片段逐段表（local-only artifact: `dragon_streak_classification/possible_fragments.csv`）。

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


> Public release: stock-level evidence tables are retained locally and are not redistributed. Aggregate results and methods are preserved below.



002836、300585上市于2016-12-29，603929上市于2016-12-30；2017-01-04才开始可用连续正向段，分别已经是上市第4、4、3交易日。缺少IPO初始前缀，故保留unknown，不能把它们强行补成完整IPO周期。601360的2012年上市日和较早非涨停记录支持“非IPO”；其17板为全一字OHLC，起点特殊状态仍不完整，**不称作已确认普通换手连板**。本口径2017年≥10板换手候选 **0**，并不证明市场历史上没有此类股票。

183行明细包含代码、上市日期、上市交易序号、起止、最低高度、OHLC一字/区间日数、分类原因、ST、证据状态、边界和可能片段标记：2017逐段审计CSV（local-only artifact: `dragon_streak_classification/audit_2017_ge10.csv`）。

## 指定年度同口径检查

| 年份 | 全量≥10 | IPO初始 | 非IPO | IPO归属unknown |
|---|---|---|---|---|
| 2017 | 183 | 179 | 1 | 3 |
| 2018 | 30 | 27 | 3 | 0 |
| 2020 | 51 | 42 | 9 | 0 |
| 2023 | 7 | 1 | 6 | 0 |
| 2024 | 24 | 0 | 24 | 0 |
| 2026 | 1 | 0 | 1 | 0 |


2018明细（local-only artifact: `dragon_streak_classification/audit_2018_ge10.csv`） · 2020明细（local-only artifact: `dragon_streak_classification/audit_2020_ge10.csv`） · 2023明细（local-only artifact: `dragon_streak_classification/audit_2023_ge10.csv`） · 2024明细（local-only artifact: `dragon_streak_classification/audit_2024_ge10.csv`） · 2026明细（local-only artifact: `dragon_streak_classification/audit_2026_ge10.csv`）。所有年度使用同一分类函数，差异仅来自历史IPO制度和已有证据覆盖，2026截至11月6日。

## 分类型年度3/5/7/10板数量

前三类互斥，逐年相加严格等于原全量数量；换手候选为非IPO子集，不可再相加。每个格子是唯一观察段数，门槛累计，≥10板同时满足≥3/5/7。

| 年份 | 类型 | ≥3 | ≥5 | ≥7 | ≥10 |
|---|---|---|---|---|---|
| 2017 | IPO初始 | 421 | 371 | 305 | 179 |
| 2017 | 非IPO | 162 | 37 | 2 | 1 |
| 2017 | 换手候选（非IPO子集） | 77 | 4 | 0 | 0 |
| 2017 | IPO归属unknown | 27 | 17 | 7 | 3 |
| 2018 | IPO初始 | 96 | 79 | 58 | 27 |
| 2018 | 非IPO | 499 | 84 | 16 | 3 |
| 2018 | 换手候选（非IPO子集） | 271 | 26 | 2 | 0 |
| 2018 | IPO归属unknown | 1 | 0 | 0 | 0 |
| 2019 | IPO初始 | 110 | 88 | 62 | 29 |
| 2019 | 非IPO | 812 | 186 | 39 | 7 |
| 2019 | 换手候选（非IPO子集） | 406 | 50 | 2 | 0 |
| 2019 | IPO归属unknown | 0 | 0 | 0 | 0 |
| 2020 | IPO初始 | 142 | 112 | 79 | 42 |
| 2020 | 非IPO | 951 | 211 | 52 | 9 |
| 2020 | 换手候选（非IPO子集） | 466 | 46 | 7 | 0 |
| 2020 | IPO归属unknown | 0 | 0 | 0 | 0 |
| 2021 | IPO初始 | 81 | 52 | 28 | 20 |
| 2021 | 非IPO | 1021 | 228 | 71 | 13 |
| 2021 | 换手候选（非IPO子集） | 552 | 48 | 5 | 1 |
| 2021 | IPO归属unknown | 0 | 0 | 0 | 0 |
| 2022 | IPO初始 | 45 | 26 | 18 | 9 |
| 2022 | 非IPO | 922 | 220 | 62 | 10 |
| 2022 | 换手候选（非IPO子集） | 500 | 64 | 7 | 1 |
| 2022 | IPO归属unknown | 0 | 0 | 0 | 0 |
| 2023 | IPO初始 | 13 | 7 | 2 | 1 |
| 2023 | 非IPO | 495 | 120 | 37 | 6 |
| 2023 | 换手候选（非IPO子集） | 243 | 29 | 2 | 0 |
| 2023 | IPO归属unknown | 1 | 0 | 0 | 0 |
| 2024 | IPO初始 | 0 | 0 | 0 | 0 |
| 2024 | 非IPO | 1161 | 311 | 107 | 24 |
| 2024 | 换手候选（非IPO子集） | 529 | 56 | 9 | 1 |
| 2024 | IPO归属unknown | 1 | 0 | 0 | 0 |
| 2025 | IPO初始 | 0 | 0 | 0 | 0 |
| 2025 | 非IPO | 967 | 228 | 59 | 8 |
| 2025 | 换手候选（非IPO子集） | 436 | 48 | 4 | 1 |
| 2026 | IPO初始 | 0 | 0 | 0 | 0 |
| 2026 | 非IPO | 640 | 115 | 27 | 1 |
| 2026 | 换手候选（非IPO子集） | 332 | 31 | 3 | 0 |


## 精确数量、边界与可信度

下表唯一段数量含1/2板，等于来源全部周期数量；“完整”仅表示原基础情景下两侧边界可定位，不等于涨停独立证据或普通非ST资格。unknown归属不代表该段涨停未知，ST未知也不等于IPO归属未知。

| 年份 | 类型 | 唯一观察段（含1/2板） | 两侧边界完整 | 边界不完整 | 全段confirmed | ST未知段 | 可能片段 |
|---|---|---|---|---|---|---|---|
| 2017 | IPO初始 | 434 | 0 | 434 | 0 | 434 | 0 |
| 2017 | 非IPO | 4637 | 4418 | 219 | 0 | 4637 | 19 |
| 2017 | IPO归属unknown | 109 | 1 | 108 | 0 | 109 | 18 |
| 2018 | IPO初始 | 104 | 0 | 104 | 0 | 104 | 0 |
| 2018 | 非IPO | 8061 | 7799 | 262 | 0 | 8061 | 20 |
| 2018 | IPO归属unknown | 7 | 3 | 4 | 0 | 7 | 3 |
| 2019 | IPO初始 | 125 | 0 | 125 | 0 | 125 | 0 |
| 2019 | 非IPO | 11203 | 10991 | 212 | 0 | 11203 | 19 |
| 2019 | IPO归属unknown | 2 | 2 | 0 | 0 | 2 | 0 |
| 2020 | IPO初始 | 179 | 0 | 179 | 84 | 179 | 0 |
| 2020 | 非IPO | 13722 | 13533 | 189 | 7599 | 13722 | 21 |
| 2020 | IPO归属unknown | 1 | 1 | 0 | 1 | 1 | 0 |
| 2021 | IPO初始 | 115 | 0 | 115 | 0 | 115 | 0 |
| 2021 | 非IPO | 15253 | 15028 | 225 | 0 | 15244 | 30 |
| 2021 | IPO归属unknown | 7 | 7 | 0 | 0 | 7 | 0 |
| 2022 | IPO初始 | 65 | 0 | 65 | 0 | 65 | 0 |
| 2022 | 非IPO | 13546 | 13317 | 229 | 0 | 13544 | 31 |
| 2022 | IPO归属unknown | 2 | 2 | 0 | 0 | 2 | 0 |
| 2023 | IPO初始 | 24 | 0 | 24 | 0 | 24 | 0 |
| 2023 | 非IPO | 7987 | 7845 | 142 | 1 | 7973 | 18 |
| 2023 | IPO归属unknown | 6 | 6 | 0 | 0 | 6 | 0 |
| 2024 | IPO初始 | 5 | 0 | 5 | 0 | 5 | 0 |
| 2024 | 非IPO | 15292 | 15118 | 174 | 1 | 15224 | 11 |
| 2024 | IPO归属unknown | 4 | 3 | 1 | 0 | 3 | 0 |
| 2025 | IPO初始 | 7 | 0 | 7 | 0 | 7 | 0 |
| 2025 | 非IPO | 13681 | 13431 | 250 | 0 | 13676 | 35 |
| 2026 | IPO初始 | 3 | 0 | 3 | 0 | 3 | 0 |
| 2026 | 非IPO | 13030 | 12842 | 188 | 499 | 12516 | 25 |


全量可机读分类：年度CSV（local-only artifact: `dragon_streak_classification/yearly.csv`）、全部周期逐段CSV（local-only artifact: `dragon_streak_classification/all_classified_cycles.csv.gz`）。原日线unknown股票日数量不因本次分类而变化；本表unknown是IPO归属未知，不能与未知股票日混为一个分母。

## 页面与测试

现有 **http://127.0.0.1:3001/dragons → 历史连板排行** 新增“全部 / 排除IPO初始连板（保留unknown） / 换手连板候选”切换，结合年月、高度、confirmed范围使用；表格显示IPO归属、上市日期、OHLC形态、可能片段及ST。底部“分类型年度3/5/7/10板、完整边界与可信度”展示年度矩阵。全量排行榜与严格研究页面保留。

confirmed模式按原连续confirmed子段计数，分类继承其所属原观察段，不因子段起点晚于IPO日期而错误变成非IPO。每日图及期间质量摘要仍描述全量股票日，分类切换只筛选周期列表；全量年度摘要明确不受分类筛选影响。

浏览器实测：桌面2017年≥10板“全部183→排除IPO4→换手候选0”；移动390×844下2024年3月≥3板换手候选29段，含艾艾13板，点击进入对应65个交易日K线，页面无横向溢出。桌面截图（local-only artifact: `dragon_streak_classification/desktop.png`）、移动截图（local-only artifact: `dragon_streak_classification/mobile.png`）。

自动测试 **291 passed，9条已有依赖警告**；前端构建及TypeScript通过。回归验证历史IPO锚点、无涨跌幅限制日排除、未知前缀不强分、OHLC缺失、正成交量、代码日期去重、逐年分类计数守恒，以及2017年183→排除IPO4→换手候选0的筛选结果。

## 复现与限制

版本 `streak-classification-v1`，统计快照 `~/Datasets/asharedragon/statistics/history_limits/snapshots/d950e6433af9a4e4c998`，分类快照 `~/Datasets/asharedragon/statistics/history_limits/classifications/1ec16f50c57295dfebcc`。分类文件独立保存，API校验其父统计快照匹配才加载；不会回写原周期和原行情。复现：

```sh
PYTHONPATH=backend .venv/bin/python scripts/classify_dragon_streaks.py
PYTHONPATH=backend .venv/bin/python scripts/report_dragon_streak_classification.py
PYTHONPATH=backend .venv/bin/pytest backend/tests scripts -q --disable-warnings
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build --prefix frontend
```

版本与代码校验和（local-only artifact: `dragon_streak_classification/snapshot_lock.json`）。未调用付费接口、未下载行情、未训练模型、未重构数据库、未执行GitHub Push。上市目录覆盖、源数据特殊状态和候选证据限制仍存在，分类是研究分层，不能当作已经发现可交易规律。
