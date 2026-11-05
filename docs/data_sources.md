# 数据源验证报告

v0.2 新增 BaoStock 的接口、单位、退市范围与访问限制见 [免费历史来源说明](history_v02.md)，实测覆盖与准确率见 [v0.2 验收报告](validation_v02.md)。以下保留 v0.1 的 AKShare / Tushare 验证记录。

本机实测：2026-11-04（America/Los_Angeles）；对应采集时间 2026-11-05 上海下午。最近完成交易日为 2026-11-04。AKShare 1.20.27。所有市场数字来自实际返回；网络失败不等于接口永久不可用。

## 结论

- 近期涨停/炸板/跌停池、昨日涨停池、个股未复权日线、交易日历可用。已按源日历采集 2026-10-26 至 2026-11-04 的 8 个交易日，实际数据在 PostgreSQL。
- 2024-01-15、2022-06-15 的涨停池均为空；2026-09-30、10-09、10-12 的抽样亦为空，不能用于旧历史研究。空表已明确标记未确认，旧年份缺口也记入数据库。
- 本轮最早成功的涨停池抽样为 2026-10-16（61 条）；10-13/14/15 的请求发生超时或 SSL 错误，所以**不能断言 10-16 是硬性最早边界**。最早支持日期随服务变化，仍需未来复核。
- 已安装源码中炸板/跌停接口含最近窗口限制；炸板/跌停提示“30 个交易日”，实际检查使用 `datetime.now() - timedelta(days=30)`，这是自然日判断。涨停池没有同样的本地检查，也不表示支持任意历史日期。
- 全市场现货和人工智能概念成分探测超时；概念名单、龙虎榜成功。它们仅用于探测，未作为历史事实灌入其他年份。

## 请求结果（实际耗时，秒）

| 接口 | 参数 | 状态 | 行数 | 耗时 | 原始证据 |
|---|---|---|---:|---:|---|
| `stock_zt_pool_em` | `{"date": "20261104"}` | success | 118 | 2.21 | `data/probes/stock_zt_pool_em_20261104.json` |
| `stock_zt_pool_em` | `{"date": "20240115"}` | empty_unverified | 0 | 2.2 | `data/probes/stock_zt_pool_em_20240115.json` |
| `stock_zt_pool_em` | `{"date": "20220615"}` | empty_unverified | 0 | 2.36 | `data/probes/stock_zt_pool_em_20220615.json` |
| `stock_zh_a_hist` | `{"symbol": "000001", "period": "daily", "start_date": "20240102", "end_date": "20240131", "adjust": ""}` | success | 22 | 2.08 | `data/probes/stock_zh_a_hist_20240102.json` |
| `tool_trade_date_hist_sina` | `{}` | success | 8797 | 2.25 | `data/probes/tool_trade_date_hist_sina_calendar.json` |
| `stock_zt_pool_previous_em` | `{"date": "20261104"}` | success | 48 | 3.61 | `data/probes/extended_stock_zt_pool_previous_em_20261104.json` |
| `stock_zh_a_spot_em` | `{}` | failed | — | 31.03 | `data/probes/extended_stock_zh_a_spot_em_snapshot.json` |
| `stock_board_concept_name_em` | `{}` | success | 531 | 22.75 | `data/probes/extended_stock_board_concept_name_em_snapshot.json` |
| `stock_board_concept_cons_em` | `{"symbol": "人工智能"}` | failed | — | 31.03 | `data/probes/extended_stock_board_concept_cons_em_snapshot.json` |
| `stock_lhb_detail_em` | `{"start_date": "20261104", "end_date": "20261104"}` | success | 84 | 5.38 | `data/probes/extended_stock_lhb_detail_em_snapshot.json` |
| `stock_zt_pool_em` | `{"date": "20260930"}` | empty_unverified | 0 | 3.28 | `data/probes/extended_stock_zt_pool_em_20260930.json` |
| `stock_zt_pool_em` | `{"date": "20261009"}` | empty_unverified | 0 | 3.33 | `data/probes/extended_stock_zt_pool_em_20261009.json` |
| `stock_zt_pool_em` | `{"date": "20261012"}` | empty_unverified | 0 | 3.6 | `data/probes/boundary_20261012.json` |
| `stock_zt_pool_em` | `{"date": "20261016"}` | success | 61 | 3.38 | `data/probes/boundary_20261016.json` |
| `stock_zt_pool_em` | `{"date": "20261019"}` | success | 46 | 3.28 | `data/probes/boundary_20261019.json` |
| `stock_zt_pool_em` | `{"date": "20261020"}` | success | 54 | 3.25 | `data/probes/boundary_20261020.json` |
| `stock_zt_pool_em` | `{"date": "20261021"}` | success | 50 | 3.21 | `data/probes/boundary_20261021.json` |
| `stock_zt_pool_em` | `{"date": "20261022"}` | success | 33 | 3.36 | `data/probes/boundary_20261022.json` |
| `stock_zt_pool_em` | `{"date": "20261023"}` | success | 82 | 3.4 | `data/probes/boundary_20261023.json` |
| `stock_zt_pool_em` | `{"date": "20261013"}` | failed | — | 11.7 | `data/probes/boundary_20261013.json` |
| `stock_zt_pool_em` | `{"date": "20261014"}` | failed | — | 6.38 | `data/probes/boundary_20261014.json` |
| `stock_zt_pool_em` | `{"date": "20261015"}` | failed | — | 11.39 | `data/probes/boundary_20261015.json` |

