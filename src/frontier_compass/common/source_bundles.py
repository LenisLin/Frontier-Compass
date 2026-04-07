"""Local source-bundle registry and deterministic paper filtering helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from frontier_compass.common.text_normalization import slugify
from frontier_compass.storage.schema import PaperRecord


DEFAULT_SOURCE_BUNDLES_PATH = Path("configs/source_bundles.json")
SOURCE_BUNDLE_BIOMEDICAL = "biomedical"
SOURCE_BUNDLE_AI_FOR_MEDICINE = "ai-for-medicine"
OFFICIAL_SOURCE_BUNDLE_IDS = (
    SOURCE_BUNDLE_BIOMEDICAL,
    SOURCE_BUNDLE_AI_FOR_MEDICINE,
)
DEFAULT_PUBLIC_SOURCE_BUNDLE = SOURCE_BUNDLE_BIOMEDICAL
DEFAULT_ENABLED_SOURCES = ("arxiv", "biorxiv")

_BIOMEDICAL_MATCH_TERMS = (
    "biomedical",
    "bioinformatics",
    "biomarker",
    "cell atlas",
    "clinical",
    "digital pathology",
    "drug discovery",
    "gene expression",
    "genomics",
    "histology",
    "histopathology",
    "medical",
    "medical imaging",
    "microbiology",
    "microscopy",
    "multi-omics",
    "omics",
    "pathology",
    "patient",
    "precision medicine",
    "proteomics",
    "protein structure",
    "radiology",
    "single-cell",
    "spatial transcriptomics",
    "transcriptomics",
    "tumor",
)
_AI_MATCH_TERMS = (
    "agentic",
    "artificial intelligence",
    "deep learning",
    "foundation model",
    "language model",
    "llm",
    "machine learning",
    "multimodal",
    "neural",
    "prediction model",
    "reasoning model",
    "retrieval",
    "transformer",
    "vision-language",
)
_MEDICINE_MATCH_TERMS = (
    "biomedical",
    "clinical",
    "diagnosis",
    "disease",
    "drug",
    "ehr",
    "genomics",
    "healthcare",
    "histopathology",
    "hospital",
    "medical",
    "medicine",
    "oncology",
    "pathology",
    "patient",
    "protein",
    "radiology",
    "therapeutic",
    "therapy",
)
_AI_FOR_MEDICINE_COMBINED_TERMS = (
    "medical ai",
    "clinical ai",
    "biomedical ai",
    "medical imaging",
    "radiology ai",
    "pathology ai",
    "ehr foundation model",
    "clinical language model",
    "drug discovery model",
)


@dataclass(slots=True, frozen=True)
class SourceBundleDefinition:
    bundle_id: str
    label: str
    description: str = ""
    enabled_sources: tuple[str, ...] = DEFAULT_ENABLED_SOURCES
    include_terms: tuple[str, ...] = ()
    exclude_terms: tuple[str, ...] = ()
    official: bool = False

    def to_mapping(self) -> dict[str, Any]:
        return {
            "id": self.bundle_id,
            "label": self.label,
            "description": self.description,
            "enabled_sources": list(self.enabled_sources),
            "include_terms": list(self.include_terms),
            "exclude_terms": list(self.exclude_terms),
            "official": self.official,
        }


@dataclass(slots=True, frozen=True)
class LoadedSourceBundles:
    path: Path
    bundles: tuple[SourceBundleDefinition, ...]
    loaded: bool


def official_source_bundles() -> tuple[SourceBundleDefinition, ...]:
    return (
        SourceBundleDefinition(
            bundle_id=SOURCE_BUNDLE_BIOMEDICAL,
            label="Biomedical",
            description=(
                "Default public biomedical scouting over the cached daily arXiv and bioRxiv snapshot. "
                "medRxiv remains compatibility-only and is not part of the default release path."
            ),
            enabled_sources=DEFAULT_ENABLED_SOURCES,
            official=True,
        ),
        SourceBundleDefinition(
            bundle_id=SOURCE_BUNDLE_AI_FOR_MEDICINE,
            label="AI for medicine",
            description=(
                "Curated AI-for-medicine track over the same daily local arXiv and bioRxiv snapshot."
            ),
            enabled_sources=DEFAULT_ENABLED_SOURCES,
            official=True,
        ),
    )


def load_source_bundles(*, config_path: str | Path | None = None) -> LoadedSourceBundles:
    resolved_path = Path(config_path) if config_path is not None else DEFAULT_SOURCE_BUNDLES_PATH
    official_lookup = {bundle.bundle_id: bundle for bundle in official_source_bundles()}
    if not resolved_path.exists():
        return LoadedSourceBundles(
            path=resolved_path,
            bundles=tuple(official_lookup.values()),
            loaded=False,
        )

    try:
        payload = json.loads(resolved_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"Unable to read source bundle config {resolved_path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid JSON in source bundle config {resolved_path}: {exc}") from exc

    bundles_payload = payload.get("bundles") if isinstance(payload, Mapping) else payload
    if not isinstance(bundles_payload, list):
        raise ValueError(f"Source bundle config {resolved_path} must contain a JSON array or a bundles array.")

    custom_bundles: list[SourceBundleDefinition] = []
    for index, raw_bundle in enumerate(bundles_payload, start=1):
        if not isinstance(raw_bundle, Mapping):
            raise ValueError(f"Source bundle #{index} in {resolved_path} must be an object.")
        bundle = _parse_bundle_definition(raw_bundle, index=index)
        if bundle.bundle_id in official_lookup:
            raise ValueError(f"Custom source bundle id {bundle.bundle_id} is reserved.")
        custom_bundles.append(bundle)

    return LoadedSourceBundles(
        path=resolved_path,
        bundles=tuple((*official_lookup.values(), *custom_bundles)),
        loaded=True,
    )


def save_custom_source_bundles(
    bundles: Sequence[SourceBundleDefinition],
    *,
    config_path: str | Path | None = None,
) -> Path:
    resolved_path = Path(config_path) if config_path is not None else DEFAULT_SOURCE_BUNDLES_PATH
    resolved_path.parent.mkdir(parents=True, exist_ok=True)
    custom_payload = [bundle.to_mapping() for bundle in bundles if not bundle.official]
    resolved_path.write_text(json.dumps({"bundles": custom_payload}, indent=2), encoding="utf-8")
    return resolved_path


def list_public_source_bundles(*, config_path: str | Path | None = None) -> tuple[SourceBundleDefinition, ...]:
    return load_source_bundles(config_path=config_path).bundles


def list_custom_source_bundles(*, config_path: str | Path | None = None) -> tuple[SourceBundleDefinition, ...]:
    return tuple(
        bundle
        for bundle in load_source_bundles(config_path=config_path).bundles
        if not bundle.official
    )


def resolve_source_bundle(
    bundle_id: str | None,
    *,
    config_path: str | Path | None = None,
) -> SourceBundleDefinition | None:
    normalized = normalize_source_bundle_id(bundle_id)
    if not normalized:
        return None
    loaded = load_source_bundles(config_path=config_path)
    for bundle in loaded.bundles:
        if bundle.bundle_id == normalized:
            return bundle
    return None


def normalize_source_bundle_id(value: str | None) -> str:
    return str(value or "").strip().lower()


def build_custom_source_bundle(
    *,
    name: str,
    enabled_sources: Sequence[str],
    include_terms: Sequence[str] = (),
    exclude_terms: Sequence[str] = (),
    description: str = '',
    bundle_id: str | None = None,
) -> SourceBundleDefinition:
    label = str(name or '').strip()
    if not label:
        raise ValueError('Custom source bundle name is required.')
    resolved_bundle_id = normalize_source_bundle_id(bundle_id) or _generated_custom_bundle_id(label)
    if resolved_bundle_id in OFFICIAL_SOURCE_BUNDLE_IDS:
        raise ValueError(f'Custom source bundle id {resolved_bundle_id} is reserved.')
    return SourceBundleDefinition(
        bundle_id=resolved_bundle_id,
        label=label,
        description=str(description or '').strip(),
        enabled_sources=_normalize_text_values(enabled_sources, field_name='enabled_sources') or DEFAULT_ENABLED_SOURCES,
        include_terms=_normalize_text_values(include_terms, field_name='include_terms'),
        exclude_terms=_normalize_text_values(exclude_terms, field_name='exclude_terms'),
        official=False,
    )


def source_bundle_label(bundle_id: str, *, config_path: str | Path | None = None) -> str:
    bundle = resolve_source_bundle(bundle_id, config_path=config_path)
    if bundle is not None:
        return bundle.label
    return bundle_id


def filter_papers_for_bundle(
    papers: Sequence[PaperRecord],
    bundle: SourceBundleDefinition,
) -> list[PaperRecord]:
    return [paper for paper in papers if bundle_matches_paper(bundle, paper)]


def bundle_matches_paper(bundle: SourceBundleDefinition, paper: PaperRecord) -> bool:
    source = str(paper.source or "").strip().lower()
    if source and bundle.enabled_sources and source not in bundle.enabled_sources:
        return False

    text = paper.normalized_text()
    if bundle.bundle_id == SOURCE_BUNDLE_BIOMEDICAL:
        return _matches_biomedical_bundle(paper, text)
    if bundle.bundle_id == SOURCE_BUNDLE_AI_FOR_MEDICINE:
        return _matches_ai_for_medicine_bundle(text)
    return _matches_custom_bundle(bundle, text)


def upsert_custom_source_bundle(
    bundle: SourceBundleDefinition,
    *,
    config_path: str | Path | None = None,
) -> LoadedSourceBundles:
    if bundle.official:
        raise ValueError("Official source bundles cannot be overwritten.")
    loaded = load_source_bundles(config_path=config_path)
    updated_custom: list[SourceBundleDefinition] = []
    replaced = False
    for existing in loaded.bundles:
        if existing.official:
            continue
        if existing.bundle_id == bundle.bundle_id:
            updated_custom.append(bundle)
            replaced = True
            continue
        updated_custom.append(existing)
    if not replaced:
        updated_custom.append(bundle)
    save_custom_source_bundles(updated_custom, config_path=config_path)
    return load_source_bundles(config_path=config_path)


def delete_custom_source_bundle(
    bundle_id: str,
    *,
    config_path: str | Path | None = None,
) -> LoadedSourceBundles:
    normalized = normalize_source_bundle_id(bundle_id)
    if normalized in OFFICIAL_SOURCE_BUNDLE_IDS:
        raise ValueError(f"Official source bundle {normalized} cannot be deleted.")
    loaded = load_source_bundles(config_path=config_path)
    updated_custom = [
        bundle
        for bundle in loaded.bundles
        if not bundle.official and bundle.bundle_id != normalized
    ]
    save_custom_source_bundles(updated_custom, config_path=config_path)
    return load_source_bundles(config_path=config_path)


def _parse_bundle_definition(payload: Mapping[str, Any], *, index: int) -> SourceBundleDefinition:
    bundle_id = normalize_source_bundle_id(str(payload.get("id", "")))
    if not bundle_id:
        raise ValueError(f"Source bundle #{index} is missing id.")
    label = str(payload.get("label", "")).strip()
    if not label:
        raise ValueError(f"Source bundle {bundle_id} is missing label.")
    enabled_sources = _parse_text_array(payload.get("enabled_sources"), field_name=f"{bundle_id}.enabled_sources")
    include_terms = _parse_text_array(payload.get("include_terms"), field_name=f"{bundle_id}.include_terms")
    exclude_terms = _parse_text_array(payload.get("exclude_terms"), field_name=f"{bundle_id}.exclude_terms")
    return SourceBundleDefinition(
        bundle_id=bundle_id,
        label=label,
        description=str(payload.get("description", "")).strip(),
        enabled_sources=enabled_sources or DEFAULT_ENABLED_SOURCES,
        include_terms=include_terms,
        exclude_terms=exclude_terms,
        official=bool(payload.get("official", False)),
    )


def _parse_text_array(value: Any, *, field_name: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be an array of strings.")
    return _normalize_text_values(value, field_name=field_name)


def _normalize_text_values(values: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    items: list[str] = []
    seen: set[str] = set()
    for raw_item in values:
        if not isinstance(raw_item, str):
            raise ValueError(f"{field_name} must contain only strings.")
        normalized = raw_item.strip()
        canonical = normalized.lower()
        if not normalized or canonical in seen:
            continue
        items.append(normalized)
        seen.add(canonical)
    return tuple(items)


def _generated_custom_bundle_id(label: str) -> str:
    generated = normalize_source_bundle_id(slugify(label).replace(' ', '-'))
    if not generated or generated == 'item':
        return 'custom-bundle'
    if generated in OFFICIAL_SOURCE_BUNDLE_IDS:
        return f'custom-{generated}'
    return generated


def _matches_biomedical_bundle(paper: PaperRecord, text: str) -> bool:
    if paper.source in {"biorxiv", "medrxiv"}:
        return True
    categories = {str(category).strip().lower() for category in paper.categories if str(category).strip()}
    if any(category.startswith("q-bio") for category in categories):
        return True
    return _contains_any_term(text, _BIOMEDICAL_MATCH_TERMS)


def _matches_ai_for_medicine_bundle(text: str) -> bool:
    if _contains_any_term(text, _AI_FOR_MEDICINE_COMBINED_TERMS):
        return True
    return _contains_any_term(text, _AI_MATCH_TERMS) and _contains_any_term(text, _MEDICINE_MATCH_TERMS)


def _matches_custom_bundle(bundle: SourceBundleDefinition, text: str) -> bool:
    if bundle.exclude_terms and _contains_any_term(text, bundle.exclude_terms):
        return False
    if not bundle.include_terms:
        return True
    return _contains_any_term(text, bundle.include_terms)


def _contains_any_term(text: str, terms: Sequence[str]) -> bool:
    normalized_text = str(text or "").lower()
    return any(term.strip().lower() in normalized_text for term in terms if term and term.strip())
