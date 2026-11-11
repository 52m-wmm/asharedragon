import type { Metadata } from 'next';
import Link from 'next/link';
import Nav from '@/components/Nav';
import './globals.css';
export const metadata: Metadata={title:'AShareDragon · A股历史指标与交易研究',description:'真实数据、可追溯的A股历史指标与交易研究工作台'};
export default function Layout({children}:{children:React.ReactNode}){return <html lang="zh-CN"><body><aside><Link className="brand" href="/"><span className="brandmark">龍</span><span>AShareDragon<small>历史指标与交易研究工作台</small></span></Link><div className="nav-label">研究工作台</div><Nav/><div className="sidebar-bottom"><span className="dot"/> 本地研究环境<small>POSTGRESQL · AKSHARE / BAOSTOCK</small><p>事实与后验标签分离<br/>收盘后研究 · Asia/Shanghai</p></div></aside><div className="workspace"><header><span>中国市场 <b>/</b> 历史指标与交易研究</span><span className="badge">RESEARCH · V0.7</span></header><main>{children}</main><footer>AShareDragon · 数据仅代表已覆盖的供应商样本 <span>金额：人民币元　|　缺失值不计作 0</span></footer></div></body></html>}
