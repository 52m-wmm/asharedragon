"""Read-only queries for the expanded, explicitly separated v0.5.2 population."""
from pathlib import Path
import json
import duckdb
from app.research.dragons import research_state

ROOT=Path.home()/'Datasets/asharedragon/research/dragon_v052'

class DragonCoverage:
    def __init__(self,root=ROOT):
        root=Path(root).resolve();manifest=json.loads((root/'manifest.json').read_text())
        self.path=(root/manifest['dataset']).resolve()
        if not self.path.is_relative_to(root):raise ValueError('Invalid coverage snapshot path')
        self.summary=json.loads((self.path/'summary.json').read_text())

    def coverage(self):
        return dict(data=self.summary,cases=json.loads((self.path/'cases.json').read_text()),
                    meta={'label_version':self.summary['version'],'strict_annual_accepted':False,
                          'warning':'审计范围不等于正式标签覆盖；价格诊断候选与旧事件候选为不同总体，不能直接相加。'})

    def listing(self,mode='formal',start_year=2017,end_year=2026,min_height=1,page=1,page_size=30,scope='legacy'):
        if mode not in ('formal','diagnostic','unknown') or start_year>end_year or page<1 or not 1<=page_size<=100 or min_height not in (1,3,5,7,10):raise ValueError('Invalid coverage filter')
        rows=json.loads((self.path/'cycles.json').read_text())
        eligibility=None
        if scope!='legacy':
            from app.research.st_research_scope import apply, separate_rates
            rows,eligibility=apply(rows,'reconstruction',scope=scope,whole_cycle=True)
        state='verified' if mode=='formal' else mode
        cohort=[r for r in rows if start_year<=r['first_board_year']<=end_year and r['research_state']==state]
        if mode=='formal':cohort=[r for r in cohort if research_state(r,publication_accepted=self.summary.get('strict_accepted') is True)=='verified']
        # Unknown final outcomes cannot pass or fail a height threshold.
        selected=[r for r in cohort if (mode=='unknown' and min_height==1) or (r['max_height'] is not None and r['max_height']>=min_height)]
        return dict(data=selected[(page-1)*page_size:page*page_size],meta=dict(mode=mode,total=len(selected),candidates=len(cohort),
                    unknown_final_heights=sum(r['max_height'] is None for r in cohort),page=page,page_size=page_size,
                    label_version=self.summary['version'],strict_accepted=mode=='formal' and bool(selected),
                    formal_annual_eligible_years=self.summary['formal_annual_eligible_years'],research_scope=scope,eligibility=eligibility))

    def stock_days(self,code,start,end):
        if start>end:raise ValueError('Invalid date interval')
        with duckdb.connect(config={'memory_limit':'256MB','threads':2}) as db:
            result=db.execute('SELECT * FROM read_parquet(?) WHERE stock_code=? AND trade_date BETWEEN ? AND ? ORDER BY trade_date',[str(self.path/'facts_*.parquet'),code,start,end])
            columns=[x[0] for x in result.description];rows=[dict(zip(columns,row)) for row in result.fetchall()]
        return dict(data=rows,meta=dict(label_version=self.summary['version'],source='immutable_v052_snapshot',
                    warning='没有记录不表示未涨停；unknown 不是零收益或停牌。',observed_rows=len(rows)))
