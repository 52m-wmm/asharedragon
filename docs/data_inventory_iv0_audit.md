# IV-0：现有三层 Data Inventory 只读对账报告

审计日期：2026-11-10。依据 Notion《AShareDragon Spec v1.9》第十四节 IV-0（内部规格），读取版本最后编辑时间 `2026-11-10T12:25:26.878Z`。状态：已完成只读审计，等待用户验收。

## 结论

**已有一套 Inventory，有原始证据、标准化数据、统一快照和派生指标；无需建设第二套。E1/E2/E3/D1 的实际输入版本可以从磁盘追溯，但现有 `/data` 没有把三层连起来，尚不足以作为“按用途已验证数据”的完整入口。**

主要缺口是：页面不展示统一/派生快照依赖、字段级质量与允许用途、审计证据和代码版本状态；文件来源的逐记录追溯需要按来源、代码、日期重建路径。另发现一项实际统计错误：Inventory 对 Tushare 共用端点的不同事件类别选批次时互相覆盖，少展示了 12,668 条事件；原数据库和统一快照未丢失这些记录。

本次仅新增本报告。未修改业务代码、数据库、原始文件、快照、manifest 指针或其他未提交文件；没有补采、重建、提升 candidate 为 verified、提交 Git 或部署。未执行 IV-1/IV-2、D2—D4、E4/E5。

## 1. 现有入口及三层结构

| 入口 | 实际读取内容 | 已有能力 | 缺口 |
| --- | --- | --- | --- |
| `/data` → `DataInventory.tsx` → `/api/data/inventory` | 本地 processed 日线 manifests、PG `stock_daily` / `limitup_events` / `raw_observations` | 按来源、年份显示行数、股票数、日期、缺日、缺参考价/ST；区分已整理数据与原始响应，提示不可跨来源相加 | 未显示统一快照、E1/E2/E3/D1、输入版本与质量证据关联 |
| `/data/operations` → `/api/data/status` | `data_coverage`、任务、失败响应和 SSE 日历 | 采集运行状态、池缺日、重试入口 | 属于运维状态，不是字段验证；`tushare_configured` 仅表示配置，不代表数据存在或权限已核实 |
| `data/asset_audit/20261110T074640Z/report.json` | 原包、processed、交叉核对和缺口 | 有真实审计结果、容差、价格/成交量差异及原包核验 | 页面仍是固定的“局部校验”文字，未读取此证据；报告自身也不是所有后续快照的批准单 |
| `research/unified/manifest.json` → U | 冻结 PG 导出、文件源和其他证据，DuckDB 只读视图 | 已有不可变版本集合、文件哈希、输入哈希、字段质量和来源选择 | 不含完整 raw payload；缺构建代码 commit/hash、统一的按用途状态映射 |
| H / E1 / E2 / E3 / D1 manifests、inputs、summary | 各自冻结输入、规则与派生结果 | 已有依赖链和重现证据，E1 以后有代码内容哈希 | Inventory 未登记这些“被使用的版本”；字段验证与允许用途未汇总到原入口 |

实际调用现有 `inventory()` 服务取得 **20 条资产条目**。调用使用 PostgreSQL `REPEATABLE READ, READ ONLY` 事务，`SHOW transaction_read_only=on`；没有通过 ORM 写入。页面分析依据现有组件和路由代码，未启动新服务或执行浏览器交互验收。

## 2. 四个来源的原始数据、范围和版本

以下范围均区分“响应请求日期”与“响应内实际行情日期”；最小/最大日期不代表连续全量覆盖。不同层、不同来源不能相加为市场总量。

### 2.1 本地购买数据包

