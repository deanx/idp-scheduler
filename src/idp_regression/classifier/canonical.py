"""Per-type canonical comparison (ADR-0003 — Strategy pattern, idiomatic).

Each field type registers two pure comparison tiers:

* **value tier** — the strict canonical form. Equal here -> ``match``.
* **format tier** — a looser, alphanumeric/whitespace-insensitive form.
  Equal here but not in the value tier -> ``wrong_format``.
  Otherwise -> ``wrong_value``.

Per ADR-0003 / AC4:
* ``number`` — numeric compare (strip currency symbols, thousands separators;
  ``1250.00`` == ``$1,250.00``).
* ``date`` — the *value* tier is the exact string; the *format* tier is the
  parsed ISO date, so ``"2024-03-15"`` vs ``"March 15, 2024"`` is
  ``wrong_format`` (not ``match`` and not ``wrong_value``) per AC4/EX-A1-4.
* ``id`` — exact string is the value tier; alphanumeric-strip is the format
  tier (``"INV 001"`` vs ``"INV-001"`` -> ``wrong_format``).
* ``text`` — exact string is the value tier; whitespace-normalised is the
  format tier.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime
from typing import NamedTuple

from idp_regression.classifier.types import VerdictLiteral

_DATE_FORMATS = (
    "%Y-%m-%d",
    "%m/%d/%Y",
    "%d/%m/%Y",
    "%B %d, %Y",
    "%b %d, %Y",
    "%d %b %Y",
    "%d %B %Y",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%Y/%m/%d",
)


def is_empty(value: object) -> bool:
    """A value that was not read: ``None``, or blank after stripping.

    The single definition. `gate.py` and every scorer use this one --
    there were three identical private copies before 2026-09-25, and a
    rule about what "nothing was read" means is exactly the kind of thing
    that must not be able to differ between the fan-out and the scorer
    deciding on it.
    """
    return value is None or (isinstance(value, str) and value.strip() == "")


def _value_number(value: str) -> str | float:
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    try:
        return round(float(cleaned), 2)
    except ValueError:
        return value.strip()


def _value_date(value: str) -> str:
    # Value tier for date is the exact (trimmed) string — format differences
    # must surface as wrong_format per AC4, not be folded into match.
    return value.strip()


def _value_id(value: str) -> str:
    return value.strip()


def _value_text(value: str) -> str:
    return value.strip()


def _format_number(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _format_date(value: str) -> str:
    s = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            continue
    return s.lower()


def _format_date_with(value: str, date_format: str) -> str:
    """Parse ``value`` with the DECLARED format first, then fall back.

    The fallback matters: a golden declares the format its own expected
    value is written in, but the extractor's actual value is whatever the
    new Action version emitted -- commonly ISO, which the declared
    pattern will not parse. Falling back to `_format_date` means a
    declared format disambiguates the ambiguous side without rejecting
    the unambiguous one.
    """
    try:
        return datetime.strptime(value.strip(), date_format).date().isoformat()
    except ValueError:
        return _format_date(value)


def _format_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _format_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _Canonicalizer(NamedTuple):
    """A (value_form, format_form) pair of pure callables for one field type.

    ADR-0003 §Design patterns: "a module-level registry dict + small
    functions, not a class hierarchy." This NamedTuple of two callables
    replaces the former _Number/_Date/_Id/_TextCanonicalizer classes and the
    Protocol — same two-method surface (``value_form`` / ``format_form``),
    no state, no inheritance.
    """

    value_form: Callable[[str], object]
    format_form: Callable[[str], object]


#: Registry of per-type canonicalizers (ADR-0003 Strategy pattern, idiomatic).
#: Adding a new type is "add a _Canonicalizer(value_fn, format_fn) entry".
CANONICALIZERS: dict[str, _Canonicalizer] = {
    "number": _Canonicalizer(_value_number, _format_number),
    "date": _Canonicalizer(_value_date, _format_date),
    "id": _Canonicalizer(_value_id, _format_id),
    "text": _Canonicalizer(_value_text, _format_text),
}


def match_key_form(value: str) -> str:
    """Normalize a line-item ``match_key`` value for row pairing (BR8).

    Case- and punctuation-insensitive so reordered/retyped rows still pair.
    """
    return re.sub(r"[^a-z0-9]", "", value.lower())


def compare_value(
    field_type: str, expected: str, actual: str, *, date_format: str | None = None
) -> VerdictLiteral:
    """Compare an actual value against the expected under the type's tiers.

    Returns ``match`` if equal under the value tier, ``wrong_format`` if equal
    only under the format tier, else ``wrong_value``. Assumes ``actual`` is a
    non-empty string (the ``missing`` check is the caller's responsibility).

    ``date_format`` (D2b, user decision 2026-09-25, closing DEBT-81) is an
    optional ``strptime`` pattern that resolves an ambiguous date. It is a
    KEYWORD with a default because this function is the public
    scorer-authoring API (``classifier/scoring.py``); every custom scorer
    calls it with three positional arguments, and a signature change here
    would break scorers this project does not own.

    Why it is needed: ``_DATE_FORMATS`` tries ``%m/%d/%Y`` before
    ``%d/%m/%Y``, so any ``NN/NN/YYYY`` whose day is 12 or less is read
    American-first, silently. A European ``03/04/2024`` (3 April) is
    compared as 4 March, and the gate then reports a confident verdict
    about the wrong calendar date.

    Ignored for every type but ``date``, so a scorer may pass it
    unconditionally without having to branch on the field's type.
    """
    canonicalizer = CANONICALIZERS[field_type]
    if canonicalizer.value_form(expected) == canonicalizer.value_form(actual):
        return "match"
    # `date_format` applies to the FORMAT tier, which for a date is the
    # tier that asks "which calendar date does this string denote" --
    # `_format_date`'s lenient parse. The value tier is the strict literal
    # form and is deliberately untouched: two spellings of one date are a
    # formatting difference (ADR-0003), and declaring a format resolves
    # WHICH date is meant, never whether the notation changed.
    if field_type == "date" and date_format:
        if _format_date_with(expected, date_format) == _format_date_with(actual, date_format):
            return "wrong_format"
        return "wrong_value"
    if canonicalizer.format_form(expected) == canonicalizer.format_form(actual):
        return "wrong_format"
    return "wrong_value"