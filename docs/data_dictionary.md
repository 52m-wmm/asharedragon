# 数据字典

迁移 `backend/alembic/versions/9e5a582ce7ae_initial_schema.py` 是本版固定数据库结构，模型定义见 `backend/app/models/__init__.py`。

## 共同约定

- 股票代码 `600000.SH` / `000001.SZ` / `920001.BJ`；A 股代码前缀与交易所不一致时拒绝，不隐式补零猜代码。
- `trade_date` 是交易所本地日期；板内时间是 Asia/Shanghai 的本地时间。时间戳使用 PostgreSQL timestamptz，内部 UTC，展示明确上海时间。
- 金额、市值：人民币元，Numeric(24,2)；价格 Numeric(20,4)；成交量和封单量：股。AK 日线原“手”乘 100。
- turnover_rate、pct_change、amplitude 为百分数点（12.3 表示 12.3%）；broken_limit_rate、promotion_rate 和研究收益为 0–1 比率。
- PE/PB、价格、封单、板型等缺失保留 NULL；不从当前行情反推历史。
- 事实表 Evidence 字段：provider、endpoint、trade_date、fetched_at、collected_at、available_at、source_record_id、raw_id、ingestion_status、error_message。available_at 未核实时 NULL，不能以收盘时间冒充已公开时间。

## 核心表

| 表 | 关键字段 / 粒度 | 语义 |
|---|---|---|
| stocks | stock_code + provider + endpoint + trade_date + raw_id | 源池当天股票名称/行业观察；valid_from=valid_to=当天，不能推到整个过去或未来；board/ST/上市退市时间未核实时 NULL |
| stock_daily | 股票 + 日期 + 源 + 接口 + adjustment + raw_id | open/high/low/close/pre_close、volume、amount、turnover_rate、amplitude、pct_change、市值与估值；当前采集 adjustment=none；pre_close 不由相邻收盘猜测 |
| limitup_events | 股票 + 日期 + limit_type + 源 + 接口 + raw_id | up=收盘涨停，broken=盘中触及后炸板，down=跌停池；封板时间、次数、资金、连板数、当日池市值；最新价不自动填为官方涨停价；动态 PE 不冒充 PE TTM |
| themes | theme_id | 题材名、分类、来源；本版无自动历史填充 |
| stock_themes | 股票 + 题材 + valid_from + 源 + raw_id | 闭区间有效期，valid_to NULL 表示未给结束；查询必须尊重有效期与来源 |
| market_sentiment_daily | 日期 + provider | 三类池数量、炸板率、板数分布、同队列晋级率；全市场成交/上涨/下跌家数当前 NULL |
| dragon_labels | 股票 + start_date + 类型 + label_version + provider | 纯后验标签；未来窗口收益、最大回撤、周期、定义 JSON 与生成时间；本版不自动创建任何标签 |
| ingestion_jobs | UUID job_id | 数据集、日期范围、参数、queued/running/success/partial/failed/interrupted、收到/插入/更新数、开始/完成时间和错误 |

## 追踪表

| 表 | 作用 |
|---|---|
| raw_observations | 请求参数、库版本、接口签名、返回字段、原始 DataFrame JSON、哈希、任务引用、错误与采集时间；不存 Token |
| data_coverage | 源 + dataset + 日期唯一；success/missing、有效行数、检查时间与最近错误；daily:代码 与各事件池独立 |
| trading_days | 交易日期 + exchange；当前为 AKShare 新浪 SSE 日历，每条有 raw_id；不是用 weekday 生成 |

## 索引与版本

股票代码和交易日期带索引；raw_id 外键指向原始响应。事实唯一键包含 raw_id，允许保留多个来源修订。同内容重复返回不产生新事实行。事件查询选择同源、同接口、同日最近成功 raw 的完整快照；日线选择同源、同股票、同日、同复权方式最近版本。

## AKShare 当前字段映射

