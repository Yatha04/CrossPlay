"""Tests for the database layer — migrations, CRUD, constraints, and indexes."""

import sqlite3
import pytest

from db.migrations import run_migrations
from db import queries


@pytest.fixture()
def db_path(tmp_path):
    """Create a fresh in-memory-like temp database for each test."""
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------

class TestMigrations:
    def test_tables_created(self, db_path):
        conn = sqlite3.connect(db_path)
        tables = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        conn.close()
        assert tables == {"auth_tokens", "sync_log", "song_cache", "playlist_state"}

    def test_indexes_created(self, db_path):
        conn = sqlite3.connect(db_path)
        indexes = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        conn.close()
        expected = {"idx_sync_source", "idx_sync_target", "idx_sync_isrc", "idx_cache_lookup"}
        assert expected.issubset(indexes)

    def test_idempotent(self, db_path):
        """Running migrations twice should not raise."""
        run_migrations(db_path)
        run_migrations(db_path)


# ---------------------------------------------------------------------------
# auth_tokens
# ---------------------------------------------------------------------------

class TestAuthTokens:
    def test_insert_and_get(self, db_path):
        queries.upsert_auth_token(
            db_path, "spotify", "user_a", "acc123", "ref456", "pl_1", "2026-12-31T00:00:00"
        )
        row = queries.get_auth_token(db_path, "spotify", "user_a")
        assert row is not None
        assert row["access_token"] == "acc123"
        assert row["refresh_token"] == "ref456"
        assert row["playlist_id"] == "pl_1"

    def test_upsert_updates_existing(self, db_path):
        queries.upsert_auth_token(db_path, "spotify", "user_a", "old", "old_ref", "pl_1")
        queries.upsert_auth_token(db_path, "spotify", "user_a", "new", "new_ref", "pl_2")
        row = queries.get_auth_token(db_path, "spotify", "user_a")
        assert row["access_token"] == "new"
        assert row["refresh_token"] == "new_ref"
        assert row["playlist_id"] == "pl_2"

    def test_get_nonexistent(self, db_path):
        assert queries.get_auth_token(db_path, "spotify", "nobody") is None

    def test_different_platforms_coexist(self, db_path):
        queries.upsert_auth_token(db_path, "spotify", "user_a", "sp", "sp_ref", "sp_pl")
        queries.upsert_auth_token(db_path, "youtube_music", "user_a", "yt", "yt_ref", "yt_pl")
        sp = queries.get_auth_token(db_path, "spotify", "user_a")
        yt = queries.get_auth_token(db_path, "youtube_music", "user_a")
        assert sp["access_token"] == "sp"
        assert yt["access_token"] == "yt"


# ---------------------------------------------------------------------------
# sync_log
# ---------------------------------------------------------------------------

class TestSyncLog:
    def test_insert_and_query(self, db_path):
        row_id = queries.insert_sync_log(
            db_path,
            source_platform="spotify",
            source_track_id="sp:track:123",
            target_platform="youtube_music",
            target_track_id="yt_vid_456",
            song_title="Blinding Lights",
            artist_name="The Weeknd",
            isrc="USUG11904221",
            match_method="isrc",
            match_score=1.0,
        )
        assert row_id > 0

    def test_is_already_synced(self, db_path):
        assert not queries.is_already_synced(db_path, "spotify", "sp:1")
        queries.insert_sync_log(db_path, "spotify", "sp:1", "youtube_music")
        assert queries.is_already_synced(db_path, "spotify", "sp:1")

    def test_is_echo(self, db_path):
        """If we wrote track X to spotify, then seeing X on spotify is an echo."""
        queries.insert_sync_log(
            db_path, "youtube_music", "yt:1", "spotify", target_track_id="sp:1"
        )
        assert queries.is_echo(db_path, "spotify", "sp:1")
        assert not queries.is_echo(db_path, "youtube_music", "yt:1")

    def test_should_sync_new_track(self, db_path):
        assert queries.should_sync(db_path, "spotify", "sp:new", "youtube_music")

    def test_should_sync_blocks_echo(self, db_path):
        queries.insert_sync_log(
            db_path, "youtube_music", "yt:1", "spotify", target_track_id="sp:1"
        )
        # sp:1 appeared on spotify, but WE put it there — don't sync back
        assert not queries.should_sync(db_path, "spotify", "sp:1", "youtube_music")

    def test_should_sync_blocks_duplicate(self, db_path):
        queries.insert_sync_log(db_path, "spotify", "sp:1", "youtube_music")
        assert not queries.should_sync(db_path, "spotify", "sp:1", "youtube_music")

    def test_get_by_isrc(self, db_path):
        queries.insert_sync_log(db_path, "spotify", "sp:1", "youtube_music", isrc="US1234")
        queries.insert_sync_log(db_path, "spotify", "sp:2", "youtube_music", isrc="US1234")
        results = queries.get_sync_log_by_isrc(db_path, "US1234")
        assert len(results) == 2

    def test_get_failed_syncs(self, db_path):
        queries.insert_sync_log(db_path, "spotify", "sp:1", "youtube_music", status="synced")
        queries.insert_sync_log(
            db_path, "spotify", "sp:2", "youtube_music",
            status="failed", error_message="not found"
        )
        failed = queries.get_failed_syncs(db_path)
        assert len(failed) == 1
        assert failed[0]["source_track_id"] == "sp:2"
        assert failed[0]["error_message"] == "not found"


