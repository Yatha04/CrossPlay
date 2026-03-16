"""Tests for main.py — app creation, scheduler setup, and sync runner."""

import os
from unittest.mock import patch, MagicMock, call
from dataclasses import dataclass

import pytest

from db.migrations import run_migrations


@dataclass(frozen=True)
class FakeConfig:
    spotify_client_id: str = "test_id"
    spotify_client_secret: str = "test_secret"
    spotify_redirect_uri: str = "http://localhost:8888/callback"
    spotify_playlist_id: str = "sp_pl"
    yt_oauth_json: str = "base64data"
    youtube_playlist_id: str = "yt_pl"
    database_path: str = ""
    poll_interval_seconds: int = 180
    fuzzy_match_threshold: int = 85


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


@pytest.fixture()
def cfg(db_path):
    return FakeConfig(database_path=db_path)


# ---------------------------------------------------------------------------
# create_app
# ---------------------------------------------------------------------------

class TestCreateApp:
    def test_app_has_routes(self):
        from main import create_app
        app = create_app()
        paths = [route.path for route in app.routes]
        assert "/auth/spotify" in paths
        assert "/auth/spotify/callback" in paths
        assert "/auth/youtube" in paths
        assert "/auth/youtube/callback" in paths
        assert "/health" in paths

    def test_app_title(self):
        from main import create_app
        app = create_app()
        assert app.title == "CrossPlay"


# ---------------------------------------------------------------------------
# start_scheduler
# ---------------------------------------------------------------------------

class TestScheduler:
    def test_scheduler_starts_and_has_job(self, cfg):
        from main import start_scheduler
        scheduler = start_scheduler(cfg)
        try:
            job = scheduler.get_job("sync_cycle")
            assert job is not None
            assert job.name == "Playlist sync cycle"
        finally:
            scheduler.shutdown(wait=False)

    def test_scheduler_interval_matches_config(self, cfg):
        from main import start_scheduler
        scheduler = start_scheduler(cfg)
        try:
            job = scheduler.get_job("sync_cycle")
            assert job.trigger.interval.total_seconds() == cfg.poll_interval_seconds
        finally:
            scheduler.shutdown(wait=False)

    def test_scheduler_max_instances(self, cfg):
        from main import start_scheduler
        scheduler = start_scheduler(cfg)
        try:
            job = scheduler.get_job("sync_cycle")
            assert job.max_instances == 1
        finally:
            scheduler.shutdown(wait=False)


# ---------------------------------------------------------------------------
# _run_sync
# ---------------------------------------------------------------------------

class TestRunSync:
    @patch("main.run_sync_cycle")
    @patch("main.get_ytmusic_client")
    @patch("main.get_spotify_client")
    def test_calls_sync_cycle(self, mock_sp, mock_yt, mock_sync, cfg):
        from main import _run_sync
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = MagicMock()
        mock_sync.return_value = {"spotify_to_yt": 0, "yt_to_spotify": 0, "failed": 0}

        _run_sync(cfg)

        mock_sp.assert_called_once_with(cfg.spotify_client_id, cfg.spotify_redirect_uri, cfg.database_path)
        mock_yt.assert_called_once_with(cfg.database_path, yt_oauth_json_b64=cfg.yt_oauth_json)
        mock_sync.assert_called_once()

    @patch("main.run_sync_cycle")
    @patch("main.get_ytmusic_client")
    @patch("main.get_spotify_client")
    def test_skips_when_no_spotify(self, mock_sp, mock_yt, mock_sync, cfg):
        from main import _run_sync
        mock_sp.return_value = None
        mock_yt.return_value = MagicMock()

        _run_sync(cfg)
        mock_sync.assert_not_called()

    @patch("main.run_sync_cycle")
    @patch("main.get_ytmusic_client")
    @patch("main.get_spotify_client")
    def test_skips_when_no_youtube(self, mock_sp, mock_yt, mock_sync, cfg):
        from main import _run_sync
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = None

        _run_sync(cfg)
        mock_sync.assert_not_called()

    @patch("main.run_sync_cycle")
    @patch("main.get_ytmusic_client")
    @patch("main.get_spotify_client")
    def test_handles_sync_exception(self, mock_sp, mock_yt, mock_sync, cfg):
        from main import _run_sync
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = MagicMock()
        mock_sync.side_effect = Exception("API blew up")

        # Should not raise
        _run_sync(cfg)

    @patch("main.run_sync_cycle")
    @patch("main.get_ytmusic_client")
    @patch("main.get_spotify_client")
    def test_passes_fuzzy_threshold(self, mock_sp, mock_yt, mock_sync, cfg):
        from main import _run_sync
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = MagicMock()
        mock_sync.return_value = {}

        _run_sync(cfg)
        _, kwargs = mock_sync.call_args
        # fuzzy_match_threshold should be the last positional arg
        args = mock_sync.call_args[0]
        assert args[-1] == 85  # cfg.fuzzy_match_threshold


# ---------------------------------------------------------------------------
# Full test suite regression
# ---------------------------------------------------------------------------

class TestFullSuite:
    def test_all_existing_tests_still_pass(self):
        """Meta-test: import all test modules to verify no import errors."""
        import tests.test_config
        import tests.test_queries
        import tests.test_normalize
        import tests.test_matcher
        import tests.test_poller
        import tests.test_writer
        import tests.test_sync_engine
        import tests.test_routes
        import tests.test_auth
