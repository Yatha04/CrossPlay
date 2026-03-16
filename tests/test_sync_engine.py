"""End-to-end tests for the sync engine with mocked API clients."""

from unittest.mock import MagicMock, patch
import pytest

from db.migrations import run_migrations
from db.queries import is_already_synced, is_echo, insert_sync_log, get_failed_syncs
from sync.engine import run_sync_cycle, _search_youtube, _search_spotify
from sync.poller import NewTrack


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


def _mock_sp(snapshot, tracks, search_results=None):
    """Build a mock Spotify client."""
    sp = MagicMock()
    sp.playlist.return_value = {"snapshot_id": snapshot}
    sp.playlist_items.return_value = {
        "items": [
            {"track": {
                "id": t["id"], "name": t["name"],
                "artists": [{"name": a} for a in t.get("artists", ["Unknown"])],
                "external_ids": {"isrc": t.get("isrc")},
                "duration_ms": t.get("duration_ms", 240_000),
            }}
            for t in tracks
        ],
        "next": None,
    }
    if search_results is not None:
        sp.search.return_value = {
            "tracks": {
                "items": [
                    {
                        "id": r["id"], "name": r["name"],
                        "artists": [{"name": a} for a in r.get("artists", ["Unknown"])],
                        "external_ids": {"isrc": r.get("isrc")},
                        "duration_ms": r.get("duration_ms", 240_000),
                    }
                    for r in search_results
                ]
            }
        }
    else:
        sp.search.return_value = {"tracks": {"items": []}}
    return sp


def _mock_yt(tracks, search_results=None):
    """Build a mock YouTube Music client."""
    yt = MagicMock()
    yt.get_playlist.return_value = {
        "tracks": [
            {
                "videoId": t["id"], "title": t["name"],
                "artists": [{"name": a} for a in t.get("artists", ["Unknown"])],
                "duration_seconds": t.get("duration_ms", 240_000) // 1000,
            }
            for t in tracks
        ]
    }
    if search_results is not None:
        yt.search.return_value = [
            {
                "videoId": r["id"], "title": r["name"],
                "artists": [{"name": a} for a in r.get("artists", ["Unknown"])],
                "duration_seconds": r.get("duration_ms", 240_000) // 1000,
            }
            for r in search_results
        ]
    else:
        yt.search.return_value = []
    return yt


# ---------------------------------------------------------------------------
# Full sync cycle
# ---------------------------------------------------------------------------

