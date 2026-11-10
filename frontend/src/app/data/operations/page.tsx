import Link from 'next/link';
import {DataManagement} from '@/components/Research';
export default function Page(){return <><div className="filters"><Link href="/data">← 数据资产总览</Link><Link href="/supplier">供应商行情概览 →</Link></div><DataManagement/></>}
