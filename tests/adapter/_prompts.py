"""Test helper: build MuleSoft's DOCUMENTED `prompts` shape from a list of
entries (DEBT-69(a), 2026-09-27).

The adapter's prompt tests were written against a LIST of entries, a shape
no MuleSoft documentation describes. The documented one is a map keyed by
the prompt's name (docs.mulesoft.com/idp/integrating-idp-with-anypoint-studio):

    "prompts": {"business": {"prompt": "...", "source": "document",
                             "answer": {"value": null}}}

Each existing test keeps its entries and its intent; this converts them. The
names are synthetic (`p0`, `p1`, ...), because `normalize()` keys prompts by
their question text, not their name. So two entries sharing a question are
still the duplicate they were in the list form.
"""

from __future__ import annotations

from typing import Any


def _as_documented_map(entries: list[Any]) -> dict[str, Any]:
    return {f"p{index}": entry for index, entry in enumerate(entries)}
