"""YouTube Music auth via ytmusicapi — OAuth wrapper with DB persistence."""

import base64
import json
import os
import tempfile

from ytmusicapi import YTMusic

from db.queries import upsert_auth_token, get_auth_token
from utils.logging import get_logger

log = get_logger("youtube_auth")


def get_ytmusic_client(
    db_path: str,
    user_label: str = "user_a",
    yt_oauth_json_b64: str | None = None,
) -> YTMusic | None:
    """Return an authenticated YTMusic client.

    Priority:
      1. Token stored in DB
      2. Base64-encoded oauth.json from env/param
      3. None (needs initial OAuth)
    """
    # Try DB first
    token_row = get_auth_token(db_path, "youtube_music", user_label)
    if token_row:
        oauth_json_str = token_row["access_token"]  # we store the full oauth.json here
        return _client_from_json_str(oauth_json_str)

    # Try base64 env var
    if yt_oauth_json_b64:
        try:
            oauth_json_str = base64.b64decode(yt_oauth_json_b64).decode("utf-8")
            return _client_from_json_str(oauth_json_str)
        except Exception as e:
            log.error("Failed to decode YT_OAUTH_JSON: %s", e)
            return None

    log.warning("No YouTube Music credentials for %s — OAuth required", user_label)
    return None


def store_youtube_token(
    db_path: str,
    user_label: str,
    oauth_json_str: str,
    playlist_id: str,
) -> None:
    """Persist YouTube Music oauth.json contents in the database."""
    upsert_auth_token(
        db_path, "youtube_music", user_label,
        access_token=oauth_json_str,   # store full JSON as access_token
        refresh_token="",              # ytmusicapi handles refresh internally
        playlist_id=playlist_id,
    )
    log.info("Stored YouTube Music token for %s", user_label)


def _client_from_json_str(oauth_json_str: str) -> YTMusic | None:
    """Create a YTMusic client from an oauth.json string."""
    try:
        # ytmusicapi requires a file path, so write to a temp file
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            f.write(oauth_json_str)
            tmp_path = f.name

        client = YTMusic(tmp_path)

        # Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

        return client
    except Exception as e:
        log.error("Failed to create YTMusic client: %s", e)
        return None
