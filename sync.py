#!/usr/bin/env python3

"""Spotify <-> YouTube Music playlist converter.

Usage:
    python sync.py --to ytmusic  --playlist <spotify_playlist_url_or_id> [<another> ...] [--name "My Playlist"] [--dry-run]
    python sync.py --to spotify  --playlist <ytmusic_playlist_url_or_id> [<another> ...] [--name "My Playlist"] [--dry-run]
"""

import argparse
import csv
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import matcher
import spotify_client as sp_client
import sync_state
import ytmusic_client as yt_client
from matcher import Candidate, Track

SEARCH_WORKERS = 8


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert playlists between Spotify and YouTube Music.")
    parser.add_argument("--to", required=True, choices=["spotify", "ytmusic"], help="Destination service.")
    parser.add_argument("--playlist", required=True, nargs="+", help="One or more source playlist URLs or IDs.")
    parser.add_argument("--name", default=None, help="Name for the new playlist (defaults to the source playlist's name; only valid with a single --playlist).")
    parser.add_argument("--dry-run", action="store_true", help="Match tracks but don't create or modify any playlist.")
    parser.add_argument("--baseline", action="store_true", help="Don't add anything; record all current source tracks as already synced. Use once after pointing sync_state.json at an existing destination playlist that already contains the songs.")
    args = parser.parse_args()
    if args.name and len(args.playlist) > 1:
        parser.error("--name only makes sense with a single --playlist")
    if args.baseline and args.dry_run:
        parser.error("--baseline and --dry-run are mutually exclusive")
    return args