`代码 → stock_code`，`名称 → stock_name`，`所属行业 → industry`，`首次封板时间 → first_limit_time`，`最后封板时间 → last_limit_time`，`炸板次数/开板次数 → open_times`，`连板数 → consecutive_limit_days`，`封板资金 → seal_amount`，`成交额 → amount`，`流通市值 → circulating_market_cap`，`总市值 → total_market_cap`，`换手率 → turnover_rate`。

涨停池不提供显式涨停价、一字板、T 字板、涨停原因和 PE TTM，保持 NULL。跌停池的“封单资金”为卖出封单，不映射涨停买入封单；“动态市盈率”不映射 PE TTM。

## v0.2 增量字典

`stock_daily` 新增可空 `is_st`（当日源 ST）、`trade_status`（当日是否交易），BaoStock 的 pre_close/PE/PB 直接保留历史字段，金额元、数量股。原 AKShare 行新增字段为空。

| 新表 | 粒度 | 用途 |
|---|---|---|
| reconstruction_runs | run_id | 规则版本、数据范围、样本范围、任务参数、输入 raw_ids、状态；后续批次有算法文件摘要 |
| reconstructed_limits | run_id + code + date | 每只样本逐日 up/not_up/suspended/no_limit/unknown/missing、原因、源 daily_id、规则价、连板高度、可追溯首板日 |
| reconstruction_daily | run_id + date | 历史目录应有数、选择数、收到数、可判定数、未知数、覆盖率、样本涨停数、最高连板、晋级率和质量 |
| reconstruction_validations | run_id + date + reference_raw_id | AKShare 对照指标、未覆盖、误报、漏报、连板差异及证据 JSON |

重建表没有伪造的盘中观测；统一 API 中封板时间、次数、金额显式 NULL。月度统计不跨批次重复累加；研究需保存固定 run_id。

## 最终研究发布 `research-final-v1`（2026-11-07）

入口为仓库外 `~/Datasets/asharedragon/research/unified/manifest.json`。它原子指向一个不可变快照，快照内 `research.duckdb` 提供 Parquet 视图。所有文件有 SHA-256；`inputs.json` 记录输入路径和摘要，`archive_inventory.json` 记录本地 Tushare 归档。构建只读 PostgreSQL，不改源表。数据库文件中的 Parquet 路径绑定当前快照绝对位置；迁移机器后须用离线构建重新生成目录，不可只移动 DuckDB 文件。

| 表/视图 | 主键或粒度 | 语义 |
|---|---|---|
| stock_daily | stock_code + trade_date | 年/月分区的统一日级表；stock_day_id=`day:代码:日期`；包含 OHLCV、事件摘要、估值、ST/交易状态、情绪引用、各用途质量 |
| price_candidates / price_conflicts | 来源股票日 / 股票日 | 原价候选与冲突审计；不覆盖原始 Parquet |
| events | event_id | 来源+接口+股票+日期+事件类型；保留 pg id/raw_id；日级选择仅是摘要，不能代替事件表 |
| selected_events / event_conflicts | 股票日 | 决定日级事件摘要；相互矛盾的事件状态保持 unknown |
| cycles | cycle_id | 最新 v0.5.6 的 8,309 个观测周期；完整周期与边界未知周期分开；不与 legacy_cycles 相加 |
| streak_facts / cycle_days | fact_id / cycle_id+日期 | 同一最新周期的完整证据和逐日进程，非额外样本 |
| labels | label_id | cycle_id + reached_5；复用已有五交易日结果定义；success/failure/unknown 与质量分别存储 |
| first_board_samples | sample_id=`first:代码:日期` | 来源报告连板数为1的收盘首板样本；无未来结果字段，不等于所有样本首板边界均独立核验 |
| sample_cycles / sample_labels | sample_id+cycle_id / sample_id+threshold | 样本与周期关联；无唯一周期结果时显式 unknown |
| preboard_features | cycle_id | v0.6 已有首板前特征及 cutoff、缺失原因、来源；排除 outcome、未来高度和结果筛选标志；母体仍是既有完整周期，存在选择偏差 |
| legacy_cycles | 原始旧版 cycle_id | 旧 v1 诊断结果，仅追溯，不能与最新周期混合计数 |
| historical_stock_status / security_aliases | 有效期证据 / 身份有效期 | 保留原始多条证据；resolved_historical_status 经有效期与身份关联后合并，冲突不填状态 |
| day_evidence | 股票日 | v0.5.6 优先、先前事实为补充；收盘状态证据独立于 ST 和数值涨停价 |
| market_sentiment | sentiment_id | 来源+日期；日表仅关联选择的来源，不重复扩增股票日 |
| pg_themes / pg_stock_themes | theme_id / pg id | 原有题材与有效期关系；目前空表，禁止用现有题材倒填历史 |
| pg_stock_daily / pg_limitup_events / pg_raw_links | PostgreSQL 原 id | 本地一致性快照；`supplement_pg_id`、`event_id` 对应记录中的 id/raw_id 可回查原 PostgreSQL |
| cross_qqq / cross_qqq_actions / cross_us_calendar | 原始美股粒度 | QQQ、公司行动和美股日历；不按同名日期强行连接 A 股 |
| cross_aligned_diagnostics / cross_sentiment | 原跨市场研究粒度 | 保留既有诊断及可得时间约束，不扩展研究指标 |
| yearly_coverage | year | 交易日期覆盖与已知生命周期股票日覆盖分别计算 |

