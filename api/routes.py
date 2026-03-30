"""FastAPI routes — OAuth callbacks for both platforms + health check + migration."""

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auth.spotify_auth import build_auth_manager, store_spotify_token, get_spotify_client
from auth.youtube_auth import store_youtube_token, get_ytmusic_client
from db.queries import (
    get_auth_token, get_failed_syncs, get_playlist_state,
    get_migration_job, get_migration_jobs, get_migration_tracks,
)
from migrate.fetcher import parse_playlist_url
from migrate.migrator import run_migration_async
from utils.logging import get_logger

log = get_logger("routes")

router = APIRouter()


# ---------------------------------------------------------------------------
# Spotify OAuth
# ---------------------------------------------------------------------------

@router.get("/auth/spotify")
def spotify_auth(request: Request):
    """Redirect the user to Spotify's OAuth consent page."""
    cfg = request.app.state.config
    auth_manager = build_auth_manager(cfg.spotify_client_id, cfg.spotify_redirect_uri)
    auth_url = auth_manager.get_authorize_url()
    log.info("Redirecting to Spotify OAuth")
    return RedirectResponse(auth_url)


@router.get("/auth/spotify/callback")
def spotify_callback(request: Request, code: str = Query(...)):
    """Exchange the Spotify auth code for tokens and persist them."""
    cfg = request.app.state.config
    auth_manager = build_auth_manager(cfg.spotify_client_id, cfg.spotify_redirect_uri)

    try:
        token_info = auth_manager.get_access_token(code)
    except Exception as e:
        log.error("Spotify token exchange failed: %s", e)
        return JSONResponse(
            {"status": "error", "detail": f"Token exchange failed: {e}"},
            status_code=400,
        )

    store_spotify_token(
        cfg.database_path,
        user_label="user_b",
        token_info=token_info,
        playlist_id=cfg.spotify_playlist_id,
    )

    return JSONResponse({
        "status": "ok",
        "message": "Spotify connected successfully. You can close this page.",
    })


# ---------------------------------------------------------------------------
# YouTube Music OAuth
# ---------------------------------------------------------------------------

@router.get("/auth/youtube")
def youtube_auth():
    """Instructions for YouTube Music OAuth setup.

    ytmusicapi requires a browser-based interactive flow; we can't redirect
    to a standard OAuth URL. Instead, the user runs `ytmusicapi oauth` locally
    and uploads the resulting oauth.json.
    """
    return JSONResponse({
        "status": "info",
        "message": (
            "YouTube Music uses ytmusicapi's OAuth flow. "
            "Run `python -m ytmusicapi oauth` locally, then POST the resulting "
            "oauth.json contents to /auth/youtube/callback as base64."
        ),
    })


@router.post("/auth/youtube/callback")
async def youtube_callback(request: Request):
    """Accept a base64-encoded oauth.json and store it."""
    cfg = request.app.state.config

    try:
        body = await request.json()
        oauth_b64 = body.get("oauth_json_b64", "")
        if not oauth_b64:
            return JSONResponse(
                {"status": "error", "detail": "Missing oauth_json_b64 in body"},
                status_code=400,
            )

        oauth_json_str = base64.b64decode(oauth_b64).decode("utf-8")
    except Exception as e:
        log.error("Failed to decode YouTube OAuth payload: %s", e)
        return JSONResponse(
            {"status": "error", "detail": f"Invalid payload: {e}"},
            status_code=400,
        )

    store_youtube_token(
        cfg.database_path,
        user_label="user_a",
        oauth_json_str=oauth_json_str,
        playlist_id=cfg.youtube_playlist_id,
    )

    return JSONResponse({
        "status": "ok",
        "message": "YouTube Music connected successfully.",
    })


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@router.get("/health")
def health(request: Request):
    """Return service health: token validity, sync stats, last poll times."""
    cfg = request.app.state.config

    spotify_token = get_auth_token(cfg.database_path, "spotify", "user_b")
    youtube_token = get_auth_token(cfg.database_path, "youtube_music", "user_a")

    spotify_state = get_playlist_state(
        cfg.database_path, "spotify", cfg.spotify_playlist_id
    )
    youtube_state = get_playlist_state(
        cfg.database_path, "youtube_music", cfg.youtube_playlist_id
    )

    failed = get_failed_syncs(cfg.database_path)
    recent_failures = len(failed)

    now = datetime.now(timezone.utc)

    def _last_poll_age(state):
        if not state or not state.get("last_polled_at"):
            return None
        try:
            polled = datetime.fromisoformat(state["last_polled_at"])
            if polled.tzinfo is None:
                polled = polled.replace(tzinfo=timezone.utc)
            return int((now - polled).total_seconds())
        except (ValueError, TypeError):
            return None

    def _token_status(token_row, platform):
        if not token_row:
            return "missing"
        if platform == "spotify":
            expiry = token_row.get("token_expiry")
            if expiry:
                try:
                    exp_dt = datetime.fromisoformat(expiry)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=timezone.utc)
                    if now >= exp_dt:
                        return "expired"
                except (ValueError, TypeError):
                    pass
            return "valid"
        # YouTube Music — if we have a token row, it's considered valid
        # (ytmusicapi handles refresh internally)
        return "valid"

    spotify_poll_age = _last_poll_age(spotify_state)
    youtube_poll_age = _last_poll_age(youtube_state)

    healthy = all([
        spotify_token is not None,
        youtube_token is not None,
        _token_status(spotify_token, "spotify") != "expired",
        recent_failures <= 5,
    ])

    return JSONResponse({
        "status": "healthy" if healthy else "degraded",
        "tokens": {
            "spotify": _token_status(spotify_token, "spotify"),
            "youtube_music": _token_status(youtube_token, "youtube_music"),
        },
        "last_poll_seconds_ago": {
            "spotify": spotify_poll_age,
            "youtube_music": youtube_poll_age,
        },
        "recent_failures": recent_failures,
    })


