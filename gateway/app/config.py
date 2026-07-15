from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str
    database_url: str
    log_level: str = "info"
    rate_limit_fail_open: bool = True  # see Week 5 decision log

    model_config = SettingsConfigDict(env_file=".env")


settings = Settings()
