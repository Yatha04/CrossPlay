"""Tests for the poller — Spotify snapshot detection, YouTube diff, dedup integration."""

from unittest.mock import MagicMock, patch
import pytest

from db.migrations import run_migrations
from db.queries import upsert_playlist_state, insert_sync_log
from sync.poller import poll_spotify, poll_youtube, _parse_duration, NewTrack


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


# ---------------------------------------------------------------------------
# Spotify polling
# ---------------------------------------------------------------------------

class TestPollSpotify:
    def _mock_sp(self, snapshot_id, items):
        sp = MagicMock()
        sp.playlist.return_value = {"snapshot_id": snapshot_id}
        sp.playlist_items.return_value = {
            "items": [
                {"track": {
                    "id": t["id"],
                    "name": t["name"],
                    "artists": [{"name": a} for a in t.get("artists", ["Unknown"])],
                    "external_ids": {"isrc": t.get("isrc")},
                    "duration_ms": t.get("duration_ms"),
                }}
                for t in items
            ],
            "next": None,
        }
        return sp

    def test_first_poll_detects_all(self, db_path):
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
            {"id": "sp2", "name": "Song B", "artists": ["Artist B"]},
        ])
        result = poll_spotify(sp, "pl1", db_path)
        assert len(result) == 2
        assert result[0].track_id == "sp1"
        assert result[0].platform == "spotify"

    def test_unchanged_snapshot_returns_empty(self, db_path):
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
        ])
        poll_spotify(sp, "pl1", db_path)  # first poll

        # Second poll with same snapshot
        result = poll_spotify(sp, "pl1", db_path)
        assert len(result) == 0

    def test_new_track_detected(self, db_path):
        sp1 = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
        ])
        poll_spotify(sp1, "pl1", db_path)

        sp2 = self._mock_sp("snap2", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
            {"id": "sp2", "name": "Song B", "artists": ["Artist B"]},
        ])
        result = poll_spotify(sp2, "pl1", db_path)
        assert len(result) == 1
        assert result[0].track_id == "sp2"

    def test_echo_prevention(self, db_path):
        """Tracks we synced TO spotify should not be picked up as new."""
        insert_sync_log(
            db_path, "youtube_music", "yt1", "spotify", target_track_id="sp1"
        )
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Echoed Song", "artists": ["Artist"]},
        ])
        result = poll_spotify(sp, "pl1", db_path)
        assert len(result) == 0

    def test_already_synced_not_redetected(self, db_path):
        insert_sync_log(db_path, "spotify", "sp1", "youtube_music")
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist"]},
        ])
        result = poll_spotify(sp, "pl1", db_path)
        assert len(result) == 0

    def test_isrc_extracted(self, db_path):
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song", "artists": ["Art"], "isrc": "US1234"},
        ])
        result = poll_spotify(sp, "pl1", db_path)
        assert result[0].isrc == "US1234"

    def test_duration_extracted(self, db_path):
        sp = self._mock_sp("snap1", [
            {"id": "sp1", "name": "Song", "artists": ["Art"], "duration_ms": 240000},
        ])
        result = poll_spotify(sp, "pl1", db_path)
        assert result[0].duration_ms == 240000

    def test_skip_null_track(self, db_path):
        """Local files or unavailable tracks should be skipped."""
        sp = MagicMock()
        sp.playlist.return_value = {"snapshot_id": "snap1"}
        sp.playlist_items.return_value = {
            "items": [
                {"track": None},
                {"track": {"id": None, "name": "No ID"}},
                {"track": {"id": "sp1", "name": "Valid", "artists": [{"name": "A"}], "external_ids": {}, "duration_ms": None}},
            ],
            "next": None,
        }
        result = poll_spotify(sp, "pl1", db_path)
        assert len(result) == 1
        assert result[0].track_id == "sp1"


# ---------------------------------------------------------------------------
# YouTube Music polling
# ---------------------------------------------------------------------------

class TestPollYouTube:
    def _mock_yt(self, tracks):
        yt = MagicMock()
        yt.get_playlist.return_value = {"tracks": tracks}
        return yt

    def _yt_track(self, vid, title="Song", artists=None, duration=None, duration_seconds=None):
        t = {"videoId": vid, "title": title}
        if artists:
            t["artists"] = [{"name": a} for a in artists]
        else:
            t["artists"] = [{"name": "Artist"}]
        if duration:
            t["duration"] = duration
        if duration_seconds:
            t["duration_seconds"] = duration_seconds
        return t

    def test_first_poll_detects_all(self, db_path):
        yt = self._mock_yt([
            self._yt_track("yt1", "Song A"),
            self._yt_track("yt2", "Song B"),
        ])
        result = poll_youtube(yt, "pl1", db_path)
        assert len(result) == 2

    def test_new_track_detected(self, db_path):
        yt1 = self._mock_yt([self._yt_track("yt1")])
        poll_youtube(yt1, "pl1", db_path)

        yt2 = self._mock_yt([self._yt_track("yt1"), self._yt_track("yt2", "New Song")])
        result = poll_youtube(yt2, "pl1", db_path)
        assert len(result) == 1
        assert result[0].track_id == "yt2"

    def test_no_changes(self, db_path):
        yt = self._mock_yt([self._yt_track("yt1")])
        poll_youtube(yt, "pl1", db_path)
        result = poll_youtube(yt, "pl1", db_path)
        assert len(result) == 0

    def test_echo_prevention(self, db_path):
        insert_sync_log(
            db_path, "spotify", "sp1", "youtube_music", target_track_id="yt1"
        )
        yt = self._mock_yt([self._yt_track("yt1")])
        result = poll_youtube(yt, "pl1", db_path)
        assert len(result) == 0

    def test_duration_from_seconds(self, db_path):
        yt = self._mock_yt([self._yt_track("yt1", duration_seconds=240)])
        result = poll_youtube(yt, "pl1", db_path)
        assert result[0].duration_ms == 240_000

    def test_duration_from_string(self, db_path):
        yt = self._mock_yt([self._yt_track("yt1", duration="4:30")])
        result = poll_youtube(yt, "pl1", db_path)
        assert result[0].duration_ms == 270_000

    def test_multiple_artists(self, db_path):
        yt = self._mock_yt([
            self._yt_track("yt1", artists=["Artist A", "Artist B"]),
        ])
        result = poll_youtube(yt, "pl1", db_path)
        assert result[0].artist == "Artist A, Artist B"

    def test_skip_null_videoid(self, db_path):
        yt = self._mock_yt([
            {"videoId": None, "title": "Bad"},
            self._yt_track("yt1"),
        ])
        result = poll_youtube(yt, "pl1", db_path)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Duration parsing
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_mm_ss(self):
        assert _parse_duration("3:45") == 225_000

    def test_h_mm_ss(self):
        assert _parse_duration("1:02:30") == 3_750_000

    def test_zero(self):
        assert _parse_duration("0:00") == 0

    def test_invalid(self):
        assert _parse_duration("invalid") is None

    def test_none(self):
        assert _parse_duration(None) is None
