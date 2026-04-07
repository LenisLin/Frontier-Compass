"""Deterministic frontier-report helpers shared across reporting surfaces."""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Mapping, Sequence

from frontier_compass.common.report_mode import DEFAULT_REPORT_MODE, ZERO_TOKEN_COST_MODE
from frontier_compass.storage.schema import (
    DailyFrontierReport,
    FrontierReportHighlight,
    FrontierReportSignal,
    PaperRecord,
    RankedPaper,
)


GENOMICS_THEME = "genomics / transcriptomics / single-cell"
PATHOLOGY_THEME = "pathology / histopathology / microscopy"
IMAGING_THEME = "medical imaging / radiology"
PROTEIN_THEME = "protein / biomolecular / sequence modeling"
CLINICAL_THEME = "clinical / EHR / tabular biomedical ML"
GENERAL_THEME = "general biomedical methods"

PATHOLOGY_TERMS = (
    "pathology",
    "histopathology",
    "histology",
    "whole-slide",
    "whole slide",
    "wsi",
    "microscopy",
    "slide",
)
IMAGING_TERMS = (
    "medical imaging",
    "biomedical imaging",
    "radiology",
    "ct",
    "mri",
    "x-ray",
    "x ray",
    "xray",
    "ultrasound",
    "radiotherapy",
    "scan",
)
CLINICAL_TERMS = (
    "clinical",
    "patient",
    "ehr",
    "electronic health records",
    "tabular",
    "cohort",
    "registry",
    "phenotypic",
    "liquid biopsy",
)
GENOMICS_TERMS = (
    "genomics",
    "transcriptomics",
    "single-cell",
    "single cell",
    "spatial transcriptomics",
    "cell atlas",
    "multi-omics",
    "perturbation",
)
PROTEIN_TERMS = (
    "protein",
    "peptide",
    "biomolecular",
    "molecular",
    "sequence",
    "binding",
    "ligand",
    "enzyme",
    "rna binding",
)
FOUNDATION_MODEL_TERMS = (
    "foundation model",
    "foundation models",
    "multimodal",
    "vision language",
    "vision-language",
    "language model",
    "language models",
    "llm",
    "mllm",
)
REPRESENTATION_TERMS = (
    "representation learning",
    "embedding",
    "embeddings",
    "contrastive",
    "pretrain",
    "pre-training",
    "pretraining",
    "self-supervised",
    "latent representation",
)
INTERPRETABILITY_TERMS = (
    "interpretable",
    "interpretability",
    "sparse autoencoder",
    "sparse autoencoders",
    "sparse feature",
    "sparse features",
    "concept-driven",
)
SEGMENTATION_TERMS = (
    "segmentation",
    "segment",
    "detection",
    "localization",
    "annotation",
    "mask",
)
RETRIEVAL_TERMS = (
    "retrieval",
    "search",
    "retriever",
    "rerank",
    "reranking",
    "agent",
    "agents",
)

GENOMICS_CATEGORY_HINTS = frozenset({"q-bio.gn"})
PROTEIN_CATEGORY_HINTS = frozenset({"q-bio.bm"})
Q_BIO_PREFIX = "q-bio"
DEFAULT_FIELD_HIGHLIGHT_LIMIT = 6
DEFAULT_PROFILE_HIGHLIGHT_LIMIT = 3
DEFAULT_PROFILE_RELEVANCE_THRESHOLD = 0.45

TOPIC_BUCKETS = (
    ("single-cell / omics / atlas", GENOMICS_TERMS),
    ("pathology / whole-slide / microscopy", PATHOLOGY_TERMS),
    ("medical imaging / radiology", IMAGING_TERMS),
    ("clinical / EHR / tabular", CLINICAL_TERMS),
    ("protein / biomolecular modeling", PROTEIN_TERMS),
    ("foundation / multimodal / language models", FOUNDATION_MODEL_TERMS),
    ("representation / embeddings / contrastive learning", REPRESENTATION_TERMS),
    ("interpretability / sparse representations", INTERPRETABILITY_TERMS),
    ("segmentation / detection / localization", SEGMENTATION_TERMS),
    ("retrieval / search / agents", RETRIEVAL_TERMS),
)


@dataclass(slots=True, frozen=True)
class _HighlightCandidate:
    paper: PaperRecord
    theme_label: str
    topic_hits: tuple[str, ...]
    salience_score: int


