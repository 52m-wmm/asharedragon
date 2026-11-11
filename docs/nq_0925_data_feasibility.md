# 历史 NQ 北京时间 09:25 行情：可行性、来源与成本

本次核查对应 AShareDragon 的 2024—2026 首次择时实验，研究数据截止 2026-11-10。本文件只记录公开文档和本地只读检查；没有注册账户、购买、下载行情、调用收费 API 或读取/输出凭证。价格是本次访问的厂商网页报价，不是已经取得的订单或账单。

**结论：技术上可行，但本地尚无可验收的 NQ 盘中数据。优先评估 Databento 按需请求报价；FinzData 是需要样本核验的低成本候选；IBKR 不适合单独补全整个 2024 年。NQ 保持独立后续输入，不阻塞当前 A 股实验。**

## 1. 本地已有与缺失

只读检索 `backend/`、`scripts/`、`docs/`、`data/` 和 `~/Datasets/asharedragon` 的 NQ、Nasdaq futures、Databento、期货关键词及文件名。已有的是 QQQ 日线/公司行动和旧的跨市场可行性笔记，没有发现具备 NQ 合约、盘中时刻与价格字段的历史资产。

- `backend/app/api/us_signals.py` 的 `/api/research/us/nq` 目前明确返回 `data=null`、`coverage_status=unavailable`，没有回退成 QQQ 或当日收盘。
- `docs/us_market_sources.md`、`docs/us_market_signals.md` 已说明连续合约和盘中时点缺口；本轮不修改这些既有文件。
- 数据目录中名称含 `nasdaq_history.json` 的文件属于既有 QQQ 原始响应，文件名不能证明持有 NQ。没有逐个读取私人凭证或配置。

这是本地搜索及代码检查结果，不是对任何外部账户已购权限的判断。

## 2. “09:25”必须固定成什么数据

建议把研究字段定义成 **A 股交易日 T 的 Asia/Shanghai 09:25:00 截止时刻前，选定实际 NQ 合约的最近已完成一分钟 bar 收盘**，同时保留该 bar 的起止时间、真实合约、距截止的秒数和缺失原因。若要声称“09:25:00 当时的可成交价格”，则需该时刻前的逐笔/BBO 以及接收时刻；一分钟收盘只是最近成交的代理，不是保证可成交报价。

本地 Python `zoneinfo` 实算：

| A 股截止时刻 | UTC | America/Chicago | America/New_York |
|---|---|---|---|
| 2024-01-02 09:25 | 2024-01-02 01:25 | 2024-01-01 19:25 CST | 2024-01-01 20:25 EST |
| 2024-03-11 09:25 | 2024-03-11 01:25 | 2024-03-10 20:25 CDT | 2024-03-10 21:25 EDT |
| 2024-11-04 09:25 | 2024-11-04 01:25 | 2024-11-03 19:25 CST | 2024-11-03 20:25 EST |
| 2026-03-09 09:25 | 2026-03-09 01:25 | 2026-03-08 20:25 CDT | 2026-03-08 21:25 EDT |
| 2026-11-02 09:25 | 2026-11-02 01:25 | 2026-11-01 19:25 CST | 2026-11-01 20:25 EST |

