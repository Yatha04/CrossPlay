"""Multi-tier cross-platform song matching.

Tiers (in priority order):
  1. ISRC exact match
  2. Normalized artist+title exact match
  3. Fuzzy match (token_sort_ratio >= threshold)
  4. Duration cross-check (reject if >15s difference)
  5. Unmatched — log and skip
"""

from dataclasses import dataclass
from thefuzz import fuzz

from utils.logging import get_logger
from utils.normalize import normalize_title, normalize_artist

log = get_logger("matcher")

DEFAULT_FUZZY_THRESHOLD = 85
MAX_DURATION_DIFF_MS = 15_000  # 15 seconds


@dataclass
class MatchResult:
    matched: bool
    target_track_id: str | None = None
    method: str | None = None       # 'isrc', 'exact', 'fuzzy'
    score: float = 0.0
    reason: str | None = None       # why it failed, if it did


@dataclass
class TrackInfo:
    """Minimal track metadata used for matching."""
    track_id: str
    title: str
    artist: str
    isrc: str | None = None
    duration_ms: int | None = None


def _match_isrc(source: TrackInfo, candidates: list[TrackInfo]) -> MatchResult | None:
    """Tier 1: exact ISRC match."""
    if not source.isrc:
        return None
    for c in candidates:
        if c.isrc and c.isrc.upper() == source.isrc.upper():
            return MatchResult(
                matched=True,
                target_track_id=c.track_id,
                method="isrc",
                score=1.0,
            )
    return None


def _match_exact(source: TrackInfo, candidates: list[TrackInfo]) -> MatchResult | None:
    """Tier 2: normalized artist+title exact match."""
    src_title = normalize_title(source.title)
    src_artist = normalize_artist(source.artist)

    for c in candidates:
        if normalize_title(c.title) == src_title and normalize_artist(c.artist) == src_artist:
            return MatchResult(
                matched=True,
                target_track_id=c.track_id,
                method="exact",
                score=1.0,
            )
    return None


def _match_fuzzy(
    source: TrackInfo,
    candidates: list[TrackInfo],
    threshold: int,
) -> MatchResult | None:
    """Tier 3: fuzzy match using token_sort_ratio."""
    src_str = f"{normalize_artist(source.artist)} {normalize_title(source.title)}"

    best_score = 0
    best_candidate = None

    for c in candidates:
        cand_str = f"{normalize_artist(c.artist)} {normalize_title(c.title)}"
        score = fuzz.token_sort_ratio(src_str, cand_str)
        if score > best_score:
            best_score = score
            best_candidate = c

    if best_candidate and best_score >= threshold:
        return MatchResult(
            matched=True,
            target_track_id=best_candidate.track_id,
            method="fuzzy",
            score=best_score / 100.0,
        )
    return None


def _duration_check(source: TrackInfo, target: TrackInfo) -> bool:
    """Tier 4: return True if durations are close enough (or unknown)."""
    if source.duration_ms is None or target.duration_ms is None:
        return True  # can't check, so pass
    return abs(source.duration_ms - target.duration_ms) <= MAX_DURATION_DIFF_MS


def find_match(
    source: TrackInfo,
    candidates: list[TrackInfo],
    fuzzy_threshold: int = DEFAULT_FUZZY_THRESHOLD,
) -> MatchResult:
    """Run all matching tiers against *candidates* and return the result.

    The *candidates* list comes from searching the target platform
    (e.g. Spotify search results when source is YouTube Music).
    """
    if not candidates:
        return MatchResult(matched=False, reason="no candidates provided")

    # Tier 1: ISRC
    result = _match_isrc(source, candidates)
    if result:
        # Tier 4 validation
        target = next((c for c in candidates if c.track_id == result.target_track_id), None)
        if target and not _duration_check(source, target):
            log.warning(
                "ISRC match for '%s' failed duration check (diff > %ds)",
                source.title, MAX_DURATION_DIFF_MS // 1000,
            )
            result.score = 0.5  # demote but still accept ISRC
        log.info("ISRC match: %s → %s", source.isrc, result.target_track_id)
        return result

    # Tier 2: exact normalized
    result = _match_exact(source, candidates)
    if result:
        target = next((c for c in candidates if c.track_id == result.target_track_id), None)
        if target and not _duration_check(source, target):
            log.warning(
                "Exact match for '%s' failed duration check, demoting score",
                source.title,
            )
            result.score = 0.7
        log.info("Exact match: '%s' by '%s' → %s", source.title, source.artist, result.target_track_id)
        return result

    # Tier 3: fuzzy
    result = _match_fuzzy(source, candidates, fuzzy_threshold)
    if result:
        target = next((c for c in candidates if c.track_id == result.target_track_id), None)
        if target and not _duration_check(source, target):
            log.warning(
                "Fuzzy match for '%s' failed duration check, rejecting",
                source.title,
            )
            return MatchResult(
                matched=False,
                reason=f"fuzzy match found (score={result.score:.0%}) but duration mismatch",
            )
        log.info(
            "Fuzzy match: '%s' by '%s' → %s (score=%.0f%%)",
            source.title, source.artist, result.target_track_id, result.score * 100,
        )
        return result

    # Tier 5: no match
    log.warning("No match found for '%s' by '%s'", source.title, source.artist)
    return MatchResult(matched=False, reason="no match after all tiers")
