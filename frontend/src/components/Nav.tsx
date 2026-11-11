'use client';
import Link from 'next/link';
import {usePathname} from 'next/navigation';
import {primaryNavigation,stockNavigation,dataNavigation,navigationSection} from '../lib/navigation';
export default function Nav(){
 const path=usePathname(),section=navigationSection(path);
 return <div className="workbench-navigation"><nav className="primary-navigation" aria-label="主导航">{primaryNavigation.map((item,index)=>{const active=section===['market','lab','stock'][index];return <Link key={item.href} href={item.href} className={active?'active':''} aria-current={active?(item.href===path?'page':'true'):undefined}><span>{item.icon}</span>{item.label}{active&&<i/>}</Link>})}</nav>
 {section==='stock'&&<nav className="secondary-navigation" aria-label="股票研究子导航">{stockNavigation.map(item=><Link key={item.href} href={item.href} className={item.href===path?'active':''} aria-current={item.href===path?'page':undefined}>{item.label}</Link>)}</nav>}
 <details className="data-navigation" key={section==='data'?'data':'research'} open={section==='data'}><summary>数据管理</summary><nav className="secondary-navigation" aria-label="数据管理子导航">{dataNavigation.map(item=><Link key={item.href} href={item.href} className={item.href===path?'active':''} aria-current={item.href===path?'page':undefined}>{item.label}</Link>)}</nav></details></div>
}
