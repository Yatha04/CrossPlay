"""Tests for migrate/migrator.py — full migration pipeline."""

from unittest.mock import MagicMock, patch, call
import pytest

from db.migrations import run_migrations
from db.queries import get_migration_job, get_migration_tracks
from migrate.migrator import (
    run_migration,
    MigrationResult,
    TrackStatus,
    _create_target_playlist,
    _write_track_to_target,
)
from migrate.fetcher import PlaylistData, TrackData


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


def _sample_playlist(platform="spotify", tracks=None):
    """Build a sample PlaylistData for testing."""
    if tracks is None:
        tracks = [
            TrackData("t1", "Song A", "Artist A", isrc="US1111", duration_ms=200000),
            TrackData("t2", "Song B", "Artist B", isrc="US2222", duration_ms=180000),
        ]
    return PlaylistData(
        name="Test Playlist",
        description="A test playlist",
        platform=platform,
        playlist_id="test_pl_id",
        tracks=tracks,
        track_count=len(tracks),
    )


# ---------------------------------------------------------------------------
# run_migration (synchronous)
# ---------------------------------------------------------------------------

class TestRunMigration:
    @patch("migrate.migrator.fetch_spotify_playlist")
    def test_spotify_to_youtube(self, mock_fetch, db_path):
        """Full pipeline: Spotify → YouTube Music."""
        mock_fetch.return_value = _sample_playlist("spotify")

        sp = MagicMock()
        yt = MagicMock()

        # YouTube search returns matches
        yt.search.return_value = [
            {"videoId": "yt1", "title": "Song A", "artists": [{"name": "Artist A"}], "duration_seconds": 200},
        ]
        yt.create_playlist.return_value = "new_yt_pl"

        result = run_migration(
            source_url="spotify:test_pl_id",
            target_platform="youtube_music",
            sp=sp, yt=yt,
            db_path=db_path,
            client_id="cid", client_secret="csec",
        )

        assert isinstance(result, MigrationResult)
        assert result.status == "completed"
        assert result.target_playlist_id == "new_yt_pl"
        assert result.total_tracks == 2

        # Verify DB records
        job = get_migration_job(db_path, result.job_id)
        assert job["status"] == "completed"
        assert job["target_playlist_id"] == "new_yt_pl"

        tracks = get_migration_tracks(db_path, result.job_id)
        assert len(tracks) == 2

    @patch("migrate.migrator.fetch_youtube_playlist")
    def test_youtube_to_spotify(self, mock_fetch, db_path):
        """Full pipeline: YouTube Music → Spotify."""
        mock_fetch.return_value = _sample_playlist("youtube_music")

        sp = MagicMock()
        yt = MagicMock()

        # Spotify search returns matches
        sp.search.return_value = {
            "tracks": {"items": [
                {"id": "sp1", "name": "Song A", "artists": [{"name": "Artist A"}],
                 "external_ids": {"isrc": "US1111"}, "duration_ms": 200000},
            ]}
        }
        sp.current_user.return_value = {"id": "user123"}
        sp.user_playlist_create.return_value = {"id": "new_sp_pl"}

        result = run_migration(
            source_url="youtube:test_pl_id",
            target_platform="spotify",
            sp=sp, yt=yt,
            db_path=db_path,
            client_id="cid", client_secret="csec",
        )

        assert result.status == "completed"
        assert result.target_playlist_id == "new_sp_pl"
        sp.user_playlist_create.assert_called_once()

    def test_same_platform_raises(self, db_path):
        with pytest.raises(ValueError, match="same platform"):
            run_migration(
                "spotify:pl1", "spotify",
                MagicMock(), MagicMock(), db_path, "cid", "csec",
            )

    @patch("migrate.migrator.fetch_spotify_playlist")
    def test_empty_playlist_raises(self, mock_fetch, db_path):
        mock_fetch.return_value = _sample_playlist("spotify", tracks=[])

        with pytest.raises(ValueError, match="no tracks"):
            run_migration(
                "spotify:pl1", "youtube_music",
                MagicMock(), MagicMock(), db_path, "cid", "csec",
            )

    @patch("migrate.migrator.fetch_spotify_playlist")
    def test_partial_match(self, mock_fetch, db_path):
        """Some tracks match, some don't."""
        mock_fetch.return_value = _sample_playlist("spotify", tracks=[
            TrackData("t1", "Song A", "Artist A", isrc="US1111", duration_ms=200000),
            TrackData("t2", "Obscure Song", "Nobody", duration_ms=180000),
        ])

        sp = MagicMock()
        yt = MagicMock()
        yt.create_playlist.return_value = "new_yt_pl"

        # First search returns a match, second returns nothing
        yt.search.side_effect = [
            [{"videoId": "yt1", "title": "Song A", "artists": [{"name": "Artist A"}], "duration_seconds": 200}],
            [],  # no results for obscure song
        ]

        result = run_migration(
            "spotify:pl1", "youtube_music",
            sp, yt, db_path, "cid", "csec",
        )

        assert result.matched >= 1
        assert result.failed >= 1
        assert result.status == "completed"


# ---------------------------------------------------------------------------
# _create_target_playlist
# ---------------------------------------------------------------------------

class TestCreateTargetPlaylist:
    def test_create_spotify_playlist(self):
        sp = MagicMock()
        sp.current_user.return_value = {"id": "user123"}
        sp.user_playlist_create.return_value = {"id": "new_pl_id"}

        result = _create_target_playlist("spotify", sp, None, "My Playlist", "Desc")
        assert result == "new_pl_id"
        sp.user_playlist_create.assert_called_once_with(
            "user123",
            name="My Playlist",
            public=False,
            description="Migrated by CrossPlay. Desc",
        )

    def test_create_youtube_playlist(self):
        yt = MagicMock()
        yt.create_playlist.return_value = "yt_pl_id"

        result = _create_target_playlist("youtube_music", None, yt, "My Playlist", "Desc")
        assert result == "yt_pl_id"
        yt.create_playlist.assert_called_once_with(
            title="My Playlist",
            description="Migrated by CrossPlay. Desc",
            privacy_status="PRIVATE",
        )

    def test_unsupported_platform(self):
        with pytest.raises(ValueError, match="Unsupported"):
            _create_target_playlist("tidal", None, None, "PL", "")


# ---------------------------------------------------------------------------
# _write_track_to_target
# ---------------------------------------------------------------------------

class TestWriteTrackToTarget:
    def test_write_to_spotify(self):
        sp = MagicMock()
        _write_track_to_target("spotify", "pl1", "track1", sp, None)
        sp.playlist_add_items.assert_called_once_with("pl1", ["spotify:track:track1"])

    def test_write_to_youtube(self):
        yt = MagicMock()
        _write_track_to_target("youtube_music", "pl1", "vid1", None, yt)
        yt.add_playlist_items.assert_called_once_with("pl1", ["vid1"])

    def test_write_unsupported(self):
        with pytest.raises(ValueError):
            _write_track_to_target("tidal", "pl1", "t1", None, None)
