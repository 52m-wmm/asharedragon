"""Provider-isolated, append-only observations; revisions retain their raw source."""
from datetime import date, datetime, time, timezone
from decimal import Decimal
from sqlalchemy import Date, DateTime, Numeric, JSON, String, UniqueConstraint, Index, ForeignKey
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def now(): return datetime.now(timezone.utc)
class Base(DeclarativeBase): pass
class Evidence:
    provider: Mapped[str] = mapped_column(String(30), index=True)
    endpoint: Mapped[str] = mapped_column(String(80))
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    source_record_id: Mapped[str | None] = mapped_column(String(160))
    raw_id: Mapped[int] = mapped_column(ForeignKey('raw_observations.id'))
    ingestion_status: Mapped[str] = mapped_column(default='success')
    error_message: Mapped[str | None]

class Job(Base):
    __tablename__ = 'ingestion_jobs'
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    provider: Mapped[str]
    dataset: Mapped[str]
    start_date: Mapped[date]
    end_date: Mapped[date]
    parameters: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(index=True, default='queued')
    rows_received: Mapped[int] = mapped_column(default=0)
    rows_inserted: Mapped[int] = mapped_column(default=0)
    rows_updated: Mapped[int] = mapped_column(default=0)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_message: Mapped[str | None]

class Raw(Base):
    __tablename__ = 'raw_observations'
    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('ingestion_jobs.job_id'), index=True)
    provider: Mapped[str]
    endpoint: Mapped[str]
    trade_date: Mapped[date | None] = mapped_column(index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    available_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_record_id: Mapped[str | None]
    parameters: Mapped[dict] = mapped_column(JSON)
    raw_payload: Mapped[dict] = mapped_column(JSON)
    sha256: Mapped[str]
    ingestion_status: Mapped[str]
    error_message: Mapped[str | None]
    __table_args__ = (UniqueConstraint('provider','endpoint','sha256','trade_date'),)

class Coverage(Base):
    __tablename__ = 'data_coverage'
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str]
    dataset: Mapped[str]
    trade_date: Mapped[date] = mapped_column(index=True)
    status: Mapped[str]
    row_count: Mapped[int | None]
    error_message: Mapped[str | None]
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('provider','dataset','trade_date'),)

class TradingDay(Base):
    __tablename__ = 'trading_days'
    trade_date: Mapped[date] = mapped_column(primary_key=True)
    exchange: Mapped[str] = mapped_column(primary_key=True, default='SSE')
    source: Mapped[str]
    raw_id: Mapped[int] = mapped_column(ForeignKey('raw_observations.id'))

class Stock(Evidence, Base):
    __tablename__ = 'stocks'
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    stock_name: Mapped[str | None]
    exchange: Mapped[str]
    board: Mapped[str | None]
    industry: Mapped[str | None]
    listing_date: Mapped[date | None]
    delisting_date: Mapped[date | None]
    status: Mapped[str | None]
    is_st: Mapped[bool | None]
    valid_from: Mapped[date]
    valid_to: Mapped[date | None]
    __table_args__ = (UniqueConstraint('stock_code','provider','endpoint','trade_date','raw_id'),)

class Daily(Evidence, Base):
    __tablename__ = 'stock_daily'
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    adjustment: Mapped[str] = mapped_column(default='none')
    is_st: Mapped[bool | None]
    trade_status: Mapped[bool | None]
    open: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    low: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    close: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    pre_close: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    turnover_rate: Mapped[float | None]
    amplitude: Mapped[float | None]
    pct_change: Mapped[float | None]
    total_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    circulating_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    free_float_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    pe_ttm: Mapped[float | None]
    pb: Mapped[float | None]
    __table_args__ = (UniqueConstraint('stock_code','trade_date','provider','endpoint','adjustment','raw_id'),)

class Event(Evidence, Base):
    __tablename__ = 'limitup_events'
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    stock_name: Mapped[str | None]
    industry: Mapped[str | None]
    limit_type: Mapped[str] = mapped_column(String(12))
    first_limit_time: Mapped[time | None]
    last_limit_time: Mapped[time | None]
    open_times: Mapped[int | None]
    seal_amount: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    seal_volume: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    turnover_rate: Mapped[float | None]
    consecutive_limit_days: Mapped[int | None]
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    is_one_word_limit: Mapped[bool | None]
    is_t_shape_limit: Mapped[bool | None]
    is_sealed_at_close: Mapped[bool | None]
    limit_reason: Mapped[str | None]
    data_source: Mapped[str]
    amount: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    circulating_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    total_market_cap: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    pe_ttm: Mapped[float | None]
    __table_args__ = (UniqueConstraint('stock_code','trade_date','limit_type','provider','endpoint','raw_id'),)

class Theme(Base):
    __tablename__ = 'themes'
    theme_id: Mapped[str] = mapped_column(primary_key=True)
    theme_name: Mapped[str]
    category: Mapped[str | None]
    source: Mapped[str]
