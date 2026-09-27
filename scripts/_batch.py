"""Shared machinery for the two batch operator tools (2026-09-24).

    scripts/bootstrap_golden_set.py   PDFs -> a DRAFT golden set      (#1)
    scripts/noise_floor.py            PDFs -> a self-consistency report (#2)

Both walk a directory of documents, spend real IDP extraction quota, and
must survive a mid-batch failure without losing what they already paid
for. That shared shape lives here so the two scripts differ only in what
they do with each result.

Three rules this module exists to enforce
-----------------------------------------
1. **Quota is spent only on an explicit `--yes`.** Every entry point
   prints the exact number of extractions it is about to spend and
   refuses to start without the flag. A typo in `--glob` that widens a
   batch from 5 documents to 5,000 should cost a re-run, not an invoice.
2. **Nothing paid for is lost.** Captures and partial results are
   flushed after every document, so a crash at document 700 of 1,000
   leaves 699 usable results on disk, not an empty file.
3. **Outputs are owner-only.** These files carry the same extracted
   financial values `CLAUDE.md ## Domain` calls sensitive, so they are
   written `0700`/`0600`, the same posture as
   `orchestration/run_artifact.py`.

Run identity (`--org` / `--action` / `--version`) is always a flag, never
an environment fallback (ADR-0004 A8/A9): what a batch measured must be
visible in the invocation that produced it.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import unicodedata
import zipfile
from pathlib import Path
from typing import Any, Protocol

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "src"))

#: Same posture, and for the same reason, as `run_artifact._DIR_MODE` /
#: `_FILE_MODE`: local disk is only a narrower disclosure surface than
#: the platform if it is actually private to the invoking user.
DIR_MODE = 0o700
FILE_MODE = 0o600


class SupportsExtract(Protocol):
    """The slice of the IDP adapter these tools consume. Declared as a
    Protocol so the tests can drive the whole batch loop with a fake and
    never touch the network."""

    def extract(self, document_path: str, action_id: str, version: str) -> dict[str, Any]: ...


# ── document discovery ────────────────────────────────────────────────

#: What a customer's document folder actually contains. IDP takes images
#: as readily as PDFs, and a scanned-invoice corpus is usually TIFFs or
#: JPEGs, so defaulting to `*.pdf` alone would silently process a handful
#: of files out of a zip of thousands and report success. Comma-separated
#: so `--glob` can narrow it without a new flag.
DEFAULT_DOCUMENT_PATTERNS = "*.pdf,*.png,*.jpg,*.jpeg,*.tif,*.tiff,*.webp,*.bmp"


def discover_documents(document_dir: Path, pattern: str, limit: int | None) -> list[Path]:
    """Documents to process, sorted, capped at `limit` (`None`: every one).

    **A cap is a SAMPLE.** Only the noise floor, which measures a sample
    by design, should pass a number here. A tool that measures or pins a
    CORPUS passes `None` and then calls `refuse_over_ceiling`: truncating
    a corpus silently is how `compare_versions` once reported four
    documents `STILL VALID` for a ten-document zip and exited 0 (DEBT-86).

    Sorted so two runs over an unchanged directory process the same
    documents in the same order -- a noise-floor number computed over a
    different sample each time is not a baseline. Capped because `limit`
    is the last line of defence between a wrong `--glob` and the org's
    extraction quota.

    Only regular files are returned, and a symlink pointing outside
    `document_dir` is skipped: these tools are pointed at customer
    document directories, and a batch tool should not follow a link out
    of the directory the operator named.
    """
    resolved_dir = document_dir.resolve()
    found: list[Path] = []
    seen: set[Path] = set()
    # Several patterns, because one glob cannot describe "the documents in
    # this folder" -- and a file matching two of them is still one file.
    for one in (p.strip() for p in pattern.split(",") if p.strip()):
        for candidate in document_dir.glob(one):
            if candidate in seen or not candidate.is_file():
                continue
            if not candidate.resolve().is_relative_to(resolved_dir):
                print(
                    f"  skipping {candidate.name}: resolves outside --document-dir",
                    file=sys.stderr,
                )
                continue
            seen.add(candidate)
            found.append(candidate)
    return sorted(found) if limit is None else sorted(found)[:limit]


def pinned_elsewhere(
    store: Path, dataset: str, names: set[str], own_dir: Path
) -> dict[str, str]:
    """`{document: "action/version"}` for each of `names` that the store also
    has pinned for `dataset` under a DIFFERENT `goldens/<action>/<version>/`.

    The platform keys a pinned item on `uuid5(dataset|document_id)` -- no
    action, no version. So one document pinned into one dataset at two
    versions is two goldens on disk but ONE item on the platform, holding
    whichever was provisioned last; verifying "against v1" then compares
    against v2's reading and says `pinned against v1` (DEBT-89). A
    (dataset, document) may therefore be pinned at one action/version only.
    Reads the store's own `_pins.json` files: a second store writing the
    same dataset is outside what this can see."""
    root = store / "goldens"
    conflicts: dict[str, str] = {}
    if not root.is_dir():
        return conflicts
    own = own_dir.resolve()
    for pins_file in sorted(root.glob("*/*/_pins.json")):
        if pins_file.parent.resolve() == own:
            continue
        pins = read_json_if_present(pins_file)
        for name, record in pins.items():
            if name in names and isinstance(record, dict) and record.get("dataset") == dataset:
                conflicts.setdefault(
                    name, f"{pins_file.parent.parent.name}/{pins_file.parent.name}"
                )
    return conflicts


class CorpusTooLargeError(Exception):
    """More documents than the ceiling -- refused, never truncated."""


def refuse_over_ceiling(
    documents: list[Path], limit: int, *, flag: str = "--max-documents"
) -> None:
    """Raise `CorpusTooLargeError` when a corpus exceeds its ceiling.

    The ceiling is a guard against spending quota on a wrong `--glob`, and
    the only honest response to tripping it is to stop -- the same rule
    `run_eval` applies to `--max-documents-per-run` (ADR-0004 A10). Taking
    the first N instead produces a result about N documents that every
    downstream line reports as a result about the corpus (DEBT-86)."""
    if len(documents) > limit:
        raise CorpusTooLargeError(
            f"{len(documents)} documents found, {flag} is {limit}. Refusing rather than "
            f"measuring the first {limit} and reporting on the whole corpus -- raise "
            f"{flag} to at least {len(documents)}, or narrow --glob / the source."
        )


# ── cost guard ────────────────────────────────────────────────────────


class QuotaRefusedError(Exception):
    """The batch would spend quota and `--yes` was not passed."""


def confirm_cost(*, documents: int, extractions_each: int, approved: bool) -> int:
    """Return the number of extractions this batch will spend, or raise
    `QuotaRefusedError` if the operator has not approved it.

    Deliberately a flag and not an interactive prompt: these tools are
    also run from a scheduler, where a prompt is a hang, and an approval
    that lives in the invocation is an approval a reviewer can see.
    """
    total = documents * extractions_each
    # One caller (the pipeline) has already summed its stages and passes
    # the total with `extractions_each=1`; printing "12 documents x 1"
    # there would misdescribe what was counted.
    breakdown = (
        f"{documents} document(s) x {extractions_each} extraction(s) = "
        if extractions_each > 1
        else ""
    )
    print(f"  {breakdown}{total} real IDP extraction(s) to be spent", file=sys.stderr)
    if not approved:
        raise QuotaRefusedError(
            f"this batch would spend {total} real IDP extraction(s) against your org's "
            "quota and process real documents. Re-run with --yes once that number is "
            "the number you meant."
        )
    return total


# ── owner-only output ─────────────────────────────────────────────────


def ensure_private_dir(path: Path) -> None:
    """Create `path` (and parents) owner-only, tightening it if it
    already exists world-readable -- `makedirs` does not chmod an
    existing directory, so an operator who created it by hand would
    otherwise keep a world-readable copy of extracted values."""
    path.mkdir(parents=True, exist_ok=True)
    os.chmod(path, DIR_MODE)


def make_private_parents(path: Path) -> None:
    """Create every MISSING directory of `path`, owner-only. An existing
    directory is never touched.

    `ensure_private_dir` tightens an existing directory on purpose, for
    directories a tool OWNS (a captures dir, an extraction target). Applied
    to the parent of an arbitrary output file it chmodded whatever the
    operator happened to name: `--out golden.json` set the working directory
    to 0700, `--extract-to ~/x` their folder, and `--out /tmp/floor.json`
    raised PermissionError on root-owned `/tmp` -- in `noise_floor`, AFTER
    every extraction had been paid for (DEBT-99). An output file is
    protected by its own 0600 mode; its parent is the operator's."""
    missing: list[Path] = []
    current = path
    while not current.exists():
        missing.append(current)
        current = current.parent
    for directory in reversed(missing):
        # A umask can only REMOVE bits, so 0o700 cannot come out wider.
        directory.mkdir(mode=DIR_MODE)


