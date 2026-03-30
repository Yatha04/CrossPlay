"""Migration orchestrator — fetch source playlist → create target → match & write tracks."""

import threading
from dataclasses import dataclass, field

from migrate.fetcher import (
    PlaylistData,
    TrackData,
    fetch_spotify_playlist,
    fetch_youtube_playlist,
    parse_playlist_url,
)
from sync.matcher import TrackInfo, find_match
from sync.engine import _search_youtube, _search_spotify
from db.queries import (
    create_migration_job,
    update_migration_job,
    insert_migration_track,
)
from utils.logging import get_logger

log = get_logger("migrator")


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class TrackStatus:
    source_track_id: str
    title: str
    artist: str
    target_track_id: str | None = None
    match_method: str | None = None
    match_score: float = 0.0
    status: str = "pending"  # pending, matched, failed, skipped
    error: str | None = None


@dataclass
class MigrationResult:
    job_id: int
    source_name: str
    source_platform: str
    target_platform: str
    target_playlist_id: str | None = None
    total_tracks: int = 0
    matched: int = 0
    failed: int = 0
    skipped: int = 0
    tracks: list[TrackStatus] = field(default_factory=list)
    status: str = "pending"
    error: str | None = None


# ---------------------------------------------------------------------------
# Core migration logic
# ---------------------------------------------------------------------------

def run_migration(
    source_url: str,
    target_platform: str,
    sp,
    yt,
    db_path: str,
    client_id: str,
    client_secret: str,
    fuzzy_threshold: int = 85,
) -> MigrationResult:
    """Run a full playlist migration synchronously.

    1. Parse source URL
    2. Fetch all tracks from source (public, no user auth needed)
    3. Create a new playlist on the target platform
    4. For each track: search target → match → write
    5. Log everything to migration_jobs / migration_tracks tables
    """
    # --- Step 1: Parse source ---
    source_platform, source_playlist_id = parse_playlist_url(source_url)

    if source_platform == target_platform:
        raise ValueError(
            f"Source and target are the same platform: {source_platform}. "
            "Migration requires different source and target platforms."
        )

    # --- Step 2: Fetch source playlist ---
    log.info("Fetching source playlist from %s: %s", source_platform, source_playlist_id)
    playlist = _fetch_source(source_platform, source_playlist_id, client_id, client_secret)

    if not playlist.tracks:
        raise ValueError(f"Playlist '{playlist.name}' has no tracks to migrate.")

    # --- Step 3: Create DB job ---
    job_id = create_migration_job(
        db_path,
        source_platform=source_platform,
        source_playlist_id=source_playlist_id,
        source_playlist_name=playlist.name,
        target_platform=target_platform,
        total_tracks=len(playlist.tracks),
    )

    update_migration_job(db_path, job_id, status="running")

    # --- Step 4: Create target playlist ---
    try:
        target_playlist_id = _create_target_playlist(
            target_platform, sp, yt, playlist.name, playlist.description
        )
    except Exception as e:
        log.error("Failed to create target playlist: %s", e)
        update_migration_job(db_path, job_id, status="failed")
        raise

    update_migration_job(db_path, job_id, target_playlist_id=target_playlist_id)

    # --- Step 5: Match & write each track ---
    result = MigrationResult(
        job_id=job_id,
        source_name=playlist.name,
        source_platform=source_platform,
        target_platform=target_platform,
        target_playlist_id=target_playlist_id,
        total_tracks=len(playlist.tracks),
        status="running",
    )

    for track_data in playlist.tracks:
        track_status = _migrate_single_track(
            track_data, target_platform, target_playlist_id,
            sp, yt, db_path, job_id, fuzzy_threshold,
        )
        result.tracks.append(track_status)
        if track_status.status == "matched":
            result.matched += 1
        elif track_status.status == "failed":
            result.failed += 1
        else:
            result.skipped += 1

        # Update job progress in DB
        update_migration_job(
            db_path, job_id,
            matched_tracks=result.matched,
            failed_tracks=result.failed,
        )

    # --- Step 6: Finalize ---
    result.status = "completed"
    update_migration_job(db_path, job_id, status="completed")

    log.info(
        "Migration complete: '%s' → %s | %d matched, %d failed, %d skipped out of %d",
        playlist.name, target_platform,
        result.matched, result.failed, result.skipped, result.total_tracks,
    )

    return result