这些只是时区转换测试，不代表所列时刻一定开市。北京无季节偏移，UTC 始终为 01:25；芝加哥是前一自然日的 19:25 或 20:25。交易所 session date 又可能不同于当地自然日，必须由历史交易日历确认。CME 发布产品交易时间和假日/提前收市安排，不能以美股现货日历代替期货日历。[CME 交易时间与假日](https://www.cmegroup.com/trading-hours.html)

Databento 的 OHLCV 时间戳标记区间开始，无成交的区间不产生 bar。因此标为北京时间 **09:25 的一分钟 bar 包含 09:25—09:26 的未来信息**，不能作为 09:25 截止信号；通常最后完整 bar 为 `[09:24,09:25)`。严格可得性还要考虑聚合完成/发布延迟：不能仅因 bar 结束等于 cutoff 就断言它在该毫秒已发布。若没有逐笔接收或历史发布证据，可额外留一整分钟延迟并将其标为假设；不能称为经认证的 PIT 数据。[Databento OHLCV 时间戳与聚合约定](https://databento.com/docs/schemas/ohlcv)

无 bar 时先区分休市、低成交和供应商缺口。保留陈旧时长；禁止跨周末/假日无上限向前填充，也禁止用下一笔成交补回截止价格。FinzData 的盘中时间戳采用美国东部时间的 bar 起点，若采用该源，应先用 `America/New_York` 解析后转 UTC，而不是把字符串当北京或芝加哥时间。[FinzData NQ 产品说明](https://finzdata.com/futures/nq)

## 3. 可用来源和已核实报价

| 来源 | 本任务适配性 | 当前公开成本/边界 | 结论 |
|---|---|---|---|
| CME DataMine | 可提供成交和 BBO；交易所直接历史来源，适合建立严格 as-of 记录 | 官方说明按数据类型和请求时长定价；本次未得到 NQ 2024—2026 的具体总价，应在 DataMine 选择产品或询价。不能报成免费或某个固定美元数 | 可信来源路线，成本待报价 |
| Databento `GLBX.MDP3` | 文档覆盖 CME 期货及期权历史，自2010年6月起；可选择分钟线或逐笔/BBO | 历史按用量收费且不强制订阅；官网列新用户 $125 额度，资格/授权须核实。CME Standard 网页价 $199/月，与按需购买是不同方案 | 本任务优先做小窗口、具体合约的按需报价；不必直接订阅月套餐 |
| IBKR TWS API | 有授权时可取历史期货；可指定实际合约和 `includeExpired` | 官网页列非专业 CME L1 $1.55/月、L2 $12.10/月；这是市场数据订阅费，不是三年历史档案报价，仍受账户资格、权限和 API 限制。过期期货仅能回溯到期后两年 | 到2026年11月，2024年早期到期合约已超界，不能单独完成全段 |
| FinzData NQ 单市场包 | 厂商声明包含单合约、连续序列及1分钟等盘中；NQ分钟历史自2007年9月起 | NQ 当前产品页为 **$49 一次性**；更新为可选 **$19.95/月**，页面提示默认放入购物车。免费层只有最近30日日线，不含本任务需要的长期分钟线 | 可作低成本备选；本次未实际验证数据、夜盘完整性、许可或历史修订 |

CME 成本依据：[CME 历史服务](https://www.cmegroup.com/market-data/real-time-and-historical-data.html)、[官方 DataMine 支持/价格说明](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457319709/CME+DataMine+Support)。

Databento 依据：[GLBX.MDP3 历史范围](https://databento.com/docs/datasets/glbx-mdp3)、[当前价格页面](https://databento.com/pricing)。页面初始化的“0 products / <$0.01”不是 NQ 报价；本次没有认证账户下的 `get_cost` 结果，不给出伪精确总费用。$125 额度也不能先验保证足够覆盖所选逐笔/盘口范围。

IBKR 依据：[官网市场数据价格及脚注](https://www.interactivebrokers.com/en/pricing/market-data-pricing.php)、[历史数据可用性限制](https://www.interactivebrokers.com/docs/tws-api/doc/md/market-data-limitations)、[实际合约与过期期货规则](https://www.interactivebrokers.com/docs/tws-api/contracts/futures)。其 continuous futures 不能指定任意历史 endDateTime，不能靠连续代码绕过过期期货保留期。

FinzData 依据：[NQ 产品页](https://finzdata.com/futures/nq)、[期货定价与范围](https://finzdata.com/futures-data)。搜索引擎摘要仍出现过期的 $19 单市场价格；本报告采用实际打开产品页显示的 $49。产品页的日线末日显示 2026-11-05，盘中末日未单独明确，因此 **不能据此声称已经覆盖到项目所需2026-11-10**，末端及09:25夜盘覆盖须另验。网页标价未核实税费、结算币种附加费、用户许可、再分发资格或当前用户适用性。

## 4. 连续合约与收益口径

NQ 是实际期货品种，不能用 Nasdaq-100 现货指数、QQQ、MNQ 或 NAS100 CFD 替代后保留“NQ”名称。

首选下载实际到期月份合约，按**截止时刻之前就固定**的换月规则选择当日合约。若用成交量选主力，只能用此前已经完成 session 的成交量；不得用当日全天最终成交量决定09:25的合约。每条记录保存 `raw_symbol/instrument_id`、到期日、映射依据和决策时间。Databento 的 continuous symbology 映射到实际合约，返回的是原始未调整价格；它不是自动去除跳空的后复权连续曲线。[Databento 连续合约定义](https://databento.com/docs/examples/symbology/continuous)

FinzData 提供差额后调连续序列和未调整选择；其默认连续价格可能随后来的换月差额改变历史尺度。不要直接用今天的后调曲线模拟当年实际09:25报价。应选单合约原始价格，核验其 roll calendar 的生成依据；若将两天不同合约价格相除，跨月价差不应冒充市场收益。[FinzData 连续序列口径](https://finzdata.com/futures-data)

本项目未来应区分两个问题：

1. NQ 当时的跨市场信号，例如同一实际合约相对前一已完成时段的变动。
2. NQ 本身可执行的收益：需要买卖方向、bid/ask或成交代理、换月交易及成本，不能拿连续合约收盘涨跌直接作策略收益。

以上是研究设计约束，不是本轮已经计算的交易回报。

## 5. 最小后续取样与预算流程（未执行）

1. 固定 A 股690个交易日的09:25截止表；按历史CME状态对齐，保留休市/缺失。先选约12个日期：普通日、2024/2025/2026美国夏令时切换后首个A股日、季度换月附近、美国假日与中美休市错位。
2. 请求这些日期截止前10—15分钟的真实合约分钟线；另取少数日期逐笔或BBO进行09:24 bar与截止前最后成交复核。分钟数据不足以证明接收时点时，将结果继续标为可得性假设。
3. 对 Databento，先用 `metadata.get_cost(dataset='GLBX.MDP3', schema='ohlcv-1m', symbols=[具体NQ合约], start=..., end=...)` 获取美元报价，逐请求汇总并另核许可。文档提醒非10分钟边界的估价可能偏高，报价与实际按传输字节结算不同；可以用对齐边界估预算，但研究字段仍必须严格截断在cutoff前。[get_cost 官方参数与计价说明](https://databento.com/docs/api-historical/metadata/metadata-get-cost)
4. 若评估 FinzData，先让供应商提供上述日期的实际单合约夜盘样本与字段/许可说明；免费日线不能完成这项验收。先验证报价 $49 是否适用于所需范围，并明确不要默认添加持续更新服务。本轮没有发出询价或发送消息。
5. 样本通过再决定是否获取690日：至少核验实际合约、DST、bar起止、夜盘、缺失/陈旧率、价格档位、到期及换月、历史修订与可得时间。保存原始文件、SHA-256、来源/许可、取数参数、取得时间和归档版本。

**本轮成本实际支出为0。** 不能据这些网页推断 NQ 09:25 对 A 股择时有用；取得合格数据后仍需独立的时间留出和多重检验。现有 A 股实验继续运行，NQ 不参与本轮特征、不触发任何数据采购或 active 切换。
