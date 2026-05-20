"""
Abbreviation Expander — loads abbreviations from data/abbreviations.json
and expands them in user queries before rewriting.

Whole-word, case-insensitive matching using regex \\b boundaries.
"""

import json
import re
from pathlib import Path

# Path to abbreviations file (relative to project root)
_ABBREV_FILE = Path(__file__).parent.parent / "data" / "abbreviations.json"

# Module-level state: loaded once at import time
_abbreviations: dict[str, str] = {}
_pattern: re.Pattern | None = None


def _load_abbreviations():
    """Load abbreviations from JSON file and compile regex pattern."""
    global _abbreviations, _pattern

    if not _ABBREV_FILE.exists():
        print(f"[ABBREVIATION] WARNING: {_ABBREV_FILE} not found, skipping expansion")
        return

    try:
        with open(_ABBREV_FILE, "r", encoding="utf-8") as f:
            _abbreviations = json.load(f)

        if _abbreviations:
            # Sort by length descending so longer abbreviations match first
            sorted_abbrevs = sorted(_abbreviations.keys(), key=len, reverse=True)
            # Build pattern with word boundaries, case-insensitive
            escaped = [re.escape(a) for a in sorted_abbrevs]
            _pattern = re.compile(r"\b(" + "|".join(escaped) + r")\b", re.IGNORECASE)

        print(f"[ABBREVIATION] Loaded {len(_abbreviations)} abbreviations")

    except Exception as e:
        print(f"[ABBREVIATION] WARNING: Failed to load {_ABBREV_FILE}: {type(e).__name__}: {e}")


# Load on module import
_load_abbreviations()


def expand_abbreviations(text: str) -> str:
    """
    Expand abbreviations in text using whole-word, case-insensitive matching.
    Returns the expanded text. If no abbreviations loaded, returns text unchanged.
    """
    if not _pattern or not _abbreviations:
        return text

    def _replace(match):
        original = match.group(0)
        # Lookup is case-insensitive: normalize to uppercase for dict key
        key = original.upper()
        # Find the matching key (abbreviations.json keys are uppercase)
        for abbrev_key in _abbreviations:
            if abbrev_key.upper() == key:
                expanded = _abbreviations[abbrev_key]
                print(f"[ABBREVIATION] Expanded: \"{original}\" -> \"{expanded}\"")
                return expanded
        return original

    return _pattern.sub(_replace, text)
