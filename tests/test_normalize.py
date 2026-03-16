"""Extensive tests for title and artist normalization with real-world edge cases."""

import pytest
from utils.normalize import normalize_title, normalize_artist, normalize_for_matching


# ---------------------------------------------------------------------------
# Title normalization
# ---------------------------------------------------------------------------

class TestNormalizeTitle:
    """Core title normalization."""

    def test_lowercase(self):
        assert normalize_title("Blinding Lights") == "blinding lights"

    def test_empty_string(self):
        assert normalize_title("") == ""

    def test_whitespace_collapse(self):
        assert normalize_title("  Blinding   Lights  ") == "blinding lights"

    # --- Parenthetical stripping ---

    def test_strip_feat(self):
        assert normalize_title("Bad Guy (feat. Justin Bieber)") == "bad guy"

    def test_strip_feat_no_dot(self):
        assert normalize_title("Bad Guy (feat Justin Bieber)") == "bad guy"

    def test_strip_ft(self):
        assert normalize_title("Sicko Mode (ft. Drake)") == "sicko mode"

    def test_strip_ft_no_dot(self):
        assert normalize_title("Sicko Mode (ft Drake)") == "sicko mode"

    def test_strip_with(self):
        assert normalize_title("Stay (with Justin Bieber)") == "stay"

    def test_strip_official_video(self):
        assert normalize_title("Shape of You (Official Video)") == "shape of you"

    def test_strip_official_audio(self):
        assert normalize_title("Shape of You (Official Audio)") == "shape of you"

    def test_strip_official_music_video(self):
        assert normalize_title("Shape of You (Official Music Video)") == "shape of you"

    def test_strip_music_video(self):
        assert normalize_title("Bohemian Rhapsody (Music Video)") == "bohemian rhapsody"

    def test_strip_lyric_video(self):
        assert normalize_title("Stay (Lyric Video)") == "stay"

    def test_strip_lyrics(self):
        assert normalize_title("Stay (Lyrics)") == "stay"

    def test_strip_remastered(self):
        assert normalize_title("Wonderwall (Remastered)") == "wonderwall"

    def test_strip_remastered_year(self):
        assert normalize_title("Bohemian Rhapsody (Remastered 2011)") == "bohemian rhapsody"

    def test_strip_deluxe(self):
        assert normalize_title("Album Track (Deluxe Edition)") == "album track"

    def test_strip_live(self):
        assert normalize_title("Hotel California (Live at the Forum)") == "hotel california"

    def test_strip_acoustic(self):
        assert normalize_title("Creep (Acoustic)") == "creep"

    def test_strip_bare_remix(self):
        assert normalize_title("Blinding Lights (Remix)") == "blinding lights"

    def test_strip_bonus_track(self):
        assert normalize_title("Hidden Song (Bonus Track)") == "hidden song"

    def test_strip_explicit(self):
        assert normalize_title("WAP (Explicit)") == "wap"

    def test_strip_clean(self):
        assert normalize_title("WAP (Clean)") == "wap"

    # --- Square brackets ---

    def test_strip_square_brackets(self):
        assert normalize_title("Shape of You [Official Video]") == "shape of you"

    def test_strip_hd_bracket(self):
        assert normalize_title("Bohemian Rhapsody [HD]") == "bohemian rhapsody"

    def test_strip_4k_bracket(self):
        assert normalize_title("Video [4K]") == "video"

    # --- Multiple patterns at once ---

    def test_multiple_parentheticals(self):
        result = normalize_title("Song (feat. Artist) (Official Video) [HD]")
        assert result == "song"

    def test_remastered_with_brackets(self):
        result = normalize_title("Come Together (Remastered 2009) [Official Video]")
        assert result == "come together"

    # --- Named remixes should NOT be stripped ---

    def test_named_remix_preserved(self):
        """Named remixes like '(Skrillex Remix)' should be kept."""
        result = normalize_title("Blinding Lights (Skrillex Remix)")
        assert "skrillex remix" in result

    # --- Punctuation ---

    def test_strip_punctuation(self):
        assert normalize_title("Don't Stop Me Now") == "dont stop me now"

    def test_strip_ampersand(self):
        assert normalize_title("Guns & Roses") == "guns roses"

    # --- Unicode ---

    def test_unicode_accents(self):
        assert normalize_title("Déjà Vu") == "deja vu"

    def test_unicode_spanish(self):
        assert normalize_title("Señorita") == "senorita"

    def test_unicode_umlaut(self):
        assert normalize_title("Über Alles") == "uber alles"

    def test_japanese_preserved(self):
        """Non-latin scripts should pass through (not stripped by accent removal)."""
        result = normalize_title("夜に駆ける")
        assert len(result) > 0


# ---------------------------------------------------------------------------
# Artist normalization
# ---------------------------------------------------------------------------

class TestNormalizeArtist:
    def test_lowercase(self):
        assert normalize_artist("The Weeknd") == "the weeknd"

    def test_empty_string(self):
        assert normalize_artist("") == ""

    def test_strip_topic(self):
        assert normalize_artist("The Weeknd - Topic") == "the weeknd"

    def test_strip_topic_extra_spaces(self):
        assert normalize_artist("Oasis  -  Topic") == "oasis"

    def test_strip_vevo(self):
        assert normalize_artist("TheWeekndVEVO") == "theweeknd"

    def test_strip_vevo_case_insensitive(self):
        assert normalize_artist("TheWeekndvevo") == "theweeknd"

    def test_strip_official(self):
        assert normalize_artist("The Weeknd Official") == "the weeknd"

    def test_unicode_accents(self):
        assert normalize_artist("Beyoncé") == "beyonce"

    def test_punctuation_stripped(self):
        assert normalize_artist("P!nk") == "pnk"

    def test_multiple_artists_passthrough(self):
        """We don't split multi-artist strings; just normalize them."""
        result = normalize_artist("Post Malone, The Weeknd")
        assert "post malone" in result
        assert "the weeknd" in result


# ---------------------------------------------------------------------------
# Combined matching string
# ---------------------------------------------------------------------------

class TestNormalizeForMatching:
    def test_combined_output(self):
        result = normalize_for_matching("Blinding Lights (Official Video)", "The Weeknd - Topic")
        assert result == "the weeknd blinding lights"

    def test_both_empty(self):
        assert normalize_for_matching("", "") == " "

    def test_complex_real_world(self):
        result = normalize_for_matching(
            "Bohemian Rhapsody (Remastered 2011) [Official Video]",
            "QueenVEVO"
        )
        assert result == "queen bohemian rhapsody"

    def test_feat_and_topic(self):
        result = normalize_for_matching(
            "Stay (feat. Justin Bieber) (Official Audio)",
            "The Kid LAROI - Topic"
        )
        assert result == "the kid laroi stay"
