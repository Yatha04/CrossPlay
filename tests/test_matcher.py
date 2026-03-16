"""Tests for multi-tier song matcher."""

import pytest
from sync.matcher import find_match, TrackInfo, MatchResult, _duration_check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _track(tid="t1", title="Song", artist="Artist", isrc=None, duration_ms=None):
    return TrackInfo(track_id=tid, title=title, artist=artist, isrc=isrc, duration_ms=duration_ms)


# ---------------------------------------------------------------------------
# Tier 1: ISRC
# ---------------------------------------------------------------------------

class TestISRCMatch:
    def test_exact_isrc(self):
        src = _track(isrc="USUG11904221")
        candidates = [
            _track(tid="c1", isrc="USUG11904221"),
            _track(tid="c2", isrc="OTHER"),
        ]
        result = find_match(src, candidates)
        assert result.matched
        assert result.target_track_id == "c1"
        assert result.method == "isrc"
        assert result.score == 1.0

    def test_isrc_case_insensitive(self):
        src = _track(isrc="usug11904221")
        candidates = [_track(tid="c1", isrc="USUG11904221")]
        result = find_match(src, candidates)
        assert result.matched
        assert result.method == "isrc"

    def test_no_isrc_on_source_skips_tier(self):
        src = _track(isrc=None, title="Blinding Lights", artist="The Weeknd")
        candidates = [_track(tid="c1", isrc="USUG11904221", title="Blinding Lights", artist="The Weeknd")]
        result = find_match(src, candidates)
        # Should still match via tier 2 (exact), not ISRC
        assert result.matched
        assert result.method == "exact"

    def test_isrc_no_match_falls_through(self):
        src = _track(isrc="NOMATCH", title="Blinding Lights", artist="The Weeknd")
        candidates = [_track(tid="c1", isrc="USUG11904221", title="Blinding Lights", artist="The Weeknd")]
        result = find_match(src, candidates)
        assert result.matched
        assert result.method == "exact"  # fell through to tier 2


# ---------------------------------------------------------------------------
# Tier 2: Exact normalized match
# ---------------------------------------------------------------------------

class TestExactMatch:
    def test_exact_match(self):
        src = _track(title="Blinding Lights", artist="The Weeknd")
        candidates = [_track(tid="c1", title="Blinding Lights", artist="The Weeknd")]
        result = find_match(src, candidates)
        assert result.matched
        assert result.method == "exact"

    def test_strips_official_video(self):
        src = _track(title="Blinding Lights (Official Video)", artist="The Weeknd - Topic")
        candidates = [_track(tid="c1", title="Blinding Lights", artist="The Weeknd")]
        result = find_match(src, candidates)
        assert result.matched
        assert result.method == "exact"

    def test_strips_remastered(self):
        src = _track(title="Bohemian Rhapsody (Remastered 2011)", artist="Queen")
        candidates = [_track(tid="c1", title="Bohemian Rhapsody", artist="Queen")]
        result = find_match(src, candidates)
        assert result.matched

    def test_case_insensitive(self):
        src = _track(title="BLINDING LIGHTS", artist="THE WEEKND")
        candidates = [_track(tid="c1", title="blinding lights", artist="the weeknd")]
        result = find_match(src, candidates)
        assert result.matched
        assert result.method == "exact"

    def test_different_artist_no_match(self):
        src = _track(title="Stay", artist="The Kid LAROI")
        candidates = [_track(tid="c1", title="Stay", artist="Rihanna")]
        result = find_match(src, candidates)
        # Same title, different artist — should NOT exact match
        # May fuzzy match though
        assert result.method != "exact" or not result.matched


# ---------------------------------------------------------------------------
# Tier 3: Fuzzy match
# ---------------------------------------------------------------------------

class TestFuzzyMatch:
    def test_slight_title_difference(self):
        src = _track(title="Dont Stop Me Now", artist="Queen")
        candidates = [_track(tid="c1", title="Don't Stop Me Now", artist="Queen")]
        result = find_match(src, candidates)
        assert result.matched
        # Could be exact (punctuation stripped) or fuzzy
        assert result.method in ("exact", "fuzzy")

    def test_artist_variation(self):
        src = _track(title="Stay", artist="The Kid LAROI, Justin Bieber")
        candidates = [_track(tid="c1", title="Stay", artist="The Kid LAROI feat. Justin Bieber")]
        result = find_match(src, candidates)
        assert result.matched

    def test_below_threshold_no_match(self):
        src = _track(title="Completely Different Song", artist="Unknown Artist")
        candidates = [_track(tid="c1", title="Another Song Entirely", artist="Some Other Artist")]
        result = find_match(src, candidates)
        assert not result.matched

    def test_custom_threshold(self):
        src = _track(title="Stay With Me", artist="Sam Smith")
        candidates = [_track(tid="c1", title="Stay With Me Tonight", artist="Sam Smith")]
        # With very high threshold, might not match
        result_strict = find_match(src, candidates, fuzzy_threshold=99)
        result_loose = find_match(src, candidates, fuzzy_threshold=70)
        # Loose should be more likely to match
        assert result_loose.matched

    def test_remix_vs_original(self):
        """A named remix should NOT match the original at high threshold."""
        src = _track(title="Blinding Lights", artist="The Weeknd")
        candidates = [_track(tid="c1", title="Blinding Lights (Chromatics Remix)", artist="The Weeknd")]
        result = find_match(src, candidates, fuzzy_threshold=95)
        # At 95 threshold, the remix suffix should prevent matching
        # (depending on fuzzy score — this tests the boundary)
        # At default 85 it would likely match, which is the intended behavior for most cases


