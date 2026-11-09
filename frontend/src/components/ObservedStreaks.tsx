'use client';
import {useEffect,useState} from 'react';
import Link from 'next/link';

type Field={value:boolean|number|string|null;quality:string;evidence_ids:string[];known_at_utc:string|null};
type Blocker={field:string;reason:string;trade_date?:string|null;dates?:string[];detail?:string;minimal_evidence:string};
type Fact={historical_st?:{status:string;first_date_status:string;evidence_ids:string[]};fact_id:string;stock_code:string;first_observed_date:string;last_observed_date:string;observed_min_height:number;
  exact_max_streak:number|null;first_board_date:string|null;cycle_end_date:string|null;complete_cycle_eligible:boolean;
  fields:Record<string,Field>;blockers:Blocker[];publication_blockers:string[];evidence_ids:string[];label_version:string;
  progress:{trade_date:string;observed_count_asof:number}[]};
type CaseAudit={stock_code:string;window_start:string;window_end:string;days:number;unconfirmed_dates:{trade_date:string;reason:string}[]};
type Result={data:Fact[];meta:{total:number;page:number;page_size:number;label_version:string;case_audits:CaseAudit[];built_at_utc:string}};
const API=process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
const labels:Record<string,string>={first_board_date:'精确首板日期',cycle_end_date:'周期结束日期',exact_max_streak:'精确连板高度',
  observed_streak_ge_3:'至少3板',observed_streak_ge_5:'至少5板',observed_streak_ge_7:'至少7板',observed_streak_ge_10:'至少10板'};

