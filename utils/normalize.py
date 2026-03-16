"""Title and artist string normalization for cross-platform song matching."""

import re
import unicodedata

# Patterns stripped from titles before comparison (order doesn't matter).
_TITLE_STRIP_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\(feat\.?\s.*?\)",       # (feat. Artist) / (feat Artist)
        r"\(ft\.?\s.*?\)",         # (ft. Artist)
        r"\(with\s.*?\)",          # (with Artist)
        r"\(official\s.*?\)",      # (Official Video), (Official Audio)
        r"\(music\s*video\)",      # (Music Video)
        r"\(lyric.*?\)",           # (Lyric Video), (Lyrics)
        r"\(remaster.*?\)",        # (Remastered), (Remastered 2024)
        r"\(deluxe.*?\)",          # (Deluxe Edition)
        r"\(live.*?\)",            # (Live at ...)
        r"\(acoustic.*?\)",        # (Acoustic)
        r"\(remix\)",              # bare (Remix) — NOT named remixes like (Skrillex Remix)
        r"\(bonus\s*track\)",      # (Bonus Track)
        r"\(explicit\)",           # (Explicit)
        r"\(clean\)",              # (Clean)
        r"\[.*?\]",                # [Official Video], [HD], [4K], etc.
    ]
]

# Patterns stripped from artist names.
_ARTIST_STRIP_PATTERNS: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\s*-\s*topic$",         # "Artist Name - Topic" (YouTube auto-generated)
        r"\s*vevo$",              # "ArtistVEVO"
        r"\s*official$",          # "Artist Official"
    ]
]

# Normalize unicode characters (accented → base form).
def _strip_accents(text: str) -> str:
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalize_title(title: str) -> str:
    """Normalize a song title for comparison.

    Lowercase, strip parentheticals/brackets, collapse whitespace, remove punctuation.
    """
    if not title:
        return ""

    text = title.lower()
    text = _strip_accents(text)

    for pattern in _TITLE_STRIP_PATTERNS:
        text = pattern.sub("", text)

    # Remove non-alphanumeric characters (keep spaces)
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_artist(artist: str) -> str:
    """Normalize an artist name for comparison.

    Lowercase, strip Topic/VEVO suffixes, collapse whitespace, remove punctuation.
    """
    if not artist:
        return ""

    text = artist.lower()
    text = _strip_accents(text)

    for pattern in _ARTIST_STRIP_PATTERNS:
        text = pattern.sub("", text)

    # Remove non-alphanumeric characters (keep spaces)
    text = re.sub(r"[^\w\s]", "", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()

    return text


def normalize_for_matching(title: str, artist: str) -> str:
    """Combine normalized title and artist into a single comparison string."""
    return f"{normalize_artist(artist)} {normalize_title(title)}"
