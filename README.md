# Spotify <-> Youtube Playlist Sync

Convert a playlist between Spotify and YouTube Music

## 1. Install dependencies

```
pip install -r requirements.txt
```

## 2. Set up Spotify credentials (unofficial, via [SpotAPI](https://github.com/Aran404/SpotAPI))

**Steps:**

1. Log into [open.spotify.com](https://open.spotify.com) in your browser.
2. Open DevTools (F12) -> **Network** tab, and reload the page (or click around).
3. Click any request to `open.spotify.com` (e.g. the page's initial `/` request or a `/api/...` call), find the **Request Headers** section, and copy the full value of the `Cookie:` header.
4. Create `spotify_cookies.json` in this project folder:

```json
{
  "identifier": "your_spotify_email_or_username",
  "cookies": "sp_dc=...; sp_key=...; (paste the whole Cookie header value here)"
}
```

## 3. Set up YouTube Music credentials (via [ytmusicapi](https://github.com/sigma67/ytmusicapi))

### Option A — Browser auth

1. Open [music.youtube.com](https://music.youtube.com) in your browser and make sure you're logged in.
2. Open DevTools (F12) -> **Network** tab, filter for `browse`.
3. Click any sidebar item (e.g. "Library") to trigger a request, click the `browse` request,
   and copy the **request headers**.
4. In this project folder, run:

```
ytmusicapi browser
```

   and paste the headers when prompted. This creates `browser.json`, which `ytmusic_client.py`
   picks up automatically.

Full walkthrough: https://ytmusicapi.readthedocs.io/en/stable/setup/browser.html

### Option B — OAuth

1. In [Google Cloud Console](https://console.cloud.google.com/), create a project and enable the
   **YouTube Data API v3**.
2. Create OAuth client credentials of type **TVs and Limited Input devices**.
3. Run:

```
ytmusicapi oauth --client-id YOUR_CLIENT_ID --client-secret YOUR_CLIENT_SECRET
```

   and follow the prompts. This creates `oauth.json`, which takes priority over `browser.json`
   if both exist.

Full walkthrough: https://ytmusicapi.readthedocs.io/en/stable/setup/oauth.html

> Google has occasionally restricted this OAuth flow for third-party tools; if it stops
> working, switch to Option A.

## 4. Run it

Spotify -> YouTube Music:

```
python sync.py --to ytmusic --playlist https://open.spotify.com/playlist/
```

YouTube Music -> Spotify:

```
python sync.py --to spotify --playlist https://music.youtube.com/playlist?
```

Other flags:

- `--name "Custom Name"` — name the new playlist (defaults to the source playlist's name).
- `--dry-run` — match tracks and print/save the report, but don't create or modify any playlist.

Playlist URLs or bare IDs both work for `--playlist`.

## How it works

```
Spotify track
    ↓
Exact artist + title search
    ↓
No result?
    ↓
Normalized title + primary artist
    ↓
No result?
    ↓
Remove feat./with/remix metadata and retry
    ↓
No result?
    ↓
Search title + all artists
    ↓
Score candidates
    ↓
Accept only if score ≥ threshold
```

For each source track, the destination service is searched and every candidate is scored:

- Fuzzy title and artist similarity (`rapidfuzz`), after stripping noise
- Duration closeness (bonus if within a few seconds, penalty if very different).
- **Audio-only preference**: YouTube Music searches use `filter="songs"` to restrict
  results to catalog audio tracks; any result that's still a music video is penalized
  in scoring so an audio-only match wins whenever one exists.
- **Explicit preference**: candidates flagged explicit get a small score bonus, so an
  explicit version is chosen over a clean one when both are close matches.
- **Original-only**: No Radio Edits, Live Versions, Remastered, etc

The top-scoring candidate is used if it clears a minimum similarity threshold; otherwise
the track is left unmatched and logged to `unmatched_tracks.csv` at the end of the run.
