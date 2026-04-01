"""Selectors for demo diversification and daily exploration lanes."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from frontier_compass.common.text_normalization import tokenize
from frontier_compass.ranking.relevance import (
    DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    biomedical_evidence_for_paper,
    category_hits_for_paper,
    keyword_hits_for_paper,
    zotero_concept_hits_for_paper,
    zotero_keyword_hits_for_paper,
)
from frontier_compass.storage.schema import DailyDigest, ExplorationPolicy, RankedPaper, UserInterestProfile


DEFAULT_DAILY_EXPLORATION_THEME_PENALTY = 0.12
DEFAULT_DAILY_EXPLORATION_QBIO_BONUS = 0.10
DEFAULT_DAILY_EXPLORATION_POLICY = ExplorationPolicy(
    label="daily-adjacent-v1",
    shortlist_size=8,
    max_items=3,
    max_per_theme=1,
    min_score=0.35,
    min_biomedical_keyword=0.13,
    notes=(
        "Keeps a fixed adjacent lane outside the main score-first shortlist, limits one pick per theme, "
        "and favors biomedical adjacency over core-profile overlap."
    ),
)
DEFAULT_DAILY_EXPLORATION_LIMIT = DEFAULT_DAILY_EXPLORATION_POLICY.max_items
DEFAULT_DAILY_EXPLORATION_MIN_SCORE = DEFAULT_DAILY_EXPLORATION_POLICY.min_score
DEFAULT_DAILY_EXPLORATION_MIN_BIOMEDICAL_KEYWORD = DEFAULT_DAILY_EXPLORATION_POLICY.min_biomedical_keyword
DEFAULT_DAILY_EXPLORATION_THEME_CAP = DEFAULT_DAILY_EXPLORATION_POLICY.max_per_theme
DEFAULT_DAILY_EXPLORATION_SHORTLIST_SIZE = DEFAULT_DAILY_EXPLORATION_POLICY.shortlist_size


@dataclass(slots=True, frozen=True)
class DailyExplorationCandidate:
    item: RankedPaper
    ranked_index: int
    exploration_score: float
    theme_label: str
    theme_count: int
    biomedical_hits: tuple[str, ...]
    category_hits: tuple[str, ...]
    profile_hits: tuple[str, ...]
    zotero_keyword_hits: tuple[str, ...]
    zotero_concept_hits: tuple[str, ...]
    profile_overlap: float
    zotero_overlap: float
    qbio_supported: bool


class ExplorationSelector:
    def __init__(self, *, max_per_topic: int = 2) -> None:
        self.max_per_topic = max_per_topic

    def select(self, ranked_papers: Sequence[RankedPaper], *, limit: int = 10) -> list[RankedPaper]:
        if limit <= 0:
            return []

        topic_counts: Counter[str] = Counter()
        selected: list[RankedPaper] = []

        for ranked in ranked_papers:
            topic = self._primary_topic(ranked)
            if topic_counts[topic] >= self.max_per_topic:
                continue
            selected.append(ranked)
            topic_counts[topic] += 1
            if len(selected) >= limit:
                return selected

        for ranked in ranked_papers:
            if ranked in selected:
                continue
            selected.append(ranked)
            if len(selected) >= limit:
                break

        return selected

    def _primary_topic(self, ranked: RankedPaper) -> str:
        if ranked.paper.categories:
            topic_tokens = tokenize(ranked.paper.categories[0], min_length=3)
            if topic_tokens:
                return topic_tokens[0]
        return ranked.paper.source



def select_for_exploration(ranked_papers: Sequence[RankedPaper], *, limit: int = 10, max_per_topic: int = 2) -> list[RankedPaper]:
    return ExplorationSelector(max_per_topic=max_per_topic).select(ranked_papers, limit=limit)


def select_daily_exploration_picks(
    ranked_papers: Sequence[RankedPaper],
    profile: UserInterestProfile,
    *,
    limit: int | None = None,
    policy: ExplorationPolicy | None = None,
) -> list[RankedPaper]:
    build_reviewer_shortlist, theme_label_for_ranked_paper = _daily_brief_helpers()
    resolved_policy = _resolve_policy(policy, limit=limit)
    if resolved_policy.max_items <= 0 or not ranked_papers:
        return []

    shortlist, _shortlist_title = build_reviewer_shortlist(
        ranked_papers,
        max_items=resolved_policy.shortlist_size,
        recommended_threshold=DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    )
    shortlisted_ids = {_paper_key(item) for item in shortlist}
    shortlist_theme_counts = Counter(theme_label_for_ranked_paper(item) for item in shortlist)

    candidates = []
    for ranked_index, item in enumerate(ranked_papers):
        if _paper_key(item) in shortlisted_ids:
            continue
        candidate = _build_daily_candidate(
            item,
            profile,
            ranked_index=ranked_index,
            shortlist_theme_counts=shortlist_theme_counts,
            policy=resolved_policy,
        )
        if candidate is None:
            continue
        candidates.append(candidate)

    ordered = sorted(candidates, key=lambda candidate: (-candidate.exploration_score, candidate.ranked_index))
    selected: list[RankedPaper] = []
    selected_ids: set[str] = set()
    selected_theme_counts: Counter[str] = Counter()

    for candidate in ordered:
        if selected_theme_counts[candidate.theme_label] >= resolved_policy.max_per_theme:
            continue
        selected.append(candidate.item)
        selected_ids.add(_paper_key(candidate.item))
        selected_theme_counts[candidate.theme_label] += 1
        if len(selected) >= resolved_policy.max_items:
            return selected

    for candidate in ordered:
        candidate_id = _paper_key(candidate.item)
        if candidate_id in selected_ids:
            continue
        selected.append(candidate.item)
        selected_ids.add(candidate_id)
        if len(selected) >= resolved_policy.max_items:
            break
    return selected


def resolve_daily_exploration_picks(
    digest: DailyDigest,
    *,
    limit: int | None = None,
    policy: ExplorationPolicy | None = None,
) -> list[RankedPaper]:
    resolved_policy = _resolve_policy(policy or digest.exploration_policy, limit=limit)
    if digest.exploration_picks:
        return list(digest.exploration_picks[: max(resolved_policy.max_items, 0)])
    return select_daily_exploration_picks(
        digest.ranked,
        digest.profile,
        policy=resolved_policy,
    )


def daily_exploration_note(
    item: RankedPaper,
    *,
    ranked_papers: Sequence[RankedPaper],
    profile: UserInterestProfile,
    policy: ExplorationPolicy | None = None,
) -> str:
    build_reviewer_shortlist, theme_label_for_ranked_paper = _daily_brief_helpers()
    resolved_policy = _resolve_policy(policy)
    shortlist, _shortlist_title = build_reviewer_shortlist(
        ranked_papers,
        max_items=resolved_policy.shortlist_size,
        recommended_threshold=DEFAULT_RECOMMENDED_SCORE_THRESHOLD,
    )
    shortlist_theme_counts = Counter(theme_label_for_ranked_paper(shortlisted) for shortlisted in shortlist)
    candidate = _build_daily_candidate(
        item,
        profile,
        ranked_index=0,
        shortlist_theme_counts=shortlist_theme_counts,
        allow_any_ranked_item=True,
        policy=resolved_policy,
    )
    if candidate is None:
        theme_label = theme_label_for_ranked_paper(item).lower()
        return (
            f"Still adjacent via {theme_label}, but kept outside the main score-first shortlist under the "
            f"{resolved_policy.label} exploration lane."
        )

    adjacency = _adjacency_signal_text(candidate)
    distance = _distance_signal_text(candidate)
    return (
        f"{adjacency}, but {distance}; the {resolved_policy.label} lane keeps at most "
        f"{resolved_policy.max_items} picks and up to {resolved_policy.max_per_theme} per theme."
    )


def daily_exploration_intro(
    profile: UserInterestProfile,
    policy: ExplorationPolicy | None = None,
) -> str:
    resolved_policy = _resolve_policy(policy)
    basis = profile.basis_label or "current biomedical profile"
    return (
        f"These use the same {basis} profile, but stay outside the main score-first recommendations as a "
        f"fixed {resolved_policy.max_items}-paper adjacent lane with up to {resolved_policy.max_per_theme} per theme."
    )


def _build_daily_candidate(
    item: RankedPaper,
    profile: UserInterestProfile,
    *,
    ranked_index: int,
    shortlist_theme_counts: Counter[str],
    allow_any_ranked_item: bool = False,
    policy: ExplorationPolicy,
) -> DailyExplorationCandidate | None:
    _build_reviewer_shortlist, theme_label_for_ranked_paper = _daily_brief_helpers()
    generic_cs_penalty = float(item.facets.get("generic_cs_penalty", 0.0))
    biomedical_keyword = float(item.facets.get("biomedical_keyword", 0.0))
    if generic_cs_penalty > 0.0:
        return None
    if item.score < policy.min_score:
        return None

    biomedical_hits = biomedical_evidence_for_paper(item.paper, profile)
    category_hits = category_hits_for_paper(item.paper, profile)
    profile_hits = keyword_hits_for_paper(item.paper, profile)
    zotero_keyword_hits = zotero_keyword_hits_for_paper(item.paper, profile)
    zotero_concept_hits = zotero_concept_hits_for_paper(item.paper, profile)
    qbio_supported = _has_qbio_category(item)
    if not allow_any_ranked_item and not (
        biomedical_hits or qbio_supported or biomedical_keyword >= policy.min_biomedical_keyword
    ):
        return None

    profile_overlap = min(len(profile_hits) / 4.0, 1.0)
    zotero_overlap = min((len(zotero_keyword_hits) + len(zotero_concept_hits)) / 3.0, 1.0)
    theme_label = theme_label_for_ranked_paper(item)
    theme_count = shortlist_theme_counts.get(theme_label, 0)
    qbio_bonus = DEFAULT_DAILY_EXPLORATION_QBIO_BONUS if qbio_supported else 0.0
    theme_penalty = DEFAULT_DAILY_EXPLORATION_THEME_PENALTY * theme_count
    exploration_score = round(
        (0.55 * biomedical_keyword)
        + (0.25 * item.score)
        + qbio_bonus
        - (0.20 * profile_overlap)
        - (0.15 * zotero_overlap)
        - theme_penalty,
        4,
    )

    return DailyExplorationCandidate(
        item=item,
        ranked_index=ranked_index,
        exploration_score=exploration_score,
        theme_label=theme_label,
        theme_count=theme_count,
        biomedical_hits=biomedical_hits,
        category_hits=category_hits,
        profile_hits=profile_hits,
        zotero_keyword_hits=zotero_keyword_hits,
        zotero_concept_hits=zotero_concept_hits,
        profile_overlap=profile_overlap,
        zotero_overlap=zotero_overlap,
        qbio_supported=qbio_supported,
    )


def _adjacency_signal_text(candidate: DailyExplorationCandidate) -> str:
    if candidate.biomedical_hits:
        return f"still biomedical-adjacent via {', '.join(candidate.biomedical_hits[:2])}"
    if candidate.qbio_supported:
        return "still biomedical-adjacent via explicit q-bio category support"
    return f"still adjacent via {candidate.theme_label.lower()}"


def _distance_signal_text(candidate: DailyExplorationCandidate) -> str:
    if candidate.zotero_overlap < 0.34 and (candidate.zotero_keyword_hits or candidate.zotero_concept_hits):
        return "it has lighter Zotero overlap than the main shortlist center"
    if candidate.zotero_overlap == 0.0 and (candidate.profile_hits or candidate.theme_count > 0):
        return "it sits outside the strongest Zotero overlap in the main shortlist"
    if candidate.profile_overlap < 0.5:
        return "it has lighter profile-keyword overlap than the main shortlist center"
    if candidate.theme_count <= 0:
        return "it comes from a less-dominant theme than the main shortlist"
    return "it stays outside the strongest profile center in the main shortlist"


def _has_qbio_category(item: RankedPaper) -> bool:
    return any(category.lower() == "q-bio" or category.lower().startswith("q-bio.") for category in item.paper.categories)


def _paper_key(item: RankedPaper) -> str:
    return item.paper.display_id


def _daily_brief_helpers():
    from frontier_compass.reporting.daily_brief import build_reviewer_shortlist, theme_label_for_ranked_paper

    return build_reviewer_shortlist, theme_label_for_ranked_paper


def _resolve_policy(
    policy: ExplorationPolicy | None,
    *,
    limit: int | None = None,
) -> ExplorationPolicy:
    resolved = policy or DEFAULT_DAILY_EXPLORATION_POLICY
    if limit is None:
        return resolved
    return ExplorationPolicy(
        label=resolved.label,
        shortlist_size=resolved.shortlist_size,
        max_items=max(limit, 0),
        max_per_theme=resolved.max_per_theme,
        min_score=resolved.min_score,
        min_biomedical_keyword=resolved.min_biomedical_keyword,
        notes=resolved.notes,
    )