class StockTheme(Evidence, Base):
    __tablename__ = 'stock_themes'
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(index=True)
    theme_id: Mapped[str] = mapped_column(ForeignKey('themes.theme_id'))
    valid_from: Mapped[date]
    valid_to: Mapped[date | None]
    source: Mapped[str]
    __table_args__ = (UniqueConstraint('stock_code','theme_id','valid_from','provider','raw_id'),)
class Sentiment(Base):
    __tablename__ = 'market_sentiment_daily'
    trade_date: Mapped[date] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(primary_key=True)
    limit_up_count: Mapped[int | None]
    limit_down_count: Mapped[int | None]
    broken_limit_count: Mapped[int | None]
    broken_limit_rate: Mapped[float | None]
    first_board_count: Mapped[int | None]
    second_board_count: Mapped[int | None]
    third_board_count: Mapped[int | None]
    fourth_plus_board_count: Mapped[int | None]
    max_consecutive_boards: Mapped[int | None]
    second_board_promotion_rate: Mapped[float | None]
    third_board_promotion_rate: Mapped[float | None]
    total_market_turnover: Mapped[Decimal | None] = mapped_column(Numeric(24,2))
    advancing_stock_count: Mapped[int | None]
    declining_stock_count: Mapped[int | None]
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    methodology: Mapped[str] = mapped_column(default='close-v1-provider-universe')
class DragonLabel(Base):
    __tablename__ = 'dragon_labels'
    id: Mapped[int] = mapped_column(primary_key=True)
    stock_code: Mapped[str] = mapped_column(index=True)
    start_date: Mapped[date] = mapped_column(index=True)
    dragon_type: Mapped[str]
    max_consecutive_boards: Mapped[int | None]
    max_gain_5d: Mapped[float | None]
    max_gain_10d: Mapped[float | None]
    max_gain_20d: Mapped[float | None]
    max_drawdown_20d: Mapped[float | None]
    cycle_duration: Mapped[int | None]
    label_version: Mapped[str]
    provider: Mapped[str]
    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    definition: Mapped[dict] = mapped_column(JSON)
    __table_args__ = (UniqueConstraint('stock_code','start_date','dragon_type','label_version','provider'),)

# v0.2 reconstruction is deliberately separate from observed event tables.
class ReconstructionRun(Base):
    __tablename__ = 'reconstruction_runs'
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey('ingestion_jobs.job_id'))
    start_date: Mapped[date]
    end_date: Mapped[date]
    algorithm_version: Mapped[str]
    provider: Mapped[str] = mapped_column(default='baostock')
    scope: Mapped[str]  # sample or provider_universe; neither claims all A shares
    parameters: Mapped[dict] = mapped_column(JSON)
    source_raw_ids: Mapped[list] = mapped_column(JSON)
    status: Mapped[str]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ReconstructionFact(Base):
    __tablename__ = 'reconstructed_limits'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('reconstruction_runs.run_id'), index=True)
    stock_code: Mapped[str] = mapped_column(String(12), index=True)
    trade_date: Mapped[date] = mapped_column(index=True)
    daily_id: Mapped[int | None] = mapped_column(ForeignKey('stock_daily.id'))
    classification: Mapped[str]  # up / not_up / suspended / no_limit / unknown / missing
    reason: Mapped[str]
    limit_price: Mapped[Decimal | None] = mapped_column(Numeric(20,4))
    consecutive_limit_days: Mapped[int | None]
    first_board_date: Mapped[date | None]
    quality: Mapped[str] = mapped_column(default='rule_reconstructed')
    __table_args__ = (UniqueConstraint('run_id','stock_code','trade_date'),)

class ReconstructionDay(Base):
    __tablename__ = 'reconstruction_daily'
    run_id: Mapped[str] = mapped_column(ForeignKey('reconstruction_runs.run_id'), primary_key=True)
    trade_date: Mapped[date] = mapped_column(primary_key=True, index=True)
    expected_stocks: Mapped[int]  # full historical provider roster, not requested sample
    selected_stocks: Mapped[int]
    received_stocks: Mapped[int]
    classified_stocks: Mapped[int]
    unknown_stocks: Mapped[int]
    coverage_rate: Mapped[float | None]
    classification_rate: Mapped[float | None]
    limit_up_count: Mapped[int | None]  # classified sample count, never advertised as complete market
    max_consecutive_boards: Mapped[int | None]
    second_board_promotion_rate: Mapped[float | None]
    third_board_promotion_rate: Mapped[float | None]
    quality: Mapped[str]

class ValidationReport(Base):
    __tablename__ = 'reconstruction_validations'
    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[str] = mapped_column(ForeignKey('reconstruction_runs.run_id'), index=True)
    trade_date: Mapped[date] = mapped_column(index=True)
    reference_provider: Mapped[str] = mapped_column(default='akshare')
    reference_raw_id: Mapped[int | None] = mapped_column(ForeignKey('raw_observations.id'))
    result: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('run_id','trade_date','reference_raw_id'),)