def build_daily_frontier_report(
    *,
    paper_pool: Sequence[PaperRecord],
    ranked_papers: Sequence[RankedPaper],
    requested_date: date,
    effective_date: date,
    source: str,
    mode: str,
    mode_label: str,
    mode_kind: str = "",
    requested_report_mode: str = DEFAULT_REPORT_MODE,
    report_mode: str = DEFAULT_REPORT_MODE,
    cost_mode: str = ZERO_TOKEN_COST_MODE,
    enhanced_track: str = "",
    enhanced_item_count: int = 0,
    runtime_note: str = "",
    llm_requested: bool = False,
    llm_applied: bool = False,
    llm_provider: str | None = None,
    llm_fallback_reason: str | None = None,
    llm_seconds: float | None = None,
    searched_categories: Sequence[str] = (),
    total_fetched: int = 0,
    field_highlight_limit: int = DEFAULT_FIELD_HIGHLIGHT_LIMIT,
    profile_highlight_limit: int = DEFAULT_PROFILE_HIGHLIGHT_LIMIT,
    profile_relevance_threshold: float = DEFAULT_PROFILE_RELEVANCE_THRESHOLD,
) -> DailyFrontierReport:
    papers = list(paper_pool)
    ranked_items = list(ranked_papers)
    resolved_total_fetched = max(int(total_fetched), len(papers))

    theme_counter: Counter[str] = Counter()
    topic_counter: Counter[str] = Counter()
    adjacent_counter: Counter[str] = Counter()
    source_counter: Counter[str] = Counter()
    topic_hits_by_identifier: dict[str, tuple[str, ...]] = {}
    theme_by_identifier: dict[str, str] = {}

    for paper in papers:
        theme_label = theme_label_for_paper(paper)
        topic_hits = topic_bucket_hits_for_paper(paper)
        identifier = _paper_lookup_key(paper)
        theme_by_identifier[identifier] = theme_label
        topic_hits_by_identifier[identifier] = topic_hits
        theme_counter.update((theme_label,))
        topic_counter.update(topic_hits)
        source_counter.update(((paper.source or "unknown").strip().lower(),))
        if not _has_qbio_category(paper.categories):
            adjacent_counter.update(topic_hits or (theme_label,))

    repeated_themes = _ordered_signals(theme_counter, max_signals=4, min_count=2)
    if not repeated_themes:
        repeated_themes = _ordered_signals(theme_counter, max_signals=4, min_count=1)
    salient_topics = _ordered_signals(topic_counter, max_signals=6, min_count=2)
    if not salient_topics:
        salient_topics = _ordered_signals(topic_counter, max_signals=6, min_count=1)
    adjacent_themes = _ordered_signals(adjacent_counter, max_signals=4, min_count=2)
    if not adjacent_themes:
        adjacent_themes = _ordered_signals(adjacent_counter, max_signals=4, min_count=1)

    field_highlights = _build_field_highlights(
        papers,
        theme_counter=theme_counter,
        topic_counter=topic_counter,
        theme_by_identifier=theme_by_identifier,
        topic_hits_by_identifier=topic_hits_by_identifier,
        limit=field_highlight_limit,
    )
    profile_relevant_highlights = _build_profile_relevant_highlights(
        ranked_items,
        theme_by_identifier=theme_by_identifier,
        highlighted_identifiers={item.identifier for item in field_highlights},
        limit=profile_highlight_limit,
        threshold=profile_relevance_threshold,
    )
    takeaways = _build_takeaways(
        papers,
        searched_categories=searched_categories,
        total_fetched=resolved_total_fetched,
        source_counts=_ordered_source_counts(source_counter),
        repeated_themes=repeated_themes,
        salient_topics=salient_topics,
        adjacent_themes=adjacent_themes,
        field_highlights=field_highlights,
    )

    return DailyFrontierReport(
        requested_date=requested_date,
        effective_date=effective_date,
        source=source,
        mode=mode,
        mode_label=mode_label,
        mode_kind=mode_kind,
        requested_report_mode=requested_report_mode,
        report_mode=report_mode,
        cost_mode=cost_mode,
        enhanced_track=enhanced_track,
        enhanced_item_count=enhanced_item_count,
        runtime_note=runtime_note,
        llm_requested=llm_requested,
        llm_applied=llm_applied,
        llm_provider=llm_provider,
        llm_fallback_reason=llm_fallback_reason,
        llm_seconds=llm_seconds,
        searched_categories=tuple(str(value) for value in searched_categories if str(value)),
        total_fetched=resolved_total_fetched,
        total_ranked=len(papers),
        source_counts=_ordered_source_counts(source_counter),
        repeated_themes=repeated_themes,
        salient_topics=salient_topics,
        adjacent_themes=adjacent_themes,
        deterministic_takeaways=takeaways,
        deterministic_field_highlights=field_highlights,
        takeaways=takeaways,
        field_highlights=field_highlights,
        profile_relevant_highlights=profile_relevant_highlights,
    )