export function ObservedStreaks({code,scope='all'}:{code?:string;scope?:string}){
  const [height,setHeight]=useState('3'),[search,setSearch]=useState(''),[start,setStart]=useState('2017'),[end,setEnd]=useState('2026');
  const [sort,setSort]=useState('observed_min_height'),[page,setPage]=useState(1),[result,setResult]=useState<Result|null>(null),[error,setError]=useState('');
  const query=new URLSearchParams({scope,min_height:height,start_year:start,end_year:end,sort,page:String(page),page_size:'20'});
  if(code||search.trim())query.set('code',code||search.trim());
  const path='/api/research/dragons/observed-streaks?'+query.toString();
  useEffect(()=>{const controller=new AbortController();setResult(null);setError('');
    fetch(API+path,{signal:controller.signal,cache:'no-store'}).then(async r=>{const data=await r.json();if(!r.ok)throw Error(data.error?.message||'事实标签读取失败');setResult(data)}).catch(e=>{if(e.name!=='AbortError')setError(e.message)});
    return()=>controller.abort();},[path]);
  useEffect(()=>setPage(1),[scope]);
  const reset=(fn:(v:string)=>void,v:string)=>{fn(v);setPage(1)};
  return <section className="panel observed-streaks" data-testid="observed-streaks">
    <div className="panel-head"><h2>已确认连续涨停事实</h2><small>下限、精确高度与首板分别验收</small></div>
    <p className="panel-copy">最低高度来自逐日已确认的连续涨停；不要求周期两端先完整。精确高度只有两端验证后才显示。此处为事后证据，不是首板当日预测信号；有限审计范围不纳入年度正式统计。</p>
    <div className="filters">
      {!code&&<label>标准股票代码<input aria-label="事实股票代码" placeholder="603178.SH / 603580.SH" value={search} onChange={e=>reset(setSearch,e.target.value)}/></label>}
      <label>已确认最低高度<select aria-label="事实最低高度" value={height} onChange={e=>reset(setHeight,e.target.value)}>{[3,5,7,10].map(n=><option key={n} value={n}>至少{n}板</option>)}</select></label>
      <label>首次观测年份 ≥<select value={start} onChange={e=>reset(setStart,e.target.value)}>{Array.from({length:10},(_,i)=>2017+i).map(y=><option key={y}>{y}</option>)}</select></label>
      <label>首次观测年份 ≤<select value={end} onChange={e=>reset(setEnd,e.target.value)}>{Array.from({length:10},(_,i)=>2017+i).map(y=><option key={y}>{y}</option>)}</select></label>
      <label>事实排序<select value={sort} onChange={e=>reset(setSort,e.target.value)}><option value="observed_min_height">最低高度降序</option><option value="first_observed_date">首次观测日期降序</option><option value="stock_code">股票代码降序</option></select></label>
    </div>
    {error&&<div className="error" role="alert">{error}</div>}
    {!result&&!error&&<p role="status" className="panel-copy">正在读取独立事实…</p>}
    {result&&<><p className="panel-copy">当前筛选：{result.meta.total} 段已确认序列 · 版本 {result.meta.label_version}。年份按首次观测日期筛选，不替代首板启动年份。</p>
      {result.data.length?<div className="table-scroll"><table><thead><tr><th>股票代码 / 观测区间</th><th>已确认最低连板高度</th><th>精确连板高度</th><th>首板日期</th><th>周期终点（最后涨停日）</th><th>历史ST属性</th><th>证据状态 / 精确阻断</th></tr></thead><tbody>
        {result.data.map(r=><tr key={r.fact_id} data-fact-code={r.stock_code}><td><Link href={'/stock?code='+r.stock_code}>{r.stock_code}</Link><small>{r.first_observed_date} → {r.last_observed_date}</small></td>
          <td><b className="height">至少 {r.observed_min_height} 板</b></td><td>{r.exact_max_streak==null?'unknown':`${r.exact_max_streak} 板`}</td><td>{r.first_board_date||'unknown'}</td><td>{r.cycle_end_date||'unknown'}</td>
          <td>{({confirmed_st:"已确认ST",confirmed_non_st:"已确认非ST",unknown:"状态未知"} as Record<string,string>)[r.historical_st?.status||"unknown"]}<small>独立属性，不影响连板事实</small></td><td><span className="status partial">{r.complete_cycle_eligible?'逐日证据与两端已确认':'连续事实已确认 · 周期未完整'}</span>
            <details><summary>查看证据、逐日序列与阻断</summary>
              <p>年度完整区间验收未通过，不纳入年度正式统计。</p>
              {r.blockers.length?r.blockers.map((b,i)=><p key={i}><b>{labels[b.field]||b.field}</b>：{b.trade_date||b.dates?.join('、')||'两端'} · {b.reason}<br/>{b.minimal_evidence}<small>{b.detail}</small></p>):<p>该局部周期无剩余字段阻断；年度统计验收独立保留。</p>}
              <div className="chips">{[3,5,7,10].map(n=><span key={n}>≥{n}：{r.fields[`observed_streak_ge_${n}`].value===true?'已确认':r.fields[`observed_streak_ge_${n}`].value===false?'已排除':'unknown'}</span>)}</div>
              <p>逐日观测计数（起点未知时不是精确第几板）：</p>
              <ol>{r.progress.map(d=><li key={d.trade_date}>{d.trade_date} · 已观察至少 {d.observed_count_asof} 板</li>)}</ol>
              <details><summary>字段证据及确认时间 / UTC</summary><pre>{JSON.stringify(r.fields,null,2)}</pre></details>
            </details>
          </td></tr>)}
      </tbody></table></div>:<div className="empty">当前范围没有已确认达到该高度的事实；不代表股票未达到或收益为零。</div>}
      <div className="pagination"><button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>第 {page} / {Math.max(1,Math.ceil(result.meta.total/20))} 页</span><button disabled={page*20>=result.meta.total} onClick={()=>setPage(page+1)}>下一页</button></div>
      {result.meta.case_audits.length>0&&<details className="history-report"><summary>优先案例的逐日剩余缺口（不受高度筛选影响）</summary>{result.meta.case_audits.map(c=><div key={c.stock_code}><b>{c.stock_code} · {c.window_start} 至 {c.window_end}</b><p>{c.unconfirmed_dates.length?`缺少可定位的收盘涨停/非涨停证据：${c.unconfirmed_dates.map(d=>d.trade_date).join('、')}。最小补证为这些股票日期的可信收盘事件，或独立限价与原价；直接事件不重复要求ST。`:'本案例窗口逐日状态已确认。'}</p></div>)}</details>}
    </>}
  </section>;
}
