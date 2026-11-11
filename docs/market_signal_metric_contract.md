# 市场广度与正负信号：E0 指标契约及实际差异

审计日期：2026-11-10。状态：**E0 报告待用户验收；未进入 E1—E5，未宣告 A0 通过。**

依据：Notion《AShareDragon 改造 Spec v1.1》（内部规格），读取版本最后修改时间为 `2026-11-10T10:35:12.504Z`。

本文区分三类内容：**实测**是本轮本地只读查询；**现状**是现有代码行为；**契约**是后续实现应遵循的要求，不代表功能已经完成。本轮只新增本文，不实现指标、不重建快照、不补采数据、不运行迁移、不训练模型。

## 1. 工作区、提交授权与快照基线

### 1.1 工作区保护

- 开始 HEAD：`369b57b831892618fd9e7423cb6d948b2b839dc5`，分支 `main`。
- 开始状态：25 个 tracked 文件修改，17 个 untracked 顶层条目；展开目录后为 18 个新文件，总共 43 个变更文件。
- tracked diff 为 `+295/-91`，新文件为 `+634`，合计 **+929/-91**。
- 用户随后明确允许先提交右上角显示的这批 `+929/-91`；已原样提交为 **`92addd24bdd4fe10eab1a38fa9a2fe731a38869e`**，提交信息 `Preserve data inventory, historical rule and research UI updates`。这是保存既有工作的单独授权，未包含本报告，没有 push。
- 提交前，对 `git ls-files --cached --others --exclude-standard` 所列全部现存文件记录 SHA-256，并在暂存前逐文件核对一致。没有 stash、reset、clean、checkout 覆盖或修改既有文件。
- 提交后工作区曾为空；报告交付时预期唯一新增项为本文。下方附录列出本轮开始时的完整变更清单。
- 原始审计清单暂存于 `/tmp/asharedragon-e0-before.json`；可持久复核的既有变更内容与清单由上述 commit 保存，不能依赖临时文件长期存在。

### 1.2 当前本地数据

| 资产 | 实际标识 / 结果 |
| --- | --- |
| 数据根目录 | `~/Datasets/asharedragon` |
| 统一研究快照 U | `research/unified/snapshots/20261110T091616883852Z` |
| U 版本 / 截止日 | `research-final-v1` / `2026-11-10` |
| U 构建时 PostgreSQL snapshot | `21494:21494:` |
| U manifest SHA-256 | `5c817be97ae11c8fed36208e96f92ee3400b486cae58322b3d69f576ca031ccc` |
| 历史统计快照 H | `statistics/history_limits/snapshots/e0860bd1d2439accd838` |
| H 版本 / 构建时间 | `historical-limit-statistics-v2.2` / `2026-11-10T09:17:34.458268+00:00` |
| H source_snapshot | 指向上述 U，当前两层快照匹配 |
| H manifest SHA-256 | `3fc1c83b046e9687db0c16599ad6d4ee08ec50d0756705a1c9c5ff01e262b55f` |
| U 行数 / 唯一 stock_code+trade_date | 均为 **10,354,849**，重复键 0；2017-01-03 至 2026-11-10 |
| 沪深 A 股范围内观察行数 | **10,354,823**；另有 26 行北交所事件/证据键，不应进入本期范围 |
| verified 价格行数 | **10,354,398**，不等于合法收益参考价已全部核验，也不等于严格非 ST |
| feature_pit_verified=true | **0** |

读取统一 DuckDB 使用 `read_only=True`；临时关联和聚合使用内存 DuckDB。PostgreSQL 仅在 `REPEATABLE READ, READ ONLY` 事务中执行 SELECT，本轮读取事务标识 `21583:21583:`，近期情绪数值与 U 中缓存一致。未调用构建、采集、修复或 recalculate 函数。

U manifest 中列出的 **162 个文件全部实际重算 SHA-256，与清单一致**。H 关键文件哈希：

| 文件 | SHA-256 |
| --- | --- |
| `summary.json` | `c47ee476c5d4475d557c1344a3e232911e7400a047d355753fa43a69099c6324` |
| `daily.parquet` | `09c7adf80e7a7958cd66c309a93f0e29fdd4d479324ee3ce0797f6a27b2f7106` |
| `days.parquet` | `70d54fcca8267ca929942b9729f9e75006af743c80dee65033538e50b7ce4b6e` |
| `positives.parquet` | `ca4d0f41aa3f6ec953adc25774ba07878c028f454fa74c08fe5198f608b178c5` |
| `cycles.parquet` | `a0aef7a09182deb65e218febacd76bae9dfc962a6c5249773be67e4d605ba38d` |
| `recovered_references.parquet` | `7e6e05228ff5b02ba0189b6f8a29b72f18a770b947fa72ca5330eaa5b0740e0c` |

旧 `docs/history_limits_v2/snapshot_lock.json` 指向 `d950e6433af9a4e4c998` / `20261108T052941763243Z`，不是当前基线。旧快照锁与当前代码哈希不可混用；本轮未改它。

## 2. 实际来源与现有代码差异

| 位置 | 实际职责 | 与 v1.1 的差异 |
| --- | --- | --- |
| `backend/app/research/unified.py` | 按 manifest 只读打开 U 的 `research.duckdb` | 可冻结读取；不能仅依赖会变化的 manifest 名称 |
| `scripts/finalize_research.py` | OHLC 整组选择：购买数据 > 扩展日线 > BaoStock > AKShare；按日期关联身份和事件 | `selected_events` 会跨来源择优、按 up/down/broken 排序，不能直接作为同源完整股票池；PIT 标记全部 false |
| `backend/app/providers/normalize.py:daily_ak` | 同响应收盘减涨跌额恢复前收盘价，校验正值、分币和百分比一致性 | 不补上一行 close；身份字段不会因此得到核实 |
| `backend/app/statistics/reference_recovery.py` | 扩展日线 raw close-change，百分比误差容限 0.0051 个百分点 | `extract()` 会写文件，本轮未调用；复核现有恢复文件与匹配条件 |
| `scripts/build_history_limits.py` | OHLC 匹配后仅对原 pre_close=NULL 恢复；构建 days/positives/daily | expected_stock_days 是目录存续股票数，未扣停牌；覆盖分子不是收益有效数；比率被 least(...,1) 截断 |
| `backend/app/statistics/history_limits.py` | confirmed / reconstructed_candidate / unknown，按交易日连续性重建高度 | 事件 confirmed 不要求 is_st=false；height=1 不代表首板边界已确认；有退市前 60 天等事后生命周期判断，不可直接当 PIT 特征 |
| `backend/app/analytics/metrics.py:sentiment` | 计数、炸板率及两种股票级晋级率 | 已做代码交集，但不校验身份、停牌、行情、边界、池互斥性；未在今日池出现直接算未晋级；前日 cohort 是列表，调用方必须确保去重 |
| `backend/app/collectors/ingest.py:recalculate` | 同 provider，coverage success 后计算；previous_day 取 SSE 日历 | success 是采集成功，不等于严格合资格全集已核实；空响应被记为 missing，不能证明零 |
| `backend/app/services/calendar.py:previous_day` | SSE 日历中严格小于 D 的最大日期 | 没有主动识别日历内部漏日，需要独立日历质量门禁；不能用有行情日期代替 |
| `backend/app/statistics/history_limit_queries.py` | 历史统计 API 数据查询 | intraday_series 混合 days 与各来源 events；broken/down 使用 nullif(count,0)，无法保留“已确认零”；不适合作为正式炸板率的直接分母 |
| `backend/app/api/history_limits.py` | 现有日期/年月查询入口 | 尚无本契约的广度、队列异常和跨日可比门禁 |

来源优先级仅用于选定已发布价格版本，不意味着可任意跨来源拼接分子分母。后续应保留原字段语义，将 `event_confirmed` 与 `strict_non_st_eligible` 分为两维；不要把现有 confirmed 静默改名为严格非 ST。

## 3. 市场广度：分母、价格与身份分开

### 3.1 股票集合与计数契约

代码键保留交易所后缀及前导零。范围为沪市 60/68 前缀 `.SH` 与深市 00/30 前缀 `.SZ` 的 A 股，结合目录板块核验；北交所、B 股、未知板块独立报告，不混入指标。

- `U_listed(t)`：经可验证历史上市/退市区间确认，当日属于沪深 A 股的目标全集。退市日期究竟为最后交易日还是摘牌日必须有来源说明，不能凭第一/最后行情倒推。
- `U_expected(t)`：`U_listed(t)` 中按该数据源的“实际交易日线”口径应有记录的集合；**仅扣除当日有证据的全日停牌**，局部停牌且有有效收盘交易的不自动扣除。身份和交易状态未知不能被自动剔除。
- `observed_count`：目标范围内、当日可观察 stock-day 唯一键数 O，包括仅有事件或状态而无价格的行；另外记录 `price_observed_count`，防止将事件行称作价格样本。
- `price_valid_count`：有有效收盘价、合法同日参考价、无价格冲突且非已确认全日停牌的收益可判断数 V。不能直接使用现有 `price_quality='verified'` 计数替代。
- `price_unknown_count=O−V`；`up+down+flat=V`，`up+down+flat+price_unknown=O`。范围外行另记，不塞进 unknown。
- `st_confirmed_count` / `non_st_confirmed_count` / `st_unknown_count`：分别在 O 与 V 两个基数上输出三分计数及分母，三者之和必须等于其基数。身份冲突归 unknown 并保留 reason。
- 广度允许 ST 与身份未知样本，但必须写明“沪深 A 股可观测价格样本”，不能称严格非 ST。
- 正式 `coverage_ratio=|V∩U_expected|/|U_expected|`，前提是历史全集、停牌与计数口径均有可验证来源，分母非零。否则 **`coverage_ratio=null, expected_universe_count=null, universe_status=unknown`**。可以单列 `known_catalogue_count` 及诊断性目录匹配情况，不能借其冒充正式分母。
- `V/O` 只能叫“观察样本收益可判断比例”，不是全市场覆盖率。分子超分母、价格样本在目录外等异常要暴露，不用 min(...,1) 掩盖。

### 3.2 U(t) 的真实可得性

实测 `stocks` 有 **5,565** 个代码，来源均为 BaoStock：沪主板 1,847、深主板 1,645、创业板 1,453、科创板 620；listing_date 缺失 0，delisting_date 非空 341。这是跨历史的目录，不是任一交易日的全集。

`docs/stock_identity_policy.md` 已约定本地 `research/stocks.parquet` 为研究主口径。这项选择不等于证明目录完整。现存 `data/lifecycle_audit/20261110T075306Z/report.json` 与该目录 SHA 匹配，报告 **5,534 verified、27 conflict、4 missing_evidence、1 missing_local**（含外部新增项，因此总数大于本地目录）。该报告仅核对上市/退市，不核验停牌；暂停上市/摘牌及重新上市边界仍有疑问。本轮引用该既有审计，没有重新访问交易所。