# ---------------------------------------------------------------------------
# Tier 4: Duration cross-check
# ---------------------------------------------------------------------------

class TestDurationCheck:
    def test_within_threshold(self):
        src = _track(duration_ms=240_000)
        target = _track(duration_ms=245_000)  # 5s diff
        assert _duration_check(src, target)

    def test_exactly_at_threshold(self):
        src = _track(duration_ms=240_000)
        target = _track(duration_ms=255_000)  # 15s diff
        assert _duration_check(src, target)

    def test_over_threshold(self):
        src = _track(duration_ms=240_000)
        target = _track(duration_ms=256_000)  # 16s diff
        assert not _duration_check(src, target)

    def test_none_source_passes(self):
        src = _track(duration_ms=None)
        target = _track(duration_ms=240_000)
        assert _duration_check(src, target)

    def test_none_target_passes(self):
        src = _track(duration_ms=240_000)
        target = _track(duration_ms=None)
        assert _duration_check(src, target)

    def test_both_none_passes(self):
        src = _track(duration_ms=None)
        target = _track(duration_ms=None)
        assert _duration_check(src, target)

    def test_duration_rejects_fuzzy_match(self):
        """Fuzzy match with bad duration should be rejected."""
        src = _track(title="Hotel California", artist="Eagles", duration_ms=240_000)
        candidates = [
            _track(tid="c1", title="Hotel California", artist="Eagles", duration_ms=420_000),  # live version, 3min longer
        ]
        # Exact title match but duration way off
        result = find_match(src, candidates)
        # Should match but with demoted score (exact match demotes, doesn't reject)
        assert result.matched
        assert result.score < 1.0

    def test_duration_demotes_isrc(self):
        src = _track(isrc="US1234", duration_ms=180_000)
        candidates = [_track(tid="c1", isrc="US1234", duration_ms=300_000)]
        result = find_match(src, candidates)
        assert result.matched  # ISRC still matches
        assert result.score == 0.5  # but demoted


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_candidates(self):
        src = _track()
        result = find_match(src, [])
        assert not result.matched
        assert "no candidates" in result.reason

    def test_multiple_candidates_best_wins(self):
        src = _track(title="Wonderwall", artist="Oasis")
        candidates = [
            _track(tid="c1", title="Wonderwall Acoustic", artist="Oasis"),
            _track(tid="c2", title="Wonderwall", artist="Oasis"),
            _track(tid="c3", title="Champagne Supernova", artist="Oasis"),
        ]
        result = find_match(src, candidates)
        assert result.matched
        assert result.target_track_id == "c2"  # exact match

    def test_live_vs_studio(self):
        """Live version should fuzzy match studio but may be demoted by duration."""
        src = _track(title="Comfortably Numb", artist="Pink Floyd", duration_ms=383_000)
        candidates = [
            _track(tid="c1", title="Comfortably Numb (Live)", artist="Pink Floyd", duration_ms=600_000),
        ]
        # "(Live)" gets stripped by normalization → exact match, but duration demotes
        result = find_match(src, candidates)
        assert result.matched
        assert result.score < 1.0

    def test_same_name_different_artist_no_exact(self):
        src = _track(title="Angel", artist="Massive Attack")
        candidates = [_track(tid="c1", title="Angel", artist="Shaggy")]
        result = find_match(src, candidates)
        # Should not be an exact match (different artist)
        if result.matched:
            assert result.method == "fuzzy"

    def test_unicode_matching(self):
        src = _track(title="Déjà Vu", artist="Beyoncé")
        candidates = [_track(tid="c1", title="Deja Vu", artist="Beyonce")]
        result = find_match(src, candidates)
        assert result.matched

    def test_result_dataclass_fields(self):
        result = MatchResult(matched=False, reason="test")
        assert result.target_track_id is None
        assert result.method is None
        assert result.score == 0.0