- 原始 ZIP：`/Volumes/NO NAME/股票1d线.zip`，本次确认可访问。
- SHA-256：`0dfebf6ee9bc3e9c0b6bc64da773d1c85ea852143b9318001033dc986287de67`，本次全文件复算匹配。
- 原包范围 **2010-01-04—2026-03-13**，3,930 个日文件、13,376,389 行，来自已有资产审计；本次未重新全量解压审计全部成员，抽查一个原始日文件。
- 已导入研究范围 **2017-01-03—2026-03-13**，111 个月分区，9,517,685 行、5,419 只股票。原包其余 3,858,704 行位于当时导入范围之外，不能称为导入丢失。
- 来源版本 `purchased_usb_0dfebf6ee9bc`。staging manifest 保存原包哈希、成员名/CRC、选定范围、分区哈希、重复和非法值计数；processed manifest 状态为 `validated_partial`，不是全市场完整或所有字段正确。
- 已有原包→暂存、暂存→processed 审计差异为 0；本次 111 个 processed 分区哈希全部匹配，统一快照中该来源全部 9,517,685 条 OHLC/pre_close 与 processed 记录对应，缺关联/值差异均 0。

位置：

```text
~/Datasets/asharedragon/staging/stocks/daily/purchased_usb_0dfebf6ee9bc/
~/Datasets/asharedragon/processed/stocks/daily/purchased_usb_0dfebf6ee9bc/
```

processed manifest SHA-256：`45dcaa6c835772f729d19fd795b89555868f80951b42e0eb399ca9994fd27939`。原数据包再分发许可未在现有清单中找到明确记录，不能把原包或行情上传 Git。

### 2.2 AKShare：文件扩展与 PG 近期/样本必须分开

**文件扩展链**：

- `staging/stocks/daily/akshare_em_unadjusted_20260316_20261106/symbols/*.json`：5,246 个股票检查点，包含规范化 rows 与原始 raw 响应。
- 原始 records 826,378 行，实际记录范围 **2026-03-02—2026-11-06**；其中 100 行为前期 10 股各 10 日交叉验证重叠样本。
- processed 研究范围 **2026-03-16—2026-11-06**，159 个交易日、9 个月文件、826,278 行、5,244 只有数据股票；不是将 5,246 个检查点误称为 5,246 只都有有效数据。
- 版本 `akshare_em_unadjusted_20260316_20261106`，manifest 状态 `validated_partial`。本次 9 个月分区哈希匹配，全部 826,278 条统一数据与 processed OHLC/pre_close 对应，差异 0。
- 原始检查点按文件名排序、依次哈希“文件名+文件内容”的集合摘要为 `2e80d0aaa8ccf0694f4597ab0bd22b1a699be0a6fc218e7da5684c31c13a072a`，与现有 H 参考价恢复报告记录完全相同。
- processed manifest SHA-256：`9196b15168eb06057f802b998570e5cbca62195dfb885c707d28f98b5ba9c6de`。

**PostgreSQL 原始响应**：

| 端点 | 响应版本数 | 请求日期范围 | 成功 / 失败 | 原始 records 行数 |
| --- | ---: | --- | --- | ---: |
| stock_zh_a_hist | 5,230 | 2024-01-31—2026-11-10 | 5,222 / 8 | 10,581 |
| stock_zt_pool_em | 16 | 2022-06-15—2026-11-10 | 12 / 4 | 847 |
| stock_zt_pool_zbgc_em | 13 | 2026-10-26—2026-11-10 | 12 / 1 | 396 |
| stock_zt_pool_dtgc_em | 13 | 2026-10-26—2026-11-10 | 10 / 3 | 227 |
| tool_trade_date_hist_sina | 1 | 请求 trade_date 为 null | 1 / 0 | 8,797 |

日线规范化入库 10,581 行，**实际行情日期为 2024-01-02—2026-11-10，只有 86 个不同日期**，包含稀疏抽样，不能把范围写成连续全覆盖。统一 U 最终选取 AKShare 价格 10,434 行，均在 2026-11-09—11-10。

事件规范化为 up 807、broken 396、down 227；实际事件范围均从 2026-10-26 开始，down 结束于 11-09。请求过 2022-06-15 不代表有那天可用的涨停池。原始 up 响应 847 与规范化 807 不同，原始响应含失败批次等记录，不能机械相等或直接累加。

### 2.3 Tushare：当前实际是兼容代理事件，不是完整官方日线库

