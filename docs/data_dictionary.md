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