现有 `expected_stock_days` 的真实公式是：交易日历 JOIN 本地目录，条件 `trade_date>=listing_date AND (delisting_date IS NULL OR trade_date<=delisting_date)`。没有停牌扣除，且假设退市日包含在内。因此**历史全市场真实分母当前不可确认**。

2026-11-09 目录存续数 5,224，观察 5,221（其中 3 只已确认停牌），verified 价格 5,218，目录缺 3。3 只停牌为 `000831.SZ`、`002129.SZ`、`002717.SZ`。仅作条件推演，若目录及停牌全集都完整，应有交易数可为 5,221；目前不能证明“只有这 3 只停牌”，故不得发布该正式分母。

2026-11-10 目录存续数仍为 5,224，观察/verified 价格均为 5,217，目录缺 7；不能把缺失都解释为停牌，也不能把 11-09 的停牌状态延续填到 11-10。

### 3.3 三种价格来源的质量分布（实测）

| price_source | 记录数 | 日期范围 | 原 pre_close 有正有限值 | is_st=false / true / NULL |
| --- | ---: | --- | ---: | --- |
| `purchased_usb_0dfebf6ee9bc` | 9,517,685 | 2017-01-03—2026-03-13 | 9,517,685 | 15,444 / 690 / 9,501,551 |
| `akshare_em_unadjusted_20260316_20261106` | 826,278 | 2026-03-16—2026-11-06 | 0 | 45,293 / 2,111 / 778,874 |
| `akshare` | 10,434 | 2026-11-09—2026-11-10 | 10,434 | 0 / 0 / 10,434 |
| `baostock` | 91 | 2023-11-17—2026-11-09 | 91 | 40 / 51 / 0 |
| 无价格来源 | 361 | 2019-12-31—2026-11-06 | 0 | 0 / 0 / 361 |

上表为 U 全部记录的来源分布，包含上述 26 行范围外事件键。BaoStock 91 行中仅 1 行 price_quality=verified，90 行有停牌状态；不能把正 pre_close 当作可交易价格证明。

在沪深范围内，O 总计 10,354,823，身份 false/true/NULL 分别为 **60,777 / 2,852 / 10,291,194**。价格质量与身份质量严重不同，不能用价格覆盖替代身份覆盖。

扩展期已有恢复文件 826,278 条，summary 记录 raw_records=recoverable=joined_references=826,278、rejected=0，原始文件集合摘要 `2e80d0aaa8ccf0694f4597ab0bd22b1a699be0a6fc218e7da5684c31c13a072a`。本轮核验恢复文件 SHA、匹配代码和抽样关联；未重新扫描所有原始 JSON 来独立复算这个 raw 摘要。

恢复契约：close/change 均为有限分币值，`reference=raw_close−raw_change>0`，`abs(raw_change/reference*100−raw_pct)<=0.0051`，代码日期一致，原始 OHLC 与所选 OHLC 各项差小于 `1e-8`，原 pre_close 必须缺失、来源及价格质量匹配。若原参考价非空但非法，不能用 coalesce 悄悄覆盖；标 unknown。禁止 lag(close) 填空。

### 3.4 抽样质量计数，不是 E1 广度产品产出

| 日期 / 数据期 | 沪深 O | verified 价格数 | 原始参考价正且有限的 verified 数 | O 内非 ST / ST / 未知 |
| --- | ---: | ---: | ---: | --- |
| 2017-01-03 / 购买 | 2,832 | 2,832 | 2,832 | 3 / 0 / 2,829 |
| 2020-12-01 / 购买 | 4,070 | 4,070 | 4,070 | 3 / 0 / 4,067 |
| 2026-03-17 / 扩展 | 5,180 | 5,180 | 0；现有恢复后 5,180 | 2 / 1 / 5,177 |
| 2026-11-06 / 扩展 | 5,219 | 5,218 | 0；现有恢复后 5,218 | 0 / 4 / 5,215 |
| 2026-11-09 / 近期 | 5,221 | 5,218 | 5,218 | 2 / 2 / 5,217 |
| 2026-11-10 / 近期 | 5,217 | 5,217 | 5,217 | 0 / 0 / 5,217 |

11-06 原 H observed_rows=5,223，多出的 4 行为范围外北交所键；H daily_rows=5,218。现有 H 的目录比率当日约 99.885%，不能据此断言全市场广度覆盖 99.885%。

上述“正且有限”是必要条件计数，**不宣称购买期所有参考价的除权口径已独立验证**。`docs/historical_daily_import.md` 仍要求核实除权除息参考口径。后续需明确接受哪些来源证据；异常或无法证明参考口径的样本保留 unknown。

同样抽样日期中，按“verified 价格 + 正有限同日/已恢复参考价 + 非已确认停牌”筛选后的**价格有效候选集合**，非 ST / ST / 未知分别为：2017-01-03 `3/0/2829`；2020-12-01 `3/0/4067`；2026-03-17 `2/1/5177`；2026-11-06 `0/4/5214`；2026-11-09 `0/1/5217`；2026-11-10 `0/0/5217`。这与 O 上的身份计数分开；候选集合不能跳过上述参考口径审核就称为最终 V。

## 4. 每项指标的契约

下表 B 表示上述广度范围；L 表示沪深严格非 ST、适用当日板块及限价规则的范围。L 的事件确认、身份确认、边界确认、来源完整性是独立条件，不能只看 status='confirmed'。

| 指标 | 实际字段与来源 | 公式 / 单位 | 可用性与异常 |
| --- | --- | --- | --- |
| 上涨/下跌/平盘 | U.stock_daily 的 close/pre_close/price_source/price_quality/price_conflict/trade_status；H 恢复参考价及其 raw 依据 | B 内有效 `r=close/reference−1`；r>0 / <0 / =0 计家数 | 合法同日参考价、有限正价、价格无冲突、非已确认全日停牌；非法值/缺价为 price_unknown。分币价格用十进制比较，不能把显示四舍五入后的 0% 当平盘 |
| 有效样本 / 无法判断 | 同上 | V；O−V，单位家 | 去重键；冲突不随机取值；所有广度分项守恒 |
| 上涨占比 | 上涨数及 V | up/V；存储比例 [0,1]，显示 % | V=0 返回 null；ST 未知可在 B 内，但披露计数 |
| 收益中位数 | 同一 B 有效样本的 r | median(r)；存储比例，显示 % | 缺失不补 0；空样本 null；不是复权总回报或可投资收益 |
| 正式市场覆盖率 | 历史目录、日历、停牌证据、V | `|V∩U_expected|/|U_expected|`；比例 | 当前真实分母无法确认，null；另列目录诊断数和观察样本数 |
| 身份质量 | U.is_st/st_quality/status_conflict/status_source/historical_status_evidence_ids | 非 ST / ST / unknown 家数，分别按 O、V 基数统计 | 同日、有日期范围的身份依据；当前名称无 ST 不构成历史非 ST；冲突归 unknown |
| 涨停总数 | U.events 中同 provider/endpoint/date 的整批最新成功 up 名单、pg_data_coverage、raw_id；结合身份与规则证据 | L 内唯一收盘涨停代码数；家 | 完整范围可核实才是正式总数。事件 confirmed 但身份不明只可展示供应商观察数/候选；缺采 null；已独立确认空池才为 0 |
| 跌停总数（沿用事实项） | 同源 down 名单、coverage、身份与限价证据 | L 内唯一跌停代码数；家 | 不从“未见记录”推零；11-10 down=missing，必须 null |
| 首板/二板/三板及以上 | vendor consecutive_limit_days 与 H positives 的 height/confirmed_height/first_board_date/exact_start/status/st_status | 严格高度 h=1、h=2、h>=3 的家数 | 高度有效且边界/连续交易日可证明；重建 run_start 不是首板；候选与未知高度单列。三板以上=旧 third_board_count+fourth_plus_board_count，任一未知不能当 0 相加 |
| 一进二 | 昨日严格合格首板固定代码队列与今日同源证据 | P1/N1；比例，见第 5 节 | 任一结果未判定则正式比率 null；前日队列本身未确认则 N1=null，不能把观察到的空子集当 0 |
| 二进三 | 昨日严格合格二板固定代码队列与今日同源证据 | P2/N2；比例，见第 5 节 | 本期 E2/A2 必做；与一进二完全相同的质量门禁 |
| 炸板率 | 同供应商、同日期、同范围、互斥 up 与 broken 整批名单 | `|broken|/(|up|+|broken|)`；比例 | broken 必须触板且收盘未封；open_times>0 不能直接归炸板。需检查重复、冲突、池交集、两池覆盖和身份；分母 0/null 则 null |
| 上一交易日变化 | SSE 实际交易日历、两日冻结指标与质量证据 | 家数 `xD−xPrev`；比率 `100*(rD−rPrev)` pp | 第 6 节门禁；前日缺数据不能跳到更早有值日；不可比返回 null+reason |
| 正/负/中性/未知证据 | 上述可比 delta、metric source、snapshot、规则版本 | 家数/上涨占比/晋级率 delta>0 正面；炸板率 delta>0 负面；0 中性 | 不可比/缺数据为 unknown，不计入正负数量；同日可正负并存；不生成预测概率或综合权重 |

信号记录至少包含 `id,name,direction,value,previous_value,delta,unit,reason,status,source,snapshot_id,rule_version,comparison_status`。数值缺失必须有 reason；事件、来源、覆盖和身份状态不宜压成一个布尔值。

## 5. 晋级固定队列、异常处理与二进三范围

### 5.1 固定分母

对 k=1 和 k=2，Prev 为 D 的上一 SSE 真实交易日：

`Ck(Prev)={在 Prev 已确认严格非 ST 且精确高度=k 的唯一代码}`，`Nk=|Ck|`。保存队列代码、来源、raw_id、日期、身份和边界证据，以及队列版本。**先证明队列本身完整可信，再冻结 N**。

来源缺采、前日身份/边界不明导致完整队列无法确立时，应 `cohort_status=unavailable, fixed_denominator=null`；可另存供应商候选队列大小，但不能称严格分母。如果完整合格队列已确认且确实为空，N=0、正式比率仍为 null，reason=zero_denominator。

固定后，D 日每个队员必须有一个互斥主状态，可另附多个质量原因：