- PostgreSQL provider 为 **`tushare_proxy`**；当前没有 provider=`tushare` 的规范化日线，也没有 Tushare `stock_daily`。没有证据可把“配置了 Tushare”当成已持有完整日线/ST/涨跌停价库。
- PG `limit_list_d`：401 个原始响应版本，398 success、3 failed；请求日期 2020-01-02—2022-01-04；实际规范化事件日期 **2020-01-02—2020-12-31**，17,772 行。
- 文件归档位于 `data/raw/tushare/`，当前共 **831 个 JSON**，其中 **823 个响应档案 + 8 个检查点/状态档案**。响应中有 48,007 行 records，包含修订、范围响应等重叠，不能视作唯一事件数，也不能再加到 PG 17,772 上。
- 823 个响应档案：success 780、validated_range 28、received 14、failed 1。所有响应内部 `response_hash` 本次复算匹配；冻结 U 的 `archive_inventory.json` 登记的 831 个文件 SHA-256 也全部匹配。
- 档案带 dataset、trade_date、category、source_id、request_status、response_hash、archived_at；同路径修订保留带旧哈希后缀的版本。原始范围响应中的记录日期需要读取 records，不能仅凭档案文件名/请求日判断覆盖。
- 当前正确规范化事件：up **10,917**、down **3,112**、broken **3,743**，合计 **17,772**。统一 U.events 保留这些事件。
- `proxy_facts` 当前为空；未见已入库的历史 `stk_limit`/`stock_st` 等可据此放行严格资格的记录。没有为本次审计探测远程权限。

### 2.4 BaoStock：价格补充、状态证据和目录

| 端点 | 响应版本数 | 请求日期范围 | 成功 / 失败 | 原始 records 行数 |
| --- | ---: | --- | --- | ---: |
| query_history_k_data_plus | 86 | 2022-01-31—2026-11-10 | 79 / 7 | 11,583 |
| query_daily_history_k_AStock | 9 | 2026-10-23—2026-11-04 | 9 / 0 | 47,012 |
| query_all_stock | 8 | 2022-01-04—2026-11-04 | 8 / 0 | 53,008 |
| query_stock_basic | 1 | 2026-11-05 | 1 / 0 | 9,031 |

- PG 规范化日线共 58,577 个存储版本；按现有 Inventory 的 provider/代码/日期/复权键取最新后为 **58,547 条**，实际行情范围 **2017-01-03—2026-11-09**。58,577 中含 30 个被较新记录替代的版本，不是数据丢失。
- 全市场单日端点规范化 46,994 行，原响应 47,012；原始目录/原始范围可能含研究范围之外证券，需依据规范化规则和原记录说明，不能把所有差异都标成损坏。
- 日线有 `preclose/isST/tradestatus/adjustflag`；58,577 条存储版本的 pre_close、is_st 均非空，90 条为停牌状态。不能把这小部分来源的身份覆盖扩展到整个 U。
- U 最终选取 BaoStock 作为价格源 **91 行**（2023-11-17—2026-11-09）：90 条停牌价格为 diagnostic，1 条 verified；它还作为其他股票日线的状态/估值补充来源。因此它是实际依赖，不能遗漏。

### 2.5 原始响应哈希口径也需要版本说明

PG 当前共 5,778 个响应：AKShare 5,273、BaoStock 104、Tushare 兼容代理 401。全部有 64 位 sha256。

- 5,752 条按当前 `save_raw` 的 `SHA256(sorted JSON {params,payload,status}, ensure_ascii=False)` 复算匹配。
- 最早 AKShare raw_id **1—26** 用当前方法不匹配，但全部与 `SHA256(sorted JSON {params,payload}, ensure_ascii=False)` 精确匹配，即不含 status 的口径。
- 因此不能把这 26 条直接报为原始数据损坏；应披露“哈希口径不统一、缺少算法版本元数据”。本次只观察并复算两种口径，未改写旧 hash，也未假称已找到历史发布说明。

## 3. 清洗、去重、冲突与排除规则

