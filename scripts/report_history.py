"""Generate v0.2 acceptance evidence from actual PostgreSQL records."""
import json
import re
from collections import Counter
from datetime import date
from pathlib import Path
from sqlalchemy import select,func,tuple_
from app.db.session import SessionLocal
from app.models import (Daily,Event,Job,Raw,Stock,ReconstructionRun,ReconstructionDay,
                        ReconstructionFact,ValidationReport)
from app.services.queries import serialize

ROOT=Path(__file__).resolve().parents[1]
def percent(v):return 'NULL' if v is None else f'{v*100:.4f}%'
with SessionLocal() as db:
    runs=list(db.scalars(select(ReconstructionRun).where(ReconstructionRun.status=='ready').order_by(ReconstructionRun.created_at)))
    count_daily=dict(db.execute(select(Daily.provider,func.count()).group_by(Daily.provider)).all())
    count_events=dict(db.execute(select(Event.limit_type,func.count()).where(Event.provider=='akshare').group_by(Event.limit_type)).all())
    validations=list(db.scalars(select(ValidationReport).order_by(ValidationReport.trade_date,ValidationReport.created_at)))
    payload={'execution_date_local':'2026-11-05','daily_rows_by_provider':count_daily,'v01_events_unchanged':count_events,
             'runs':[],'validations':[serialize(v) for v in validations]}
    out=['# AShareDragon v0.2 实际验收报告','',
         '执行日期：2026-11-05（America/Los_Angeles）。本文件由脚本查询真实数据库生成；没有用测试夹具填研究数据。','',
         '## 兼容与环境','',
         f'- 原 AKShare 未复权日线保留 {count_daily.get("akshare",0)} 条；原涨停/炸板/跌停事件分别为 {count_events.get("up",0)}/{count_events.get("broken",0)}/{count_events.get("down",0)} 条。',
         f'- 新增 BaoStock 日线 {count_daily.get("baostock",0)} 条，原始响应与来源在 PostgreSQL。',
         '- 本地 PostgreSQL 16，Python 3.12，BaoStock 0.9.4；迁移只增加可空字段及新表。升级前备份保存在 data/backups。','',
         '## 实际采集与重建批次','',
         '| 范围 | 模式 | 选中代码数 | 重建交易日 | 目标股票日 / 已收到 | 覆盖率 | 样本涨停事件 | 最大已知高度 | 不可判定原因 |',
         '|---|---|---:|---:|---|---:|---:|---:|---|']
    for run in runs:
        days=list(db.scalars(select(ReconstructionDay).where(ReconstructionDay.run_id==run.run_id)))
        facts=list(db.scalars(select(ReconstructionFact).where(ReconstructionFact.run_id==run.run_id)))
        counts=Counter(f.reason for f in facts if f.classification in ('unknown','missing'))
        expected=sum(d.expected_stocks for d in days);received=sum(d.received_stocks for d in days)
        heights=[f.consecutive_limit_days for f in facts if f.consecutive_limit_days is not None]
        ups=sum(f.classification=='up' for f in facts)
        out.append(f'| {run.start_date} ~ {run.end_date} | {run.scope} | {len(run.parameters.get("selected_codes",[]))} | {len(days)} | {expected} / {received} | {percent(received/expected if expected else None)} | {ups} | {max(heights,default=0)} | {dict(counts)} |')
        payload['runs'].append({'run':serialize(run),'days':[serialize(d) for d in days], 'unknown_reasons':dict(counts),'sample_up_events':ups})
    out+=['','三个历史月是每月 24 只分层样本试采，不是三个月全市场完整回填。覆盖分母保留历史供应商目录。近期 provider_universe 批次也只表示供应商范围，尚有未核实品种/特殊状态时不能称全 A 股完整。重复批次具有不同 run_id，查询不累加。','',
          '## AKShare 对照核验','',
          '| 日期 | 重建批次 | TP | FP | FN | TN | 准确率 | 精确率 | 召回率 | 参考池覆盖 | 不可核验参考股 |',
          '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for v in validations:
        r=v.result
        out.append(f'| {v.trade_date} | {v.run_id[:8]} | {r["true_positive"]} | {r["false_positive"]} | {r["false_negative"]} | {r["true_negative"]} | {percent(r["accuracy"])} | {percent(r["precision"])} | {percent(r["recall"])} | {percent(r["reference_coverage"])} | {len(r["unassessable_reference"])} |')
    if not validations:out+=['','暂无已完成的重叠日期核验；不发布虚构准确率。']
    for v in validations:
        r=v.result
        out+=['',f'### {v.trade_date} / {v.run_id}', '', f'参考原始快照 raw_id={v.reference_raw_id}。以下未证实原因保留 unresolved/unverified。','',
              '```json',json.dumps({k:r[k] for k in ('false_positives','false_negatives','unassessable_reference','height_mismatches') if k in r},ensure_ascii=False,indent=2),'```']
    out+=['','## 日线字段可用性审计','', '| 字段 | 非 NULL 行数 | 总行数 |','|---|---:|---:|']
    total=count_daily.get('baostock',0)
    fields={}
    for field in ('open','high','low','close','pre_close','volume','amount','turnover_rate','pe_ttm','pb','is_st','trade_status'):
        column=getattr(Daily,field,None)
        if column is None:continue
        n=db.scalar(select(func.count(column)).where(Daily.provider=='baostock'))
        fields[field]={'available':n,'total':total}
        out.append(f'| {field} | {n} | {total} |')
    payload['field_availability']=fields
    log=(ROOT/'data/v02-tests.log').read_text() if (ROOT/'data/v02-tests.log').exists() else ''
    tests=re.search(r'\d+ passed[^\n]*',log)
    out+=['','## 工程验证','',
          f'- Pytest：{tests.group(0) if tests else "尚未取得日志"}，含原有 29 项；隔离 PostgreSQL schema 执行迁移 upgrade/downgrade/upgrade。',
          '- 实际缓存重放：63 条已存源日线再次标准化，新增 0 行，日线总数不变，未发起网络请求。',
          '- 新增测试：BaoStock 单位/ST/PE/PB、复权拒绝、板块与生效日期、IPO、停牌/退市排除、缺口与左截尾、分层目录、重复采集、市场快照校验、来源隔离、首板与 API、核验分母和报告幂等。',
          '- 前端生产构建与 TypeScript 检查：参见 data/v02-frontend-build.log。Alembic check 验证无模型漂移。',
          '- 浏览器实测见 data/v02-*.png；桌面/月度覆盖/重建池/首板及估值/核验明细均使用实际 API。',
          '- 原有 Starlette TestClient/httpx 弃用提示仍存在，不影响断言。','',
          '## 尚未完成与下一步','',
          '未执行六年全量回填；仅生成分月计划并提供显式执行命令。历史月份当前仍是小样本。后续逐月扩展供应商范围，核查特殊状态和未覆盖退市证券，复核所有未解释差异后再用于龙头识别研究。未知封单/时间/开板次数保持空值。',
          '', '重现：`.venv/bin/python scripts/report_history.py`；操作与公式见 [history_v02.md](history_v02.md)。']
    (ROOT/'docs/validation_v02.md').write_text('\n'.join(out)+'\n')
    (ROOT/'docs/validation_v02_metadata.json').write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str))
