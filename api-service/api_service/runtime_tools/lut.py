"""Shared FreeSurfer Color LUT loading and label resolution.

This module is the single source of truth for LUT parsing. Both the
``freesurfer_lut`` tool (partial search) and ``gui_focus_label`` (centroid
lookup) consume it.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from difflib import SequenceMatcher

from backend_common.settings import ROOT_DIR

logger = logging.getLogger(__name__)

# Default path for the FreeSurfer LUT.
_DEFAULT_LUT_PATH = ROOT_DIR / "config" / "FreeSurferColorLUT.txt"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


@dataclass(frozen=True)
class LutRecord:
    label_id: int
    name: str
    comment: str
    name_text: str
    comment_text: str
    name_tokens: frozenset[str]
    search_tokens: frozenset[str]
    name_compact: str
    search_compact: str


@dataclass(frozen=True)
class LutSearchResult:
    label_id: int
    name: str
    comment: str
    score: int


@dataclass(frozen=True)
class _LutData:
    mtime_ns: int
    by_id: dict[int, LutRecord]
    by_name: dict[str, LutRecord]
    records: list[LutRecord]


_lut_cache: dict[str, _LutData] = {}


def _tokens(text: str) -> tuple[str, ...]:
    """Return lowercase alphanumeric search tokens from text."""
    return tuple(_TOKEN_RE.findall(text.casefold()))


def _normalized_text(text: str) -> str:
    """Normalize text to space-separated lowercase search tokens."""
    return " ".join(_tokens(text))


def _compact(text: str) -> str:
    """Normalize text to a token-only string without separators."""
    return "".join(_tokens(text))


def _build_record(label_id: int, name: str, comment: str) -> LutRecord:
    """Precompute searchable forms for one LUT label row."""
    name_text = _normalized_text(name)
    comment_text = _normalized_text(comment)
    return LutRecord(
        label_id=label_id,
        name=name,
        comment=comment,
        name_text=name_text,
        comment_text=comment_text,
        name_tokens=frozenset(_tokens(name)),
        search_tokens=frozenset(_tokens(f"{name} {comment}")),
        name_compact=_compact(name),
        search_compact=_compact(f"{name} {comment}"),
    )


def _parse_row(line: str) -> LutRecord | None:
    """Parse one FreeSurferColorLUT line, skipping comments and malformed rows."""
    raw = line.strip()
    if not raw or raw.startswith("#"):
        return None

    data_text, _, comment = raw.partition("#")
    parts = data_text.split()
    if len(parts) < 2:
        return None

    try:
        label_id = int(parts[0])
    except ValueError:
        return None

    return _build_record(label_id, parts[1], comment.strip())


def _load_lut(lut_path: str | None = None) -> _LutData:
    """Load and cache the LUT file by path and modification time."""
    path = os.path.abspath(lut_path or _DEFAULT_LUT_PATH)
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError as e:
        logger.warning(f"Could not stat FreeSurferColorLUT.txt at {path}: {e}")
        return _LutData(mtime_ns=-1, by_id={}, by_name={}, records=[])

    cached = _lut_cache.get(path)
    if cached and cached.mtime_ns == mtime_ns:
        return cached

    by_id: dict[int, LutRecord] = {}
    by_name: dict[str, LutRecord] = {}
    records: list[LutRecord] = []

    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                record = _parse_row(line)
                if record is None:
                    continue
                by_id[record.label_id] = record
                by_name[record.name_text] = record
                records.append(record)
    except Exception as e:
        logger.warning(f"Could not load FreeSurferColorLUT.txt from {path}: {e}")

    data = _LutData(mtime_ns=mtime_ns, by_id=by_id, by_name=by_name, records=records)
    _lut_cache[path] = data
    return data


def _fuzzy_token_match(
    query_tokens: tuple[str, ...], candidate_tokens: frozenset[str]
) -> bool:
    """Return whether every query token closely matches a candidate token."""
    if not query_tokens or not candidate_tokens:
        return False
    fuzzy_candidates = [candidate for candidate in candidate_tokens if len(candidate) >= 4]
    if not fuzzy_candidates:
        return False
    for query_token in query_tokens:
        if len(query_token) < 4:
            return False
        best_ratio = max(
            SequenceMatcher(None, query_token, candidate).ratio()
            for candidate in fuzzy_candidates
        )
        if best_ratio < 0.86:
            return False
    return True


def _prefix_token_match(
    query_tokens: tuple[str, ...], candidate_tokens: frozenset[str]
) -> bool:
    """Return whether each query token prefixes at least one candidate token."""
    if not query_tokens:
        return False
    return all(
        any(candidate.startswith(query_token) for candidate in candidate_tokens)
        for query_token in query_tokens
    )


def _rank_record(record: LutRecord, query: str) -> int | None:
    """Return the match rank for a LUT record, or ``None`` if it does not match."""
    query_text = _normalized_text(query)
    if not query_text:
        return None

    query_tokens = _tokens(query)
    query_token_set = frozenset(query_tokens)
    query_compact = _compact(query)

    if record.name_text == query_text:
        return 0
    if record.name_compact == query_compact:
        return 1
    if query_text and query_text in record.name_text:
        extra_name_tokens = len(record.name_text.split()) - len(query_tokens)
        if record.name_text.startswith(query_text) or (
            record.name_text.endswith(query_text) and extra_name_tokens <= 1
        ):
            return 2
    if query_text and query_text in record.comment_text:
        return 3
    if query_text and query_text in record.name_text:
        return 4
    if query_token_set.issubset(record.name_tokens):
        return 5
    if query_token_set.issubset(record.search_tokens):
        return 6
    if _prefix_token_match(query_tokens, record.name_tokens):
        return 7
    if _prefix_token_match(query_tokens, record.search_tokens):
        return 8
    if query_compact and query_compact in record.search_compact:
        return 9
    if _fuzzy_token_match(query_tokens, record.name_tokens):
        return 10
    if _fuzzy_token_match(query_tokens, record.search_tokens):
        return 11
    return None


# ── Public API ───────────────────────────────────────────────────────────────

def get_by_id(label_id: int, lut_path: str | None = None) -> str | None:
    """Return the label name for a numeric ID, or ``None``."""
    record = _load_lut(lut_path).by_id.get(label_id)
    return record.name if record else None


def search_lut(
    query: str,
    *,
    limit: int = 50,
    lut_path: str | None = None,
    allowed_label_ids: set[int] | None = None,
) -> tuple[list[LutSearchResult], int]:
    """Ranked search across label names and annotated LUT comments."""
    data = _load_lut(lut_path)
    scored: list[tuple[int, int, LutRecord]] = []
    for record in data.records:
        if allowed_label_ids is not None and record.label_id not in allowed_label_ids:
            continue
        score = _rank_record(record, query)
        if score is not None:
            scored.append((score, record.label_id, record))

    scored.sort(key=lambda item: (item[0], item[1], item[2].name))
    matches = [
        LutSearchResult(
            label_id=record.label_id,
            name=record.name,
            comment=record.comment,
            score=score,
        )
        for score, _, record in scored[:limit]
    ]
    return matches, len(scored)


def resolve_label(label_query: str, lut_path: str | None = None) -> tuple[int | None, str | None]:
    """Resolve a free-form label query to ``(label_id, label_name)``.

    Matching priority:
      1. Numeric ID (e.g. ``"17"``)
      2. Ranked name/comment match using the same matcher as
         ``freesurfer_lut``

    Returns ``(None, None)`` if nothing matches.
    """
    data = _load_lut(lut_path)

    # Numeric?
    try:
        lid = int(label_query)
        record = data.by_id.get(lid)
        return (lid, record.name) if record else (None, None)
    except ValueError:
        pass

    exact_match = data.by_name.get(_normalized_text(label_query))
    if exact_match:
        return exact_match.label_id, exact_match.name

    matches, _ = search_lut(label_query, limit=1, lut_path=lut_path)
    if matches:
        match = matches[0]
        return match.label_id, match.name

    return None, None