| 环节 | 当前实现 | 对记录的处理及限制 |
| --- | --- | --- |
| 购买包导入 | `scripts/import_historical_daily.py` | 校验 ZIP 路径/成员、文件日期与记录日期、代码格式、必需字段；按股票/日期检查重复；非法批次暂停，不任意保留一行；保留 source/pre_close/adj_factor，初始复权状态 unknown |
| 购买包发布 | `scripts/release_historical_daily.py` | 对审计哈希、重复、异常日期、价格精度、OHLCV、VWAP 与本地交叉样本设置门禁；拒绝失败分区；发布 adjustment=none、股/CNY、validated_partial，未认证复权因子全历史完整性 |
| AKShare 日线规范化 | `providers/normalize.py:daily_ak` | 日期/代码核对、OHLC 正数及高低一致；成交量从手×100转换为股；参考价只从同响应 close−涨跌额恢复并校验涨跌幅，不用前一行 close 补值 |
| 扩展文件发布 | `scripts/extend_daily_2026.py` | 日期限于 03-16—11-06，100 行验证重叠数据保留在 raw 而不重复发布；非交易日/重复日期拒绝；按代码/日期检查重复；旧扩展 processed 的 pre_close 仍为 null，恢复值存于 H，不原地回填 |
| BaoStock 规范化 | `providers/baostock.py:normalize_daily` | 要求 adjustflag=3；状态只接受 0/1/空；交易中记录校验 OHLC/preclose，停牌不虚构涨跌；量为股，不再乘100 |
| PG raw 与规范化 | `models` / `collectors/ingest.py` | raw 按 provider/endpoint/sha256/trade_date 去重；保留各版本。Daily 唯一键含代码/日期/provider/endpoint/adjustment/raw_id；Event 含代码/日期/limit_type/provider/endpoint/raw_id；都有 raw_id 外键 |
| AKShare 池批次 | `normalize_pool` | 同批同代码、除序号外相同的重复行合并；冲突字段拒绝整批；空响应不自动等于零事件 |
| 统一 U 价格选择 | `scripts/finalize_research.py` | 成套 OHLCV+pre_close 选择：购买包 → 扩展文件 → 本地 BaoStock → 本地 AKShare；股/日键唯一，其他状态/事件/估值来源单列，不把整行称作同一供应商数据 |
| U PG 预选 | `daily_supplements` | adjustment=none、ingestion_status=success；同股同日先 BaoStock，再按 fetched_at/id 新旧。PG 先选一个补充来源，之后才进入 price_candidates |
| U 冲突 | `price_conflicts`、`event_conflicts` | 价格候选 OHLC 不同会标记冲突/diagnostic，仍保留记录；事件状态/高度冲突置未知；当前 price_conflicts=0 只表示已进入候选集合的比较，不证明预选淘汰的所有 PG 来源都一致 |
| U 事件选择 | `events` / `selected_events` | 每 provider/endpoint/date/limit_type 的最新整批，再按股票去重；单个 stock/day 的 selected_event 先 up、down、broken，再优先 tushare_proxy；完整 events 仍独立保留 |
| U 股票日全集 | keys 并集 | 价格、事件、既有日证据键的并集；因此保留 361 条没有价格源的证据行，不是每条 U 行都有已清洗价格 |
| E1 有效价格样本 | `statistics/market_breadth.py` | 沪深范围；停牌、冲突、非法价格、参考价缺失/不可信各有原因，不从原快照删除；26 条北交所记录在 E1 范围外；ST 未知不阻断普通价格广度 |
| E2/E3/D1 用途门禁 | 各自版本规则 | 严格非 ST 需身份、来源范围和队列资格；供应商仅诊断；共同样本仅比较两日可信同股价格，不放行三处未核实来源切换 |

**Inventory 页面自己的“取最新”不等于研究快照清洗规则。** 日线页面按 provider/代码/日期/adjustment、最大 id；U 的 PG 预选则包含来源优先级和 fetched_at。应显示各自选择口径及输入版本，不能把不同选择所得行数混称为一个清洗后总量。

## 4. 逐记录追溯：已有链路与断点

### 已实测的关联完整性

