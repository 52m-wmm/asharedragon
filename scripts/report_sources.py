"""Render the observed probe evidence, without inferring unsupported coverage."""
import json
from pathlib import Path
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models import Raw,Sentiment
root=Path(__file__).resolve().parents[1]
probe=root/'data/probes'
summary=[]
for name in ['summary.json','extended_summary.json','boundary_summary.json']:
    if (probe/name).exists():summary.extend(json.loads((probe/name).read_text()))
signatures=json.loads((probe/'signatures.json').read_text())
report=['# 数据源验证报告','',
'本机实测：2026-11-04（America/Los_Angeles）；对应采集时间 2026-11-05 上海下午。最近完成交易日为 2026-11-04。AKShare '+signatures['version']+'。所有市场数字来自实际返回；网络失败不等于接口永久不可用。','',
'## 结论','',
'- 近期涨停/炸板/跌停池、昨日涨停池、个股未复权日线、交易日历可用。已按源日历采集 2026-10-26 至 2026-11-04 的 8 个交易日，实际数据在 PostgreSQL。',
'- 2024-01-15、2022-06-15 的涨停池均为空；2026-09-30、10-09、10-12 的抽样亦为空，不能用于旧历史研究。空表已明确标记未确认，旧年份缺口也记入数据库。',
'- 本轮最早成功的涨停池抽样为 2026-10-16（61 条）；10-13/14/15 的请求发生超时或 SSL 错误，所以**不能断言 10-16 是硬性最早边界**。最早支持日期随服务变化，仍需未来复核。',
'- 已安装源码中炸板/跌停接口含最近窗口限制；炸板/跌停提示“30 个交易日”，实际检查使用 `datetime.now() - timedelta(days=30)`，这是自然日判断。涨停池没有同样的本地检查，也不表示支持任意历史日期。',
'- 全市场现货和人工智能概念成分探测超时；概念名单、龙虎榜成功。它们仅用于探测，未作为历史事实灌入其他年份。','',
'## 请求结果（实际耗时，秒）','',
'| 接口 | 参数 | 状态 | 行数 | 耗时 | 原始证据 |','|---|---|---|---:|---:|---|']
for r in summary:
    report.append(f"| `{r['endpoint']}` | `{json.dumps(r.get('params',{}),ensure_ascii=False)}` | {r['status']} | {r.get('rows','—')} | {r.get('elapsed',r.get('seconds','—'))} | `{r.get('raw_path','')}` |")
report+=['','## 安装版本实际签名','']
for endpoint,signature in signatures['signatures'].items():report.append(f'- `{endpoint}{signature}`')
report+=['','## 返回字段（成功探测）','']
seen=set()
for r in summary:
    if r['status']=='success' and r['endpoint'] not in seen:
        report.append(f"- `{r['endpoint']}`："+'、'.join(r['columns']));seen.add(r['endpoint'])
with SessionLocal() as db:
    for ep in ['stock_zt_pool_zbgc_em','stock_zt_pool_dtgc_em']:
        r=db.scalar(select(Raw).where(Raw.endpoint==ep,Raw.ingestion_status=='success').order_by(Raw.id).limit(1))
        if r:report.append(f'- `{ep}`：'+'、'.join(r.raw_payload['columns']))
    report+=['','## 实际入库的 8 个交易日','', '| 日期 | 涨停 | 炸板 | 跌停 | 二板晋级率 |','|---|---:|---:|---:|---:|']
    for r in db.scalars(select(Sentiment).order_by(Sentiment.trade_date)):
        report.append(f'| {r.trade_date} | {r.limit_up_count} | {r.broken_limit_count} | {r.limit_down_count} | {r.second_board_promotion_rate if r.second_board_promotion_rate is not None else "NULL"} |')
report+=['','## 数据质量与缺失字段','',
'2026-10-29 涨停源返回 39 行，其中澳洋健康 002172 重复两行，仅“序号”不同；按事实字段完全一致去重后 38 只。初次严格校验失败的原始记录和任务保留，随后成功补采。发生字段冲突的重复会拒绝整个分区。',
'涨停池缺少可靠历史 PE TTM/PB、自由流通市值、官方涨停价、一字/T 字标识、封单股数、涨停原因和公开时间，均保留 NULL。日线缺少市值和估值，不能用现货快照填充过去。龙虎榜的上榜后收益属于未来结果，不能进入上榜当日预测特征。',
'本版原始证据为 AKShare 返回的 DataFrame JSON，包含参数、签名、字段、版本及采集时刻；不是保全 HTTP 原始字节。探测文件位于本机 data/probes（Git 忽略），可重跑脚本；以下归档只保留小体积元数据。','',
'## Tushare Pro（可选，尚无 Token）','',
'通用 `Client("tushare").fetch(endpoint, **params)` 适配器使用官方 SDK 的 query。未提供 Token，未执行账户权限验证、真实返回验证和标准化入库，不宣称接口已可用。','',
'| 接口 | 核验重点 / 官方说明 | 当前状态 |','|---|---|---|',
'| limit_list_d | 官方说明历史自 2020 年起，不含 ST，当前说明至少 5000 积分 | 未授权实测 |',
'| limit_list_ths | 官方说明历史自 2023-11-01，当前说明至少 8000 积分 | 未授权实测 |',
'| daily | 未复权行情；成交量手、成交额千元需转换 | 未授权实测 |',
'| daily_basic | 每日估值与市值；万元字段需转换为元 | 未授权实测 |',
'| stk_limit | 优先读取官方历史涨跌停价，不能靠涨幅阈值 | 未授权实测 |',
'| stock_basic | 需含上市、暂停、退市样本及历史状态，当前名单不等于历史全集 | 未授权实测 |',
'| trade_cal | 按交易所验证日历及权限 | 未授权实测 |','',
'官方资料：[AKShare 股票行情](https://akshare.akfamily.xyz/data/stock/stock_quote.html)、[Tushare limit_list_d](https://tushare.pro/document/2?doc_id=298)、[Tushare limit_list_ths](https://tushare.pro/document/2?doc_id=355)。账户实际权限以持有 Token 的返回为准。','',
'## 重现','',
'```bash','.venv/bin/python scripts/probe_sources.py --recent 20261104','.venv/bin/python scripts/probe_extended.py','.venv/bin/python scripts/report_sources.py','```','',
'边界探测原始文件记录每次实际请求的日期、返回和错误。滚动窗口会变化，未来复跑同日可能已经不可得。真实接口集成验收记录见 validation.md。']
(root/'docs/data_sources.md').write_text('\n'.join(report)+'\n')
(root/'docs/source_probe_metadata.json').write_text(json.dumps({'signatures':signatures,'probes':summary},ensure_ascii=False,indent=2))
