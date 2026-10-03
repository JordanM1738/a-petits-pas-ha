"""Tests for const.py."""

from __future__ import annotations

import json
from pathlib import Path

from custom_components.a_petits_pas.const import PRESENCE_CODES

COMPONENT_DIR = Path(__file__).parent.parent / "custom_components" / "a_petits_pas"


def test_presence_codes_are_lowercase():
    # Must match the presence sensor's enum `options` and the "state" keys
    # in strings.json/translations/*.json exactly (case-sensitive).
    assert all(code == code.lower() for code in PRESENCE_CODES)


def test_presence_codes_known_set():
    assert set(PRESENCE_CODES) == {"p", "a", "dp", "expected", "not_expected"}


def test_translation_files_have_a_state_label_for_every_presence_code():
    # Regression guard for the enum sensor: every code in PRESENCE_CODES
    # must have a matching "state" entry in strings.json and both
    # translations/*.json, or that code_ph would show untranslated in one
    # language (or crash on the "options must be non-empty" HA check if the
    # whole "state" block were missing).
    for path in (
        COMPONENT_DIR / "strings.json",
        COMPONENT_DIR / "translations" / "en.json",
        COMPONENT_DIR / "translations" / "fr.json",
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        states = data["entity"]["sensor"]["presence_today"]["state"]
        assert set(states) == set(PRESENCE_CODES), path


def test_activity_categories_known():
    from custom_components.a_petits_pas.const import KNOWN_ACTIVITY_TYPE_CATEGORIES

    assert KNOWN_ACTIVITY_TYPE_CATEGORIES[4] == "bottle"
    assert KNOWN_ACTIVITY_TYPE_CATEGORIES[9] == "info"


def test_translation_files_have_activity_and_image_keys():
    from custom_components.a_petits_pas.const import KNOWN_ACTIVITY_TYPE_CATEGORIES

    for path in (
        COMPONENT_DIR / "strings.json",
        COMPONENT_DIR / "translations" / "en.json",
        COMPONENT_DIR / "translations" / "fr.json",
    ):
        data = json.loads(path.read_text(encoding="utf-8"))
        sensors = data["entity"]["sensor"]
        for cat in set(KNOWN_ACTIVITY_TYPE_CATEGORIES.values()):
            assert f"activity_{cat}" in sensors, f"Missing activity_{cat} in {path}"
        images = data["entity"]["image"]
        assert "profile_photo" in images
        assert "journal_photo" in images