| 状态 | 判定证据 | 是否属于未判定 Q |
| --- | --- | --- |
| promoted | D 精确高度=k+1、合法当日身份/规则、同源事件及连续性均确认 | 否，计入 P |
| confirmed_not_promoted | D 有足够事实证明未达到 k+1，且同范围完整来源/身份/边界无疑点 | 否，计入 F；不在今日池中本身不足以证明 |
| suspended | D 有全日停牌证据 | 是；不能从 N 删除，也不自动算失败 |
| price_missing | 当日行情或必要参考价缺失且无法由充分事件证据确定结果 | 是 |
| identity_unknown | 当日身份未知/冲突，或资格变化尚无已冻结处理规则 | 是 |
| boundary_unknown | 高度、首板边界、连续性、事件冲突或必要覆盖无法确认 | 是；附具体原因，不能掩盖成普通失败 |

建议分类优先级（待验收）：已确认 suspended > identity_unknown > price_missing > boundary_unknown > promoted/confirmed_not_promoted；同时保留全部 reason flags。缺价格但同日直接事件足以证明结果，是否允许独立于价格判定，必须显式记录证据门槛，不能默许。已确认变为 ST 的队员保留 N，暂作 qualification_changed 未判定子原因；不伪称它的身份未知，待确定映射/新增状态策略。

守恒：`N=P+F+Q`。正式比率仅在 N>0、Q=0、队列及来源门禁均通过时为 P/N，否则 null。可选区间 `[P/N,(P+Q)/N]`，标注“固定队列结果未判定区间”，**不是统计置信区间**。队列本身不能确认（N=null）时区间也为 null；不得用供应商候选队列生成严格口径区间。

### 5.2 供应商数值基线重新核验

以下全部是**供应商观测/旧函数口径**，不是 v1.1 严格非 ST 已验收指标。查询 U 的事件并与 PostgreSQL 当前情绪记录交叉核对；up/broken 在 11-06、11-09、11-10 的同源代码交集均为 0，无同批重复代码。

| 日期 | up | broken | 炸板率 | 首板 / 二板 / 三板 / 四板以上 | 一进二交集 | 二进三交集 |
| --- | ---: | ---: | --- | --- | --- | --- |
| 2026-11-06 | 32 | 14 | 14/46=30.4348% | 21 / 4 / 3 / 4 | 4/63=6.3492% | 3/17=17.6471% |
| 2026-11-09 | 54 | 44 | 44/98=44.8980% | 45 / 8 / 0 / 1 | 8/21=38.0952% | 0/4=0% |
| 2026-11-10 | 93 | 19 | 19/112=16.9643% | 73 / 15 / 4 / 1 | **14/45=31.1111%** | **4/8=50%** |

11-10 对比 11-09：涨停 +39、炸板率 **−27.9337 pp**、一进二 **−6.9841 pp**、二进三 **+50 pp**。前三项与 Spec 示例一致，但不得把这个算术复现当作严格可比信号通过。

来源 raw_id：11-06 up/broken=529/530，11-09=538/539，11-10=541/542。对应 up raw SHA-256：

- 529：`2512dcfe331ec9f5d4c15c417eb9d440b59d585867fa22d987b01c0b007eac9e`
- 538：`4a0b8d5f96bf6d01f44a7714b37d13280d088e3b453dd0c791ce0c464643f707`
- 541：`13785eebf90ee973846539f3c52f8624a9ad8eeb881d148291adc07b47e30a16`

11-10 一进二交集 14 只：`000530.SZ,001226.SZ,001379.SZ,002347.SZ,002708.SZ,002931.SZ,600408.SH,600609.SH,600715.SH,600892.SH,603121.SH,603286.SH,603703.SH,603706.SH`。

11-10 二板总数比交集多出的股票是 **002623.SZ**：11-09 不在首板名单，也无该日统一日线；11-10 供应商高度=2，而 H 重建 height=confirmed_height=1、first_board_date=NULL。这是边界未知，不能加入前日固定队列，也不能把它算首板。

11-09 的 **001379.SZ** 供应商声明首板，但 H first_board_date=NULL。供应商高度与本地连续边界证据的优先级需要验收，不能无条件把“14 个供应商交集”称严格确认晋级。

11-10 二进三固定供应商队列（8 只）及匹配证据：

| 前日二板代码 | 今日供应商高度 | 供应商交集结果 | 严格身份检查 |
| --- | ---: | --- | --- |
| 000525.SZ | 无 up 记录 | 未匹配，不能仅凭此确认失败 | unknown |
| 001316.SZ | 无 up 记录 | 未匹配，不能仅凭此确认失败 | unknown |
| 002310.SZ | 3 | 匹配 | unknown |
| 002889.SZ | 无 up 记录 | 未匹配，不能仅凭此确认失败 | unknown |
| 600278.SH | 无 up 记录 | 未匹配，不能仅凭此确认失败 | unknown |
| 600327.SH | 3 | 匹配 | unknown |
| 601188.SH | 3 | 匹配 | unknown |
| 603366.SH | 3 | 匹配 | unknown |

45 只候选首板和 8 只候选二板今日均有 verified 价格，但身份均为 NULL；11-09 全部 54 只 up 和 11-10 全部 93 只 up 的身份也为 NULL。因此严格前日队列无法确立，正式一进二/二进三应 **unavailable、rate=null、strict N=null**，不能把过滤后 0 只误称“已确认空队列”。保留观察队列 45/8 及 14/4 个交集作为诊断证据。93 同理是供应商涨停观测数，不是已完成严格资格核验的全市场总数。

### 5.3 后续 E2/A2 的明确验收范围（本轮未执行）

一进二与二进三都必做，二进三至少包括：

1. 正常队列且身份/边界/覆盖均已确认：代码级复现 P/N；当前 4/8 只提供算术样本，不能算严格正常样本已通过。
2. 一只队员缺行情：N 不变，Q 增加，正式比率 null；无充分替代证据时不得算失败。
3. 一只停牌或身份未知：N 不变、正式比率 null，区间守恒。
4. N=0、前日缺采、未知边界、已确认零事件与未采集分别处理。
5. 历史证据确实不足的日期返回 unavailable+reason 可满足该日期的正确性验收，不要求伪造连续历史数值。

本轮没有新增这些测试或修改业务函数。

## 6. 跨交易日可比性契约

### 6.1 必须同时通过的门禁

1. **交易日**：Prev 只能从已核验 SSE 日历取得；D 与 Prev 都须在同一日历版本覆盖内。日历本身缺口未知也返回 unknown，不能以 max(有值日) 替代。
2. **快照与截止**：一个请求固定 U/H 标识及 H.source_snapshot；两日事件各保留 raw_id、抓取时间和修订选择规则。raw_id 随日期不同是正常现象，不要求同一 raw_id；要求可追溯同一发布版本与同一种收盘后截止口径。
3. **来源与公式**：provider、endpoint 定义、归一化版本、收益参考价规则、板块/限价制度版本兼容；购买到扩展、扩展到近期的来源切换默认不可直接比较，除非补充可审查的等价性证据。
4. **范围与身份**：沪深/北交所、ST 包含政策、严格非 ST 资格、去重键一致。不能用“含 ST 未知的广度”覆盖率去证明“严格非 ST 晋级”质量。
5. **质量**：两个日期的必需字段、来源完整性和各自指标门禁通过；缺采、身份未知、边界未知不因当天其他指标有效而被忽略。
6. **股票集合**：保存两日 Vprev、VD 的交集 I，报告 `|I|/|Vprev|`、`|I|/|VD|`、Jaccard=`|I|/|Vprev∪VD|`，并列出新增/退出的代码及证据原因。新股、退市、停牌、源缺失分别解释，不删掉异常代码凑比例。

不可比则 `delta=null, signal=unknown, comparison_status=unavailable`，附一项或多项 reason。单日数字可以仍作为观察值展示，但不能把两者的算术差赋予正负含义。

### 6.2 从实测分布提出的门槛

本轮只对质量集合做抽样只读关联，未产出广度时间序列。按已有恢复路径匹配参考价后：

| Prev → D | 前日价格/参考价可用候选数 | 当日候选数 | 共同代码 | Jaccard |
| --- | ---: | ---: | ---: | ---: |
| 2026-03-13 → 03-16 | 5,183 | 5,182 | 5,181 | 99.9421% |
| 2026-11-05 → 11-06 | 5,217 | 5,218 | 5,217 | 99.9808% |
| 2026-11-06 → 11-09 | 5,218 | 5,218 | 5,218 | 100% |
| 2026-11-09 → 11-10 | 5,218 | 5,217 | 5,216 | 99.9425% |

这些是正价/参考价和 price_quality 的候选集合，仍需合法参考口径门禁。未接入恢复文件时扩展期 pre_close 全 NULL，11-06 候选数会错误降为 0。

**建议冻结的首版门槛是“未解释的样本差异数=0、未解决的必需质量冲突数=0”这一证据门禁，而非 95%/98% 数值阈值。** 理由：11-06→11-09 的交集达到 100%，仍跨价格来源和参考价生成路径；11-09→11-10 的重叠超过 99.94%，却存在缺失样本变化。重叠比例无法独立证明可比。

对观察样本广度，只有同来源/公式且集合一致，或每个集合差异都能由已核实上市/退市/停牌解释时，才可考虑比较，并保持“观察样本”标签；若 U(t) 未知且无法证明缺失机制稳定，仍 unknown。全市场口径必须先有可验证 U(t)。共同样本收益只能作为单独诊断，不得替换主指标分母。暂不设统计显著性或最小样本量阈值；以后需更多已核验日期的分布支持，规则版本化后再引入。

### 6.3 已固定的反例

- 11-10 对比日必须是 11-09；若 11-09 某指标缺采，不许回退到 11-06。
- 11-09 上一交易日为 11-06，而非周末。当地历中 2026-01-05 前日为 2025-12-31；2025-01-02 前日为 2024-12-31。这里核实的是本地日历，不等于本轮独立验证全部节假日来源。
- 11-06→11-09：即使价格样本交集 100%，扩展文件来源与近期 AKShare/BaoStock 选择路径不同，未有等价性证据前，广度 delta 不可直接发布。
- 11-09→11-10：前日有价而今日无价为 `688053.SH,688798.SH`；今日新增可观测代码为 `002623.SZ`。不能把这 3 项观察差异自动称作上市/退市，需缺失原因证据。
- 供应商 11-10 的 93 与前日 54 可以复现 +39；严格非 ST 身份尚未确认，正式“严格非 ST 热度改善”信号仍 unknown。
- history_limits 的 intraday_series 将多来源事件和候选触板并集，不能与单来源 broken 19 直接组成正式炸板率。
- 某日前池 height=1 但 first_board_date=NULL，不因当日价格正常就确认为首板或补齐晋级分母。

## 7. 待确认与尚未解决的问题

