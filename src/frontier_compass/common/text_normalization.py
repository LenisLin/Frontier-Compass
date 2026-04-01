"""Deterministic text normalization helpers shared across the package."""

from __future__ import annotations

import re

TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")

STOPWORDS = {
    "about",
    "across",
    "after",
    "also",
    "among",
    "analysis",
    "approach",
    "based",
    "been",
    "between",
    "data",
    "from",
    "have",
    "into",
    "method",
    "methods",
    "model",
    "models",
    "paper",
    "papers",
    "preprint",
    "preprints",
    "results",
    "show",
    "shows",
    "study",
    "such",
    "than",
    "that",
    "their",
    "them",
    "then",
    "these",
    "they",
    "this",
    "those",
    "through",
    "toward",
    "towards",
    "under",
    "using",
    "with",
    "within",
}


def normalize_token(token: str) -> str:
    return token.lower().strip("-_")



def tokenize(text: str, *, min_length: int = 3) -> list[str]:
    if not text:
        return []

    tokens: list[str] = []
    for token in TOKEN_RE.findall(text.lower()):
        token = normalize_token(token)
        if len(token) < min_length or token in STOPWORDS:
            continue
        tokens.append(token)
    return tokens



def slugify(text: str) -> str:
    tokens = tokenize(text, min_length=2)
    if not tokens:
        return "item"
    return "-".join(tokens[:8])
