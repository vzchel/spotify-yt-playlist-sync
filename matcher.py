"""Matching and scoring logic for pairing tracks between Spotify and YouTube Music."""

import re
from dataclasses import dataclass, field
from typing import Optional

from rapidfuzz import fuzz

_NOISE_PATTERNS = [
    r"\(feat\.?[^)]*\)",
    r"\[feat\.?[^\]]*\]",
    r"\(with [^)]*\)",
    r"\bfeat\.?\s.*$",
    r"\bft\.?\s.*$",
    r"\(remaster(ed)?[^)]*\)",
    r"\[remaster(ed)?[^\]]*\]",
    r"\(live[^)]*\)",
    r"\[live[^\]]*\]",
    r"\(explicit\)",
    r"\(clean\)",
    r"\(official[^)]*\)",
    r"\(audio\)",
    r"\(lyrics?\)",
    r"\(mono\)",
    r"\(stereo\)",
    r"-\s*single( version)?$",
    r"-\s*radio edit$",
]

MATCH_THRESHOLD = 0.60


def normalize(text: str) -> str:
    t = text.lower().strip()
    for pattern in _NOISE_PATTERNS:
        t = re.sub(pattern, "", t, flags=re.IGNORECASE)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@dataclass
class Track:
    title: str
    artists: list[str]
    duration_ms: Optional[int] = None
    explicit: bool = False
    source_id: Optional[str] = None


@dataclass
class Candidate:
    title: str
    artists: list[str]
    id: str
    duration_ms: Optional[int] = None
    explicit: bool = False
    is_video: bool = False  # True when the result is a music video, not an audio-only track
    raw: dict = field(default_factory=dict)


def score_candidate(track: Track, candidate: Candidate) -> float:
    title_score = fuzz.token_sort_ratio(normalize(track.title), normalize(candidate.title)) / 100

    artist_score = fuzz.token_sort_ratio(
        normalize(" ".join(track.artists)), normalize(" ".join(candidate.artists))
    ) / 100
    for ta in track.artists:
        for ca in candidate.artists:
            single = fuzz.token_sort_ratio(normalize(ta), normalize(ca)) / 100
            artist_score = max(artist_score, single)

    score = 0.55 * title_score + 0.35 * artist_score

    if track.duration_ms and candidate.duration_ms:
        diff_seconds = abs(track.duration_ms - candidate.duration_ms) / 1000
        if diff_seconds <= 3:
            score += 0.08
        elif diff_seconds <= 8:
            score += 0.03
        elif diff_seconds > 25:
            score -= 0.15

    if candidate.is_video:
        score -= 0.25  # audio-only tracks are strongly preferred over music videos

    if candidate.explicit:
        score += 0.04  # explicit versions are preferred when available
    elif track.explicit:
        score -= 0.02  # source was explicit but this candidate isn't

    return score


def best_match(track: Track, candidates: list[Candidate]) -> Optional[Candidate]:
    if not candidates:
        return None
    scored = [(score_candidate(track, c), c) for c in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top_candidate = scored[0]
    if top_score < MATCH_THRESHOLD:
        return None
    return top_candidate


