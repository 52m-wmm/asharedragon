# v0.2 免费历史数据：方法、操作与边界

本版在 v0.1 上追加 BaoStock 适配器与独立重建表；原有 AKShare 观测、任务、数据和页面保留。没有引入预测模型。首次升级前已保存本地 `data/backups/asharedragon-before-v02.dump`。

## 供应商实测

安装版本：BaoStock 0.9.4，匿名登录，无 Token。先独立探测，再接入生产采集。

| 接口 | 已验证内容 | 说明 |
|---|---|---|
| query_stock_basic | 9,031 条证券资料，其中 5,565 条可标准化 A 股记录；341 条带退市日期 | 包含指数及其他证券，不能把整个响应当 A 股；退市全集仍以实际源范围为限 |
| query_all_stock(day) | 历史日期名单，2022-01-04 共 5,782 条证券 | 按基本资料 type 排除指数/B 股；保存历史月初/月末名单及原始响应 |
| query_history_k_data_plus | 2022/2024/2025 三个 1 月的真实日线 | adjustflag=3 未复权；有 preclose、volume、amount、turn、peTTM、pbMRQ、isST、tradestatus |
| query_trade_dates | 2020-01-01 至 2026-11-04 返回 2,500 个自然日的交易标记 | 不以工作日猜测假日 |
| query_daily_history_k_AStock | 2024-01-02 返回 5,097 条全市场日线 | 安装版 SDK 的真实接口；全部 adjustflag=3。免费匿名实测成功，单次约 63 秒 |

最初全量资料和名单的 35 秒超时不足以完成分页，后续基础资料约 95 秒成功；全市场接口首次 55 秒超时，扩大有界超时后成功。生产每次有硬超时、失败重试、限速，未做无限等待或并发轰炸。BaoStock SDK 的分页结束若仍停在完整页，会被判为未确认分页，防止静默截断。

源字段单位以安装 SDK/真实返回及接口说明核验：成交量是**股**，成交额是**人民币元**，换手率和 pctChg 是百分数点；PE/PB 无量纲。不能沿用 AKShare 日线成交量乘 100 的转换。空字符串映射 NULL，不把负 PE 清空。停牌状态不从零成交猜测。

真实探测文件：`data/baostock_probes/`（本地 Git 忽略）。数据库 `raw_observations` 保存库版本、调用签名、参数、返回列和逐条原始文本。历史基本资料的“当前名称/状态”按采集日保存，不当作过去名称；上市/退市日期供后验样本边界审计。

## 重建口径

版本 `baostock-close-v2.0`；重建与实际事件分开存储，前端明确标识 `algorithm_reconstructed`。不写入或覆盖 `limitup_events`。

- 只用未复权日线、源前收盘价、当日 isST、上市日期和源交易日历。
- 主板常规 10%；主板 ST 在 2026-07-06 前为 5%，之后为 10%；创业板 2020-08-24 起 20%；科创板 20%。均用 Decimal、0.01 元价格单位和 ROUND_HALF_UP，考虑最小一档变动。
- 科创板、注册制创业板/主板上市前五交易日标记 no_limit；旧核准制 IPO 首日不推算。上市日期缺失、未知板块、未核实复牌特殊期、最终退市前 60 自然日保守排除；这会牺牲部分覆盖，原因保留。
- 北交所未在本轮 BaoStock 可核实股票范围内，不输出未经验证的北交所重建。689 开头等不支持规则的品种明确 unknown。
- 日内最高价超过规则价时标记特殊事件/源异常待查，不把异常行情硬套普通涨停规则。除权参照价和重大特殊事件仍可能造成差异，输出为研究重建结果，非官方价格认证。
- 涨停要求 `close == rule_limit_price`，不使用 9.9% 阈值。封板时间、开板次数、封单金额 API 一律 NULL；不通过 K 线推断一字/T 字、盘中触板或可成交性。
- 连板按连续交易所交易日计算。未知/缺失导致起点及高度未知，直到明确非涨停观测重置。停牌断开本版连板口径；复牌第一日保守 unknown。窗口左侧已在连板时，不将首条记录当首板。
- 默认预热 60 自然日，可用 `--warmup-days 1..120` 改变；短预热导致无法追溯时返回 NULL。首板按确定的第一板日期查询。
- 二板/三板晋级追踪上一交易日同股票队列；前序分类/高度不完整或队列当日不可判定时 NULL，零分母 NULL，不将稀疏样本晋级率当完整市场概率。

