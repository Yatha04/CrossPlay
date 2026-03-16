"""Tests for auth modules — token storage, expiry detection, refresh logic."""

import base64
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from db.migrations import run_migrations
from db.queries import get_auth_token
from auth.spotify_auth import (
    _is_expired,
    _expiry_from_token_info,
    store_spotify_token,
    get_spotify_client,
)
from auth.youtube_auth import store_youtube_token, get_ytmusic_client


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


# ---------------------------------------------------------------------------
# Spotify: expiry detection
# ---------------------------------------------------------------------------

class TestSpotifyExpiry:
    def test_none_is_expired(self):
        assert _is_expired(None)

    def test_empty_string_is_expired(self):
        assert _is_expired("")

    def test_past_is_expired(self):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        assert _is_expired(past)

    def test_future_is_not_expired(self):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        assert not _is_expired(future)

    def test_near_future_is_expired(self):
        """Tokens expiring within 5 minutes should be treated as expired (proactive refresh)."""
        near = (datetime.now(timezone.utc) + timedelta(minutes=3)).isoformat()
        # _is_expired checks >= current time, not with a buffer,
        # so 3 minutes out should NOT be expired yet
        assert not _is_expired(near)

    def test_invalid_string_is_expired(self):
        assert _is_expired("not-a-date")


class TestSpotifyExpiryFromTokenInfo:
    def test_with_expires_at(self):
        future_ts = time.time() + 3600
        result = _expiry_from_token_info({"expires_at": future_ts})
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_with_expires_in(self):
        result = _expiry_from_token_info({"expires_in": 7200})
        dt = datetime.fromisoformat(result)
        assert dt > datetime.now(timezone.utc)

    def test_default_3600(self):
        result = _expiry_from_token_info({})
        dt = datetime.fromisoformat(result)
        assert dt > datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Spotify: token storage
# ---------------------------------------------------------------------------

class TestSpotifyTokenStorage:
    def test_store_and_retrieve(self, db_path):
        token_info = {
            "access_token": "sp_access_123",
            "refresh_token": "sp_refresh_456",
            "expires_at": time.time() + 3600,
        }
        store_spotify_token(db_path, "user_b", token_info, "pl_spotify_1")
        row = get_auth_token(db_path, "spotify", "user_b")
        assert row is not None
        assert row["access_token"] == "sp_access_123"
        assert row["refresh_token"] == "sp_refresh_456"
        assert row["playlist_id"] == "pl_spotify_1"

    def test_overwrite_on_re_auth(self, db_path):
        token1 = {"access_token": "old", "refresh_token": "old_ref", "expires_in": 3600}
        token2 = {"access_token": "new", "refresh_token": "new_ref", "expires_in": 3600}
        store_spotify_token(db_path, "user_b", token1, "pl1")
        store_spotify_token(db_path, "user_b", token2, "pl2")
        row = get_auth_token(db_path, "spotify", "user_b")
        assert row["access_token"] == "new"
        assert row["playlist_id"] == "pl2"


class TestSpotifyClient:
    def test_no_token_returns_none(self, db_path):
        result = get_spotify_client("cid", "http://localhost/cb", db_path)
        assert result is None

    @patch("auth.spotify_auth.spotipy.Spotify")
    def test_valid_token_returns_client(self, mock_spotify_cls, db_path):
        future = (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat()
        from db.queries import upsert_auth_token
        upsert_auth_token(db_path, "spotify", "user_b", "valid_token", "ref", "pl1", future)

        client = get_spotify_client("cid", "http://localhost/cb", db_path)
        mock_spotify_cls.assert_called_once_with(auth="valid_token")
        assert client is not None

    @patch("auth.spotify_auth.build_auth_manager")
    @patch("auth.spotify_auth.spotipy.Spotify")
    def test_expired_token_triggers_refresh(self, mock_spotify_cls, mock_build_am, db_path):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        from db.queries import upsert_auth_token
        upsert_auth_token(db_path, "spotify", "user_b", "expired", "ref_token", "pl1", past)

        mock_am = MagicMock()
        mock_am.refresh_access_token.return_value = {
            "access_token": "refreshed",
            "refresh_token": "new_ref",
            "expires_in": 3600,
        }
        mock_build_am.return_value = mock_am

        client = get_spotify_client("cid", "http://localhost/cb", db_path)
        mock_am.refresh_access_token.assert_called_once_with("ref_token")
        mock_spotify_cls.assert_called_once_with(auth="refreshed")

        # Verify token was updated in DB
        row = get_auth_token(db_path, "spotify", "user_b")
        assert row["access_token"] == "refreshed"

    @patch("auth.spotify_auth.build_auth_manager")
    def test_refresh_failure_returns_none(self, mock_build_am, db_path):
        past = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        from db.queries import upsert_auth_token
        upsert_auth_token(db_path, "spotify", "user_b", "expired", "ref", "pl1", past)

        mock_am = MagicMock()
        mock_am.refresh_access_token.side_effect = Exception("Network error")
        mock_build_am.return_value = mock_am

        result = get_spotify_client("cid", "http://localhost/cb", db_path)
        assert result is None


# ---------------------------------------------------------------------------
# YouTube Music: token storage
# ---------------------------------------------------------------------------

class TestYouTubeTokenStorage:
    def test_store_and_retrieve(self, db_path):
        oauth_json = '{"access_token": "yt_access", "refresh_token": "yt_ref"}'
        store_youtube_token(db_path, "user_a", oauth_json, "yt_playlist_1")
        row = get_auth_token(db_path, "youtube_music", "user_a")
        assert row is not None
        assert row["access_token"] == oauth_json  # stored as full JSON
        assert row["playlist_id"] == "yt_playlist_1"


class TestYouTubeClient:
    def test_no_token_returns_none(self, db_path):
        result = get_ytmusic_client(db_path, "user_a")
        assert result is None

    @patch("auth.youtube_auth._client_from_json_str")
    def test_db_token_used(self, mock_client_fn, db_path):
        oauth_json = '{"test": true}'
        store_youtube_token(db_path, "user_a", oauth_json, "pl1")
        mock_client_fn.return_value = MagicMock()

        client = get_ytmusic_client(db_path, "user_a")
        mock_client_fn.assert_called_once_with(oauth_json)
        assert client is not None

    @patch("auth.youtube_auth._client_from_json_str")
    def test_b64_fallback(self, mock_client_fn, db_path):
        oauth_json = '{"fallback": true}'
        b64 = base64.b64encode(oauth_json.encode()).decode()
        mock_client_fn.return_value = MagicMock()

        client = get_ytmusic_client(db_path, "user_a", yt_oauth_json_b64=b64)
        mock_client_fn.assert_called_once_with(oauth_json)

    def test_invalid_b64_returns_none(self, db_path):
        result = get_ytmusic_client(db_path, "user_a", yt_oauth_json_b64="not_valid_b64!!!")
        assert result is None
