from datetime import date
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from app.providers.normalize import stock_code

class JobRequest(BaseModel):
    dataset: Literal['pools','daily','calendar','history']='pools'
    provider: Literal['akshare','baostock']='akshare'
    start_date: date
    end_date: date
    stock_code: str | None=None
    force: bool=False
    sample_size: int=Field(24,ge=1,le=100)
    all_stocks: bool=False
    @model_validator(mode='after')
    def validate_range(self):
        if self.end_date<self.start_date: raise ValueError('开始日期不能晚于结束日期')
        if (self.end_date-self.start_date).days>366: raise ValueError('单批最多 366 天，请按月份/年份分批')
        if self.dataset=='history':
            if self.provider!='baostock': raise ValueError('历史重建使用 BaoStock')
            if (self.start_date.year,self.start_date.month)!=(self.end_date.year,self.end_date.month): raise ValueError('历史任务按单月提交')
        elif self.provider!='akshare': raise ValueError('此数据集请使用 AKShare')
        if self.dataset=='daily':
            if not self.stock_code: raise ValueError('日线采集需要股票代码')
            self.stock_code=stock_code(self.stock_code)
        return self
