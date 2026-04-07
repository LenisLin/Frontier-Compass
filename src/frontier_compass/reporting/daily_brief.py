"""Deterministic helpers for daily brief summaries and shortlist filtering."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import Literal, Sequence

from frontier_compass.common.frontier_report import (
    CLINICAL_THEME,
    GENERAL_THEME,
    GENOMICS_THEME,
    IMAGING_THEME,
    PATHOLOGY_THEME,
    PROTEIN_THEME,
    theme_label_for_paper,
)
from frontier_compass.ranking.relevance import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    biomedical_evidence_for_paper,
    category_hits_for_paper,
    keyword_hits_for_paper,
    priority_label_for_score,
)
from frontier_compass.storage.schema import RankedPaper, UserInterestProfile


SortMode = Literal["score", "newest"]

DEFAULT_THEME_CAP = 2


@dataclass(slots=True, frozen=True)
class BriefSignal:
    label: str
    count: int


@dataclass(slots=True, frozen=True)
class DailyBriefSummary:
    shown_count: int
    total_ranked: int
    recommended_count: int
    average_score: float
    top_theme_signals: tuple[BriefSignal, ...]
    top_category_signals: tuple[BriefSignal, ...]
    top_keyword_signals: tuple[BriefSignal, ...]
    takeaways: tuple[str, ...]


def build_daily_brief(
    profile: UserInterestProfile,
    ranked_papers: Sequence[RankedPaper],
    *,
    total_ranked: int | None = None,
    recommended_threshold: float = DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    max_signals: int = 4,
) -> DailyBriefSummary:
    items = list(ranked_papers)
    total_count = total_ranked if total_ranked is not None else len(items)
    shown_count = len(items)
    recommended_count = sum(1 for item in items if is_recommended(item.score, recommended_threshold=recommended_threshold))
    average_score = round(sum(item.score for item in items) / shown_count, 3) if shown_count else 0.0

    theme_counter: Counter[str] = Counter()
    category_counter: Counter[str] = Counter()
    keyword_counter: Counter[str] = Counter()
    for item in items:
        theme_counter.update((theme_label_for_ranked_paper(item),))
        category_hits = category_hits_for_paper(item.paper, profile)
        if category_hits:
            category_counter.update(category_hits[:2])
        else:
            category_counter.update(category.lower() for category in item.paper.categories[:2] if category)
        evidence_hits = biomedical_evidence_for_paper(item.paper, profile)
        if evidence_hits:
            keyword_counter.update(evidence_hits[:2])
        else:
            keyword_counter.update(keyword_hits_for_paper(item.paper, profile)[:2])

    top_theme_signals = _ordered_signals(theme_counter, max_signals=max_signals)
    top_category_signals = _ordered_signals(category_counter, max_signals=max_signals)
    top_keyword_signals = _ordered_signals(keyword_counter, max_signals=max_signals)
    takeaways = _build_takeaways(
        items,
        total_ranked=total_count,
        recommended_count=recommended_count,
        recommended_threshold=recommended_threshold,
        top_theme_signals=top_theme_signals,
        top_keyword_signals=top_keyword_signals,
        profile=profile,
    )

    return DailyBriefSummary(
        shown_count=shown_count,
        total_ranked=total_count,
        recommended_count=recommended_count,
        average_score=average_score,
        top_theme_signals=top_theme_signals,
        top_category_signals=top_category_signals,
        top_keyword_signals=top_keyword_signals,
        takeaways=takeaways,
    )


def build_reviewer_shortlist(
    ranked_papers: Sequence[RankedPaper],
    *,
    max_items: int = 8,
    recommended_threshold: float = DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    theme_cap: int = DEFAULT_THEME_CAP,
) -> tuple[list[RankedPaper], str]:
    if max_items <= 0 or not ranked_papers:
        return [], "Top recommendations"

    recommended = filter_ranked_papers(
        ranked_papers,
        recommended_only=True,
        recommended_threshold=recommended_threshold,
        sort_mode="score",
    )
    if recommended:
        return _balance_ranked_papers(recommended, max_items=max_items, theme_cap=theme_cap), "Top recommendations"

    score_ordered = filter_ranked_papers(ranked_papers, sort_mode="score")
    return _balance_ranked_papers(score_ordered, max_items=max_items, theme_cap=theme_cap), "Top ranked papers"


def theme_label_for_ranked_paper(item: RankedPaper) -> str:
    return theme_label_for_paper(item.paper)


def filter_ranked_papers(
    ranked_papers: Sequence[RankedPaper],
    *,
    min_score: float = 0.0,
    max_items: int | None = None,
    recommended_only: bool = False,
    recommended_threshold: float = DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    sort_mode: SortMode = "score",
) -> list[RankedPaper]:
    filtered = [item for item in ranked_papers if item.score >= min_score]
    if recommended_only:
        filtered = [item for item in filtered if is_recommended(item.score, recommended_threshold=recommended_threshold)]

    if sort_mode == "newest":
        filtered.sort(
            key=lambda item: (
                item.paper.published or item.paper.updated or date.min,
                item.score,
                item.paper.title.lower(),
            ),
            reverse=True,
        )
    else:
        filtered.sort(
            key=lambda item: (
                item.score,
                item.paper.published or item.paper.updated or date.min,
                item.paper.title.lower(),
            ),
            reverse=True,
        )

    if max_items is not None:
        return filtered[: max(max_items, 0)]
    return filtered


def is_recommended(score: float, *, recommended_threshold: float = DEFAULT_RECOMMENDED_SCORE_THRESHOLD) -> bool:
    return score >= recommended_threshold


def summarize_category_counts(
    searched_categories: Sequence[str],
    per_category_counts: dict[str, int],
) -> tuple[str, ...]:
    labels: list[str] = []
    seen: set[str] = set()
    for category in searched_categories:
        if category in seen:
            continue
        labels.append(f"{category}: {int(per_category_counts.get(category, 0))}")
        seen.add(category)
    for category in sorted(per_category_counts):
        if category in seen:
            continue
        labels.append(f"{category}: {int(per_category_counts.get(category, 0))}")
    return tuple(labels)


def _ordered_signals(counter: Counter[str], *, max_signals: int) -> tuple[BriefSignal, ...]:
    ordered = sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    return tuple(BriefSignal(label=label, count=count) for label, count in ordered[:max_signals])


def _balance_ranked_papers(
    ranked_papers: Sequence[RankedPaper],
    *,
    max_items: int,
    theme_cap: int,
) -> list[RankedPaper]:
    if max_items <= 0:
        return []

    selected: list[RankedPaper] = []
    skipped: list[RankedPaper] = []
    theme_counts: Counter[str] = Counter()
    effective_theme_cap = max(theme_cap, 0)

    for item in ranked_papers:
        theme_label = theme_label_for_ranked_paper(item)
        if effective_theme_cap and theme_counts[theme_label] >= effective_theme_cap:
            skipped.append(item)
            continue
        selected.append(item)
        theme_counts[theme_label] += 1
        if len(selected) >= max_items:
            return selected

    for item in skipped:
        selected.append(item)
        if len(selected) >= max_items:
            break
    return selected


def _build_takeaways(
    ranked_papers: Sequence[RankedPaper],
    *,
    total_ranked: int,
    recommended_count: int,
    recommended_threshold: float,
    top_theme_signals: tuple[BriefSignal, ...],
    top_keyword_signals: tuple[BriefSignal, ...],
    profile: UserInterestProfile,
) -> tuple[str, ...]:
    shown_count = len(ranked_papers)
    if shown_count == 0:
        return (
            f"Digest shortlist shows 0 of {total_ranked} ranked papers for this run.",
            "Lower the minimum score or switch to show all ranked papers to inspect the full digest outside the shortlist.",
        )

    takeaways = [
        (
            f"Digest shortlist shows {shown_count} of {total_ranked} ranked papers for this run; "
            f"{recommended_count} clear the recommendation threshold ({recommended_threshold:.2f}+)."
        ),
        (
            f"Repeated themes in the shortlist: {_signal_text(top_theme_signals)}."
            if top_theme_signals
            else "Shortlist themes are diffuse with no repeated pattern in the current reading-first lane."
        ),
        (
            f"User-interest signals in the shortlist: {_signal_text(top_keyword_signals)}."
            if top_keyword_signals
            else (
                "Matched biomedical keyword hits are sparse in the shortlist; the read-first lane is leaning more on "
                "category fit, recency, and score."
            )
        ),
    ]

    top_paper = ranked_papers[0]
    top_signals = _paper_signal_text(top_paper, profile)
    takeaways.append(
        f'{priority_label_for_score(top_paper.score)} lead: "{top_paper.paper.title}" '
        f'({top_paper.score:.3f}, {theme_label_for_ranked_paper(top_paper)}) surfaced for {top_signals}.'
    )
    return tuple(takeaways[:4])


def _signal_text(signals: Sequence[BriefSignal]) -> str:
    return ", ".join(f"{signal.label} ({signal.count})" for signal in signals)


def _paper_signal_text(item: RankedPaper, profile: UserInterestProfile) -> str:
    keyword_hits = biomedical_evidence_for_paper(item.paper, profile) or keyword_hits_for_paper(item.paper, profile)
    category_hits = category_hits_for_paper(item.paper, profile)
    lead_signals = list(keyword_hits[:2]) + list(category_hits[:2])
    if lead_signals:
        return _join_labels(tuple(dict.fromkeys(lead_signals)))
    if item.paper.categories:
        return _join_labels(tuple(category.lower() for category in item.paper.categories[:2]))
    return f"{item.paper.source} feed coverage"


def _join_labels(labels: Sequence[str]) -> str:
    values = [label for label in labels if label]
    if not values:
        return "broad feed relevance"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" and {values[-1]}"