- PG `stock_daily`、`limitup_events`、`stocks` 的 raw_id 缺失/孤儿均为 0。
- U 中 **10,525** 条选择自 PG 的价格记录，`pg_daily_id → 冻结 pg_stock_daily → pg_raw_links` 缺关联为 0，代码/日期/provider 不一致为 0。
- U 的 19,202 条 events 到 `pg_raw_links` 孤儿为 0。
- U 中购买包 **9,517,685** 条、扩展文件 **826,278** 条，按 source+代码+日期均可匹配 processed，OHLC/pre_close 元组差异均 0。
- **361 条无价格源记录**不能追到价格原文，因为本来没有价格；应追到事件/日证据并标明 price unknown，不能伪装成清洗后有效行情。

### 抽样原始证据

| 来源 / 样本 | 已复核链路 |
| --- | --- |
| 购买包抽查 | U→source/代码/日期→processed 月分区→原始 ZIP 分区；价格及参考价一致。具体股票日和原值保留于本地审计证据，不公开分发行情记录。 |
| AKShare 扩展抽查 | U→同名 source→原始 symbols 文件内日期；缺失参考价与已有核验恢复参考价用途分开。原值留在本地证据。 |
| AKShare 近期抽查 | U.pg_daily_id→raw_id；同响应价格与参考价一致，可定位原始响应与哈希。原始字段值不公开。 |
| BaoStock 抽查 | PG daily id→raw_id→原响应哈希，可反查来源。原始记录保留于本地。 |
| Tushare 代理：000716.SZ，2020-01-02 up | 规范化事件→raw_id=150；SHA `d9525be14ac5070774e9d00c1095795dab52f8a56d3812fe804a86a642a6d5c9`；同日不同类别原始批次独立存在 |

### 不能宣称已经完全闭环的部分

1. 文件来源行没有显式 `raw_file_id/raw_row_id`；通过固定 source、股票代码、日期、manifest/原包成员规则可重建路径，但 UI 没有 resolver 或点击入口。本次验证清洗→processed 的全量关联，并抽查 raw；不宣称所有原始行都重新逐条人工验证。
2. U 的 `pg_raw_links.parquet` 只冻结 id/provider/endpoint/date/fetched_at/status，**没有原 payload 或原 raw.sha256**。旧版追溯仍依赖 PG 的 raw 保留及其他归档；统一文件哈希不等于已冻结所有原始响应内容。
3. 价格、ST、事件、估值可能来自不同证据；U 有相应来源/ID，但缺一个按字段汇总的追溯视图。不能用 price_source 代表整行身份来源。
4. E1/E2/E3/D1 的 inputs 有代码内容哈希，但没有 `code_commit`。这些实现仍未提交，不能把当前 HEAD 谎报为其实现 commit。U/H 还存在代码元数据不够显式的问题：U 保存输入文件 hash、版本和 PG 快照号，H 的 key 含部分代码内容，但未完整列出源码哈希/commit。
5. 从磁盘可追到数据和规则，但从当前 `/data` UI 直接完成“任意日期→E1/E2/E3/D1→原文→精确构建 commit”的完整链路尚不成立。

## 5. 价格、参考价、ST、涨跌停价分别验证到了哪里

这些数量基于当前冻结 U 或 E1，不能把不同分母相加。

