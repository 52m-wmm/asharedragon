# AShareDragon v0.3：Tushare 回填准备与首月验证

日期：2026-11-05（America/Los_Angeles）。本轮只执行接口补测与 **2020 年 12 月**首月采集。没有执行多年全量回填，没有修改 UI 架构，没有 GitHub 推送。

## 数据源与安全

使用用户提供的第三方 Tushare 兼容代理，来源标识 `tushare_proxy`，不是官方直连。地址由 `TUSHARE_API_URL` 指定，凭据只从被忽略的 `.env`/环境变量读取。HTTP 风险已经用户明确接受；CLI 仍要求 `--accept-insecure-http`。禁止重定向、禁止官方域名回退、不打印 Token，不把 Token 放进归档。

原 SDK 在地址后追加接口名，本版采用兼容 JSON POST `{api_name, token, params, fields}`，直接发送到配置地址。旧 Tushare 原始适配器也改为经过同一代理客户端，避免第三方凭据被发送到官方默认地址。这个改动没有改变 AKShare/BaoStock 的采集路径。旧适配器若需要 HTTP，需本地显式配置 `TUSHARE_ALLOW_HTTP=true`；本轮 CLI 使用明确参数授权。

采集前已备份 `data/backups/asharedragon-before-v03.dump`。Token、原始数据、归档、数据库和逐条核验报告均不提交公开仓库。

## 实际验证与边界


> Public release: stock-level evidence tables are retained locally and are not redistributed. Aggregate results and methods are preserved below.


补测只请求之前缺失的日期/参数；阶段一共 5 次 HTTP 请求，无额外重试。历史 2020/2022 的开盘啦接口与旧涨停池是不同数据集，不视为重复下载。原先探测只保留最多 5 条样例，不能作为全日事实缓存；本轮完整 12 月 1 日 U/D/Z 请求与原来仅 U 样例的探测范围不同。

首次月度采集的 12 月 25 日遇到服务码错误 `[upstream_pool_exhausted]`，提示等待后重试。其他日期继续完成；修复暂时不可用错误分类后，仅恢复该日，一次请求补齐 110 条。未发生真实限频，不能声称已测出代理最高 QPS。限频和上游耗尽的 60 秒等待由离线测试验证，不通过压测触碰服务限额。

## 金额和字段映射

| 源字段 | 入库字段/位置 | 标准单位与规则 |
|---|---|---|
| limit_list_d.fd_amount | limitup_events.seal_amount | 人民币元，禁止误用 limit_amount |
| limit_list_d.float_mv | circulating_market_cap | 人民币元，不乘 10,000 |
| limit_list_d.total_mv | total_market_cap | 人民币元 |
| limit_list_d.amount | amount | 人民币元 |
| limit_list_d.limit_amount | raw_observations / 本地原始响应 | 板上成交金额，不能改名为封单金额；涨停样本为空属源口径 |
| first_time / last_time | first_limit_time / last_limit_time | HHMMSS → PostgreSQL TIME，Asia/Shanghai 交易所本地时间；缺失为 NULL |
| open_times / limit_times | open_times / consecutive_limit_days | 整数；连板高度只对 U 使用，非 U 不以 0 假充连续板数 |
| up_stat / close / pct_chg | 原始响应保留 | 不把收盘价无条件映射为官方涨停价；limit_price 保持 NULL |
| daily_basic.total_mv / circ_mv | proxy_facts.normalized | 源单位万元，乘 10,000 后为元 |
| daily_basic.pe_ttm / pb / turnover_rate | proxy_facts.normalized | PE/PB 无量纲、换手率百分数点，亏损 PE 缺失不补 0 |
| kpl_list.theme / lu_desc / status | proxy_facts.normalized | 当日来源观测，保留来源字段，不推断题材长期有效期 |
| kpl_list/THS 封单及实际流通字段 | proxy_facts.source_fields | 源生单位暂未独立核实，不混入已经验证的元单位 Event 字段 |

实际单位证据：2025-09-01 同一股票的 limit_list_d 流通市值约 3,806,168,520.57 元，daily_basic.circ_mv 为 380,616.8521 万元；乘 10,000 后基本一致，微小差别保留为源精度差异。日线收盘价 × 流通股本（万股）也与 circ_mv（万元）一致。另将此前保留的 5 条近期代理样例与本地 AKShare 的流通市值及封单金额交叉核对，5 条均在分精度内一致。样本匹配不等于全历史单位/金额准确性认证。