class OutputNotWritableError(Exception):
    """The output path cannot be written -- raised BEFORE any quota is spent."""


def assert_writable_output(path: Path) -> None:
    """Fail before spending, not after. Creates missing parents (owner-only,
    as `make_private_parents` does) and checks the directory accepts a new
    file. A report that cannot be written after N extractions is N
    extractions lost."""
    try:
        make_private_parents(path.parent)
    except OSError as exc:
        raise OutputNotWritableError(
            f"cannot create {path.parent} for the output ({type(exc).__name__})"
        ) from None
    if not os.access(path.parent, os.W_OK | os.X_OK):
        raise OutputNotWritableError(f"{path.parent} is not writable by this user")


def write_private_json(path: Path, payload: object) -> None:
    """Write `payload` as JSON, owner-only, via a same-directory temp
    file and an atomic rename -- so a crash mid-write leaves the previous
    good file, never a truncated one a later `--resume` would fail to
    parse. The temp file is CREATED 0600 (never written first and chmodded
    after), and an existing parent directory is left as it is (DEBT-99)."""
    make_private_parents(path.parent)
    tmp = path.with_name(f".{path.name}.partial")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    os.fchmod(fd, FILE_MODE)  # a pre-existing .partial keeps its old mode otherwise
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def file_sha256(path: Path) -> str:
    """Content identity of a document. A pin, a capture and a golden are
    otherwise keyed by FILENAME, and a re-sent archive can reuse a name for
    different bytes; a golden read from the old bytes then judges the new
    ones (DEBT-100)."""
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        while chunk := fh.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def changed_since_recorded(
    documents: list[Path], recorded: dict[str, Any], *, key: str = "sha256"
) -> list[str]:
    """Names of `documents` whose bytes differ from the digest recorded for
    them. A document with no recorded digest (written before DEBT-100) is
    not listed: there is nothing to compare, and it is reported as such by
    the caller rather than guessed at."""
    changed = []
    for document in documents:
        entry = recorded.get(document.name)
        expected = entry.get(key) if isinstance(entry, dict) else entry
        if isinstance(expected, str) and document.is_file() and file_sha256(document) != expected:
            changed.append(document.name)
    return sorted(changed)


