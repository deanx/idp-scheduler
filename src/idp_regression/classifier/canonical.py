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
from datetime import datetime
from typing import Protocol

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


def _is_empty(value: object) -> bool:
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


def _format_id(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _format_text(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


class _Canonicalizer(Protocol):
    def value_form(self, value: str) -> object: ...
    def format_form(self, value: str) -> object: ...


class _NumberCanonicalizer:
    def value_form(self, value: str) -> object:
        return _value_number(value)

    def format_form(self, value: str) -> object:
        return _format_number(value)


class _DateCanonicalizer:
    def value_form(self, value: str) -> object:
        return _value_date(value)

    def format_form(self, value: str) -> object:
        return _format_date(value)


class _IdCanonicalizer:
    def value_form(self, value: str) -> object:
        return _value_id(value)

    def format_form(self, value: str) -> object:
        return _format_id(value)


class _TextCanonicalizer:
    def value_form(self, value: str) -> object:
        return _value_text(value)

    def format_form(self, value: str) -> object:
        return _format_text(value)


#: Registry of per-type canonicalizers (ADR-0003 Strategy pattern). Adding a
#: new type is "add a class and register it".
CANONICALIZERS: dict[str, _Canonicalizer] = {
    "number": _NumberCanonicalizer(),
    "date": _DateCanonicalizer(),
    "id": _IdCanonicalizer(),
    "text": _TextCanonicalizer(),
}


def compare_value(field_type: str, expected: str, actual: str) -> VerdictLiteral:
    """Compare an actual value against the expected under the type's tiers.

    Returns ``match`` if equal under the value tier, ``wrong_format`` if equal
    only under the format tier, else ``wrong_value``. Assumes ``actual`` is a
    non-empty string (the ``missing`` check is the caller's responsibility).
    """
    canonicalizer = CANONICALIZERS[field_type]
    if canonicalizer.value_form(expected) == canonicalizer.value_form(actual):
        return "match"
    if canonicalizer.format_form(expected) == canonicalizer.format_form(actual):
        return "wrong_format"
    return "wrong_value"