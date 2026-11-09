"""Conservative extraction of explicit all-session closing-limit statements.
No stock-specific dates or target heights. Ambiguous/intraday language is not
expanded to a daily series. The caller must verify original PDF provenance.
"""
import re
from datetime import date
from app.providers.normalize import stock_code


def closing_ranges(text,publication_date,calendar):
    compact=re.sub(r'\s+','',text)
    match=re.search(r'证券代码[：:](\d{6})',compact)
    if not match:return []
    code=stock_code(match[1]);claims=[]
    # Explicit N of N closing-limit sessions over a fully specified month/day range.
    pattern=r'自(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日至(\d{1,2})月(\d{1,2})日(\d+)个交易日中(\d+)个交易日收盘价格涨停'
    for m in re.finditer(pattern,compact):
        if m[6]!=m[7]:continue
        year=int(m[1]) if m[1] else publication_date.year
        start=date(year,int(m[2]),int(m[3]));end=date(year,int(m[4]),int(m[5]))
        claims.append((start,end,int(m[6]),m[0]))
    # Dated closing-limit statement immediately followed by its continuous run.
    pattern=r'(\d{4})年(\d{1,2})月(\d{1,2})日公司股票以涨停价收盘[，,]自(\d{1,2})月(\d{1,2})日至(\d{1,2})日[，,]已连续(\d+)个交易日涨停'
    for m in re.finditer(pattern,compact):
        if m[3]!=m[6] or m[2]!=m[4]:continue
        start=date(int(m[1]),int(m[4]),int(m[5]));end=date(int(m[1]),int(m[2]),int(m[3]))
        claims.append((start,end,int(m[7]),m[0]))
    output=[];seen=set()
    for start,end,count,quote in claims:
        days=[d for d in calendar if start<=d<=end]
        if start>end or end>=publication_date or len(days)!=count or not days or days[0]!=start or days[-1]!=end:continue
        key=(code,start,end)
        if key in seen:continue
        seen.add(key);output.append(dict(stock_code=code,start=start,end=end,stated_sessions=count,dates=days,excerpt=quote,
            statement_type='all_sessions_closed_at_limit',not_first_board_or_final_height=True))
    return output
