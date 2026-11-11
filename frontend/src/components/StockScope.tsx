'use client';
import {createContext,useContext,useState,ReactNode} from 'react';
export type StockScope={exclude_st:boolean;exclude_ipo:boolean};
const empty:StockScope={exclude_st:false,exclude_ipo:false};
const Context=createContext<{value:StockScope;set:(v:StockScope)=>void}|null>(null);
export function StockScopeProvider({children}:{children:ReactNode}){const parent=useContext(Context),[value,set]=useState(empty);return parent?<>{children}</>:<Context.Provider value={{value,set}}>{children}</Context.Provider>}
export function useStockScope(){return useContext(Context)||{value:empty,set:(_v:StockScope)=>{}}}
export function scopeUrl(path:string,value:StockScope){const [base,query='']=path.split('?');const p=new URLSearchParams(query);p.set('exclude_st',String(value.exclude_st));p.set('exclude_ipo',String(value.exclude_ipo));return base+'?'+p}
export function useScopedUrl(path:string){return scopeUrl(path,useStockScope().value)}
export function StockScopeFilter(){const {value,set}=useStockScope();return <div className="stock-scope"><div className="filters" role="group" aria-label="股票范围"><span>股票范围</span><button type="button" aria-pressed={!value.exclude_st&&!value.exclude_ipo} onClick={()=>set(empty)}>全部</button><button type="button" aria-pressed={value.exclude_st} onClick={()=>set({...value,exclude_st:!value.exclude_st})}>排除 ST</button><button type="button" aria-pressed={value.exclude_ipo} onClick={()=>set({...value,exclude_ipo:!value.exclude_ipo})}>排除新股</button></div><p className="panel-copy">{value.exclude_st?'仅纳入历史交易日已确认非 ST 的记录；ST/*ST、状态未知或跨期不确定不进入该队列。':'全部保留历史 ST 与未知状态，不将未知视为非 ST。'}{value.exclude_ipo?' 排除上市初期特殊交易阶段及阶段未知；周期每个已观察日均须满足条件。':' 新股特殊阶段保留原有证据状态。'} IPO 特殊阶段按历史上市日期、板块制度和交易日历判断；不等于整段 IPO 初始连板。筛选改变样本，不改变原始涨停事实或已观察高度。</p></div>}
