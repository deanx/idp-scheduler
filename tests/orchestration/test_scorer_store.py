"""Where custom scorer specs live, and how a run gets them.

The compiler's own tests are `tests/classifier/test_custom_scorers.py`
(pure, no filesystem). These cover the half that touches disk and the
registry -- including the property that matters most to a run: importing
the module registers NOTHING, so a run that never asks for custom
scorers behaves exactly as it did before they existed.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from idp_regression.classifier import custom as cs
from idp_regression.classifier.registry import CLASSIFIERS
from idp_regression.orchestration import scorer_store


def spec(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "confidence-floor",
        "description": "Fail a match IDP was not confident about.",
        "base": "regression",
        "rules": [
            {"when": {"confidence_below": 0.8, "verdict_is": ["match"]},
             "then": {"verdict": "wrong_value", "critical": True}},
        ],
    }
    base.update(overrides)
    return base


class TestStorage:
    def test_save_load_round_trip(self, tmp_path: Path) -> None:
        parsed = cs.parse_spec(spec())
        path = scorer_store.save_spec(parsed, tmp_path)
        assert json.loads(path.read_text())["name"] == "confidence-floor"
        specs, errors = scorer_store.load_specs(tmp_path)
        assert [s.name for s in specs] == ["confidence-floor"]
        assert errors == []

    def test_one_unreadable_spec_never_hides_the_good_ones(self, tmp_path: Path) -> None:
        scorer_store.save_spec(cs.parse_spec(spec()), tmp_path)
        (tmp_path / "broken.json").write_text("{not json", encoding="utf-8")
        (tmp_path / "invalid.json").write_text(json.dumps({"name": "x"}), encoding="utf-8")
        specs, errors = scorer_store.load_specs(tmp_path)
        assert [s.name for s in specs] == ["confidence-floor"]
        assert len(errors) == 2
        assert all(name in " ".join(errors) for name in ("broken.json", "invalid.json"))

    @pytest.mark.parametrize("name", ["../escape", "a/b", "..", "with.dot"])
    def test_a_name_can_never_traverse_out_of_the_scorer_directory(
        self, name: str, tmp_path: Path
    ) -> None:
        with pytest.raises(cs.SpecError):
            scorer_store.spec_path(name, tmp_path)

    def test_missing_directory_is_empty_not_an_error(self, tmp_path: Path) -> None:
        assert scorer_store.load_specs(tmp_path / "absent") == ([], [])

    def test_delete_reports_whether_anything_was_removed(self, tmp_path: Path) -> None:
        scorer_store.save_spec(cs.parse_spec(spec()), tmp_path)
        assert scorer_store.delete_spec("confidence-floor", tmp_path) is True
        assert scorer_store.delete_spec("confidence-floor", tmp_path) is False


class TestRegistration:
    def test_registering_never_changes_a_shipped_classifier(self, tmp_path: Path) -> None:
        before = dict(CLASSIFIERS)
        scorer_store.save_spec(cs.parse_spec(spec()), tmp_path)
        registered, errors = scorer_store.register_custom_classifiers(tmp_path)
        try:
            assert sorted(registered) == ["confidence-floor"]
            assert errors == []
            for name in before:
                assert CLASSIFIERS[name] is before[name]
        finally:
            CLASSIFIERS.pop("confidence-floor", None)

    def test_importing_the_module_registers_nothing_by_itself(self) -> None:
        """A run that did not ask for custom scorers must behave exactly
        as it did before this module existed."""
        assert set(CLASSIFIERS) == {"regression", "pinned-file"}


def test_a_relaxing_spec_file_is_reported_not_registered(tmp_path: Path) -> None:
    """S-01.1 re-stamp #2 G-1(b): the console refused a relaxing spec on
    save, but `.idp-regression-scorers/` is committed and hand-editable, and
    `load_specs` never ran `verify_monotone` -- so `--classifier` loaded what
    the console would refuse. A spec that fails the proof is a load error."""
    relaxing = {"name": "format-is-value", "base": "regression",
                "rules": [{"when": {"verdict_is": ["wrong_format"]},
                           "then": {"verdict": "wrong_value"}}]}
    (tmp_path / "format-is-value.json").write_text(json.dumps(relaxing), encoding="utf-8")
    scorer_store.save_spec(cs.parse_spec(spec()), tmp_path)
    registered, errors = scorer_store.register_custom_classifiers(tmp_path)
    try:
        assert sorted(registered) == ["confidence-floor"]
        assert len(errors) == 1
        assert "format-is-value.json" in errors[0]
        assert "format-is-value" not in CLASSIFIERS
    finally:
        CLASSIFIERS.pop("confidence-floor", None)
        CLASSIFIERS.pop("format-is-value", None)
