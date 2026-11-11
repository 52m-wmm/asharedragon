'use client';
import {useEffect,useState} from 'react';
import {useScopedUrl,useStockScope} from './StockScope';
type Row=Record<string,any>;
const API=process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
const names:Record<string,string>={all:'全部股票',confirmed_non_st:'已确认非ST',confirmed_st:'已确认ST',unknown:'状态未知',legacy:'原研究版本对照（76成功 / 5298失败）'};
export function ScopeChoice({scope,setScope}:{scope:string;setScope:(s:string)=>void}){const {value}=useStockScope();const filtered=value.exclude_st||value.exclude_ipo;useEffect(()=>{if(filtered&&scope==='legacy')setScope('all')},[filtered,scope,setScope]);return <div className="notice"><label>历史ST研究范围 <select aria-label="研究资格版本" value={scope} onChange={e=>setScope(e.target.value)}>{Object.entries(names).map(([k,v])=><option key={k} value={k} disabled={filtered&&k==='legacy'}>{v}</option>)}</select></label><p>历史ST是独立属性，不撤销已确认涨停事实。未知状态可进入探索研究，不能进入严格非ST统计。按周期已观测结果日期筛选；状态变化或日期证据缺失归入未知。不同ST状态与制度背景分别统计。</p>{scope==='legacy'&&<p>不可变历史版本对照；旧版汇总比例含混合制度，不用于当前组间比较。</p>}</div>}
export function NonStScope({scope='all'}:{scope?:string}){
 const [value,setValue]=useState<Row|null>(null),[error,setError]=useState(''),[cohort,setCohort]=useState('direct');
 const path=useScopedUrl('/api/research/dragons/st-research-scope?scope='+scope+'&cohort='+cohort);
 useEffect(()=>{const c=new AbortController();setValue(null);setError('');fetch(API+path,{signal:c.signal}).then(async r=>{const j=await r.json();if(!r.ok)throw Error(j.detail||'状态加载失败');setValue(j)}).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>c.abort()},[path]);
 const data=value?.data;
 return <section className="panel" data-testid="st-research-scope"><div className="panel-head"><h2>历史ST属性与分组研究</h2><span className="badge">局部证据 · 探索性分析</span></div><label>样本来源 <select value={cohort} onChange={e=>setCohort(e.target.value)}><option value="direct">独立涨停事实</option><option value="reconstruction">日线重建诊断</option></select></label>
 <p className="panel-copy">{data?.selection_bias} 分母仅含结局已知的成功和失败；ST未知与结局未知是两个独立属性。原始事实及旧研究版本完整保留。制度名称描述历史背景，未核实的特殊制度不视为普通10%或20%。</p>
 {error&&<p role="alert">{error}</p>}{!value&&!error&&<p>正在读取历史状态视图…</p>}
 {data&&<><div className="quality-grid">{[['样本','selected'],['成功','success'],['失败','failure'],['结局unknown','unknown']].map(([label,k])=><span key={k}>{label}<b>{(k==='selected'?data[k]:data.outcomes[k]||0).toLocaleString('zh-CN')}</b></span>)}</div><div className="table-scroll"><table><thead><tr><th>历史ST状态</th><th>涨跌幅制度背景</th><th>成功</th><th>失败</th><th>结局unknown</th><th>有效N</th><th>组内比例</th><th>来源 / 证据等级</th></tr></thead><tbody>{data.groups.map((r:Row)=><tr key={r.st_status+r.regime}><td>{names[r.st_status]}</td><td>{r.regime}</td><td>{r.success}</td><td>{r.failure}</td><td>{r.unknown}</td><td>{r.effective_n}</td><td>{r.promotion_rate==null?'—':(r.promotion_rate*100).toFixed(2)+'%'}</td><td>{r.sources.join(', ')||'ST证据缺失'}<small>{JSON.stringify(r.evidence_grades)}</small></td></tr>)}</tbody></table></div><details><summary>来源、版本与选择偏差</summary><p>样本快照：{value?.meta.source_snapshot}；属性版本：{value?.meta.version}。ST状态基于已有按代码、证券ID和日期归属的证据，不从普通名称推断非ST。原693周期审计中，{value?.meta.audit_693.limit_status_ST_gate_redundant}个边界无需重复ST门槛，{value?.meta.audit_693.limit_status_still_unknown}个仍缺涨停状态证据；此处不重写标签。</p></details></>}
 </section>;
}
