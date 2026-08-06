"""Spotify integration via the unofficial SpotAPI client (https://github.com/Aran404/SpotAPI).
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any

from spotapi import Config, Login, Logger, PrivatePlaylist, PublicPlaylist, Song
from spotapi.http.request import TLSClient

from matcher import Candidate, Track

COOKIE_FILE = "spotify_cookies.json"
DEBUG_DIR = "spotapi_debug"


@dataclass
class SpotifyClient:
    login: Login


def get_client() -> SpotifyClient:
    if not os.path.exists(COOKIE_FILE):
        raise FileNotFoundError(
            f"{COOKIE_FILE} not found."
        )
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        dump = json.load(f)

    cfg = Config(logger=Logger(), client=TLSClient("chrome120", "", auto_retries=3))
    login = Login.from_cookies(dump, cfg)
    return SpotifyClient(login=login)


def extract_playlist_id(playlist_ref: str) -> str:
    match = re.search(r"playlist[/:]([a-zA-Z0-9]+)", playlist_ref)
    if match:
        return match.group(1)
    return playlist_ref.strip()


def _dump_debug(name: str, payload: Any) -> None:
    """Writes raw API responses to disk when SPOTAPI_DEBUG=1, since SpotAPI's internal
    JSON schema isn't documented and can shift -- this is for diagnosing parse failures."""
    if not os.environ.get("SPOTAPI_DEBUG"):
        return
    os.makedirs(DEBUG_DIR, exist_ok=True)
    safe_name = re.sub(r"[^\w.-]", "_", name)[:80]
    with open(os.path.join(DEBUG_DIR, f"{safe_name}.json"), "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)


def _extract_artist_names(data: dict) -> list[str]:
    names: list[str] = []
    artists = data.get("artists")
    if isinstance(artists, dict):
        artists = artists.get("items", [])
    if isinstance(artists, list):
        for a in artists:
            if not isinstance(a, dict):
                continue
            name = a.get("name") or (a.get("profile") or {}).get("name")
            if name:
                names.append(name)
    return names


def _parse_track_data(data: dict) -> Candidate | None:
    """Parses a Spotify pathfinder 'track' object into a Candidate.

    Field paths (duration, explicit, artist names) are matched defensively with
    fallbacks since SpotAPI reverse-engineers an undocumented, unversioned schema.
    """
    uri = data.get("uri")
    name = data.get("name")
    if not (isinstance(uri, str) and uri.startswith("spotify:track:") and name):
        return None

    duration = (
        (data.get("duration") or {}).get("totalMilliseconds")
        or data.get("durationMs")
        or data.get("duration_ms")
    )
    rating = (data.get("contentRating") or {}).get("label", "")
    explicit = str(rating).upper() == "EXPLICIT" or bool(data.get("isExplicit"))

    return Candidate(
        title=name,
        artists=_extract_artist_names(data) or ["Unknown"],
        id=uri.split("spotify:track:")[-1],
        duration_ms=int(duration) if duration else None,
        explicit=explicit,
        is_video=False,
        raw=data,
    )


def _walk_track_objects(node: Any, found: list[dict] | None = None) -> list[dict]:
    """Recursively scans a JSON response for track-shaped objects (uri + sibling name),
    rather than relying on one hardcoded path, since the exact nesting is unverified."""
    if found is None:
        found = []
    if isinstance(node, dict):
        uri = node.get("uri")
        if isinstance(uri, str) and uri.startswith("spotify:track:") and "name" in node:
            found.append(node)
        else:
            for value in node.values():
                _walk_track_objects(value, found)
    elif isinstance(node, list):
        for item in node:
            _walk_track_objects(item, found)
    return found


def get_playlist_name(client: SpotifyClient, playlist_id: str) -> str:
    public = PublicPlaylist(playlist_id, client=client.login.client)
    info = public.get_playlist_info(limit=1)
    _dump_debug(f"playlist_info_{playlist_id}", info)
    try:
        return info["data"]["playlistV2"]["name"]
    except (KeyError, TypeError):
        return playlist_id


def read_playlist_tracks(client: SpotifyClient, playlist_id: str) -> list[Track]:
    public = PublicPlaylist(playlist_id, client=client.login.client)
    tracks: list[Track] = []
    for i, chunk in enumerate(public.paginate_playlist()):
        _dump_debug(f"playlist_chunk_{playlist_id}_{i}", chunk)
        for item in chunk.get("items", []):
            data = (item.get("itemV2") or {}).get("data") or {}
            candidate = _parse_track_data(data)
            if candidate is None:
                continue
            tracks.append(
                Track(
                    title=candidate.title,
                    artists=candidate.artists,
                    duration_ms=candidate.duration_ms,
                    explicit=candidate.explicit,
                    source_id=candidate.id,
                )
            )
    return tracks


def search_track(client: SpotifyClient, track: Track) -> list[Candidate]:
    song = Song(client=client.login.client)
    query = f"{track.artists[0]} {track.title}"
    try:
        results = song.query_songs(query, limit=10)
    except Exception:
        return []
    _dump_debug(f"search_{query}", results)

    candidates = []
    for data in _walk_track_objects(results):
        candidate = _parse_track_data(data)
        if candidate:
            candidates.append(candidate)
    return candidates


def create_playlist(client: SpotifyClient, name: str, description: str = "") -> str:
    # SpotAPI's create_playlist has no parameter for setting a description.
    private = PrivatePlaylist(client.login)
    playlist_uri = private.create_playlist(name)
    return playlist_uri.split("spotify:playlist:")[-1]


def add_tracks(client: SpotifyClient, playlist_id: str, track_ids: list[str]) -> None:
    private = PrivatePlaylist(client.login)
    private.set_playlist(playlist_id)
    song = Song(playlist=private)
    for i in range(0, len(track_ids), 100):
        song.add_songs_to_playlist(track_ids[i : i + 100])
