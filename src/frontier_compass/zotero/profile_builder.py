"""Build lightweight deterministic interest profiles from Zotero-like items."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping

from frontier_compass.common.text_normalization import tokenize
from frontier_compass.storage.schema import (
    PROFILE_SOURCE_LIVE_ZOTERO_DB,
    PROFILE_SOURCE_ZOTERO,
    PROFILE_SOURCE_ZOTERO_EXPORT,
    ProfileBasis,
    UserInterestProfile,
    ZoteroRetrievalHint,
)
from frontier_compass.zotero.export_loader import ZoteroExportItem, load_csl_json_export
from frontier_compass.zotero.sqlite_loader import load_sqlite_library


BIOMEDICAL_SIGNAL_PHRASES = (
    "bioinformatics",
    "genomics",
    "transcriptomics",
    "proteomics",
    "multi-omics",
    "single-cell",
    "spatial transcriptomics",
    "cell atlas",
    "perturbation",
    "pathology",
    "histopathology",
    "histology",
    "whole-slide",
    "digital pathology",
    "biomedical imaging",
    "medical imaging",
    "radiology",
    "microscopy",
    "clinical genomics",
    "precision medicine",
    "tumor microenvironment",
    "drug discovery",
    "protein structure",
    "rna sequencing",
    "gene expression",
    "cohort analysis",
)
BIOMEDICAL_SIGNAL_TOKENS = frozenset(
    {
        "atlas",
        "biomedical",
        "bioinformatics",
        "biomolecular",
        "cellular",
        "clinical",
        "cohort",
        "disease",
        "gene",
        "genomic",
        "genomics",
        "histology",
        "histopathology",
        "medical",
        "microscopy",
        "molecular",
        "pathology",
        "perturbation",
        "precision",
        "protein",
        "proteomics",
        "radiology",
        "single-cell",
        "spatial",
        "tissue",
        "transcriptomic",
        "transcriptomics",
        "tumor",
    }
)
GENERIC_NOISE_TOKENS = frozenset(
    {
        "algorithm",
        "algorithms",
        "architecture",
        "architectures",
        "benchmark",
        "benchmarks",
        "dataset",
        "datasets",
        "framework",
        "frameworks",
        "general",
        "graph",
        "language",
        "learning",
        "machine",
        "network",
        "networks",
        "pretraining",
        "representation",
        "representations",
        "survey",
        "system",
        "systems",
    }
)
ZOTERO_RETRIEVAL_PROFILE_LABEL = "zotero-biomedical-augmentation-v1"
RETRIEVAL_THEME_TERMS = {
    "omics": (
        "spatial transcriptomics",
        "single-cell atlas",
        "single-cell",
        "cell atlas",
        "genomics",
        "transcriptomics",
        "multi-omics",
        "perturbation",
        "gene expression",
    ),
    "pathology-imaging": (
        "digital pathology",
        "histopathology",
        "whole-slide imaging",
        "whole-slide",
        "pathology",
        "histology",
        "microscopy",
        "medical imaging",
        "biomedical imaging",
        "radiology",
        "tumor microenvironment",
    ),
    "protein-discovery": (
        "protein structure",
        "drug discovery",
        "protein therapeutics",
        "biomolecular modeling",
        "proteomics",
        "protein",
        "biomolecular",
    ),
    "clinical": (
        "clinical genomics",
        "precision medicine",
        "cohort analysis",
        "clinical",
    ),
}
RETRIEVAL_PROFILE_BLUEPRINTS = (
    (
        "zotero-omics-pathology",
        ("omics", "pathology-imaging"),
        "Pairs repeated omics and pathology/imaging signals from the Zotero biomedical subset.",
    ),
    (
        "zotero-protein-discovery",
        ("protein-discovery",),
        "Adds a compact biomolecular discovery profile derived from repeated Zotero protein/drug signals.",
    ),
    (
        "zotero-clinical-translation",
        ("clinical",),
        "Adds a conservative clinical translation profile derived from repeated Zotero clinical signals.",
    ),
)


@dataclass(slots=True, frozen=True)
class DerivedZoteroSignals:
    parsed_item_count: int
    used_item_count: int
    keywords: tuple[str, ...]
    concepts: tuple[str, ...]
    seed_titles: tuple[str, ...]
    retrieval_hints: tuple[ZoteroRetrievalHint, ...]


class ZoteroProfileBuilder:
    def __init__(
        self,
        *,
        max_keywords: int = 8,
        max_concepts: int = 6,
        max_seed_titles: int = 5,
        max_retrieval_hints: int = 2,
        max_retrieval_terms: int = 2,
        min_keyword_length: int = 4,
    ) -> None:
        self.max_keywords = max_keywords
        self.max_concepts = max_concepts
        self.max_seed_titles = max_seed_titles
        self.max_retrieval_hints = max_retrieval_hints
        self.max_retrieval_terms = max_retrieval_terms
        self.min_keyword_length = min_keyword_length

    def build(self, items: Iterable[Mapping[str, Any] | ZoteroExportItem]) -> UserInterestProfile:
        signals = self.derive_signals(items)
        retrieval_hint_text = _retrieval_hint_text(signals.retrieval_hints)
        notes = (
            f"Derived compact Zotero profile from {signals.used_item_count} biomedical items out of "
            f"{signals.parsed_item_count} parsed; strongest signals: "
            f"{', '.join((*signals.keywords[:3], *signals.concepts[:2])) or 'none'}."
            f"{f' Retrieval hints: {retrieval_hint_text}.' if retrieval_hint_text else ''}"
        )
        return UserInterestProfile(
            keywords=signals.keywords,
            category_weights=_normalized_weights(signals.concepts or signals.keywords, limit=self.max_keywords),
            seed_titles=signals.seed_titles,
            notes=notes,
            basis_label="Zotero export",
            profile_basis=ProfileBasis(
                source=PROFILE_SOURCE_ZOTERO_EXPORT,
                label="Zotero export",
                item_count=signals.parsed_item_count,
                used_item_count=signals.used_item_count,
            ),
            zotero_item_count=signals.parsed_item_count,
            zotero_used_item_count=signals.used_item_count,
            zotero_keywords=signals.keywords,
            zotero_concepts=signals.concepts,
            zotero_retrieval_hints=signals.retrieval_hints,
        )

    def build_augmented_profile(
        self,
        baseline: UserInterestProfile,
        *,
        export_path: str | Path,
        selected_collections: Iterable[str] = (),
        profile_source: str = PROFILE_SOURCE_ZOTERO_EXPORT,
        profile_label: str = "Zotero export",
    ) -> UserInterestProfile:
        export_file = Path(export_path)
        export_name = export_file.name
        export_label = str(export_file.expanduser().resolve())
        return self.build_augmented_profile_from_items(
            baseline,
            items=load_csl_json_export(export_path),
            profile_source=profile_source,
            profile_label=profile_label,
            profile_path=export_label,
            export_name=export_name,
            selected_collections=selected_collections,
        )

    def build_augmented_profile_from_db(
        self,
        baseline: UserInterestProfile,
        *,
        db_path: str | Path,
    ) -> UserInterestProfile:
        db_file = Path(db_path)
        db_name = db_file.name
        db_label = str(db_file.expanduser().resolve())
        return self.build_augmented_profile_from_items(
            baseline,
            items=load_sqlite_library(db_path),
            profile_source=PROFILE_SOURCE_LIVE_ZOTERO_DB,
            profile_label="Live Zotero DB",
            profile_path=db_label,
            db_name=db_name,
        )

    def build_augmented_profile_from_items(
        self,
        baseline: UserInterestProfile,
        *,
        items: Iterable[Mapping[str, Any] | ZoteroExportItem],
        profile_source: str = PROFILE_SOURCE_ZOTERO,
        profile_label: str = "Zotero",
        profile_path: str = "",
        export_name: str = "",
        db_name: str = "",
        selected_collections: Iterable[str] = (),
    ) -> UserInterestProfile:
        signals = self.derive_signals(items)
        collection_tuple = _dedupe_preserving_order(selected_collections)
        top_signal_text = ", ".join((*signals.keywords[:3], *signals.concepts[:2])) or "none"
        retrieval_hint_text = _retrieval_hint_text(signals.retrieval_hints)
        collection_note = (
            f" Selected collections: {', '.join(collection_tuple)}."
            if collection_tuple
            else ""
        )
        if profile_source == PROFILE_SOURCE_LIVE_ZOTERO_DB:
            source_summary = f"live local Zotero DB {db_name}"
            basis_label = "biomedical baseline + live Zotero DB"
            description = "Read-only local Zotero SQLite profile source."
        elif profile_source == PROFILE_SOURCE_ZOTERO_EXPORT:
            source_summary = f"CSL JSON Zotero export {export_name or profile_label}"
            basis_label = "biomedical baseline + Zotero export"
            description = "Read-only CSL JSON Zotero profile source."
        else:
            source_summary = f"Zotero export snapshot {export_name or profile_label}"
            basis_label = "biomedical baseline + Zotero"
            description = "Locally exported Zotero profile snapshot."
        notes = (
            f"{baseline.notes} Personalized with {source_summary}: "
            f"{signals.used_item_count} biomedical items from {signals.parsed_item_count} parsed; "
            f"top Zotero signals: {top_signal_text}."
            f"{' No biomedical-usable signals were found.' if signals.used_item_count <= 0 else ''}"
            f"{f' Retrieval hints: {retrieval_hint_text}.' if retrieval_hint_text else ''}"
            f"{collection_note}"
        ).strip()
        merged_keywords = _dedupe_preserving_order((*baseline.keywords, *signals.keywords, *signals.concepts))
        if signals.used_item_count <= 0:
            return UserInterestProfile(
                keywords=baseline.keywords,
                category_weights=dict(baseline.category_weights),
                seed_titles=baseline.seed_titles,
                notes=notes,
                basis_label="biomedical baseline",
                profile_basis=ProfileBasis(
                    source=profile_source,
                    label=profile_label,
                    description=description,
                    path=profile_path,
                    item_count=signals.parsed_item_count,
                    used_item_count=signals.used_item_count,
                ),
                zotero_item_count=signals.parsed_item_count,
                zotero_used_item_count=signals.used_item_count,
                zotero_export_name=export_name,
                zotero_db_name=db_name,
                zotero_selected_collections=collection_tuple,
            )
        return UserInterestProfile(
            keywords=merged_keywords or baseline.keywords,
            category_weights=dict(baseline.category_weights),
            seed_titles=signals.seed_titles or baseline.seed_titles,
            notes=notes,
            basis_label=basis_label,
            profile_basis=ProfileBasis(
                source=profile_source,
                label=profile_label,
                description=description,
                path=profile_path,
                item_count=signals.parsed_item_count,
                used_item_count=signals.used_item_count,
            ),
            zotero_item_count=signals.parsed_item_count,
            zotero_used_item_count=signals.used_item_count,
            zotero_export_name=export_name,
            zotero_db_name=db_name,
            zotero_keywords=signals.keywords,
            zotero_concepts=signals.concepts,
            zotero_selected_collections=collection_tuple,
            zotero_retrieval_hints=signals.retrieval_hints,
        )

    def derive_signals(self, items: Iterable[Mapping[str, Any] | ZoteroExportItem]) -> DerivedZoteroSignals:
        normalized_items = tuple(_normalize_items(items))
        keyword_counts: Counter[str] = Counter()
        concept_counts: Counter[str] = Counter()
        seed_titles: list[str] = []
        seen_titles: set[str] = set()
        used_item_count = 0
        reference_date = max(
            (item.date_added for item in normalized_items if item.date_added is not None),
            default=None,
        )

        for item in normalized_items:
            if not _is_biomedical_usable(item, min_keyword_length=self.min_keyword_length):
                continue
            used_item_count += 1
            if item.title not in seen_titles:
                seed_titles.append(item.title)
                seen_titles.add(item.title)

            recency_weight = _recency_weight(item.date_added, reference_date=reference_date)
            explicit_terms = (*item.keywords, *item.collections)
            explicit_tokens = {
                token
                for keyword in explicit_terms
                for token in tokenize(keyword, min_length=self.min_keyword_length)
                if token not in GENERIC_NOISE_TOKENS
            }
            text_tokens = {
                token
                for token in tokenize(f"{item.title} {item.abstract}", min_length=self.min_keyword_length)
                if token not in GENERIC_NOISE_TOKENS
            }
            for token in explicit_tokens:
                keyword_counts[token] += 3 * recency_weight
            for token in text_tokens:
                keyword_counts[token] += 1 * recency_weight

            explicit_concepts = _dedupe_preserving_order(
                concept
                for keyword in explicit_terms
                for concept in _phrase_candidates(keyword)
            )
            text_concepts = tuple(
                phrase
                for phrase in BIOMEDICAL_SIGNAL_PHRASES
                if phrase in item.normalized_text()
            )
            for concept in explicit_concepts:
                concept_counts[concept] += 4 * recency_weight
            for concept in text_concepts:
                concept_counts[concept] += 2 * recency_weight

        keywords = tuple(keyword for keyword, _count in keyword_counts.most_common(self.max_keywords))
        concepts = tuple(concept for concept, _count in concept_counts.most_common(self.max_concepts))
        retrieval_hints = _build_retrieval_hints(
            concept_counts,
            keyword_counts,
            max_hints=self.max_retrieval_hints,
            max_terms=self.max_retrieval_terms,
        )
        return DerivedZoteroSignals(
            parsed_item_count=len(normalized_items),
            used_item_count=used_item_count,
            keywords=keywords,
            concepts=concepts,
            seed_titles=tuple(seed_titles[: self.max_seed_titles]),
            retrieval_hints=retrieval_hints,
        )


def build_profile(
    items: Iterable[Mapping[str, Any] | ZoteroExportItem],
    *,
    max_keywords: int = 8,
    min_keyword_length: int = 4,
) -> UserInterestProfile:
    return ZoteroProfileBuilder(
        max_keywords=max_keywords,
        min_keyword_length=min_keyword_length,
    ).build(items)


def _normalize_items(items: Iterable[Mapping[str, Any] | ZoteroExportItem]) -> list[ZoteroExportItem]:
    normalized: list[ZoteroExportItem] = []
    for item in items:
        if isinstance(item, ZoteroExportItem):
            normalized.append(item)
            continue
        if not isinstance(item, Mapping):
            continue
        title = str(item.get("title", "")).strip()
        abstract = str(item.get("abstract") or item.get("abstractNote") or "").strip()
        tags = _dedupe_preserving_order(
            (*_iter_tags(item.get("tags", [])), *_iter_strings(item.get("keywords", ())), *_iter_strings(item.get("keyword")))
        )
        collections = _dedupe_preserving_order(_iter_strings(item.get("collections", ())))
        if not title and not abstract and not tags and not collections:
            continue
        normalized.append(
            ZoteroExportItem(
                title=title,
                abstract=abstract,
                keywords=tags,
                collections=collections,
                date_added=_parse_mapping_date(item.get("date_added") or item.get("dateAdded")),
            )
        )
    return normalized


def _is_biomedical_usable(item: ZoteroExportItem, *, min_keyword_length: int) -> bool:
    text = item.normalized_text()
    if any(phrase in text for phrase in BIOMEDICAL_SIGNAL_PHRASES):
        return True
    return bool(BIOMEDICAL_SIGNAL_TOKENS & set(tokenize(text, min_length=min_keyword_length)))


def _phrase_candidates(text: str) -> tuple[str, ...]:
    cleaned = " ".join(text.strip().lower().split())
    if not cleaned:
        return ()
    if cleaned in BIOMEDICAL_SIGNAL_PHRASES:
        return (cleaned,)

    tokens = [token for token in tokenize(cleaned, min_length=4) if token not in GENERIC_NOISE_TOKENS]
    if len(tokens) < 2:
        return ()
    if not (set(tokens) & BIOMEDICAL_SIGNAL_TOKENS):
        return ()
    phrase = " ".join(tokens[:3] if len(tokens) >= 3 and len(tokens[0]) <= 6 else tokens[:2])
    if len(phrase.split()) < 2:
        return ()
    return (phrase,)


def _dedupe_preserving_order(values: Iterable[str]) -> tuple[str, ...]:
    ordered: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value).strip()
        if not normalized:
            continue
        canonical = normalized.lower()
        if canonical in seen:
            continue
        ordered.append(normalized)
        seen.add(canonical)
    return tuple(ordered)


def _normalized_weights(values: Iterable[str], *, limit: int) -> dict[str, float]:
    selected = list(_dedupe_preserving_order(values))[:limit]
    total = sum(range(len(selected), 0, -1))
    if not selected or total <= 0:
        return {}
    return {
        value.lower(): round((len(selected) - index) / total, 3)
        for index, value in enumerate(selected)
    }


def _build_retrieval_hints(
    concept_counts: Counter[str],
    keyword_counts: Counter[str],
    *,
    max_hints: int,
    max_terms: int,
) -> tuple[ZoteroRetrievalHint, ...]:
    if max_hints <= 0 or max_terms <= 0:
        return ()

    hints: list[ZoteroRetrievalHint] = []
    used_terms: set[str] = set()
    for label, preferred_themes, rationale in RETRIEVAL_PROFILE_BLUEPRINTS:
        selected_terms = _select_profile_terms(
            preferred_themes,
            concept_counts,
            keyword_counts,
            used_terms=used_terms,
            max_terms=max_terms,
        )
        if not selected_terms:
            continue
        hints.append(
            ZoteroRetrievalHint(
                label=label,
                terms=selected_terms,
                rationale=rationale,
            )
        )
        used_terms.update(term.lower() for term in selected_terms)
        if len(hints) >= max_hints:
            break
    return tuple(hints)


def _select_profile_terms(
    preferred_themes: tuple[str, ...],
    concept_counts: Counter[str],
    keyword_counts: Counter[str],
    *,
    used_terms: set[str],
    max_terms: int,
) -> tuple[str, ...]:
    selected: list[str] = []
    excluded = set(used_terms)
    for theme in preferred_themes:
        term = _best_theme_term(theme, concept_counts, keyword_counts, excluded=excluded)
        if term:
            selected.append(term)
            excluded.add(term.lower())
        if len(selected) >= max_terms:
            return tuple(selected[:max_terms])

    for theme in preferred_themes:
        if len(selected) >= max_terms:
            break
        theme_terms = _theme_candidates(theme, concept_counts, keyword_counts, excluded=excluded)
        for term in theme_terms:
            if term.lower() in excluded or _term_is_redundant(term, selected):
                continue
            selected.append(term)
            excluded.add(term.lower())
            if len(selected) >= max_terms:
                break

    return tuple(selected[:max_terms])


def _best_theme_term(
    theme: str,
    concept_counts: Counter[str],
    keyword_counts: Counter[str],
    *,
    excluded: set[str],
) -> str:
    candidates = _theme_candidates(theme, concept_counts, keyword_counts, excluded=excluded)
    if not candidates:
        return ""
    return candidates[0]


def _theme_candidates(
    theme: str,
    concept_counts: Counter[str],
    keyword_counts: Counter[str],
    *,
    excluded: set[str],
) -> list[str]:
    anchors = RETRIEVAL_THEME_TERMS.get(theme, ())
    if not anchors:
        return []

    scored_terms: list[tuple[int, str]] = []
    for term, count in concept_counts.items():
        normalized = term.strip().lower()
        if normalized in excluded or not _matches_theme(normalized, anchors):
            continue
        score = (count * 3) + len(normalized.split())
        scored_terms.append((score, normalized))
    for term, count in keyword_counts.items():
        normalized = term.strip().lower()
        if normalized in excluded or not _matches_theme(normalized, anchors):
            continue
        if normalized in {value for _score, value in scored_terms}:
            continue
        score = count
        scored_terms.append((score, normalized))

    ordered = sorted(scored_terms, key=lambda item: (-item[0], item[1]))
    return [term for _score, term in ordered]


def _matches_theme(term: str, anchors: tuple[str, ...]) -> bool:
    term_tokens = set(tokenize(term, min_length=4))
    for anchor in anchors:
        normalized_anchor = anchor.lower()
        if normalized_anchor == term or normalized_anchor in term:
            return True
        anchor_tokens = set(tokenize(normalized_anchor, min_length=4))
        if anchor_tokens and anchor_tokens <= term_tokens:
            return True
    return False


def _term_is_redundant(candidate: str, selected: list[str]) -> bool:
    candidate_tokens = set(tokenize(candidate, min_length=4))
    if not candidate_tokens:
        return False
    for existing in selected:
        existing_tokens = set(tokenize(existing, min_length=4))
        if not existing_tokens:
            continue
        overlap = candidate_tokens & existing_tokens
        if overlap and min(len(overlap) / len(candidate_tokens), len(overlap) / len(existing_tokens)) >= 0.67:
            return True
    return False


def _retrieval_hint_text(hints: tuple[ZoteroRetrievalHint, ...]) -> str:
    if not hints:
        return ""
    return "; ".join(" + ".join(hint.terms) for hint in hints if hint.terms)


def _recency_weight(item_date, *, reference_date) -> float:  # type: ignore[no-untyped-def]
    if item_date is None or reference_date is None:
        return 1.0
    delta_days = max((reference_date - item_date).days, 0)
    if delta_days <= 30:
        return 1.8
    if delta_days <= 180:
        return 1.35
    return 1.0


def _parse_mapping_date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _iter_tags(raw_tags: Any) -> list[str]:
    values: list[str] = []
    for tag in _iter_strings(raw_tags):
        values.append(tag)
    if isinstance(raw_tags, list):
        for tag in raw_tags:
            if isinstance(tag, Mapping):
                value = str(tag.get("tag", "")).strip()
                if value:
                    values.append(value)
    return values


def _iter_strings(raw_value: Any) -> list[str]:
    if raw_value is None:
        return []
    if isinstance(raw_value, str):
        return [raw_value]
    if isinstance(raw_value, list):
        return [str(value).strip() for value in raw_value if str(value).strip()]
    return [str(raw_value).strip()]
