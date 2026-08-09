"""Persistent record of playlists created by sync.py and the tracks already synced
into them, so repeat runs update the same destination playlist instead of creating
a new one each time."""

import json
from pathlib import Path

STATE_FILE = Path("sync_state.json")


def key(source: str, playlist_id: str, destination: str) -> str:
    return f"{source}:{playlist_id}:{destination}"


def load() -> dict:
    if STATE_FILE.exists():
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def save(state: dict) -> None:
    with STATE_FILE.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2)
        f.write("\n")