官方参考：[涨跌停事件](https://tushare.pro/document/2?doc_id=298)、[每日指标及万元单位](https://tushare.pro/document/2?doc_id=32)、[开盘啦](https://tushare.pro/document/2?doc_id=347)、[同花顺榜单](https://tushare.pro/document/2?doc_id=355)。官方的积分/QPS 说明不能自动视为第三方代理配额。

## 数据库与恢复机制

Alembic 迁移 `26817c2cfe81` 仅增加 `proxy_batches`、`proxy_partitions`、`proxy_facts` 三表，不修改原有数据表。复用 `raw_observations` 与 `limitup_events`：核心观察使用 `provider=tushare_proxy`，与 AKShare 观测和 BaoStock 重建分开。

- `proxy_batches`：日期范围、接口列表、参数、逐分区进度、状态和时间。
- `proxy_partitions`：代理地址指纹 + 接口 + 日期 + 参数哈希唯一；成功分区重跑直接跳过。
- `proxy_facts`：估值/题材/辅助字段按接口独立保存，不向 stock_daily 插入缺 OHLC 的稀疏估值行，避免遮住已有日线。
- 每个交易日独立事务；源完整响应先落 `data/tushare_proxy/<source_id>/`，再标准化入库。原始归档为不可变内容文件，参数索引用于本地缓存。
- 本地缓存成功响应后即使入库前中断，也可继续使用；失败分区重试会刷新源，不把旧空响应永久缓存成零。
- 失败保留错误和已成功分区；失败刷新不会降级可用旧快照或删除历史。显式 `--refresh` 产生新来源修订，不更新旧事件。
- 相同 Raw 不重复插入；相同分区中的完全相同行去重并审计，冲突重复拒绝整个分区。跨修订允许保留不同 Raw 的事实，研究应选完整成功分区快照。
- 取数来源和 fetched_at 保留；事实 collected_at 保留源采集时间。历史回采不代表历史当时已可见，available_at 继续为 NULL。
- CLI 使用独立 PostgreSQL advisory lock 防止两个代理批量进程同时采集。不进入旧队列，避免 worker 自动启动未授权回填。

## 每接口限速、超时及截断

默认配置定义于 `backend/app/providers/tushare_proxy.py`，可通过 CLI `--config` 指定 JSON，按接口覆盖 timeout、interval、retries；不能通过配置提高行数上限来掩盖截断。

| 接口 | 单次上限（按官方文档保守配置） | 读取超时秒 | 最小请求间隔秒 | 额外重试 |
|---|---:|---:|---:|---:|
| limit_list_d | 2500 | 45 | 1.5 | 2 |
| kpl_list | 8000 | 45 | 2 | 1 |
| limit_list_ths | 4000 | 45 | 2 | 1 |
| daily_basic | 6000 | 60 | 2 | 1 |
| stk_limit | 5800 | 45 | 2 | 1 |
| stock_st | 1000 | 30 | 2 | 1 |
| limit_cpt_list | 2000 | 30 | 2 | 1 |

网络错误/5xx 有界重试；HTTP 429 或正文频率提示等待 60 秒，代理上游池耗尽同样等待 60 秒。权限、证书、重定向、不可识别结构不盲目重试。记录 HTTP 状态、服务码、错误类别及耗时，不输出凭据。

达到上限时不能发布为完整：limit_list_d 先按 U/D/Z 拆分，再按 SH/SZ/BJ 拆分；THS 按 HS/GEM/STAR。仍达上限时，仅在提供明确股票集合时逐股缩小范围，否则标记截断、保留缺口。对其他接口也可用明确代码范围缩小；未确认分页能力不猜 offset 参数。所有分片检查日期和筛选条件是否被源执行。

重要：没有代码集合时发生无法继续拆分的截断会安全停止该分区，不会伪称已拉全。`--codes` 表示显式样本范围，不等于全市场证券目录。空响应也保持未确认，不直接计作零事件。

## 首月实际验收

2020-12 批次：`25f7be91-9336-4ad2-afec-d55468398bc4`。

| 项目 | 结果 |
|---|---:|
| 交易日 / 成功日期 | 23 / 23 |
| 缺失日期 | 0 |
| 涨停 / 炸板 / 跌停记录 | 1039 / 367 / 322 |
| 合计 | 1728 |
| 首次、最后封板时间覆盖率（涨停记录为分母） | 100% |
| 连板数、炸板次数覆盖率（涨停记录为分母） | 100% |
| 封单金额、流通市值覆盖率（涨停记录为分母） | 100% |
| 源重复率 / 入库重复率 | 0% / 0% |
| 日期、代码格式错误 | 0 |
| 与已有 BaoStock 股票生命周期冲突 / 目录未匹配代码 | 0 / 0 |
| 完成后再次 resume | 0 次网络请求、0 条新增 |

成功日期：12 月 1、2、3、4、7、8、9、10、11、14、15、16、17、18、21、22、23、24、25、28、29、30、31 日。没有因周末补零或猜测交易日。

股票生命周期对照使用现有 BaoStock 上市/退市日期，属于事后质量审计，不是当时可见预测特征。代码不按当前名称或当前上市状态替换；历史名称与代码保留来源原值。目录匹配不证明全 A 股历史无幸存者偏差。

**与已有 BaoStock 重建结果无共同股票交易日**：现有重建批次从 2022/2024/2025 样本月及近期日期组成，没有 2020-12 重建。准确率、漏报和误报为未适用，不给出虚构的 0 或 100%。本轮没有为制造重叠而额外回填 BaoStock。

更详细的本地报告为 `docs/tushare_month_validation.md`（Git 忽略）；机器可读证据 `data/v03-month-audit.json`。54 项后端测试通过，包含原有 41 项；6 项独立探测离线测试继续通过。迁移升级/回滚/再升级在隔离 schema 验证，生产 Alembic check 无模型漂移。

## 2020 年以来的计划与请求估计

基于当前存储的交易日历，2020-01-01 至 2026-11-05 共 **1657 个交易日**，83 个自然月份。仅生成本地 `data/v03-backfill-plan.json`，未执行。

执行顺序必须保持：

1. limit_list_d：按日完整 U/D/Z，基础约 1657 次；2020-12 已完成可跳过。截断拆分、失败重试会增加次数。
2. kpl_list 历史涨停原因/题材：只在实际验证覆盖的年份先扩展小月。若整个区间均可用，涨停/炸板两类基础约 3314 次；这只是上界场景估计，不是覆盖承诺。
3. limit_list_ths：从文档明确的 2023-11-01 开始验证；至 2026-11-05 为 730 个交易日，两类约 1460 次，不默认请求更早年份。
4. daily_basic：全市场每日模式基础约 1657 次，但此前全市场超时；先按首板候选股票逐日请求，实际次数等于去重的股票交易日数，可能显著多于 1657，不能承诺日卡内完成。
5. stk_limit、stock_st、limit_cpt_list 等辅助接口：每个基础约 1657 次，分别确认历史范围；不因为近期可用就放行全历史。

上述不含已有缓存、重试、截断拆分，也不包含全市场每只股票日线下载。代理最高限频、共享账号配额与数据使用许可以服务商实际约束为准。

## 执行命令

以下是后续可复现命令；除首月 collect/resume 和 plan 外，本轮没有执行：

```bash
# 增量升级（保留原有数据库）
.venv/bin/alembic -c backend/alembic.ini upgrade head

# 首月单次采集：已有成功分区会跳过
.venv/bin/python -m app.tushare_cli collect --start 2020-12-01 --end 2020-12-31 --accept-insecure-http --max-requests 80

# 断点恢复同一批次
.venv/bin/python -m app.tushare_cli resume --batch-id 25f7be91-9336-4ad2-afec-d55468398bc4 --accept-insecure-http --max-requests 80

# 只读审计，不发送 API 请求
.venv/bin/python scripts/audit_tushare_month.py --start 2020-12-01 --end 2020-12-31

# 仅生成计划，不发送 API 请求
.venv/bin/python -m app.tushare_cli plan --start 2020-01-01 --end 2026-11-05 --endpoints limit_list_d,kpl_list,limit_list_ths,daily_basic,stk_limit,stock_st,limit_cpt_list

# 后续经过审核才执行：按年、按月观察结果，避免直接多年份一把跑
.venv/bin/python -m app.tushare_cli backfill --start 2020-01-01 --end 2020-12-31 --endpoints limit_list_d --execute --accept-insecure-http --max-requests 350

# 慢接口小范围试采示例，仍须下次明确执行
.venv/bin/python -m app.tushare_cli collect --start 2025-09-01 --end 2025-09-01 --endpoints daily_basic --codes 000010.SZ --accept-insecure-http --max-requests 3
```

没有 `--execute` 的 backfill 只打印计划。多年/多月执行要求同源、非代码子样本的 2020-12 批次已成功；单月依然可单独诊断。一个接口的月度失败会停止该接口继续推进，保留进度；其他接口仍独立处理，受共同请求总预算约束。预算耗尽后不自动重启新进程绕过额度。
