"""CT-05 — golden JSON Schema contract (ADR-0005 Decisions #1-4, #8).

Static walk of the committed schema enforcing the Ajv ``strict: true``
authoring rules Langfuse 4.38.0 requires at write time (probed live in
docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md Addendum 2):

1. every ``if``/``then`` subschema declares ``"type"`` (strictTypes);
2. every key in a ``required`` list is declared in that schema's
   ``properties`` (strictRequired);
3. the minified schema is under the 10,000-char Langfuse cap;
4. ``prompts.propertyNames`` matches the pinned control-char-excluding
   pattern (1-200 chars);
5. the schema accepts the DATA-MODEL-01 §1 example and rejects a
   ``"twelve fifty"`` value in a ``number`` field.
"""

from __future__ import annotations

import json
from typing import Any

import jsonschema
import pytest

from idp_regression.platform.schema import golden_schema_json, load_golden_schema

PROMPT_PROPERTY_NAMES_PATTERN = r"^[^\u0000-\u001F\u007F]{1,200}$"


def _walk_if_then(node: Any, path: str = "$") -> list[str]:
    """Return a list of violations: every if/then subschema must declare type."""
    violations: list[str] = []
    if isinstance(node, dict):
        if "if" in node:
            if_schema = node["if"]
            if isinstance(if_schema, dict) and "type" not in if_schema:
                violations.append(f"{path}.if missing 'type'")
        for key, value in node.items():
            violations.extend(_walk_if_then(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            violations.extend(_walk_if_then(item, f"{path}[{i}]"))
    return violations


def _walk_required_subset(node: Any, path: str = "$") -> list[str]:
    """Every 'required' list must be a subset of that schema's 'properties'."""
    violations: list[str] = []
    if isinstance(node, dict):
        if "required" in node and isinstance(node.get("required"), list):
            props = node.get("properties", {})
            prop_keys = set(props.keys()) if isinstance(props, dict) else set()
            for key in node["required"]:
                if key not in prop_keys:
                    violations.append(f"{path}: required '{key}' not in properties")
        for key, value in node.items():
            violations.extend(_walk_required_subset(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            violations.extend(_walk_required_subset(item, f"{path}[{i}]"))
    return violations


@pytest.fixture
def schema() -> dict[str, Any]:
    return load_golden_schema()


def test_every_if_then_declares_type(schema: dict[str, Any]) -> None:
    assert _walk_if_then(schema) == []


def test_every_required_key_is_declared_in_properties(schema: dict[str, Any]) -> None:
    assert _walk_required_subset(schema) == []


def test_minified_schema_under_10000_chars(schema: dict[str, Any]) -> None:
    minified = golden_schema_json()
    assert len(minified) < 10_000
    # sanity: it really is minified (no incidental whitespace padding)
    assert minified == json.dumps(schema, separators=(",", ":"), sort_keys=True)


def test_prompts_property_names_pattern_is_pinned(schema: dict[str, Any]) -> None:
    prompts_schema = schema["properties"]["prompts"]
    assert prompts_schema["propertyNames"] == {
        "type": "string",
        "pattern": PROMPT_PROPERTY_NAMES_PATTERN,
    }


def test_accepts_data_model_01_example(schema: dict[str, Any]) -> None:
    example = {
        "document_id": "invoice-007.pdf",
        "fields": {
            "invoice_number": {"value": "INV-1", "type": "id", "critical": True},
            "invoice_date": {"value": "2024-03-15", "type": "date", "critical": True},
            "total": {"value": "1250.00", "type": "number", "critical": True},
        },
        "tables": {
            "line_items": {
                "match_key": "description",
                "critical": True,
                "rows": [{"description": "Widget A", "qty": "10", "unit_price": "50.00"}],
            }
        },
        "prompts": {"What is the vendor name?": {"answer": "Acme Corp", "critical": False}},
    }
    jsonschema.validate(example, schema)


def test_rejects_non_numeric_value_in_number_field(schema: dict[str, Any]) -> None:
    bad = {"fields": {"total": {"value": "twelve fifty", "type": "number", "critical": True}}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


def test_rejects_control_character_in_prompt_key(schema: dict[str, Any]) -> None:
    bad = {"fields": {}, "prompts": {"bad\nkey": {"answer": "x"}}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)
