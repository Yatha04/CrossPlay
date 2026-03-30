# 🎵 CrossPlay

Welcome to CrossPlay! This app lets you sync playlists across spotify and youtube music seamlessly. I made this app becuase I wanted to have a shared playlist with someone on a different platform. This does the job pretty well. You can also use this app to migrate playlists from one platform to another.

When you add a new song to your playlist on Spotify, it automatically appears on YouTube Music within a few minutes—and vice versa! You only need to set it up once, and then it quietly does its magic in the background.


## 🌟 How it works

Imagine you have a playlist on Spotify and another one on YouTube Music. CrossPlay acts as an invisible bridge between them.

```mermaid
flowchart LR
    Spotify[Spotify Playlist]
    YTM[YouTube Music Playlist]
    CrossPlay((CrossPlay Magic ✨))
    
    Spotify -->|New song added| CrossPlay
    YTM -->|New song added| CrossPlay
    CrossPlay -->|Updates| Spotify
    CrossPlay -->|Updates| YTM
```

- **Two-way Sync:** Add a song on one platform, and it pops up on the other.
- **Add-only:** If you delete a song, it won't delete it on the other side. This keeps your music safe from accidental deletions!
- **Smart Matching:** It searches for the exact song. If it can't find it easily, it tries really hard using the artist name, song title, and track length to find the best match.
- **No Duplicates:** It remembers what it has already synced, so it will never add the same song twice.

## 🛠️ What do you need?

To get started, you'll need:
- A computer with Python installed (version 3.11 or newer).
- A Spotify Premium account (needed to create a connection).
- A Google account (for YouTube Music).

## 🚀 Step-by-Step Setup

Follow these steps to get everything running!

### 1. Download the Project
First, grab a copy of this app and install the necessary pieces:
```bash
git clone https://github.com/Yatha04/CrossPlay.git
cd CrossPlay
pip install -r requirements.txt
```

### 2. Set Up Your Secret Keys
Set up your credentials in `.env`.

Copy the example settings file to create your own:
```bash
cp .env.example .env
```

Open the new `.env` file and fill in your details:
- **Spotify Details:** Create an app on the [Spotify Developer Dashboard](https://developer.spotify.com/) to get your `Client ID` and `Client Secret`.
- **Playlist IDs:** The unique links of the playlists you want to sync.

### 3. Connect to YouTube Music
Run this command to allow the app to talk to your YouTube Music account:
```bash
python -m ytmusicapi oauth
```
A browser window will open. Sign in with your Google account. This will create a tiny file called `oauth.json`.

*(Note: The app needs this file converted into text format called Base64. You can convert it and paste the text into your `.env` file under `YT_OAUTH_JSON`.)*

### 4. Start the Magic!
Now, bring the app to life:
```bash
python main.py
```

Finally, open your web browser and go to this link to connect your Spotify account:
👉 `http://localhost:8888/auth/spotify`

**That's it! 🎉** Your playlists will now automatically check for new songs every 3 minutes.

---

### How I built this:
- I used FastAPI and SQLite to build the backend, so its fast and lightweight. 
- It uses a 5-step search system to find the best match for a song, and it uses a 3-step system to verify the match. 
- You can run 'pytest tests/ -v' to check all 206 automated tests!