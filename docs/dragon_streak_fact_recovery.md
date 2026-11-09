# 连续涨停事实标签恢复与 UI 接入

日期：2026-11-07（America/Los_Angeles）。本轮基于既有 v0.5.4 行情、事件、公告与证据快照，未补采股票数据；仅查阅并归档交易所历史规则。标签版本：`trading-evidence-v0.5.4.1`。

## 结论与恢复数量

发布了 49 段已确认连续涨停事实，涉及 44 只股票，共 61 条为 true 的 `observed_streak_ge_n` 阈值标签。阈值计数相互包含，不能相加成为股票或周期数量。

| 标签 | 可查询数量 |
|---|---:|
| observed_streak_ge_3 | 49 |
| observed_streak_ge_5 | 7 |
| observed_streak_ge_7 | 3 |
| observed_streak_ge_10 | 2 |
| 精确完整局部周期 | 2 |
| 仅能确认下限、精确高度未知 | 47 |

61 条下限事实在 v0.5.4 审计文件中已经有证据，本轮恢复的是独立发布、筛选和 UI 可见性，不能称为新增 61 条市场观测。新确认 6 个非收盘涨停日期，其中 4 个解除两个优先案例的周期边界，新增 2 组精确高度、首板日期和周期结束日期事实。另 2 个为克来机电窗口边界，其正向序列尚未完成逐日定位。

两个局部周期已通过逐日及两端证据验证，发布为 `confirmed_complete_cycle`。47 段发布为 `confirmed_lower_bound`。没有把周期不完整视为连续涨停事实不成立；没有将至少 N 板冒充最终恰好 N 板。

历史旧版完整区间统计快照保持不变，其正式全市场 L1 计数仍为 0；这个旧计数不再控制独立事实发布。年度完整覆盖验收与局部事实确认是两个问题。本轮没有把这 49 段当作完整年度样本、生成季节性结论或总体晋级概率。

## 根因定位与修复

1. `dragon_queries.py::listing` 旧列表要求 `cycle_complete` 才按高度筛选；unknown 只能作为单独审计候选显示，不能按已确认下限筛选。这适合旧版完整周期查询，却不适合连续涨停事实。
2. v0.5.4 `streak_fields` 已拆分各字段，但 `/trading-evidence` 只是审计接口，`Dragons.tsx` 没有调用它。研究中心和个股页仍使用旧 `dragon_v1` 快照，所以已有证据没有进入用户可用的标签区。
3. 直接可信收盘涨停事件在 v0.5.4 已能绕过 ST 数值重建门槛，本轮回归测试确认没有重复要求 ST。问题主要出现在负向边界：原路径在不能算出数值限价时把所有日期保留 unknown，遗漏了可独立证明“不是收盘涨停”的情形。

新增独立 `dragon_facts` 快照及 `/api/research/dragons/observed-streaks` 查询。该查询只按已确认最低高度过滤，不调用旧完整周期发布门槛。原始行情、旧周期统计和 PostgreSQL 业务记录均未改写。

UI 在现有龙头研究中心、季节性研究页面及个股详情中增加独立事实区，分别展示最低高度、精确高度、首板、周期终点、证据状态、逐日序列和阻断日期。高度筛选、标准代码查询、年份、排序和分页适用于独立事实区；年份按首次观测日期筛选，不冒充首板启动年份。旧正式/诊断统计保持独立。

## 不依赖 ST 的否定证据

依据[上交所 2023 年交易规则及其实施通知](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/c_20250612_10824490.shtml)第 3.3.13—3.3.15 条：日涨跌幅限制与盘中申报价格范围是不同概念。我们作如下逻辑推导：在有全日涨停上限 U 的交易日，有效最高成交价 H ≤ U；若收盘 C < H，则 C < U，不能为收盘涨停。若当日不设日涨跌幅限制，则也不存在日收盘涨停。这个否定命题不需要知道 ST 比例或 U 的数值。

本轮实现需要显式的已审查规则证据，只启用上交所主板 A 股 2023-04-10—2024-12-31 范围，并仅在五案例有限窗口应用。不能泛化到未审查的市场、时期或动态价格制度；没有向 2017—2026 全市场直接推广。原价必须可信、不复权、有效且为同一证券同一交易日；成交/生命周期冲突、源事件冲突继续隔离。`close == high` 不能据此确认涨停；代码不补出数值限价、非 ST、除权参考价或历史特殊状态。