| 字段维度 | 已执行验证与现有结果 | 未通过 / 未知 | 允许用途 |
| --- | --- | --- | --- |
| 基础价格 / OHLCV | 规范化单位、OHLC 数值与高低关系、重复键、文件哈希；U.price_quality verified **10,354,398** 行 | diagnostic 90、unknown 361。verified 是程序质量口径，不是所有记录都与交易所逐条独立核验 | 作为进一步检查的价格事实；收益仍需参考价门禁 |
| 可用收益 / pre_close | E1 有效 **10,312,942**：购买期合法原参考价与真实前交易日 close 一致 **9,476,229**；扩展同响应 close−change、涨跌幅舍入及 OHLC 对应 **826,278**；PG 同响应参考价/pct 核验 **10,435** | E1 reference_unverified **41,456**；停牌90；missing_or_invalid_price335；outside_scope26。原 U 中扩展 pre_close 仍是 null，不能误报已回填 | 观察样本广度与经过来源门禁的共同样本收益；不是认证全历史除权因子 |
| ST 身份 | U.st_quality verified **63,629**：非ST60,777、ST2,852；有 BaoStock 同日标记及历史身份证据 | unknown **10,291,220**；供应商字段/证据核验不是完整历史身份覆盖，也没有 PIT 证明 | 普通价格样本可包含未知身份并披露；严格非ST限价/晋级必须继续单独门禁 |
| 官方涨停/跌停价格 | U.`official_limit_up_price` 非空 **0**；`official_limit_down_price` 非空 **0** | 全部无对应官方数值证据。AKShare broken events 有396条供应商 `limit_price`，不等于交易所官方历史限价；up/down 与 Tushare事件该数值列为空 | 供应商名单诊断、已知规则下 candidate 推算；不能称完整严格官方限价库 |
| 跨日价格源兼容 | D1 同源同参考规则的共同样本计算及敏感性检查 | 03-16、11-09、11-10 三个边界待核；25日构成敏感 | 可算2,386日中2,361日明确共同样本方向，25日不下整日唯一结论；5日不可算 |
| 全市场资格/覆盖 | 保留市场范围、身份、停牌与生命周期质量记录 | 全集分母未核实、27项生命周期冲突仍在 | coverage_ratio=null；不得把“某日有记录”或有效样本率称为全市场覆盖率 |

额外证据：既有资产审计对扩展价与 BaoStock 的 46,938 对重叠价格未发现差异；成交量 34,221 对存在差异，后续记录最大50股、无超过100股或0.1%的差异。这是带容差的既有局部交叉证据，不是全历史同值证明。购买发布也依赖局部 BaoStock/Tushare 对照，缺供应商原始单位/复权文档的限制仍保留。

注意 `data/historical_st_audit/20261110T075913Z/report.json` 指向旧 U=`20261110T072624457608Z`，不能直接把它的计数贴到当前 U。当前身份数量来自本次对当前 U 的实际读取。

## 6. E1/E2/E3/D1 实际版本及使用方

以下是**当前指针实际选择**，不是本次新增验收批准或 active_verified 提升。

| 逻辑数据集 | 当前版本 / 快照 | 父依赖与读取方 |
| --- | --- | --- |
| processed 两个文件源 | source+分区 manifest，均 validated_partial | `Research` 根据 `research/catalog.json` 读取，检查状态与未复权；用于 `/api/research/daily/market/coverage` 等旧入口 |
| U 统一日线 | research-final-v1 / `20261110T091616883852Z` | `UnifiedResearch` 读取统一 manifest 或显式 dataset；`/api/research/unified/*` 返回 snapshot；162个发布文件哈希及182个输入资产哈希本次全匹配 |
| H 历史统计 | v2.2 / `e0860bd1d2439accd838` | summary.source_snapshot 固定 U；`/api/history-limits` 读取 H manifest，事件读取 H 指定 U |
| E1 | observed-market-breadth-v1 / `c4708c8d4b038858cefa` | inputs 固定 U、H 恢复参考价、生命周期审计与代码 hash；summary+238个分区内容文件哈希核对通过 |
| E2 | limit-structure-v1 / `97a75650db37d3a7c059` | inputs 固定 U、H、E1，三项内容文件及 summary 验证通过 |
| E3 | direction-signals-v1 / `b5d7a3aac723793c13ac` | inputs 固定 U、E1、E2，123项内容文件及 summary 验证通过 |
| D1 | matched-cohort-breadth-v1 / `85a306fef660a4d1ab66` | inputs 固定 U、E1、E2、旧E3及代码；243项内容文件及 summary 验证通过 |

E1/E2/E3/D1 目前由构建脚本消费和生成，尚未接入业务 API/网页，不能把 `/data` 当前页面当成在显示这些新指标。

当前指针的 SHA-256（用于复核，不另建发布机制）：

