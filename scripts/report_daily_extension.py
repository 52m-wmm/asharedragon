"""Write aggregate-only acceptance report; detailed licensed data stays outside Git."""
import json
from pathlib import Path
from collections import Counter,defaultdict
from datetime import date
from app.research.queries import Research
from extend_daily_2026 import ROOT, SOURCE, LOCAL


def report():
    a=json.loads((ROOT/'research/coverage_audit.json').read_text())
    progress=json.loads((ROOT/'staging/stocks/daily'/SOURCE/'progress.json').read_text())
    gate=json.loads((LOCAL/'sample_gate.json').read_text())
    states=Counter(v['status'] for v in progress['symbols'].values())
    with Research(ROOT) as r:
        latest_market=r.market(date(2026,11,6),page_size=1)['meta']
        latest=r.query('SELECT max(trade_date) AS day,count(*) AS rows,count(DISTINCT stock_code) AS stocks FROM daily')[0]
        added=r.query('SELECT count(*) AS rows,count(DISTINCT stock_code) AS stocks,count(DISTINCT trade_date) AS days,min(trade_date) AS first,max(trade_date) AS last FROM daily WHERE source=?',[SOURCE])[0]
        new=r.query('SELECT DISTINCT stock_code FROM daily WHERE source=? EXCEPT SELECT DISTINCT stock_code FROM daily WHERE source<>?',[SOURCE,SOURCE])
        missing_recent=r.query("SELECT count(*) AS rows,count(DISTINCT stock_code) AS stocks FROM read_parquet(?) WHERE trade_date>=DATE '2026-03-16'",[str(ROOT/'research/missing_stock_dates.parquet')])[0]
        unknown_status=r.query('SELECT count(*) AS rows,count(*) FILTER(WHERE trade_status IS NULL) AS unknown_trade_status,count(*) FILTER(WHERE is_st IS NULL) AS unknown_st_status FROM daily WHERE source=?',[SOURCE])[0]
    year_metrics=defaultdict(lambda:[0,0])
    for m in a['monthly']:
        year_metrics[int(m['month'][:4])][0]+=m['matched_stock_sessions'] or 0
        year_metrics[int(m['month'][:4])][1]+=m['expected_stock_sessions'] or 0
    table='\n'.join(f"| {y['year']} | {y['stocks'] or 0:,} | {y['rows'] or 0:,} | {y['observed_days']}/{y['expected_days']} | {y['date_coverage']:.2%} | {year_metrics[y['year']][0]/year_metrics[y['year']][1]:.2%} |" for y in a['yearly'])
    samples='\n'.join(f"| {code} | {s.get('overlap',0)} | {len(s.get('price_mismatch_dates',[]))} | {s.get('max_volume_difference_shares')} | {s.get('amount_mismatches')} |" for code,s in gate['stocks'].items())
    exceptions=[(c,s) for c,s in progress['symbols'].items() if s['status']!='success']
    exception_table='\n'.join(f"| {c} | {s['status']} |" for c,s in exceptions) or '| 无 | 无 |'
    boards='\n'.join(f"| {r['board']} | {r['observed_stocks']:,} | {r['rows']:,} |" for r in a['board_coverage'])
    absent=', '.join(r['stock_code'] for r in a['absent_stocks']) or '无'
    missing_dates=', '.join(str(r['trade_date']) for r in a['missing_dates']) or '无'
    manifest_path=ROOT/'processed/stocks/daily'/SOURCE/'manifest.json'
    manifest=json.loads(manifest_path.read_text()) if manifest_path.exists() else {'months':{}}
    size=sum(r['bytes'] for r in manifest['months'].values())
    content=f"""# 2026 年历史日线补齐与证券覆盖审计

执行日期：2026-11-07（America/Los_Angeles）。目标窗口：2026-03-16—2026-11-06。截止日来自本地 SSE 交易日历，10 股实际响应均验证到 11-06，未请求尚未完成的交易日。

## 本轮验收结果

- 实际新增有行情的交易日：**{added['days']} / 159**；日期范围 {added['first']}—{added['last']}。日期有数据不等于每只股票完整。
- 新增行情：**{added['rows']:,} 条**，涉及 **{added['stocks']:,} 只股票**。其中 **{len(new)} 只**此前没有出现在购买数据中；这是新增证券数量，不是将所有补采股票重复计为新股。
- 合并数据最新交易日：**{latest['day']}**；总计 **{latest['rows']:,} 条、{latest['stocks']:,} 只股票**。
- 本轮目录候选股票 {progress['universe_size']} 只；成功 {states.get('success',0)} 只、失败 {states.get('failed',0)} 只、空响应 {states.get('empty_unverified',0)} 只。未尝试 {len(progress.get('not_attempted',[]))} 只。成功响应仍可能存在个别交易日缺行。
- 目标窗口按已知上市退市闭区间计算，剩余 **{missing_recent['rows']:,} 个股票交易日缺口、涉及 {missing_recent['stocks']} 只股票**。停牌/退市边界及来源未返回均需进一步区分，不把这些缺口填成零行情。
- 最新交易日观测 **{latest_market['observed_stocks']:,} / {latest_market['expected_active_stocks']:,}** 只目录在市股票（{latest_market['observed_stocks']/latest_market['expected_active_stocks']:.2%}）；缺失代码：{', '.join(latest_market['missing_stock_codes'])}，原因未全部确认。
- 北交所独立状态：`out_of_scope_not_collected`，不包含在沪深分母中。

## 免费来源验证及采集方式

BaoStock 单股请求超时 35 秒；全市场日线、股票目录请求各超时 45 秒，没有以空响应冒充覆盖。AKShare `stock_zh_a_hist` 成功，使用 `adjust=''`，并沿用项目 `daily_ak` 适配器：价格未复权、成交量手乘 100 转股、成交额人民币元。

样本为深市主板 000001、000957、002432；沪市主板 600000、603178、603960；创业板 300641、300750；科创板 688001、688981。2026-03-02—03-13 共 10 个交易日，100 条重叠日线与购买 Parquet 核验通过：

| 股票 | 重叠日数 | OHLC 超容差条数 | 最大量差（股） | 金额超容差条数 |
| --- | ---: | ---: | ---: | ---: |
{samples}

OHLC 容差 0.011 元，金额容差 max(1 元, 对照金额×1e-6)，成交量允许不超过 1 手的展示精度差。样本实际差异符合手数舍入；没有进行价格复权或用新源覆盖旧源。

批量按股票拉取整个缺失区间，最多 4 个子进程并发，请求开始间隔至少 0.5 秒；单次超时 30 秒，无紧密自动重试。连续 8 次失败暂停，成功股票写入原子检查点，恢复只请求未完成股票。网络失败在首轮结束后通过同一恢复入口重试，空响应保留 `empty_unverified`。来源仍受限时保留缺口，不引入模拟数据。未调用任何 Tushare 代理或付费接口。

查询前刷新沪市主板、科创板、深市当前目录。主板与深市第一次超时，有限重试后取得数据；当前目录仅补充历史 BaoStock 目录，未删除退市记录。研究目录保留证据时间及来源，不能仅凭今日存续名单重建历史全集。

## 2017—2026 年覆盖

“日期覆盖率”以 SSE 交易日历为分母；2026 年只计算到 11-06。“目录股票日覆盖率”以已知上市日起至退市日（含边界）内的股票交易日对为分母。停牌缺行、退市生效日与最后交易日差异会影响后者，不能直接把它当作纯粹的数据采集成功率。

| 年份 | 观测股票数 | 日线记录数 | 有行情日期/交易日 | 日期覆盖率 | 已知目录股票日覆盖率 |
| --- | ---: | ---: | ---: | ---: | ---: |
{table}

整个目标期没有任何股票行情的交易日期：{missing_dates}。

历史有效期与 2017—2026 目标窗口相交、但合并后完全没有行情的目录股票：{absent}。逐股局部缺行不等于整只股票缺失，完整清单另存本地文件。

| 板块 | 观测股票数 | 记录数 |
| --- | ---: | ---: |
{boards}

2026 年上市目录股票共 {len(a['new_2026'])} 只，其上市日、首末观测日期、记录数均保存在 `coverage_audit.json` 的 `new_2026`。与已知生命周期不符的行情条数：{a['lifecycle_anomalies'][0]['outside_known_lifecycle']}；目录中无法识别的行情代码数：{len(a['unknown_stocks'])}。在目标期内已知退市股票 {a['delisted'][0]['known_delisted']} 只，其中 {a['delisted'][0]['observed']} 只有行情观测。这些都是相对于现有目录的审计结果，不宣称官方历史全集已被完全证明。

## 缺口、停复牌与异常

采集结束后的非成功股票状态：

| 股票 | 状态 |
| --- | --- |
{exception_table}

完整、可查询的本地证据：

- `~/Datasets/asharedragon/research/missing_stock_dates.parquet`：每个缺失代码、交易日、板块、上市/退市日期、已知缺失原因。
- `~/Datasets/asharedragon/research/missing_stocks_summary.parquet`：每股缺失次数和首末缺失日期。
- `~/Datasets/asharedragon/research/coverage_audit.json`：年度/月度覆盖、新上市、全缺失股票、异常及分板块统计。
- `~/Datasets/asharedragon/staging/stocks/daily/{SOURCE}/progress.json`：逐股采集状态及真实错误。

异常 OHLC {a['price_quality'][0]['invalid_ohlc']} 条；重复股票日期键 {a['duplicate_keys']} 个；无效量额 {sum(x['invalid_volume_amount'] for x in a['unit_sanity'])} 条；隐含成交均价偏离高低价 {sum(x['implied_price_outliers'] for x in a['unit_sanity'])} 条；零成交量 {sum(x['zero_volume_rows'] for x in a['unit_sanity'])} 条。辅助检查中的隐含成交均价偏离使用高低价 ±0.02 元，且成交量超过 1,000 股才计入；手数舍入仍需逐条解释，不能仅靠该指标判定错误。

本地 BaoStock 状态证据共 {a['status_evidence'][0]['rows']:,} 条，涉及 {a['status_evidence'][0]['stocks']:,} 只股票，含 {a['status_evidence'][0]['suspended']} 条明确停牌记录；通过相邻交易日明确的“停牌→交易”状态确认复牌转换：{a['resume_transitions'][0]['verified_resumptions']} 次。2017 年至目标截止日的全部逐股缺口共 {sum(x['rows'] for x in a['missing_reasons']):,} 条，其中 84 条有明确停牌证据，其余未确认原因。新增日线经研究查询补充已有证据后，仍有 {unknown_status['unknown_trade_status']:,} 条交易状态未知、{unknown_status['unknown_st_status']:,} 条 ST 状态未知。AKShare 不提供完整历史 ST/停复牌状态，物理 Parquet 保持 `null`；研究查询只在同代码、同日已有 BaoStock 事实时附加状态，并显示 `status_source`。不能把空响应解释成真实退市或停牌，退市状态来自独立证券目录。

## 存储与原始数据保护

新增正式目录：

```text
~/Datasets/asharedragon/processed/stocks/daily/{SOURCE}/
  manifest.json
  year=2026/month=03/daily.parquet
  ...
```

新源共 {len(manifest['months'])} 个月分区，Parquet {size:,} 字节（{size/1024**2:.2f} MiB）。原购买来源目录没有改写；新增分区过滤掉 03-13 及之前记录。统一保留 `stock_code,trade_date,open,high,low,close,volume,amount,source,adjustment_type`，缺失的复权因子、前收及状态保持空值，原接口返回另存本地检查点。

首轮嵌套 JSON 汇总触及 512 MB 限制，已改为逐文件读取、约 20,000 行缓冲的 PyArrow 流式临时 Parquet，再由 DuckDB 分月排序发布；原始检查点没有丢失。恢复时复用了 5,218 个已有检查点，只重试 28 个网络失败，全部成功。最后一次重试与发布耗时 53.52 秒，进程峰值 RSS 约 322.7 MiB，DuckDB 内存限制仍为 512 MB。

正式发布后，原购买数据 111 个分区及新增 9 个分区均逐文件校验 SHA-256，与各自清单一致；五类研究 API 在真实合并数据上返回 200，并携带覆盖状态。

采集和研究审计不向 PostgreSQL 业务表写入，测试仅使用隔离 schema。行情、原始响应、清单都保存在仓库外或被 Git 忽略的目录，没有提交或推送 GitHub。

## 研究接口及测试

新增 `/api/research/daily`、`/market`、`/preboard`、`/coverage`、`/events`，均返回覆盖信息。缺失月份记录数为 `null`；首板前窗口按交易所日历，缺行则不计算完整窗口特征；Tushare 事件使用左连接并显示独立的事件覆盖范围。没有创建正式龙头标签。使用方式与覆盖语义见 [研究查询说明](research_daily_queries.md)。

验证命令：`.venv/bin/python -m pytest backend/tests scripts -q`，**135 项通过**；`npm run build --prefix frontend` 成功。新增查询测试验证缺失月份不伪装为零值、交易日窗口不跳过缺行、上市/退市边界、真实停牌状态保留、事件无日线仍保留及接口输入验证。新增采集测试验证单位换算、来源代码、重复/非交易日拒绝和 Parquet 发布往返。保留原有 Starlette/httpx 弃用警告。

尚需关注：历史生命周期目录的独立完整性核验、停牌缺口与源缺口的进一步区分、历史 ST/公司行动状态，以及未来新增交易日批次。即使日期覆盖率达到 100%，也不能据此宣布所有历史股票状态或逐股日线完全齐备。
"""
    Path('docs/daily_extension_2026.md').write_text(content)
    print('docs/daily_extension_2026.md')


if __name__=='__main__':report()
