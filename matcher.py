"""Matching and scoring logic for pairing tracks between Spotify and YouTube Music."""

import re
import unicodedata
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Optional

from rapidfuzz import fuzz


MATCH_THRESHOLD = 0.50

TITLE_MATCH_THRESHOLD = 0.85

REQUIRED_FIELD_THRESHOLD = 0.65

EXTRA_ARTIST_THRESHOLD = 0.65

FULL_MATCH_TITLE = 0.90
FULL_MATCH_ARTIST = 0.85

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

_VERSION_META = re.compile(
    r"[(\[][^)\]]*\b(?:remix|feat\.?|ft\.?|with|version|edit|mix)\b[^)\]]*[)\]]",
    re.IGNORECASE,
)


@lru_cache(maxsize=8192)
def normalize(text: str) -> str:
    """Lowercased title with noise stripped, for title-to-title comparison."""
    t = text.lower().strip()
    for pattern in _NOISE_PATTERNS:
        t = re.sub(pattern, "", t, flags=re.IGNORECASE)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


@lru_cache(maxsize=8192)
def _basic_norm(text: str) -> str:
    """Lowercase, strip accents and punctuation without removing feat/with
    credits, so featured-artist names survive for artist matching."""
    t = unicodedata.normalize("NFKD", text.lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


@lru_cache(maxsize=8192)
def bare_title(title: str) -> str:
    """Title with feat./with/remix/version metadata removed, for retry queries."""
    return normalize(_VERSION_META.sub(" ", title))


def _similarity(a: str, b: str) -> float:
    """Containment-style fuzzy similarity: 1.0 when the shorter string appears
    (nearly) verbatim inside the longer one."""
    return fuzz.partial_ratio(a, b) / 100


def _matches_any(needle: str, haystacks: list[str], threshold: float) -> bool:
    return any(_similarity(needle, h) >= threshold for h in haystacks)

_HARD_VERSION_PATTERNS = [
    r"\blive\b",
    r"\bacoustic\b",
    r"\bdemo\b",
    r"\binstrumental\b",
    r"\bkaraoke\b",
    r"\bcover\b",
    r"\bremix\b",
    r"\bsped up\b",
    r"\bslowed\b",
    r"\ba\s?cappella\b",
    r"\bacapella\b",
    r"\bunplugged\b",
    r"\bbootleg\b",
    r"\bmashup\b",
    r"\bnightcore\b",
    r"\breverb\b",
    r"\b8d\b",
]


_SOFT_VERSION_PATTERNS = [
    r"\bremaster(ed)?\b",
    r"\bradio edit\b",
    r"\bmono\b",
    r"\bstereo\b",
    r"\bextended\b",
]

_HARD_VERSION_RE = [re.compile(p) for p in _HARD_VERSION_PATTERNS]
_NON_ORIGINAL_RE = [re.compile(p) for p in _HARD_VERSION_PATTERNS + _SOFT_VERSION_PATTERNS]


def has_hard_version_marker(title: str) -> bool:
    t = title.lower()
    return any(p.search(t) for p in _HARD_VERSION_RE)


def is_original(title: str) -> bool:
    """True if the title has no markers of a remaster, live recording, radio
    edit, or other non-original version."""
    t = title.lower()
    return not any(p.search(t) for p in _NON_ORIGINAL_RE)

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

_PLACEHOLDER_CREDITS = frozenset({"", "unknown", "various artists"})


def _featured_artists(title: str) -> list[str]:
    """Artist names parsed from feat./ft./with credits inside a title."""
    names: list[str] = []
    for m in _FEAT_IN_TITLE.finditer(title):
        chunk = next((g for g in m.groups() if g), "")
        names.extend(re.split(r",|&|\band\b|\bx\b", chunk, flags=re.IGNORECASE))
    return [n.strip() for n in names if n.strip()]


def _required_artists(track: Track) -> list[str]:
    """Every artist the track credits — the artist list plus features parsed
    from the title — normalized, deduped, main artist first."""
    required: list[str] = []
    for name in list(track.artists) + _featured_artists(track.title):
        n = _basic_norm(name)
        if n and n not in required:
            required.append(n)
    return required


def _credited_artists(names: list[str]) -> list[str]:
    """Normalized artist credits with placeholder entries dropped."""
    return [n for n in (_basic_norm(a) for a in names) if n not in _PLACEHOLDER_CREDITS]


def has_title_and_artists(track: Track, candidate: Candidate) -> bool:
    """Hard requirements, all of which must hold or the candidate is an automatic miss:
    - the candidate carries the track's title;
    - if the source title has no hard version markers (remix/live/a cappella/...),
      the candidate title must not have any either;
    - the candidate credits every artist on the track (main + features);
    - the candidate credits no artist the track doesn't have (rejects covers,
      reuploads, and feat. versions of a solo track)."""
    return (
        _titles_match(track.title, candidate.title)
        and _versions_compatible(track.title, candidate.title)
        and _artists_match(track, candidate)
    )


def _titles_match(track_title: str, candidate_title: str) -> bool:
    return _similarity(normalize(track_title), normalize(candidate_title)) >= TITLE_MATCH_THRESHOLD


def _versions_compatible(track_title: str, candidate_title: str) -> bool:
    """A live/remix/cover/... candidate never matches an original source.
    Alternate masters (remaster, mono/stereo, radio edit) stay matchable and
    are only nudged by og scoring."""
    return has_hard_version_marker(track_title) or not has_hard_version_marker(candidate_title)


def _artists_match(track: Track, candidate: Candidate) -> bool:
    required = _required_artists(track)
    if not required:
        return True

    main, features = required[0], required[1:]
    artist_fields = _credited_artists(candidate.artists)
    candidate_title = _basic_norm(candidate.title)

    if not _matches_any(main, artist_fields or [candidate_title], REQUIRED_FIELD_THRESHOLD):
        return False

    feature_haystacks = artist_fields + [candidate_title]
    if not all(_matches_any(f, feature_haystacks, REQUIRED_FIELD_THRESHOLD) for f in features):
        return False

    credited = _credited_artists(candidate.artists) + _credited_artists(
        _featured_artists(candidate.title)
    )
    return all(_matches_any(name, required, EXTRA_ARTIST_THRESHOLD) for name in credited)

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
    """Highest-scoring candidate that passes the gate and clears MATCH_THRESHOLD."""
    survivors = [c for c in candidates if has_title_and_artists(track, c)]
    if not survivors:
        return None
    score, candidate = max(
        ((score_candidate(track, c), c) for c in survivors), key=lambda pair: pair[0]
    )
    return candidate if score >= MATCH_THRESHOLD else None


def is_full_match(track: Track, candidate: Candidate) -> bool:
    """True when a first search result is unambiguously the right track: it
    passes every hard requirement, the whole titles line up (not just
    containment), and the main artist is a near-exact credit."""
    if not has_title_and_artists(track, candidate):
        return False
    whole_title = fuzz.token_sort_ratio(normalize(track.title), normalize(candidate.title)) / 100
    if whole_title < FULL_MATCH_TITLE:
        return False
    if not track.artists:
        return True
    main = _basic_norm(track.artists[0])
    haystacks = _credited_artists(candidate.artists) or [_basic_norm(candidate.title)]
    return _matches_any(main, haystacks, FULL_MATCH_ARTIST)


def find_match(
    track: Track, search: Callable[[str], list[Candidate]]
) -> Optional[Candidate]:
    """Staged search pipeline. `search` maps a query string to Candidates.

    1. exact main artist + raw title    -> accept first result if full match
    2. normalized title + main artist   -> accept first result if full match
    3. feat./with/remix meta stripped   -> accept first result if full match
    4. title + all artists              -> score every candidate seen so far,
                                           accept best if it clears MATCH_THRESHOLD

    Duplicate queries (common once normalization is a no-op) are skipped.
    """
    main = track.artists[0] if track.artists else ""
    queries = [
        f"{main} {track.title}".strip(),
        f"{_basic_norm(main)} {_basic_norm(track.title)}".strip(),
        f"{_basic_norm(main)} {bare_title(track.title)}".strip(),
        " ".join([track.title, *track.artists]).strip(),
    ]

    seen_queries: set[str] = set()
    seen_ids: set[str] = set()
    pool: list[Candidate] = []
    last_stage = len(queries) - 1

    for stage, query in enumerate(queries):
        if not query or query in seen_queries:
            continue
        seen_queries.add(query)

        results = search(query)
        for c in results:
            if c.id not in seen_ids:
                seen_ids.add(c.id)
                pool.append(c)

        if stage < last_stage and results and is_full_match(track, results[0]):
            return results[0]

    return best_match(track, pool)