1. **供应商观察层与严格层的展示**：建议保留 93、14/45、4/8 为明确标注的供应商诊断值；严格指标返回 null。需要确认可接受当前近期严格层大面积 unavailable，而不通过隐含“池已排除 ST”假设放宽 v1.1。
2. **供应商池范围证据**：本地 success/row_count 只证明成功采集；尚缺可追溯到日期/版本的池是否包含 ST、新股、全部板块等范围契约。即便供应商宣称排除 ST，也需决定它是否足以证明逐股票当日非 ST 身份。
3. **历史全集和停牌**：本地目录虽为主口径，27 项冲突及历史全量停牌仍未解决；coverage_ratio 暂保持 null。退市日是否计入可交易集合、重新上市处理待证据确认。
4. **购买期参考价**：正值、分币与未复权数据不能自动证明所有除权日参考价正确；需明确沿用现有审计的证据门槛，未证实项 unknown。
5. **边界证据优先级**：供应商高度与本地 first_board_date 缺失/冲突时如何确认，例如 001379.SZ、002623.SZ；不允许无标记替换。
6. **资格变化与多重异常**：固定队列成员 D 日确认变成 ST、退市、特殊无涨跌幅限制时，建议保留分母并标 qualification_changed；需确认状态映射或追加状态。多个异常保留 flags，主状态优先级见第 5 节。
7. **比较门槛**：待确认“零未解释差异/零必需质量冲突”的保守首版门禁；不能根据 4 个抽样日期定一个万能重叠率阈值。
8. **严格二进三正常样本**：本轮供应商 4/8 算术已核验，严格身份完整的正常样本尚未确定；E2 再选择真实合格样本并加入缺行情/停牌测试，不记为 A2 已通过。
9. **日历与 PIT**：SSE 本地日历已用于邻日查询，但全区间完整性未独立验证；所有 feature_pit_verified=false，且历史分类包含事后生命周期信息，不能称严格 PIT 回测。

## 8. A0 六项验收证据

下表为待用户验收的证据映射，**不是自动勾选通过**。

| 验收 | 本轮证据 | 限制 / 待用户判断 |
| --- | --- | --- |
| A0-1 工作区与快照保护 | §1 记录原 HEAD、授权提交后 HEAD、完整变更清单、文件哈希比对；U 的 162 文件哈希匹配；H 单独哈希；唯一新交付为本文 | 用户后续明确授权提交既有 +929/-91，因此 Git HEAD 有意变化；报告未提交 |
| A0-2 每指标契约 | §4 对全部广度、梯队、晋级、炸板、邻日变化和方向证据列来源/字段、范围、公式、单位及 unknown 条件 | 尚未实现，不能称 API 已支持 |
| A0-3 覆盖率与两维质量 | §3 实测目录 5,565、生命周期冲突与停牌缺口；分别给 O/价格/身份计数和来源分布；明确正式覆盖率 null | 历史真实全市场分母未获证明，不能给确定比例 |
| A0-4 固定队列与异常 | §5 两种晋级固定分母、6 类状态、N=P+F+Q、null 与上下界；完整列出二进三 8 只代码及两种交集 | 严格资格队列不可确认时 N=null，不冒充零；资格变化规则待确认 |
| A0-5 跨日可比 | §6 日历、来源版本、范围/身份、截止、质量、重叠及反例；基于实测提出零未解释差异门禁 | 100% 交集仍可能跨来源；门禁建议待验收 |
| A0-6 只产报告且复核基准 | 93、19/112、14/45、15 与 14 差异、二进三 4/8 及日差均重查；PG 只读事务；未改业务/数据库/行情 | 既有代码的授权提交与 E0 新产出分开；未运行 E1—E5，不训练或验证交易策略 |

核查执行：

- `git status --short`、`git rev-parse HEAD`、`git diff --stat/--numstat/--check`、tracked/untracked 文件 SHA-256 对照。
- U 的 DuckDB 只读 SELECT、H 的 Parquet SELECT、manifest 全量哈希核验；PG 只读事务 SELECT。
- 为保存用户既有改动执行相关现存测试：`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_metrics.py backend/tests/test_history_limits.py backend/tests/test_dated_limit_rules.py backend/tests/test_lifecycle_audit.py backend/tests/test_non_ipo_study.py backend/tests/test_streak_classification.py backend/tests/test_ingestion.py::test_daily_reference_uses_same_response_and_checks_symbol backend/tests/test_data_inventory.py::test_files_read_actual_rows_and_report_missing_partition -q`：**41 passed**，1 条 FastAPI/Starlette 的 httpx 弃用警告。
- `npm run typecheck --prefix frontend -- --incremental false`：通过。
- 交付前再次核验：开始时记录的 **256 个现存 tracked/untracked 文件全部内容一致、无缺失**；U 的 162 个清单文件仍全部匹配；H 的 6 个文件与本轮读取时摘要一致。`git status --short` 仅显示 `?? docs/market_signal_metric_contract.md`。普通 `git diff --check` 通过；新文件另以 `git diff --no-index --check /dev/null docs/market_signal_metric_contract.md` 检查，无空白错误（新文件差异本身返回 1）。
- **未运行**完整集成测试，因为其 fixture 会 CREATE/DROP PostgreSQL 测试 schema；本次保持数据库只读。未运行构建、浏览器/UI 验收、E1—E5 的新功能测试，不将以上检查当成 A1—A5 通过。

## 附录 A：开始时 +929/-91 的完整文件范围

以下 25 个文件当时已跟踪且修改，随后原样纳入授权提交：

```text
backend/app/analytics/reconstruction.py
backend/app/analytics/rules.py
backend/app/collectors/ingest.py
backend/app/main.py
backend/app/providers/normalize.py
backend/app/research/limit_rules.py
backend/app/research/seasonality_study.py
backend/app/research/unified.py
backend/app/statistics/history_limit_queries.py
backend/app/statistics/history_limits.py
backend/tests/test_history_limits.py
backend/tests/test_ingestion.py
backend/tests/test_metrics.py
backend/tests/test_non_ipo_study.py
backend/tests/test_streak_classification.py
docs/historical_limit_rule_sources.md
frontend/src/app/data/page.tsx
frontend/src/app/globals.css
frontend/src/components/Chart.tsx
frontend/src/components/Dragons.tsx
frontend/src/components/HistoryLimits.tsx
frontend/src/components/Nav.tsx
frontend/src/components/Research.tsx
scripts/build_history_limits.py
scripts/finalize_research.py
```

以下 18 个文件当时未跟踪，随后原样纳入同一次授权提交：

```text
backend/app/api/data_inventory.py
backend/app/collectors/repair.py
backend/app/services/data_inventory.py
backend/app/services/lifecycle_audit.py
backend/tests/test_data_inventory.py
backend/tests/test_dated_limit_rules.py
backend/tests/test_lifecycle_audit.py
docs/stock_identity_policy.md
frontend/src/app/data/lifecycle/page.tsx
frontend/src/app/data/operations/page.tsx
frontend/src/app/supplier/page.tsx
frontend/src/app/supplier/pool/page.tsx
frontend/src/components/DataInventory.tsx
scripts/audit_data_assets.py
scripts/audit_historical_st.py
scripts/audit_stock_lifecycle.py
scripts/collect_recent_daily.py
scripts/repair_pool_gaps.py
```

## 附录 B：核心复核 SQL

以下 SQL 对冻结的 U.research.duckdb 以 read_only=True 执行，路径 U/H 见 §1；不运行任何构建入口。

```sql
-- 来源、参考价及身份质量：正参考价只是必要条件，不等于全部合法性已验证。
SELECT price_source, count(*) n,
  count(*) FILTER (WHERE price_quality='verified') price_verified,
  count(*) FILTER (WHERE pre_close>0 AND isfinite(pre_close)) reference_present,
  count(*) FILTER (WHERE is_st=false) non_st,
  count(*) FILTER (WHERE is_st=true) st,
  count(*) FILTER (WHERE is_st IS NULL) st_unknown
FROM stock_daily GROUP BY price_source;

-- 固定同源供应商队列，分别代入 k=1、2；只复核交集，不宣称严格合资格。
WITH prev AS (
  SELECT stock_code, consecutive_limit_days, raw_id
  FROM events WHERE provider='akshare' AND limit_type='up'
    AND trade_date=DATE '2026-11-09'
), today AS (
  SELECT stock_code, consecutive_limit_days, raw_id
  FROM events WHERE provider='akshare' AND limit_type='up'
    AND trade_date=DATE '2026-11-10'
)
SELECT p.consecutive_limit_days level, count(*) fixed_observed_n,
  count(*) FILTER (WHERE t.consecutive_limit_days=p.consecutive_limit_days+1) matched_n
FROM prev p LEFT JOIN today t USING(stock_code)
WHERE p.consecutive_limit_days IN (1,2) GROUP BY 1;

-- 上一日来自完整日历序列，不能先过滤成两个有数据的日期再 lag。
SELECT trade_date, lag(trade_date) OVER (ORDER BY trade_date) previous_trading_day
FROM calendar
QUALIFY trade_date IN (DATE '2026-11-09', DATE '2026-11-10');
```

基线复核先检查同源各池 `(stock_code,trade_date)` 唯一性、整批 raw_id、coverage success 和 up/broken 互斥；不能把 selected_events 的跨来源优先选择当成完整池的通用实现。

## 附录 C：E1 条件放行与实际交付（2026-11-10）

上面的 E0 报告保留原始审计时状态。用户随后按以下三项条件放行 **E1**：正式全市场覆盖率继续 null；14/45、4/8 仅作供应商诊断，身份未确认时严格非 ST 晋级率 null；27 项生命周期冲突完整保留，不能删股或改分母。此次仅执行 E1，不代表 E2—E5 获得放行。

### 实现范围与参考价决定

- 新增 `backend/app/statistics/market_breadth.py`：逐日广度与质量守恒校验。
- 新增 `scripts/build_market_breadth.py`：读取冻结 U 和已有恢复文件，按月增量写入独立目录；内容哈希确定版本，每月完成标记支持断点重跑，整批完成后才发布 manifest。
- 新增 `backend/tests/test_market_breadth.py`：16 项计算、缺值、来源匹配、月份边界及身份/生命周期保留测试。
- E0 原文未覆盖，本节追加交付证据。未更改已有业务模块、API、UI、PostgreSQL、原始行情、U/H 快照或历史 `daily.parquet`，未提交 Git。

为落实“合法参考价”而不将所有正数 pre_close 默认认证，E1 采用明确的保守路径：

1. 购买期：必须有原始同日 pre_close，且与**前一 SSE 交易日**无价格冲突、有效且非已确认停牌的 close 一致，才作为普通交易日参考价；只作校验，绝不填入缺失的 pre_close。除权/复牌/首个观测日无法核实则 unknown。没有使用 adj_factor 或未来价格。
2. 扩展期：仅当同代码、同日期、同来源、OHLC 完整匹配且原 pre_close=NULL 时，使用已审计的 raw close-change/pct 恢复结果。非空但非法的原参考价不被覆盖。
3. 近期数据库来源：通过冻结的 pg_daily_id 找到同响应记录，校验 provider/date/code、未复权、成功状态、OHLC/pre_close 与所选价格一致，且按该参考价计算的涨跌幅与同响应 pct_change 相差不超过 0.0051 pp。

