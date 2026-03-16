"""Adds matched tracks to target playlists and records in sync_log."""

import time

from db.queries import insert_sync_log, upsert_song_cache
from sync.matcher import MatchResult, TrackInfo
from sync.poller import NewTrack
from utils.logging import get_logger

log = get_logger("writer")

MAX_RETRIES = 3
RETRY_BACKOFF = [1, 4, 16]  # seconds


class WriteError(Exception):
    """Raised when a track write fails after retries."""


def write_to_spotify(
    sp,
    playlist_id: str,
    source_track: NewTrack,
    match: MatchResult,
    db_path: str,
) -> bool:
    """Add a matched track to the Spotify playlist.

    Returns True on success, False on failure.
    """
    if not match.matched or not match.target_track_id:
        _log_failed(db_path, source_track, match)
        return False

    spotify_uri = f"spotify:track:{match.target_track_id}"

    for attempt in range(MAX_RETRIES):
        try:
            sp.playlist_add_items(playlist_id, [spotify_uri])
            _log_success(db_path, source_track, match)
            log.info(
                "Added '%s' by '%s' to Spotify playlist",
                source_track.title, source_track.artist,
            )
            return True
        except Exception as e:
            retry_after = _handle_api_error(e, attempt, source_track)
            if retry_after is None:
                break
            time.sleep(retry_after)

    _log_failed(db_path, source_track, match, error_message="max retries exceeded")
    return False


def write_to_youtube(
    yt,
    playlist_id: str,
    source_track: NewTrack,
    match: MatchResult,
    db_path: str,
) -> bool:
    """Add a matched track to the YouTube Music playlist.

    Returns True on success, False on failure.
    """
    if not match.matched or not match.target_track_id:
        _log_failed(db_path, source_track, match)
        return False

    for attempt in range(MAX_RETRIES):
        try:
            yt.add_playlist_items(playlist_id, [match.target_track_id])
            _log_success(db_path, source_track, match)
            log.info(
                "Added '%s' by '%s' to YouTube Music playlist",
                source_track.title, source_track.artist,
            )
            return True
        except Exception as e:
            retry_after = _handle_api_error(e, attempt, source_track)
            if retry_after is None:
                break
            time.sleep(retry_after)

    _log_failed(db_path, source_track, match, error_message="max retries exceeded")
    return False


def _log_success(db_path: str, source: NewTrack, match: MatchResult) -> None:
    target_platform = "youtube_music" if source.platform == "spotify" else "spotify"
    insert_sync_log(
        db_path,
        source_platform=source.platform,
        source_track_id=source.track_id,
        target_platform=target_platform,
        target_track_id=match.target_track_id,
        song_title=source.title,
        artist_name=source.artist,
        isrc=source.isrc,
        match_method=match.method,
        match_score=match.score,
        status="synced",
    )
    # Cache the song metadata
    upsert_song_cache(
        db_path, source.platform, source.track_id,
        title=source.title, artist=source.artist,
        isrc=source.isrc, duration_ms=source.duration_ms,
    )


def _log_failed(
    db_path: str,
    source: NewTrack,
    match: MatchResult,
    error_message: str | None = None,
) -> None:
    target_platform = "youtube_music" if source.platform == "spotify" else "spotify"
    reason = error_message or match.reason or "unknown error"
    insert_sync_log(
        db_path,
        source_platform=source.platform,
        source_track_id=source.track_id,
        target_platform=target_platform,
        target_track_id=match.target_track_id,
        song_title=source.title,
        artist_name=source.artist,
        isrc=source.isrc,
        match_method=match.method,
        match_score=match.score,
        status="failed",
        error_message=reason,
    )
    log.warning("Failed to sync '%s' by '%s': %s", source.title, source.artist, reason)


def _handle_api_error(error: Exception, attempt: int, source: NewTrack) -> float | None:
    """Handle API errors. Returns seconds to wait before retry, or None to stop."""
    error_str = str(error).lower()

    # 401 Unauthorized — don't retry, token needs refresh
    if "401" in error_str or "unauthorized" in error_str:
        log.error("Auth error syncing '%s': %s", source.title, error)
        return None

    # 404 Not Found — track or playlist doesn't exist, don't retry
    if "404" in error_str or "not found" in error_str:
        log.error("Not found error syncing '%s': %s", source.title, error)
        return None

    # 429 Rate Limited — use Retry-After if available
    if "429" in error_str or "rate" in error_str:
        retry_after = getattr(error, "headers", {}).get("Retry-After")
        wait = int(retry_after) if retry_after else RETRY_BACKOFF[min(attempt, len(RETRY_BACKOFF) - 1)]
        log.warning("Rate limited, waiting %ds (attempt %d)", wait, attempt + 1)
        return wait

    # Transient errors — retry with backoff
    if attempt < MAX_RETRIES - 1:
        wait = RETRY_BACKOFF[attempt]
        log.warning("Transient error syncing '%s' (attempt %d): %s", source.title, attempt + 1, error)
        return wait

    log.error("Giving up on '%s' after %d attempts: %s", source.title, MAX_RETRIES, error)
    return None
