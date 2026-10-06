"""Text helpers shared across the pipeline."""

from __future__ import annotations

import re
import unicodedata
from typing import Iterable, List, Optional

_WS = re.compile(r"\s+")
_NON_WORD = re.compile(r"[^\w\s]+", re.UNICODE)


def clean_whitespace(value: Optional[str]) -> str:
    """Collapse all runs of whitespace to single spaces and trim."""
    if value is None:
        return ""
    return _WS.sub(" ", str(value)).strip()


def strip_accents(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def fold(value: Optional[str]) -> str:
    """Case-, accent- and whitespace-insensitive form used for comparisons."""
    if not value:
        return ""
    return clean_whitespace(strip_accents(unicodedata.normalize("NFKC", str(value))).casefold())


def fold_words(value: Optional[str]) -> str:
    """Like :func:`fold` but punctuation becomes whitespace (for phrase matching)."""
    return clean_whitespace(_NON_WORD.sub(" ", fold(value)).replace("_", " "))


def contains_phrase(haystack_folded: str, phrase: str) -> bool:
    """Whole-word phrase match. ``haystack_folded`` must already be ``fold_words``-ed."""
    needle = fold_words(phrase)
    if not needle or not haystack_folded:
        return False
    return re.search(r"(?<!\w)" + re.escape(needle) + r"(?!\w)", haystack_folded) is not None


def any_phrase(haystack_folded: str, phrases: Iterable[str]) -> Optional[str]:
    """Return the first phrase found in the haystack, or None."""
    for phrase in phrases:
        if contains_phrase(haystack_folded, phrase):
            return phrase
    return None


def as_list(value) -> List:
    """Coerce scalars / None / comma-separated strings into a list."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v not in (None, "")]
    if isinstance(value, str) and "," in value:
        return [part.strip() for part in value.split(",") if part.strip()]
    return [value]


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 1)].rstrip() + "…"


def slugify(value: str, fallback: str = "scrape") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", fold(value)).strip("-")
    return slug[:80] or fallback