三种路径都要求正有限分币价格/参考价、合法 OHLC、无价格冲突；已确认全日停牌不计算收益。未确认身份不排除广度样本，但身份统计保留 unknown。参考价核验路径在逐日质量分组中分别列出，不声称独立逐股票认证或严格 PIT 能力。

**没有使用上市/退市日期过滤广度记录，也没有把生命周期冲突作为价格失效条件。** 冲突同时记录 observed/valid 数；无有效价格时的 unknown 来自价格或参考证据，而不是通过删除冲突代码消除问题。

### 实际快照与总量

新目录：`~/Datasets/asharedragon/statistics/market_breadth/snapshots/c4708c8d4b038858cefa`。

| 项目 | 实测结果 |
| --- | --- |
| 输入 | U=`20261110T091616883852Z`，H=`e0860bd1d2439accd838`，与 E0 相同 |
| 范围 | 2017-01-03—2026-11-10；119 个月，2,391 个本地交易日 |
| 沪深 observed_count 总计 | 10,354,823 |
| price_valid_count 总计 | 10,312,942 |
| price_unknown_count 总计 | 41,881：参考价未核实 41,456，已确认停牌 90，缺失/非法价格 335 |
| 范围外记录 | 26 条，另列 outside_scope，不混入沪深分母 |
| 正式覆盖率 / 股票全集状态 | 每日 coverage_ratio=null、expected_universe_count=null、universe_status=unknown |
| 日期可用性 | 2,390 日有可计算观察样本；2017-01-03 为 no_valid_prices，涨跌平及中位收益为 null |
| 生命周期冲突 | 27 项原始冲突逐项原样保留；冲突代码有 18,022 条观察记录，其中 17,896 条有效价格记录仍纳入广度 |

产物：每月 `YYYY-MM.parquet` 为日期级指标，`YYYY-MM.quality.json` 为按日/价格来源/参考价依据/未知原因/身份/生命周期状态分组的计数；`lifecycle_quality.json` 保存全量 27 项冲突及其他未解决项，包含该时期没有行情的冲突股票；`inputs.json`、`summary.json` 保存来源、规则代码哈希、计算时间和文件摘要。质量分组不是抽样截断列表。

每日 `observed_valid_ratio` 是有效数/观察数，**即使等于 1 也不是全市场覆盖率**。无有效样本日的 up/down/flat 均 null，守恒校验使用 coalesce(...,0)；有有效样本且某分类为空时，该分类为已观察的 0。

### A1 抽样与验收证据

| 日期 | 上涨 | 下跌 | 平盘 | unknown | 观察总数 | 中位收益（%） |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2018-06-15（购买） | 480 | 2,661 | 51 | 109 | 3,301 | -2.518705 |
| 2020-12-01（购买） | 3,276 | 624 | 167 | 3 | 4,070 | 0.907441 |
| 2026-05-18（扩展） | 2,307 | 2,763 | 117 | 0 | 5,187 | -0.263019 |
| 2026-11-06（扩展） | 716 | 4,431 | 71 | 1 | 5,219 | -1.851852 |
| 2026-11-09（近期） | 2,042 | 3,010 | 166 | 3 | 5,221 | -0.350176 |
| 2026-11-10（近期） | 4,002 | 1,073 | 142 | 0 | 5,217 | 1.370679 |

| A1 项 | 已执行的证据 |
| --- | --- |
| A1-1 三期抽样 | 先对上述购买、扩展、近期日期及 2017 首日做只读计算，再扩展全历史；每个数据期均有有效日期 |
| A1-2 守恒 | 全部 2,391 日：涨+跌+平+unknown 与原始沪深唯一代码数对照，差异 0；观察/有效两个基数上的 ST 三分计数均守恒 |
| A1-3 缺价/非法参考价 | 测试覆盖 None、0、NaN、Infinity、非分币、停牌、来源冲突、恢复 OHLC 不匹配；缺价不作平盘，不能 lag 填充 |
| A1-4 确定性与重跑 | 同输入重跑复用全部 119 个月，快照 ID 与文件摘要不变；另独立重新计算 2018-06、2020-12、2026-05、2026-11 共 68 个日结果，与已发布结果逐字段一致 |
| A1-5 日期隔离 | 测试相邻日期收益不混算、缺日不向更早有值日回退、跨月前一交易日校验；日内恢复要求同代码同日期 |

执行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python scripts/build_market_breadth.py --lifecycle-report data/lifecycle_audit/20261110T075306Z/report.json
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_market_breadth.py backend/tests/test_metrics.py backend/tests/test_history_limits.py -q
```

相关测试 **44 passed**（其中新测试 16 项）。构建初次暴露 DuckDB 保留字别名问题，已改为明确别名并通过两日 CLI 冒烟、全历史构建与全量复用重跑；失败尝试未发布完整 manifest，也未触碰既有数据。当前只有新广度目录的 manifest 指向上述完整快照。

**尚存限制：** 历史全集、全量停牌、27 项生命周期冲突和历史 ST 缺口均未被本次“解决”；购买期部分除权/复牌日因参考证据不足保留 unknown。14/45 与 4/8 的供应商诊断结论保留，E1 不计算或发布严格晋级率，不实现跨日信号。API/UI 集成属于后续步骤，本次未实施；A1 证据待用户复核，不自动推进 E2。

## 附录 D：E2 涨停梯队与两组晋级队列（2026-11-10）

用户在 E1 交付后回复“可以了 下一步”，本次据此执行 E2。三项保守约束继续有效；本节不修改上文历史审计记录，不进入 E3—E5。**E2 实现与证据待用户验收。**

新增文件：`backend/app/analytics/limit_structure.py`（分层计算及固定队列）、`scripts/build_limit_structure.py`（冻结输入构建）、`backend/tests/test_limit_structure.py`（20 项测试）。复用现有 `analytics/metrics.py` 的供应商算术逻辑和 E1 价格有效性判断；不改变原函数、现有 API/UI 或 E1 计算，不提交 Git。

### 数据与实现规则

- E2 快照：`~/Datasets/asharedragon/statistics/limit_structure/snapshots/97a75650db37d3a7c059`。来源 U/H 与 E1 一致；覆盖 2,391 个日期，其中 12 日有完整采集标记的 AKShare 涨停池。其他历史日期的供应商指标明确 unavailable，不按最近有值日填充。
- `daily.json` 保存日期级梯队、炸板分子分母、严格与供应商两组队列、每只队员的状态/reasons/前后 raw_id/当日高度/参考价依据；同时保存 `historical_evidence`，将旧 H 的事件确认观察数、推算候选数和观察未知数分列。旧 H 计数不是严格非 ST 全市场确认值，不参与供应商比率分母。
- `lifecycle_quality.json` 原样保留 27 项冲突及其他未解决项；不以生命周期过滤删除股票。`inputs.json` 和 `summary.json` 保存全部输入与规则代码哈希。重跑重新计算并逐项比较稳定产物，不能覆盖一个内容不同的同名快照。
- 同池必须同供应商、日期、类型、整批 raw_id，代码无重复，采集 row_count 与事件记录数一致；范围外北交所代码另记。空响应不自动确认为零；只有显式独立空池证据才允许 []，缺采为 None。
- `supplier_diagnostic` 可以给出观察比率；`strict_non_st` 和正式晋级需要身份、价格、制度/事件、边界及来源范围证据。当前适配器将 `scope_verified=false`：本地 success 只证明采集成功，尚无可用的日期化范围认证，不能偷偷将其改为 true。
- 精确高度必须与供应商高度、H 的连续 confirmed 高度一致；首板起点有证据，起点前一真实交易日有同源 confirmed 非涨停证据。仅有候选负例或重建 height=1 均不够。计算不读取 exact_end、final_height 或未来结果标签。
- 前日完整严格队列成立才有正式 N；前日队列不可确认则 N/rate/区间均 null，观察队列另留。N 一旦成立，当日 suspended、identity_unknown、price_missing、boundary_unknown 均保留在 N 中；`N=P+F+Q`，Q>0 则 rate=null，区间为 `[P/N,(P+Q)/N]`，不是置信区间。
- 已确认变成 ST 的成员仍留在固定队列，主状态暂归 identity_unknown、原因明确为 qualification_changed，并保留真实身份 confirmed_st，避免把“身份已知但资格改变”误写成身份事实缺失。
- 不在今日涨停池中不足以判失败，还要求当日价格、身份、规则和直接非涨停事实。缺行情不因出现供应商事件而跳过价格门禁。多个异常完整记录，主状态按停牌→身份/资格→价格→边界优先。

### 真实样本复核

| 2026-11-10 指标 | 供应商诊断结果 | 严格层 |
| --- | --- | --- |
| 涨停总数 | 93 | null |
| 首板 / 二板 / 三板以上 | 73 / 15 / 5 | 均 null |
| 炸板率 | 19 / (93+19) = 16.9643% | null |
| 一进二 | 固定观察队列 45，代码交集 14，31.1111% | rate=null，正式 N=null；45 个观察队员均有 identity_unknown 记录 |
| 二进三 | 固定观察队列 8，代码交集 4，50% | rate=null，正式 N=null；8 个观察队员均有 identity_unknown 记录 |
| 跌停数量 | null（缺采，不能记为 0） | null |

一进二诊断对象明确输出 `outside_previous_cohort_codes=['002623.SZ']`：这只今日二板不属于昨日首板队列；不会把 15 当成晋级分子。二进三交集为 `002310.SZ、600327.SH、601188.SH、603366.SH`。两组完整代码交集通过直接查询冻结 U.events 的同源 SQL 再次独立核对，结果一致。

### A2 六项验收证据

| 验收项 | 已执行证据 |
| --- | --- |
| A2-1 数值基准 | 真实快照复现 93、19/112、14/45，明确标为供应商诊断；正式严格值未误用这些数值 |
| A2-2 15 与 14 的区别 | 输出二板数 15、一进二交集 14 及多出的 002623.SZ；测试验证今日目标高度总数不能替代代码交集 |
| A2-3 两种股票级追溯 | 一进二 45、二进三 8 个观察队员均保存前后 raw_id、高度、状态、原因；每组诊断另存全量 matches，已知 ST 被排除出严格队列时仍保留在供应商审计队列 |
| A2-4 二进三正常与异常 | 受控测试验证身份/边界/范围齐全时 1/2=50%，以及缺行情、停牌、身份未知、资格改变、来源冲突；**正常严格场景为测试夹具，不是本地已找到的严格历史样本** |
| A2-5 固定分母 | 对一进二和二进三均测试：一人晋级、一人未判定时 N=2 不变，P=1、Q=1、rate=null、区间 [0.5,1]；前日队列不可确认时正式分母与区间均 null |
| A2-6 边界/零/缺采 | 测试边界未知不标首板、N=0 返回 null、独立确认空池计数 0、未采集 null、缺日前日不回退、多来源不可混算、up/broken 重叠不出炸板率、开板回封仍属于 up |

命令与结果：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.build_limit_structure
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_limit_structure.py backend/tests/test_metrics.py backend/tests/test_history_limits.py backend/tests/test_market_breadth.py -q
```

