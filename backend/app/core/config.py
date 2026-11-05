from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]
class Settings(BaseSettings):
    database_url: str = 'postgresql+psycopg://localhost/asharedragon'
    tushare_token: str = ''
    request_timeout: int = 25
    request_retries: int = 2
    request_interval: float = 1.0
    cors_origins: list[str] = ['http://localhost:3000','http://127.0.0.1:3000']
    model_config = SettingsConfigDict(env_file=ROOT/'.env', extra='ignore')
settings = Settings()
