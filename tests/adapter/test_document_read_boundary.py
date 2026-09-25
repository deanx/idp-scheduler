"""The local-document read boundary: symlink swap (DEBT-52) and size cap (DEBT-54 A-2).

Both defects live at the same `open()` in `transport.post_multipart_file`,
which is the only place this codebase reads a customer document off disk.
"""

from __future__ import annotations

import pathlib

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPTransportError


def test_a_symlink_at_the_document_path_is_refused_not_followed(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-52: the TOCTOU window between the containment check and the read.

    `_resolve_document_path` resolves symlinks with `os.path.realpath`
    and proves the result is inside `IDP_DOCUMENT_DIR`. But the adapter
    `open()`s that path LATER, so anything able to write to the document
    directory can swap the file for a symlink in between -- and the read
    would follow it straight out of the containment the orchestrator had
    just verified.

    Closed at the read itself with `O_NOFOLLOW`: a symlink in the final
    position fails the open rather than being resolved. This is the
    check-and-use pair being made atomic on the thing actually used --
    the file descriptor -- instead of re-checking a path that can change
    again the moment the check returns.
    """
    secret = tmp_path / "outside-the-root.txt"
    secret.write_bytes(b"CONTENTS THAT MUST NEVER BE UPLOADED")
    document_dir = tmp_path / "docs"
    document_dir.mkdir()
    planted = document_dir / "invoice.pdf"
    planted.symlink_to(secret)

    # `_urlopen` is stubbed with a DISTINGUISHABLE marker so this test
    # cannot pass for the wrong reason. Without it the call fails at the
    # network anyway (the host is unresolvable) and `pytest.raises`
    # would go green whether or not the symlink was ever refused --
    # a test that constrains the setup instead of the invariant.
    def _marker_urlopen(req: object, timeout: float) -> object:
        raise IDPTransportError("MARKER: the read was allowed and we reached the network")

    monkeypatch.setattr(transport, "_urlopen", _marker_urlopen)
    with pytest.raises(IDPTransportError) as excinfo:
        transport.post_multipart_file(
            "https://example.invalid/submit", "file", str(planted), 5.0
        )

    assert "MARKER" not in str(excinfo.value), (
        "the symlink was followed: the read succeeded and reached the network"
    )
    assert "failed to read the local document file" in str(excinfo.value)


def test_a_regular_file_is_still_read_normally(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The O_NOFOLLOW guard must not break the ordinary case.

    Pinned because a guard that refuses everything also passes the test
    above, and would take every real document with it.
    """
    document = tmp_path / "invoice.pdf"
    document.write_bytes(b"%PDF-1.4 real bytes")
    captured: dict[str, object] = {}

    def _fake_urlopen(req: object, timeout: float) -> object:
        captured["body"] = getattr(req, "data", None)
        raise IDPTransportError("stop here -- the read already happened")

    monkeypatch.setattr(transport, "_urlopen", _fake_urlopen)
    with pytest.raises(IDPTransportError):
        transport.post_multipart_file(
            "https://example.invalid/submit", "file", str(document), 5.0
        )

    body = captured["body"]
    assert isinstance(body, bytes)
    assert b"%PDF-1.4 real bytes" in body


def test_a_document_over_the_size_cap_is_refused_before_it_is_read_into_memory(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """DEBT-54 A-2: `fh.read()` was unbounded.

    The whole file was pulled into memory and then COPIED again into the
    multipart body -- so peak usage is twice the file size, and a large
    or hostile file raises `MemoryError` inside the escaping class
    GAP-1 hardened, which is the one exception shape that does not
    behave like the typed errors every caller expects.

    The size is taken with `os.fstat` on the ALREADY-OPEN descriptor,
    not `os.stat` on the path: stat-then-open is the same TOCTOU pair
    this module just closed, and checking the size of a file other than
    the one being read would be a guard in name only.
    """
    document = tmp_path / "huge.pdf"
    document.write_bytes(b"x" * (transport.MAX_DOCUMENT_BYTES + 1))

    # Independent review (Atchim, 2026-09-25) caught this test vacuous in
    # its first form: it asserted only `pytest.raises(IDPTransportError)`
    # against an unresolvable host, and `_send`'s transport catch-all
    # turns the DNS failure into that same exception type -- so it passed
    # with the size cap deleted entirely. The identical defect had been
    # found and fixed for the symlink test one function above and not
    # carried across to this sibling. Both halves of the fix matter: the
    # marker proves the read was refused BEFORE the network, and the
    # `match` proves it was refused by the CAP rather than by any other
    # read failure.
    def _marker_urlopen(req: object, timeout: float) -> object:
        raise IDPTransportError("MARKER: the read was allowed and we reached the network")

    monkeypatch.setattr(transport, "_urlopen", _marker_urlopen)
    with pytest.raises(IDPTransportError, match="exceeds the maximum size") as excinfo:
        transport.post_multipart_file(
            "https://example.invalid/submit", "file", str(document), 5.0
        )
    assert "MARKER" not in str(excinfo.value)


def test_a_document_exactly_at_the_size_cap_is_accepted(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The boundary is `>`, not `>=` -- pinned so an off-by-one that
    rejects a document exactly at the documented limit cannot ship."""
    document = tmp_path / "exact.pdf"
    document.write_bytes(b"x" * transport.MAX_DOCUMENT_BYTES)

    def _fake_urlopen(req: object, timeout: float) -> object:
        raise IDPTransportError("read succeeded; stop before the network")

    monkeypatch.setattr(transport, "_urlopen", _fake_urlopen)
    with pytest.raises(IDPTransportError, match="read succeeded"):
        transport.post_multipart_file(
            "https://example.invalid/submit", "file", str(document), 5.0
        )
