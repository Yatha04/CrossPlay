"""CrossPlay entry point — initializes DB, starts FastAPI + APScheduler."""

import signal
import sys
import threading

import uvicorn
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI

from api.routes import router
from auth.spotify_auth import get_spotify_client
from auth.youtube_auth import get_ytmusic_client
from config import load_config
from db.migrations import run_migrations
from sync.engine import run_sync_cycle
from utils.logging import setup_logging, get_logger

log = get_logger("main")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    app = FastAPI(title="CrossPlay", version="1.0.0")
    app.include_router(router)
    return app


def _run_sync(cfg) -> None:
    """Execute a single sync cycle — called by the scheduler."""
    try:
        sp = get_spotify_client(
            cfg.spotify_client_id,
            cfg.spotify_redirect_uri,
            cfg.database_path,
        )
        yt = get_ytmusic_client(
            cfg.database_path,
            yt_oauth_json_b64=cfg.yt_oauth_json,
        )

        if not sp:
            log.warning("Skipping sync: Spotify client not available")
            return
        if not yt:
            log.warning("Skipping sync: YouTube Music client not available")
            return

        run_sync_cycle(
            sp, yt,
            cfg.spotify_playlist_id,
            cfg.youtube_playlist_id,
            cfg.database_path,
            cfg.fuzzy_match_threshold,
        )
    except Exception as e:
        log.error("Sync cycle failed: %s", e)


def start_scheduler(cfg) -> BackgroundScheduler:
    """Start APScheduler with the sync job on an interval."""
    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _run_sync,
        "interval",
        args=[cfg],
        seconds=cfg.poll_interval_seconds,
        id="sync_cycle",
        name="Playlist sync cycle",
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info("Scheduler started: syncing every %ds", cfg.poll_interval_seconds)
    return scheduler


def main() -> None:
    """Initialize everything and start the service."""
    setup_logging()

    try:
        cfg = load_config()
    except Exception as e:
        log.error("Failed to load config: %s", e)
        sys.exit(1)

    # Initialize database
    run_migrations(cfg.database_path)

    # Create FastAPI app and attach config
    app = create_app()
    app.state.config = cfg

    # Start scheduler
    scheduler = start_scheduler(cfg)

    # Graceful shutdown
    def _shutdown(signum, frame):
        log.info("Shutting down...")
        scheduler.shutdown(wait=False)
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Run the first sync immediately in a background thread
    threading.Thread(target=_run_sync, args=(cfg,), daemon=True).start()

    # Start uvicorn
    log.info("Starting CrossPlay on port 8888")
    uvicorn.run(app, host="0.0.0.0", port=8888, log_level="warning")


if __name__ == "__main__":
    main()
