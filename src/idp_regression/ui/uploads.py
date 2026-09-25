"""ZIP upload: validate first, unpack only on request.

A corpus arrives as an archive. Two things have to happen before anyone
spends a single extraction on it, and this module does them in that
order:

1. **Validate.** `_batch.extract_documents_from_zip(dry_run=True)` runs
   every trust-boundary check -- zip-slip, symlinks, entry-count,
   uncompressed-size and per-entry compression-ratio caps -- and reports
   what WOULD be written, without writing a byte. The browser gets the
   same refusals the CLI would give, with the same reasons.
2. **Cost it.** A batch is N extractions, or 2N for a pin-then-verify
   pair. The count is shown before an `--yes` equivalent exists in the
   UI at all, because "pointing at a folder never implies a batch".

Unpacking is a separate, explicit call. Uploading an archive to look at
it is not consent to write its contents to disk, and it is certainly not
consent to spend quota.

**This module never spends quota and never calls IDP.** The UI's job
stops at a validated directory and a printed command; running it stays
with the operator at a terminal, where the `--yes` and the quota cost are
already in front of them.
"""

from __future__ import annotations

import datetime as dt
import uuid
from pathlib import Path
from typing import Any

from idp_regression.ui import workspace
from idp_regression.ui.reader import list_pins
from idp_regression.ui.scripts_bridge import load


#: Uploaded archives are staged here, owner-only, under the workspace so
#: a job started from the console finds them where the console put them.
#: A customer's documents are as sensitive as the values extracted from
#: them.
def UPLOAD_DIR() -> Path:  # noqa: N802 - see workspace.py
    return workspace.upload_dir()


def DEFAULT_EXTRACT_DIR() -> Path:  # noqa: N802
    """Where an unpacked corpus lands. This is the directory
    `IDP_DOCUMENT_DIR` must then point at -- the golden set names those
    files and nothing copies them again."""
    return workspace.upload_dir() / "documents"

MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024


class UploadRejectedError(Exception):
    """The archive was refused as a whole, with the reason the batch
    tools would have given."""


def _batch() -> Any:
    return load("_batch")


def stage_archive(
    filename: str, payload: bytes, upload_dir: Path | None = None
) -> tuple[str, Path]:
    """Write an uploaded archive to private staging and return its id.

    The stored name is a generated id, never the client's filename: a
    browser upload is untrusted input and `../` in a filename is the
    oldest trick there is. The original name is carried separately, for
    display only.
    """
    if len(payload) > MAX_UPLOAD_BYTES:
        raise UploadRejectedError(
            f"{filename}: {len(payload) / 1e9:.1f} GB upload is over the "
            f"{MAX_UPLOAD_BYTES / 1e9:.0f} GB cap"
        )
    if not payload:
        raise UploadRejectedError(f"{filename}: empty upload")

    root = upload_dir or UPLOAD_DIR()
    batch = _batch()
    batch.ensure_private_dir(root)
    upload_id = uuid.uuid4().hex
    target = root / f"{upload_id}.zip"
    target.write_bytes(payload)
    target.chmod(batch.FILE_MODE)
    (root / f"{upload_id}.name").write_text(Path(filename).name, encoding="utf-8")
    return upload_id, target


def _archive_path(upload_id: str, upload_dir: Path | None = None) -> Path:
    root = (upload_dir or UPLOAD_DIR()).resolve()
    if not upload_id.isalnum() or len(upload_id) != 32:
        raise FileNotFoundError(f"{upload_id!r} is not an upload id")
    path = (root / f"{upload_id}.zip").resolve()
    if path.parent != root or not path.is_file():
        raise FileNotFoundError(f"no staged archive {upload_id!r}")
    return path


def original_name(upload_id: str, upload_dir: Path | None = None) -> str:
    root = upload_dir or UPLOAD_DIR()
    marker = root / f"{upload_id}.name"
    if marker.is_file():
        return marker.read_text(encoding="utf-8").strip()
    return f"{upload_id}.zip"