## 安装版本实际签名

- `stock_zt_pool_em(date: str = '20241008') -> pandas.DataFrame`
- `stock_zt_pool_zbgc_em(date: str = '20241011') -> pandas.DataFrame`
- `stock_zt_pool_dtgc_em(date: str = '20241011') -> pandas.DataFrame`
- `stock_zt_pool_previous_em(date: str = '20240415') -> pandas.DataFrame`
- `stock_zh_a_hist(symbol: str = '000001', period: str = 'daily', start_date: str = '19700101', end_date: str = '20500101', adjust: str = '') -> pandas.DataFrame`
- `stock_zh_a_spot_em() -> pandas.DataFrame`
- `stock_board_concept_name_em() -> pandas.DataFrame`
- `stock_board_concept_cons_em(symbol: str = '融资融券') -> pandas.DataFrame`
- `stock_lhb_detail_em(start_date: str = '20230403', end_date: str = '20230417') -> pandas.DataFrame`
- `tool_trade_date_hist_sina() -> pandas.DataFrame`

## 返回字段（成功探测）

- `stock_zt_pool_em`：序号、代码、名称、涨跌幅、最新价、成交额、流通市值、总市值、换手率、封板资金、首次封板时间、最后封板时间、炸板次数、涨停统计、连板数、所属行业
- `stock_zh_a_hist`：日期、股票代码、开盘、收盘、最高、最低、成交量、成交额、振幅、涨跌幅、涨跌额、换手率
- `tool_trade_date_hist_sina`：trade_date
- `stock_zt_pool_previous_em`：序号、代码、名称、涨跌幅、最新价、涨停价、成交额、流通市值、总市值、换手率、涨速、振幅、昨日封板时间、昨日连板数、涨停统计、所属行业
- `stock_board_concept_name_em`：排名、板块名称、板块代码、最新价、涨跌额、涨跌幅、总市值、换手率、上涨家数、下跌家数、领涨股票、领涨股票-涨跌幅、领涨股票-代码
- `stock_lhb_detail_em`：序号、代码、名称、上榜日、解读、收盘价、涨跌幅、龙虎榜净买额、龙虎榜买入额、龙虎榜卖出额、龙虎榜成交额、市场总成交额、净买额占总成交比、成交额占总成交比、换手率、流通市值、上榜原因、上榜后1日、上榜后2日、上榜后5日、上榜后10日
- `stock_zt_pool_zbgc_em`：序号、代码、名称、涨跌幅、最新价、涨停价、成交额、流通市值、总市值、换手率、涨速、首次封板时间、炸板次数、涨停统计、振幅、所属行业
- `stock_zt_pool_dtgc_em`：序号、代码、名称、涨跌幅、最新价、成交额、流通市值、总市值、动态市盈率、换手率、封单资金、最后封板时间、板上成交额、连续跌停、开板次数、所属行业

