from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from shared.db import DatabaseConfig


class TelegramCredentials(BaseModel):
    api_hash: str
    api_id: str
    session: str


class RedisConfig(BaseModel):
    host: str
    port: int


class JsonConfig(BaseModel):
    path: str


class ExporterConfig(BaseModel):
    required_exporters: list[str]
    redis: RedisConfig
    json_exporter: JsonConfig = Field(alias="json")


class Config(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_nested_delimiter="__",
    )

    database: DatabaseConfig
    telegram: TelegramCredentials
    exporters: ExporterConfig