def paper_frontier_text(paper: PaperRecord) -> str:
    return " ".join(
        part.strip().lower()
        for part in (paper.title, paper.summary, " ".join(paper.categories))
        if part and part.strip()
    ).strip()


def theme_label_for_ranked_paper(item: RankedPaper) -> str:
    return theme_label_for_paper(item.paper)


def theme_label_for_paper(paper: PaperRecord) -> str:
    text = paper_frontier_text(paper)
    normalized_categories = {category.lower() for category in paper.categories if category}

    if _contains_any_term(text, PATHOLOGY_TERMS):
        return PATHOLOGY_THEME
    if _contains_any_term(text, IMAGING_TERMS):
        return IMAGING_THEME
    if _contains_any_term(text, CLINICAL_TERMS):
        return CLINICAL_THEME
    if _contains_any_term(text, GENOMICS_TERMS) or normalized_categories & GENOMICS_CATEGORY_HINTS:
        return GENOMICS_THEME
    if _contains_any_term(text, PROTEIN_TERMS) or normalized_categories & PROTEIN_CATEGORY_HINTS:
        return PROTEIN_THEME
    return GENERAL_THEME


def topic_bucket_hits_for_paper(paper: PaperRecord) -> tuple[str, ...]:
    text = paper_frontier_text(paper)
    hits: list[str] = []
    for label, terms in TOPIC_BUCKETS:
        if _contains_any_term(text, terms):
            hits.append(label)
    return tuple(hits)


def _ordered_signals(counter: Counter[str], *, max_signals: int, min_count: int) -> tuple[FrontierReportSignal, ...]:
    ordered = sorted(
        (
            (label, count)
            for label, count in counter.items()
            if label and count >= min_count
        ),
        key=lambda item: (-item[1], item[0]),
    )
    return tuple(
        FrontierReportSignal(label=label, count=count)
        for label, count in ordered[:max(max_signals, 0)]
    )


def _build_field_highlights(
    papers: Sequence[PaperRecord],
    *,
    theme_counter: Counter[str],
    topic_counter: Counter[str],
    theme_by_identifier: dict[str, str],
    topic_hits_by_identifier: dict[str, tuple[str, ...]],
    limit: int,
) -> tuple[FrontierReportHighlight, ...]:
    if not papers or limit <= 0:
        return ()

    candidates: list[_HighlightCandidate] = []
    for paper in papers:
        identifier = _paper_lookup_key(paper)
        theme_label = theme_by_identifier.get(identifier, theme_label_for_paper(paper))
        topic_hits = topic_hits_by_identifier.get(identifier, ())
        salience_score = (
            theme_counter.get(theme_label, 0) * 3
            + sum(topic_counter.get(label, 0) for label in topic_hits[:3])
            + (2 * len(topic_hits))
            + min(len(paper.categories), 3)
        )
        candidates.append(
            _HighlightCandidate(
                paper=paper,
                theme_label=theme_label,
                topic_hits=topic_hits,
                salience_score=salience_score,
            )
        )

    ordered_candidates = sorted(candidates, key=_field_candidate_sort_key)
    selected = _select_diverse_candidates(ordered_candidates, limit=limit)
    return tuple(
        FrontierReportHighlight(
            source=candidate.paper.source,
            identifier=candidate.paper.identifier,
            title=candidate.paper.title,
            theme_label=candidate.theme_label,
            why=_field_highlight_reason(
                candidate.theme_label,
                candidate.topic_hits,
                theme_count=theme_counter.get(candidate.theme_label, 0),
            ),
            summary=_summary_snippet(candidate.paper.summary),
            categories=candidate.paper.categories[:4],
            url=candidate.paper.url,
            published=candidate.paper.published or candidate.paper.updated,
        )
        for candidate in selected
    )


