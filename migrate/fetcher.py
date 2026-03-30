"""Fetch public playlists from Spotify and YouTube Music — no user auth required on source."""

import re
from dataclasses import dataclass, field
from urllib.parse import urlparse, parse_qs

import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from ytmusicapi import YTMusic

from utils.logging import get_logger

log = get_logger("fetcher")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class TrackData:
    """A single track extracted from a source playlist."""
    track_id: str
    title: str
    artist: str
    album: str | None = None
    isrc: str | None = None
    duration_ms: int | None = None


@dataclass
class PlaylistData:
    """Complete snapshot of a public playlist."""
    name: str
    description: str
    platform: str
    playlist_id: str
    tracks: list[TrackData] = field(default_factory=list)
    track_count: int = 0
    owner: str | None = None
    image_url: str | None = None


# ---------------------------------------------------------------------------
# URL parsing
# ---------------------------------------------------------------------------

# Spotify: https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M
_SPOTIFY_URL_RE = re.compile(
    r"(?:https?://)?open\.spotify\.com/playlist/([A-Za-z0-9]+)", re.IGNORECASE
)

# YouTube Music: https://music.youtube.com/playlist?list=PLxyz...
_YOUTUBE_URL_RE = re.compile(
    r"(?:https?://)?music\.youtube\.com/playlist\?list=([A-Za-z0-9_-]+)", re.IGNORECASE
)

# Plain YouTube Music with query params
_YOUTUBE_URL_QS_RE = re.compile(
    r"(?:https?://)?music\.youtube\.com/playlist", re.IGNORECASE
)


def parse_playlist_url(url_or_id: str) -> tuple[str, str]:
    """Parse a playlist URL or ID into (platform, playlist_id).

    Supported formats:
      - Spotify URL:  https://open.spotify.com/playlist/<ID>
      - YouTube URL:  https://music.youtube.com/playlist?list=<ID>
      - spotify:<ID>  / youtube:<ID>  (explicit prefix)

    Raises ValueError if the format is unrecognised.
    """
    text = url_or_id.strip()

    # Explicit prefix shortcuts
    if text.startswith("spotify:"):
        return ("spotify", text.split(":", 1)[1])
    if text.startswith("youtube:") or text.startswith("youtube_music:"):
        return ("youtube_music", text.split(":", 1)[1])

    # Spotify URL
    m = _SPOTIFY_URL_RE.search(text)
    if m:
        return ("spotify", m.group(1))

    # YouTube Music URL (regex)
    m = _YOUTUBE_URL_RE.search(text)
    if m:
        return ("youtube_music", m.group(1))

    # YouTube Music URL (query-string fallback)
    if _YOUTUBE_URL_QS_RE.search(text):
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        list_id = qs.get("list", [None])[0]
        if list_id:
            return ("youtube_music", list_id)

    raise ValueError(
        f"Could not determine platform from: {url_or_id!r}. "
        "Provide a Spotify or YouTube Music playlist URL, or prefix with 'spotify:' / 'youtube:'."
    )


# ---------------------------------------------------------------------------
# Spotify public playlist fetcher
# ---------------------------------------------------------------------------

def fetch_spotify_playlist(
    playlist_id: str,
    client_id: str,
    client_secret: str,
) -> PlaylistData:
    """Fetch a public Spotify playlist using Client Credentials (no user login).

    Raises an exception if the playlist is private or doesn't exist.
    """
    auth_manager = SpotifyClientCredentials(
        client_id=client_id,
        client_secret=client_secret,
    )
    sp = spotipy.Spotify(auth_manager=auth_manager)

    # Fetch metadata
    meta = sp.playlist(playlist_id, fields="name,description,owner(display_name),images")
    name = meta.get("name", "Untitled Playlist")
    description = meta.get("description", "")
    owner = meta.get("owner", {}).get("display_name")
    images = meta.get("images", [])
    image_url = images[0]["url"] if images else None

    # Fetch all tracks (handles pagination)
    tracks = _fetch_all_spotify_public_tracks(sp, playlist_id)

    log.info("Fetched Spotify playlist '%s' — %d tracks", name, len(tracks))

    return PlaylistData(
        name=name,
        description=description or "",
        platform="spotify",
        playlist_id=playlist_id,
        tracks=tracks,
        track_count=len(tracks),
        owner=owner,
        image_url=image_url,
    )


def _fetch_all_spotify_public_tracks(sp: spotipy.Spotify, playlist_id: str) -> list[TrackData]:
    """Paginate through all tracks in a Spotify playlist."""
    tracks: list[TrackData] = []
    results = sp.playlist_items(
        playlist_id,
        fields="items(track(id,name,artists,album(name),external_ids,duration_ms)),next",
    )

    while results:
        for item in results.get("items", []):
            track = item.get("track")
            if not track or not track.get("id"):
                continue  # skip local files or unavailable tracks
            artists = ", ".join(a["name"] for a in track.get("artists", []))
            external_ids = track.get("external_ids", {})
            album_name = track.get("album", {}).get("name")
            tracks.append(TrackData(
                track_id=track["id"],
                title=track["name"],
                artist=artists,
                album=album_name,
                isrc=external_ids.get("isrc"),
                duration_ms=track.get("duration_ms"),
            ))
        if results.get("next"):
            results = sp.next(results)
        else:
            break

    return tracks


# ---------------------------------------------------------------------------
# YouTube Music public playlist fetcher
# ---------------------------------------------------------------------------

def fetch_youtube_playlist(playlist_id: str) -> PlaylistData:
    """Fetch a public YouTube Music playlist — no authentication needed.

    Raises an exception if the playlist is private or doesn't exist.
    """
    yt = YTMusic()  # unauthenticated client
    playlist = yt.get_playlist(playlist_id, limit=None)

    name = playlist.get("title", "Untitled Playlist")
    description = playlist.get("description", "")
    owner = playlist.get("author", {}).get("name") if isinstance(playlist.get("author"), dict) else playlist.get("author")

    playlist_tracks = playlist.get("tracks", [])
    tracks: list[TrackData] = []

    for t in playlist_tracks:
        vid = t.get("videoId")
        if not vid:
            continue
        artists = ", ".join(a["name"] for a in t.get("artists", []) if a.get("name"))
        album_name = t.get("album", {}).get("name") if isinstance(t.get("album"), dict) else None

        duration_ms = None
        if t.get("duration_seconds"):
            duration_ms = t["duration_seconds"] * 1000
        elif t.get("duration"):
            duration_ms = _parse_duration(t["duration"])

        tracks.append(TrackData(
            track_id=vid,
            title=t.get("title", ""),
            artist=artists,
            album=album_name,
            duration_ms=duration_ms,
        ))

    log.info("Fetched YouTube Music playlist '%s' — %d tracks", name, len(tracks))

    return PlaylistData(
        name=name,
        description=description or "",
        platform="youtube_music",
        playlist_id=playlist_id,
        tracks=tracks,
        track_count=len(tracks),
        owner=owner,
    )


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
