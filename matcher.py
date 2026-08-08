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

MATCH_THRESHOLD = 0.55
REQUIRED_FIELD_THRESHOLD = 0.80

_NON_ORIGINAL_PATTERNS = [
    r"\bremaster(ed)?\b",
    r"\blive\b",
    r"\bradio edit\b",
    r"\bacoustic\b",
    r"\bdemo\b",
    r"\binstrumental\b",
    r"\bkaraoke\b",
    r"\bcover\b",
    r"\bremix\b",
    r"\bmono\b",
    r"\bstereo\b",
    r"\bsped up\b",
    r"\bslowed\b",
]


def is_original(title: str) -> bool:
    """True if the title has no markers of a remaster, live recording, radio edit,
    or other non-original version."""
    t = title.lower()
    return not any(re.search(p, t) for p in _NON_ORIGINAL_PATTERNS)


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
    og: bool = False


@dataclass
class Candidate:
    title: str
    artists: list[str]
    id: str
    duration_ms: Optional[int] = None
    explicit: bool = False
    is_video: bool = False  # True when the result is a music video, not an audio-only track
    raw: dict = field(default_factory=dict)
    og: bool = False


_FEAT_IN_TITLE = re.compile(
    r"[(\[](?:feat\.?|ft\.?|with)\s+([^)\]]*)[)\]]|\b(?:feat\.?|ft\.?)\s+(.+)$",
    re.IGNORECASE,
)


def _basic_norm(text: str) -> str:
    """Lowercase and strip punctuation without removing feat/with credits,
    so featured-artist names survive for artist matching."""
    t = re.sub(r"[^\w\s]", " ", text.lower())
    return re.sub(r"\s+", " ", t).strip()


def _featured_artists(title: str) -> list[str]:
    names: list[str] = []
    for m in _FEAT_IN_TITLE.finditer(title):
        chunk = next((g for g in m.groups() if g), "")
        names.extend(re.split(r",|&|\band\b|\bx\b", chunk, flags=re.IGNORECASE))
    return [n.strip() for n in names if n.strip()]


def has_title_and_artists(track: Track, candidate: Candidate) -> bool:
    """Hard requirement: the candidate must carry the track's title and credit
    every artist on it — the main artist and all features. This keeps a feat.
    version from matching the solo original. Anything else is an automatic miss."""
    title_ok = (
        fuzz.partial_ratio(normalize(track.title), normalize(candidate.title)) / 100
        >= REQUIRED_FIELD_THRESHOLD
    )
    if not title_ok:
        return False

    required = list(track.artists) + _featured_artists(track.title)
    required_norm: list[str] = []
    for name in required:
        n = _basic_norm(name)
        if n and n not in required_norm:
            required_norm.append(n)
    if not required_norm:
        return True

    # Some results (notably YT videos) embed artists in the title instead of
    # the artist field, so the candidate title counts as a fallback.
    haystacks = [_basic_norm(a) for a in candidate.artists] + [_basic_norm(candidate.title)]
    haystacks = [h for h in haystacks if h]
    return all(
        any(fuzz.partial_ratio(artist, h) / 100 >= REQUIRED_FIELD_THRESHOLD for h in haystacks)
        for artist in required_norm
    )


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

    if candidate.og and track.og:
        score += 0.03
    elif track.og:
        score -= 0.03

    return score


def best_match(track: Track, candidates: list[Candidate]) -> Optional[Candidate]:
    candidates = [c for c in candidates if has_title_and_artists(track, c)]
    if not candidates:
        return None
    scored = [(score_candidate(track, c), c) for c in candidates]
    scored.sort(key=lambda pair: pair[0], reverse=True)
    top_score, top_candidate = scored[0]
    if top_score < MATCH_THRESHOLD:
        return None
    return top_candidate


