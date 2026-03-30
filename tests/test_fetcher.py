"""Tests for migrate/fetcher.py — URL parsing and public playlist fetching."""

from unittest.mock import MagicMock, patch
import pytest

from migrate.fetcher import (
    parse_playlist_url,
    fetch_spotify_playlist,
    fetch_youtube_playlist,
    PlaylistData,
    TrackData,
    _parse_duration,
)


# ---------------------------------------------------------------------------
# parse_playlist_url
# ---------------------------------------------------------------------------

class TestParsePlaylistUrl:
    def test_spotify_url(self):
        url = "https://open.spotify.com/playlist/37i9dQZF1DXcBWIGoYBM5M"
        platform, pid = parse_playlist_url(url)
        assert platform == "spotify"
        assert pid == "37i9dQZF1DXcBWIGoYBM5M"

    def test_spotify_url_no_scheme(self):
        url = "open.spotify.com/playlist/abc123"
        platform, pid = parse_playlist_url(url)
        assert platform == "spotify"
        assert pid == "abc123"

    def test_youtube_music_url(self):
        url = "https://music.youtube.com/playlist?list=PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"
        platform, pid = parse_playlist_url(url)
        assert platform == "youtube_music"
        assert pid == "PLrAXtmErZgOeiKm4sgNOknGvNjby9efdf"

    def test_youtube_music_url_no_scheme(self):
        url = "music.youtube.com/playlist?list=PLtest123"
        platform, pid = parse_playlist_url(url)
        assert platform == "youtube_music"
        assert pid == "PLtest123"

    def test_spotify_prefix(self):
        platform, pid = parse_playlist_url("spotify:37i9dQZF1DXcBWIGoYBM5M")
        assert platform == "spotify"
        assert pid == "37i9dQZF1DXcBWIGoYBM5M"

    def test_youtube_prefix(self):
        platform, pid = parse_playlist_url("youtube:PLtest")
        assert platform == "youtube_music"
        assert pid == "PLtest"

    def test_youtube_music_prefix(self):
        platform, pid = parse_playlist_url("youtube_music:PLtest")
        assert platform == "youtube_music"
        assert pid == "PLtest"

    def test_invalid_url_raises(self):
        with pytest.raises(ValueError, match="Could not determine platform"):
            parse_playlist_url("not-a-valid-url")

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            parse_playlist_url("")

    def test_strips_whitespace(self):
        platform, pid = parse_playlist_url("  spotify:test123  ")
        assert platform == "spotify"
        assert pid == "test123"


# ---------------------------------------------------------------------------
# fetch_spotify_playlist
# ---------------------------------------------------------------------------

class TestFetchSpotifyPlaylist:
    @patch("migrate.fetcher.spotipy.Spotify")
    @patch("migrate.fetcher.SpotifyClientCredentials")
    def test_fetches_playlist(self, mock_creds, mock_spotify_cls):
        sp = MagicMock()
        mock_spotify_cls.return_value = sp

        sp.playlist.return_value = {
            "name": "Test Playlist",
            "description": "A cool playlist",
            "owner": {"display_name": "TestUser"},
            "images": [{"url": "https://example.com/img.jpg"}],
        }
        sp.playlist_items.return_value = {
            "items": [
                {"track": {
                    "id": "sp1", "name": "Song A",
                    "artists": [{"name": "Artist A"}],
                    "album": {"name": "Album A"},
                    "external_ids": {"isrc": "US1234"},
                    "duration_ms": 200000,
                }},
                {"track": {
                    "id": "sp2", "name": "Song B",
                    "artists": [{"name": "Artist B"}, {"name": "Artist C"}],
                    "album": {"name": "Album B"},
                    "external_ids": {},
                    "duration_ms": 180000,
                }},
            ],
            "next": None,
        }

        result = fetch_spotify_playlist("test_pl", "client_id", "client_secret")

        assert isinstance(result, PlaylistData)
        assert result.name == "Test Playlist"
        assert result.platform == "spotify"
        assert result.track_count == 2
        assert result.owner == "TestUser"
        assert result.image_url == "https://example.com/img.jpg"
        assert result.tracks[0].title == "Song A"
        assert result.tracks[0].isrc == "US1234"
        assert result.tracks[1].artist == "Artist B, Artist C"

    @patch("migrate.fetcher.spotipy.Spotify")
    @patch("migrate.fetcher.SpotifyClientCredentials")
    def test_handles_pagination(self, mock_creds, mock_spotify_cls):
        sp = MagicMock()
        mock_spotify_cls.return_value = sp

        sp.playlist.return_value = {
            "name": "Big Playlist", "description": "",
            "owner": {"display_name": "User"}, "images": [],
        }
        # First page
        sp.playlist_items.return_value = {
            "items": [{"track": {"id": "sp1", "name": "S1", "artists": [{"name": "A1"}], "external_ids": {}, "duration_ms": 100}}],
            "next": "https://api.spotify.com/next",
        }
        # Second page (returned by sp.next)
        sp.next.return_value = {
            "items": [{"track": {"id": "sp2", "name": "S2", "artists": [{"name": "A2"}], "external_ids": {}, "duration_ms": 100}}],
            "next": None,
        }

        result = fetch_spotify_playlist("pl", "cid", "csec")
        assert result.track_count == 2

    @patch("migrate.fetcher.spotipy.Spotify")
    @patch("migrate.fetcher.SpotifyClientCredentials")
    def test_skips_null_tracks(self, mock_creds, mock_spotify_cls):
        sp = MagicMock()
        mock_spotify_cls.return_value = sp

        sp.playlist.return_value = {
            "name": "PL", "description": "", "owner": {"display_name": ""}, "images": [],
        }
        sp.playlist_items.return_value = {
            "items": [
                {"track": None},  # local file or unavailable
                {"track": {"id": None, "name": "Bad"}},  # no ID
                {"track": {"id": "sp1", "name": "Good", "artists": [{"name": "A"}], "external_ids": {}, "duration_ms": 100}},
            ],
            "next": None,
        }

        result = fetch_spotify_playlist("pl", "cid", "csec")
        assert result.track_count == 1
        assert result.tracks[0].track_id == "sp1"


