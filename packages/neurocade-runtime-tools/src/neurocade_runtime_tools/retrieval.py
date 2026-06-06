"""Search installed NeuroCade tool rows with lightweight lexical ranking."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9]+")
_IGNORED_TOKENS = {
    "a",
    "an",
    "and",
    "can",
    "command",
    "could",
    "me",
    "my",
    "need",
    "or",
    "please",
    "program",
    "the",
    "to",
    "tool",
    "want",
    "with",
    "would",
    "you",
}
_TOKEN_SYNONYMS = {
    "anat": {"anatomical", "anatomy"},
    "anatomical": {"anat", "anatomy"},
    "bold": {"fmri", "functional"},
    "dcm": {"dicom"},
    "dcm2niix": {"dicom", "nifti", "convert", "conversion"},
    "dicom": {"dcm", "nifti", "convert", "conversion"},
    "dim": {"dimension", "dimensions", "size"},
    "dimensions": {"dim", "size"},
    "fmri": {"bold", "functional"},
    "functional": {"bold", "fmri"},
    "header": {"metadata", "info", "information"},
    "info": {"header", "metadata", "information"},
    "metadata": {"header", "info", "information"},
    "mgz": {"mri", "volume", "image"},
    "nifti": {"dicom", "convert", "conversion"},
    "recon": {"reconstruction"},
    "registration": {"register", "transform"},
    "res": {"resolution"},
    "resolution": {"res"},
    "segmentation": {"segment"},
    "surface": {"surf"},
    "surf": {"surface"},
    "transform": {"registration", "register"},
    "vol": {"volume"},
    "volume": {"vol"},
}


def tokenize(text: str) -> list[str]:
    """Tokenize query or document text for installed-tool search.

    Parameters
    ----------
    text : str
        Text to normalize into searchable tokens.

    Returns
    -------
    list[str]
        Lowercase tokens with a small neuroimaging synonym expansion.
    """
    explicit_short_flags = {
        match.group(1).lower()
        for match in re.finditer(r"(?<!\w)-{1,2}([A-Za-z])\b", text)
    }
    normalized = text.replace("_", " ").replace("-", " ")
    normalized = re.sub(r"([a-z])([A-Z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1 \2", normalized)
    normalized = re.sub(r"([A-Za-z])([0-9])", r"\1 \2", normalized)
    normalized = re.sub(r"([0-9])([A-Za-z])", r"\1 \2", normalized)
    base_tokens = [
        token.lower()
        for token in _TOKEN_PATTERN.findall(normalized)
        if token.lower() not in _IGNORED_TOKENS
        and (len(token) > 1 or token.lower() in explicit_short_flags)
    ]
    expanded: list[str] = []
    for token in base_tokens:
        expanded.append(token)
        expanded.extend(sorted(_TOKEN_SYNONYMS.get(token, ())))
    return expanded


def load_jsonl_records(path: Path) -> list[dict[str, Any]]:
    """Load installed tool records from a JSONL file.

    Parameters
    ----------
    path : Path
        JSONL file containing one installed tool record per line.

    Returns
    -------
    list[dict[str, Any]]
        Parsed non-empty JSONL rows.
    """
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _record_text(record: dict[str, Any]) -> str:
    """Flatten an installed tool record into searchable text.

    Parameters
    ----------
    record : dict[str, Any]
        Installed tool record from the local index.

    Returns
    -------
    str
        Searchable text assembled from stable record fields.
    """
    parts = [
        record.get("name", ""),
        record.get("toolbox", ""),
        record.get("app", ""),
        record.get("container_command", ""),
        record.get("description", ""),
        record.get("synopsis", ""),
        record.get("searchable_text", ""),
        record.get("raw_help_text", ""),
        _structured_text(record.get("arguments")),
        _structured_text(record.get("outputs")),
    ]
    parts.extend(str(alias) for alias in record.get("aliases", []) or [])
    parts.extend(str(category) for category in record.get("categories", []) or [])
    return " ".join(str(part) for part in parts if part)


def _structured_text(value: Any) -> str:
    parts: list[str] = []
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                parts.append(" ".join(str(part) for part in item.values() if part))
            elif item:
                parts.append(str(item))
    return " ".join(parts)


def _score(query: str, record: dict[str, Any]) -> float:
    """Score one installed tool record for a query.

    Parameters
    ----------
    query : str
        User query.
    record : dict[str, Any]
        Installed tool record.

    Returns
    -------
    float
        Lexical relevance score.
    """
    query_tokens = set(tokenize(query))
    if not query_tokens:
        return 0.0
    text_tokens = set(tokenize(_record_text(record)))
    if not text_tokens:
        return 0.0
    overlap = query_tokens & text_tokens
    recall = len(overlap) / len(query_tokens)
    name_tokens = set(tokenize(str(record.get("name") or "")))
    command_tokens = set(tokenize(str(record.get("container_command") or "")))
    name_boost = 1.0 if query_tokens & (name_tokens | command_tokens) else 0.0
    exact_name_boost = 1.0 if str(record.get("name") or "").lower() in query.lower() else 0.0
    return recall + name_boost + exact_name_boost


def hybrid_rank(query: str, records: list[dict[str, Any]], n_results: int = 5) -> list[dict[str, Any]]:
    """Rank installed tool records for a query.

    Parameters
    ----------
    query : str
        User search query.
    records : list[dict[str, Any]]
        Installed tool records.
    n_results : int
        Maximum number of records to return.

    Returns
    -------
    list[dict[str, Any]]
        Ranked records with a ``score`` field added.
    """
    ranked: list[dict[str, Any]] = []
    for record in records:
        row = dict(record)
        row["score"] = _score(query, record)
        ranked.append(row)
    ranked.sort(key=lambda row: (float(row.get("score") or 0.0), str(row.get("name") or "")), reverse=True)
    return ranked[:n_results]