def read_json_if_present(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    with path.open(encoding="utf-8") as fh:
        loaded = json.load(fh)
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} is not a JSON object")
    return loaded


# ── the batch loop ────────────────────────────────────────────────────


class DocumentFailedError(Exception):
    """One document failed; the batch continues. Carries only the
    exception TYPE name -- never `str(exc)`, which on this seam can
    carry a token, a path or an extracted value (INV-02)."""

    def __init__(self, document_id: str, attempt: int, error_type: str) -> None:
        self.document_id = document_id
        self.attempt = attempt
        self.error_type = error_type
        super().__init__(f"{document_id} (attempt {attempt}): {error_type}")

    def as_record(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "attempt": self.attempt,
            "error_type": self.error_type,
        }


def extract_with_containment(
    adapter: SupportsExtract,
    document: Path,
    *,
    action_id: str,
    version: str,
    attempt: int = 1,
) -> dict[str, Any]:
    """`adapter.extract`, with every failure converted to a typed
    `DocumentFailedError` so one bad document cannot end a 1,000-document
    batch."""
    try:
        return adapter.extract(str(document), action_id, version)
    except Exception as exc:  # noqa: BLE001 - INV-02: type name only, never str(exc)
        raise DocumentFailedError(document.name, attempt, type(exc).__name__) from None


