from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.db import DatabaseConfig


class TelegramCredentials(BaseModel):
    api_hash: str
    api_id: str
    session: str


class RedisConfig(BaseModel):
    host: str
    port: int


class ExporterConfig(BaseModel):
    required_exporters: list[str]
    redis: RedisConfig


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
    )

    database: DatabaseConfig
    telegram: TelegramCredentials
    exporters: ExporterConfig
