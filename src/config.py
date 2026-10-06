from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict

__all__ = ("env",)


class EnvConfig(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )


class Env(EnvConfig):
    PROJECT_NAME: str
    POSTGRES_VPS: str
    POSTGRES_HOME: str
    STEAM_FRIEND_IRENE_ID64: int
    STEAM_FRIEND_IRENE_ID32: int
    STEAM_IRENESTEST_USERNAME: str
    STEAM_IRENESTEST_PASSWORD: str
    STEAM_IRENESBOT_USERNAME: str
    STEAM_IRENESBOT_PASSWORD: str
    STRATZ_BEARER: str
    STEAM_API_KEY: str
    WEBHOOK_ERROR: str


env = Env()