def progress(index: int, total: int, document_id: str, started: float) -> None:
    """One line per document. A 1,000-document batch runs for hours; an
    operator watching it needs to know it is alive and roughly when it
    ends, without any extracted value appearing in the terminal."""
    elapsed = time.monotonic() - started
    rate = elapsed / index if index else 0.0
    remaining = rate * (total - index)
    print(
        f"  [{index}/{total}] {document_id}  "
        f"({elapsed / 60:.1f} min elapsed, ~{remaining / 60:.1f} min left)",
        file=sys.stderr,
        flush=True,
    )


# ── zip input ─────────────────────────────────────────────────────────
#
# An operator hands over a zip, not a mounted folder -- that is how a
# corpus of customer documents actually arrives. Unpacking it is
# therefore part of the tool, and unpacking an archive from outside the
# trust boundary is a security surface, not a convenience: the caps and
# checks below are the point of this section, not overhead on it.

#: Whole-archive refusals. Generous enough that no honest corpus trips
#: them, tight enough that a decompression bomb cannot fill the disk of
#: whoever was handed the zip.
ZIP_MAX_ENTRIES = 20_000
ZIP_MAX_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
#: Per-entry compression ratio. Real PDFs and images are already
#: compressed and sit near 1:1; 200:1 is a file that exists to expand.
ZIP_MAX_RATIO = 200

#: Archive metadata every macOS/Windows zip carries and no corpus wants.
#: Matched by exact name or the AppleDouble `._` prefix -- NOT by a bare
#: leading dot, which would swallow a `..` traversal component into the
#: "junk" bucket and report a zip-slip attempt as ordinary noise (caught
#: by `test_zip_rejects_a_path_traversal_entry`, 2026-09-25).
_JUNK_NAMES = frozenset({"__MACOSX", ".DS_Store", "Thumbs.db", "desktop.ini"})


def _is_junk(member_name: str) -> bool:
    parts = [part for part in member_name.split("/") if part]
    return any(part in _JUNK_NAMES or part.startswith("._") for part in parts)


class ZipRejectedError(Exception):
    """The archive as a whole is refused -- unreadable, or past a cap.
    Distinct from a skipped entry, which is reported and survivable."""


def _name_key(flat: str) -> str:
    """How the FILESYSTEM compares two names. macOS's default volume is
    case-insensitive and normalization-insensitive, so `Inv.pdf` and
    `inv.pdf` -- or an NFC and an NFD `é` -- are one file there."""
    return unicodedata.normalize("NFC", flat).casefold()


def _flatten_name(member_name: str, claimed: dict[str, str]) -> str | None:
    """One flat filename per document, or None when the entry cannot have
    one without overwriting another.

    The golden's `document_id` is resolved by `run_eval` against
    `IDP_DOCUMENT_DIR` (`facade._resolve_document_path`), and a flat
    filename is the shape that survives every later step unambiguously:
    the capture file, the golden key, and the dataset item all key off
    it. So a nested `invoices/2024/a.pdf` becomes `a.pdf` -- unless that
    name is already claimed by a DIFFERENT entry, in which case the parent
    path is folded into the name (`invoices__2024__a.pdf`).

    `claimed` maps `_name_key(flat)` -> the entry that took it, and it is
    checked for EVERY candidate, the folded one included. The first
    version checked only the incoming basename, so a folded `b/x.pdf ->
    b__x.pdf` was then overwritten by a real `b__x.pdf` entry, and
    `p/Inv.pdf` and `q/inv.pdf` became one file on a case-insensitive
    disk -- while the returned list still counted both (DEBT-87).
    """
    base = os.path.basename(member_name)
    folded = member_name.replace("/", "__").replace("\\", "__")
    for candidate in (base, folded):
        owner = claimed.get(_name_key(candidate))
        if owner is None or owner == member_name:
            return candidate
    return None


