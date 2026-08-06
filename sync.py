#!/usr/bin/env python3

"""Spotify <-> YouTube Music playlist converter.

Usage:
    python sync.py --to ytmusic  --playlist <spotify_playlist_url_or_id> [--name "My Playlist"] [--dry-run]
    python sync.py --to spotify  --playlist <ytmusic_playlist_url_or_id>  [--name "My Playlist"] [--dry-run]
"""

import argparse
import csv
from pathlib import Path

import spotify_client as sp_client
import ytmusic_client as yt_client
from matcher import Track, best_match


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Convert a playlist between Spotify and YouTube Music.")
    parser.add_argument("--to", required=True, choices=["spotify", "ytmusic"], help="Destination service.")
    parser.add_argument("--playlist", required=True, help="Source playlist URL or ID.")
    parser.add_argument("--name", default=None, help="Name for the new playlist (defaults to the source playlist's name).")
    parser.add_argument("--dry-run", action="store_true", help="Match tracks but don't create or modify any playlist.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    destination = args.to
    source = "spotify" if destination == "ytmusic" else "ytmusic"

    sp = sp_client.get_client() if "spotify" in (source, destination) else None
    yt = yt_client.get_client() if "ytmusic" in (source, destination) else None

    print(f"Reading source playlist from {source}")
    if source == "spotify":
        playlist_id = sp_client.extract_playlist_id(args.playlist)
        playlist_name = sp_client.get_playlist_name(sp, playlist_id)
        tracks = sp_client.read_playlist_tracks(sp, playlist_id)
    else:
        playlist_id = yt_client.extract_playlist_id(args.playlist)
        playlist_name = yt_client.get_playlist_name(yt, playlist_id)
        tracks = yt_client.read_playlist_tracks(yt, playlist_id)

    print(f"Found {len(tracks)} tracks in '{playlist_name}'.")
    if not tracks:
        print("Nothing to convert.")
        return

    matched_ids: list[str] = []
    unmatched: list[Track] = []

    for i, track in enumerate(tracks, 1):
        label = f"{', '.join(track.artists)} - {track.title}"
        candidates = yt_client.search_track(yt, track) if destination == "ytmusic" else sp_client.search_track(sp, track)
        match = best_match(track, candidates)

        if match:
            matched_ids.append(match.id)
            tag = " [video]" if match.is_video else ""
            exp = " [explicit]" if match.explicit else ""
            print(f"  [{i}/{len(tracks)}] OK   {label}  ->  {', '.join(match.artists)} - {match.title}{exp}{tag}")
        else:
            unmatched.append(track)
            print(f"  [{i}/{len(tracks)}] MISS {label}")

    print()
    print(f"Matched {len(matched_ids)}/{len(tracks)} tracks.")

    if unmatched:
        report_path = Path("unmatched_tracks.csv")
        with report_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["title", "artists"])
            for t in unmatched:
                writer.writerow([t.title, ", ".join(t.artists)])
        print(f"{len(unmatched)} unmatched tracks written to {report_path.resolve()}")

    if args.dry_run:
        print("Dry run: no playlist was created.")
        return

    if not matched_ids:
        print("No matches found; skipping playlist creation.")
        return

    new_name = args.name or playlist_name
    description = f"Converted from {source} playlist '{playlist_name}' via github.com/vzchel/spotify-yt-playlist-sync."

    print(f"Creating '{new_name}' on {destination}")
    if destination == "ytmusic":
        new_playlist_id = yt_client.create_playlist(yt, new_name, description)
        yt_client.add_tracks(yt, new_playlist_id, matched_ids)
        print(f"Done: https://music.youtube.com/playlist?list={new_playlist_id}")
    else:
        new_playlist_id = sp_client.create_playlist(sp, new_name, description)
        sp_client.add_tracks(sp, new_playlist_id, matched_ids)
        print(f"Done: https://open.spotify.com/playlist/{new_playlist_id}")


if __name__ == "__main__":
    main()
