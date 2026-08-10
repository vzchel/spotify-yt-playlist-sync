# Spotify <-> Youtube Playlist Sync

Convert a playlist between Spotify and YouTube Music

## 0. Fork this repo

Fork the repo for the continuous weekly sync and 'sync_state.json'

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
4. Right click and Copy -> Copy as fetch (node.js)
4. In this project folder, run:

```
python fetchtobrowser.py
```

   and paste the 'Copy as fetch (node.js)' when prompted. This creates `browser.json`, which `ytmusic_client.py`
   picks up automatically.


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

Playlist URLs or bare IDs both work for `--playlist`, and you can pass several at once
to sync multiple playlists in one run:

```
python sync.py --to ytmusic --playlist <url1> <url2> <url3>
```

### Syncing into playlists you already have

By default the first run creates a new destination playlist. To sync into an existing
one instead, copy `template_sync_state.json` to `sync_state.json` and fill in the IDs:
the Spotify playlist ID is the part of the URL after `/playlist/` (before any `?`), and
the YouTube Music playlist ID is the `list=` value in its URL. Then, if the destination
playlist already contains the songs, run once with `--baseline`:

```
python sync.py --to ytmusic --playlist <spotify_url> --baseline
```

This records every current source track as already synced without adding anything, so
the next real run only picks up songs added since. Skip `--baseline` if the destination
playlist is empty and you want everything copied in.

Runs are incremental: `sync_state.json` records the playlist created on the destination
and which source tracks are already in it, so re-running the same conversion adds only
new tracks to the existing playlist instead of creating a duplicate. Delete the entry
from `sync_state.json` (or the whole file) to start fresh. Unmatched tracks are retried
on every run.

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