def _is_symlink(member: zipfile.ZipInfo) -> bool:
    return (member.external_attr >> 16) & 0o170000 == 0o120000


def extract_documents_from_zip(
    archive: Path,
    destination: Path,
    *,
    patterns: str = DEFAULT_DOCUMENT_PATTERNS,
    dry_run: bool = False,
) -> tuple[list[Path], list[str]]:
    """Unpack the document files from `archive` into `destination`, flat.

    Returns `(extracted_paths, skipped_notes)`. Refuses the whole archive
    (`ZipRejectedError`) when it is unreadable or past a cap; skips a
    single entry, with a note, when that entry is not a document this
    tool should write:

    * **path traversal** -- an absolute name, a `..` component, or
      anything that resolves outside `destination` (zip-slip). Checked on
      the RESOLVED path, not on the name, so a name that only looks safe
      is still caught.
    * **symlinks** -- a zip can carry one, and a link is a way to make a
      later read escape `destination` even though the extraction did not.
      Never written.
    * **archive junk** -- `__MACOSX/`, `.DS_Store`, AppleDouble `._`
      files. Real entries, never documents.
    * **non-documents** -- anything not matching `patterns`. A zip of a
      customer's records carries spreadsheets and notes; spending an IDP
      extraction on a `.xlsx` is a paid-for failure.

    `dry_run` runs every check and reports what WOULD be written without
    creating a file, so `--plan` can size a zip without unpacking it.
    """
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, OSError) as exc:
        raise ZipRejectedError(
            f"{archive.name} is not a readable zip archive ({type(exc).__name__})"
        ) from None

    skipped: list[str] = []
    extracted: list[Path] = []
    with zf:
        members = zf.infolist()
        if len(members) > ZIP_MAX_ENTRIES:
            raise ZipRejectedError(
                f"{archive.name} holds {len(members)} entries, over the "
                f"{ZIP_MAX_ENTRIES} cap. Split it, or raise the cap deliberately."
            )
        declared = sum(m.file_size for m in members)
        if declared > ZIP_MAX_TOTAL_BYTES:
            raise ZipRejectedError(
                f"{archive.name} declares {declared / 1e9:.1f} GB uncompressed, over the "
                f"{ZIP_MAX_TOTAL_BYTES / 1e9:.1f} GB cap."
            )

        if not dry_run:
            ensure_private_dir(destination)
        root = destination.resolve()
        wanted = [p.strip() for p in patterns.split(",") if p.strip()]
        claimed: dict[str, str] = {}

        for member in sorted(members, key=lambda m: m.filename):
            name = member.filename
            if member.is_dir():
                continue
            # Traversal FIRST: a rejected entry must be reported as
            # rejected, never quietly filed under "junk" or "not a
            # document" -- the operator needs to know the archive tried.
            if os.path.isabs(name) or ".." in Path(name).parts or name.startswith("/"):
                skipped.append(f"{name}: path traversal (rejected)")
                continue
            if _is_symlink(member):
                skipped.append(f"{name}: symlink (never extracted)")
                continue
            if _is_junk(name):
                continue
            base = os.path.basename(name)
            if not any(Path(base).match(one) for one in wanted):
                skipped.append(f"{name}: not a document ({patterns})")
                continue
            if member.compress_size and member.file_size / member.compress_size > ZIP_MAX_RATIO:
                raise ZipRejectedError(
                    f"{archive.name}: entry {name!r} expands "
                    f"{member.file_size // max(member.compress_size, 1)}x, over the "
                    f"{ZIP_MAX_RATIO}x cap -- treated as a decompression bomb."
                )

            flat = _flatten_name(name, claimed)
            if flat is None:
                # Refuse the ARCHIVE, not the entry: skipping one would
                # leave a corpus that silently lost a document, which is
                # the outcome this check exists to prevent.
                other = claimed[_name_key(os.path.basename(name))]
                raise ZipRejectedError(
                    f"{archive.name}: entries {other!r} and {name!r} cannot be unpacked "
                    "flat without one overwriting the other (names that differ only in "
                    "case or Unicode form are the same file on this disk). Rename one "
                    "and re-send the archive."
                )
            claimed[_name_key(flat)] = name
            target = (destination / flat).resolve()
            if not target.is_relative_to(root) or target == root:
                # Belt and braces: the name checks above should already
                # have caught this, and a zip-slip that survives them is
                # exactly the case this line exists for.
                skipped.append(f"{name}: resolves outside the extraction directory")
                continue
            if dry_run:
                extracted.append(destination / flat)
                continue

            written = 0
            with zf.open(member) as src, open(target, "wb") as dst:
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > member.file_size:
                        dst.close()
                        target.unlink(missing_ok=True)
                        raise ZipRejectedError(
                            f"{archive.name}: entry {name!r} is larger than its header "
                            "declares -- refusing the archive."
                        )
                    dst.write(chunk)
            # Owner-only, like every other file these tools write: this is
            # a customer document now sitting on local disk.
            os.chmod(target, FILE_MODE)
            extracted.append(target)

    return sorted(extracted), skipped