相关测试 **64 passed**，其中 E2 新测试 20 项；同输入完整重算再次通过，2,391 日产物一致。核验 U 的 162 个文件、H 的 6 个文件及 E1 已发布月分区哈希均未变化。未连接或写入 PostgreSQL，未请求行情供应商，未提交 Git。

**尚未解决：** 历史 ST 与来源范围证据仍不足，当前发布的严格晋级率全部 null；需补证后才能获得真实严格正常样本。27 项生命周期冲突及全市场全集/停牌缺口仍保留，coverage_ratio 始终 null。E3 信号、E4 API、E5 UI 尚未执行，当前网页不会自动显示新产物。

## 附录 E：E3 / A3 方向信号交付（2026-11-10，待用户验收）

依据 Notion Spec v1.3（内部规格），读取版本最后编辑时间为 `2026-11-10T11:29:54.497Z`。本次只实现 E3；前述 E0—E2 结论及限制继续有效。E4 API、E5 UI 未开发；本附录取代前文“E3 尚未执行”的进度描述，但不代表 A3 已获用户验收。

### 实际文件、输入与产物

本次新增三个文件：

- `backend/app/analytics/direction_signals.py`：无 I/O 的方向规则与指标级质量门禁。
- `scripts/build_direction_signals.py`：复用 E1 计算器，读取冻结 E1/E2，逐月生成独立 E3 快照；全量重跑核验确定性。
- `backend/tests/test_direction_signals.py`：35 项 E3 测试。

本报告仅追加附录。E1/E2 实现、原业务 API、前端、数据库均未修改，无重构。HEAD 保持 `92addd24bdd4fe10eab1a38fa9a2fe731a38869e`，未提交 Git、未部署。

冻结输入为 U=`20261110T091616883852Z`、E1=`c4708c8d4b038858cefa`、E2=`97a75650db37d3a7c059`；参考价恢复沿用 H=`e0860bd1d2439accd838`。规则版本 `direction-signals-v1`，正式 E3 快照为：

`~/Datasets/asharedragon/statistics/direction_signals/snapshots/b5d7a3aac723793c13ac`

产物包含 119 个月分区、`inputs.json`（输入路径、哈希、算法字段白名单）、`rules.json`、`lifecycle_quality.json`、`samples.json`、`summary.json`。共 2,391 个交易日，每日 12 条信号，共 28,692 条。构建中重新推导股票级可比性档案，逐日全部聚合字段必须与冻结 E1 完全相等；任何不一致直接停止，不回写 E1。

### 方向规则与契约

| 层 / 指标 | 数值、分母与公式 | 正面 / 负面规则 | 正式可用条件 |
| --- | --- | --- | --- |
| observed_breadth.up | E1 `up`，有效参考价下 close > reference 的家数 | 增加 / 减少 | 两日广度质量和股票级可比性均通过 |
| observed_breadth.down | E1 `down`，close < reference 的家数 | 减少 / 增加 | 同上 |
| observed_breadth.advancing_ratio | E1 `up / price_valid_count` | 增加 / 减少 | 同上，分母 > 0 |
| observed_breadth.median_return | E1 `median(close / verified_reference - 1)` | 增加 / 减少 | 同上，数值有限 |
| strict_non_st.limit_up_count | E2 `strict_non_st.up_count` | 增加 / 减少 | 已确认同源/同范围和非 ST 资格，E2 无关键质量原因 |
| strict_non_st.broken_limit_rate | E2 严格 `break_numerator / break_denominator` | 减少 / 增加 | 同上；涨停/炸板互斥、分母 > 0 |
| strict_non_st.promotion_1to2 / promotion_2to3 | E2 两组正式 `promoted_count / fixed_denominator` | 增加 / 减少 | 队列已确认、未判定数为 0、分母 > 0；两日各自完整 |
| supplier_diagnostic 对应四项 | E2 供应商池数量、炸板分子分母、股票级交集晋级率 | 与对应规则相同 | 同源同范围池可用、两日版本相容、数值和分母自洽；仅 diagnostic |

窗口固定为上一 SSE 真实交易日；家数差为 `当日 − 上一交易日`，比率差为 `(当日比率 − 上一交易日比率) × 100 pp`。零差为 neutral；不设任意幅度阈值或模型权重，不宣称统计显著性。出现缺失、非有限数、零分母或不可比时，保留已知 `value/previous_value`，`delta=null`、`direction=unknown`，原因显式输出。

每条信号输出 A3 要求的 `id、name、direction、value、previous_value、delta、unit、reason、status、source、snapshot_id、rule_version`，另有两日日期、`delta_unit、layer、reason_codes、comparison_status、input_snapshots、basis、eligible_for_formal_counts`。`value` 的比率单位为 ratio，`delta_unit=pp`。来源含两日价格来源/参考价校验分布，或供应商名字、池类型、日期、raw_ids、范围和可用状态。晋级依据还包含两日队列异常分类计数、资格原因、固定分母、未判定数及上下界；股票级完整名单继续指向冻结 E2，不复制或改变队列。

`counts.formal` 只统计可观察样本广度与严格非 ST 层；`counts.supplier_diagnostic` 单列供应商诊断，绝不混入正式正负数。两者均分 positive / negative / neutral / unknown，不生成单一总分。普通价格广度的“正式”只指可观察样本描述性方向，不能解释为全市场或严格非 ST。

### 可比性门禁及保守边界

1. 从冻结 SSE calendar 取得确切前驱；若该日前日数据缺失，不向更早日期回退。每条输入的日期再次核对。11-09 的上一交易日是 11-06，不是周末。
2. E1/E2 必须来自同一个 U 快照；检查源 manifest、分区哈希和既定算法版本。广度按原始字段白名单重算只为获得代码集合和质量档案，不创建新的广度定义。
3. 广度检查两日计数守恒、非负整数计数、有效样本 > 0、上涨占比自洽；检查完整已观察集合、完整有效集合、各代码价格来源、参考价依据、质量分类，以及影响价格的冲突。任何尚未解释的集合/质量差异或来源变化均阻止比较。输出新增/移出/来源变化/质量变化代码与交集、两侧重叠率、Jaccard；**不通过取交集、删除股票或修改分母制造可比性**。
4. 阈值是“零未解释样本差异、零关键价格冲突”，不是 95%/98% 重叠率。当前没有经核实的新股/退市/停牌变化解释清单，故这些变化仍保守地阻断方向。全市场 `coverage_ratio` 始终 null；全市场全集未知不会被观察样本相同误称为已解决。
5. ST 质量与价格质量分开。普通广度允许身份未知，同时展示已确认非 ST、ST 和未知数量；严格层仍依赖 E2 的身份、边界和结局确认。27 项生命周期冲突全量保存在质量报告中，不删股、不减分母；生命周期审计状态仅作注记，不用于价格广度选股或方向判断。
6. 供应商池必须具有明确 provider、scope、交易日与可用状态；跨日 provider / E2 规则版本 / E2 快照 / U 快照变化，或同日涨停炸板范围不一致，均为 unknown。供应商范围尚未独立核实的事实通过 `scope_verified=false` 保留，故这些方向仅为诊断。

### 实测汇总与典型样本

| 层 | positive | negative | neutral | unknown |
| --- | ---: | ---: | ---: | ---: |
| 可观察样本广度 | 16 | 24 | 0 | 9,524 |
| 严格非 ST | 0 | 0 | 0 | 9,564 |
| 供应商诊断 | 19 | 23 | 0 | 9,522 |

广度只有 **10 / 2,391 日**通过完整可比门禁；其余 2,381 日四项方向全部 unknown。通过日期为 2024-02-20、02-21、03-05、03-22、03-25；2026-03-18、06-04、08-14、09-18、10-27。大部分阻断来自观察/有效集合变化和逐股票质量变化，不是笼统地被历史 ST 未知或生命周期报告阻断。没有为增加正式信号数量放宽门禁。

| 日期及对比日 | 真实证据 | 结论 |
| --- | --- | --- |
| 2024-02-20 vs 02-19 | 两日 5,104 个观察/有效代码完全相同；上涨 4,033→3,348（−685）；下跌 1,022→1,557（+535）；上涨占比 −13.420846 pp；中位收益 −2.384711 pp | 四条正式广度 negative；当日 5,101 个 ST 未知、3 个生命周期冲突观察仍保留 |
| 2024-02-21 vs 02-20 | 同样 5,104 个代码、同价格源和参考价依据；上涨 3,348→3,986（+638）；下跌 1,557→992（−565）；上涨占比 +12.5 pp；中位收益 +0.559830 pp | 四条正式广度 positive |
| 2026-11-09 vs 11-06 | 有效集合交集 5,218，重叠 100%；5,218 个代码价格来源/参考价依据变化，观察集合也变化 | 四条广度 unknown，delta=null；证明“100% 有效重叠”也不能掩盖换源 |
| 2026-11-10 vs 11-09 | 有效集合新增 002623.SZ，移出 688053.SH / 688798.SH，共同 5,216；另有 1 个代码来源变化 | 四条广度 unknown，delta=null，不把表面上涨家数改善升格为方向 |
| 2017-01-03 vs 2016-12-30 | E1/E2 无确切前驱数据；首日价格参考质量不足 | 12 条均 unknown，不向其他有值日回退 |

2026-11-10 的 **AKShare 供应商诊断**（11-09→11-10；涨停 raw_id 538→541、炸板 539→542）：

| 指标 | 上一日 → 当日 | delta | 方向 / 层 |
| --- | --- | ---: | --- |
| 涨停家数 | 54 → 93 | +39 家 | positive / diagnostic |
| 炸板率 | 44/98 → 19/112 | −27.933673 pp | positive / diagnostic |
| 一进二 | 8/21 → 14/45 | −6.984127 pp | negative / diagnostic |
| 二进三 | 0/4 → 4/8 | +50 pp | positive / diagnostic |

