"""Catalog files, parity gate and interpolation (I18N design Section 4, unit IP1-1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from claire.i18n import catalog, t, tn, use_locale
from claire.i18n.locales import SUPPORTED_LOCALES


def test_catalogs_have_the_same_keys_and_placeholders() -> None:
    en = catalog.load_catalog("en")
    for locale in SUPPORTED_LOCALES:
        other = catalog.load_catalog(locale)
        assert set(other) == set(en), f"{locale}.json keys differ from en.json"
        for key, template in en.items():
            assert sorted(catalog.PLACEHOLDER.findall(other[key])) == sorted(catalog.PLACEHOLDER.findall(template)), f"{locale}.json placeholders differ for {key}"


def test_catalog_files_are_served_from_the_static_directory() -> None:
    # The browser reads the same files at /static/locales/{locale}.json (decision ID4).
    assert catalog.LOCALES_DIR == Path(catalog.__file__).resolve().parent.parent / "static" / "locales"
    assert {p.name for p in catalog.LOCALES_DIR.glob("*.json")} == {f"{code}.json" for code in SUPPORTED_LOCALES}


@pytest.mark.parametrize("content", ['["a.b"]', '{"a.b": 1}', '{"NoDots": "x"}', '{"a.b": {"c": "x"}}'])
def test_malformed_catalog_file_is_rejected(tmp_path: Path, content: str) -> None:
    path = tmp_path / "en.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError):
        catalog.read_catalog_file(path)


def test_translation_uses_the_active_locale_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    assert t("common.action.save") == "Save"
    with use_locale("ko"):
        assert t("common.action.save") == "저장"
        assert t("common.action.save", locale="en") == "Save"
        assert t("missing.key") == "missing.key"
    monkeypatch.setattr(catalog, "load_catalog", lambda locale: {"only.en": "English {name}"} if locale == "en" else {})
    assert t("only.en", locale="ko", name="x") == "English x"


def test_interpolation_is_safe_and_plural_keys_resolve() -> None:
    assert catalog.interpolate("{a} {b} {a.__class__} {} {0} }{", {"a": 1}) == "1 {b} {a.__class__} {} {0} }{"
    assert json.loads(json.dumps(catalog.interpolate("{x}", {"x": "{y}", "y": "no"}))) == "{y}"
    assert tn("doc.count", 1) == "1 document" and tn("doc.count", 3) == "3 documents"
    assert tn("doc.count", 1, locale="ko") == "1개 문서"
