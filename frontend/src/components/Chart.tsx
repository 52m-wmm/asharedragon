'use client';
import {useEffect,useRef} from 'react';
import * as echarts from 'echarts';
export default function Chart({option,height=300}:{option:echarts.EChartsOption;height?:number}){const ref=useRef<HTMLDivElement>(null);useEffect(()=>{if(!ref.current)return;const chart=echarts.init(ref.current);chart.setOption({backgroundColor:'transparent',textStyle:{color:'#95a4bc',fontFamily:'system-ui'},tooltip:{trigger:'axis',backgroundColor:'#182331',borderColor:'#344353',textStyle:{color:'#e5edf5'}},grid:{left:52,right:24,top:30,bottom:38},...option});const obs=new ResizeObserver(()=>chart.resize());obs.observe(ref.current);return()=>{obs.disconnect();chart.dispose()}},[option]);return <div role="img" aria-label="研究数据图表" style={{height,width:'100%'}} ref={ref}/>}
