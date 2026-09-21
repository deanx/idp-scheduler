"""T-01.4.13 (DEBT-19): `run_name` generation must never let two
invocations merge into the same experiment on the evaluation platform. Live-confirmed in
S-01.3: two `record_run` calls with the SAME `run_name` but different
`run_id` merge into ONE experiment (`itemCount` sums). The fix is
composition, not discovery — the orchestrator composes the platform
experiment name as ``f"{run_name}-{run_id[:8]}"``.
"""

from __future__ import annotations

import re

from idp_regression.orchestration.run_naming import compose_experiment_name, generate_run_id

_HEX32 = re.compile(r"^[0-9a-f]{32}$")


def test_generate_run_id_is_a_32_char_lowercase_hex_string() -> None:
    run_id = generate_run_id()
    assert _HEX32.match(run_id), run_id


def test_generate_run_id_differs_across_calls() -> None:
    first = generate_run_id()
    second = generate_run_id()
    assert first != second


def test_compose_experiment_name_appends_the_first_eight_chars_of_run_id() -> None:
    run_id = "0123456789abcdef0123456789abcdef"
    composed = compose_experiment_name("nightly", run_id)
    assert composed == "nightly-01234567"


def test_compose_experiment_name_preserves_the_operator_run_name_verbatim() -> None:
    run_id = "a" * 32
    composed = compose_experiment_name("my-run_name.v2", run_id)
    assert composed.startswith("my-run_name.v2-")


def test_compose_experiment_name_differs_for_two_invocations_sharing_a_run_name() -> None:
    """The DEBT-19 core assertion: same operator `--run` value, two
    invocations (two `run_id`s) -> two distinct experiment names, so
    the platform never merges them."""
    first = compose_experiment_name("nightly", generate_run_id())
    second = compose_experiment_name("nightly", generate_run_id())
    assert first != second


def test_compose_experiment_name_is_a_pure_function_of_its_inputs() -> None:
    run_id = "f" * 32
    assert compose_experiment_name("nightly", run_id) == compose_experiment_name(
        "nightly", run_id
    )
