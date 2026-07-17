from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    redis_url: str
    database_url: str
    # Week 9 admin API: policies is SELECT-only for shieldstream_app (Phase 1's
    # RLS design, see DECISIONS.md rev #4) — writing a policy update needs the
    # shieldstream_worker role instead, the same one the analytics/alert
    # consumers already use, and the one DECISIONS.md already earmarked for
    # "admin policy API" when the role grants were first designed.
    admin_database_url: str
    log_level: str = "info"
    rate_limit_fail_open: bool = True  # see Week 5 decision log

    # extra="ignore": OTEL_EXPORTER_OTLP_ENDPOINT lives in .env / the compose
    # environment too, but is read directly by app.tracing via os.environ,
    # not through this model — without "ignore" pydantic-settings rejects
    # any .env key that isn't a declared field.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
