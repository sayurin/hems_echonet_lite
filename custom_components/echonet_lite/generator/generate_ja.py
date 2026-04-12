# ruff: noqa: T201
"""Generate Japanese translation (ja.json) for the ECHONET Lite integration.

This script generates translations/ja.json from pyhems definitions using
name_ja fields. It follows the same entity processing logic as
generate_strings.py but outputs resolved Japanese text without key references
or common section deduplication.

Run with: python -m custom_components.echonet_lite.generator.generate_ja

Requires pyhems to be installed in the environment:
- Development: uv pip install -e /workspaces/pyhems
- Released: uv pip install pyhems

Input files:
- pyhems definitions.json (source of entity definitions with name_ja)
- generator/strings_static_ja.json (static Japanese strings for config, options, issues)

Output files:
- custom_components/echonet_lite/translations/ja.json
"""

from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

from pyhems import DefinitionsRegistry, EntityDefinition, load_definitions_registry

from ..const import camel_to_snake
from ..entity import can_process_enum_values, infer_platform

# ============================================================================
# Constants
# ============================================================================

ECHONET_LITE_DIR = Path(__file__).parent.parent
GENERATOR_DIR = Path(__file__).parent
STRINGS_STATIC_JA_FILE = GENERATOR_DIR / "strings_static_ja.json"

# Pattern to match [%key:path::to::value%] references
_KEY_REF_PATTERN = re.compile(r"^\[%key:(.+)%\]$")


# ============================================================================
# Key Reference Resolution
# ============================================================================


def _resolve_key_path(data: dict[str, Any], key_path: str) -> str | None:
    """Resolve a '::'-separated key path within the data dict.

    Args:
        data: The full translation data dict.
        key_path: '::'-separated path
                  (e.g., "config::step::user::data::interface").

    Returns:
        Resolved string value, or None if not found.
    """
    parts = key_path.split("::")
    current: Any = data
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, str) else None


def _resolve_references(data: dict[str, Any]) -> None:
    """Resolve all [%key:...%] references in the data dict in-place.

    Handles self-references within the data:
    - [%key:component::echonet_lite::...%] → resolved within data

    References to HA core common strings (e.g., [%key:common::...%]) are
    expected to be pre-resolved in strings_static_ja.json with Japanese
    text, since HA core Japanese translations are not publicly accessible.

    References are resolved iteratively until no more remain, supporting
    chained references (e.g., A → B → literal).

    Args:
        data: The full translation data dict (mutated in-place).
    """
    max_iterations = 10
    for _ in range(max_iterations):
        if not _resolve_references_pass(data, data):
            break


def _resolve_references_pass(node: Any, root: dict[str, Any]) -> bool:
    """Single pass of reference resolution. Returns True if any resolved."""
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if isinstance(value, str):
                match = _KEY_REF_PATTERN.match(value)
                if match:
                    ref_path = match.group(1)
                    resolved = _resolve_reference(root, ref_path)
                    if resolved is not None:
                        node[key] = resolved
                        changed = True
            elif isinstance(value, dict):
                if _resolve_references_pass(value, root):
                    changed = True
    return changed


def _resolve_reference(root: dict[str, Any], ref_path: str) -> str | None:
    """Resolve a single key reference path.

    Args:
        root: The full translation data dict.
        ref_path: Full reference path (e.g., "component::echonet_lite::config::...").

    Returns:
        Resolved string or None if unresolvable.
    """
    # Self-reference: component::echonet_lite::...
    prefix = "component::echonet_lite::"
    if ref_path.startswith(prefix):
        return _resolve_key_path(root, ref_path[len(prefix) :])

    return None


# ============================================================================
# Strings Generation
# ============================================================================


def _escape_html_brackets(text: str) -> str:
    """Escape angle brackets to square brackets.

    HA translation validation rejects strings containing < or > as HTML.
    MRA data uses angle brackets for categorization.
    """
    return text.replace("<", "[").replace(">", "]")


def _add_entity_string(
    entity_strings: dict[str, dict[str, dict[str, Any]]],
    platform: str,
    entity: EntityDefinition,
    state: dict[str, str] | None,
) -> None:
    """Add entity string with optional state translations.

    Args:
        entity_strings: Dictionary to add entity strings to.
        platform: Entity platform (binary_sensor, switch, select, sensor, etc.)
        entity: EntityDefinition dataclass from pyhems.
        state: State translations dict or None.
    """
    entry: dict[str, Any] = {"name": _escape_html_brackets(entity.name_ja)}
    if state:
        entry["state"] = state
    entity_strings.setdefault(platform, {})[entity.id] = entry


