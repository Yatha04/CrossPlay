"""Periodic playlist polling — detects new tracks on both platforms."""

from dataclasses import dataclass

from db.queries import (
    get_playlist_state,
    upsert_playlist_state,
    should_sync,
)
from utils.logging import get_logger

log = get_logger("poller")


@dataclass
class NewTrack:
    """A track detected as newly added to a playlist."""
    platform: str
    track_id: str
    title: str
    artist: str
    isrc: str | None = None
    duration_ms: int | None = None


# ---------------------------------------------------------------------------
# Spotify polling
# ---------------------------------------------------------------------------

def poll_spotify(sp, playlist_id: str, db_path: str) -> list[NewTrack]:
    """Check Spotify playlist for new tracks using snapshot_id for efficiency.

    Returns a list of NewTrack objects for tracks that should be synced.
    """
    state = get_playlist_state(db_path, "spotify", playlist_id)
    last_snapshot = state["last_snapshot"] if state else None

    # Fetch current snapshot
    playlist_meta = sp.playlist(playlist_id, fields="snapshot_id")
    current_snapshot = playlist_meta["snapshot_id"]

    if current_snapshot == last_snapshot:
        log.info("Spotify snapshot unchanged, skipping")
        return []

    log.info("Spotify snapshot changed: %s → %s", last_snapshot, current_snapshot)

    # Fetch all tracks
    tracks = _fetch_all_spotify_tracks(sp, playlist_id)
    known_ids = set(state["last_track_ids"]) if state and state.get("last_track_ids") else set()
    current_ids = [t["track_id"] for t in tracks]

    new_tracks = []
    for t in tracks:
        if t["track_id"] not in known_ids:
            if should_sync(db_path, "spotify", t["track_id"], "youtube_music"):
                new_tracks.append(NewTrack(
                    platform="spotify",
                    track_id=t["track_id"],
                    title=t["title"],
                    artist=t["artist"],
                    isrc=t.get("isrc"),
                    duration_ms=t.get("duration_ms"),
                ))

    # Update state
    upsert_playlist_state(db_path, "spotify", playlist_id, current_snapshot, current_ids)

    if new_tracks:
        log.info("%d new track(s) on Spotify", len(new_tracks))
    return new_tracks


def _fetch_all_spotify_tracks(sp, playlist_id: str) -> list[dict]:
    """Fetch all tracks from a Spotify playlist, handling pagination."""
    tracks = []
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists,external_ids,duration_ms)),next",
    )

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if not track or not track.get("id"):
                continue  # skip local files or unavailable tracks
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            external_ids = track.get("external_ids", {})
            tracks.append({
                "track_id": track["id"],
                "title": track["name"],
                "artist": artists,
                "isrc": external_ids.get("isrc"),
                "duration_ms": track.get("duration_ms"),
            })
        if results.get("next"):
            results = sp.next(results)
        else:
            break

    return tracks


# ---------------------------------------------------------------------------
# YouTube Music polling
# ---------------------------------------------------------------------------

def poll_youtube(yt, playlist_id: str, db_path: str) -> list[NewTrack]:
    """Check YouTube Music playlist for new tracks via full diff.

    Returns a list of NewTrack objects for tracks that should be synced.
    """
    state = get_playlist_state(db_path, "youtube_music", playlist_id)
    known_ids = set(state["last_track_ids"]) if state and state.get("last_track_ids") else set()

    playlist = yt.get_playlist(playlist_id, limit=None)
    playlist_tracks = playlist.get("tracks", [])
    current_ids = [t["videoId"] for t in playlist_tracks if t.get("videoId")]

    new_tracks = []
    for t in playlist_tracks:
        vid = t.get("videoId")
        if not vid:
            continue
        if vid not in known_ids:
            if should_sync(db_path, "youtube_music", vid, "spotify"):
                artists = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
                duration_ms = None
                if t.get("duration_seconds"):
                    duration_ms = t["duration_seconds"] * 1000
                elif t.get("duration"):
                    duration_ms = _parse_duration(t["duration"])

                new_tracks.append(NewTrack(
                    platform="youtube_music",
                    track_id=vid,
                    title=t.get("title", ""),
                    artist=artists,
                    duration_ms=duration_ms,
                ))

    # Update state
    upsert_playlist_state(db_path, "youtube_music", playlist_id, last_track_ids=current_ids)

    if new_tracks:
        log.info("%d new track(s) on YouTube Music", len(new_tracks))
    return new_tracks


def _parse_duration(duration_str: str) -> int | None:
    """Parse 'M:SS' or 'H:MM:SS' to milliseconds."""
    try:
        parts = duration_str.split(":")
        if len(parts) == 2:
            return (int(parts[0]) * 60 + int(parts[1])) * 1000
        if len(parts) == 3:
            return (int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])) * 1000
    except (ValueError, AttributeError):
        pass
    return None