该日诊断计数是正面 3、负面 1；正式计数是 unknown 8。两组严格晋级的当日 `identity_unknown` 分项分别为 45 和 8，正式 N、rate 仍为 null，不把诊断分母 45/8 升格为正式分母。所有历史严格信号均 unknown。真实数据中暂未出现通过门禁的 neutral 或同日正式正负混合样本；这两个分支由受控测试验证，不能冒称历史实测。真实同日正负共存证据来自上述供应商诊断。

### A3 五项验收证据与补充检查

| 验收项 | 已执行证据 |
| --- | --- |
| A3-1 完整解释契约 | 每日 12 条、三层隔离；35 项 E3 测试验证字段、来源、状态、数值、分母、pp、规则版本、异常计数；样本文件可逐条查看 |
| A3-2 同输入确定性 | 最终代码下完整构建两次，全部 119 月重算；123 个内容文件与 summary.json 逐字节哈希一致，快照 ID 均为 b5d7a3aac723793c13ac |
| A3-3 不跨缺日 | 缺失前驱与误传更早日期测试均 unknown；独立核对全部 2,391 日 previous_trading_day 与冻结 SSE 日历完全一致；含跨周末及首日缺失 |
| A3-4 正负可并存 | 11-10 实测供应商诊断正 3 / 负 1，未混入正式；受控正式广度样本同时正 3 / 负 1；零变动测试 neutral，缺失不计正负 |
| A3-5 旧日期无未来标签依赖 | SQL 白名单只有日线价格、同日身份和质量字段；不读后续退市日期、周期最终高度、exact_end；修改未来结果标签的测试不改变输出。原始购买日线独立 SQL 核对 02-19/20/21 的 5,104 样本及涨跌家数一致 |
| 补充质量门禁 | 覆盖同大小不同代码集合、100% 重叠换源、参考价依据变化、质量变化、价格冲突、零分母、ST 未知、单池缺失、池范围/日期/供应商不一致、版本/快照改变、异常负数和 NaN；不相关广度仍可运行 |
| 工作区与输入保护 | E3 开始时 263 个已有文件在报告追加前全部哈希不变；报告追加后原文前缀哈希仍一致。U 162 文件、H 6 文件、E1/E2 364 文件哈希未变化；27 项冲突原样保留 |

