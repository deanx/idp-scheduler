"""`--document` / `run_eval(documents=...)`: restrict a run to named
dataset items (2026-09-25, user decision).

The scenario: a file already validated against the trusted Action
version, re-checked against a new version built on a different LLM. Its
golden is one dataset item, so verifying it is a run over that one item.

`select_items` is pure and is where every decision lives, so it is tested
directly. Three properties matter more than the mechanics:

* **absent selectors change nothing** -- the filter is strictly additive
  and an unfiltered run must behave exactly as it always has;
* **an unmatched selector is a refusal, never an empty run** -- a
  filtered run that measured zero documents would exit 0 and read as a
  pass, which is the false-green this project exists to prevent;
* **an ambiguous selector is a refusal, never a guess** -- guessing
  spends a real extraction on a file the operator did not name.
"""

from __future__ import annotations

from typing import Any, cast

import pytest

from idp_regression.orchestration.facade import _DocumentSelectionError, select_items
from idp_regression.platform.types import DatasetItem


def _items(*document_ids: str) -> list[DatasetItem]:
    return [
        cast(
            "DatasetItem",
            {"document_id": document_id, "golden": {"fields": {}}},
        )
        for document_id in document_ids
    ]


def _ids(items: list[DatasetItem]) -> list[str]:
    return [item["document_id"] for item in items]


def test_no_selector_returns_every_item_unchanged() -> None:
    items = _items("a.pdf", "b.pdf")

    assert select_items(items, None) is items
    assert select_items(items, []) is items


def test_an_exact_document_id_selects_that_item() -> None:
    items = _items("inv-001.pdf", "inv-002.pdf")

    assert _ids(select_items(items, ["inv-002.pdf"])) == ["inv-002.pdf"]


def test_a_unique_substring_selects_that_item() -> None:
    """Operators type `inv-001`, not `inv-001.pdf`."""
    items = _items("inv-001.pdf", "inv-002.pdf")

    assert _ids(select_items(items, ["inv-001"])) == ["inv-001.pdf"]


def test_an_exact_match_wins_over_a_substring_of_another_id() -> None:
    """`a.pdf` is a substring of `extra.pdf`; naming it exactly must not
    be ambiguous."""
    items = _items("a.pdf", "extra.pdf")

    assert _ids(select_items(items, ["a.pdf"])) == ["a.pdf"]


def test_an_ambiguous_substring_is_refused_not_guessed() -> None:
    items = _items("inv-001.pdf", "inv-002.pdf")

    with pytest.raises(_DocumentSelectionError, match="matches 2 items"):
        select_items(items, ["inv-00"])


def test_an_unmatched_selector_is_refused() -> None:
    items = _items("inv-001.pdf")

    with pytest.raises(_DocumentSelectionError, match="no dataset item matches"):
        select_items(items, ["nope.pdf"])


def test_several_selectors_select_several_items_in_dataset_order() -> None:
    """Dataset order, not the order the flags were typed: the run's
    per-document sequence must not depend on how it was invoked."""
    items = _items("a.pdf", "b.pdf", "c.pdf")

    assert _ids(select_items(items, ["c.pdf", "a.pdf"])) == ["a.pdf", "c.pdf"]


def test_the_same_item_named_twice_is_processed_once() -> None:
    """A repeated selector must not spend two extractions on one file."""
    items = _items("a.pdf", "b.pdf")

    assert _ids(select_items(items, ["a.pdf", "a"])) == ["a.pdf"]


def test_selection_never_mutates_the_dataset_it_filters() -> None:
    """`golden_version` is hashed over the FULL item list (INV-04), so a
    filter that mutated it would silently change the recorded golden
    version of a filtered run."""
    items = _items("a.pdf", "b.pdf")
    before = [dict(cast("dict[str, Any]", item)) for item in items]

    select_items(items, ["a.pdf"])

    assert [dict(cast("dict[str, Any]", item)) for item in items] == before


# --- DEBT-117: programmatic callers get exact matches only ------------------


def test_exact_mode_refuses_a_selector_the_substring_fallback_would_resolve() -> None:
    """`1.pdf` is a unique substring of `inv-1.pdf`. A program that passed
    `1.pdf` meant `1.pdf`; measuring `inv-1.pdf` instead would report on a
    document it never named."""
    items = _items("inv-1.pdf", "inv-2.pdf")

    assert _ids(select_items(items, ["1.pdf"])) == ["inv-1.pdf"]  # the CLI convenience
    with pytest.raises(_DocumentSelectionError, match="exact match required"):
        select_items(items, ["1.pdf"], exact=True)


def test_exact_mode_still_selects_exact_ids() -> None:
    items = _items("inv-1.pdf", "inv-2.pdf")
    assert _ids(select_items(items, ["inv-2.pdf", "inv-1.pdf"], exact=True)) == [
        "inv-1.pdf", "inv-2.pdf",
    ]
