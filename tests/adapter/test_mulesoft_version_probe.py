"""MuleSoftVersionProbe — the concrete IDPVersionProbe implementation
(ADR-0006 §A'.1): reuses transport's shared no-redirect opener and a
TokenCache, never a second HTTP path (D14)."""

from __future__ import annotations

import pytest

from idp_regression.adapter import transport, version_probe
from idp_regression.adapter.errors import IDPTransportError
from idp_regression.adapter.version_probe import MuleSoftVersionProbe, ProbeResult


def _probe(monkeypatch: pytest.MonkeyPatch, response: tuple[int, object]) -> MuleSoftVersionProbe:
    monkeypatch.setattr(
        version_probe, "fetch_access_token", lambda *a, **k: ("tok-123", 3600.0)
    )
    monkeypatch.setattr(
        transport,
        "post_empty_multipart",
        lambda *a, **k: response,
    )
    return MuleSoftVersionProbe("cid", "csecret", "us-east-1", clock=lambda: 0.0)


def test_probe_exists_on_the_pinned_400_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _probe(monkeypatch, (400, {"detail": "Invalid query parameter 'file'"}))
    assert p.probe("org", "action", "1.0.0") is ProbeResult.EXISTS


def test_probe_absent_on_the_pinned_404_detail(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _probe(
        monkeypatch,
        (404, {"detail": "Document Action Id: abc and version 9.9.9 not found"}),
    )
    assert p.probe("org", "action", "9.9.9") is ProbeResult.ABSENT


def test_probe_unknown_on_401(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _probe(monkeypatch, (401, {"detail": "unauthorized"}))
    assert p.probe("org", "action", "1.0.0") is ProbeResult.UNKNOWN
    assert p.last_status_code == 401


def test_probe_unknown_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(*a: object, **k: object) -> tuple[int, object]:
        raise IDPTransportError("boom")

    monkeypatch.setattr(version_probe, "fetch_access_token", lambda *a, **k: ("tok", 3600.0))
    monkeypatch.setattr(transport, "post_empty_multipart", _raise)
    p = MuleSoftVersionProbe("cid", "csecret", "us-east-1", clock=lambda: 0.0)
    assert p.probe("org", "action", "1.0.0") is ProbeResult.UNKNOWN
    assert p.last_status_code is None


def test_probe_rejects_an_off_grammar_version_before_reaching_the_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Defence-in-depth (ADR-0006 §A'.1): even though the candidate is
    LOCALLY generated, a generator bug producing e.g. a path-traversal
    string must never reach a URL. Proven by asserting the transport call
    never happens."""
    calls: list[str] = []
    monkeypatch.setattr(version_probe, "fetch_access_token", lambda *a, **k: ("tok", 3600.0))

    def _spy(*a: object, **k: object) -> tuple[int, object]:
        calls.append("called")
        return (400, {"detail": "Invalid query parameter 'file'"})

    monkeypatch.setattr(transport, "post_empty_multipart", _spy)
    p = MuleSoftVersionProbe("cid", "csecret", "us-east-1", clock=lambda: 0.0)
    assert p.probe("org", "action", "../../etc/passwd") is ProbeResult.UNKNOWN
    assert calls == []


def test_probe_body_not_a_dict_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    p = _probe(monkeypatch, (400, "not a dict"))
    assert p.probe("org", "action", "1.0.0") is ProbeResult.UNKNOWN
