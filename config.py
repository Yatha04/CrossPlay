"""Configuration loader — reads .env and exposes typed constants."""

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


class ConfigError(Exception):
    """Raised when a required config value is missing or invalid."""


def _require(key: str) -> str:
    """Return an env var or raise ConfigError with a clear message."""
    value = os.getenv(key)
    if not value:
        raise ConfigError(f"Missing required environment variable: {key}")
    return value


def _optional(key: str, default: str = "") -> str:
    return os.getenv(key, default)


@dataclass(frozen=True)
class Config:
    # Spotify
    spotify_client_id: str
    spotify_client_secret: str
    spotify_redirect_uri: str
    spotify_playlist_id: str

    # YouTube Music
    yt_oauth_json: str
    youtube_playlist_id: str

    # Database
    database_path: str

    # Sync tuning
    poll_interval_seconds: int
    fuzzy_match_threshold: int


def load_config() -> Config:
    """Build a Config from environment variables.

    Raises ConfigError for any missing required value.
    """
    return Config(
        spotify_client_id=_require("SPOTIFY_CLIENT_ID"),
        spotify_client_secret=_require("SPOTIFY_CLIENT_SECRET"),
        spotify_redirect_uri=_require("SPOTIFY_REDIRECT_URI"),
        spotify_playlist_id=_require("SPOTIFY_PLAYLIST_ID"),
        yt_oauth_json=_require("YT_OAUTH_JSON"),
        youtube_playlist_id=_require("YOUTUBE_PLAYLIST_ID"),
        database_path=_optional("DATABASE_PATH", "sync.db"),
        poll_interval_seconds=int(_optional("POLL_INTERVAL_SECONDS", "180")),
        fuzzy_match_threshold=int(_optional("FUZZY_MATCH_THRESHOLD", "85")),
    )
