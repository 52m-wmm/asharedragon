"""Read-only v0.5 snapshot queries; formal and diagnostic populations never blend."""
from pathlib import Path
import json,statistics
from app.research.dragons import wilson,research_state

ROOT=Path.home()/'Datasets/asharedragon/research/dragon_v1'


def distribution(values):
    values=sorted(float(v) for v in values if v is not None)
    if not values:return {'n':0,'median':None,'min':None,'q1':None,'q3':None,'max':None}
    def quantile(p):
        position=(len(values)-1)*p;i=int(position);j=min(i+1,len(values)-1)
        return values[i]+(values[j]-values[i])*(position-i)
    return {'n':len(values),'median':statistics.median(values),'min':values[0],'q1':quantile(.25),'q3':quantile(.75),'max':values[-1]}


class Dragons:
    def __init__(self,root=ROOT):
        root=Path(root).resolve();manifest=json.loads((root/'manifest.json').read_text());path=(root/manifest['dataset']).resolve()
        if not path.is_relative_to(root):raise ValueError('Snapshot path escaped research root')
        self.rows=json.loads((path/'cycles.json').read_text());self.coverage=json.loads((path/'coverage.json').read_text());self.meta=json.loads((path/'metadata.json').read_text())
        for row in self.rows:
            row['research_state']=research_state(row,publication_accepted=self.meta.get('strict_accepted') is True)
            row['max_height_state']=row['research_state'] if row['cycle_complete'] else 'unknown'

    def filtered(self,mode='formal',start_year=2017,end_year=2026,month=None,min_cap=None,max_cap=None,search='',board_type=None,industry=None,theme=None,provider=None,**_):
        if start_year>end_year:raise ValueError('开始年份不能晚于结束年份')
        if min_cap is not None and max_cap is not None and min_cap>max_cap:raise ValueError('市值区间无效')
        if mode not in ('formal','diagnostic'):raise ValueError('Unknown label mode')
        rows=[]
        for r in self.rows:
            if mode=='formal' and research_state(r,publication_accepted=self.meta.get('strict_accepted') is True)!='verified':continue
            if provider and r['provider']!=provider:continue
            day=r['first_board_date'] or r['first_observed_date']
            if not start_year<=int(day[:4])<=end_year or (month and int(day[5:7])!=month):continue
            if search.lower() not in (r['stock_code']+' '+(r['stock_name'] or '')).lower():continue
            if min_cap is not None and (r['circulating_market_cap'] is None or r['circulating_market_cap']<min_cap):continue
            if max_cap is not None and (r['circulating_market_cap'] is None or r['circulating_market_cap']>max_cap):continue
            if board_type and r['board_type']!=board_type:continue
            if industry and r['industry']!=industry:continue
            if theme and r['theme']!=theme:continue
            rows.append(r)
        return rows

    def metadata(self,rows,mode):
        return {**self.meta,'research_state_counts':{state:sum(research_state(r,publication_accepted=self.meta.get('strict_accepted') is True)==state for r in rows) for state in ('verified','diagnostic','unknown')},'mode':mode,'filtered_candidates':len(rows),
            'candidate_resolution_fraction':sum(r['cycle_complete'] for r in rows)/len(rows) if rows else None,
            'valid_samples':sum(r['cycle_complete'] for r in rows),'unknown_samples':sum(not r['cycle_complete'] for r in rows),
            'statistical_scope':'complete diagnostic cycles in audited intervals; not full-market dragon facts',
            'empty_reason':'严格验收尚未通过，正式标签为0；可切换诊断研究查看有限样本。' if mode=='formal' else '没有符合条件的诊断样本。'}

    def listing(self,*,min_height=5,include_unknown=False,sort='max_height',descending=True,page=1,page_size=30,**filters):
        if min_height not in (1,3,5,7,10):raise ValueError('高度仅支持1、3、5、7、10')
        if sort not in ('max_height','first_board_date','circulating_market_cap','stock_code'):raise ValueError('Unknown sort field')
        if page<1 or not 1<=page_size<=100:raise ValueError('Invalid pagination')
        cohort=self.filtered(**filters)
        rows=[r for r in cohort if (r['cycle_complete'] and r['max_height']>=min_height) or (include_unknown and not r['cycle_complete'])]
        known=[r for r in rows if r[sort] is not None];unknown=[r for r in rows if r[sort] is None]
        rows=sorted(known,key=lambda r:(r[sort],r['cycle_id']),reverse=descending)+sorted(unknown,key=lambda r:r['cycle_id'])
        compact=[{k:v for k,v in r.items() if k not in ('candles','feature_provenance','progress','windows')} for r in rows[(page-1)*page_size:page*page_size]]
        return {'data':compact,'meta':{**self.metadata(cohort,filters.get('mode','formal')),'total':len(rows),'page':page,'page_size':page_size,
            'unknown_filter_policy':'Unknown cycles are an explicit separate audit population; height cannot be tested for them'}}

    def statistics(self,min_height=5,**filters):
        if min_height not in (1,3,5,7,10):raise ValueError('高度仅支持1、3、5、7、10')
        cohort=self.filtered(**filters);complete=[r for r in cohort if r['cycle_complete']];monthly=[]
        for original in self.coverage:
            c=original.copy()
            if filters.get('provider'):
                source=(c.get('by_provider') or {}).get(filters['provider'])
                c.update(source or {'event_sessions':None,'event_coverage':None,'full_month_events':False,'full_month_audit':False,'strict_accepted':False})
                c['coverage_provider']=filters['provider']
            if not filters.get('start_year',2017)<=c['year']<=filters.get('end_year',2026):continue
            if filters.get('month') and filters['month']!=c['month']:continue
            candidates=[r for r in cohort if int((r['first_board_date'] or r['first_observed_date'])[:4])==c['year'] and int((r['first_board_date'] or r['first_observed_date'])[5:7])==c['month']]
            rows=[r for r in candidates if r['cycle_complete']]
            eligible=c['full_month_events'] and c['full_month_audit'] and (filters.get('mode','formal')=='diagnostic' or c['strict_accepted'])
            successes=sum(r['max_height']>=5 for r in rows)
            monthly.append({**c,'eligible':eligible,'first_boards':len(rows) if eligible else None,
                'dragon_starts':sum(r['max_height']>=min_height for r in rows) if eligible else None,
                'five_board_successes':successes if eligible else None,'promotion_rate':successes/len(rows) if eligible and rows else None,
                'promotion_ci':wilson(successes,len(rows)) if eligible else None,
                'valid_samples':len(rows),'unknown_samples':sum(not r['cycle_complete'] for r in candidates),
                'cap_distribution':distribution([r['circulating_market_cap'] for r in rows if r['max_height']>=min_height]) if eligible else distribution([]),
                'scope':'observed qualifying cohort only; boundary-unknown stocks excluded, not failed'})
        leader=[r for r in complete if r['max_height']>=5];ordinary=[r for r in complete if r['max_height']<5]
        compare=[]
        for provider in sorted({r['provider'] for r in complete}):
            source_leaders=[r for r in leader if r['provider']==provider]
            source_ordinary=[r for r in ordinary if r['provider']==provider]
            for key in ('circulating_market_cap','pe_ttm','pb','turnover_rate','amount','open_times','first_limit_seconds','market_limit_up_count','market_max_height','pre5_price','pre10_price','pre20_price','pre5_volume','pre10_volume','pre20_volume'):
                def value(r):
                    if key.startswith('pre'):
                        n,kind=key[3:].split('_');return r['windows'].get(n,{}).get('raw_price_change' if kind=='price' else 'volume_ratio')
                    if key=='first_limit_seconds':
                        t=r.get('first_limit_time')
                        return sum(int(v)*m for v,m in zip(t.split(':'),(3600,60,1))) if t else None
                    if key=='market_limit_up_count':return (r.get('market') or {}).get('limit_up_count')
                    if key=='market_max_height':return (r.get('market') or {}).get('market_max_height')
                    return r.get(key)
                a=distribution([value(r) for r in source_leaders]);b=distribution([value(r) for r in source_ordinary])
                compare.append({'provider':provider,'feature':key,'leader':a,'ordinary':b,'leader_total':len(source_leaders),'ordinary_total':len(source_ordinary),
                    'leader_coverage':a['n']/len(source_leaders) if source_leaders else None,'ordinary_coverage':b['n']/len(source_ordinary) if source_ordinary else None,'median_difference':a['median']-b['median'] if a['n'] and b['n'] else None,
                    'significance':'not_established_small_selected_dependent_diagnostic_sample','p_value':None})
        verified=[r for r in cohort if r['first_board_date']];unknown=sum(not r['cycle_complete'] for r in verified)
        success=len(leader);n=len(complete)
        known_success=sum(r['reached_5'] is True for r in verified);threshold_unknown=sum(r['reached_5'] is None for r in verified)
        return {'data':{'monthly':monthly,'comparison':compare,'cohort':{'first_boards':len(verified),'complete_cycles':n,'leaders_5_plus':success,
            'ordinary_below_5':len(ordinary),'unknown_outcomes':unknown,'promotion_rate':success/n if n else None,'promotion_ci':wilson(success,n),
            'known_reached_5':known_success,'unknown_threshold_outcomes':threshold_unknown,
            'censoring_bounds':[known_success/len(verified),(known_success+threshold_unknown)/len(verified)] if verified else None,
            'warning':'Complete-case diagnostic proportion, not population probability; Wilson interval assumes independence not established here'}},
            'meta':self.metadata(cohort,filters.get('mode','formal'))}

    def stock(self,code,mode='formal'):
        rows=[r for r in self.rows if r['stock_code']==code and (mode=='diagnostic' or research_state(r,publication_accepted=self.meta.get('strict_accepted') is True)=='verified')]
        return {'data':rows,'meta':self.metadata(rows,mode)}
