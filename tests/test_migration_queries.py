"""Tests for migration DB queries."""

import pytest

from db.migrations import run_migrations
from db.queries import (
    create_migration_job,
    update_migration_job,
    get_migration_job,
    get_migration_jobs,
    insert_migration_track,
    get_migration_tracks,
)


@pytest.fixture()
def db_path(tmp_path):
    path = str(tmp_path / "test.db")
    run_migrations(path)
    return path


# ---------------------------------------------------------------------------
# migration_jobs
# ---------------------------------------------------------------------------

class TestMigrationJobs:
    def test_create_job(self, db_path):
        job_id = create_migration_job(
            db_path,
            source_platform="spotify",
            source_playlist_id="sp_pl_123",
            source_playlist_name="My Playlist",
            target_platform="youtube_music",
            total_tracks=10,
        )
        assert job_id is not None
        assert job_id > 0

    def test_get_job(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "Playlist", "youtube_music", 5
        )
        job = get_migration_job(db_path, job_id)
        assert job is not None
        assert job["source_platform"] == "spotify"
        assert job["source_playlist_name"] == "Playlist"
        assert job["target_platform"] == "youtube_music"
        assert job["total_tracks"] == 5
        assert job["status"] == "pending"

    def test_get_nonexistent_job(self, db_path):
        assert get_migration_job(db_path, 9999) is None

    def test_update_status(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 10
        )
        update_migration_job(db_path, job_id, status="running")
        job = get_migration_job(db_path, job_id)
        assert job["status"] == "running"

    def test_update_completed_sets_timestamp(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 10
        )
        update_migration_job(db_path, job_id, status="completed")
        job = get_migration_job(db_path, job_id)
        assert job["status"] == "completed"
        assert job["completed_at"] is not None

    def test_update_progress(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 10
        )
        update_migration_job(
            db_path, job_id,
            matched_tracks=7, failed_tracks=3,
            target_playlist_id="yt_new_pl",
        )
        job = get_migration_job(db_path, job_id)
        assert job["matched_tracks"] == 7
        assert job["failed_tracks"] == 3
        assert job["target_playlist_id"] == "yt_new_pl"

    def test_update_with_no_fields(self, db_path):
        """Calling update with all None should not error."""
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 10
        )
        update_migration_job(db_path, job_id)  # no fields
        job = get_migration_job(db_path, job_id)
        assert job["status"] == "pending"

    def test_get_jobs_ordering(self, db_path):
        create_migration_job(db_path, "spotify", "sp1", "First", "youtube_music", 5)
        create_migration_job(db_path, "youtube_music", "yt1", "Second", "spotify", 10)

        jobs = get_migration_jobs(db_path)
        assert len(jobs) == 2
        # Newest first
        assert jobs[0]["source_playlist_name"] == "Second"
        assert jobs[1]["source_playlist_name"] == "First"

    def test_get_jobs_limit(self, db_path):
        for i in range(5):
            create_migration_job(db_path, "spotify", f"sp{i}", f"PL{i}", "youtube_music", 1)

        jobs = get_migration_jobs(db_path, limit=3)
        assert len(jobs) == 3


# ---------------------------------------------------------------------------
# migration_tracks
# ---------------------------------------------------------------------------

class TestMigrationTracks:
    def test_insert_and_get(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 2
        )

        insert_migration_track(
            db_path, job_id, "sp1", "Song A", "Artist A",
            target_track_id="yt1", match_method="isrc",
            match_score=1.0, status="matched",
        )
        insert_migration_track(
            db_path, job_id, "sp2", "Song B", "Artist B",
            status="failed", error_message="no match",
        )

        tracks = get_migration_tracks(db_path, job_id)
        assert len(tracks) == 2
        assert tracks[0]["source_title"] == "Song A"
        assert tracks[0]["status"] == "matched"
        assert tracks[1]["status"] == "failed"
        assert tracks[1]["error_message"] == "no match"

    def test_empty_tracks(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 0
        )
        tracks = get_migration_tracks(db_path, job_id)
        assert tracks == []

    def test_tracks_ordered_by_id(self, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 3
        )
        insert_migration_track(db_path, job_id, "sp1", "A", "A1", status="matched")
        insert_migration_track(db_path, job_id, "sp2", "B", "A2", status="failed")
        insert_migration_track(db_path, job_id, "sp3", "C", "A3", status="matched")

        tracks = get_migration_tracks(db_path, job_id)
        assert [t["source_title"] for t in tracks] == ["A", "B", "C"]
