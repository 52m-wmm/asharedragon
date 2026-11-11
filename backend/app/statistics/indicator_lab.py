"""Read-only indicator projection over published research snapshots. No new facts."""
from collections import Counter
from datetime import date, timedelta
from functools import lru_cache
import json
import duckdb
from app.statistics import market_sentiment as ms
from app.research.us_signals import USSignals, ROOT as US_ROOT, align_session, us_schedule

DEFINITIONS = [
 ('breadth_up','上涨家数','市场广度','家','E1'),
 ('breadth_down','下跌家数','市场广度','家','E1'),
 ('advancing_ratio','上涨比例','市场广度','ratio','E1'),
 ('limit_up','涨停家数','涨跌停','家','H'),
 ('limit_down','跌停家数','涨跌停','家','E2'),
 ('broken_rate','炸板率','涨跌停','ratio','E2'),
 ('first','首板数量','连板','家','H'),
 ('second','二板数量','连板','家','H'),
 ('third_plus','三板及以上','连板','家','H'),
 ('max_height','最高观察连板','连板','板','H'),
 ('promotion_1to2','一进二晋级率','连板','ratio','E2'),
 ('promotion_2to3','二进三晋级率','连板','ratio','E2'),
 ('qqq_close','QQQ 收盘价','外部市场','USD','QQQ'),
]
DEFS={r[0]:dict(zip(('indicator_id','name','category','unit','layer'),r)) for r in DEFINITIONS}


def catalog():
 signature=ms.active_signature();_,versions,days=ms.releases(signature)
 return {'data':[dict(d,scope_applicable=d['layer']!='QQQ') for d in DEFS.values()],
         'meta':{'start':days[0],'end':days[-1],'trading_days':len(days),'snapshots':versions,
                 'reserved':[{'indicator_id':'nq','name':'NQ 期货','available':False},
                             {'indicator_id':'ashare_index','name':'A 股指数','available':False}],
                 'independent_candidate_snapshot_included':False}}


@lru_cache(maxsize=36)
def breadth_month(signature,month):
 paths,_,_=ms.releases(signature)
 with duckdb.connect() as db:
  return {str(r['trade_date']):r for r in ms.rows(db,'SELECT * FROM read_parquet(?)',[str(paths['E1']/(month+'.parquet'))])}


@lru_cache(maxsize=2)
def qqq_inputs(manifest_hash,end):
 manifest_path=US_ROOT/'manifest.json'
 if ms.digest(manifest_path)!=manifest_hash:raise ValueError('QQQ manifest changed')
 with USSignals() as reader:
  path=(US_ROOT/reader.manifest['dataset']).resolve()
  for filename in ('qqq.parquet','us_calendar.parquet'):
   expected=reader.manifest['files'].get(filename)
   if not expected or ms.digest(path/filename)!=expected:raise ValueError('QQQ checksum mismatch')
  # Extend the EXPECTED session calendar, never synthesize prices or carry a stale bar.
  schedule=us_schedule('2016-12-01',str(date.fromisoformat(end)+timedelta(days=1)))
  return reader.bars,schedule,path.name


def qqq_point(day,bars,schedule,mode):
 aligned=align_session(date.fromisoformat(day),schedule,bars,'09:25',mode)
 accepted=aligned['status'] in ('observed_before_cutoff','retrospective_unverified_vintage')
 bar=bars.get(aligned.get('us_trade_date'),{})
 value=bar.get('close') if accepted else None
 if bar.get('close_disagreement'):value=None
 return value,('confirmed' if aligned.get('pit_verified') else 'candidate') if value is not None else 'unknown',aligned


