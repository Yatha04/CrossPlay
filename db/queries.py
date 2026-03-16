"""Common database operations for all tables."""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any


def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# auth_tokens
# ---------------------------------------------------------------------------

def upsert_auth_token(
    db_path: str,
    platform: str,
    user_label: str,
    access_token: str,
    refresh_token: str,
    playlist_id: str,
    token_expiry: str | None = None,
) -> int:
    """Insert or update an auth token. Returns the row id."""
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO auth_tokens (platform, user_label, access_token, refresh_token, token_expiry, playlist_id)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, user_label) DO UPDATE SET
                access_token = excluded.access_token,
                refresh_token = excluded.refresh_token,
                token_expiry = excluded.token_expiry,
                playlist_id = excluded.playlist_id,
                updated_at = CURRENT_TIMESTAMP
            """,
            (platform, user_label, access_token, refresh_token, token_expiry, playlist_id),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_auth_token(db_path: str, platform: str, user_label: str) -> dict | None:
    """Return the auth token row as a dict, or None."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM auth_tokens WHERE platform = ? AND user_label = ?",
            (platform, user_label),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# sync_log
# ---------------------------------------------------------------------------

def insert_sync_log(
    db_path: str,
    source_platform: str,
    source_track_id: str,
    target_platform: str,
    target_track_id: str | None = None,
    song_title: str | None = None,
    artist_name: str | None = None,
    isrc: str | None = None,
    match_method: str | None = None,
    match_score: float | None = None,
    status: str = "synced",
    error_message: str | None = None,
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO sync_log
                (source_platform, source_track_id, target_platform, target_track_id,
                 song_title, artist_name, isrc, match_method, match_score, status, error_message)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (source_platform, source_track_id, target_platform, target_track_id,
             song_title, artist_name, isrc, match_method, match_score, status, error_message),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def is_already_synced(db_path: str, source_platform: str, source_track_id: str) -> bool:
    """True if this source track has already been synced (any status)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sync_log WHERE source_platform = ? AND source_track_id = ?",
            (source_platform, source_track_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def is_echo(db_path: str, platform: str, track_id: str) -> bool:
    """True if this track was placed on *platform* by a previous sync (echo prevention)."""
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT 1 FROM sync_log WHERE target_platform = ? AND target_track_id = ?",
            (platform, track_id),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


def should_sync(db_path: str, source_platform: str, source_track_id: str, target_platform: str) -> bool:
    """Return True only if the track is a genuine new addition that should be synced."""
    if is_echo(db_path, source_platform, source_track_id):
        return False
    if is_already_synced(db_path, source_platform, source_track_id):
        return False
    return True


def get_sync_log_by_isrc(db_path: str, isrc: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute("SELECT * FROM sync_log WHERE isrc = ?", (isrc,)).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_failed_syncs(db_path: str) -> list[dict]:
    conn = _connect(db_path)
    try:
        rows = conn.execute(
            "SELECT * FROM sync_log WHERE status = 'failed' ORDER BY synced_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# song_cache
# ---------------------------------------------------------------------------

def upsert_song_cache(
    db_path: str,
    platform: str,
    track_id: str,
    title: str | None = None,
    artist: str | None = None,
    album: str | None = None,
    isrc: str | None = None,
    duration_ms: int | None = None,
) -> int:
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO song_cache (platform, track_id, title, artist, album, isrc, duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(platform, track_id) DO UPDATE SET
                title = excluded.title,
                artist = excluded.artist,
                album = excluded.album,
                isrc = excluded.isrc,
                duration_ms = excluded.duration_ms,
                cached_at = CURRENT_TIMESTAMP
            """,
            (platform, track_id, title, artist, album, isrc, duration_ms),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_cached_song(db_path: str, platform: str, track_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM song_cache WHERE platform = ? AND track_id = ?",
            (platform, track_id),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# playlist_state
# ---------------------------------------------------------------------------

def upsert_playlist_state(
    db_path: str,
    platform: str,
    playlist_id: str,
    last_snapshot: str | None = None,
    last_track_ids: list[str] | None = None,
) -> int:
    track_ids_json = json.dumps(last_track_ids) if last_track_ids is not None else None
    now = datetime.now(timezone.utc).isoformat()
    conn = _connect(db_path)
    try:
        cur = conn.execute(
            """
            INSERT INTO playlist_state (platform, playlist_id, last_snapshot, last_track_ids, last_polled_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(platform, playlist_id) DO UPDATE SET
                last_snapshot = excluded.last_snapshot,
                last_track_ids = excluded.last_track_ids,
                last_polled_at = excluded.last_polled_at
            """,
            (platform, playlist_id, last_snapshot, track_ids_json, now),
        )
        conn.commit()
        return cur.lastrowid
    finally:
        conn.close()


def get_playlist_state(db_path: str, platform: str, playlist_id: str) -> dict | None:
    conn = _connect(db_path)
    try:
        row = conn.execute(
            "SELECT * FROM playlist_state WHERE platform = ? AND playlist_id = ?",
            (platform, playlist_id),
        ).fetchone()
        if row is None:
            return None
        result = dict(row)
        if result.get("last_track_ids"):
            result["last_track_ids"] = json.loads(result["last_track_ids"])
        return result
    finally:
        conn.close()