def _build_profile_relevant_highlights(
    ranked_papers: Sequence[RankedPaper],
    *,
    theme_by_identifier: dict[str, str],
    highlighted_identifiers: set[str],
    limit: int,
    threshold: float,
) -> tuple[FrontierReportHighlight, ...]:
    if not ranked_papers or limit <= 0:
        return ()

    recommended = [item for item in ranked_papers if item.score >= threshold]
    ordered = recommended or list(ranked_papers)
    ordered.sort(
        key=lambda item: (
            item.score,
            item.paper.published or item.paper.updated or date.min,
            item.paper.title.lower(),
        ),
        reverse=True,
    )

    selected_items = [
        item
        for item in ordered
        if item.paper.identifier not in highlighted_identifiers
    ]
    if len(selected_items) < limit:
        selected_items = ordered

    highlights: list[FrontierReportHighlight] = []
    seen: set[str] = set()
    for item in selected_items:
        if item.paper.identifier in seen:
            continue
        seen.add(item.paper.identifier)
        highlights.append(
            FrontierReportHighlight(
                source=item.paper.source,
                identifier=item.paper.identifier,
                title=item.paper.title,
                theme_label=theme_by_identifier.get(_paper_lookup_key(item.paper), theme_label_for_paper(item.paper)),
                why=_profile_highlight_reason(item),
                summary=_summary_snippet(item.paper.summary),
                categories=item.paper.categories[:4],
                url=item.paper.url,
                published=item.paper.published or item.paper.updated,
                score=round(item.score, 3),
            )
        )
        if len(highlights) >= limit:
            break
    return tuple(highlights)


def _build_takeaways(
    ranked_papers: Sequence[RankedPaper],
    *,
    searched_categories: Sequence[str],
    total_fetched: int,
    source_counts: Mapping[str, int] | None = None,
    repeated_themes: Sequence[FrontierReportSignal],
    salient_topics: Sequence[FrontierReportSignal],
    adjacent_themes: Sequence[FrontierReportSignal],
    field_highlights: Sequence[FrontierReportHighlight],
) -> tuple[str, ...]:
    if not ranked_papers:
        return (
            f"Frontier Report is empty for the current run: 0 ranked papers from {total_fetched} fetched items.",
            "Refresh the current day or inspect a different source to populate the broader field view.",
        )

    coverage_line = (
        f"Frontier Report covers {len(ranked_papers)} ranked papers from {total_fetched} fetched items"
        + (
            f" across {len(tuple(dict.fromkeys(str(value) for value in searched_categories if str(value))))} searched categories."
            if searched_categories
            else "."
        )
    )
    source_line = (
        f"Source composition in this broader pool: {_source_text(source_counts)}."
        if source_counts
        else "Source composition is unavailable for this broader pool."
    )
    theme_line = (
        f"Repeated themes / hotspots: {_signal_text(repeated_themes)}."
        if repeated_themes
        else "Repeated themes are diffuse across the broader pool."
    )
    topic_line = (
        f"Method hotspots today: {_signal_text(salient_topics)}."
        if salient_topics
        else "No single method or topic bucket dominates the broader pool."
    )
    highlight_line = (
        f'Important highlight: "{field_highlights[0].title}" - {field_highlights[0].why}'
        if field_highlights
        else "No important field-wide highlight was selected from this broader pool."
    )
    if adjacent_themes:
        adjacent_line = f"Adjacent frontier signals: {_signal_text(adjacent_themes)}."
    else:
        adjacent_line = "Adjacent signals are limited; the broader pool stays close to the core biomedical baseline."
    lines = [coverage_line, source_line, theme_line, topic_line, highlight_line, adjacent_line]
    return tuple(lines[:6])


def _field_candidate_sort_key(candidate: _HighlightCandidate) -> tuple[int, int, str]:
    published = candidate.paper.published or candidate.paper.updated or date.min
    return (-candidate.salience_score, -published.toordinal(), candidate.paper.title.lower())


