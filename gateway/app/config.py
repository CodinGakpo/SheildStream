from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str
    database_url: str
    log_level: str = "info"
    rate_limit_fail_open: bool = True  # see Week 5 decision log

    # extra="ignore": OTEL_EXPORTER_OTLP_ENDPOINT lives in .env / the compose
    # environment too, but is read directly by app.tracing via os.environ,
    # not through this model — without "ignore" pydantic-settings rejects
    # any .env key that isn't a declared field.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