def validate(
    upload_id: str,
    *,
    patterns: str | None = None,
    upload_dir: Path | None = None,
    pin_store: Path | None = None,
) -> dict[str, Any]:
    """Dry-run the archive and report what it holds.

    Returns the accepted document names, every skipped entry WITH its
    reason, the extraction cost, and -- because the question after "is
    this archive sane" is always "have I paid for these already" -- which
    of the documents are already pinned, and at which versions.
    """
    batch = _batch()
    archive = _archive_path(upload_id, upload_dir)
    patterns = patterns or batch.DEFAULT_DOCUMENT_PATTERNS
    try:
        planned, skipped = batch.extract_documents_from_zip(
            archive, workspace.upload_dir() / ".plan", patterns=patterns, dry_run=True
        )
    except batch.ZipRejectedError as exc:
        raise UploadRejectedError(str(exc)) from None

    names = [p.name for p in planned]
    pinned = _pinned_index(pin_store)
    return {
        "upload_id": upload_id,
        "filename": original_name(upload_id, upload_dir),
        "size_bytes": archive.stat().st_size,
        "uploaded_at": dt.datetime.fromtimestamp(archive.stat().st_mtime, dt.UTC).isoformat(),
        "patterns": patterns,
        "documents": [
            {"name": name, "pinned_at": pinned.get(name, [])} for name in names
        ],
        "document_count": len(names),
        "skipped": skipped,
        "rejected_entries": [s for s in skipped if "rejected" in s or "symlink" in s],
        "cost": {
            "pin_only": len(names),
            "verify_only": len(names),
            "pin_and_verify": 2 * len(names),
            "noise_floor_two_repeats": 2 * min(len(names), 100),
        },
        "already_pinned": sum(1 for name in names if name in pinned),
    }


def _pinned_index(pin_store: Path | None = None) -> dict[str, list[dict[str, str]]]:
    """`document_id -> [{action_id, action_version}, ...]`."""
    index: dict[str, list[dict[str, str]]] = {}
    for group in list_pins(pin_store or workspace.pin_store_dir()):
        for document in group["documents"]:
            index.setdefault(document["document_id"], []).append({
                "action_id": group["action_id"],
                "action_version": group["action_version"],
            })
    return index


def unpack(
    upload_id: str,
    *,
    destination: Path | None = None,
    patterns: str | None = None,
    upload_dir: Path | None = None,
) -> dict[str, Any]:
    """Actually write the documents out, flat and owner-only.

    Separate from `validate` on purpose: the validation answers "is this
    archive safe and what does it cost", and only a second, explicit act
    puts customer documents on this disk.
    """
    batch = _batch()
    archive = _archive_path(upload_id, upload_dir)
    target = destination or (DEFAULT_EXTRACT_DIR() / upload_id)
    try:
        extracted, skipped = batch.extract_documents_from_zip(
            archive, target, patterns=patterns or batch.DEFAULT_DOCUMENT_PATTERNS
        )
    except batch.ZipRejectedError as exc:
        raise UploadRejectedError(str(exc)) from None
    return {
        "upload_id": upload_id,
        "document_dir": str(target.resolve()),
        "documents": [p.name for p in extracted],
        "document_count": len(extracted),
        "skipped": skipped,
    }


def next_commands(document_dir: str, document_count: int) -> list[dict[str, str]]:
    """The exact commands to run against an unpacked corpus.

    Printed rather than executed. Each one spends real IDP quota against
    a live org, and its `--yes` belongs in front of the person who pays
    for it -- the UI is localhost-bound and unauthenticated, which is
    precisely the wrong place to put a button that costs money.
    """
    return [
        {
            "label": f"Noise floor first ({min(document_count, 100) * 2} extractions)",
            "why": (
                "measures the extractor's disagreement with ITSELF "
                "before any two versions are compared"
            ),
            "command": (
                f".venv/bin/python scripts/noise_floor.py --document-dir {document_dir} "
                "--org <id> --action <id> --version <incumbent-v> --repeats 2 --plan"
            ),
        },
        {
            "label": f"Pin every document to the trusted version ({document_count} extractions)",
            "why": "records the trusted version's reading of each file as its golden",
            "command": (
                f".venv/bin/python scripts/pin_document.py --document-dir {document_dir} --all "
                "--dataset <name> --org <id> --action <id> --version <trusted-v> --yes"
            ),
        },
        {
            "label": f"Verify against a new version ({document_count} extractions)",
            "why": "re-reads each pinned file with the new version through the ordinary gate",
            "command": (
                f".venv/bin/python scripts/verify_document.py --all --document-dir {document_dir} "
                "--dataset <name> --version <new-v> --yes"
            ),
        },
        {
            "label": f"Both halves in one command ({2 * document_count} extractions)",
            "why": "unpacks once, so the bytes compared are provably the bytes pinned",
            "command": (
                f".venv/bin/python scripts/compare_versions.py --document-dir {document_dir} "
                "--dataset <name> --org <id> --action <id> "
                "--trusted-version <v1> --candidate-version <v2> --plan"
            ),
        },
    ]