class TestSyncCycle:
    def test_no_changes(self, db_path):
        sp = _mock_sp("snap1", [])
        yt = _mock_yt([])
        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["spotify_to_yt"] == 0
        assert summary["yt_to_spotify"] == 0

    def test_spotify_to_youtube(self, db_path):
        """New Spotify track should be synced to YouTube Music."""
        sp = _mock_sp("snap1", [
            {"id": "sp1", "name": "Blinding Lights", "artists": ["The Weeknd"], "isrc": "US123"},
        ])
        yt = _mock_yt([], search_results=[
            {"id": "yt1", "name": "Blinding Lights", "artists": ["The Weeknd"]},
        ])

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["spotify_to_yt"] == 1
        yt.add_playlist_items.assert_called_once_with("yt_pl", ["yt1"])
        assert is_already_synced(db_path, "spotify", "sp1")
        assert is_echo(db_path, "youtube_music", "yt1")

    def test_youtube_to_spotify(self, db_path):
        """New YouTube track should be synced to Spotify."""
        sp = _mock_sp("snap1", [], search_results=[
            {"id": "sp1", "name": "Wonderwall", "artists": ["Oasis"], "isrc": "GB456"},
        ])
        yt = _mock_yt([
            {"id": "yt1", "name": "Wonderwall", "artists": ["Oasis"]},
        ])

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["yt_to_spotify"] == 1
        sp.playlist_add_items.assert_called_once_with("sp_pl", ["spotify:track:sp1"])

    def test_bidirectional_single_cycle(self, db_path):
        """Both directions can sync in one cycle."""
        sp = _mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
        ], search_results=[
            {"id": "sp2", "name": "Song B", "artists": ["Artist B"]},
        ])
        yt = _mock_yt([
            {"id": "yt1", "name": "Song B", "artists": ["Artist B"]},
        ], search_results=[
            {"id": "yt2", "name": "Song A", "artists": ["Artist A"]},
        ])

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["spotify_to_yt"] == 1
        assert summary["yt_to_spotify"] == 1

    def test_no_infinite_loop(self, db_path):
        """Second cycle should not re-sync tracks from first cycle."""
        sp = _mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
        ])
        yt = _mock_yt([], search_results=[
            {"id": "yt1", "name": "Song A", "artists": ["Artist A"]},
        ])

        # First cycle
        run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)

        # Second cycle — yt1 now appears on YouTube but was synced BY us
        sp2 = _mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Artist A"]},
        ])
        yt2 = _mock_yt([
            {"id": "yt1", "name": "Song A", "artists": ["Artist A"]},
        ])
        summary = run_sync_cycle(sp2, yt2, "sp_pl", "yt_pl", db_path)
        # sp1 already synced, yt1 is an echo — nothing new
        assert summary["spotify_to_yt"] == 0
        assert summary["yt_to_spotify"] == 0

    def test_failed_match_counted(self, db_path):
        """Unmatched tracks should increment failed count."""
        sp = _mock_sp("snap1", [
            {"id": "sp1", "name": "Obscure Song", "artists": ["Nobody"]},
        ])
        yt = _mock_yt([])  # search returns no results

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["failed"] == 1
        assert summary["spotify_to_yt"] == 0

    def test_poll_error_handled(self, db_path):
        """Poll failure should not crash the cycle."""
        sp = MagicMock()
        sp.playlist.side_effect = Exception("Network error")
        yt = _mock_yt([])

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        # Should gracefully continue
        assert summary["spotify_to_yt"] == 0

    def test_multiple_new_tracks(self, db_path):
        sp = _mock_sp("snap1", [
            {"id": "sp1", "name": "Song A", "artists": ["Art A"]},
            {"id": "sp2", "name": "Song B", "artists": ["Art B"]},
            {"id": "sp3", "name": "Song C", "artists": ["Art C"]},
        ])
        # Each search returns a matching result
        yt = _mock_yt([])
        yt.search.return_value = [
            {"videoId": "yt_match", "title": "Match", "artists": [{"name": "Art"}], "duration_seconds": 240},
        ]

        summary = run_sync_cycle(sp, yt, "sp_pl", "yt_pl", db_path)
        assert summary["spotify_to_yt"] + summary["failed"] == 3


# ---------------------------------------------------------------------------
# Search helpers
# ---------------------------------------------------------------------------

class TestSearchYouTube:
    def test_parses_results(self):
        yt = MagicMock()
        yt.search.return_value = [
            {"videoId": "yt1", "title": "Song", "artists": [{"name": "Artist"}], "duration_seconds": 200},
            {"videoId": None, "title": "Bad"},  # should be skipped
        ]
        track = NewTrack(platform="spotify", track_id="sp1", title="Song", artist="Artist")
        results = _search_youtube(yt, track)
        assert len(results) == 1
        assert results[0].track_id == "yt1"
        assert results[0].duration_ms == 200_000

    def test_search_error_returns_empty(self):
        yt = MagicMock()
        yt.search.side_effect = Exception("API error")
        track = NewTrack(platform="spotify", track_id="sp1", title="Song", artist="Artist")
        results = _search_youtube(yt, track)
        assert results == []


class TestSearchSpotify:
    def test_parses_results(self):
        sp = MagicMock()
        sp.search.return_value = {
            "tracks": {"items": [
                {
                    "id": "sp1", "name": "Song",
                    "artists": [{"name": "Artist"}],
                    "external_ids": {"isrc": "US1234"},
                    "duration_ms": 240_000,
                },
            ]}
        }
        track = NewTrack(platform="youtube_music", track_id="yt1", title="Song", artist="Artist")
        results = _search_spotify(sp, track)
        assert len(results) == 1
        assert results[0].isrc == "US1234"

    def test_search_error_returns_empty(self):
        sp = MagicMock()
        sp.search.side_effect = Exception("API error")
        track = NewTrack(platform="youtube_music", track_id="yt1", title="Song", artist="Artist")
        results = _search_spotify(sp, track)
        assert results == []