# ---------------------------------------------------------------------------
# song_cache
# ---------------------------------------------------------------------------

class TestSongCache:
    def test_insert_and_get(self, db_path):
        queries.upsert_song_cache(
            db_path, "spotify", "sp:1",
            title="Wonderwall", artist="Oasis", album="Morning Glory",
            isrc="GBARL9500078", duration_ms=258000,
        )
        row = queries.get_cached_song(db_path, "spotify", "sp:1")
        assert row["title"] == "Wonderwall"
        assert row["duration_ms"] == 258000

    def test_upsert_updates(self, db_path):
        queries.upsert_song_cache(db_path, "spotify", "sp:1", title="Old Title")
        queries.upsert_song_cache(db_path, "spotify", "sp:1", title="New Title")
        row = queries.get_cached_song(db_path, "spotify", "sp:1")
        assert row["title"] == "New Title"

    def test_unique_constraint(self, db_path):
        """Same platform+track_id should upsert, not create duplicates."""
        queries.upsert_song_cache(db_path, "spotify", "sp:1", title="A")
        queries.upsert_song_cache(db_path, "spotify", "sp:1", title="B")
        conn = sqlite3.connect(db_path)
        count = conn.execute(
            "SELECT COUNT(*) FROM song_cache WHERE platform='spotify' AND track_id='sp:1'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_nonexistent(self, db_path):
        assert queries.get_cached_song(db_path, "spotify", "nope") is None


# ---------------------------------------------------------------------------
# playlist_state
# ---------------------------------------------------------------------------

class TestPlaylistState:
    def test_insert_and_get(self, db_path):
        queries.upsert_playlist_state(
            db_path, "spotify", "pl_1",
            last_snapshot="snap_abc", last_track_ids=["sp:1", "sp:2"],
        )
        state = queries.get_playlist_state(db_path, "spotify", "pl_1")
        assert state["last_snapshot"] == "snap_abc"
        assert state["last_track_ids"] == ["sp:1", "sp:2"]

    def test_upsert_updates(self, db_path):
        queries.upsert_playlist_state(db_path, "spotify", "pl_1", last_snapshot="old")
        queries.upsert_playlist_state(db_path, "spotify", "pl_1", last_snapshot="new")
        state = queries.get_playlist_state(db_path, "spotify", "pl_1")
        assert state["last_snapshot"] == "new"

    def test_track_ids_json_roundtrip(self, db_path):
        ids = ["a", "b", "c"]
        queries.upsert_playlist_state(db_path, "youtube_music", "yt_pl", last_track_ids=ids)
        state = queries.get_playlist_state(db_path, "youtube_music", "yt_pl")
        assert state["last_track_ids"] == ids

    def test_null_track_ids(self, db_path):
        queries.upsert_playlist_state(db_path, "spotify", "pl_1")
        state = queries.get_playlist_state(db_path, "spotify", "pl_1")
        assert state["last_track_ids"] is None

    def test_get_nonexistent(self, db_path):
        assert queries.get_playlist_state(db_path, "spotify", "nope") is None

    def test_last_polled_at_set(self, db_path):
        queries.upsert_playlist_state(db_path, "spotify", "pl_1")
        state = queries.get_playlist_state(db_path, "spotify", "pl_1")
        assert state["last_polled_at"] is not None
