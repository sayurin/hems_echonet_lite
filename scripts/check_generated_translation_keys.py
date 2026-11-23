"""Check that generated translation sections have the same key structure."""

from __future__ import annotations

import json
from pathlib import Path
import sys
from typing import Any


FILES = (
    Path("custom_components/echonet_lite/strings.json"),
    Path("custom_components/echonet_lite/translations/en.json"),
    Path("custom_components/echonet_lite/translations/ja.json"),
)
IGNORED_SECTIONS = frozenset({"common"})


def load_json(path: Path) -> dict[str, Any]:
    """Load a translation file."""
    with path.open(encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def section_keys(data: dict[str, Any], section: str, path: Path) -> set[str]:
    """Return the immediate child keys for a translation section."""
    value = data.get(section)
    if not isinstance(value, dict):
        raise ValueError(f"{path}: {section!r} must contain a JSON object")
    return set(value)


def key_set_error(
    expected: set[str], actual: set[str], path: Path, section: str
) -> str | None:
    """Return an error for a key-set mismatch."""
    if actual == expected:
        return None

    details = []
    if missing := sorted(expected - actual):
        details.append(f"missing={missing}")
    if extra := sorted(actual - expected):
        details.append(f"extra={extra}")
    return f"{path}: {section}: {', '.join(details)}"


def compare_key_sets(documents: dict[Path, dict[str, Any]]) -> list[str]:
    """Compare generated translation key sets."""
    sections = set().union(*(data for data in documents.values()))
    sections -= IGNORED_SECTIONS

    errors: list[str] = []
    first_path, first_data = next(iter(documents.items()))
    for section in sorted(sections):
        expected = section_keys(first_data, section, first_path)
        for path, data in documents.items():
            actual = section_keys(data, section, path)
            if error := key_set_error(expected, actual, path, section):
                errors.append(error)

        if section != "entity":
            continue

        for platform in sorted(expected):
            expected_entities = section_keys(first_data[section], platform, first_path)
            for path, data in documents.items():
                if platform not in data[section]:
                    continue
                actual_entities = section_keys(data[section], platform, path)
                if error := key_set_error(
                    expected_entities, actual_entities, path, f"entity.{platform}"
                ):
                    errors.append(error)

    return errors


def main() -> int:
    """Check the generated translation key structure."""
    try:
        documents = {path: load_json(path) for path in FILES}
        errors = compare_key_sets(documents)

        if errors:
            print("Generated translation key sets differ:", file=sys.stderr)
            print("\n".join(errors), file=sys.stderr)
            return 1
    except (OSError, ValueError, json.JSONDecodeError) as err:
        print(f"Generated translation check failed: {err}", file=sys.stderr)
        return 1

    print("Generated translation key sets match (excluding common).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