def _select_diverse_candidates(
    candidates: Sequence[_HighlightCandidate],
    *,
    limit: int,
) -> list[_HighlightCandidate]:
    selected: list[_HighlightCandidate] = []
    skipped: list[_HighlightCandidate] = []
    theme_counts: Counter[str] = Counter()
    topic_counts: Counter[str] = Counter()

    effective_limit = min(max(limit, 1), len(candidates), 10)
    for candidate in candidates:
        allow_theme = theme_counts[candidate.theme_label] < 2
        allow_topic = (
            not candidate.topic_hits
            or any(topic_counts[label] < 2 for label in candidate.topic_hits[:2])
        )
        if not allow_theme or not allow_topic:
            skipped.append(candidate)
            continue
        selected.append(candidate)
        theme_counts[candidate.theme_label] += 1
        for label in candidate.topic_hits[:2]:
            topic_counts[label] += 1
        if len(selected) >= effective_limit:
            return selected

    for candidate in skipped:
        selected.append(candidate)
        if len(selected) >= effective_limit:
            break
    return selected


def _field_highlight_reason(theme_label: str, topic_hits: Sequence[str], *, theme_count: int) -> str:
    if topic_hits:
        lead_topics = _join_labels(topic_hits[:2])
        if theme_count > 1:
            return f"Repeated {theme_label} signal in today’s pool; also carries {lead_topics}."
        return f"Field-wide highlight for {theme_label}; carries {lead_topics}."
    if theme_count > 1:
        return f"Repeated {theme_label} signal in today’s pool."
    return f"Representative field-wide highlight for {theme_label}."


def _profile_highlight_reason(item: RankedPaper) -> str:
    explanation = item.explanation
    if explanation is None:
        return item.recommendation_summary or "Potential match to the current research profile."

    parts: list[str] = []
    if explanation.baseline_keyword_hits:
        parts.append(f"baseline signals: {_join_labels(explanation.baseline_keyword_hits[:2])}")
    if explanation.category_hits:
        parts.append(f"category support: {_join_labels(explanation.category_hits[:2])}")

    zotero_hits = tuple(
        value
        for value in (*explanation.zotero_keyword_hits[:2], *explanation.zotero_concept_hits[:2])
        if value
    )
    if zotero_hits:
        parts.append(f"Zotero signals: {_join_labels(zotero_hits)}")

    if parts:
        return "; ".join(parts)
    if item.reasons:
        return item.reasons[0]
    return item.recommendation_summary or "Potential match to the current research profile."


def _summary_snippet(summary: str, *, max_length: int = 220) -> str:
    normalized = " ".join(summary.split())
    if len(normalized) <= max_length:
        return normalized
    sentence = re.split(r"(?<=[.!?])\s+", normalized, maxsplit=1)[0].strip()
    if 60 <= len(sentence) <= max_length:
        return sentence
    return normalized[: max_length - 3].rstrip() + "..."


def _signal_text(signals: Sequence[FrontierReportSignal]) -> str:
    return ", ".join(f"{signal.label} ({signal.count})" for signal in signals)


def _source_text(source_counts: Mapping[str, int] | None) -> str:
    if not source_counts:
        return "no source counts"
    ordered = sorted(source_counts.items(), key=lambda item: (-item[1], item[0]))
    return ", ".join(f"{label} ({count})" for label, count in ordered if label)


def _ordered_source_counts(counter: Counter[str]) -> dict[str, int]:
    return {label: count for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])) if label}


def _join_labels(labels: Sequence[str]) -> str:
    values = [label for label in labels if label]
    if not values:
        return "broad frontier coverage"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" and {values[-1]}"


def _paper_lookup_key(paper: PaperRecord) -> str:
    return f"{(paper.source or 'unknown').strip().lower()}::{paper.display_id}"


def _has_qbio_category(categories: Sequence[str]) -> bool:
    return any(category.strip().lower().startswith(Q_BIO_PREFIX) for category in categories if category)


def _contains_any_term(text: str, terms: Sequence[str]) -> bool:
    return any(_contains_term(text, term) for term in terms)


def _contains_term(text: str, term: str) -> bool:
    if not term:
        return False
    escaped = re.escape(term.lower()).replace(r"\ ", r"\s+")
    pattern = rf"(?<![a-z0-9]){escaped}(?![a-z0-9])"
    return re.search(pattern, text) is not None
