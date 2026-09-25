"""`--classifier my-rule` for a spec authored in the UI.

The wiring is one call, and its placement is the whole test: argparse
freezes `--classifier`'s `choices` when the parser is BUILT, so a spec
registered after that point would be rejected before
`registry.resolve()` ever saw it. Everything else here is about the
directory not being able to break a run that does not use it.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from pathlib import Path

import pytest

from idp_regression.classifier.registry import CLASSIFIERS
from idp_regression.orchestration import cli


@pytest.fixture(autouse=True)
def restore_registry() -> object:
    """Registration mutates a module-level table; a leaked entry would
    make a later test pass for the wrong reason."""
    before = dict(CLASSIFIERS)
    yield
    CLASSIFIERS.clear()
    CLASSIFIERS.update(before)


SPEC = {
    "name": "confidence-floor",
    "description": "Fail a match IDP was not confident about.",
    "base": "regression",
    "rules": [
        {"when": {"confidence_below": 0.8, "verdict_is": ["match"]},
         "then": {"verdict": "wrong_value", "critical": True}},
    ],
}


def write_spec(
    root: Path, spec: Mapping[str, object] | str, name: str = "confidence-floor"
) -> None:
    root.mkdir(parents=True, exist_ok=True)
    payload = spec if isinstance(spec, str) else json.dumps(dict(spec))
    (root / f"{name}.json").write_text(payload, encoding="utf-8")


#: `--org` and `--action` are validated as UUIDs by the CLI itself.
ORG = "11111111-1111-4111-8111-111111111111"
ACTION = "22222222-2222-4222-8222-222222222222"


def run_cli(extra: list[str]) -> int:
    return cli.main(
        ["--org", ORG, "--action", ACTION, "--version", "1.0.0",
         "--dataset", "d", "--run", "r", *extra]
    )


def test_a_saved_spec_becomes_a_valid_classifier_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    write_spec(tmp_path / ".idp-regression-scorers", SPEC)

    called: dict[str, object] = {}

    def fake_run_eval(*_args: object, **kwargs: object) -> int:
        called.update(kwargs)
        return 0

    monkeypatch.setattr(cli, "run_eval", fake_run_eval)
    exit_code = run_cli(["--classifier", "confidence-floor"])
    assert exit_code == 0
    assert called["classifier"] == "confidence-floor"


def test_without_a_spec_directory_the_name_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: an unrecognised comparison strategy must never
    silently fall back to the default, or two runs would be incomparable
    without saying so."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "run_eval", lambda *_a, **_k: 0)
    assert run_cli(["--classifier", "confidence-floor"]) == 2


def test_a_spec_can_never_change_what_the_shipped_names_mean(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A spec named `regression` is refused at parse time, so a
    directory on disk cannot alter the default comparison."""
    monkeypatch.chdir(tmp_path)
    shipped = CLASSIFIERS["regression"]
    write_spec(tmp_path / ".idp-regression-scorers", {**SPEC, "name": "regression"}, "regression")
    monkeypatch.setattr(cli, "run_eval", lambda *_a, **_k: 0)
    run_cli([])
    assert CLASSIFIERS["regression"] is shipped


def test_a_broken_spec_does_not_fail_a_run_that_does_not_name_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A half-edited file someone left in the directory must not hold
    every other run hostage -- it is reported and skipped."""
    monkeypatch.chdir(tmp_path)
    write_spec(tmp_path / ".idp-regression-scorers", "{not json", "broken")
    monkeypatch.setattr(cli, "run_eval", lambda *_a, **_k: 0)
    with caplog.at_level(logging.WARNING):
        exit_code = run_cli([])
    assert exit_code == 0
    assert "custom_scorer_not_loaded" in caplog.text
    assert "broken.json" in caplog.text


def test_naming_a_broken_spec_is_refused_and_the_reason_is_pointed_at(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """argparse says "invalid choice", which reads as a typo. The real
    cause is a spec file that did not load, and the operator is told
    where to look."""
    monkeypatch.chdir(tmp_path)
    write_spec(tmp_path / ".idp-regression-scorers", "{not json", "broken")
    monkeypatch.setattr(cli, "run_eval", lambda *_a, **_k: 0)
    with caplog.at_level(logging.ERROR):
        exit_code = run_cli(["--classifier", "broken"])
    assert exit_code == 2
    assert "failed to load" in caplog.text


def test_the_selected_spec_is_logged_with_a_content_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Run identity, the same job `golden_version` does for the golden
    set: a spec file can be edited between two runs that both name
    `--classifier my-rule`, and nothing else would say they compared
    differently."""
    monkeypatch.chdir(tmp_path)
    write_spec(tmp_path / ".idp-regression-scorers", SPEC)
    monkeypatch.setattr(cli, "run_eval", lambda *_a, **_k: 0)
    with caplog.at_level(logging.INFO):
        run_cli(["--classifier", "confidence-floor"])
    assert "custom_scorer_selected" in caplog.text
    assert "spec_digest=" in caplog.text


def test_editing_a_spec_changes_its_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from idp_regression.classifier.custom import parse_spec

    tightened = {
        **SPEC,
        "rules": [{"when": {"confidence_below": 0.95, "verdict_is": ["match"]},
                   "then": {"verdict": "wrong_value", "critical": True}}],
    }
    assert parse_spec(SPEC).digest() != parse_spec(tightened).digest()


def test_a_description_only_edit_does_not_change_the_digest() -> None:
    """The digest answers "did this run compare differently", so prose
    must not make two identical comparisons look different."""
    from idp_regression.classifier.custom import parse_spec

    assert parse_spec(SPEC).digest() == parse_spec({**SPEC, "description": "reworded"}).digest()
