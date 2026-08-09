"""YouTube Music API wrapper for reading and writing playlists.(https://github.com/sigma67/ytmusicapi)
"""

import os
import re
import time

from ytmusicapi import YTMusic
from ytmusicapi.exceptions import YTMusicServerError

from matcher import Candidate, Track, is_original

BROWSER_FILE = "./auth/browser.json"


def get_client() -> YTMusic:
    if os.path.exists(BROWSER_FILE):
        return YTMusic(BROWSER_FILE)
    raise FileNotFoundError(
        f"No YouTube Music credentials found. Run 'ytmusicapi browser' "
        f"to create {BROWSER_FILE} (see README.md)."
    )


def extract_playlist_id(playlist_ref: str) -> str:
    match = re.search(r"[?&]list=([a-zA-Z0-9_-]+)", playlist_ref)
    if match:
        return match.group(1)
    return playlist_ref.strip() # assumes user used ID instead


def get_playlist_name(yt: YTMusic, playlist_id: str) -> str:
    return yt.get_playlist(playlist_id, limit=1)["title"]


def read_playlist_tracks(yt: YTMusic, playlist_id: str) -> list[Track]:
    playlist = yt.get_playlist(playlist_id, limit=None)
    tracks: list[Track] = []
    for item in playlist["tracks"]:
        if not item.get("videoId"):
            continue
        duration_seconds = item.get("duration_seconds")
        tracks.append(
            Track(
                title=item["title"],
                artists=[a["name"] for a in item.get("artists", []) or []] or ["Unknown"],
                duration_ms=duration_seconds * 1000 if duration_seconds else None,
                explicit=item.get("isExplicit", False),
                source_id=item["videoId"],
                og=is_original(item["title"]),
            )
        )
    return tracks


def search_track(yt: YTMusic, track: Track) -> list[Candidate]:
    return search_query(yt, f"{track.artists[0]} {track.title}")


def search_query(yt: YTMusic, query: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    # filter="songs" restricts results to audio-only catalog tracks, excluding music videos.
    try:
        results = yt.search(query, filter="songs", limit=5, ignore_spelling=True)
    except Exception:
        results = []
    for r in results:
        if not r.get("videoId"):
            continue
        duration_seconds = r.get("duration_seconds")
        candidates.append(
            Candidate(
                title=r.get("title", ""),
                artists=[a["name"] for a in r.get("artists", []) or []] or ["Unknown"],
                id=r["videoId"],
                duration_ms=duration_seconds * 1000 if duration_seconds else None,
                explicit=r.get("isExplicit", False),
                is_video=(r.get("resultType") == "video"),
                raw=r,
                og=is_original(r.get("title", "")),
            )
        )
    return candidates


def create_playlist(yt: YTMusic, name: str, description: str = "") -> str:
    return yt.create_playlist(name, description, privacy_status="PRIVATE")


def add_tracks(yt: YTMusic, playlist_id: str, video_ids: list[str]) -> None:
    for i in range(0, len(video_ids), 100):
        _add_batch_with_retry(yt, playlist_id, video_ids[i : i + 100])


def _add_batch_with_retry(
    yt: YTMusic, playlist_id: str, batch: list[str], max_attempts: int = 5
) -> None:
    # A freshly created playlist can take a moment to propagate on YT Music's
    # backend; adding items right away frequently 409s until it settles.
    delay = 1.0
    for attempt in range(1, max_attempts + 1):
        try:
            # duplicates=True skips videoIds already in the playlist instead of
            # rejecting the whole batch -- with duplicates=False, if any single
            # id in the batch is already present, YT Music silently adds nothing
            # from that batch and returns a non-exception failure status.
            response = yt.add_playlist_items(playlist_id, batch, duplicates=True)
            status = response.get("status", "") if isinstance(response, dict) else str(response)
            if "SUCCEEDED" not in status:
                raise YTMusicServerError(f"add_playlist_items did not succeed: {response}")
            return
        except YTMusicServerError:
            if attempt == max_attempts:
                raise
            time.sleep(delay)
            delay *= 2