# ── what a validation run concluded ───────────────────────────────────


def run_outcome(
    returncode: int, *, since: float, artifact_dir: Path | None = None
) -> tuple[str, str]:
    """`(verdict, reason)` for a verification: STILL VALID, CHANGED or
    RUN FAILED -- read from the RUN ARTIFACT, never from the exit code alone.

    `run_eval` returns 1 for a gate FAIL and for an abort alike, and
    `verify_document` returns 2 for its own refusals, so labelling every
    non-zero exit `CHANGED -- a critical field's value differs` blamed the
    model for plumbing failures (DEBT-98). The console's `jobs.summarize`
    already read the artifact; this is the same rule for the CLI. Only a
    run whose artifact records a failing document is CHANGED.

    `since` bounds which artifact is this run's (the newest written after
    it started). Two runs finishing in the same directory at once can
    confuse that -- the console serialises jobs with a lock; a CLI user
    running two at once gets the verdict of whichever wrote last."""
    if returncode == 0:
        return "STILL VALID", ""
    from idp_regression.classifier.gate import overall_gate
    from idp_regression.orchestration.run_artifact import (
        ARTIFACT_DIR_NAME,
        parse_run_artifact,
        run_level_gate,
    )

    directory = artifact_dir if artifact_dir is not None else Path(ARTIFACT_DIR_NAME)
    candidates = (
        sorted(
            (p for p in directory.glob("*.json") if p.stat().st_mtime >= since),
            key=lambda p: p.stat().st_mtime,
        )
        if directory.is_dir()
        else []
    )
    if not candidates:
        return "RUN FAILED", "it stopped before writing a result -- see the messages above"
    try:
        artifact = parse_run_artifact(json.loads(candidates[-1].read_text(encoding="utf-8")))
    except (ValueError, OSError):
        return "RUN FAILED", "its result could not be read"
    gates: list[str] = []
    for fields in artifact.documents.values():
        try:
            gates.append(overall_gate(fields))
        except Exception:  # noqa: BLE001 - an unreadable map is UNKNOWN, never a pass
            gates.append("UNKNOWN")
    gate = run_level_gate(gates, artifact.status)
    if gate == "FAIL":
        failed = gates.count("FAIL")
        suffix = (
            f"; the run then ABORTED ({artifact.abort_reason}), so documents after it "
            "were not measured"
            if artifact.status == "aborted"
            else ""
        )
        return "CHANGED", f"{failed} document(s) failed the gate{suffix}"
    if artifact.status == "aborted":
        return "RUN FAILED", f"the run aborted ({artifact.abort_reason or 'reason not recorded'})"
    return "RUN FAILED", "the exit code was non-zero but the result records no failure"

