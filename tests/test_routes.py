"""Tests for FastAPI routes — OAuth flows and health check."""

import base64
import json
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import router
from db.migrations import run_migrations
from db.queries import upsert_auth_token, insert_sync_log, upsert_playlist_state


@dataclass(frozen=True)
class FakeConfig:
    spotify_client_id: str = "test_client_id"
    spotify_client_secret: str = "test_secret"
    spotify_redirect_uri: str = "http://localhost:8888/callback"
    spotify_playlist_id: str = "sp_playlist_123"
    yt_oauth_json: str = ""
    youtube_playlist_id: str = "yt_playlist_456"
    database_path: str = ""
    poll_interval_seconds: int = 180
    fuzzy_match_threshold: int = 85


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


@pytest.fixture()
def client(db_path):
    app = FastAPI()
    app.include_router(router)
    cfg = FakeConfig(database_path=db_path)
    app.state.config = cfg
    return TestClient(app)


# ---------------------------------------------------------------------------
# Spotify OAuth
# ---------------------------------------------------------------------------

class TestSpotifyAuth:
    @patch("api.routes.build_auth_manager")
    def test_redirect_to_spotify(self, mock_build, client):
        mock_am = MagicMock()
        mock_am.get_authorize_url.return_value = "https://accounts.spotify.com/authorize?foo=bar"
        mock_build.return_value = mock_am

        resp = client.get("/auth/spotify", follow_redirects=False)
        assert resp.status_code == 307
        assert "accounts.spotify.com" in resp.headers["location"]

    @patch("api.routes.build_auth_manager")
    @patch("api.routes.store_spotify_token")
    def test_callback_success(self, mock_store, mock_build, client):
        mock_am = MagicMock()
        mock_am.get_access_token.return_value = {
            "access_token": "at_123",
            "refresh_token": "rt_456",
            "expires_in": 3600,
        }
        mock_build.return_value = mock_am

        resp = client.get("/auth/spotify/callback?code=test_auth_code")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        mock_store.assert_called_once()

    @patch("api.routes.build_auth_manager")
    def test_callback_bad_code(self, mock_build, client):
        mock_am = MagicMock()
        mock_am.get_access_token.side_effect = Exception("invalid_grant")
        mock_build.return_value = mock_am

        resp = client.get("/auth/spotify/callback?code=bad_code")
        assert resp.status_code == 400
        assert "error" in resp.json()["status"]

    def test_callback_missing_code(self, client):
        resp = client.get("/auth/spotify/callback")
        assert resp.status_code == 422  # FastAPI validation error


# ---------------------------------------------------------------------------
# YouTube Music OAuth
# ---------------------------------------------------------------------------

class TestYouTubeAuth:
    def test_auth_returns_instructions(self, client):
        resp = client.get("/auth/youtube")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "info"
        assert "ytmusicapi" in data["message"]

    @patch("api.routes.store_youtube_token")
    def test_callback_success(self, mock_store, client):
        oauth_data = json.dumps({"access_token": "ya29.xxx", "refresh_token": "1//xxx"})
        b64 = base64.b64encode(oauth_data.encode()).decode()

        resp = client.post(
            "/auth/youtube/callback",
            json={"oauth_json_b64": b64},
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
        mock_store.assert_called_once()
        # Verify the decoded JSON was passed
        call_args = mock_store.call_args
        assert "ya29.xxx" in call_args.kwargs.get("oauth_json_str", call_args[1].get("oauth_json_str", str(call_args)))

    def test_callback_missing_payload(self, client):
        resp = client.post("/auth/youtube/callback", json={})
        assert resp.status_code == 400
        assert resp.json()["status"] == "error"

    def test_callback_invalid_base64(self, client):
        resp = client.post(
            "/auth/youtube/callback",
            json={"oauth_json_b64": "not-valid-base64!!!"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

class TestHealth:
    def test_healthy_with_both_tokens(self, client, db_path):
        upsert_auth_token(db_path, "spotify", "user_b", "at", "rt", "sp_pl")
        upsert_auth_token(db_path, "youtube_music", "user_a", "{}", "", "yt_pl")

        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "healthy"
        assert data["tokens"]["spotify"] == "valid"
        assert data["tokens"]["youtube_music"] == "valid"

    def test_degraded_missing_spotify(self, client, db_path):
        upsert_auth_token(db_path, "youtube_music", "user_a", "{}", "", "yt_pl")

        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["tokens"]["spotify"] == "missing"

    def test_degraded_missing_youtube(self, client, db_path):
        upsert_auth_token(db_path, "spotify", "user_b", "at", "rt", "sp_pl")

        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["tokens"]["youtube_music"] == "missing"

    def test_degraded_expired_spotify(self, client, db_path):
        upsert_auth_token(
            db_path, "spotify", "user_b", "at", "rt", "sp_pl",
            token_expiry="2020-01-01T00:00:00+00:00",
        )
        upsert_auth_token(db_path, "youtube_music", "user_a", "{}", "", "yt_pl")

        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["tokens"]["spotify"] == "expired"

    def test_degraded_too_many_failures(self, client, db_path):
        upsert_auth_token(db_path, "spotify", "user_b", "at", "rt", "sp_pl")
        upsert_auth_token(db_path, "youtube_music", "user_a", "{}", "", "yt_pl")

        for i in range(6):
            insert_sync_log(
                db_path, "spotify", f"sp_{i}", "youtube_music",
                status="failed", error_message="test error",
            )

        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["recent_failures"] == 6

    def test_health_includes_poll_age(self, client, db_path):
        upsert_auth_token(db_path, "spotify", "user_b", "at", "rt", "sp_pl")
        upsert_auth_token(db_path, "youtube_music", "user_a", "{}", "", "yt_pl")
        upsert_playlist_state(db_path, "spotify", "sp_playlist_123", "snap1", ["t1"])

        resp = client.get("/health")
        data = resp.json()
        # Should have a numeric value for spotify (just polled)
        assert data["last_poll_seconds_ago"]["spotify"] is not None
        assert isinstance(data["last_poll_seconds_ago"]["spotify"], int)
        # YouTube not polled yet
        assert data["last_poll_seconds_ago"]["youtube_music"] is None

    def test_health_no_tokens_at_all(self, client):
        resp = client.get("/health")
        data = resp.json()
        assert data["status"] == "degraded"
        assert data["tokens"]["spotify"] == "missing"
        assert data["tokens"]["youtube_music"] == "missing"
        assert data["recent_failures"] == 0
