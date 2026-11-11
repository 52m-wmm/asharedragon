'use client';
import {useEffect,useState} from 'react';
import Link from 'next/link';
import {useStockScope} from './StockScope';
import Chart from './Chart';
const API=process.env.NEXT_PUBLIC_API_URL||'http://127.0.0.1:8000';
type Row=Record<string,any>;
const pct=(v:number|null|undefined)=>v==null?'—':`${(v*100).toFixed(2)}%`;
const num=(v:number|null|undefined)=>v==null?'—':v.toLocaleString('zh-CN',{maximumFractionDigits:3});
const ci=(v:number[]|null)=>v?v.map(pct).join(' ～ '):'—';
const regimes:Row={main_10:'主板10%背景',chinext_10:'创业板改革前10%背景',chinext_20:'创业板20%背景',star_20:'科创板20%背景'};
const statuses:Row={confirmed_non_st:'历史非ST',confirmed_st:'历史ST',unknown:'历史状态未知'};
const windows:Row={march_may:'3—5月',sep_dec:'9—12月',combined:'3—5月及9—12月'};
export function SeasonalityExploration(){
 const [cohort,setCohort]=useState('direct'),[start,setStart]=useState('2017'),[end,setEnd]=useState('2026'),[board,setBoard]=useState(''),[regime,setRegime]=useState(''),[st,setSt]=useState('all'),[grade,setGrade]=useState('all'),[height,setHeight]=useState('5'),[month,setMonth]=useState<number|null>(null),[page,setPage]=useState(1);
 const [data,setData]=useState<Row|null>(null),[error,setError]=useState('');
 const q=new URLSearchParams({cohort,start_year:start,end_year:end,st,grade,height,page:String(page)});if(board)q.set('board',board);if(regime)q.set('regime',regime);if(month)q.set('month',String(month));
 const {value:stockScope}=useStockScope();q.set('exclude_st',String(stockScope.exclude_st));q.set('exclude_ipo',String(stockScope.exclude_ipo));
 const query=q.toString();
 useEffect(()=>{setPage(1)},[stockScope]);
 const ready=data?._query===query;
 useEffect(()=>{const c=new AbortController();setData(null);setError('');fetch(API+'/api/research/dragons/seasonality-study-v1?'+query,{signal:c.signal}).then(async r=>{const j=await r.json();if(!r.ok)throw Error(j.error?.message||'筛选参数无效');setData({...j,_query:query})}).catch(e=>{if(e.name!=='AbortError')setError(e.message)});return()=>c.abort()},[query]);
 const pick=(m:number|null)=>{setMonth(m);setPage(1)};
 const select=(label:string,value:string,set:(x:string)=>void,options:Row)=><label>{label}<select aria-label={label} value={value} onChange={e=>{set(e.target.value);setPage(1);setMonth(null)}}>{Object.entries(options).map(([v,t])=><option value={v} key={v}>{t}</option>)}</select></label>;
 const years=Object.fromEntries(Array.from({length:10},(_,i)=>[2017+i,String(2017+i)]));
 return <section className="seasonality-study" data-testid="seasonality-study-v1" role="tabpanel" aria-label="启动规律">
 <div className="panel"><div className="panel-head"><h2>首板启动规律 · 第一轮实证</h2><span className="badge">探索性 · 未发现正式季节规律</span></div>
 <p>精确首板启动日期归月。≥3 / 5 / 7 / 10板为已证实达到的累计门槛，不是互斥数量，也不是最高价月份。</p>
 <div className="filters">
 {select('证据队列',cohort,setCohort,{direct:'独立证据',reconstruction:'日线重建诊断（归档）'})}
 {select('起始年份',start,setStart,years)}{select('结束年份',end,setEnd,years)}
 {select('板块',board,setBoard,{'':'全部板块',main:'主板',chinext:'创业板',star:'科创板'})}
 {select('制度背景',regime,setRegime,{'':'全部制度',...regimes})}
 {select('历史ST状态',st,setSt,{all:'全部状态',...statuses})}
 {select('结局证据等级',grade,setGrade,{all:'全部等级',verified:'独立结局已验证',diagnostic:'诊断',unknown:'unknown'})}
 {select('连板高度',height,setHeight,{'1':'≥1板','3':'≥3板','5':'≥5板','7':'≥7板','10':'≥10板'})}
 </div><div className="notice">{cohort==='direct'?'独立证据样本':'归档日线重建诊断样本'}单独统计，两队列不可相加。高度筛选影响数量、热力图及列表；5板晋级率使用筛选范围内全部已知结局首板。缺失月份留空，unknown 不计失败。</div></div>
 {error&&<div role="alert" className="notice warning">{error}</div>}{!ready&&!error&&<p role="status">读取统一研究数据…</p>}
 {data&&ready&&<>
 <div className="panel"><div className="panel-head"><h3>月度龙头数量 · ≥{height}板</h3><small>点击柱形联动历史列表</small></div>
 <Chart onMonthClick={pick} option={{xAxis:{type:'category',data:data.months.map((r:Row)=>r.month+'月')},yAxis:{type:'value',minInterval:1},series:[{name:'达到门槛数量',type:'bar',data:data.months.map((r:Row)=>r.leaders),itemStyle:{color:cohort==='direct'?'#57c7a8':'#e5be73'}}]}}/>
 <div className="filters" aria-label="月份联动"><button onClick={()=>pick(null)} aria-pressed={month===null}>全部月份</button>{data.months.map((r:Row)=><button key={r.month} disabled={r.first_boards==null} aria-pressed={month===r.month} onClick={()=>pick(r.month)}>{r.month}月</button>)}</div>
 <h3>首板 → 5板晋级率 · 95% Wilson 区间</h3>
 <Chart onMonthClick={pick} option={{legend:{data:['晋级率','区间下限','区间上限'],textStyle:{color:'#9fb2ca'}},grid:{top:48,left:55,right:20,bottom:38},xAxis:{type:'category',data:data.months.map((r:Row)=>r.month+'月')},yAxis:{type:'value',axisLabel:{formatter:'{value}%'}},series:[{name:'晋级率',type:'line',connectNulls:false,data:data.months.map((r:Row)=>r.rate==null?null:r.rate*100)},{name:'区间下限',type:'line',lineStyle:{type:'dashed'},data:data.months.map((r:Row)=>r.ci?r.ci[0]*100:null)},{name:'区间上限',type:'line',lineStyle:{type:'dashed'},data:data.months.map((r:Row)=>r.ci?r.ci[1]*100:null)}]}}/>
 <p>Wilson区间为IID参考，不能修正选样偏差。整体证券聚类 bootstrap 区间：{ci(data.rate_uncertainty.ci_stock)}（不足5个成功或失败股票簇时不估计）。</p>
 <div className="table-scroll"><table><thead><tr><th>启动月</th><th>首板</th>{[3,5,7,10].map(h=><th key={h}>≥{h}板</th>)}<th>有效n</th><th>unknown</th><th>5板晋级率 / 95%区间</th></tr></thead><tbody>{data.months.map((r:Row)=><tr key={r.month}><td><button onClick={()=>pick(r.month)}>{r.month}月</button></td><td>{num(r.first_boards)}</td>{[3,5,7,10].map(h=><td key={h}>{num(r.heights[h])}</td>)}<td>{r.effective_n}</td><td>{num(r.unknown)}</td><td>{pct(r.rate)} / {ci(r.ci)}</td></tr>)}</tbody></table></div></div>
 <div className="panel"><h3>年份 × 启动月份 · ≥{height}板热力图</h3><p>— 表示无可用首板样本；0仅表示已有样本中未观察到该门槛。2026年截至 {data.meta.data_end}。</p><div className="table-scroll"><table className="heatmap"><thead><tr><th>年份</th>{data.months.map((r:Row)=><th key={r.month}>{r.month}月</th>)}</tr></thead><tbody>{data.years.map((y:Row)=><tr key={y.year}><th>{y.year}</th>{data.cells.filter((r:Row)=>r.year===y.year).map((r:Row)=><td key={r.month} style={{background:r.leaders==null?'transparent':`rgba(45,190,151,${Math.min(.7,.08+r.leaders/20)})`}}><button disabled={r.first_boards==null} title={`首板 ${num(r.first_boards)} · 有效 ${r.effective_n} · unknown ${num(r.unknown)}`} onClick={()=>{setStart(String(y.year));setEnd(String(y.year));pick(r.month)}}>{num(r.leaders)}</button></td>)}</tr>)}</tbody></table></div></div>
 <div className="panel" id="seasonality-history"><div className="panel-head"><h3>历史龙头列表 · {month?month+'月':'全部月份'} · ≥{height}板</h3><span>{data.total}个周期</span></div><div className="table-scroll"><table><thead><tr><th>股票</th><th>精确首板日期</th><th>已达高度 / 精确最高</th><th>5板结局</th><th>制度 / 历史ST</th><th>证据等级</th></tr></thead><tbody>{data.samples.map((r:Row)=><tr key={r.cycle_id}><td><Link href={`/stock?code=${r.stock_code}`}>{r.stock_code}</Link></td><td>{r.first_board_date}</td><td>≥{r.height} / {num(r.exact_height)}</td><td>{r.outcome}</td><td>{regimes[r.regime]} / {statuses[r.st]}</td><td>{r.grade}</td></tr>)}</tbody></table></div>{!data.total&&<p>当前筛选无样本；不代表该范围没有龙头。</p>}<div className="pagination"><button disabled={page===1} onClick={()=>setPage(page-1)}>上一页</button><span>{page} / {Math.max(1,Math.ceil(data.total/30))}</span><button disabled={page*30>=data.total} onClick={()=>setPage(page+1)}>下一页</button></div></div>
 <div className="panel"><h3>3—5月、9—12月对照分析</h3><p>候选龙头定义为首板后达到5板。每组均对其补集；差值为晋级率差（百分点）。单侧Fisher为IID参考，三项检验BH校正。</p><div className="table-scroll"><table><thead><tr><th>窗口</th><th>窗口 / 其他 有效n</th><th>窗口 / 其他 晋级率</th><th>差值 / 聚类95%区间</th><th>BH q</th><th>去2020差值</th><th>每股首周期差值</th><th>年份等权差值</th><th>同年制度ST调整</th><th>高结局覆盖差值</th></tr></thead><tbody>{data.contrasts.map((r:Row)=><tr key={r.window}><td>{windows[r.window]}</td><td>{r.target.effective_n} / {r.other.effective_n}</td><td>{pct(r.target.rate)} / {pct(r.other.rate)}</td><td>{pct(r.difference)} / {ci(r.stock_cluster_ci)}</td><td>{num(r.bh_q)}</td><td>{pct(r.without_2020.difference)}</td><td>{pct(r.one_cycle_per_stock.difference)}</td><td>{pct(r.balanced_year_difference)}</td><td>{pct(r.adjusted.difference)}<small>{r.adjusted.strata}个共同分层</small></td><td>{pct(r.coverage_restricted.difference)}</td></tr>)}</tbody></table></div><p>高覆盖分析只保留结局覆盖率不低于当前年月中位数的年月，无法消除未入样首板偏差。所有差异仍属探索性。</p></div>
 <div className="panel"><h3>首板特征与后续5板表现</h3><p>首板收盘后回顾性字段，不能当作盘中信号；缺失不补造。Cliff’s δ正值表示成功组字段偏大，首封秒数越小表示越早。p值使用每股首个已知结局周期，5项特征BH校正。</p><div className="table-scroll"><table><thead><tr><th>特征</th><th>≥5板 n / 中位数</th><th>&lt;5板 n / 中位数</th><th>已知结局字段缺失</th><th>Cliff’s δ</th><th>BH q</th></tr></thead><tbody>{data.features.map((r:Row)=><tr key={r.feature}><td>{r.label}</td><td>{r.success.n} / {num(r.success.median)}</td><td>{r.failure.n} / {num(r.failure.median)}</td><td>{r.missing}</td><td>{num(r.cliffs_delta)}</td><td>{num(r.bh_q)}</td></tr>)}</tbody></table></div></div>
 <div className="panel"><h3>样本覆盖与证据质量</h3>{cohort==='direct'&&<p>主样本采用已验收v0.7周期结局规则，当前筛选恢复 {data.restored_short_cycles} 个已结束短周期为失败。它们不再只是敏感性样本。旧额外五日窗口限制仅作对照：有效n {data.legacy_window_sensitivity.effective_n}，晋级率 {pct(data.legacy_window_sensitivity.rate)}。</p>}<p>当前首板 {num(data.summary.first_boards)} · 有效结局 {data.summary.effective_n} · unknown {num(data.summary.unknown)} · 结局覆盖 {pct(data.summary.known_fraction)}。unknown极端情景晋级率范围 {ci(data.summary.unknown_bounds)}，不是置信区间。</p><p>全快照未定位启动日期的{data.meta.undated_all_cohort}个记录未分配月份（不随当前筛选变化）。日线覆盖不等于涨停证据覆盖。ST按首板日证据归组；主板ST不能称为10%普通交易制度。</p><div className="table-scroll"><table><thead><tr><th>制度背景</th><th>首板日ST状态</th><th>首板 / 有效 / unknown</th><th>5板晋级率 / 95%区间</th></tr></thead><tbody>{data.strata.map((r:Row)=><tr key={r.regime+r.st}><td>{regimes[r.regime]}</td><td>{statuses[r.st]}</td><td>{num(r.first_boards)} / {r.effective_n} / {num(r.unknown)}</td><td>{pct(r.rate)} / {ci(r.ci)}</td></tr>)}</tbody></table></div>
 <div className="table-scroll"><table><thead><tr><th>年</th><th>首板</th><th>有效 / unknown</th><th>日线stock-days（全板块/ST）</th><th>独立涨停判定stock-days</th></tr></thead><tbody>{data.years.map((r:Row)=>{const cov=data.coverage.filter((c:Row)=>c.year===r.year);return <tr key={r.year}><td>{r.year}</td><td>{num(r.first_boards)}</td><td>{r.effective_n} / {num(r.unknown)}</td><td>{num(cov.reduce((a:number,c:Row)=>a+c.price_days,0))}</td><td>{num(cov.reduce((a:number,c:Row)=>a+c.limit_days,0))}</td></tr>})}</tbody></table></div><p>{data.meta.warning} 版本：{data.meta.study_version} · 快照：{data.meta.snapshot.split('/').pop()}。本结果不足以支持全市场季节性结论。</p></div>
 </>}
 </section>
}
