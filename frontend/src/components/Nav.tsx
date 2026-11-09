'use client';
import Link from 'next/link';import {usePathname} from 'next/navigation';
const items=[['/','◫','市场总览'],['/pool','▤','涨停股票池'],['/stock','⌁','个股研究'],['/dragons','龍','龙头研究中心'],['/seasonality','▦','季节性研究'],['/data','▥','数据管理']];
export default function Nav(){const path=usePathname();return <nav>{items.map(([href,icon,label])=><Link className={path===href?'active':''} href={href} key={href}><span>{icon}</span>{label}{path===href&&<i/>}</Link>)}</nav>}
