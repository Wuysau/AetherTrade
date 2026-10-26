"""Explicit, local-only runtime configuration; no credentials in defaults."""
from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE_URL = "sqlite:///" + (PROJECT_ROOT / ".runtime/aethertrade.sqlite3").as_posix()
DEFAULT_ORIGINS = (
    "http://127.0.0.1:8787", "http://localhost:8787",
    "http://127.0.0.1:5173", "http://localhost:5173",
)


def _flag(value: str) -> bool:
    if value.lower() not in {"true", "false", "1", "0"}:
        raise ValueError("Boolean configuration must be true or false")
    return value.lower() in {"true", "1"}


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    host: str = "127.0.0.1"
    port: int = 8787
    web_dist: Path = PROJECT_ROOT / "web/dist"
    allowed_origins: tuple[str, ...] = DEFAULT_ORIGINS
    session_hours: int = 12
    market_poll_seconds: int = 15
    quote_max_age_seconds: int = 90
    market_enabled: bool = True
    testing: bool = False
    cookie_name: str = "aethertrade_session"
    cookie_secure: bool = False

    def __post_init__(self) -> None:
        if self.host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("The local console must bind a loopback address")
        if not 1 <= self.port <= 65535:
            raise ValueError("Invalid server port")
        if not 1 <= self.session_hours <= 168:
            raise ValueError("Session duration must be 1..168 hours")
        if not 1 <= self.market_poll_seconds <= 3600:
            raise ValueError("Market polling interval must be 1..3600 seconds")
        if not 1 <= self.quote_max_age_seconds <= 300:
            raise ValueError("Quote maximum age must be 1..300 seconds")
        if not self.database_url.startswith(("sqlite:///", "postgresql+psycopg://")):
            raise ValueError("Use an explicit SQLite or postgresql+psycopg database URL")
        if not self.allowed_origins:
            raise ValueError("At least one explicit browser origin is required")
        for origin in self.allowed_origins:
            parsed = urlsplit(origin)
            if (parsed.scheme not in {"http", "https"} or not parsed.netloc
                    or parsed.path or parsed.query or parsed.fragment
                    or parsed.username or "*" in origin):
                raise ValueError("Origins must be exact HTTP(S) origins without paths")
            if not self.testing and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
                raise ValueError("Local console browser origins must be loopback")
        object.__setattr__(self, "web_dist", Path(self.web_dist).resolve())

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(PROJECT_ROOT / ".env.runtime", override=False)
        host = os.getenv("AETHERTRADE_HOST", "127.0.0.1")
        port = int(os.getenv("AETHERTRADE_PORT", "8787"))
        default_origins = list(DEFAULT_ORIGINS)
        for name in ("127.0.0.1", "localhost"):
            value = f"http://{name}:{port}"
            if value not in default_origins:
                default_origins.append(value)
        origins = os.getenv("AETHERTRADE_ALLOWED_ORIGINS", ",".join(default_origins))
        return cls(
            database_url=os.getenv("AETHERTRADE_DATABASE_URL", DEFAULT_DATABASE_URL),
            host=host, port=port,
            web_dist=Path(os.getenv("AETHERTRADE_WEB_DIST", str(PROJECT_ROOT / "web/dist"))),
            allowed_origins=tuple(value.strip() for value in origins.split(",") if value.strip()),
            session_hours=int(os.getenv("AETHERTRADE_SESSION_HOURS", "12")),
            market_poll_seconds=int(os.getenv("AETHERTRADE_MARKET_POLL_SECONDS", "15")),
            quote_max_age_seconds=int(os.getenv("AETHERTRADE_QUOTE_MAX_AGE_SECONDS", "90")),
            market_enabled=_flag(os.getenv("AETHERTRADE_MARKET_ENABLED", "true")),
            cookie_secure=_flag(os.getenv("AETHERTRADE_COOKIE_SECURE", "false")),
        )
