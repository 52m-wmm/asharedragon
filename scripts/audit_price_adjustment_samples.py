"""Compare cached public/local references and export seven-session event windows."""
import json
from pathlib import Path
import duckdb
ROOT=Path('data/price_adjustment_audit')
def run():
    db=duckdb.connect()
    db.read_parquet(str(Path.home()/'Datasets/asharedragon/processed/stocks/daily/purchased_usb_0dfebf6ee9bc/year=*/month=*/*.parquet'),hive_partitioning=False).create_view('daily')
    cases=[('000001.SZ','2017-07-21'),('000001.SZ','2018-07-12'),('600519.SH','2019-06-28'),('600036.SH','2020-07-10'),('000001.SZ','2021-05-14'),('600036.SH','2022-07-15'),('300750.SZ','2023-04-26'),('000001.SZ','2024-06-14'),('688290.SH','2024-12-04'),('002594.SZ','2025-07-29'),('600036.SH','2026-01-16')]
    refs={}
    local=json.loads((ROOT/'baostock_local_window.json').read_text())
    assert all(r.get('adjustflag')=='3' for batch in local['raw'] for r in batch['raw_payload']['records'])
    for r in json.loads((ROOT/'baostock_local_window.json').read_text())['records']:
        refs[r['stock_code'],r['trade_date']]=('本地 BaoStock raw_id=95',r)
    for r in json.loads((ROOT/'tushare_official_example.json').read_text())['records']:
        day=r['trade_date'];refs[r['ts_code'],day[:4]+'-'+day[4:6]+'-'+day[6:]]=('Tushare 官方示例',r)
    samples=[];lines=['# 除权窗口逐日价格证据','', '本附录中的2019、2020、2021、2022及2024-06-14为因子与pre_close筛出的候选窗口，未独立认证其公司行为日期；已认证窗口见主报告。价格单位：元；OHLC 顺序为开/高/低/收。每组取事件前3个和后3个实际有记录交易日；“无外源OHLC”明确表示只检查本包价格与公司行为，不冒充独立行情对照。','']
    for code,day in cases:
        r=db.execute('''with x as(select *,row_number() over(order by trade_date) rn from daily where stock_code=?),
        event as(select rn from x where trade_date=?::date) select trade_date,open,high,low,close,pre_close,adj_factor from x where rn between (select rn-3 from event) and (select rn+3 from event) order by trade_date''',[code,day])
        rows=[dict(zip([v[0] for v in r.description],v)) for v in r.fetchall()]
        assert len(rows)==7, (code,day,'incomplete window')
        lines += [f'## {code} / {day}','','| 日期 | 购买数据 OHLC | 独立对照 OHLC | 最大绝对差 | pre_close | adj_factor |','|---|---|---|---:|---:|---:|']
        for row in rows:
            row['trade_date']=str(row['trade_date']);ref=refs.get((code,row['trade_date']))
            fmt=lambda r:'/'.join(f'{float(r[k]):.2f}' for k in ['open','high','low','close'])
            row['reference_source']=ref[0] if ref else None
            row['reference_ohlc']={k:float(ref[1][k]) for k in ['open','high','low','close']} if ref else None
            row['max_difference']=max(abs(row[k]-float(ref[1][k])) for k in ['open','high','low','close']) if ref else None
            lines.append(f"| {row['trade_date']} | {fmt(row)} | {fmt(ref[1])+'（'+ref[0]+'）' if ref else '无外源OHLC'} | {row['max_difference'] if ref else '—'} | {row['pre_close']:.2f} | {row['adj_factor']} |")
        samples.append(dict(stock_code=code,event_date=day,rows=rows));lines.append('')
    comparisons=[]
    for (code,day),(source,ref) in refs.items():
        r=db.execute('select open,high,low,close from daily where stock_code=? and trade_date=?::date',[code,day]).fetchone()
        if r:comparisons.append(dict(stock_code=code,day=day,source=source,max_difference=max(abs(v-float(ref[k])) for v,k in zip(r,['open','high','low','close']))))
    r=db.execute('''with x as(select *,lag(adj_factor) over w f,lag(close) over w c,lag(trade_date) over w dt from daily window w as(partition by stock_code order by trade_date))
        select stock_code,trade_date,dt previous_date,c previous_close,pre_close,close,f previous_factor,adj_factor,adj_factor/f-1 relative_change
        from x where abs(pre_close-c)<=0.011 and abs(adj_factor/f-1)>.01 order by abs(relative_change) desc limit 20''')
    anomalies=[dict(zip([v[0] for v in r.description],row)) for row in r.fetchall()]
    result=dict(samples=samples,all_reference_comparisons=comparisons,factor_anomalies=anomalies)
    (ROOT/'samples.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+'\n')
    Path('docs/price_adjustment_windows.md').write_text('\n'.join(lines)+'\n')
    print('windows',len(samples),'rows',sum(len(s['rows']) for s in samples),'reference_rows',len(comparisons),'max_difference',max(c['max_difference'] for c in comparisons))
if __name__=='__main__':run()