def query(ids,start,end,exclude_st=False,exclude_ipo=False,qqq_mode='strict'):
 selected_ids=list(dict.fromkeys(ids))
 if not selected_ids or any(k not in DEFS for k in selected_ids):raise ValueError('Unknown or empty indicator selection')
 if qqq_mode not in ('strict','retrospective'):raise ValueError('Invalid QQQ mode')
 signature=ms.active_signature();paths,versions,days=ms.releases(signature)
 start=str(date.fromisoformat(str(start)));end=str(date.fromisoformat(str(end)))
 if start>end or start<days[0] or end>days[-1]:raise ValueError('日期范围超出已发布 A 股交易日历')
 days=[d for d in days if start<=d<=end];layers={DEFS[k]['layer'] for k in selected_ids}
 records=[];qqq_hash=None;qqq_error=None;bars={};schedule=[];qqq_version=None
 if 'QQQ' in layers:
  try:
   qqq_hash=ms.digest(US_ROOT/'manifest.json');bars,schedule,qqq_version=qqq_inputs(qqq_hash,end)
  except FileNotFoundError:qqq_error='QQQ snapshot unavailable'
 for month in sorted({d[:7] for d in days}):
  scoped=exclude_st or exclude_ipo
  b={};limits={};h={}
  if layers&{'E1','E2'}:
   if scoped:b,_,_,limits=ms.scoped_month(signature,month,exclude_st,exclude_ipo)
   else:
    if 'E1' in layers:b=breadth_month(signature,month)
    if 'E2' in layers:limits=ms.frozen_limits(signature)
  if 'H' in layers:h=ms.historical_month(signature,month,exclude_st,exclude_ipo)
  for day in (d for d in days if d.startswith(month)):
   for key in selected_ids:
    definition=DEFS[key];layer=definition['layer'];extra={};reason=None;value=None;quality='unknown'
    if layer=='E1':
     row=b.get(day,{})
     value=row.get({'breadth_up':'up','breadth_down':'down'}.get(key,key)) if row.get('price_valid_count') else None
     quality='confirmed';source='E1：参考价验证后的可观察样本，非全市场';reason=row.get('status') if value is None else None
     extra={'price_valid_count':row.get('price_valid_count'),'st_unknown_count':row.get('st_unknown_count')}
    elif layer=='H':
     row=h.get(day,{})
     if row.get('assessable'):
      value=row['confirmed']+row['candidate'] if key=='limit_up' else row['ladder'].get(key)
     quality='candidate' if row.get('candidate') else 'confirmed';source='H：已有事件证据与规则候选；非官方全量认证'
     reason='无可判断股票或高度缺失' if value is None else None
     extra={k:row.get(k) for k in ('confirmed','candidate','unknown','eligible','st_unknown','ipo_unknown')}
    elif layer=='E2':
     row=limits.get(day,{})
     if key.startswith('promotion_'):
      promotion=row.get(key,{});diagnostic=promotion.get('supplier_diagnostic',{})
      value=diagnostic.get('rate');extra={'numerator':diagnostic.get('promoted_count'),'denominator':diagnostic.get('fixed_denominator'),'strict_value':promotion.get('rate')}
     else:value=row.get('supplier_diagnostic',{}).get({'limit_down':'limit_down_count','broken_rate':'broken_limit_rate'}[key])
     quality='supplier_diagnostic';source='E2：AKShare 同源事件池 / 固定上一真实交易日股票队列；非严格非 ST 认证'
     reason='事件池、参考队列缺失或分母不可用' if value is None else None
    else:
     if schedule:value,quality,extra=qqq_point(day,bars,schedule,qqq_mode)
     reason=qqq_error or extra.get('status');source='Nasdaq public historical QQQ；原始美元收盘价，非总收益；09:25 北京时间之前最近已结束美股交易日'
    records.append({'indicator_id':key,'trade_date':day,'value':value,'unit':definition['unit'],
      'source':source,'quality':quality if value is not None else 'unknown',
      'snapshot_version':qqq_version if layer=='QQQ' else versions[layer]['id'],'reason':reason,'evidence':extra})
 if ms.active_signature()!=signature:raise ValueError('Active changed during request')
 if qqq_hash and ms.digest(US_ROOT/'manifest.json')!=qqq_hash:raise ValueError('QQQ changed during request')
 coverage={}
 for key in selected_ids:
  subset=[r for r in records if r['indicator_id']==key];valid=[r['trade_date'] for r in subset if r['value'] is not None]
  coverage[key]={'first':min(valid) if valid else None,'last':max(valid) if valid else None,'available':len(valid),'total':len(subset),'quality_counts':dict(Counter(r['quality'] for r in subset))}
 return {'data':records,'meta':{'dates':days,'coverage':coverage,'snapshots':versions,'qqq_manifest_sha256':qqq_hash,
   'stock_scope':{'exclude_st':exclude_st,'exclude_ipo':exclude_ipo},'qqq_mode':qqq_mode,'version':'indicator-lab-v1',
   'independent_candidate_snapshot_included':False,
   'warning':'A 股指标为当日收盘观察。QQQ 按北京时间09:25对齐；回顾模式未验证历史数据版本可得性，不是 PIT 认证。Z-score 仅用于回顾展示。'}}
