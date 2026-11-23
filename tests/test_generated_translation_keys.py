"""Tests for generated translation key checks."""

from pathlib import Path

from scripts.check_generated_translation_keys import compare_key_sets


def test_compare_key_sets_ignores_common() -> None:
    """The common section may differ between generated files."""
    documents = {
        Path("strings.json"): {
            "common": {"one": "one"},
            "config": {"step": {}},
            "entity": {"sensor": {"sensor_key": {}}},
        },
        Path("en.json"): {
            "common": {"two": "two"},
            "config": {"step": {}},
            "entity": {"sensor": {"sensor_key": {}}},
        },
        Path("ja.json"): {
            "config": {"step": {}},
            "entity": {"sensor": {"sensor_key": {}}},
        },
    }

    assert compare_key_sets(documents) == []


def test_compare_key_sets_checks_entity_keys() -> None:
    """Entity keys below each platform must be present in every file."""
    documents = {
        Path("strings.json"): {
            "config": {"step": {}},
            "entity": {"sensor": {"missing_key": {}, "present_key": {}}},
        },
        Path("en.json"): {
            "config": {"step": {}},
            "entity": {"sensor": {"present_key": {}}},
        },
        Path("ja.json"): {
            "config": {"step": {}},
            "entity": {"sensor": {"present_key": {}}},
        },
    }

    assert compare_key_sets(documents) == [
        "en.json: entity.sensor: missing=['missing_key']",
        "ja.json: entity.sensor: missing=['missing_key']",
    ]
