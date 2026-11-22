"""Locale checks: en.json and vi.json must have exactly the same keys and placeholders,
and every backend error code must be translated in both."""

import json
import re
from pathlib import Path

import pytest

from app import errors

LOCALES = Path(__file__).resolve().parents[2] / "frontend" / "src" / "locales"
PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_]+)")


def load(lang: str) -> dict:
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in d.items():
        full = f"{prefix}.{key}" if prefix else key
        if isinstance(value, dict):
            out.update(flatten(value, full))
        else:
            out[full] = value
    return out


@pytest.fixture(scope="module")
def en() -> dict[str, str]:
    return flatten(load("en"))


@pytest.fixture(scope="module")
def vi() -> dict[str, str]:
    return flatten(load("vi"))


def test_same_keys(en, vi):
    missing_in_vi = sorted(set(en) - set(vi))
    missing_in_en = sorted(set(vi) - set(en))
    assert not missing_in_vi, f"missing in vi.json: {missing_in_vi}"
    assert not missing_in_en, f"missing in en.json: {missing_in_en}"


def test_values_are_non_empty_strings(en, vi):
    for lang, table in (("en", en), ("vi", vi)):
        bad = [k for k, v in table.items() if not isinstance(v, str) or not v.strip()]
        assert not bad, f"{lang}: empty or non-string values: {bad}"


def test_same_placeholders(en, vi):
    for key in en:
        assert set(PLACEHOLDER.findall(en[key])) == set(PLACEHOLDER.findall(vi[key])), key


def test_every_error_code_is_translated_with_its_params(en, vi):
    for code, entry in errors.REGISTRY.items():
        key = f"errors.{code}"
        for lang, table in (("en", en), ("vi", vi)):
            assert key in table, f"{lang}.json lacks {key}"
            used = set(PLACEHOLDER.findall(table[key]))
            assert used <= set(entry.params), f"{lang} {key} uses unknown params {used - set(entry.params)}"
