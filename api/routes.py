"""FastAPI routes — OAuth callbacks for both platforms + health check."""

import base64
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from auth.spotify_auth import build_auth_manager, store_spotify_token
from auth.youtube_auth import store_youtube_token
from db.queries import get_auth_token, get_failed_syncs, get_playlist_state
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