```text
U   5c817be97ae11c8fed36208e96f92ee3400b486cae58322b3d69f576ca031ccc
H   3fc1c83b046e9687db0c16599ad6d4ee08ec50d0756705a1c9c5ff01e262b55f
E1  d1866292e483f28231e4c3c7f944d47e3f1929885b91589a8691c7d625cca52e
E2  bb3863d2a9ad85eb433f347d80a7257accfea19ad6a990628189e5bfc7afe64e
E3  4012e6c714ea5c01f31550942ce8bd615e51d8180e13a584a6a97f185320b3e6
D1  2ffdca85a2fa3356529e6ae81aadb4bda82628189b1b9e197840ead1c2d09377
```

版本读取的具体限制：

- 构建脚本先读取当前指针，再把输入版本写入派生 inputs；已有机制可复用，不需要额外 canonical 数据库或第二套 manifest。
- 普通 `UnifiedResearch()` 读当前 U，支持显式 dataset，但日常读取不逐文件重新检查 hash；hash 是发布/构建门禁与审计证据，不应在页面无条件显示“本次全量验证通过”。
- H 主统计固定其 source_snapshot，但 `history_limit_queries.candles()` 用无 dataset 的 `UnifiedResearch()` 读当前 U，返回 meta 不含 snapshot。当前两者正好一致；以后 U 升版存在统计与K线不同版本的风险。该项只记录，是否修改读取链另行授权。
- H 分类 manifest 缺失/不匹配时显式生成 unknown 分类，不是自动找到更早有效版本；页面应保留这类状态。
- `/api/data/status` provider 枚举没有 `tushare_proxy`，且默认看 AKShare 近期运行状态；这不是完整四来源 inventory 的替代入口。

## 7. 已有—缺失—推荐最小修改对照

