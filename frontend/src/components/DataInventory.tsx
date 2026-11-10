'use client';
import {useEffect,useState} from 'react';
import Link from 'next/link';
type Month={month:string;rows:number;stocks:number;days:number;missing_days:number|null;missing_reference?:number;missing_st?:number};
type Asset={id:string;source:string;kind:string;storage:string;rows:number|null;raw_rows?:number|null;stocks:number|null;start:string|null;end:string|null;months:Month[];verification:string;notes:string[];missing_days:number|null;missing_files?:string[];staged_rows?:number|null;archive?:string;archive_available?:boolean;import_duplicates?:number|null;import_invalid?:number|null;duplicate_rows?:number;versions?:number;failures?:number;source_id:string};
type Inventory={items:Asset[];scope:string;as_of:string};
const fmt=(v:number|null|undefined)=>v==null?'未盘点':v.toLocaleString('zh-CN');
const API=process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
export default function DataInventory(){
 const [data,setData]=useState<Inventory|null>(null),[error,setError]=useState(''),[refresh,setRefresh]=useState(0),[selected,setSelected]=useState(''),[source,setSource]=useState('全部来源'),[raw,setRaw]=useState(false),[year,setYear]=useState('全部年份');
 useEffect(()=>{const controller=new AbortController();setError('');setData(null);fetch(API+'/api/data/inventory',{signal:controller.signal}).then(async r=>{if(!r.ok)throw Error('盘点读取失败，请检查本地 API');return r.json()}).then(setData).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>controller.abort()},[refresh]);
 const assets=(data?.items||[]).filter(x=>raw?x.storage==='原始缓存':x.storage!=='原始缓存').filter(x=>source==='全部来源'||x.source===source).sort((a,b)=>Number(b.source==='购买的历史数据包')-Number(a.source==='购买的历史数据包'));
 const detail=data?.items.find(x=>x.id===selected);
 return <><div className="title-row"><div><h1>数据资产总览</h1><p>按来源查看：有什么、覆盖多久、哪里缺、核验到哪一步。</p></div><button onClick={()=>setRefresh(x=>x+1)}>重新盘点</button></div>
 <div className="filters"><strong>数据总览</strong><Link href="/data/operations">采集与维护 →</Link></div>
 <p className="panel-copy">完整不等于正确。这里只盘点本地已有数据，不自动下载或补采；不同来源有重叠，不相加为总量。</p>
 <div className="filters"><label>数据来源<select aria-label="数据来源" value={source} onChange={e=>{setSource(e.target.value);setSelected('')}}><option>全部来源</option>{[...new Set(data?.items.map(x=>x.source)||[])].map(s=><option key={s}>{s}</option>)}</select></label><button aria-pressed={!raw} onClick={()=>{setRaw(false);setSelected('')}}>已整理数据</button><button aria-pressed={raw} onClick={()=>{setRaw(true);setSelected('')}}>原始响应缓存</button></div>
 {error&&<p role="alert" className="error">{error}</p>}{!data&&!error&&<p role="status">正在盘点本地文件与数据库…</p>}
 {data&&<><div className="panel table-scroll"><table><thead><tr><th>数据来源 / 类型</th><th>{raw?'原始响应行数':'已整理记录'}</th><th>股票数量</th><th>{raw?'请求日期范围':'实际数据范围'}</th><th>完整情况</th><th>核验情况</th><th>详情</th></tr></thead><tbody>{assets.map(x=><tr key={x.id}><td><strong>{x.source}</strong><small>{x.kind}</small></td><td>{fmt(raw?x.raw_rows:x.rows)}<small>{raw?'含重复请求与版本':x.storage}</small></td><td>{fmt(x.stocks)}</td><td>{x.start||'未知'}<small>至 {x.end||'未知'}</small></td><td>{raw?`${fmt(x.failures)} 个失败响应`:x.missing_files?.length?`缺 ${x.missing_files.length} 个月度文件`:x.missing_days==null?'覆盖分母未知':x.missing_days?`${fmt(x.missing_days)} 个交易日无记录`:'日期有记录'}<small>{raw?'不代表接口权限或全量覆盖':'股票与字段完整度见详情'}</small></td><td>{x.verification}</td><td><button onClick={()=>{setSelected(x.id);setYear(x.months.at(-1)?.month.slice(0,4)||'全部年份')}} aria-expanded={selected===x.id}>查看明细</button></td></tr>)}</tbody></table>{!assets.length&&<p className="panel-copy">没有已登记的数据；可切换到原始响应缓存查看是否尚未整理入库。</p>}</div>
 {detail&&<section className="panel" aria-label="数据源明细"><div className="panel-head"><h2>{detail.source} · {detail.kind}</h2><button onClick={()=>setSelected('')}>收起</button></div><p className="panel-copy">来源标识：{detail.source_id}</p>
 {detail.archive&&<p className="panel-copy">原包：{detail.archive} · {detail.archive_available?'当前位置可访问':'当前位置不可访问，已导入文件仍可读取'}<br/>原包全部行数：未重新盘点 · 导入暂存 {fmt(detail.staged_rows)} 行 · 导入检查重复键 {fmt(detail.import_duplicates)} · 无效行情 {fmt(detail.import_invalid)}</p>}
 {detail.duplicate_rows!=null&&<p className="panel-copy">已整理文件重复股票日期键：{fmt(detail.duplicate_rows)}。本次统计未重新执行文件哈希与跨来源价格核验。</p>}
 {detail.missing_files?.length? <p className="error">清单中的文件缺失：{detail.missing_files.join('、')}</p>:null}
 {detail.notes.map(note=><p className="panel-copy" key={note}>{note}</p>)}
 {!!detail.months.length&&<><label>年份 <select aria-label="明细年份" value={year} onChange={e=>setYear(e.target.value)}><option>全部年份</option>{[...new Set(detail.months.map(m=>m.month.slice(0,4)))].map(y=><option key={y}>{y}</option>)}</select></label><p className="panel-copy">缺日按此来源已观察起止日期内的沪市交易日历计算；某日有记录不代表当天所有股票齐全。股票级缺口尚未按上市、停牌范围核定。</p><div className="table-scroll"><table><thead><tr><th>月份</th><th>记录数</th><th>股票数</th><th>有记录交易日</th><th>无记录交易日</th><th>缺参考价记录</th><th>缺历史 ST 记录</th></tr></thead><tbody>{detail.months.filter(m=>year==='全部年份'||m.month.startsWith(year)).map(m=><tr key={m.month}><td>{m.month}</td><td>{fmt(m.rows)}</td><td>{fmt(m.stocks)}</td><td>{fmt(m.days)}</td><td>{fmt(m.missing_days)}</td><td>{m.missing_reference==null?'不适用':fmt(m.missing_reference)}</td><td>{m.missing_st==null?'不适用':fmt(m.missing_st)}</td></tr>)}</tbody></table></div></>}
 </section>}
 <p className="panel-copy">{data.scope}<br/>盘点时间：{new Date(data.as_of).toLocaleString('zh-CN')}</p></>}
 </>
}