def _process_entity(
    entity_strings: dict[str, dict[str, dict[str, Any]]],
    entity: EntityDefinition,
) -> None:
    """Process a single entity definition for Japanese translation.

    Uses infer_platform() to determine the target platform, then extracts
    name_ja and enum name_ja values. No common::state matching or verb
    conjugation mapping is applied for Japanese.

    Args:
        entity_strings: Dictionary to add entity strings to.
        entity: EntityDefinition dataclass from pyhems.
    """
    if not can_process_enum_values(entity):
        return

    platform = infer_platform(entity)

    match platform:
        case "binary_sensor":
            on_text = _escape_html_brackets(entity.enum_values[0].name_ja)
            off_text = _escape_html_brackets(entity.enum_values[1].name_ja)
            state = {"on": on_text, "off": off_text}
            _add_entity_string(entity_strings, "binary_sensor", entity, state)
        case "switch":
            on_text = _escape_html_brackets(entity.enum_values[0].name_ja)
            off_text = _escape_html_brackets(entity.enum_values[1].name_ja)
            state = {"on": on_text, "off": off_text}
            _add_entity_string(entity_strings, "switch", entity, state)
        case "button":
            _add_entity_string(entity_strings, "button", entity, None)
        case "select":
            state = {
                camel_to_snake(ev.key): _escape_html_brackets(ev.name_ja)
                for ev in entity.enum_values
            }
            _add_entity_string(entity_strings, "select", entity, state)
        case "sensor" if entity.enum_values:
            state = {
                camel_to_snake(ev.key): _escape_html_brackets(ev.name_ja)
                for ev in entity.enum_values
            }
            _add_entity_string(entity_strings, "sensor", entity, state)
        case "sensor" | "number":
            _add_entity_string(entity_strings, platform, entity, None)


def generate_ja(registry: DefinitionsRegistry) -> dict[str, Any]:
    """Generate Japanese translation dict from DefinitionsRegistry.

    Unlike generate_strings(), this does not create a common section or
    use key references. All values are resolved Japanese text.

    Static entity entries from strings_static_ja.json are merged into the
    generated set (static takes priority), mirroring generate_strings()'s
    merge behavior.

    After merging, [%key:component::echonet_lite::...%] references from
    strings_static_ja.json are resolved to their target values.

    Args:
        registry: DefinitionsRegistry loaded from pyhems.

    Returns:
        Dictionary with config, options, issues, and entity sections.
    """
    entity_strings: dict[str, dict[str, dict[str, Any]]] = {}

    for entity_defs in registry.entities.values():
        for entity in entity_defs:
            _process_entity(entity_strings, entity)

    # Load static file
    with STRINGS_STATIC_JA_FILE.open(encoding="utf-8") as f:
        static_data = json.load(f)

    # Merge static entity entries into entity_strings (static takes priority)
    for platform, platform_entities in static_data.get("entity", {}).items():
        gen_platform = entity_strings.setdefault(platform, {})
        for entity_key, entity_value in platform_entities.items():
            gen_platform[entity_key] = entity_value

    # Build result from static non-entity sections
    result: dict[str, Any] = {k: v for k, v in static_data.items() if k != "entity"}

    # Set entity section from fully processed entity_strings
    result["entity"] = {}
    for platform, platform_entities in sorted(entity_strings.items()):
        result["entity"][platform] = dict(sorted(platform_entities.items()))

    # Resolve [%key:...%] references from strings_static_ja.json
    _resolve_references(result)

    return result


# ============================================================================
# Main Entry Point
# ============================================================================


def main() -> None:
    """Main entry point."""
    print("Loading pyhems DefinitionsRegistry...")
    registry = load_definitions_registry()
    print(f"  MRA version: {registry.mra_version}")

    all_class_codes = set(registry.entities.keys())
    entity_count = sum(len(entities) for entities in registry.entities.values())
    print(f"  Device classes: {len(all_class_codes)}")

    print("Generating ja.json...")
    ja_data = generate_ja(registry)

    # Write translations/ja.json
    ja_path = ECHONET_LITE_DIR / "translations" / "ja.json"
    with ja_path.open("w", encoding="utf-8") as f:
        json.dump(ja_data, f, indent=2, ensure_ascii=False, sort_keys=True)
        f.write("\n")
    print(f"Generated: {ja_path}")

    # Print summary
    platform_counts = {
        platform: len(entities)
        for platform, entities in sorted(ja_data.get("entity", {}).items())
    }
    print("\nSummary:")
    print(f"  MRA version: {registry.mra_version}")
    print(f"  Device classes: {len(all_class_codes)}")
    print(f"  Entities (total across platforms): {entity_count}")
    for platform, count in platform_counts.items():
        print(f"    {platform}: {count}")


if __name__ == "__main__":
    main()
