"""SQLite table definitions as raw SQL constants."""

AUTH_TOKENS_TABLE = """
CREATE TABLE IF NOT EXISTS auth_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    user_label      TEXT NOT NULL,
    access_token    TEXT NOT NULL,
    refresh_token   TEXT NOT NULL,
    token_expiry    DATETIME,
    playlist_id     TEXT NOT NULL,
    created_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, user_label)
);
"""

SYNC_LOG_TABLE = """
CREATE TABLE IF NOT EXISTS sync_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    source_platform TEXT NOT NULL,
    source_track_id TEXT NOT NULL,
    target_platform TEXT NOT NULL,
    target_track_id TEXT,
    song_title      TEXT,
    artist_name     TEXT,
    isrc            TEXT,
    match_method    TEXT,
    match_score     REAL,
    status          TEXT DEFAULT 'synced',
    error_message   TEXT,
    synced_at       DATETIME DEFAULT CURRENT_TIMESTAMP
);
"""

SONG_CACHE_TABLE = """
CREATE TABLE IF NOT EXISTS song_cache (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    track_id        TEXT NOT NULL,
    title           TEXT,
    artist          TEXT,
    album           TEXT,
    isrc            TEXT,
    duration_ms     INTEGER,
    cached_at       DATETIME DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(platform, track_id)
);
"""

PLAYLIST_STATE_TABLE = """
CREATE TABLE IF NOT EXISTS playlist_state (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT NOT NULL,
    playlist_id     TEXT NOT NULL,
    last_snapshot   TEXT,
    last_track_ids  TEXT,
    last_polled_at  DATETIME,
    UNIQUE(platform, playlist_id)
);
"""

INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_sync_source ON sync_log(source_platform, source_track_id);",
    "CREATE INDEX IF NOT EXISTS idx_sync_target ON sync_log(target_platform, target_track_id);",
    "CREATE INDEX IF NOT EXISTS idx_sync_isrc ON sync_log(isrc);",
    "CREATE INDEX IF NOT EXISTS idx_cache_lookup ON song_cache(platform, track_id);",
]

ALL_TABLES = [AUTH_TOKENS_TABLE, SYNC_LOG_TABLE, SONG_CACHE_TABLE, PLAYLIST_STATE_TABLE]