规则一手证据：[深交所 2026 交易规则](https://docs.static.szse.cn/www/lawrules/rule/trade/W020260424690713155663.pdf)、[上交所发布的 2026 交易规则通知（巨潮归档）](https://dataclouds.cninfo.com.cn/shxs/laws/spider/2026-04-25/ec4f1dbd34-5eec4f989bf5df0d03fbcf0728275970_2.pdf)、[交易所披露的实施前后 ST 比例公告](https://disc.static.szse.cn/disc/disk03/finalpage/2026-04-30/4a272c2b-fe9f-4126-87bc-b4771a3a3510.PDF)。v0.1 保守规则函数保持兼容；本版重建使用独立版本规则。

## 覆盖与抽样

目标月份先做 24 只分层试采，按板块及已退市资料分组，以代码哈希确定顺序，避免按涨幅选股。退市分层使用事后生命周期信息，仅为数据质量测试，不能作为当时预测特征或无偏选股样本。

每日分母为本供应商历史有效目录中的股票数，而不是“请求的 24 只”。同时记录 selected、received、classified、unknown。覆盖率 = 收到日线的股票交易日 / 目录应有股票交易日。仅已采集且可判定股票贡献涨停事件数。`partial_sample_or_unclassified` 明确表示样本总数，不是全市场总数。月内 IPO 导致每天的选中/应有股票数量变化是正常现象。

市场范围仅为供应商目录，不能证明是交易所完整历史全集。月初/月末历史名单与基本资料交叉检查，历史名单中存在而目录无法解释的 A 股会阻止任务发布“完整”结果。当前依然不提供保证覆盖所有旧退市证券的声明。

## 核验

对照 AKShare 已采集的近期真实收盘涨停池，保留 reference_raw_id 和固定 run_id。

- 准确率 = (TP+TN)/共同可判定股票数；精确率 = TP/(TP+FP)；召回率 = TP/(TP+FN)。
- 共同可判定样本之外的参考涨停股票另列 unassessable_reference；既不当真阴性，也不伪计已确认漏报。
- 同时显示参考池覆盖率及全参考池捕获比例，避免在小样本上的高准确率误导。
- 误报/漏报附股票代码、规则价、BaoStock 收盘/前收、当日 ST、AKShare 池价格及原始来源。价格不一致可明确识别；ST 宇宙差异、制度或参照价分歧未证实时明确标为 unverified/unresolved。
- 连板高度不一致另列，不能以匹配涨停名单等同匹配连板。
- AKShare 是对照源，不是无条件“真值”。独立不可判定事实仍保留，未为了提高指标而按参考池覆盖重建结果。

最终实测数字由 `scripts/report_history.py` 读取数据库生成到 `docs/validation_v02.md`，不手写一个虚构准确率。

## 持久化与恢复

新增迁移 `ce79b407cfc5`：stock_daily 仅新增可空 is_st / trade_status；新增 reconstruction_runs、reconstructed_limits、reconstruction_daily、reconstruction_validations。旧表旧行保留。后续迁移 `5c4817147114` 允许完全未观测交易日的涨停数为 NULL，避免把数据缺失显示为 0。

原始源成功响应缓存按完整参数查询。标准化以 raw_id 幂等；相同响应重复执行不重复插日线。每股或每个市场日独立事务，失败保留已成功分区并写任务/覆盖错误；重跑复用已成功响应。force 重新请求并保留修订，不能清空旧版本。

重建每次产生新的不可变 run，保存源 raw IDs、参数及算法版本；新批次还保存代码摘要。同 run/date/code 唯一。查询默认逐日选择最近完成批次，不累加重跑结果；需要固定样本/可复现研究时指定 run_id。原始库成功回采不意味着当时可见：available_at 未核实时仍为 NULL，原严格时间切片保持不变。

worker 与同步历史 CLI 共用 advisory lock。已有 worker 时同步 CLI 提示使用 --enqueue，避免同时写同一历史分区。网页只提交 24 只试采；完整供应商范围通过显式 CLI 参数开启。

## 运行命令

```bash
# 已有 v0.1 环境增量升级
.venv/bin/pip install -r backend/requirements.lock.txt
.venv/bin/pip install --no-deps -e backend
.venv/bin/alembic -c backend/alembic.ini upgrade head
npm ci --prefix frontend
./scripts/dev.sh

# 单月小样本；已有 worker 时加 --enqueue
.venv/bin/python -m app.history_cli collect --start 2022-01-01 --end 2022-01-31 --sample-size 24
.venv/bin/python -m app.history_cli collect --start 2024-01-01 --end 2024-01-31 --sample-size 24
.venv/bin/python -m app.history_cli collect --start 2025-01-01 --end 2025-01-31 --sample-size 24

# 默认仅输出 2020 至最近已完成日期的月份计划，不采集
.venv/bin/python -m app.history_cli backfill --start 2020-01-01 --end 2026-11-05 --all-stocks --market

# 在至少三个月试采通过、评估覆盖和核验后，显式提交单年队列
.venv/bin/python -m app.history_cli backfill --start 2020-01-01 --end 2020-12-31 --all-stocks --market --execute --enqueue

# 市场接口慢或受限时，可去掉 --market，按股票请求（耗时明显更长）
# 断点恢复：重跑同样命令；--force 才重新请求成功源分区
```

不存在自动执行六年任务的默认入口。分年度/月份观察缺口后再继续下一批。上述日期示例按上海时间完成交易日校验，不接受未来收盘数据。任务启动后接口恢复情况、覆盖率和误差必须继续监控。

## 新 API 与页面

`/api/history/runs`、`summary`、`events`、`first-boards`、`validations` 均采用原有 `{data,meta,error}` 格式。`/api/jobs` 增加 provider=baostock/dataset=history 的单月任务；旧请求默认不变。

原五页增量展示：主页/季节性页月度图和覆盖表；涨停页独立重建名单；个股页首板日期和 BaoStock 历史估值；数据管理页任务入口与展开式误差明细。原 AKShare 股票池和数据展示保留。
