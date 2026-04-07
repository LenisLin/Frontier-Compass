"""Transparent relevance scoring for candidate papers."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Mapping

from frontier_compass.common.text_normalization import tokenize
from frontier_compass.storage.schema import PaperRecord, RankedPaper, RecommendationExplanation, UserInterestProfile


DEFAULT_RECOMMENDED_SCORE_THRESHOLD = 0.45
DEFAULT_KEYWORD_WEIGHT = 0.65
DEFAULT_CATEGORY_WEIGHT = 0.2
DEFAULT_RECENCY_WEIGHT = 0.15
DEFAULT_KEYWORD_SATURATION_COUNT = 4
BIOMEDICAL_KEYWORD_SATURATION_SCORE = 3.0
GENERIC_CS_ONLY_PENALTY = 0.10
ZOTERO_KEYWORD_BONUS_MAX = 0.06
ZOTERO_CONCEPT_BONUS_MAX = 0.04
ZOTERO_BONUS_MAX = 0.10
ZOTERO_KEYWORD_SATURATION_COUNT = 3
ZOTERO_CONCEPT_SATURATION_COUNT = 2

STRONG_BIOMEDICAL_KEYWORDS = (
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
    "microscopy",
    "biomedical imaging",
    "medical imaging",
    "radiology",
    "whole-slide",
)
CONTEXTUAL_BIOMEDICAL_KEYWORDS = (
    "biomedical",
    "medical",
    "clinical",
)
AMBIGUOUS_AI_KEYWORDS = (
    "multimodal",
    "foundation model",
    "healthcare",
)
SUPPORTING_CS_CATEGORIES = frozenset(
    (
        "cs.cv",
        "cs.lg",
        "cs.ai",
        "cs.cl",
        "stat.ml",
        "eess.iv",
    )
)
KEYWORD_ALIAS_MAP = {
    "single cell": "single-cell",
    "single-cell": "single-cell",
    "multi omics": "multi-omics",
    "multi-omics": "multi-omics",
    "whole slide": "whole-slide",
    "whole-slide": "whole-slide",
}
KEYWORD_VARIANTS = {
    "single-cell": ("single-cell", "single cell"),
    "multi-omics": ("multi-omics", "multi omics"),
    "whole-slide": ("whole-slide", "whole slide"),
}
BIOMEDICAL_KEYWORD_WEIGHTS = {
    **{keyword: 1.0 for keyword in STRONG_BIOMEDICAL_KEYWORDS},
    **{keyword: 0.4 for keyword in CONTEXTUAL_BIOMEDICAL_KEYWORDS},
    **{keyword: 0.15 for keyword in AMBIGUOUS_AI_KEYWORDS},
}


@dataclass(slots=True, frozen=True)
class BiomedicalKeywordAnalysis:
    profile_keyword_hits: tuple[str, ...]
    biomedical_evidence_hits: tuple[str, ...]
    strong_hits: tuple[str, ...]
    contextual_hits: tuple[str, ...]
    ambiguous_hits: tuple[str, ...]
    keyword_score: float
    biomedical_keyword_score: float


class RelevanceRanker:
    def __init__(
        self,
        *,
        keyword_weight: float = DEFAULT_KEYWORD_WEIGHT,
        category_weight: float = DEFAULT_CATEGORY_WEIGHT,
        recency_weight: float = DEFAULT_RECENCY_WEIGHT,
    ) -> None:
        total = keyword_weight + category_weight + recency_weight
        if total <= 0:
            raise ValueError("ranking weights must sum to a positive value")
        self.keyword_weight = keyword_weight / total
        self.category_weight = category_weight / total
        self.recency_weight = recency_weight / total

    def score(self, paper: PaperRecord, profile: UserInterestProfile, *, today: date | None = None) -> RankedPaper:
        today = today or date.today()
        uses_biomedical_calibration = _uses_biomedical_calibration(profile)

        keyword_hits = keyword_hits_for_paper(paper, profile)
        biomedical_hits: tuple[str, ...] = ()
        ambiguous_hits: tuple[str, ...] = ()
        biomedical_keyword_score = 0.0
        generic_cs_penalty = 0.0
        zotero_keyword_hits: tuple[str, ...] = ()
        zotero_concept_hits: tuple[str, ...] = ()
        zotero_keyword_bonus = 0.0
        zotero_concept_bonus = 0.0
        zotero_bonus = 0.0
        has_primary_biomedical_category = _has_primary_biomedical_category(paper.categories)
        has_strong_biomedical_hit = False
        paper_terms = _expanded_terms(paper.normalized_text(), min_length=4)
        retrieval_support_origin, retrieval_support_labels, retrieval_support_terms = zotero_retrieval_support_for_paper(
            paper
        )

        if uses_biomedical_calibration:
            keyword_analysis = _biomedical_keyword_analysis(paper, profile)
            keyword_hits = keyword_analysis.profile_keyword_hits
            biomedical_hits = keyword_analysis.biomedical_evidence_hits
            ambiguous_hits = keyword_analysis.ambiguous_hits
            keyword_score = keyword_analysis.keyword_score
            biomedical_keyword_score = keyword_analysis.biomedical_keyword_score
            has_strong_biomedical_hit = bool(keyword_analysis.strong_hits)
            category_hits, category_score = _biomedical_category_hits(
                paper,
                profile,
                has_strong_biomedical_hit=has_strong_biomedical_hit,
            )
            generic_cs_penalty = _generic_cs_penalty(
                paper,
                has_primary_biomedical_category=has_primary_biomedical_category,
                has_strong_biomedical_hit=has_strong_biomedical_hit,
            )
        else:
            keyword_score = _keyword_score(keyword_hits, profile.keywords)
            biomedical_keyword_score = keyword_score
            category_hits, category_score = _generic_category_hits(paper, profile)

        if uses_biomedical_calibration and (has_primary_biomedical_category or has_strong_biomedical_hit):
            zotero_keyword_hits = _matched_keywords(profile.zotero_keywords, paper_terms)
            zotero_concept_hits = _matched_keywords(profile.zotero_concepts, paper_terms)
            zotero_keyword_bonus = _bounded_overlap_bonus(
                zotero_keyword_hits,
                profile.zotero_keywords,
                saturation_count=ZOTERO_KEYWORD_SATURATION_COUNT,
                max_bonus=ZOTERO_KEYWORD_BONUS_MAX,
            )
            zotero_concept_bonus = _bounded_overlap_bonus(
                zotero_concept_hits,
                profile.zotero_concepts,
                saturation_count=ZOTERO_CONCEPT_SATURATION_COUNT,
                max_bonus=ZOTERO_CONCEPT_BONUS_MAX,
            )
            zotero_bonus = min(zotero_keyword_bonus + zotero_concept_bonus, ZOTERO_BONUS_MAX)

        recency_score = _recency_score(paper.published or paper.updated, today)
        raw_score = (
            self.keyword_weight * keyword_score
            + self.category_weight * category_score
            + self.recency_weight * recency_score
            + zotero_bonus
            - generic_cs_penalty
        )
        score = round(min(max(raw_score, 0.0), 1.0), 4)

        reasons = _build_reasons(
            paper,
            keyword_hits=keyword_hits,
            biomedical_hits=biomedical_hits,
            ambiguous_hits=ambiguous_hits,
            category_hits=category_hits,
            zotero_keyword_hits=zotero_keyword_hits,
            zotero_concept_hits=zotero_concept_hits,
            retrieval_support_origin=retrieval_support_origin,
            retrieval_support_terms=retrieval_support_terms,
            recency_score=recency_score,
            uses_biomedical_calibration=uses_biomedical_calibration,
            generic_cs_penalty=generic_cs_penalty,
        )
        baseline_keyword_hits = biomedical_hits if uses_biomedical_calibration else keyword_hits
        explanation = RecommendationExplanation(
            total_score=score,
            baseline_contribution=round(self.keyword_weight * keyword_score, 4),
            category_contribution=round(self.category_weight * category_score, 4),
            recency_contribution=round(self.recency_weight * recency_score, 4),
            zotero_bonus_contribution=round(zotero_bonus, 4),
            generic_cs_penalty_contribution=round(generic_cs_penalty, 4),
            baseline_keyword_hits=baseline_keyword_hits,
            category_hits=category_hits,
            zotero_keyword_hits=zotero_keyword_hits,
            zotero_concept_hits=zotero_concept_hits,
            zotero_effect=zotero_effect_label(
                zotero_active=profile.zotero_active,
                zotero_bonus=zotero_bonus,
            ),
            zotero_active=profile.zotero_active,
            retrieval_support_origin=retrieval_support_origin,
            retrieval_support_labels=retrieval_support_labels,
            retrieval_support_terms=retrieval_support_terms,
        )

        return RankedPaper(
            paper=paper,
            score=score,
            reasons=reasons,
            facets={
                "keyword": round(keyword_score, 4),
                "category": round(category_score, 4),
                "recency": round(recency_score, 4),
                "biomedical_keyword": round(biomedical_keyword_score, 4),
                "generic_cs_penalty": round(generic_cs_penalty, 4),
                "zotero_keyword": round(zotero_keyword_bonus, 4),
                "zotero_concept": round(zotero_concept_bonus, 4),
                "zotero_bonus": round(zotero_bonus, 4),
            },
            recommendation_summary=_build_recommendation_summary(
                paper,
                score=score,
                keyword_hits=keyword_hits,
                biomedical_hits=biomedical_hits,
                ambiguous_hits=ambiguous_hits,
                category_hits=category_hits,
                zotero_keyword_hits=zotero_keyword_hits,
                zotero_concept_hits=zotero_concept_hits,
                retrieval_support_origin=retrieval_support_origin,
                retrieval_support_labels=retrieval_support_labels,
                retrieval_support_terms=retrieval_support_terms,
                recency_score=recency_score,
                uses_biomedical_calibration=uses_biomedical_calibration,
                generic_cs_penalty=generic_cs_penalty,
            ),
            explanation=explanation,
        )

    def rank(
        self,
        papers: Iterable[PaperRecord],
        profile: UserInterestProfile,
        *,
        limit: int | None = None,
        today: date | None = None,
    ) -> list[RankedPaper]:
        ranked = [self.score(paper, profile, today=today) for paper in papers]
        ranked.sort(
            key=lambda item: (
                item.score,
                item.paper.published or item.paper.updated or date.min,
                item.paper.title.lower(),
            ),
            reverse=True,
        )
        if limit is not None:
            return ranked[:limit]
        return ranked


def rank_papers(
    papers: Iterable[PaperRecord], profile: UserInterestProfile, *, limit: int | None = None, today: date | None = None
) -> list[RankedPaper]:
    return RelevanceRanker().rank(papers, profile, limit=limit, today=today)


def keyword_hits_for_paper(paper: PaperRecord, profile: UserInterestProfile) -> tuple[str, ...]:
    paper_terms = _expanded_terms(paper.normalized_text(), min_length=4)
    return _matched_keywords(profile.keywords, paper_terms)


def biomedical_evidence_for_paper(paper: PaperRecord, profile: UserInterestProfile) -> tuple[str, ...]:
    if not _uses_biomedical_calibration(profile):
        return keyword_hits_for_paper(paper, profile)
    return _biomedical_keyword_analysis(paper, profile).biomedical_evidence_hits


def zotero_keyword_hits_for_paper(paper: PaperRecord, profile: UserInterestProfile) -> tuple[str, ...]:
    paper_terms = _expanded_terms(paper.normalized_text(), min_length=4)
    return _matched_keywords(profile.zotero_keywords, paper_terms)


def zotero_concept_hits_for_paper(paper: PaperRecord, profile: UserInterestProfile) -> tuple[str, ...]:
    paper_terms = _expanded_terms(paper.normalized_text(), min_length=4)
    return _matched_keywords(profile.zotero_concepts, paper_terms)


def zotero_retrieval_support_for_paper(paper: PaperRecord) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    raw_support = paper.source_metadata.get("retrieval_support")
    if not isinstance(raw_support, list):
        return "", (), ()

    labels: list[str] = []
    terms: list[str] = []
    seen_labels: set[str] = set()
    seen_terms: set[str] = set()
    for item in raw_support:
        if not isinstance(item, Mapping):
            continue
        origin = str(item.get("origin", "")).strip().lower()
        if origin != "zotero":
            continue
        label = str(item.get("label", "")).strip()
        if label and label not in seen_labels:
            labels.append(label)
            seen_labels.add(label)
        raw_terms = item.get("terms", ())
        if isinstance(raw_terms, (list, tuple)):
            for term in raw_terms:
                normalized_term = str(term).strip()
                if not normalized_term or normalized_term in seen_terms:
                    continue
                terms.append(normalized_term)
                seen_terms.add(normalized_term)
    if not labels and not terms:
        return "", (), ()
    return "zotero", tuple(labels), tuple(terms)


def category_hits_for_paper(paper: PaperRecord, profile: UserInterestProfile) -> tuple[str, ...]:
    if _uses_biomedical_calibration(profile):
        keyword_analysis = _biomedical_keyword_analysis(paper, profile)
        return _biomedical_category_hits(
            paper,
            profile,
            has_strong_biomedical_hit=bool(keyword_analysis.strong_hits),
        )[0]
    return _generic_category_hits(paper, profile)[0]


def priority_label_for_score(score: float) -> str:
    if score >= 0.7:
        return "Priority review"
    if score >= DEFAULT_RECOMMENDED_SCORE_THRESHOLD:
        return "Recommended"
    return "Watchlist"


def zotero_effect_label(*, zotero_active: bool, zotero_bonus: float) -> str:
    if not zotero_active:
        return "inactive"
    if zotero_bonus <= 0.0:
        return "none"
    if zotero_bonus < 0.05:
        return "mild"
    return "strong"


def zotero_effect_badge_text(effect: str) -> str:
    labels = {
        "inactive": "Zotero: inactive",
        "none": "Zotero: none",
        "mild": "Zotero: mild",
        "strong": "Zotero: strong",
    }
    return labels.get(effect, f"Zotero: {effect or 'inactive'}")


def explanation_summary_line(explanation: RecommendationExplanation) -> str:
    parts: list[str] = []
    if explanation.baseline_keyword_hits:
        parts.append(f"baseline: {', '.join(explanation.baseline_keyword_hits[:2])}")
    if explanation.category_hits:
        parts.append(f"category: {', '.join(explanation.category_hits[:2])}")
    if explanation.retrieval_support_origin == "zotero" and explanation.retrieval_support_terms:
        parts.append(f"retrieval: Zotero hint ({', '.join(explanation.retrieval_support_terms[:2])})")
    if explanation.zotero_effect in {"mild", "strong"}:
        zotero_hits = _merge_signal_hits(explanation.zotero_concept_hits, explanation.zotero_keyword_hits)
        zotero_text = explanation.zotero_effect
        if zotero_hits:
            zotero_text += f" ({', '.join(zotero_hits[:2])})"
        parts.append(f"zotero: {zotero_text}")
    if explanation.generic_cs_penalty_contribution > 0.0:
        parts.append("penalty: generic CS")
    if explanation.recency_contribution >= 0.12:
        parts.append("timing: same-day")
    elif explanation.recency_contribution >= 0.075:
        parts.append("timing: recent")
    if not parts:
        return "score sourced from the current ranking pipeline"
    return " | ".join(parts)


def why_this_paper_line(explanation: RecommendationExplanation) -> str:
    parts: list[str] = []
    if explanation.baseline_keyword_hits:
        parts.append(f"matched biomedical signals: {', '.join(explanation.baseline_keyword_hits[:2])}")
    if explanation.category_hits:
        parts.append(f"held category support: {', '.join(explanation.category_hits[:2])}")
    if explanation.retrieval_support_origin == "zotero":
        if explanation.retrieval_support_terms:
            parts.append(f"surfaced via Zotero retrieval hint: {', '.join(explanation.retrieval_support_terms[:2])}")
        else:
            parts.append("surfaced via a Zotero retrieval hint")
    if not parts and explanation.zotero_effect in {"mild", "strong"}:
        zotero_hits = _merge_signal_hits(explanation.zotero_concept_hits, explanation.zotero_keyword_hits)
        if zotero_hits:
            parts.append(f"picked up secondary Zotero support: {', '.join(zotero_hits[:2])}")
        else:
            parts.append("picked up secondary Zotero support")
    if not parts and explanation.generic_cs_penalty_contribution > 0.0:
        return "Stayed visible mainly on recency despite a generic-CS penalty."
    if not parts:
        return "Selected by the current deterministic ranking pipeline."
    return "; ".join(parts[:3])


def score_explanation_line(explanation: RecommendationExplanation) -> str:
    positive_parts = [
        f"{label.lower()} {value:+.3f}"
        for label, value in explanation_breakdown_rows(explanation)
        if value > 0.0
    ]
    if positive_parts:
        line = "Score leans on " + _join_text_parts(positive_parts[:3]) + "."
    else:
        line = "Score stays modest because the current ranking signals are light."
    if explanation.generic_cs_penalty_contribution > 0.0:
        line = (
            line[:-1]
            + f"; generic-CS penalty {-explanation.generic_cs_penalty_contribution:+.3f}."
        )
    return line


def interest_relevance_line(explanation: RecommendationExplanation) -> str:
    parts: list[str] = []
    if explanation.baseline_keyword_hits:
        parts.append(f"keyword overlap: {', '.join(explanation.baseline_keyword_hits[:3])}")
    if explanation.category_hits:
        parts.append(f"category fit: {', '.join(explanation.category_hits[:3])}")
    zotero_hits = _merge_signal_hits(explanation.zotero_concept_hits, explanation.zotero_keyword_hits)
    if zotero_hits:
        parts.append(f"Zotero overlap: {', '.join(zotero_hits[:3])}")
    elif explanation.retrieval_support_origin == "zotero" and explanation.retrieval_support_terms:
        parts.append(f"Zotero retrieval hint: {', '.join(explanation.retrieval_support_terms[:3])}")
    if parts:
        return "; ".join(parts[:3])
    if explanation.generic_cs_penalty_contribution > 0.0:
        return "Interest overlap is light; this stays visible more on timing than on strong biomedical fit."
    return "Interest overlap is broad rather than keyword-specific in the current profile."


def explanation_breakdown_rows(explanation: RecommendationExplanation) -> tuple[tuple[str, float], ...]:
    return (
        ("Biomedical baseline", explanation.baseline_contribution),
        ("Category support", explanation.category_contribution),
        ("Recency", explanation.recency_contribution),
        ("Zotero bonus", explanation.zotero_bonus_contribution),
        ("Generic-CS penalty", -explanation.generic_cs_penalty_contribution),
    )


def explanation_detail_lines(explanation: RecommendationExplanation) -> tuple[str, ...]:
    lines: list[str] = []
    if explanation.baseline_keyword_hits:
        lines.append(f"Baseline matches: {', '.join(explanation.baseline_keyword_hits[:4])}")
    if explanation.category_hits:
        lines.append(f"Category matches: {', '.join(explanation.category_hits[:4])}")
    zotero_hits = _merge_signal_hits(explanation.zotero_concept_hits, explanation.zotero_keyword_hits)
    if zotero_hits:
        lines.append(f"Zotero matches: {', '.join(zotero_hits[:4])}")
    elif explanation.zotero_active:
        lines.append("Zotero matches: none")
    else:
        lines.append("Zotero matches: inactive")
    if explanation.retrieval_support_origin == "zotero":
        if explanation.retrieval_support_terms:
            lines.append(
                "Candidate expansion: surfaced by Zotero retrieval hint via "
                + ", ".join(explanation.retrieval_support_terms[:4])
            )
        else:
            lines.append("Candidate expansion: surfaced by Zotero retrieval hint")
    if explanation.generic_cs_penalty_contribution > 0.0:
        lines.append("Generic-CS penalty applied because no strong biomedical evidence was found.")
    return tuple(lines)


def _join_text_parts(parts: Iterable[str]) -> str:
    values = [part for part in parts if part]
    if not values:
        return ""
    if len(values) == 1:
        return values[0]
    if len(values) == 2:
        return f"{values[0]} and {values[1]}"
    return ", ".join(values[:-1]) + f", and {values[-1]}"


def recommendation_explanation_for_ranked_paper(
    item: RankedPaper,
    *,
    profile: UserInterestProfile | None = None,
) -> RecommendationExplanation:
    if item.explanation is not None:
        return item.explanation

    keyword_weight, category_weight, recency_weight = _default_component_weights()
    keyword_score = float(item.facets.get("keyword", item.facets.get("biomedical_keyword", 0.0)))
    category_score = float(item.facets.get("category", 0.0))
    recency_score = float(item.facets.get("recency", 0.0))
    zotero_bonus = float(item.facets.get("zotero_bonus", 0.0))
    generic_cs_penalty = float(item.facets.get("generic_cs_penalty", 0.0))

    if profile is not None:
        baseline_keyword_hits = biomedical_evidence_for_paper(item.paper, profile)
        category_hits = category_hits_for_paper(item.paper, profile)
        zotero_keyword_hits = zotero_keyword_hits_for_paper(item.paper, profile)
        zotero_concept_hits = zotero_concept_hits_for_paper(item.paper, profile)
        zotero_active = profile.zotero_active
    else:
        baseline_keyword_hits = _reason_hits(item.reasons, "biomedical evidence: ", "keyword overlap: ", "broad ai terms: ")
        category_hits = _reason_hits(item.reasons, "topic match: ")
        zotero_keyword_hits = _reason_hits(item.reasons, "zotero profile match: ")
        zotero_concept_hits = ()
        zotero_active = bool(zotero_bonus > 0.0 or zotero_keyword_hits or zotero_concept_hits)
    retrieval_support_origin, retrieval_support_labels, retrieval_support_terms = zotero_retrieval_support_for_paper(
        item.paper
    )

    return RecommendationExplanation(
        total_score=item.score,
        baseline_contribution=round(keyword_weight * keyword_score, 4),
        category_contribution=round(category_weight * category_score, 4),
        recency_contribution=round(recency_weight * recency_score, 4),
        zotero_bonus_contribution=round(zotero_bonus, 4),
        generic_cs_penalty_contribution=round(generic_cs_penalty, 4),
        baseline_keyword_hits=baseline_keyword_hits,
        category_hits=category_hits,
        zotero_keyword_hits=zotero_keyword_hits,
        zotero_concept_hits=zotero_concept_hits,
        zotero_effect=zotero_effect_label(zotero_active=zotero_active, zotero_bonus=zotero_bonus),
        zotero_active=zotero_active,
        retrieval_support_origin=retrieval_support_origin,
        retrieval_support_labels=retrieval_support_labels,
        retrieval_support_terms=retrieval_support_terms,
    )


def _keyword_score(keyword_hits: tuple[str, ...], keywords: tuple[str, ...]) -> float:
    if not keywords:
        return 0.0
    saturation = max(min(len(keywords), DEFAULT_KEYWORD_SATURATION_COUNT), 1)
    return min(len(keyword_hits) / saturation, 1.0)


def _bounded_overlap_bonus(
    hits: tuple[str, ...],
    reference_keywords: tuple[str, ...],
    *,
    saturation_count: int,
    max_bonus: float,
) -> float:
    if not hits or not reference_keywords or max_bonus <= 0:
        return 0.0
    saturation = max(min(len(reference_keywords), saturation_count), 1)
    return min(len(hits) / saturation, 1.0) * max_bonus


def _generic_category_hits(paper: PaperRecord, profile: UserInterestProfile) -> tuple[tuple[str, ...], float]:
    weights = {key.lower(): float(value) for key, value in profile.category_weights.items()}
    exact_categories = {category.lower() for category in paper.categories if category}
    token_categories = _expanded_terms(" ".join(paper.categories), min_length=2)
    matched = tuple(key for key in weights if _category_matches(key, exact_categories, token_categories))
    score = min(sum(weights[key] for key in matched), 1.0)
    return matched, score


def _biomedical_category_hits(
    paper: PaperRecord,
    profile: UserInterestProfile,
    *,
    has_strong_biomedical_hit: bool,
) -> tuple[tuple[str, ...], float]:
    weights = {key.lower(): float(value) for key, value in profile.category_weights.items()}
    exact_categories = {category.lower() for category in paper.categories if category}
    token_categories = _expanded_terms(" ".join(paper.categories), min_length=2)
    has_primary_biomedical_category = _has_primary_biomedical_category(paper.categories)

    primary_hits: list[str] = []
    supporting_hits: list[str] = []
    primary_score = 0.0
    supporting_score = 0.0

    for key, weight in weights.items():
        if not _category_matches(key, exact_categories, token_categories):
            continue
        if _is_primary_biomedical_category_key(key):
            primary_hits.append(key)
            primary_score += weight
            continue
        if key in SUPPORTING_CS_CATEGORIES:
            supporting_hits.append(key)
            supporting_score += weight
            continue
        primary_hits.append(key)
        primary_score += weight

    if not (has_primary_biomedical_category or has_strong_biomedical_hit):
        supporting_score = min(supporting_score, 0.05)

    matched = tuple(primary_hits + supporting_hits)
    score = min(primary_score + supporting_score, 1.0)
    return matched, score


def _biomedical_keyword_analysis(paper: PaperRecord, profile: UserInterestProfile) -> BiomedicalKeywordAnalysis:
    paper_terms = _expanded_terms(paper.normalized_text(), min_length=4)
    profile_keyword_hits = _matched_keywords(profile.keywords, paper_terms)
    strong_hits = _matched_keywords(STRONG_BIOMEDICAL_KEYWORDS, paper_terms)
    contextual_hits = _matched_keywords(CONTEXTUAL_BIOMEDICAL_KEYWORDS, paper_terms)
    ambiguous_hits = _matched_keywords(AMBIGUOUS_AI_KEYWORDS, paper_terms)
    biomedical_evidence_hits = strong_hits + tuple(hit for hit in contextual_hits if hit not in strong_hits)
    keyword_score = _weighted_keyword_score((*biomedical_evidence_hits, *ambiguous_hits))
    biomedical_keyword_score = _weighted_keyword_score(biomedical_evidence_hits)
    return BiomedicalKeywordAnalysis(
        profile_keyword_hits=profile_keyword_hits,
        biomedical_evidence_hits=biomedical_evidence_hits,
        strong_hits=strong_hits,
        contextual_hits=contextual_hits,
        ambiguous_hits=ambiguous_hits,
        keyword_score=keyword_score,
        biomedical_keyword_score=biomedical_keyword_score,
    )


def _weighted_keyword_score(keyword_hits: Iterable[str]) -> float:
    weighted_total = 0.0
    seen: set[str] = set()
    for keyword in keyword_hits:
        canonical = _canonicalize_keyword(keyword)
        if canonical in seen:
            continue
        weighted_total += BIOMEDICAL_KEYWORD_WEIGHTS.get(canonical, 0.0)
        seen.add(canonical)
    return min(weighted_total / BIOMEDICAL_KEYWORD_SATURATION_SCORE, 1.0)


def _generic_cs_penalty(
    paper: PaperRecord,
    *,
    has_primary_biomedical_category: bool,
    has_strong_biomedical_hit: bool,
) -> float:
    if has_primary_biomedical_category or has_strong_biomedical_hit:
        return 0.0
    exact_categories = {category.lower() for category in paper.categories if category}
    if any(_is_supporting_cs_category(category) for category in exact_categories):
        return GENERIC_CS_ONLY_PENALTY
    return 0.0


def _matched_keywords(keywords: Iterable[str], paper_terms: set[str]) -> tuple[str, ...]:
    matched: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        canonical = _canonicalize_keyword(keyword)
        if canonical in seen or not _keyword_matches(canonical, paper_terms):
            continue
        matched.append(canonical)
        seen.add(canonical)
    return tuple(matched)


def _keyword_matches(keyword: str, paper_terms: set[str]) -> bool:
    for variant in _keyword_variants(keyword):
        keyword_terms = _expanded_terms(variant, min_length=4)
        if keyword_terms and keyword_terms.issubset(paper_terms):
            return True
    return False


def _expanded_terms(text: str, *, min_length: int) -> set[str]:
    tokens = set(tokenize(text, min_length=min_length))
    expanded = set(tokens)
    for token in tokens:
        if "-" not in token:
            continue
        for part in token.split("-"):
            if len(part) >= min_length:
                expanded.add(part)
        squashed = token.replace("-", "")
        if len(squashed) >= min_length:
            expanded.add(squashed)
    return expanded


def _category_matches(key: str, exact_categories: set[str], token_categories: set[str]) -> bool:
    if key in exact_categories or key in token_categories:
        return True
    return any(category.startswith(f"{key}.") for category in exact_categories)


def _build_reasons(
    paper: PaperRecord,
    *,
    keyword_hits: tuple[str, ...],
    biomedical_hits: tuple[str, ...],
    ambiguous_hits: tuple[str, ...],
    category_hits: tuple[str, ...],
    zotero_keyword_hits: tuple[str, ...],
    zotero_concept_hits: tuple[str, ...],
    retrieval_support_origin: str,
    retrieval_support_terms: tuple[str, ...],
    recency_score: float,
    uses_biomedical_calibration: bool,
    generic_cs_penalty: float,
) -> tuple[str, ...]:
    reasons: list[str] = []
    if uses_biomedical_calibration:
        if biomedical_hits:
            reasons.append(f"biomedical evidence: {', '.join(biomedical_hits[:3])}")
        elif ambiguous_hits:
            reasons.append(f"broad ai terms: {', '.join(ambiguous_hits[:3])}")
    elif keyword_hits:
        reasons.append(f"keyword overlap: {', '.join(keyword_hits[:3])}")

    zotero_hits = _merge_signal_hits(zotero_concept_hits, zotero_keyword_hits)
    if zotero_hits:
        reasons.append(f"zotero profile match: {', '.join(zotero_hits[:3])}")
    if retrieval_support_origin == "zotero" and retrieval_support_terms:
        reasons.append(f"retrieval expansion: Zotero hint via {', '.join(retrieval_support_terms[:3])}")
    if category_hits:
        reasons.append(f"topic match: {', '.join(category_hits[:3])}")
    if generic_cs_penalty > 0:
        reasons.append("broad cs penalty: no strong biomedical evidence")
    if (paper.published or paper.updated) is not None and recency_score >= 0.5:
        reasons.append(f"recent preprint: {(paper.published or paper.updated).isoformat()}")
    if not reasons:
        reasons.append(f"source signal only: {paper.source}")
    return tuple(reasons)


def _build_recommendation_summary(
    paper: PaperRecord,
    *,
    score: float,
    keyword_hits: tuple[str, ...],
    biomedical_hits: tuple[str, ...],
    ambiguous_hits: tuple[str, ...],
    category_hits: tuple[str, ...],
    zotero_keyword_hits: tuple[str, ...],
    zotero_concept_hits: tuple[str, ...],
    retrieval_support_origin: str,
    retrieval_support_labels: tuple[str, ...],
    retrieval_support_terms: tuple[str, ...],
    recency_score: float,
    uses_biomedical_calibration: bool,
    generic_cs_penalty: float,
) -> str:
    lead = priority_label_for_score(score)
    signal_text = _signal_note(
        paper,
        keyword_hits=keyword_hits,
        biomedical_hits=biomedical_hits,
        ambiguous_hits=ambiguous_hits,
        category_hits=category_hits,
        zotero_keyword_hits=zotero_keyword_hits,
        zotero_concept_hits=zotero_concept_hits,
        retrieval_support_origin=retrieval_support_origin,
        retrieval_support_labels=retrieval_support_labels,
        retrieval_support_terms=retrieval_support_terms,
        recency_score=recency_score,
        uses_biomedical_calibration=uses_biomedical_calibration,
        generic_cs_penalty=generic_cs_penalty,
    )
    author_text = paper.authors[0] if paper.authors else "unknown authors"
    date_value = paper.updated or paper.published
    date_text = date_value.isoformat() if date_value else "unknown date"
    abstract_text = _summary_snippet(paper.summary, max_length=240)
    return f"{lead}. Surfaced for {signal_text}. In brief: {abstract_text} Posted {date_text} by {author_text}."


def _signal_note(
    paper: PaperRecord,
    *,
    keyword_hits: tuple[str, ...],
    biomedical_hits: tuple[str, ...],
    ambiguous_hits: tuple[str, ...],
    category_hits: tuple[str, ...],
    zotero_keyword_hits: tuple[str, ...],
    zotero_concept_hits: tuple[str, ...],
    retrieval_support_origin: str,
    retrieval_support_labels: tuple[str, ...],
    retrieval_support_terms: tuple[str, ...],
    recency_score: float,
    uses_biomedical_calibration: bool,
    generic_cs_penalty: float,
) -> str:
    signal_parts: list[str] = []
    if uses_biomedical_calibration:
        if biomedical_hits:
            signal_parts.append(f"biomedical evidence in {_join_labels(biomedical_hits[:2])}")
        elif ambiguous_hits:
            signal_parts.append(f"broad AI cues like {_join_labels(ambiguous_hits[:2])}")
    elif keyword_hits:
        signal_parts.append(f"{_join_labels(keyword_hits[:2])} keyword overlap")

    zotero_hits = _merge_signal_hits(zotero_concept_hits, zotero_keyword_hits)
    if zotero_hits:
        signal_parts.append(f"Zotero profile overlap in {_join_labels(zotero_hits[:2])}")
    if retrieval_support_origin == "zotero" and retrieval_support_terms:
        hint_text = _join_labels(retrieval_support_terms[:2])
        if retrieval_support_labels:
            hint_text += f" ({retrieval_support_labels[0]})"
        signal_parts.append(f"candidate expansion from Zotero hint {hint_text}")
    if category_hits:
        signal_parts.append(f"{_join_labels(category_hits[:2])} category support")
    if generic_cs_penalty > 0:
        signal_parts.append("reduced credit for generic CS categories")
    if not signal_parts and paper.categories:
        signal_parts.append(f"{_join_labels(paper.categories[:2])} category coverage")
    if recency_score >= 0.8:
        signal_parts.append("same-day preprint timing")
    elif recency_score >= 0.5:
        signal_parts.append("recent preprint timing")
    if not signal_parts:
        signal_parts.append(f"{paper.source} feed coverage")
    return ", ".join(signal_parts)


def _summary_snippet(text: str, *, max_length: int = 320) -> str:
    cleaned = re.sub(r"\s+", " ", text).strip()
    if not cleaned:
        return "The feed entry does not include an abstract summary yet."
    sentences = re.split(r"(?<=[.!?])\s+", cleaned)
    snippet = sentences[0]
    if len(snippet) < 140 and len(sentences) > 1:
        snippet = f"{snippet} {sentences[1]}".strip()
    if len(snippet) > max_length:
        snippet = snippet[: max_length - 3].rsplit(" ", 1)[0] + "..."
    if snippet and snippet[-1] not in ".!?":
        snippet += "."
    return snippet


def _merge_signal_hits(*groups: tuple[str, ...]) -> tuple[str, ...]:
    merged: list[str] = []
    seen: set[str] = set()
    for group in groups:
        for value in group:
            canonical = _canonicalize_keyword(value)
            if canonical in seen:
                continue
            merged.append(canonical)
            seen.add(canonical)
    return tuple(merged)


def _join_labels(labels: Iterable[str]) -> str:
    values = [label for label in labels if label]
    if not values:
        return "broad biomedical relevance"
    if len(values) == 1:
        return values[0]
    return ", ".join(values[:-1]) + f" and {values[-1]}"


def _recency_score(published: date | None, today: date) -> float:
    if published is None:
        return 0.1
    days_old = max((today - published).days, 0)
    return max(0.0, 1.0 - (days_old / 45.0))


def _canonicalize_keyword(keyword: str) -> str:
    normalized = re.sub(r"\s+", " ", keyword.strip().lower())
    return KEYWORD_ALIAS_MAP.get(normalized, normalized)


def _keyword_variants(keyword: str) -> tuple[str, ...]:
    canonical = _canonicalize_keyword(keyword)
    return KEYWORD_VARIANTS.get(canonical, (canonical,))


def _uses_biomedical_calibration(profile: UserInterestProfile) -> bool:
    weight_keys = {key.lower() for key in profile.category_weights}
    if any(_is_primary_biomedical_category_key(key) for key in weight_keys):
        return True
    return "deterministic biomedical arxiv scouting profile" in profile.notes.lower()


def _has_primary_biomedical_category(categories: Iterable[str]) -> bool:
    return any(_is_primary_biomedical_category_key(category.lower()) for category in categories if category)


def _is_primary_biomedical_category_key(category: str) -> bool:
    normalized = category.lower()
    return normalized == "q-bio" or normalized.startswith("q-bio.")


def _is_supporting_cs_category(category: str) -> bool:
    normalized = category.lower()
    return any(normalized == key or normalized.startswith(f"{key}.") for key in SUPPORTING_CS_CATEGORIES)


def _default_component_weights() -> tuple[float, float, float]:
    total = DEFAULT_KEYWORD_WEIGHT + DEFAULT_CATEGORY_WEIGHT + DEFAULT_RECENCY_WEIGHT
    return (
        DEFAULT_KEYWORD_WEIGHT / total,
        DEFAULT_CATEGORY_WEIGHT / total,
        DEFAULT_RECENCY_WEIGHT / total,
    )


def _reason_hits(reasons: Iterable[str], *prefixes: str) -> tuple[str, ...]:
    for reason in reasons:
        for prefix in prefixes:
            if not reason.startswith(prefix):
                continue
            return tuple(value.strip() for value in reason[len(prefix) :].split(",") if value.strip())
    return ()