测试命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_direction_signals.py backend/tests/test_limit_structure.py backend/tests/test_market_breadth.py backend/tests/test_metrics.py backend/tests/test_history_limits.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.build_direction_signals
# 同一构建命令完整再运行一次，内部逐文件比较已存在的最终快照。
```

结果：**99 passed in 1.52s**（E3 新增 35 项 + 原相关 64 项）。独立校验全部 28,692 条信号的 delta / direction / 分层计数、2,391 日交易日前驱及所有严格 null，均通过。另用原始 stock_daily 按同日 close 与 pre_close 比较，核对 2024-02-19/20/21 上涨/下跌分别为 4033/1022、3348/1557、3986/992。未改前端，未运行 E5 UI 验收；没有连接 PostgreSQL 或请求行情供应商。

### 尚未解决，等待 A3 验收

- 全市场 U(t)、历史停牌和历史 ST 身份证据仍不足，coverage_ratio=null，严格非 ST 指标及方向仍不可用。
- 当前广度可比门禁仅放行 10 天；新股/退市/停牌导致的样本变化尚未逐项得到可验证解释。扩大可比范围需要新的证据及规则版本，不应简单降低重叠比例阈值。
- 27 项生命周期冲突仍需归因；本期保留质量注记，不宣称已修复。
- 供应商诊断仅表示 AKShare 冻结池口径变化，不代表全市场严格晋级率；队列 ST/边界/结局不足继续阻断正式值。
- 本版本属于冻结收盘数据的描述性研究，缺少 available_at / feature_pit_verified；即使不使用未来结果标签，也不宣称 point-in-time 回测。
- 产物已生成但尚未接入 API/网页；E4/E5 等待后续授权。本次不提交 Git、不部署，A3 等待用户验收。

## 附录 F：D0 / D1 共同样本广度修复验收报告（2026-11-10，待验收）

依据 Notion《AShareDragon 市场信号 Spec v1.5》第十三节（内部规格），读取版本最后编辑时间 `2026-11-10T11:55:22.147Z`。本次仅执行 D0/D1；没有进入 D2—D4、E4/E5，没有补采行情、改写原始数据、提交 Git 或部署。

### 结果与口径边界

旧规则仍保留 **10 / 2,391 日**可比结果。新增指标 `matched_cohort_breadth` 可计算 **2,386 日**，相对于旧规则新增 **2,376 日**，没有丢失旧版 10 个可计算日。其中 **2,361 日**可给出未触发构成敏感提示的共同样本方向，**25 日**数值可算但标记 `composition_sensitive`；另 **5 日**仍不可计算。

这是新增“同一组股票两日比较”的估计对象，不是把旧 E3 的正式信号重新放行，也不是修复了原始价格数据。每日完整观察广度、共同样本方向、旧 E3 信号、供应商诊断与严格非 ST 资格仍分开保存。**2,386 不等于 2,386 天都应显示确定利好/利空**：新层 `direction=unknown` 共 30 日，包含 25 个构成敏感日与 5 个不可计算日。

新层明确方向为 positive 1,127 日、negative 1,234 日，neutral 0 日；真实数据未出现 mixed 或通过门禁的 neutral，相关分支由受控测试验证，不冒称真实历史样本。全市场 `coverage_ratio` 仍为 null，严格非 ST 的旧信号仍全部 unknown。

### 变更与保护证据

仅新增：

- `backend/app/analytics/matched_cohort_breadth.py`：D0 分类、共同样本计算、构成效应及敏感性判断。
- `scripts/build_matched_cohort_breadth.py`：复用原 E1 计算器、E3 只读加载和校验，生成独立版本的逐日审计、配对股票证据、old/new diff。
- `backend/tests/test_matched_cohort_breadth.py`：24 项新增测试。

本报告只追加此附录。旧 E1/E2/E3 规则、脚本和测试未修改；HEAD 仍为 `92addd24bdd4fe10eab1a38fa9a2fe731a38869e`。开始时保存 266 个已有工作区文件哈希；报告追加前全部一致，追加后报告原文前缀仍一致，其余 265 文件不变。保护清单中 **786 个既有数据/manifest 文件**全部哈希不变，包括原行情、H、E1/E2/E3，以及先前 E3 的预备版本。没有修改旧 manifest 指针。

规则版本：`matched-cohort-breadth-v1`。新快照：

`~/Datasets/asharedragon/statistics/matched_cohort_breadth/snapshots/85a306fef660a4d1ab66`

冻结输入：U=`20261110T091616883852Z`，E1=`c4708c8d4b038858cefa`，E2=`97a75650db37d3a7c059`，旧 E3=`b5d7a3aac723793c13ac`。旧输出和新输出分别存储，非原地覆盖。

### D0：可追溯原因审计

产物位置（相对新快照）：

| 文件 | 内容 |
| --- | --- |
| `2017-01.json` … `2026-11.json` | 2,391 日逐日审计：确切前交易日、来源分布、观察/有效集合大小、共同数、两侧共同比例、新增/退出代码、完整价格质量转移矩阵、逐股票变化与前后证据、旧原因、互斥主因、新规则结果 |
| `old_new_diff.csv` | 2,391 行 old/new 可用性比较，含来源段、共同样本数量/比例、Δpp、旧主因、旧全部原因、新状态及原因 |
| `cohorts/YYYY-MM.parquet` | 每一共同股票的日期、代码、前日收益、当日收益；共 10,271,102 对，包含仍待核的来源边界日证据，但未放行其方向 |
| `summary.json` | 按年份、来源段、原因、互斥主因汇总；全量质量转移计数及文件哈希 |
| `samples.json` | 指定五个失败日期、不同年份、跨月/跨年、停牌和构成敏感案例 |
| `inputs.json` / `rules.json` | 全部快照、文件/代码哈希、来源日期、算法版本和规则 |
| `lifecycle_quality.json` | 原有未解决事项，包括全部 27 项生命周期冲突 |

D0 独立重推旧版原因，与每一天冻结 E3 输出完全一致：

| 旧原因（可重叠，按日期） | 日数 |
| --- | ---: |
| 缺可用比较数据 | 2 |
| 共同有效股票来源/参考价依据切换 | 3 |
| 已观察集合变化 | 2,305 |
| 有效集合变化 | 2,365 |
| 已观察或有效集合变化的并集 | 2,380 |
| 逐股票价格质量状态变化 | 2,196 |

互斥主因继续使用“缺可用数据 → 来源变化 → 样本/质量变化 → 其他”：**2 + 3 + 2,376 + 0 = 2,381**；另有 10 日旧规则通过。不得把上面的重叠日数相加。

逐股票归因分开记录，不把未知解释成正常：

- `observed_suspension_evidence`：存在 E1 `suspended` 与 `trade_status=false` 的具体记录，可解释该股票未进入有效共同样本；不因此过滤其他共同股票。
- `price_quality_issue`：缺失/非法价格、参考价未核实等状态；不当作确定涨跌，不静默补成 0。价格质量未核实不等于已经证明原价错误。
- `observation_membership_unexplained`：某日未观察到记录，原因待证；不能据此认定正常 IPO、退市或停牌。
- `source_or_reference_change_pending_review`：来源或参考价依据/状态切换，保存前后字段；不是已经证明存在来源冲突。只有影响 C(t) 内股票且兼容性不能证明时，才直接阻断新共同样本方向。C(t) 外质量状态改变仍保留审计，并纳入构成敏感性分析。

类别可以重叠。首日缺前驱的审计明确 `previous_profile_available=false`，其 `not_observed` 转移不能解释成数千只新股上市。实际股票对转移包括 valid→reference_unverified 29,952 对、reference_unverified→valid 41,406 对、valid→suspended 13 对；这些是**股票对次数**，不是日期数，也没有进一步推断成除权、数据错误等 D2 根因。

### D1：新公式和门禁

令 t−1 为冻结 SSE 日历中 t 的真实前驱；V(t) 为 E1 在 t 日判定价格有效的股票集合，`C(t)=V(t−1)∩V(t)`。只有同一 C(t) 中两天各自通过价格/参考价验证的记录参与共同样本计算：

- `N=|C(t)|`；`U_C(d)=C(t) 中 d 日 close/reference−1>0 的股票数`。
- `matched_ratio(d)=U_C(d)/N`；`matched_delta_pp=100×[U_C(t)−U_C(t−1)]/N`。
- 同时输出两日共同样本涨/跌/平、收益中位数及其中位数差，身份计数，以及 `N/|V(t−1)|`、`N/|V(t)|`。这些是共同样本占比，不是全市场覆盖率。
- 逐日完整样本比例 `full_ratio(d)=E1.up(d)/|V(d)|` 保留原值。`full_delta_pp=100×[full_ratio(t)−full_ratio(t−1)]`。
- 构成效应 `composition_effect_pp=full_delta_pp−matched_delta_pp`；等价于“当日完整样本与共同样本比例差”减“前日完整样本与共同样本比例差”。输出两侧差值及分解，不偷换分母。

共同样本的前后日期、U 快照、E1 版本、范围必须一致；每个共同股票都必须有可信价格、经 E1 校验的参考价依据，且前后价格源和参考价规则相同。共同股票存在冲突、参考价不可信或未经核实的来源切换时，**保留原 C，不删除问题股票来绕过门禁**，新指标返回 unknown、delta=null。零有效样本或空 C 同样不能变成 0。

对于 C 以外的单只正常进出、有证据停牌或参考价质量切换，不再直接否决整日共同股票比较；这些股票在原事实层、逐股审计和构成分解中仍完整保留。ST 未知允许进入普通有效价格共同样本，但不能据此升级严格非 ST 身份。

### 构成敏感性：用可审计数学边界，不设任意重叠率阈值

令 W=V(t−1)∪V(t)，M=|W−C|；a=|V(t)−V(t−1)| 为前日缺有效结果数，b=|V(t−1)−V(t)| 为当日缺有效结果数。令 K 为两日完整有效样本上涨家数之差，则对 W 中缺失的“是否上涨”作 0/1 极端完成，可得到标准化同一 W 上的敏感性边界：

`lower_pp=100×(K−a)/|W|`，`upper_pp=100×(K+b)/|W|`。

这是**假设性构成压力测试**，不是补值结果、实际收益估计、置信区间或全市场指标。停牌/缺价股票没有被实际赋予 0 或 1，也没有改写 V 或 E1。判断规则：

1. 完整样本差与共同样本差方向相反：`status=mixed`。
2. 边界允许共同样本相反方向，或共同样本为零而边界允许非零方向：`composition_sensitive`。
3. 构成效应的绝对值达到/超过共同样本差的绝对值且构成效应非零：单列敏感，不把主要由构成驱动的变化笼统归为方向。
4. M≥|C|：未配对部分已不小于共同样本，标记共同样本相对不足。该界限表达信息量的结构关系，不是调参得到的 95%/98% 通过率。

敏感或混合状态保留可计算的 `delta` 与 `cohort_direction`，但汇总 `direction=unknown`，不会强行给整日唯一利好/利空。价格/来源真正不可比时则连共同样本 `delta` 也为 null。两类 unknown 明确区分。新层 `eligible_for_formal_counts=false`，不混入旧 E3 未注明共同样本范围的计数。

### 2,391 日 old/new 全量比较

| 年份 | 日数 | 旧可比 | 新共同样本可算 | 无敏感提示方向 | 构成敏感 | 不可计算 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2017 | 244 | 0 | 242 | 240 | 2 | 2 |
| 2018 | 243 | 0 | 243 | 240 | 3 | 0 |
| 2019 | 244 | 0 | 244 | 242 | 2 | 0 |
| 2020 | 243 | 0 | 243 | 241 | 2 | 0 |
| 2021 | 243 | 0 | 243 | 237 | 6 | 0 |
| 2022 | 242 | 0 | 242 | 239 | 3 | 0 |
| 2023 | 242 | 0 | 242 | 238 | 4 | 0 |
| 2024 | 242 | 5 | 242 | 242 | 0 | 0 |
| 2025 | 243 | 0 | 243 | 240 | 3 | 0 |
| 2026 | 205 | 5 | 202 | 202 | 0 | 3 |
| **合计** | **2,391** | **10** | **2,386** | **2,361** | **25** | **5** |

按当日观察记录主导价格源划段，仅用于统计展示，不代表同日不存在其他少量来源；精确来源分布仍逐日披露。

| 来源段 | 日数 | 旧可比 | 新可算（比例） | 构成敏感 | 不可计算 |
| --- | ---: | ---: | --- | ---: | ---: |
| 购买日线 | 2,230 | 5 | 2,228（99.91%） | 25 | 2 |
| AKShare 扩展 | 159 | 5 | 158（99.37%） | 0 | 1 |
| 近期 | 2 | 0 | 0（0%） | 0 | 2 |

这里的百分比是**日期计算可用率**，绝不是股票市场覆盖率。

不可计算的 5 日互斥归因：

| 日期 | 原因 | 处理 |
| --- | --- | --- |
| 2017-01-03 | 无确切前驱数据，当日有效样本也为 0 | 不跨缺口，不补 0 |
| 2017-01-04 | 前日有效样本为 0，C 为空 | 不回退至其他日期 |
| 2026-03-16 | C=5,181，购买日线→扩展日线来源/参考价依据切换尚未核实 | 保留未知，留给 D2 |
| 2026-11-09 | C=5,218，11-06→11-09 来源/参考价依据切换 | 保留未知，不能靠 100% 交集放行 |
| 2026-11-10 | C=5,216，其中 600537.SH 从 baostock→AKShare，兼容性待证 | 不删除这只股票来制造可比，仍 unknown |

不可计算原因的**重叠计数**：previous_session_profile_missing=1、zero_valid_sample=2、empty_common_sample=2、common_source_or_reference_change_unverified=3。这些原因共涉及 5 日，不能直接相加。

其余 25 个方向未知日全部为 `unpaired_outcomes_can_reverse_direction`，即共同样本数值可算，但极端构成测试允许相反方向。日期为：2017-06-05、10-12；2018-05-28、05-29、06-08；2019-06-20、06-21；2020-05-21、05-28；2021-05-20、05-27、06-04、06-10、06-21、07-19；2022-05-23、06-15、07-08；2023-05-19、06-26、07-17、07-31；2025-06-06、07-11、10-14。CSV 可直接筛选这 25 日，不隐藏或合并为确定方向。

### 真实案例与验收证据

| 场景 | 日期 / 前驱 | 共同样本及结果 |
| --- | --- | --- |
| 样本/质量改变仍可计算 | 2017-01-05 / 01-04 | C=2,815，上涨 2,348→1,130，−43.268206 pp；完整样本差 −43.161142 pp，构成效应 +0.107064 pp |
| 跨月和春节闭市 | 2017-02-03 / 01-26 | C=2,880，上涨 2,121→965，−40.138889 pp；没有按自然日前移或跨缺值找最近日期 |
| 跨年 | 2018-01-02 / 2017-12-29 | C=3,232，上涨 2,313→2,504，+5.909653 pp |
| 停牌 | 2023-11-17 / 11-16 | 600766.SH 当日 trade_status=false，保留停牌证据且不进入 C；其余 C=5,068，上涨 935→3,550，+51.598264 pp |
| 构成敏感 | 2017-06-05 / 06-02 | C=2,881，上涨 2,257→2,205，−1.804929 pp；完整样本差 −1.483919 pp；边界 [−3.252850,+0.134138] pp 跨越零，因此不输出整日确定负面 |
| 后期购买日线 | 2026-01-05 / 2025-12-31 | C=5,155，上涨 2,352→3,946，+30.921435 pp；原始完整样本与共同样本差单独记录 |

` samples.json ` 还包含 2017—2026 每年首个可计算案例及五个指定失败日。全量验证覆盖 **118 个可计算跨月日期、9 个可计算跨年日期**；全部 2,391 日前驱与冻结 SSE 日历一致。

| 验收项 | 已执行结果 |
| --- | --- |
| D0 完整归因 | 逐日重推旧原因与冻结旧输出一致；2,381 失败日与互斥/重叠汇总吻合，逐股变化和质量转移可追溯 |
| D0 不臆断自然变化 | 缺记录保留未解释；停牌仅在存在具体状态证据时分类；质量切换不直接解释成复权/除权或真实来源冲突 |
| D1 新增/停牌/质量单股变化 | 三种受控样本均返回共同样本数值，未让整日无条件 unknown；保留加入/退出与质量记录 |
| D1 兼容性和零值 | 两日异源、参考价依据切换、不可信参考价、价格冲突、空共同样本、零有效样本、缺前驱及快照/范围/版本变化均正确拒绝 |
| D1 构成敏感性 | 相同共同上涨比例但构成变化、相同完整比例但共同变化、方向相反以及共同样本不足均有测试；0/1 穷举独立验证敏感性边界，分解恒等式通过 |
| D1 old/new 全量 | 2,391 日比较完成，新可算 2,386、新增 2,376、旧可算损失 0；25 敏感与 5 不可算单列；没有把来源未知或严格 ST 未知提升为已确认 |
| 旧 E1/E2/E3 回归 | 99 项原相关测试 + 24 项新增测试，**123 passed in 1.71s** |
| 独立数值校验 | 全部 10,271,102 个共同股票对唯一性/成员哈希、配对上涨数/比例、中位数、Δpp、构成分解与旧信号保持性通过；两个层次的计数未混合 |
| 独立原始价格抽样 | 2017-01-05、2018-01-02、2023-11-17、2026-03-18、2026-11-10，共 21,511 对股票，以冻结原始 close/pre_close 或既有核验恢复值独立 SQL 复算，两日收益均一致；这不代表跨源兼容性已获验证 |
| 确定性与保护 | 最终代码完整重跑两次，243 个内容文件及 summary.json 哈希全部相同；已有 786 个数据/manifest 文件及原工作区内容未改变；27 项生命周期冲突全量保留 |

运行命令：

```bash
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -p no:cacheprovider backend/tests/test_matched_cohort_breadth.py backend/tests/test_direction_signals.py backend/tests/test_limit_structure.py backend/tests/test_market_breadth.py backend/tests/test_metrics.py backend/tests/test_history_limits.py -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m scripts.build_matched_cohort_breadth
# 完整重跑相同构建命令；对比每个 JSON/CSV/Parquet 文件及 summary 的哈希。
```

### 未解决事项与本次停止点

- 三个来源边界仍待 D2 核实，尤其 11-10 的单只共同股票换源没有因其占比小而被忽略；本次未实施 D2 的来源兼容修复。
- 样本进入/退出的真实上市、退市或缺采原因仍可能未知。本次通过明确限定共同样本解决比较定义，不宣称已补齐市场全集或解释全部进出。
- 25 个构成敏感日仍保留方向未知；敏感性边界只是描述性压力测试，不是统计显著性判断或预测概率。
- C(t) 随相邻交易日变化，本期结果仅解释各自这对日期的共同股票；不能不加说明地当作整个历史固定面板指数。
- 全市场覆盖率、历史 ST/限价/首板边界证据及 27 项生命周期冲突仍未解决。旧严格非 ST 信号保持原样，供应商诊断不会升级。
- 没有获得 available_at / feature_pit_verified，仍非严格 point-in-time 回测。
- API/网页尚未接入新层。D0/D1 实现和验证完成，等待用户验收；不自动进入 D2—D4、E4/E5，不提交 Git 或部署。