字段单位继承原规范：价格人民币元，成交量股，金额/市值/封单金额人民币元，换手率为百分数点；时间为上海本地板内时间，`*_utc` 为 UTC。PE TTM/PB 不由当前估值回填。`official_limit_up_price`、`official_limit_down_price` 没有独立数值证据即 NULL，不以收盘价冒充官方限价。

来源优先级固定如下：

- OHLCV 作为整组：已验货购买日线 > 已验货 AKShare 补充 Parquet > 本地 BaoStock > 本地 AKShare PostgreSQL。同层按固定来源名与最新记录选择；不同来源 OHLC 不一致记录到 price_conflicts，并降低该日价格质量。
- 同源事件先取最新有记录的成功原始响应；摘要优先收盘涨停、跌停、炸板，再按 Tushare > AKShare。不同事件状态或独立收盘证据冲突，`is_limit_up=NULL`、`limit_quality=unknown`；连板高度冲突独立置空。
- PE/PB 使用同日 BaoStock 优先的 PostgreSQL 补充。市值/换手率先补充日线，空值再用所选同日事件；通过 supplement_pg_id、supplement_raw_id、event_id、event_raw_id 和来源表逐字段追溯。无需丢弃其他有效字段。
- ST/交易状态先使用按身份和日期合并的历史证据，再使用当日补充和状态快照；不按股票名称跨期推断。历史证据冲突保留 unknown。
- 情绪按 Tushare > AKShare、同来源最新计算时间；数据不足的全市场解释仍为 diagnostic。

质量按用途定义，不能互相替代：`price_quality=verified` 表示未复权 OHLCV 结构与来源一致性可用，不表示已复权收益可信；`limit_quality=verified` 表示该日收盘状态有直接来源或已审定证据，不表示拥有数值官方限价；`st_quality=verified` 仅针对当日 ST 事实。估值与情绪保留 diagnostic/unknown。标签 `verified` 仅在最新既有证据满足局部五交易日结果定义时使用，**不是全市场年度统计认证**；`formal_eligible` 仍为 false。

首板收盘样本与 `preboard_features` 不混用：前者含首板日 EOD 字段，不能作为首板日盘前特征；后者仅保留既有 cutoff 之前的窗口，仍不证明供应商历史 PIT 发布版本。未来结果只在 labels/sample_labels，需显式连接。`outcome_known_at_utc` 是证据确认时间，不能倒填成首板可知信息。unknown 既不算 failure，也不进入已知结果成功率分母。