def run_migration_async(
    source_url: str,
    target_platform: str,
    sp,
    yt,
    db_path: str,
    client_id: str,
    client_secret: str,
    fuzzy_threshold: int = 85,
) -> int:
    """Start a migration in a background thread. Returns the job_id immediately.

    The caller can poll GET /migrate/{job_id} for progress.
    """
    # Create the job record first so we can return the ID
    source_platform, source_playlist_id = parse_playlist_url(source_url)

    if source_platform == target_platform:
        raise ValueError(
            f"Source and target are the same platform: {source_platform}."
        )

    job_id = create_migration_job(
        db_path,
        source_platform=source_platform,
        source_playlist_id=source_playlist_id,
        source_playlist_name="",  # will be updated once fetched
        target_platform=target_platform,
        total_tracks=0,
    )

    def _background():
        try:
            _run_migration_from_job(
                job_id, source_url, target_platform,
                sp, yt, db_path, client_id, client_secret, fuzzy_threshold,
            )
        except Exception as e:
            log.error("Background migration %d failed: %s", job_id, e)
            update_migration_job(db_path, job_id, status="failed")

    thread = threading.Thread(target=_background, daemon=True, name=f"migration-{job_id}")
    thread.start()

    return job_id


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _run_migration_from_job(
    job_id: int,
    source_url: str,
    target_platform: str,
    sp, yt, db_path: str,
    client_id: str, client_secret: str,
    fuzzy_threshold: int,
) -> None:
    """Execute a migration for an already-created job record."""
    source_platform, source_playlist_id = parse_playlist_url(source_url)

    # Fetch source
    playlist = _fetch_source(source_platform, source_playlist_id, client_id, client_secret)

    update_migration_job(
        db_path, job_id,
        status="running",
        source_playlist_name=playlist.name,
        total_tracks=len(playlist.tracks),
    )

    if not playlist.tracks:
        update_migration_job(db_path, job_id, status="completed")
        return

    # Create target playlist
    target_playlist_id = _create_target_playlist(
        target_platform, sp, yt, playlist.name, playlist.description
    )
    update_migration_job(db_path, job_id, target_playlist_id=target_playlist_id)

    # Match and write
    matched = 0
    failed = 0
    for track_data in playlist.tracks:
        track_status = _migrate_single_track(
            track_data, target_platform, target_playlist_id,
            sp, yt, db_path, job_id, fuzzy_threshold,
        )
        if track_status.status == "matched":
            matched += 1
        else:
            failed += 1

        update_migration_job(
            db_path, job_id,
            matched_tracks=matched,
            failed_tracks=failed,
        )

    update_migration_job(db_path, job_id, status="completed")
    log.info("Background migration %d completed: %d matched, %d failed", job_id, matched, failed)


def _fetch_source(
    platform: str, playlist_id: str, client_id: str, client_secret: str
) -> PlaylistData:
    """Fetch source playlist data (public, no user auth)."""
    if platform == "spotify":
        return fetch_spotify_playlist(playlist_id, client_id, client_secret)
    elif platform == "youtube_music":
        return fetch_youtube_playlist(playlist_id)
    else:
        raise ValueError(f"Unsupported source platform: {platform}")


def _create_target_playlist(
    platform: str, sp, yt, name: str, description: str
) -> str:
    """Create a new playlist on the target platform. Returns the new playlist ID."""
    desc = description[:300] if description else ""  # API limits

    if platform == "spotify":
        user_id = sp.current_user()["id"]
        result = sp.user_playlist_create(
            user_id,
            name=name,
            public=False,
            description=f"Migrated by CrossPlay. {desc}".strip(),
        )
        playlist_id = result["id"]
        log.info("Created Spotify playlist '%s' (ID: %s)", name, playlist_id)
        return playlist_id

    elif platform == "youtube_music":
        playlist_id = yt.create_playlist(
            title=name,
            description=f"Migrated by CrossPlay. {desc}".strip(),
            privacy_status="PRIVATE",
        )
        log.info("Created YouTube Music playlist '%s' (ID: %s)", name, playlist_id)
        return playlist_id

    else:
        raise ValueError(f"Unsupported target platform: {platform}")


def _migrate_single_track(
    track_data: TrackData,
    target_platform: str,
    target_playlist_id: str,
    sp, yt, db_path: str,
    job_id: int,
    fuzzy_threshold: int,
) -> TrackStatus:
    """Search, match, and write a single track to the target playlist."""
    from sync.poller import NewTrack

    status = TrackStatus(
        source_track_id=track_data.track_id,
        title=track_data.title,
        artist=track_data.artist,
    )

    # Build a NewTrack for reusing existing search helpers
    source_track = NewTrack(
        platform="spotify" if target_platform == "youtube_music" else "youtube_music",
        track_id=track_data.track_id,
        title=track_data.title,
        artist=track_data.artist,
        isrc=track_data.isrc,
        duration_ms=track_data.duration_ms,
    )

    try:
        # Search on target platform
        if target_platform == "youtube_music":
            candidates = _search_youtube(yt, source_track)
        else:
            candidates = _search_spotify(sp, source_track)

        # Match
        source_info = TrackInfo(
            track_id=track_data.track_id,
            title=track_data.title,
            artist=track_data.artist,
            isrc=track_data.isrc,
            duration_ms=track_data.duration_ms,
        )
        match = find_match(source_info, candidates, fuzzy_threshold=fuzzy_threshold)

        if not match.matched or not match.target_track_id:
            status.status = "failed"
            status.error = match.reason or "no match found"
            log.warning("No match for '%s' by '%s': %s", track_data.title, track_data.artist, status.error)
        else:
            # Write to target playlist
            _write_track_to_target(
                target_platform, target_playlist_id, match.target_track_id, sp, yt
            )
            status.status = "matched"
            status.target_track_id = match.target_track_id
            status.match_method = match.method
            status.match_score = match.score
            log.info(
                "Migrated '%s' by '%s' → %s (method=%s, score=%.0f%%)",
                track_data.title, track_data.artist,
                match.target_track_id, match.method, match.score * 100,
            )

    except Exception as e:
        status.status = "failed"
        status.error = str(e)
        log.error("Error migrating '%s': %s", track_data.title, e)

    # Log to DB
    insert_migration_track(
        db_path,
        job_id=job_id,
        source_track_id=status.source_track_id,
        source_title=status.title,
        source_artist=status.artist,
        target_track_id=status.target_track_id,
        match_method=status.match_method,
        match_score=status.match_score,
        status=status.status,
        error_message=status.error,
    )

    return status


def _write_track_to_target(
    platform: str, playlist_id: str, track_id: str, sp, yt
) -> None:
    """Add a single track to the target playlist."""
    if platform == "spotify":
        sp.playlist_add_items(playlist_id, [f"spotify:track:{track_id}"])
    elif platform == "youtube_music":
        yt.add_playlist_items(playlist_id, [track_id])
    else:
        raise ValueError(f"Unsupported target platform: {platform}")
