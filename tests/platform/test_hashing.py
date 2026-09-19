"""hash_dataset — single-fetch content-hash golden versioning (ADR-0001, INV-04)."""

from __future__ import annotations

from idp_regression.platform.hashing import hash_dataset


def _dataset() -> list[dict[str, object]]:
    return [
        {
            "document_id": "invoice-007.pdf",
            "golden": {"fields": {"total": {"value": "1250.00", "type": "number"}}},
        },
        {
            "document_id": "invoice-008.pdf",
            "golden": {"fields": {"total": {"value": "99.00", "type": "number"}}},
        },
    ]


def test_hash_is_stable_across_calls() -> None:
    dataset = _dataset()
    assert hash_dataset(dataset) == hash_dataset(dataset)


def test_hash_is_a_64_char_hex_sha256() -> None:
    digest = hash_dataset(_dataset())
    assert len(digest) == 64
    int(digest, 16)  # raises if not hex


def test_hash_is_order_independent_of_key_insertion_order() -> None:
    # Same content, keys inserted in a different order — canonical encoding
    # must produce the same hash (dict key order is not semantic content).
    reordered = [
        {
            "golden": {"fields": {"total": {"type": "number", "value": "1250.00"}}},
            "document_id": "invoice-007.pdf",
        },
        {
            "golden": {"fields": {"total": {"type": "number", "value": "99.00"}}},
            "document_id": "invoice-008.pdf",
        },
    ]
    assert hash_dataset(reordered) == hash_dataset(_dataset())


def test_hash_changes_when_content_changes() -> None:
    original = _dataset()
    changed = _dataset()
    changed[0]["golden"] = {"fields": {"total": {"value": "1250.01", "type": "number"}}}
    assert hash_dataset(original) != hash_dataset(changed)


def test_hash_changes_when_document_order_changes() -> None:
    dataset = _dataset()
    reversed_dataset = list(reversed(dataset))
    assert hash_dataset(dataset) != hash_dataset(reversed_dataset)


def test_hash_matches_a_known_digest_literal_for_a_fixed_dataset() -> None:
    """Gap 6: pin the exact encoding with a known-digest literal, not just
    stability/order-independence properties — computed independently of
    hashing.py, 2026-09-19, over _dataset()'s exact content."""
    assert (
        hash_dataset(_dataset())
        == "f25b68241785eef9d99e23a5507943182da6494036cb33f8d8dd11a78877498b"
    )
