"""Bounded 10-stock free-provider gate; no PostgreSQL writes or paid calls."""
import json
from pathlib import Path
from datetime import date
from app.providers.client import Client
from app.providers.normalize import daily_ak
from app.research.daily import connect_daily

SAMPLE = ['000001.SZ','000957.SZ','002432.SZ','600000.SH','603178.SH','603960.SH',
          '300641.SZ','300750.SZ','688001.SH','688981.SH']
OUT=Path('data/daily_extension')
BASE=Path.home()/'Datasets/asharedragon/processed/stocks/daily/purchased_usb_0dfebf6ee9bc'


def run():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'provider':'akshare','adjustment':'none','volume_unit':'shares','amount_unit':'CNY',
            'start':'2026-03-16','end':'2026-11-06','overlap_start':'2026-03-02','stocks':{},'passed':False}
    with connect_daily(BASE) as db:
        for code in SAMPLE:
            path=OUT/f'sample_{code}.json'
            try:
                if path.exists(): payload=json.loads(path.read_text())
                elif code=='000001.SZ' and (OUT/'akshare_availability.json').exists():
                    payload=json.loads((OUT/'akshare_availability.json').read_text());path.write_text(json.dumps(payload,ensure_ascii=False))
                else:
                    payload=Client('akshare',retries=1,interval=.5,timeout=30).fetch('stock_zh_a_hist',symbol=code[:6],period='daily',start_date='20260302',end_date='20261106',adjust='')
                    path.write_text(json.dumps(payload,ensure_ascii=False))
                rows=[daily_ak(r,code) for r in payload['records']]
                if not rows or len({r['trade_date'] for r in rows})!=len(rows):raise ValueError('Empty/duplicate response')
                if any(not date(2026,3,2)<=r['trade_date']<=date(2026,11,6) for r in rows):raise ValueError('Out of range')
                old={r[0]:r[1:] for r in db.execute('SELECT trade_date,open,high,low,close,volume,amount FROM daily WHERE stock_code=? AND trade_date BETWEEN ? AND ?', [code,date(2026,3,2),date(2026,3,13)]).fetchall()}
                overlap=[r for r in rows if r['trade_date'] in old]
                diffs=[];max_volume=0;amount_bad=0
                for r in overlap:
                    ref=old[r['trade_date']]
                    if any(abs(float(r[k])-ref[i])>.011 for i,k in enumerate(('open','high','low','close'))):diffs.append(str(r['trade_date']))
                    max_volume=max(max_volume,abs(float(r['volume'])-ref[4]))
                    if abs(float(r['amount'])-ref[5])>max(1,ref[5]*1e-6):amount_bad+=1
                # AKShare's lots are rounded, so allow <= one lot discrepancy.
                ok=len(overlap)==10 and not diffs and max_volume<=100 and not amount_bad and any(r['trade_date']>=date(2026,3,16) for r in rows)
                report['stocks'][code]={'rows':len(rows),'overlap':len(overlap),'price_mismatch_dates':diffs,
                    'max_volume_difference_shares':max_volume,'amount_mismatches':amount_bad,'last_date':str(max(r['trade_date'] for r in rows)),'passed':ok}
            except Exception as exc:report['stocks'][code]={'passed':False,'error':str(exc)}
            (OUT/'sample_gate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            print(code,report['stocks'][code],flush=True)
    report['passed']=len(report['stocks'])==10 and all(r['passed'] for r in report['stocks'].values())
    (OUT/'sample_gate.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':run()
