"""Tests for migration API routes (POST /migrate, GET /migrate/{id}, GET /migrate/history)."""

import json
from dataclasses import dataclass
from unittest.mock import patch, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes import router
from db.migrations import run_migrations
from db.queries import (
    create_migration_job,
    update_migration_job,
    insert_migration_track,
)


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
# POST /migrate
# ---------------------------------------------------------------------------

class TestStartMigration:
    def test_missing_source_url(self, client):
        resp = client.post("/migrate", json={"target_platform": "youtube_music"})
        assert resp.status_code == 400
        assert "source_url" in resp.json()["detail"].lower()

    def test_invalid_target_platform(self, client):
        resp = client.post("/migrate", json={
            "source_url": "spotify:pl123",
            "target_platform": "tidal",
        })
        assert resp.status_code == 400
        assert "target_platform" in resp.json()["detail"]

    def test_invalid_source_url(self, client):
        resp = client.post("/migrate", json={
            "source_url": "not-a-valid-thing",
            "target_platform": "youtube_music",
        })
        assert resp.status_code == 400
        assert "Could not determine" in resp.json()["detail"]

    def test_same_platform(self, client):
        resp = client.post("/migrate", json={
            "source_url": "spotify:pl123",
            "target_platform": "spotify",
        })
        assert resp.status_code == 400
        assert "different" in resp.json()["detail"].lower()

    @patch("api.routes.get_spotify_client")
    @patch("api.routes.get_ytmusic_client")
    def test_target_not_authenticated_spotify(self, mock_yt, mock_sp, client):
        """Spotify target not authenticated returns 401."""
        mock_sp.return_value = None  # not authenticated
        mock_yt.return_value = MagicMock()

        resp = client.post("/migrate", json={
            "source_url": "youtube:PLtest",
            "target_platform": "spotify",
        })
        assert resp.status_code == 401
        assert "Spotify not authenticated" in resp.json()["detail"]

    @patch("api.routes.get_spotify_client")
    @patch("api.routes.get_ytmusic_client")
    def test_target_not_authenticated_youtube(self, mock_yt, mock_sp, client):
        """YouTube target not authenticated returns 401."""
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = None  # not authenticated

        resp = client.post("/migrate", json={
            "source_url": "spotify:pl123",
            "target_platform": "youtube_music",
        })
        assert resp.status_code == 401
        assert "YouTube Music not authenticated" in resp.json()["detail"]

    @patch("api.routes.run_migration_async")
    @patch("api.routes.get_spotify_client")
    @patch("api.routes.get_ytmusic_client")
    def test_successful_start(self, mock_yt, mock_sp, mock_migrate, client):
        mock_sp.return_value = MagicMock()
        mock_yt.return_value = MagicMock()
        mock_migrate.return_value = 42

        resp = client.post("/migrate", json={
            "source_url": "spotify:pl123",
            "target_platform": "youtube_music",
        })
        assert resp.status_code == 202
        data = resp.json()
        assert data["status"] == "started"
        assert data["job_id"] == 42

    def test_invalid_json_body(self, client):
        resp = client.post(
            "/migrate",
            content="not json",
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /migrate/{job_id}
# ---------------------------------------------------------------------------

class TestMigrationStatus:
    def test_nonexistent_job(self, client):
        resp = client.get("/migrate/9999")
        assert resp.status_code == 404

    def test_job_with_tracks(self, client, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "My Playlist", "youtube_music", 3
        )
        update_migration_job(db_path, job_id, matched_tracks=2, failed_tracks=1, status="completed")
        insert_migration_track(
            db_path, job_id, "sp1", "Song A", "Artist A",
            target_track_id="yt1", match_method="isrc", match_score=1.0, status="matched",
        )
        insert_migration_track(
            db_path, job_id, "sp2", "Song B", "Artist B",
            status="failed", error_message="no match",
        )

        resp = client.get(f"/migrate/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["status"] == "completed"
        assert data["total_tracks"] == 3
        assert data["matched_tracks"] == 2
        assert data["progress_pct"] == 100.0
        assert len(data["tracks"]) == 2
        assert data["tracks"][0]["status"] == "matched"

    def test_progress_calculation(self, client, db_path):
        job_id = create_migration_job(
            db_path, "spotify", "sp_pl", "PL", "youtube_music", 10
        )
        update_migration_job(db_path, job_id, matched_tracks=3, failed_tracks=2)

        resp = client.get(f"/migrate/{job_id}")
        data = resp.json()
        assert data["progress_pct"] == 50.0


# ---------------------------------------------------------------------------
# GET /migrate/history
# ---------------------------------------------------------------------------

class TestMigrationHistory:
    def test_empty_history(self, client):
        resp = client.get("/migrate/history")
        assert resp.status_code == 200
        assert resp.json()["jobs"] == []

    def test_returns_jobs(self, client, db_path):
        create_migration_job(db_path, "spotify", "sp1", "First", "youtube_music", 5)
        create_migration_job(db_path, "youtube_music", "yt1", "Second", "spotify", 10)

        resp = client.get("/migrate/history")
        data = resp.json()
        assert len(data["jobs"]) == 2
        # Newest first
        assert data["jobs"][0]["source_playlist_name"] == "Second"

    def test_respects_limit(self, client, db_path):
        for i in range(5):
            create_migration_job(db_path, "spotify", f"sp{i}", f"PL{i}", "youtube_music", 1)

        resp = client.get("/migrate/history?limit=2")
        assert len(resp.json()["jobs"]) == 2
