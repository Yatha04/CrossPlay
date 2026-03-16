"""Sync engine — orchestrates Poller → Matcher → Writer pipeline."""

from sync.matcher import find_match, TrackInfo
from sync.poller import poll_spotify, poll_youtube, NewTrack
from sync.writer import write_to_spotify, write_to_youtube
from utils.logging import get_logger

log = get_logger("engine")


def run_sync_cycle(
    sp,
    yt,
    spotify_playlist_id: str,
    youtube_playlist_id: str,
    db_path: str,
    fuzzy_threshold: int = 85,
) -> dict:
    """Run a single full sync cycle: poll → match → write.

    Returns a summary dict with counts of synced/failed tracks.
    """
    summary = {"spotify_to_yt": 0, "yt_to_spotify": 0, "failed": 0, "skipped": 0}

    # --- Spotify → YouTube Music ---
    try:
        spotify_new = poll_spotify(sp, spotify_playlist_id, db_path)
    except Exception as e:
        log.error("Failed to poll Spotify: %s", e)
        spotify_new = []

    for track in spotify_new:
        success = _sync_track_to_youtube(
            track, yt, youtube_playlist_id, db_path, fuzzy_threshold
        )
        if success:
            summary["spotify_to_yt"] += 1
        else:
            summary["failed"] += 1

    # --- YouTube Music → Spotify ---
    try:
        youtube_new = poll_youtube(yt, youtube_playlist_id, db_path)
    except Exception as e:
        log.error("Failed to poll YouTube Music: %s", e)
        youtube_new = []

    for track in youtube_new:
        success = _sync_track_to_spotify(
            track, sp, spotify_playlist_id, db_path, fuzzy_threshold
        )
        if success:
            summary["yt_to_spotify"] += 1
        else:
            summary["failed"] += 1

    total = summary["spotify_to_yt"] + summary["yt_to_spotify"]
    if total > 0 or summary["failed"] > 0:
        log.info(
            "Sync cycle complete: %d synced (%d SP→YT, %d YT→SP), %d failed",
            total, summary["spotify_to_yt"], summary["yt_to_spotify"], summary["failed"],
        )
    else:
        log.info("Sync cycle complete: no changes")

    return summary


def _sync_track_to_youtube(
    track: NewTrack,
    yt,
    youtube_playlist_id: str,
    db_path: str,
    fuzzy_threshold: int,
) -> bool:
    """Search YouTube Music for a match and write it."""
    candidates = _search_youtube(yt, track)
    match = find_match(
        TrackInfo(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
        ),
        candidates,
        fuzzy_threshold=fuzzy_threshold,
    )
    return write_to_youtube(yt, youtube_playlist_id, track, match, db_path)


def _sync_track_to_spotify(
    track: NewTrack,
    sp,
    spotify_playlist_id: str,
    db_path: str,
    fuzzy_threshold: int,
) -> bool:
    """Search Spotify for a match and write it."""
    candidates = _search_spotify(sp, track)
    match = find_match(
        TrackInfo(
            track_id=track.track_id,
            title=track.title,
            artist=track.artist,
            isrc=track.isrc,
            duration_ms=track.duration_ms,
        ),
        candidates,
        fuzzy_threshold=fuzzy_threshold,
    )
    return write_to_spotify(sp, spotify_playlist_id, track, match, db_path)


def _search_youtube(yt, track: NewTrack) -> list[TrackInfo]:
    """Search YouTube Music for candidates matching the source track."""
    try:
        query = f"{track.artist} {track.title}"
        results = yt.search(query, filter="songs", limit=10)
        candidates = []
        for r in results:
            vid = r.get("videoId")
            if not vid:
                continue
            artists = ", ".join(a["name"] for a in r.get("artists", []) if a.get("name"))
            duration_ms = None
            if r.get("duration_seconds"):
                duration_ms = r["duration_seconds"] * 1000
            candidates.append(TrackInfo(
                track_id=vid,
                title=r.get("title", ""),
                artist=artists,
                duration_ms=duration_ms,
            ))
        return candidates
    except Exception as e:
        log.error("YouTube search failed for '%s': %s", track.title, e)
        return []


def _search_spotify(sp, track: NewTrack) -> list[TrackInfo]:
    """Search Spotify for candidates matching the source track."""
    try:
        query = f"track:{track.title} artist:{track.artist}"
        results = sp.search(q=query, type="track", limit=10)
        candidates = []
        for item in results.get("tracks", {}).get("items", []):
            if not item.get("id"):
                continue
            artists = ", ".join(a["name"] for a in item.get("artists", []))
            external_ids = item.get("external_ids", {})
            candidates.append(TrackInfo(
                track_id=item["id"],
                title=item["name"],
                artist=artists,
                isrc=external_ids.get("isrc"),
                duration_ms=item.get("duration_ms"),
            ))
        return candidates
    except Exception as e:
        log.error("Spotify search failed for '%s': %s", track.title, e)
        return []
