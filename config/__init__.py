import sys

import structlog
from pydantic import BaseModel, Field, HttpUrl, ValidationError, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_log = structlog.get_logger()


class ScraperSettings(BaseModel):
    query: str = "python"
    location: str = "Warszawa"
    limit: str = "all"
    force_login: bool = False
    proxy: HttpUrl | None = None

    @field_validator("limit")
    @classmethod
    def validate_limit(cls, v: str) -> str:
        if v.lower() == "all":
            return v
        try:
            int(v)
            return v
        except ValueError:
            raise ValueError("limit must be 'all' or an integer")


class RunnerSettings(BaseModel):
    package_name: str = "crawler"
    spider_name: str = "indeed_basic"


class Settings(BaseSettings):
    scraper: ScraperSettings = Field(default_factory=ScraperSettings)
    runner: RunnerSettings = Field(default_factory=RunnerSettings)
    captcha_api_key: str = ""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        nested_model_default_partial_update=True,
    )


try:
    settings = Settings()
except ValidationError as e:
    _log.error("Invalid configuration:")
    for err in e.errors():
        field = "__".join(str(loc).upper() for loc in err["loc"])
        _log.error(f"  {field}: {err['msg']}")
    sys.exit(1)

# Parse flat CLI overrides to keep main.py clean and allow simple flags
if any(
    arg in sys.argv
    for arg in ["--query", "--location", "--limit", "--proxy", "--force-login"]
):
    import argparse

    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--query", type=str)
    parser.add_argument("--location", type=str)
    parser.add_argument("--limit", type=str)
    parser.add_argument("--force-login", action="store_true")
    parser.add_argument("--proxy", type=str)
    args, _ = parser.parse_known_args()

    if args.query is not None:
        settings.scraper.query = args.query
    if args.location is not None:
        settings.scraper.location = args.location
    if args.limit is not None:
        settings.scraper.limit = args.limit
    if args.force_login:
        settings.scraper.force_login = args.force_login
    if args.proxy is not None:
        settings.scraper.proxy = HttpUrl(args.proxy)

