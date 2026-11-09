"""Render the executed v0.5.2 audit, without inventing unavailable source coverage."""
from pathlib import Path
from collections import Counter
from datetime import datetime
import json
import duckdb
from collect_historical_state import ROOT,BASE


def report():
    manifest=json.loads((ROOT/'manifest.json').read_text());snapshot=ROOT/manifest['dataset']
    summary=json.loads((snapshot/'summary.json').read_text());cases=json.loads((snapshot/'cases.json').read_text());cycles=json.loads((snapshot/'cycles.json').read_text())
    state=json.loads((ROOT/'status_summary.json').read_text());integrity=json.loads((ROOT/'integrity_check.json').read_text())
    old_manifest=json.loads((BASE/'dragon_v1/manifest.json').read_text());old=json.loads((BASE/'dragon_v1'/old_manifest['dataset']/'cycles.json').read_text())
    old_keys={(c['stock_code'],c['first_board_date']) for c in old if c['first_board_date']};new_keys={(c['stock_code'],c['first_board_date']) for c in cycles if c['research_state']=='diagnostic'}
    comparison=dict(old_first_boards=len(old_keys),new_first_boards=len(new_keys),shared_first_board_keys=len(old_keys&new_keys),new_first_board_keys=len(new_keys-old_keys),old_only_keys=len(old_keys-new_keys))
    (ROOT/'population_comparison.json').write_text(json.dumps(comparison,indent=2))
    with duckdb.connect() as db:
        db.read_parquet(str(snapshot/'facts_*.parquet')).create_view('facts')
        reasons=db.execute("SELECT diagnostic_reason,count(*) FROM facts WHERE diagnostic_classification='unknown' GROUP BY 1 ORDER BY 2 DESC").fetchall()
        missing=db.execute("SELECT count(*) FROM facts WHERE NOT price_present").fetchone()[0]
        diagnostics_days=db.execute("SELECT count(DISTINCT trade_date) FROM facts WHERE diagnostic_classification!='unknown'").fetchone()[0]
        confirmed_days=db.execute("SELECT count(DISTINCT trade_date) FROM facts WHERE status!='unknown'").fetchone()[0]
        reference=db.execute('SELECT count(*) FROM facts WHERE reference_verified').fetchone()[0]
    annual=[]
    for y in summary['yearly']:
        h=y['diagnostic_heights'];st=y['research_state_counts']
        annual.append(f"| {y['year']} | {y['audited_sessions']} | {y['formal_first_boards']} | {y['diagnostic_first_boards']} | {h['3']}/{h['5']}/{h['7']}/{h['10']} | {st['unknown']} | {y.get('unknown',0):,} | {y['diagnostic_stock_day_coverage']:.2%} |")
    case_names={'002432.SZ':'九安医疗','000957.SZ':'中通客车','603178.SH':'圣龙股份','603960.SH':'克来机电','603580.SH':'艾艾精工'}
    case_lines=[]
    for c in cases:
        matches=[x for x in c['cycles'] if x['cycle_complete']]
        case_lines.append(f"| {case_names[c['stock_code']]} {c['stock_code']} | {c['year']} | {c['price_rows']}/{c['sessions']} | {c['st_known']} | {len(matches)} | unknown：缺历史 ST、独立限价和完整事件 |")
    reason_lines=[f'| `{reason}` | {n:,} |' for reason,n in reasons]
    text=f'''# AShareDragon v0.5.2：历史状态与龙头研究覆盖

审计时间：{summary['built_at_utc']}。版本：`{summary['version']}`。

## 必须回答的六个问题

1. **693 个 ST 阻断周期解决了多少？** 本轮解决 **{summary['st_blocked_resolved']}/{summary['st_blocked_cycles']}** 个首要 ST 阻断。已完成定向任务规划和实际免费接口尝试，但 BaoStock 未返回新状态数据，不能声称 ST 补齐成功。
2. **31 日扩大到多少日？** 规则审计输入扩大到 **{summary['audit_sessions']} 个交易日**，覆盖 2017-01-03—2026-11-06 的全部原有 10,343,963 条价格记录，并显式检查 {missing:,} 个目录范围内无价格的股票交易日。其中至少有一个可分类诊断状态的日期为 **{diagnostics_days}** 日，至少有一个独立确认日状态的日期为 **{confirmed_days}** 日；正式完整连板标签仍为 0。三种覆盖不能混用。
3. **五个案例能否正确识别？** 当前不能作为正式或完整可信周期发布。五个案例关键连板区间的历史 ST 仍缺失，实际证据口径下首板和最高连板保持 unknown。本轮没有沿用上一版“假设非 ST”的价格情景来增加标签。中通客车 15 个停牌日的公告证据已保留为独立状态，复牌日不默认普通交易。
4. **新增多少标签？** 正式新增 **0**。新扩展总体有 diagnostic **{summary['states']['diagnostic']:,}**、unknown 候选 **{summary['states']['unknown']:,}**，合计 **{summary['candidate_cycles']:,}** 个候选记录，其中完整诊断周期 **{summary['complete_cycles']:,}**。按股票代码、可定位首板日期比较，旧总体 {comparison['old_first_boards']} 个诊断首板与新总体共有 {comparison['shared_first_board_keys']} 个，新总体独有 {comparison['new_first_board_keys']:,} 个。旧总体仅有的 {comparison['old_only_keys']} 个仍在旧快照中，未删除。两版纳入条件不同，不能把数量差解释为新增已验证龙头。
5. **哪些年份能正式研究？** **没有任何年份**达到正式年度季节性研究门槛。下面的年度数字仅是有限证据下的诊断总体计数，不是年度全市场晋级率或龙头分布。
6. **剩余阻塞是什么？** 免费状态接口当前不可用；本地 `stock_st`、`stk_limit` 缓存均为 0；事件缓存只涉及 {summary['event_source_sessions']} 个日期且并非每个日期全池完整；历史特殊交易日、除权参考价、部分日线及生命周期完整性仍不足。

## 31 日限制的确切来源

`validate_historical_limits.py` 原有 `samples` 固定为 2020-12-01—12-31（23 日）和 2026-10-26—11-04（8 日）。`build_dragon_research.py` 直接沿用该抽样导出，特征价格读取也固定在对应窗口附近。

- **测试/验证样本限制：存在，是 31 日的直接来源。** 既有抽样验证被当作生产标签候选范围。
- **数据源覆盖限制：另外存在。** 日线很广，但独立状态、历史限价和涨停事件并不等量覆盖。日期过滤去掉后不会自动获得这些证据。
- **质量门槛限制：存在并继续保留。** 来源冲突、缺历史 ST、特殊交易日不明、边界未知都不能发布正式标签。
- **查询错误：没有发现将完整日期意外截成 31 日的运行时错误。** 固定窗口是旧设计中的显式范围，现已新增按全年、完整交易日历流式处理的独立通道；旧快照不被冒充为全历史结果。

新的价格诊断通道显式允许“无外部涨停事件、但历史规则可计算”的候选；默认事件驱动状态机仍要求事件证据。两者均不自动获得正式资格。`require_event=False` 只放开诊断总体，正式日状态仍要求严格事实和独立证据。

## 历史状态层和实际补采

定向队列共 **{state['tasks']}** 项：498 只股票覆盖 693 个 ST 阻断周期及相邻边界，每个请求范围最多 49 个自然日；另有五个历史案例的年度及两端缓冲区间。没有重复下载十年全市场 OHLC。

请求使用现有免费 BaoStock 适配器的 `query_history_k_data_plus`，字段严格为 `date,code,isST,tradestatus`，`frequency=d`、`adjustflag=3`。单次进程超时 45 秒，限速 0.5 秒，连续失败 3 次后停止新请求。实际计划内结果：成功 {state['progress']['success']}、空响应 {state['progress']['empty']}、失败 {state['progress']['failed']}、待处理 {state['progress']['pending']}。小样本探测也未成功；TCP 可连接，但额外 EOF 检查捕获对端在完整登录响应前关闭连接，返回 BaoStock 10002007。不能据此认定是限流、鉴权要求或用户权限问题。

`status_observations.parquet` 保存来源、股票、有效日期、is_st、trade_status、UTC 采集时间、原始记录 ID/摘要；重复来源保留，矛盾值不按最新一条覆盖。当前 {state['observations']:,} 条来源观察来自本地原有 BaoStock 缓存及 15 条已经核验的公司停牌公告，**新网络状态观察为 0**。观察条数包含不同原始记录的重叠，不等于新增股票日期。另复用了未入库的 2024-01-02 BaoStock 全市场探测缓存；其原始文件缺少采集时间，保存为 NULL 并标注 `observed_time_unknown`，绝不以文件修改时间冒充采集时间。

归一化状态为 `normal_trading`、`risk_warning`、`suspended`、`outside_lifecycle`、`unknown`，另有独立 `quality=conflict/missing/observed/observed_time_unknown/catalogue`。交易标记正常但 ST 字段未知时，不把股票当作非 ST；当前名称从未参与历史推断。ST 标记也不直接等于固定涨跌幅，仍由日期有效板块制度、上市序号和特殊状态共同约束。

本地 `stock_st`、`stk_limit` 盘点为空，报告的是“无法交叉验证”，不是“一致率 100%”。以后出现新缓存时构建器会暂停，要求显式归一化与冲突核验，不静默忽略新来源。

## 参考价、日状态与独立验证

本轮从已有 BaoStock 原始缓存独立保存 `reference_observations.parquet`。只有同日不复权 OHLC 与 Parquet 一致、多个参考价无冲突时，才能使用该来源的昨收参考价；输入已存在的参考价也必须一致。当前有 {reference:,} 个股票交易日通过该参考价核对。原始价格从未改写，不用 `lag(close)` 猜除权参考价，也不启用隔离中的复权因子。

每个审计股票交易日输出 `confirmed_limit_up`、`confirmed_not_limit_up` 或 `unknown`，同时保留独立的诊断分类、未知原因、状态来源、参考价来源、原始事件引用及显式假设。涨停池没有某股票不能充当未涨停真值；规则输出不会被复制成验证真值。公司公告已确认的停牌可形成“非涨停”日状态，但始终另标 `state=suspended`，不生成价格或零收益。

完整周期沿全部交易所交易日历连接，跨月和跨年不重置。价格缺失或 unknown 中断的是认知，不是确认连板失败。起点和终点必须有证据；正式周期要求含前后边界的全部日状态严格通过，且发布门槛通过。最高高度为事后结果，不是首板时的预测信号。

## 每年研究计数

“诊断首板”包括起点可定位但终点可能未知的记录；3/5/7/10 列仅统计边界完整诊断周期，单位为周期。unknown 候选数不包括所有无法形成候选的股票交易日，后者另列。诊断可判定率分母为各年审计股票交易日网格，并不表示市场或正式标签覆盖率。

| 年份 | 审计交易日 | 正式首板 | 诊断首板 | 完整诊断 ≥3/5/7/10 | unknown 候选 | unknown 日状态 | 诊断可判定率 |
|---|---:|---:|---:|---|---:|---:|---:|
{chr(10).join(annual)}

2017、2018 的候选数为 0，是无法形成有证据候选的结果，**不是当年没有首板或龙头**。各年正式完整首板数均为 0，正式年度统计资格均为 false。启动日期、逐次进程、边界日期和来源可在独立 `cycles.json` 或查询接口中查看；未知最终高度保持 NULL。

## 五个历史案例

| 案例 | 年份 | 日线/预期交易日 | 历史 ST 已知日 | 完整周期 | 结果 |
|---|---:|---:|---:|---:|---|
{chr(10).join(case_lines)}

日线无缺失并不代表涨停与首板能确认。中通客车缺的 15 天在 [上一轮诊断](dragon_label_diagnostics.md) 已通过两次停牌/复牌公告确认，本轮只导入经过 PDF SHA-256 核验的停牌观察，未导入非 ST 假设或预先指定正标签。2022-06-10 和 2022-07-06 的复牌属性继续单独核验。其余案例的公告交叉证据和历史价格情景仍保留在上一轮审计中，不能替代完整的逐日状态与独立限价。

## 缺口及后续优先级

全量诊断仍未知的原因如下（首个阻断原因、股票交易日计数，不是互斥的全部潜在问题）：

| 原因 | 股票交易日 |
|---|---:|
{chr(10).join(reason_lines)}

独立历史涨停参考价缺口涉及 **{summary['missing_official_limit_dates']} 个交易日**，逐日股票数量保存于 `missing_limit_dates.parquet`；逐股逐日可查询 `facts_*.parquet` 的 `official_limit_available=false`。它不同于普通昨收参考价，已有 BaoStock preclose 不能充当官方限价。

后续先恢复免费状态来源并按队列断点续传，再检查 693 个周期及五个案例。补齐状态后仍需独立限价/特殊交易日证据和足够长的事件窗口，不能预先承诺全部周期转为 verified。当前仅能做带明确范围和缺口的诊断研究；无年份获准输出正式季节性结论。

## 运行产物、接口与复现

全部新增行情证据和结果位于 `~/Datasets/asharedragon/research/dragon_v052/`，不进入 Git 仓库。当前快照：`{manifest['dataset']}`。状态和参考价观察、来源事件摘要随快照保存；Parquet 按年流式生成，每批 16,384 行，使用既有 DuckDB，不部署服务器。完成后才原子更新 manifest；中断快照不作为当前结果。

- `status_plan.json`、`status_progress.json`、`status_raw/`：请求计划、失败/待办和成功原始返回；成功项按摘要验证并跳过。
- `facts_2017.parquet`—`facts_2026.parquet`：日状态、诊断结果及证据。
- `blocked_693.json`：每个旧 ST 阻断周期对应边界的补齐结果。
- `cycles.json`、`cases.json`、`summary.json`：新研究总体、五例和年度覆盖。
- `missing_limit_dates.parquet`：缺少独立历史限价的日期及股票日数量。
- `population_comparison.json`：与旧事件候选总体的键级比较，避免把两者直接相加。

新增只读 API：

- `GET /api/research/dragons/coverage`：年度覆盖、缺口、状态统计和五例。
- `GET /api/research/dragons/expanded?mode=diagnostic&start_year=2017&end_year=2026&min_height=5`：独立总体分页列表；默认 `mode=formal` 返回空。
- `GET /api/research/dragons/expanded/stock/002432.SZ?start_date=2021-11-01&end_date=2021-11-30`：可追溯日状态。

旧 `/api/research/dragons` 的事件总体保持独立，没有把价格诊断候选混入旧正式页面。本轮不重构 UI；前端构建继续通过。

```bash
# 有界补采；失败项可重试，成功项不会重复请求
PYTHONPATH=backend:scripts .venv/bin/python scripts/collect_historical_state.py
# 仅整理本地证据，绝不请求网络
PYTHONPATH=backend:scripts .venv/bin/python scripts/collect_historical_state.py --offline
# 新建全区间不可变快照
PYTHONPATH=backend:scripts .venv/bin/python scripts/build_dragon_coverage.py
# 中断后可使用 --resume <同一输入的未完成快照目录>
PYTHONPATH=backend:scripts .venv/bin/python scripts/report_dragon_coverage.py
.venv/bin/python -m pytest backend/tests scripts -q
cd frontend
npm run build
```

## 验证结果与限制

完整测试 **187 passed，9 个依赖弃用告警**；新增覆盖状态冲突、生命周期、字段缺失、同日参考价交叉核对、独立真值冲突、正式发布隔离、价格诊断缺口及已确认停牌。`npm run build` 成功。API 使用真实完成快照验证，正式仍为空。

{integrity['validated_partition_hashes']} 个原行情分区（111 个购买分区及 9 个增量分区）的 SHA-256 与原 manifest 一致。业务表 `stock_daily` 51,547 行、`limitup_events` 18,930 行保持不变；原有 **{integrity['recent_gap_count']}** 个近期缺口标记未删除。 本轮审计网格限定为实际价格库中的 5,474 只股票，因此无价格格点为 125,024；原缺口目录中的 000748.SZ 在 2017-01-03—01-18 的 12 个交易日完全无行情，仍留在原缺口目录，未被当成停牌或有效零值。全历史原缺口总数仍为 125,036，两个分母不能混用。没有付费代理调用、分钟数据处理、模型训练或 GitHub Push。

本轮完成的是全区间审计管道、定向状态补采机制与真实执行后的覆盖报告。**状态补齐目标尚未实现**：源端没有返回数据，不能将工程完成描述为历史标签已可信全覆盖。
'''
    Path('docs/dragon_coverage_v052.md').write_text(text)
    print(comparison)

if __name__=='__main__':report()