def sync_playlist(sp, yt, source: str, destination: str, playlist_ref: str,
                  name: Optional[str], dry_run: bool, baseline: bool) -> list[tuple[str, Track]]:
    """Syncs one playlist to the destination service.

    Returns the unmatched tracks as (playlist_name, track) pairs for the report.
    """
    print(f"Reading source playlist from {source}: {playlist_ref}")
    if source == "spotify":
        playlist_id = sp_client.extract_playlist_id(playlist_ref)
        playlist_name = sp_client.get_playlist_name(sp, playlist_id)
        tracks = sp_client.read_playlist_tracks(sp, playlist_id)
    else:
        playlist_id = yt_client.extract_playlist_id(playlist_ref)
        playlist_name = yt_client.get_playlist_name(yt, playlist_id)
        tracks = yt_client.read_playlist_tracks(yt, playlist_id)

    print(f"Found {len(tracks)} tracks in '{playlist_name}'.")
    if not tracks:
        print("Nothing to convert.")
        return []

    state = sync_state.load()
    state_key = sync_state.key(source, playlist_id, destination)
    entry = state.get(state_key)

    if baseline:
        if not entry or not entry.get("destination_playlist_id"):
            raise ValueError(
                f"--baseline needs an entry '{state_key}' in {sync_state.STATE_FILE} with "
                f"destination_playlist_id set (see template_sync_state.json)."
            )
        entry["synced_track_ids"] = sorted({t.source_id for t in tracks if t.source_id})
        state[state_key] = entry
        sync_state.save(state)
        print(f"Baseline: recorded {len(entry['synced_track_ids'])} tracks as already synced "
              f"to {entry['destination_playlist_id']}; nothing was added.")
        return []

    dest_playlist_id: Optional[str] = entry["destination_playlist_id"] if entry else None
    if dest_playlist_id and not dry_run:
        try:
            if destination == "ytmusic":
                yt_client.get_playlist_name(yt, dest_playlist_id)
            else:
                sp_client.get_playlist_name(sp, dest_playlist_id)
        except Exception:
            print(f"Previously created playlist {dest_playlist_id} is gone; will create a new one.")
            entry = None
            dest_playlist_id = None

    synced_ids: set[str] = set(entry["synced_track_ids"]) if entry else set()
    new_tracks = [t for t in tracks if t.source_id not in synced_ids]
    if entry:
        print(f"{len(tracks) - len(new_tracks)} tracks already synced; {len(new_tracks)} to process.")
    if not new_tracks:
        print("Playlist is already up to date.")
        return []
    tracks = new_tracks

    matched_ids: list[str] = []
    matched_source_ids: list[str] = []
    unmatched: list[Track] = []

    def match_track(track: Track) -> Optional[Candidate]:
        if destination == "ytmusic":
            return matcher.find_match(track, lambda q: yt_client.search_query(yt, q))
        return matcher.find_match(track, lambda q: sp_client.search_query(sp, q))

    # Searches are network-bound and independent, so run them concurrently;
    # pool.map yields results in playlist order.
    with ThreadPoolExecutor(max_workers=SEARCH_WORKERS) as pool:
        for i, (track, match) in enumerate(zip(tracks, pool.map(match_track, tracks)), 1):
            label = f"{', '.join(track.artists)} - {track.title}"
            if match:
                matched_ids.append(match.id)
                matched_source_ids.append(track.source_id)
                tag = " [video]" if match.is_video else ""
                exp = " [explicit]" if match.explicit else ""
                print(f"  [{i}/{len(tracks)}] OK   {label}  ->  {', '.join(match.artists)} - {match.title}{exp}{tag}")
            else:
                unmatched.append(track)
                print(f"  [{i}/{len(tracks)}] MISS {label}")

    print()
    print(f"Matched {len(matched_ids)}/{len(tracks)} tracks.")

    if dry_run:
        print("Dry run: no playlist was created or modified.")
        return [(playlist_name, t) for t in unmatched]

    if not matched_ids:
        print("No matches found; skipping playlist creation.")
        return [(playlist_name, t) for t in unmatched]

    if dest_playlist_id is None:
        new_name = name or playlist_name
        description = f"Converted from {source} playlist '{playlist_name}' via github.com/vzchel/spotify-yt-playlist-sync."
        print(f"Creating '{new_name}' on {destination}")
        if destination == "ytmusic":
            dest_playlist_id = yt_client.create_playlist(yt, new_name, description)
        else:
            dest_playlist_id = sp_client.create_playlist(sp, new_name, description)
    else:
        print(f"Adding to existing {destination} playlist {dest_playlist_id}")

    if destination == "ytmusic":
        yt_client.add_tracks(yt, dest_playlist_id, matched_ids)
        print(f"Done: https://music.youtube.com/playlist?list={dest_playlist_id}")
    else:
        sp_client.add_tracks(sp, dest_playlist_id, matched_ids)
        print(f"Done: https://open.spotify.com/playlist/{dest_playlist_id}")

    synced_ids.update(matched_source_ids)
    state = sync_state.load()  # reload in case another playlist's entry was saved meanwhile
    state[state_key] = {
        "destination_playlist_id": dest_playlist_id,
        "synced_track_ids": sorted(synced_ids),
    }
    sync_state.save(state)
    print(f"Sync state saved to {sync_state.STATE_FILE} ({len(synced_ids)} tracks tracked).")
    return [(playlist_name, t) for t in unmatched]


def main() -> None:
    args = parse_args()
    destination = args.to
    source = "spotify" if destination == "ytmusic" else "ytmusic"

    sp = sp_client.get_client() if "spotify" in (source, destination) else None
    yt = yt_client.get_client() if "ytmusic" in (source, destination) else None

    all_unmatched: list[tuple[str, Track]] = []
    failures: list[str] = []
    for playlist_ref in args.playlist:
        try:
            all_unmatched.extend(
                sync_playlist(sp, yt, source, destination, playlist_ref, args.name, args.dry_run, args.baseline)
            )
        except Exception as exc:
            failures.append(playlist_ref)
            print(f"ERROR syncing {playlist_ref}: {exc}")
        print()

    if all_unmatched:
        report_path = Path("unmatched_tracks.csv")
        with report_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["playlist", "title", "artists"])
            for playlist_name, t in all_unmatched:
                writer.writerow([playlist_name, t.title, ", ".join(t.artists)])
        print(f"{len(all_unmatched)} unmatched tracks written to {report_path.resolve()}")

    if failures:
        print(f"{len(failures)} playlist(s) failed: {', '.join(failures)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
