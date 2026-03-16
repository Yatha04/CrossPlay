"""Spotify PKCE auth flow — token acquisition, refresh, and persistence."""

import time
from datetime import datetime, timezone

import spotipy
from spotipy.oauth2 import SpotifyPKCE

from db.queries import upsert_auth_token, get_auth_token
from utils.logging import get_logger

log = get_logger("spotify_auth")

SCOPES = "playlist-read-private playlist-modify-public playlist-modify-private"


def build_auth_manager(
    client_id: str,
    redirect_uri: str,
    cache_path: str | None = None,
) -> SpotifyPKCE:
    """Create a SpotifyPKCE auth manager."""
    return SpotifyPKCE(
        client_id=client_id,
        redirect_uri=redirect_uri,
        scope=SCOPES,
        cache_path=cache_path,
    )


def get_spotify_client(
    client_id: str,
    redirect_uri: str,
    db_path: str,
    user_label: str = "user_b",
) -> spotipy.Spotify | None:
    """Return an authenticated Spotify client, refreshing tokens if needed.

    Returns None if no stored token exists (needs initial OAuth).
    """
    token_row = get_auth_token(db_path, "spotify", user_label)
    if not token_row:
        log.warning("No stored Spotify token for %s — OAuth required", user_label)
        return None

    # Check expiry and refresh proactively
    access_token = token_row["access_token"]
    refresh_token = token_row["refresh_token"]
    expiry = token_row.get("token_expiry")

    if _is_expired(expiry):
        log.info("Spotify token expired for %s, refreshing", user_label)
        auth_manager = build_auth_manager(client_id, redirect_uri)
        try:
            new_token_info = auth_manager.refresh_access_token(refresh_token)
            access_token = new_token_info["access_token"]
            new_refresh = new_token_info.get("refresh_token", refresh_token)
            new_expiry = _expiry_from_token_info(new_token_info)

            upsert_auth_token(
                db_path, "spotify", user_label,
                access_token=access_token,
                refresh_token=new_refresh,
                playlist_id=token_row["playlist_id"],
                token_expiry=new_expiry,
            )
            log.info("Spotify token refreshed for %s", user_label)
        except Exception as e:
            log.error("Failed to refresh Spotify token: %s", e)
            return None

    return spotipy.Spotify(auth=access_token)


def store_spotify_token(
    db_path: str,
    user_label: str,
    token_info: dict,
    playlist_id: str,
) -> None:
    """Persist a Spotify token after initial OAuth callback."""
    upsert_auth_token(
        db_path, "spotify", user_label,
        access_token=token_info["access_token"],
        refresh_token=token_info["refresh_token"],
        playlist_id=playlist_id,
        token_expiry=_expiry_from_token_info(token_info),
    )
    log.info("Stored Spotify token for %s", user_label)


def _is_expired(expiry: str | None) -> bool:
    """Return True if the token expiry is in the past (or missing)."""
    if not expiry:
        return True
    try:
        exp_dt = datetime.fromisoformat(expiry)
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=timezone.utc)
        # Refresh 5 minutes early to avoid edge cases
        return datetime.now(timezone.utc) >= exp_dt
    except (ValueError, TypeError):
        return True


def _expiry_from_token_info(token_info: dict) -> str:
    """Extract ISO expiry string from spotipy token_info."""
    expires_at = token_info.get("expires_at")
    if expires_at:
        return datetime.fromtimestamp(expires_at, tz=timezone.utc).isoformat()
    expires_in = token_info.get("expires_in", 3600)
    return datetime.fromtimestamp(
        time.time() + expires_in, tz=timezone.utc
    ).isoformat()
