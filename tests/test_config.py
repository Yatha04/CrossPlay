"""Tests for config.py — env var loading and validation."""

import os
import pytest
from config import load_config, ConfigError


REQUIRED_VARS = {
    "SPOTIFY_CLIENT_ID": "test_client_id",
    "SPOTIFY_CLIENT_SECRET": "test_client_secret",
    "SPOTIFY_REDIRECT_URI": "http://localhost:8888/callback",
    "SPOTIFY_PLAYLIST_ID": "sp_playlist_123",
    "YT_OAUTH_JSON": "eyJ0ZXN0IjogdHJ1ZX0=",
    "YOUTUBE_PLAYLIST_ID": "yt_playlist_456",
}


@pytest.fixture()
def env_vars(monkeypatch):
    """Set all required env vars to valid test values."""
    for key, val in REQUIRED_VARS.items():
        monkeypatch.setenv(key, val)


def test_load_config_all_required(env_vars):
    cfg = load_config()
    assert cfg.spotify_client_id == "test_client_id"
    assert cfg.spotify_client_secret == "test_client_secret"
    assert cfg.spotify_redirect_uri == "http://localhost:8888/callback"
    assert cfg.spotify_playlist_id == "sp_playlist_123"
    assert cfg.yt_oauth_json == "eyJ0ZXN0IjogdHJ1ZX0="
    assert cfg.youtube_playlist_id == "yt_playlist_456"


def test_defaults_applied(env_vars):
    cfg = load_config()
    assert cfg.database_path == "sync.db"
    assert cfg.poll_interval_seconds == 180
    assert cfg.fuzzy_match_threshold == 85


def test_optional_overrides(env_vars, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", "/tmp/test.db")
    monkeypatch.setenv("POLL_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("FUZZY_MATCH_THRESHOLD", "90")
    cfg = load_config()
    assert cfg.database_path == "/tmp/test.db"
    assert cfg.poll_interval_seconds == 60
    assert cfg.fuzzy_match_threshold == 90


@pytest.mark.parametrize("missing_var", list(REQUIRED_VARS.keys()))
def test_missing_required_var_raises(env_vars, monkeypatch, missing_var):
    monkeypatch.delenv(missing_var)
    with pytest.raises(ConfigError, match=missing_var):
        load_config()


def test_config_is_immutable(env_vars):
    cfg = load_config()
    with pytest.raises(AttributeError):
        cfg.spotify_client_id = "something_else"
