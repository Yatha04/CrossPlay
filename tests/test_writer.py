"""Tests for the writer — track addition, sync_log updates, error handling."""

from unittest.mock import MagicMock, patch
import pytest

from db.migrations import run_migrations
from db.queries import get_failed_syncs, is_already_synced, is_echo, get_cached_song
from sync.matcher import MatchResult
from sync.poller import NewTrack
from sync.writer import write_to_spotify, write_to_youtube, _handle_api_error


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


def _new_track(platform="spotify", track_id="sp1", title="Song A", artist="Artist A", isrc="US1234"):
    return NewTrack(platform=platform, track_id=track_id, title=title, artist=artist, isrc=isrc, duration_ms=240_000)


def _match(matched=True, target_id="yt1", method="isrc", score=1.0):
    return MatchResult(matched=matched, target_track_id=target_id, method=method, score=score)


# ---------------------------------------------------------------------------
# Spotify writer
# ---------------------------------------------------------------------------

class TestWriteToSpotify:
    def test_success(self, db_path):
        sp = MagicMock()
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = _match(target_id="sp_target")

        result = write_to_spotify(sp, "pl1", source, match, db_path)
        assert result is True
        sp.playlist_add_items.assert_called_once_with("pl1", ["spotify:track:sp_target"])

    def test_sync_log_recorded(self, db_path):
        sp = MagicMock()
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = _match(target_id="sp_target")

        write_to_spotify(sp, "pl1", source, match, db_path)
        assert is_already_synced(db_path, "youtube_music", "yt1")
        assert is_echo(db_path, "spotify", "sp_target")

    def test_song_cached(self, db_path):
        sp = MagicMock()
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = _match(target_id="sp_target")

        write_to_spotify(sp, "pl1", source, match, db_path)
        cached = get_cached_song(db_path, "youtube_music", "yt1")
        assert cached is not None
        assert cached["title"] == "Song A"

    def test_failed_match_logs_failure(self, db_path):
        sp = MagicMock()
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = MatchResult(matched=False, reason="no match")

        result = write_to_spotify(sp, "pl1", source, match, db_path)
        assert result is False
        sp.playlist_add_items.assert_not_called()
        failed = get_failed_syncs(db_path)
        assert len(failed) == 1

    def test_null_target_id_logs_failure(self, db_path):
        sp = MagicMock()
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = MatchResult(matched=True, target_track_id=None)

        result = write_to_spotify(sp, "pl1", source, match, db_path)
        assert result is False


# ---------------------------------------------------------------------------
# YouTube writer
# ---------------------------------------------------------------------------

class TestWriteToYouTube:
    def test_success(self, db_path):
        yt = MagicMock()
        source = _new_track(platform="spotify", track_id="sp1")
        match = _match(target_id="yt_target")

        result = write_to_youtube(yt, "pl1", source, match, db_path)
        assert result is True
        yt.add_playlist_items.assert_called_once_with("pl1", ["yt_target"])

    def test_sync_log_recorded(self, db_path):
        yt = MagicMock()
        source = _new_track(platform="spotify", track_id="sp1")
        match = _match(target_id="yt_target")

        write_to_youtube(yt, "pl1", source, match, db_path)
        assert is_already_synced(db_path, "spotify", "sp1")
        assert is_echo(db_path, "youtube_music", "yt_target")

    def test_failed_match(self, db_path):
        yt = MagicMock()
        source = _new_track(platform="spotify", track_id="sp1")
        match = MatchResult(matched=False, reason="no candidates")

        result = write_to_youtube(yt, "pl1", source, match, db_path)
        assert result is False
        yt.add_playlist_items.assert_not_called()


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_401_no_retry(self):
        source = _new_track()
        result = _handle_api_error(Exception("401 Unauthorized"), 0, source)
        assert result is None

    def test_404_no_retry(self):
        source = _new_track()
        result = _handle_api_error(Exception("404 Not Found"), 0, source)
        assert result is None

    def test_429_returns_wait(self):
        source = _new_track()
        result = _handle_api_error(Exception("429 rate limited"), 0, source)
        assert result is not None
        assert result > 0

    def test_transient_error_retries(self):
        source = _new_track()
        result = _handle_api_error(Exception("Connection timeout"), 0, source)
        assert result == 1  # first backoff

    def test_transient_error_gives_up(self):
        source = _new_track()
        result = _handle_api_error(Exception("Connection timeout"), 2, source)
        assert result is None  # last attempt

    @patch("sync.writer.time.sleep")
    def test_api_failure_retries_then_logs(self, mock_sleep, db_path):
        sp = MagicMock()
        sp.playlist_add_items.side_effect = Exception("Connection timeout")
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = _match(target_id="sp1")

        result = write_to_spotify(sp, "pl1", source, match, db_path)
        assert result is False
        assert sp.playlist_add_items.call_count == 3  # MAX_RETRIES
        failed = get_failed_syncs(db_path)
        assert len(failed) == 1
        assert "max retries" in failed[0]["error_message"]

    @patch("sync.writer.time.sleep")
    def test_auth_error_no_retry(self, mock_sleep, db_path):
        sp = MagicMock()
        sp.playlist_add_items.side_effect = Exception("401 Unauthorized")
        source = _new_track(platform="youtube_music", track_id="yt1")
        match = _match(target_id="sp1")

        result = write_to_spotify(sp, "pl1", source, match, db_path)
        assert result is False
        assert sp.playlist_add_items.call_count == 1  # no retries
