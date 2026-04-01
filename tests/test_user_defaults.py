from __future__ import annotations

import json
from pathlib import Path

import pytest

from frontier_compass.common.user_defaults import UserDefaults, load_user_defaults


def test_load_user_defaults_ignores_missing_default_file(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)

    loaded = load_user_defaults()

    assert loaded.loaded is False
    assert loaded.disabled is False
    assert loaded.defaults == UserDefaults()


@pytest.mark.parametrize(
    ("email_to_value", "expected_recipients"),
    [
        ("reviewer@example.com, second@example.com", ("reviewer@example.com", "second@example.com")),
        (["reviewer@example.com", "second@example.com"], ("reviewer@example.com", "second@example.com")),
    ],
)
def test_load_user_defaults_parses_config_and_normalizes_recipients(
    tmp_path: Path,
    email_to_value: object,
    expected_recipients: tuple[str, ...],
) -> None:
    config_path = tmp_path / "user_defaults.json"
    zotero_export = tmp_path / "exports" / "sample.csl.json"
    config_path.write_text(
        json.dumps(
            {
                "default_mode": "biomedical-latest",
                "default_report_mode": "enhanced",
                "default_max_results": 25,
                "default_zotero_export_path": "exports/sample.csl.json",
                "default_email_to": email_to_value,
                "default_email_from": "frontier@example.com",
                "default_generate_dry_run_email": True,
                "default_allow_stale_cache": True,
            }
        ),
        encoding="utf-8",
    )

    loaded = load_user_defaults(config_path=config_path)

    assert loaded.loaded is True
    assert loaded.path == config_path
    assert loaded.defaults.default_mode == "biomedical-latest"
    assert loaded.defaults.default_report_mode == "enhanced"
    assert loaded.defaults.default_max_results == 25
    assert loaded.defaults.default_zotero_export_path == zotero_export
    assert loaded.defaults.default_email_to == expected_recipients
    assert loaded.defaults.default_email_from == "frontier@example.com"
    assert loaded.defaults.default_generate_dry_run_email is True
    assert loaded.defaults.default_allow_stale_cache is True


def test_load_user_defaults_rejects_missing_explicit_config(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.json"

    with pytest.raises(ValueError, match="User defaults config not found"):
        load_user_defaults(config_path=config_path)


def test_load_user_defaults_rejects_invalid_json(tmp_path: Path) -> None:
    config_path = tmp_path / "broken.json"
    config_path.write_text("{not-json}", encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid JSON in user defaults config"):
        load_user_defaults(config_path=config_path)
