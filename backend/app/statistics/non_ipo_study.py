"""Read-only non-IPO study on the existing unique observation segments."""
from app.statistics.history_limit_queries import dataset,rows

VERSION='non-ipo-dragon-study-v1'
def study(year=None,month=None,height=5,cohort='all',page=1,page_size=30,exclude_st=False,exclude_ipo=False,non_ipo_only=True):
 with dataset() as (db,meta):
  if 'classification' not in meta:raise FileNotFoundError('Matching cycle classification snapshot required')
  from app.statistics.stock_scope import apply_to_dataset
  apply_to_dataset(db,meta,exclude_st,exclude_ipo,cycle_scope=True)
  db.execute('CREATE TEMP VIEW study AS SELECT c.*,k.* EXCLUDE(stock_code,run_start) FROM cycles c JOIN cycle_classes k USING(stock_code,run_start)')
  population="ipo_origin='non_ipo'" if non_ipo_only else 'TRUE'
  evidence="TRUE" if cohort=='all' else "status='confirmed'" if cohort=='confirmed' else "status='reconstructed_candidate'"
  yearly=rows(db,f'''SELECT year(run_start) AS year,count(*) source_segments,
   count(*) FILTER(WHERE ipo_origin='ipo_initial') ipo_excluded,
   count(*) FILTER(WHERE ipo_origin='unknown') ipo_unknown,
   count(*) FILTER(WHERE {population}) non_ipo,
   count(*) FILTER(WHERE {population} AND first_board_date IS NOT NULL) trusted_start,
   count(*) FILTER(WHERE {population} AND first_board_date IS NULL) start_unknown,
   count(*) FILTER(WHERE {population} AND final_height IS NOT NULL) complete,
   count(*) FILTER(WHERE {population} AND final_height IS NULL) incomplete,
   count(*) FILTER(WHERE {population} AND st_status='unknown') st_unknown,
   count(*) FILTER(WHERE {population} AND cycle_identity_quality='possible_fragment') possible_fragments,
   count(*) FILTER(WHERE {population} AND height>={int(height)}) high_samples,
   count(*) FILTER(WHERE {population} AND height>={int(height)} AND first_board_date IS NOT NULL) high_trusted_start,
   count(*) FILTER(WHERE {population} AND height>={int(height)} AND first_board_date IS NULL) high_start_unknown,
   count(*) FILTER(WHERE {population} AND height>={int(height)} AND final_height IS NOT NULL) high_complete,
   count(*) FILTER(WHERE {population} AND height>={int(height)} AND final_height IS NULL) high_incomplete,
   count(*) FILTER(WHERE ipo_origin='ipo_initial' AND height>={int(height)}) high_ipo_excluded,
   count(*) FILTER(WHERE ipo_origin='unknown' AND height>={int(height)}) high_ipo_unknown,
   {','.join(f"count(*) FILTER(WHERE {population} AND height>={h}) ge{h},count(*) FILTER(WHERE {population} AND height>={h} AND status='confirmed') confirmed_ge{h},count(*) FILTER(WHERE {population} AND height>={h} AND status='reconstructed_candidate') candidate_ge{h}" for h in [3,5,7,10])}
   FROM study WHERE {evidence} GROUP BY 1 ORDER BY 1''')
  # Keep all ten years explicit even when the selected positive-evidence cohort is empty.
  keys=list(yearly[0]) if yearly else ['year']
  yearly=[next((r for r in yearly if r['year']==y),{k:y if k=='year' else 0 for k in keys}) for y in range(2017,2027)]
  for r in yearly:r['complete_rate']=r.get('high_complete',0)/r['high_samples'] if r.get('high_samples') else None
  base=f"{population} AND {evidence} AND height>=?";args=[height]
  if year:base+=' AND year(run_start)=?';args.append(year)
  monthly=rows(db,f'''SELECT month(first_board_date) AS month,count(*) n,
    count(*) FILTER(WHERE status='confirmed') confirmed,count(*) FILTER(WHERE status='reconstructed_candidate') candidate,
    count(*) FILTER(WHERE final_height IS NOT NULL) complete
    FROM study WHERE {base} AND first_board_date IS NOT NULL GROUP BY 1 ORDER BY 1''',args)
  eligible=sum(r['n'] for r in monthly)
  monthly=[next((r for r in monthly if r['month']==m),dict(month=m,n=0 if eligible else None,confirmed=0 if eligible else None,candidate=0 if eligible else None,complete=0 if eligible else None)) for m in range(1,13)]
  quality=rows(db,f'''SELECT count(*) non_ipo,count(*) FILTER(WHERE first_board_date IS NOT NULL) trusted_start,
   count(*) FILTER(WHERE first_board_date IS NULL) start_unknown,count(*) FILTER(WHERE final_height IS NULL) incomplete,
   count(*) FILTER(WHERE st_status='unknown') st_unknown,count(*) FILTER(WHERE cycle_identity_quality='possible_fragment') possible_fragments
   FROM study WHERE {base}''',args)[0]
  where=base
  if month:where+=' AND month(first_board_date)=?';args.append(month)
  total=db.execute('SELECT count(*) FROM study WHERE '+where,args).fetchone()[0]
  samples=rows(db,'SELECT * FROM study WHERE '+where+' ORDER BY height DESC,run_start DESC,stock_code LIMIT ? OFFSET ?',args+[page_size,(page-1)*page_size])
  return dict(stock_scope=meta['stock_scope'],non_ipo_only=non_ipo_only,version=VERSION,yearly=yearly,months=monthly,samples=samples,total=total,page=page,page_size=page_size,quality=quality,
    filters=dict(year=year,month=month,height=height,cohort=cohort,exclude_st=exclude_st,exclude_ipo=exclude_ipo),source=dict(statistics=meta['version'],classification=meta['classification']['version'],snapshot=meta['classification']['source_snapshot']),
    warning=('探索性非IPO观察段研究；IPO归属unknown不进入主样本。' if non_ipo_only else '探索性观察段研究；股票范围统一筛选，IPO初始连板归属独立保留。')+'独立确认仅指段内正向事件，起点可信不等于严格验收。ST与可交易性独立未知。')
