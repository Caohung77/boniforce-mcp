from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="BF_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    issuer_url: str = Field(default="http://localhost:8000")
    db_path: Path = Field(default=Path("./boniforce-mcp.sqlite"))
    encryption_key: str = ""
    oauth_signing_key: str = ""
    api_base: str = "https://api.boniforce.de"
    host: str = "127.0.0.1"
    port: int = 8000
    jwt_audience: str = ""

    @property
    def issuer(self) -> str:
        return self.issuer_url.rstrip("/")

    @property
    def resource(self) -> str:
        return f"{self.issuer}/mcp"

    @property
    def audience(self) -> str:
        return self.jwt_audience or self.resource

    @property
    def jwks_uri(self) -> str:
        return f"{self.issuer}/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()
