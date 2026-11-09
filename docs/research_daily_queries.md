# 历史日线研究查询

这些接口增量加入现有 FastAPI，不改变 Next.js 架构，不生成正式龙头标签，也不会访问 Tushare 代理。

研究目录位于 `~/Datasets/asharedragon/research/`。`catalog.json` 仅列入通过验证的 Parquet 来源，保留每行的 `source`。购买数据与免费补采数据独立保存；未经显式对账，不允许跨来源出现相同股票、日期的重复记录。

## 接口

| GET 路径 | 参数 | 语义 |
| --- | --- | --- |
| `/api/research/daily` | `code,start,end` | 个股日线、已知上市退市区间、缺失交易日 |
| `/api/research/market` | `trade_date,page,page_size` | 当日沪深行情，已知在市目录分母、缺失代码 |
| `/api/research/preboard` | `code,event_date` | 事件日前 5/10/20 个交易所交易日窗口，不包括事件日 |
| `/api/research/coverage` | `start,end` | 月度日期覆盖、股票交易日覆盖、缺失月份 |
| `/api/research/events` | `start,end,code?,page,page_size` | 本地 Tushare 事件左连接日线，保留无日线的事件 |

例：

```bash
curl 'http://127.0.0.1:8000/api/research/daily?code=002432&start=2026-03-01&end=2026-11-06'
curl 'http://127.0.0.1:8000/api/research/market?trade_date=2026-11-06&page_size=100'
curl 'http://127.0.0.1:8000/api/research/preboard?code=002432&event_date=2020-12-01'
curl 'http://127.0.0.1:8000/api/research/coverage?start=2017-01-01&end=2026-11-06'
curl 'http://127.0.0.1:8000/api/research/events?start=2020-01-01&end=2020-12-31&code=002432'
```

日期范围由本地验货目录限定，越界返回 422。未生成研究目录返回 503，不回退到模拟数据。股票代码使用项目既有标准化规则，保留前导零。市场与事件查询支持分页，单页最多 1,000 行。

## 覆盖语义

每个响应均有 `meta`，包括目录快照时间、研究范围、生命周期证据、北交所独立状态和缺失提示。

- 月份完全没有数据时，`rows`、`stocks` 为 `null`，`coverage_status=missing`。`covered_days=0` 表示采集覆盖为零，不表示实际成交或市场股票数为零。
- `date_coverage` 只是当日有行情的日期覆盖率。`known_catalogue_coverage` 才比较已知生命周期内的股票交易日对，仍受目录完整性及停牌缺行限制；不会自动声称全市场完整。
- `missing_active_dates` 不包含已知上市前、退市后的日期。退市日边界按现有项目的闭区间处理，边界缺行还需核对退市生效与最后交易日定义。
- 只有原始来源明确给出状态，才显示 `trade_status` / `is_st`。可从本地 BaoStock 同代码、同日事实补充，并通过 `status_source` 显示出处。AKShare 日线缺行不被推断为停牌，缺失状态为 `null`。
- 首板窗口按交易所日历取前 N 日，而不是跳过缺行取 N 条观测。窗口不完整时，`features=null`；完整时返回均量、均额、最高价、最低价。不进行前向填充。
- `first_board_confirmed_by_local_tushare` 只表示本地事件中该日明确为涨停且连板数为 1。无此证据的日期仍可作为候选日期取窗口，但标记 `unconfirmed_candidate_date`，不创造首板或龙头标签。
- Tushare 事件范围会单独显示。没有事件记录不等于当天没有涨停。事件以左连接关联日线，缺少价格会明确显示 `daily_missing`。
- OHLC 为未复权价格，不据此自动生成复权收益、涨停价或可投资收益标签。历史公司行动与特殊交易状态仍需独立核验。

## 本地执行与恢复

在项目根目录，使用已有 `.venv`：

```bash
# 固定本轮窗口 2026-03-16—2026-11-06；先过 10 股、10 重叠交易日门槛
.venv/bin/python scripts/probe_daily_extension.py
# 每个股票一个原子检查点，已完成不重复请求；连续失败触发暂停
.venv/bin/python scripts/extend_daily_2026.py --workers 4
# 只读 PostgreSQL 快照 + 本地日线质量、目录覆盖审计；刷新查询目录
.venv/bin/python scripts/audit_daily_coverage.py
```

不要同时运行两个补采进程。脚本用文件锁保护采集，缓存与进度在仓库外 `~/Datasets/asharedragon/staging/stocks/daily/akshare_em_unadjusted_20260316_20261106/`。失败股票没有成功缓存，恢复时重新请求；空响应保留为 `empty_unverified`，不会伪造价格或股票状态。

原始接口返回和样本证据在被 Git 忽略的 `data/daily_extension/` 及仓库外 staging 中。正式月度 Parquet 在独立来源目录中，购买分区保持不变。`research/missing_stock_dates.parquet` 保存逐股逐日缺口及能确认的停牌原因，`missing_stocks_summary.parquet` 提供逐股汇总；这些是本地研究数据，不上传 GitHub。

本轮脚本固定了日期窗口及口径，后续扩大窗口应新建独立批次并重做重叠验证，不应修改既有来源的身份标记。

## 历史涨停规则及因子使用门槛（2026-11-07）

研究查询metadata现在包含 `factor_policy`：购买数据的 `adj_factor` 暂未认证，不得用于正式复权收益、复权均线或回测；OHLC与原始涨停事件仍可正常查询。原始因子保留供审计，异常标记存于独立研究目录。

新规则、L1门槛及两组真实小样本验证见[历史涨停规则验收](historical_limit_validation.md)。本轮正式L1为0条；高诊断Precision不能代替完整状态、限价和覆盖认证，旧版observed_first_board或algorithm_reconstructed也不自动升级为正式标签。
