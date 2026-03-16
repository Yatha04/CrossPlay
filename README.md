# CrossPlay

Bidirectional YouTube Music ↔ Spotify playlist sync. Add a song on one platform, it shows up on the other within minutes. After a one-time OAuth setup, it runs autonomously in the background.

## How It Works

```
Poller (3-min interval)
  ├── Spotify: snapshot_id change detection
  └── YouTube Music: full playlist diff
        │
        ▼
Matcher (5-tier resolution)
  ISRC → exact artist+title → fuzzy match → duration check → skip+log
        │
        ▼
Writer
  Adds matched track to target playlist, logs to sync_log (dedup)
```

- **Add-only sync** — removals are not propagated
- **Idempotent** — sync_log prevents duplicates and infinite echo loops
- **Cheap** — ~480 API calls/day per platform, well within free tiers

## Prerequisites

- Python 3.11+
- A [Spotify Developer](https://developer.spotify.com/) app (Premium account required for the app owner)
- A [Google Cloud](https://console.cloud.google.com/) project with YouTube Data API v3 enabled

## Setup

### 1. Clone and install

```bash
git clone https://github.com/Yatha04/CrossPlay.git
cd CrossPlay
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Fill in your `.env`:

| Variable | Description |
|---|---|
| `SPOTIFY_CLIENT_ID` | From your Spotify Developer app |
| `SPOTIFY_CLIENT_SECRET` | From your Spotify Developer app |
| `SPOTIFY_REDIRECT_URI` | Must match your Spotify app settings (default: `http://localhost:8888/callback`) |
| `SPOTIFY_PLAYLIST_ID` | Target Spotify playlist ID |
| `YT_OAUTH_JSON` | Base64-encoded oauth.json (see step 3) |
| `YOUTUBE_PLAYLIST_ID` | Target YouTube Music playlist ID |
| `DATABASE_PATH` | SQLite database path (default: `sync.db`) |
| `POLL_INTERVAL_SECONDS` | How often to check for changes (default: `180`) |
| `FUZZY_MATCH_THRESHOLD` | Minimum fuzzy match score 0-100 (default: `85`) |

### 3. Authenticate YouTube Music

```bash
python -m ytmusicapi oauth
```

This opens a browser for Google OAuth. Once complete, it generates `oauth.json`. Base64-encode it and set `YT_OAUTH_JSON`:

```bash
# Linux/macOS
base64 -i oauth.json

# Windows (PowerShell)
[Convert]::ToBase64String([IO.File]::ReadAllBytes("oauth.json"))
```

### 4. Run

```bash
python main.py
```

The service starts on port 8888. On first run:

1. Visit `http://localhost:8888/auth/spotify` to connect Spotify
2. YouTube Music auth was handled in step 3 (or POST to `/auth/youtube/callback`)
3. Sync begins automatically on a 3-minute interval

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/auth/spotify` | Redirects to Spotify OAuth |
| GET | `/auth/spotify/callback` | Handles Spotify OAuth callback |
| GET | `/auth/youtube` | Returns YouTube Music auth instructions |
| POST | `/auth/youtube/callback` | Accepts base64-encoded oauth.json |
| GET | `/health` | Service health: token status, poll age, failure count |

## Running Tests

```bash
pytest tests/ -v
```

206 tests across all modules: config, database, normalization, matching, polling, writing, sync engine, API routes, and main entry point.

## Project Structure

```
├── main.py              # Entry point: FastAPI + APScheduler
├── config.py            # Environment variable loader
├── api/routes.py        # OAuth callbacks + health check
├── auth/
│   ├── spotify_auth.py  # PKCE auth flow + token refresh
│   └── youtube_auth.py  # ytmusicapi OAuth wrapper
├── sync/
│   ├── engine.py        # Orchestrates Poller → Matcher → Writer
│   ├── poller.py        # Playlist change detection
│   ├── matcher.py       # 5-tier cross-platform song matching
│   └── writer.py        # Writes tracks with retry logic
├── db/
│   ├── models.py        # SQLite schema definitions
│   ├── migrations.py    # Idempotent table creation
│   └── queries.py       # CRUD operations + dedup logic
├── utils/
│   ├── normalize.py     # Title/artist string normalization
│   └── logging.py       # Structured logging
└── tests/               # 206 tests
```

## Tech Stack

Python 3.11+ · FastAPI · APScheduler · spotipy (Spotify PKCE) · ytmusicapi (YouTube Music) · SQLite · thefuzz (fuzzy matching)
