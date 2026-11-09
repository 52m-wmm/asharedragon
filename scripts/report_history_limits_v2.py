"""Export reproducible local rankings and the reference-recovery review."""
import json,hashlib
from pathlib import Path
from app.statistics.history_limit_queries import dataset,ROOT
OUT=Path('docs/history_limits_v2');OUT.mkdir(exist_ok=True)
with dataset() as (db,s):
 snapshot=ROOT/json.loads((ROOT/'manifest.json').read_text())['dataset']
 db.execute(f"""COPY (SELECT *,row_number() OVER(PARTITION BY year(run_start) ORDER BY height DESC,run_start DESC,stock_code) annual_rank,
 row_number() OVER(PARTITION BY year(run_start),month(run_start) ORDER BY height DESC,run_start DESC,stock_code) monthly_rank
 FROM cycles WHERE height>=3 ORDER BY year(run_start),annual_rank) TO '{OUT}/rankings.csv' (HEADER,DELIMITER ',')""")
 db.execute(f"""COPY (SELECT year(run_start) AS year,month(run_start) AS month,status,count(*) FILTER(WHERE height>=3) ge3,
 count(*) FILTER(WHERE height>=5) ge5,count(*) FILTER(WHERE height>=7) ge7,count(*) FILTER(WHERE height>=10) ge10,
 count(*) FILTER(WHERE height>=3 AND final_height IS NULL) incomplete_ge3 FROM cycles GROUP BY ALL ORDER BY 1,2,3)
 TO '{OUT}/monthly.csv' (HEADER,DELIMITER ',')""")
 db.execute(f"COPY (SELECT year(trade_date) AS year,month(trade_date) AS month,sum(unknown) unknown_stock_days,sum(unknown_observed) unknown_observed,sum(missing_catalogue_rows) missing FROM daily GROUP BY ALL ORDER BY 1,2) TO '{OUT}/monthly_unknown.csv' (HEADER,DELIMITER ',')")
 table='| 年份 | ≥3板 | ≥5板 | ≥7板 | ≥10板 | unknown股票日 | ≥3板中最终高度未知段 |\n|---|---:|---:|---:|---:|---:|---:|\n'
 for y in s['years']:
  incomplete=db.execute('SELECT count(*) FROM cycles WHERE year(run_start)=? AND height>=3 AND final_height IS NULL',[y['year']]).fetchone()[0]
  values=[y['year']]+[sum(c['ge'+str(h)] for c in y['cycles']) for h in [3,5,7,10]]+[y['unknown'],incomplete]
  table+='| '+' | '.join(str(v) if i==0 else f'{v:,}' for i,v in enumerate(values))+' |\n'
 reasons='\n'.join(f"| {r['reason']} | {r['n']:,} |" for r in s['years'][-1]['unknown_reasons'])
 quality=s['reference_recovery']
 files=['backend/app/statistics/reference_recovery.py','backend/app/statistics/history_limits.py','backend/app/statistics/history_limit_queries.py','scripts/build_history_limits.py','frontend/src/components/HistoryLimits.tsx','frontend/src/components/Dragons.tsx']
 lock=dict(version=s['version'],snapshot=str(snapshot),source_snapshot=s['source_snapshot'],raw_sha256=quality['raw_sha256'],code_sha256={f:hashlib.sha256(Path(f).read_bytes()).hexdigest() for f in files})
 (OUT/'snapshot_lock.json').write_text(json.dumps(lock,ensure_ascii=False,indent=2)+'\n')
 (OUT/'reference_recovery.json').write_text(json.dumps(quality,ensure_ascii=False,indent=2)+'\n')
 report=f'''# 2026参考价恢复与历史连板排行榜 v2

## 修复结果

2026年unknown **827,792 → 3,785**，减少 **824,007（99.54%）** 股票日；重建候选涨停 **3,674 → 15,690**，增加 **12,016** 股票日；confirmed仍为 **623**。

本次只读取已有本地文件，不下载、不调用任何外部接口。原始行情、已发布行情Parquet、统一研究快照和严格标签均未改写。全量派生流水线重建以保留跨期上下文，但参考价修复仅作用于2026-03-16—11-06增量来源；2017—2025各年度计数与原v1完全一致。

## 原因与恢复方法

统一快照中增量源 `akshare_em_unadjusted_20260316_20261106` 共826,278条，pre_close非空为0。`scripts/extend_daily_2026.py` 的normalize显式写入 `pre_close=None`；接口原始响应本身没有名为pre_close的字段，但本地 `staging/.../symbols/*.json` 完整保留了“收盘、涨跌额、涨跌幅”。因此不是原始信息被删除，而是标准化没有派生它隐含的参考价。

仅在派生层恢复 `reference = raw.close - raw.change`，采用Decimal运算并要求价格为分刻度、参考价正且有限、涨跌幅与 `change/reference` 在原显示精度0.0051个百分点内一致；同代码同日原始OHLC与统一日线必须一致，限定本次补采来源。826,278条全部通过这些字段校验及匹配。不存在以昨日收盘价填空的逻辑。

现有10只样本股2026-03-02—03-13的100个本地重叠交易日，恢复参考价与原购买数据pre_close **100/100一致**。这是样本交叉检查，并非对全部除权情形或来源独立性的证明。

进入判定时继续保留原有特殊期、源冲突、停牌、复牌/缺日、参考价断裂检测。即使参考价恢复成功，也不绕过这些unknown门槛。没有可信历史涨停价可额外使用；已有真实事件继续优先。源隐含参考价和公式判断均为 **reconstructed_candidate**，不是独立confirmed。

826,278条修复源的最终状态：候选涨停12,016；情景下非涨停811,991；原已有confirmed正向623、反向527；仍unknown1,121。恢复字段不会把历史ST未知改成非ST，也不会把unknown算为非涨停。

## 2026剩余unknown

已有观测无法判定 **1,577**；目录应有但缺行 **2,208**；合计 **3,785**。以下为已有记录原因，不能与缺行重复相加：

| 原因 | 股票日 |
|---|---:|
{reasons}

## 年度连板数量

{table}

单位是观察连续段，按连续段起日归年/月，累计门槛不可相加。数量包含confirmed和明确标注的候选；候选情景下达到的高度不是独立证据已确认高度。代码+交易日唯一化后按交易所日历连续性计算，unknown或缺日均断开。

`first_board_date`只在前一交易日可判为非涨停时提供，否则NULL，UI显示观察段起日及边界未知。`cycle_end_date`只在下一交易日可判为非涨停时提供，否则NULL并显示最后观察日期。`final_height`要求两侧边界都已定位；否则仅展示≥N板的已观察下限，最终高度unknown。候选队列的完整边界也仅在相应制度情景下成立，不升级为严格研究证据。

confirmed仅表示段内正向事件全部已确认，不保证历史ST已知。仅已确认筛选重新计算连续confirmed子段，不把候选高度混入。unknown股票日本身无法给出确定连板高度，不虚构排名；有正向观察但边界不完整的段按最低已观察高度参与排名，并单独显示最终高度unknown。

## UI交付与验收

访问 **http://127.0.0.1:3001/dragons** 的 **历史连板排行** Tab，支持年份、月份、≥3/5/7/10板、全部候选/仅已确认，显示排名、代码、首板/观察起日、结束/最后观察日、最高/下限高度、证据类别和历史ST。原“启动规律”“历史研究与证据”保留。

年度排名选全年，月度排名选具体月；按高度降序，同高度按起日降序、代码升序确定展示序号。周期按起日筛选，期间股票日质量摘要按实际交易日统计，两者不是同一分母。

桌面实际验证2026年≥3板640段，以及圣龙603178.SH在2023年9月起始的14板；390×844移动端验证艾艾603580.SH在2024年3月的13板、confirmed筛选、K线跳转（65个覆盖交易日），页面无横向溢出。边界不完整段实际显示“最终高度unknown”，ST未知保留。截图：[桌面](history_limits_v2/desktop.png)、[移动端](history_limits_v2/mobile.png)。

验证命令：

```sh
PYTHONPATH=backend .venv/bin/pytest backend/tests scripts -q --disable-warnings
NEXT_PUBLIC_API_URL=http://127.0.0.1:8001 npm run build --prefix frontend
```

测试：288 passed，9条已有依赖弃用警告；前端构建及TypeScript通过。回归覆盖原始涨跌额恢复、百分比不符拒绝、除权断裂与复牌保留unknown、连续段右边界、2017—2025计数不变、2026修复计数和排名门槛。

## 可复现输出

版本 `{s['version']}`，统计快照 `{snapshot}`；原统一快照 `{s['source_snapshot']}`。恢复原始文件整体SHA256：`{quality['raw_sha256']}`。

```sh
PYTHONPATH=backend .venv/bin/python scripts/build_history_limits.py
PYTHONPATH=backend .venv/bin/python scripts/report_history_limits_v2.py
```

[完整≥3板排名CSV](history_limits_v2/rankings.csv)含年度/月度序号、证据状态、ST、起止及最终高度NULL；[按月按证据类别数量](history_limits_v2/monthly.csv)、[月度unknown及缺行](history_limits_v2/monthly_unknown.csv)、[参考价质量审计](history_limits_v2/reference_recovery.json)、[代码快照锁](history_limits_v2/snapshot_lock.json)。派生快照另存逐日判定 `days.parquet` 与恢复引用 `recovered_references.parquet`，便于逐代码/日期复核。

未训练模型、未新增数据源、未更改数据库架构、未执行GitHub Push。当前结果仍是基础历史统计，不宣称正式学术研究证据覆盖完整市场。
'''
 Path('docs/history_limits_v2.md').write_text(report)
 print(table)
