# 美股跨市场研究：一手来源与 NQ 可行性

核查日期：2026-11-07。本文仅记录公开文档调查，不代表已经取得或验证完整 NQ 行情。没有注册付费账户、购买数据或绕过访问限制。QQQ 实际下载、覆盖及实验结果以 `us_market_signals.md` 为准。

## NQ：本轮不作为开盘前信号输入

能够取得可靠的长期 NQ 数据在技术上可行，但本次没有核实到无需凭证、可免费下载且覆盖 2017 年至今的完整历史盘中数据。CME 将历史数据访问导向 DataMine 购买渠道；Databento 公布 CME 长期历史及秒、分钟、小时、日线服务，采用按用量收费或订阅，新用户额度也需要账户，不能据此宣称永久免费或本项目已经取得覆盖。[CME 历史数据服务](https://www.cmegroup.com/market-data/real-time-and-historical-data.html)、[Databento 价格与历史范围](https://databento.com/pricing)

| 类型 | 必须保留的含义 | 本轮判断 |
| --- | --- | --- |
| 单合约日线 | 明确到期月份、合约标识、交易时段；区分 OHLC 与结算价 | 是后续可信数据的基础，但尚未采集 |
| 近月合约 | 按到期次序选择，不必是最活跃合约 | 需要公开且版本固定的换月规则 |
| 主力/活跃连续合约 | 按流动性或供应商规则拼接多个合约 | 必须保留每日实际合约；不能默认等于近月 |
| 连续调整价格 | 换月时差额/比例调整会改变历史价格尺度 | 仅作派生研究序列，原始合约价格另存 |
| 北京时间 09:30 前快照 | 截止前最后已发布的真实交易/报价或已完成 bar | 本轮缺失；绝不以当日收盘或结算代替 |

CME 的官方连续序列以结算价构建，分别提供 Active 和 Front：Front 在到期前两个工作日切换，Active 按历史换月与流动性特征选择；不是统一意义的可成交盘中价格。官方页面列明实时渠道需要相关许可，历史文件通过 DataMine 获取。[CME Continuous Price Series](https://www.cmegroup.com/market-data/cme-group-continuous-price-series.html)

“连续”本身也不表示“后复权”：Databento 的连续合约示例明确提供原始未调整价格。因此供应商的连续代码必须与其换月及调整文档一起审查；本项目不能靠 `NQ` 或某个连续代码名称推断口径。[Databento 连续合约文档](https://databento.com/docs/examples/symbology/continuous)

结算价是规则计算结果，与某一时刻最后成交不同。CME 结算说明还提示网站部分零成交/零持仓合约结算值不基于交易活动，不能把它们当真实成交。该页面说明每日报告在午夜 CT 发布：换算为北京时间通常是 13:00/14:00，晚于 A 股 09:30。**结算计算时刻不等于这个公开下载渠道的可得时刻。**[CME About Settlements](https://www.cmegroup.com/trading/about-settlements.html)

历史规则也要按生效日期区分：旧 CME 教育资料写 NQ 15:14:30–15:15 CT 结算窗口，当前 CME 客户文档写主力合约 14:59:30–15:00 CT 的成交量加权计算及后备规则。本文没有追溯全部规则变更日期，不能把任一版本直接应用于 2017–2026 全段。[CME 旧教育说明](https://www.cmegroup.com/education/courses/introduction-to-equity-index-products/understanding-equity-index-daily-and-final-settlement)、[CME 当前 Nasdaq-100 结算说明](https://cmegroupclientsite.atlassian.net/wiki/spaces/EPICSANDBOX/pages/457222172/Nasdaq-100)

若以后得到授权历史数据，至少需要实际合约、事件 UTC、可得 UTC、交易所交易日、价格类型、bar 起止时刻、修订版本、换月决策时间及调整方法。北京时间 09:30 为 UTC 01:30；一分钟 bar 的开始时间早于截止还不够，其结束和发布也必须早于截止。换月不能使用截止之后才知道的当日全日成交量。这些是本项目的防泄漏要求，不是对供应商已提供 PIT 档案的断言。

## QQQ：日历、分红与拆分核查

Nasdaq 常规股票交易时段是美东 09:30–16:00，部分假日前提前于 13:00 收盘；官方日历列有正常休市和提前收盘。只用“周一到周五”或固定 UTC 收盘时间不足以建立交易日历。使用 `America/New_York` 将每个本地交易日的实际收盘转为 UTC，并保留本地日期。[Nasdaq 交易时间与休市日历](https://www.nasdaq.com/market-activity/stock-market-holiday-schedule)、[Nasdaq Trader 日历](https://www.nasdaqtrader.com/trader.aspx?id=calendar)

2017 年之后适用的美国夏令时规则为三月第二个星期日开始、十一月第一个星期日结束；2026 年为 3 月 8 日至 11 月 1 日。由此常规收盘是 UTC 20:00 或 21:00，提前收盘是 UTC 17:00 或 18:00。应使用时区数据库转换，而非硬编码每年切换日。[NIST 夏令时规则](https://www.nist.gov/pml/time-and-frequency-division/popular-links/daylight-saving-time-dst)

公开公司行动入口和本次可验证范围：

- [Invesco QQQ 官方产品页](https://www.invesco.com/us/en/financial-products/etfs/invesco-qqq-trust-series-1.html)公开提供分配历史入口；本次正文抓取没有得到完整数据行，不能宣称已获取发行人 2017 年至今完整事件表。
- [Tiingo QQQ 分红页](https://app.tiingo.com/qqq/dividends)的搜索索引展示 ex-date、每股金额、调整后金额等数据，但直接读取为动态页面；是可交叉核验的供应商入口，不能用索引摘要替代完整下载和完整性验证。
- [Twelve Data QQQ 拆分页](https://twelvedata.com/markets/958673/etf/nasdaq/qqq/historical-data/splits)当前公开表仅列 2000-03-20 的 2:1 拆分，未列 2017 年之后事件；这属于供应商表证据，尚不等于发行人全史完整性证明，其拆分 API 也不属于无需凭证免费接口。
- [OCC 官方备忘录 53848](https://infomemo.theocc.com/infomemos?number=53848)明确记录 QQQ 2023-12-27 除息的特殊现金分配，每股 0.21584 美元、登记日 12 月 28 日、支付日 2024 年 1 月 15 日。不能根据每季一次的模式自行生成分红日历。该备忘录是清算机构公司行动摘要，仍须与实际数据交叉核验。

原始价格收益 `close_t / close_prev - 1` 不含现金分配。无拆分时，加入当日除息分配的简单持有期收益可写为 `(close_t + distribution_ex_t) / close_prev - 1`；若发生拆分，要先统一每股尺度。供应商调整收盘收益必须单列口径，不能覆盖原始 OHLC，也不能仅凭序列平滑就称为总回报。Yahoo 自身历史页将 adjusted close 描述为包含拆分及股息/资本利得分配的调整值。[Yahoo QQQ 历史页口径](https://ca.finance.yahoo.com/quote/QQQ/history/)

## 时间可得性与修订限制

按 A 股日期 T 构造带时区的研究截止时刻，只关联在截止前可得的最近已完成美国 session；美国休市时允许最近已完成 session 跨日，但须标出该 session 日期、距截止时长以及休市/数据缺失原因，不能把应有而缺失的日线静默替换为更早日线。A 股当日情绪指标是待解释/预测的结果；用于基准模型的 A 股指标至少滞后一交易日，不能把当日结果放进开盘前特征。

收盘时刻仅证明价格对应的市场时段已经结束，不能证明某免费 API 的最终历史版本当时已经发布。Nasdaq 官方计算文件允许特定取消/更正或异常情况下修正官方开收盘价；今天取得的历史序列可能含后来的修订。因此建议保存 `retrieved_at_utc`、来源响应摘要/哈希、原始版本及 `availability_basis`；用收盘加延迟的假设做初步研究时，明确标为可得时刻假设，不能宣称已拥有逐版本 PIT 数据。[Nasdaq 价格更正规则](https://indexes.nasdaq.com/docs/Calculation_Manual_Equities_and_Commodities.pdf)

本轮合适的结论是：QQQ 可在明确价格口径及时间对齐假设下做描述性和初步预测研究；NQ 开盘前历史快照保持缺失状态，待有可验证的授权盘中来源后再启用。相关性与预测检验均不能推出因果关系或可盈利策略。