## 实际入库的 8 个交易日

| 日期 | 涨停 | 炸板 | 跌停 | 二板晋级率 |
|---|---:|---:|---:|---:|
| 2026-10-26 | 55 | 23 | 26 | NULL |
| 2026-10-27 | 41 | 28 | 22 | 0.2 |
| 2026-10-28 | 72 | 69 | 5 | 0.1724137931034483 |
| 2026-10-29 | 38 | 25 | 41 | 0.09375 |
| 2026-10-30 | 47 | 31 | 82 | 0.21428571428571427 |
| 2026-11-02 | 125 | 28 | 8 | 0.15789473684210525 |
| 2026-11-03 | 48 | 34 | 17 | 0.10526315789473684 |
| 2026-11-04 | 118 | 33 | 10 | 0.18181818181818182 |

## 数据质量与缺失字段

2026-10-29 涨停源返回 39 行，其中澳洋健康 002172 重复两行，仅“序号”不同；按事实字段完全一致去重后 38 只。初次严格校验失败的原始记录和任务保留，随后成功补采。发生字段冲突的重复会拒绝整个分区。
涨停池缺少可靠历史 PE TTM/PB、自由流通市值、官方涨停价、一字/T 字标识、封单股数、涨停原因和公开时间，均保留 NULL。日线缺少市值和估值，不能用现货快照填充过去。龙虎榜的上榜后收益属于未来结果，不能进入上榜当日预测特征。
本版原始证据为 AKShare 返回的 DataFrame JSON，包含参数、签名、字段、版本及采集时刻；不是保全 HTTP 原始字节。探测文件位于本机 data/probes（Git 忽略），可重跑脚本；以下归档只保留小体积元数据。

## Tushare Pro（可选，尚无 Token）

通用 `Client("tushare").fetch(endpoint, **params)` 适配器使用官方 SDK 的 query。未提供 Token，未执行账户权限验证、真实返回验证和标准化入库，不宣称接口已可用。

| 接口 | 核验重点 / 官方说明 | 当前状态 |
|---|---|---|
| limit_list_d | 官方说明历史自 2020 年起，不含 ST，当前说明至少 5000 积分 | 未授权实测 |
| limit_list_ths | 官方说明历史自 2023-11-01，当前说明至少 8000 积分 | 未授权实测 |
| daily | 未复权行情；成交量手、成交额千元需转换 | 未授权实测 |
| daily_basic | 每日估值与市值；万元字段需转换为元 | 未授权实测 |
| stk_limit | 优先读取官方历史涨跌停价，不能靠涨幅阈值 | 未授权实测 |
| stock_basic | 需含上市、暂停、退市样本及历史状态，当前名单不等于历史全集 | 未授权实测 |
| trade_cal | 按交易所验证日历及权限 | 未授权实测 |

官方资料：[AKShare 股票行情](https://akshare.akfamily.xyz/data/stock/stock_quote.html)、[Tushare limit_list_d](https://tushare.pro/document/2?doc_id=298)、[Tushare limit_list_ths](https://tushare.pro/document/2?doc_id=355)。账户实际权限以持有 Token 的返回为准。

## 重现

```bash
.venv/bin/python scripts/probe_sources.py --recent 20261104
.venv/bin/python scripts/probe_extended.py
.venv/bin/python scripts/report_sources.py
```

边界探测原始文件记录每次实际请求的日期、返回和错误。滚动窗口会变化，未来复跑同日可能已经不可得。真实接口集成验收记录见 validation.md。