# ---------------------------------------------------------------------------
# fetch_youtube_playlist
# ---------------------------------------------------------------------------

class TestFetchYoutubePlaylist:
    @patch("migrate.fetcher.YTMusic")
    def test_fetches_playlist(self, mock_ytmusic_cls):
        yt = MagicMock()
        mock_ytmusic_cls.return_value = yt

        yt.get_playlist.return_value = {
            "title": "YT Playlist",
            "description": "YouTube playlist",
            "author": {"name": "YouTuber"},
            "tracks": [
                {
                    "videoId": "yt1", "title": "Song X",
                    "artists": [{"name": "Artist X"}],
                    "album": {"name": "Album X"},
                    "duration_seconds": 240,
                },
                {
                    "videoId": "yt2", "title": "Song Y",
                    "artists": [{"name": "Artist Y"}],
                    "album": {"name": "Album Y"},
                    "duration": "3:30",
                },
            ],
        }

        result = fetch_youtube_playlist("PLtest")

        assert isinstance(result, PlaylistData)
        assert result.name == "YT Playlist"
        assert result.platform == "youtube_music"
        assert result.track_count == 2
        assert result.tracks[0].duration_ms == 240000
        assert result.tracks[1].duration_ms == 210000  # 3:30

    @patch("migrate.fetcher.YTMusic")
    def test_skips_null_video_ids(self, mock_ytmusic_cls):
        yt = MagicMock()
        mock_ytmusic_cls.return_value = yt

        yt.get_playlist.return_value = {
            "title": "PL", "description": "",
            "tracks": [
                {"videoId": None, "title": "NoID"},
                {"videoId": "yt1", "title": "Good", "artists": [{"name": "A"}], "duration_seconds": 100},
            ],
        }

        result = fetch_youtube_playlist("PLtest")
        assert result.track_count == 1

    @patch("migrate.fetcher.YTMusic")
    def test_handles_author_string(self, mock_ytmusic_cls):
        yt = MagicMock()
        mock_ytmusic_cls.return_value = yt

        yt.get_playlist.return_value = {
            "title": "PL", "description": "",
            "author": "StringAuthor",
            "tracks": [],
        }

        result = fetch_youtube_playlist("PLtest")
        assert result.owner == "StringAuthor"


# ---------------------------------------------------------------------------
# _parse_duration
# ---------------------------------------------------------------------------

class TestParseDuration:
    def test_minutes_seconds(self):
        assert _parse_duration("3:45") == 225000

    def test_hours_minutes_seconds(self):
        assert _parse_duration("1:02:30") == 3750000

    def test_invalid(self):
        assert _parse_duration("invalid") is None

    def test_empty(self):
        assert _parse_duration("") is None