# ---------------------------------------------------------------------------
# Playlist migration
# ---------------------------------------------------------------------------

@router.post("/migrate")
async def start_migration(request: Request):
    """Start a playlist migration from a public playlist to the user's account.

    Body: {"source_url": "<playlist URL or ID>", "target_platform": "spotify" | "youtube_music"}
    """
    cfg = request.app.state.config

    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {"status": "error", "detail": "Invalid JSON body"},
            status_code=400,
        )

    source_url = body.get("source_url", "").strip()
    target_platform = body.get("target_platform", "").strip().lower()

    if not source_url:
        return JSONResponse(
            {"status": "error", "detail": "Missing source_url"},
            status_code=400,
        )
    if target_platform not in ("spotify", "youtube_music"):
        return JSONResponse(
            {"status": "error", "detail": "target_platform must be 'spotify' or 'youtube_music'"},
            status_code=400,
        )

    # Validate source URL format
    try:
        source_platform, _ = parse_playlist_url(source_url)
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "detail": str(e)},
            status_code=400,
        )

    if source_platform == target_platform:
        return JSONResponse(
            {"status": "error", "detail": "Source and target platforms must be different"},
            status_code=400,
        )

    # Verify target platform is authenticated
    if target_platform == "spotify":
        sp = get_spotify_client(
            cfg.spotify_client_id, cfg.spotify_redirect_uri, cfg.database_path,
        )
        if not sp:
            return JSONResponse(
                {"status": "error", "detail": "Spotify not authenticated. Visit /auth/spotify first."},
                status_code=401,
            )
        yt = None  # not needed as target
    else:
        yt = get_ytmusic_client(cfg.database_path, yt_oauth_json_b64=cfg.yt_oauth_json)
        if not yt:
            return JSONResponse(
                {"status": "error", "detail": "YouTube Music not authenticated. Set up OAuth first."},
                status_code=401,
            )
        sp = None  # not needed as target

    # For migration we may also need the other client for searching
    if sp is None:
        sp = get_spotify_client(
            cfg.spotify_client_id, cfg.spotify_redirect_uri, cfg.database_path,
        )
    if yt is None:
        yt = get_ytmusic_client(cfg.database_path, yt_oauth_json_b64=cfg.yt_oauth_json)

    try:
        job_id = run_migration_async(
            source_url=source_url,
            target_platform=target_platform,
            sp=sp,
            yt=yt,
            db_path=cfg.database_path,
            client_id=cfg.spotify_client_id,
            client_secret=cfg.spotify_client_secret,
            fuzzy_threshold=cfg.fuzzy_match_threshold,
        )
    except ValueError as e:
        return JSONResponse(
            {"status": "error", "detail": str(e)},
            status_code=400,
        )

    log.info("Migration %d started: %s → %s", job_id, source_url, target_platform)
    return JSONResponse(
        {"status": "started", "job_id": job_id, "message": "Migration started in background."},
        status_code=202,
    )


@router.get("/migrate/history")
def migration_history(request: Request, limit: int = Query(20, ge=1, le=100)):
    """List recent migration jobs."""
    cfg = request.app.state.config
    jobs = get_migration_jobs(cfg.database_path, limit=limit)

    return JSONResponse({
        "jobs": [
            {
                "id": j["id"],
                "source_platform": j["source_platform"],
                "source_playlist_name": j["source_playlist_name"],
                "target_platform": j["target_platform"],
                "status": j["status"],
                "total_tracks": j["total_tracks"],
                "matched_tracks": j["matched_tracks"],
                "failed_tracks": j["failed_tracks"],
                "created_at": j["created_at"],
                "completed_at": j["completed_at"],
            }
            for j in jobs
        ],
    })


@router.get("/migrate/{job_id}")
def migration_status(job_id: int, request: Request):
    """Return the current status and track-level results for a migration job."""
    cfg = request.app.state.config
    job = get_migration_job(cfg.database_path, job_id)

    if not job:
        return JSONResponse(
            {"status": "error", "detail": f"Migration job {job_id} not found"},
            status_code=404,
        )

    tracks = get_migration_tracks(cfg.database_path, job_id)
    progress_pct = 0
    if job["total_tracks"] > 0:
        processed = job["matched_tracks"] + job["failed_tracks"]
        progress_pct = round(processed / job["total_tracks"] * 100, 1)

    return JSONResponse({
        "job_id": job["id"],
        "status": job["status"],
        "source_platform": job["source_platform"],
        "source_playlist_name": job["source_playlist_name"],
        "target_platform": job["target_platform"],
        "target_playlist_id": job["target_playlist_id"],
        "total_tracks": job["total_tracks"],
        "matched_tracks": job["matched_tracks"],
        "failed_tracks": job["failed_tracks"],
        "progress_pct": progress_pct,
        "created_at": job["created_at"],
        "completed_at": job["completed_at"],
        "tracks": [
            {
                "source_title": t["source_title"],
                "source_artist": t["source_artist"],
                "target_track_id": t["target_track_id"],
                "match_method": t["match_method"],
                "match_score": t["match_score"],
                "status": t["status"],
                "error": t["error_message"],
            }
            for t in tracks
        ],
    })