| 已有功能 | 缺失或实际问题 | 推荐修改位置（本次不实施） |
| --- | --- | --- |
| 一个 `/data` 和 `/api/data/inventory` | 三层之间没有父快照/使用方关联 | 在原 `services/data_inventory.py` 增加现有 manifests/inputs/summary 的只读适配；原列表补 `kind、snapshot、parents、consumers、rule_version`；不创建新数据源选择系统 |
| PG 按最新整批展示事件 | Tushare 同端点 U/D/Z 被当成互相修订，少展示12,668条 | 同文件事件选择必须保留 provider/endpoint/date/**limit_type 或请求类别**的完整批次；不能改成逐股“最近有值”拼池；已有整池修订测试继续保留 |
| 已整理文件和 PG raw 数量 | ZIP和扩展 raw 检查点、Tushare文件归档未在 raw 层完整关联；raw日期是请求范围 | 同 service 读取已有原包/staging/归档清单，区分请求日期与实际内容日期、响应版本数与唯一记录数；不全量重扫另建库 |
| 部分缺字段计数、固定核验文案 | 未汇总字段 verified/failed/unknown、用途、证据时间、审计快照是否匹配 | 同 service 关联当前 U 质量汇总、E1参考价、E2严格资格、D1可用性和已有 asset audit；保留各自分母，提供允许/禁止用途及 known_gaps |
| 原包位置与可访问标记 | 页面固定写“原包全部行数未重新盘点”，没有展示已有原包审计与哈希 | 原 `DataInventory.tsx` 展示证据日期、hash/口径、原包审计结果及本次检查范围，不把旧审计装成实时全量校验 |
| ID/source/raw_id 与文件键可追踪 | 页面不能逐字段定位原文；代码commit缺失 | 同 service 暴露安全的 lineage 摘要和证据引用，组件呈现；没有 commit 时明确 null/未记录，使用已有 code_sha256，不能用 HEAD 冒充 |
| 现有文件分区测试和整池修订测试 | 缺共端点分类、原/清洗/派生依赖、字段状态与未知口径测试 | 扩展 `backend/tests/test_data_inventory.py`，覆盖Tushare分类不互相覆盖、无孤儿、父版本不匹配、缺参考/ST保持未知、审计版本不一致及旧hash兼容说明 |
| manifest/inputs 版本链 | H K线未 pin 到统计父U、旧 raw_links 缺原hash/构建commit等耐久元数据 | 单列后续建议：最小 pin 可在 `statistics/history_limit_queries.py` 完成；以后升版补 metadata，不回写旧快照。非本次 Inventory 展示修补的前置条件 |

**最少实施文件：两个运行文件 + 一个测试文件，共 3 个。**

1. `backend/app/services/data_inventory.py`：修复统计、复用已有快照与审计并补关联字段。
2. `frontend/src/components/DataInventory.tsx`：在原页面呈现三层、字段验证、版本依赖、用途和已知限制，类型也在此文件中。
3. `backend/tests/test_data_inventory.py`：新增必要回归。

`backend/app/api/data_inventory.py` 目前直接返回 service 对象，没有强制响应模型，可保持路由不变；`frontend/src/app/data/page.tsx` 已直接挂载组件，也无需修改。`/api/data/status` 保持运维用途，是否补 proxy provider 可以独立处理，不应为了 Inventory 重构。上述三文件方案补的是原 Inventory 的关联与查询，**不等于授权修改历史数据、审批状态或业务默认读取源**。

### 实际统计错误的可复核证据

问题位置：`backend/app/services/data_inventory.py:70` 的原始批次选取键为 `(trade_date,endpoint)`，但 Tushare U/D/Z 共用 endpoint=`limit_list_d`，各类别有独立 raw_id。

| Tushare 类别 | 当前 Inventory 展示 | 按类别选批次 / U.events | 少展示 |
| --- | ---: | ---: | ---: |
| up | 1,039 | 10,917 | 9,878 |
| down | 322 | 3,112 | 2,790 |
| broken | 3,743 | 3,743 | 0 |
| 合计 | 5,104 | 17,772 | **12,668** |

2020-01-02 原始关联例子：up raw_id=150 有84条、down raw_id=152 有1条、broken raw_id=153 有22条。它们是不同类别，不能把最新 broken 批次视为当天 up/down 的修订。现有查询还使 up/down 的展示起日错误缩到2020-12-01；正确记录从2020-01-02存在。本次未修复此查询。

## 8. 只读验证、未决事项与验收边界

本次执行：

- 阅读 Spec v1.9 IV-0、现有页面/API/service/规范化/快照构建和读取代码；未修改代码。
- PostgreSQL 全部查询在强制 READ ONLY 事务内，结束 rollback；验证原始版本、规范化范围、raw关联、实际 Inventory 返回和事件统计差异。
- 核对购买 ZIP 整文件 hash、120个 processed 分区 hash、831个Tushare档案文件 hash和823个内部响应 hash；重算扩展 raw 集合摘要并与恢复报告对齐。
- 核对 U 发布162文件/182输入资产；验证 E1/E2/E3/D1 已有摘要和内容哈希；全量核对两大文件价格源到统一表的键和值关联。
- 抽查三个历史日期的原始价格，加上 BaoStock/Tushare 的原始ID定位。未进行 IV-1 三个换源边界的逐股价格兼容性对账，不能把这项标成已通过。
- 没有运行会通过 `test_engine` 创建/迁移/删除 PostgreSQL 测试 schema 的集成测试，以遵守“数据库只读”；以上实际只读查询复现替代本次审计验证，不冒称新增回归测试通过。

保护结果：HEAD 保持 `92addd24bdd4fe10eab1a38fa9a2fe731a38869e`；审计前269个已有工作区文件及907个当前快照/manifest文件的保护清单在交付前再次核对。本报告是唯一新增的仓库文件；不保存原行情到 Git，不创建第二套 Inventory 或新 active 指针。

待验收结论：

- **版本关系可追溯：有，主要在磁盘 manifests/inputs/summary；不是从零开始。**
- **三层 Inventory 足够直接供用户判断可用性：还不够。** 缺字段级用途与证据关联、文件原始层入口、构建代码版本说明，且有 Tushare 类别统计错误。
- 建议先按上述3文件方案修补现有入口；具体实施需下一步授权，本次停在 IV-0。
- 全市场分母、历史 ST、官方涨跌停价、三处来源边界及27项生命周期冲突仍未解决。哈希匹配只能证明内容一致，不能代替行情经济含义和使用资格验证。