规则原文：[官方附件](https://www.sse.com.cn/lawandrules/sselawsrules2025/repeal/rules/c/10824490/files/dcbe58edb194451d93f19b1f7dd8fb4c.docx)。SHA-256：`7aa2319f6dcf597be1e86b3b69d7c2ad0e6acb2a5d0cc6be48a01af602fded40`。构建时核验此哈希；规则来源与审核 UTC 时间写入负向证据。旧版历史规则网页现已标注失效，不代表其在本次 2023—2024 案例日期无效。

## 优先案例的完整序列


> Public release: stock-level evidence tables are retained locally and are not redistributed. Aggregate results and methods are preserved below.


圣龙连续涨停交易日：2023-09-28、10-09、10-10、10-11、10-12、10-13、10-16、10-17、10-18、10-19、10-20、10-23、10-24、10-25。周末及国庆休市按完整交易日历跨越，没有当作中断或虚构交易日。

艾艾连续涨停交易日：2024-03-05、03-06、03-07、03-08、03-11、03-12、03-13、03-14、03-15、03-18、03-19、03-20、03-21。

正向证据为既有[圣龙公告](https://static.cninfo.com.cn/finalpage/2023-10-26/1218152094.PDF)、[艾艾公告](https://static.cninfo.com.cn/finalpage/2024-03-22/1219375304.PDF)中经过逐日区间和交易日数量核验的收盘涨停陈述，配合同日期可信成交日线；没有硬编码预期股票标签。


> Public release: stock-level evidence tables are retained locally and are not redistributed. Aggregate results and methods are preserved below.


这两个案例的局部完整周期不再需要额外 ST 或数值涨停价来否认已证实边界。若研究需要数值限价、ST 特征或无未来泄漏的当时可用版本，相关证据仍需另外补齐。字段确认时间保留实际归档/本次审核时间，不倒填为当日已知信号。

## 仍需的最小证据

47 段精确高度未知：45 段缺左边界、25 段缺右边界（可重叠）。每个边界都有股票代码、确切交易日期、旧规则阻断及最小补证路径。直接事件路径不无条件要求 ST。

- 最小路径：补对应日期的可信收盘涨停/非涨停状态；若仍涨停，则继续向外扩展到首个确认非涨停日。
- 替代路径：可信官方当日涨停价与不复权收盘价；或适用范围已审查的负向价格证明。
- 算法重建路径才按需补历史 ST、特殊状态、上市序号、独立除权参考价；它不是所有路径都必须满足的前置条件。
- 九安、中通案例仍缺可逐日定位的正向/负向事件。克来已有两侧非涨停证明，但中间 13 日期的公告展开尚未通过既有保守解析；可以用更明确的公告区间证明或独立逐日事件补齐，不能只写死“13 板”。

以下附录为每个已发布但未完整的片段的边界日期。完全未形成 ≥3 板事实的优先案例，逐日清单见 `summary.json.case_audits[].unconfirmed_dates`，UI 同步展示。

## 验证与复现

- 原始反馈回路：`pytest backend/tests/test_streak_fact_publication.py` 首次因查询缺失返回 404 而失败；修复后通过，确认 unknown 精确高度不排除 ≥5 板事实。
- 新负向价格规则回归首次失败，修复后通过；覆盖 ST 缺失、复权价格拒绝、未审查制度拒绝、价格相等不判正、冲突隔离及两端独立确认。
- 完整测试：`215 passed, 9 warnings`，无失败。
- 前端生产构建、TypeScript 检查、静态页面生成通过。
- ego-browser：1440px 桌面、390px 移动端；高度筛选、代码搜索、分页、两个优先个股页、13 日明细展开通过；页面宽度未溢出，无页面错误提示。
- 本地运行：[龙头研究中心](http://127.0.0.1:3000/dragons)、[圣龙个股](http://127.0.0.1:3000/stock?code=603178.SH)、[艾艾个股](http://127.0.0.1:3000/stock?code=603580.SH)。API：`GET /api/research/dragons/observed-streaks?min_height=5`。
- 构建：`PYTHONPATH=backend:scripts .venv/bin/python scripts/build_streak_facts.py`。输入为带哈希的 v0.5.4 快照及已归档规则；输出 `~/Datasets/asharedragon/research/dragon_facts/manifest.json` 指向新快照。

未修改原始日线，未清除 1790 个原缺口标记，未调用付费接口，未训练模型，未执行 GitHub Push。

## 附录：47 段事实的精确周期阻断


> Public release: stock-level evidence tables are retained locally and are not redistributed. Aggregate results and methods are preserved below.


新发布事实没有相互重叠的同股票日期片段。新快照文件哈希、原 v0.5.4 输入文件哈希及旧 v0.5.2 生产指针哈希核验通过。
