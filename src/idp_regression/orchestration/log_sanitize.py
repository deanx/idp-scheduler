"""Log-safety helpers for IDP-controlled values reaching the orchestrator
(T-01.4.14, DEBT-23, NFR N5).

`IDPExecutionFailedError.status` is a string the IDP itself controls (a
poll-response terminal status). The adapter already caps/strips
non-printable characters from it at construction
(`idp_regression.adapter.errors._sanitize_status`), but a quote character
is printable and survives that pass — this module adds the SAME
`sanitize_for_log` quoting already used at every other untrusted-value log
boundary in this codebase (`idp_regression.adapter.transport.sanitize_for_log`,
`idp_regression.platform.transport.sanitize_for_log`) before the status
ever reaches a structured `key=value` log line or a telemetry field.
"""

from __future__ import annotations

import os
import traceback

from idp_regression.adapter.errors import IDPExecutionFailedError
from idp_regression.adapter.transport import sanitize_for_log
from idp_regression.platform import REQUIRED_ENV_VARS

#: DEBT-54 A-1/A-3 (Branca /harden, 2026-09-21): the credential env var
#: names `redact_secrets_for_log` checks the CURRENT process environment
#: for, before a value that happens to embed one reaches a log line.
#: `REQUIRED_ENV_VARS` (`platform/__init__.py`, N24-safe -- names no
#: vendor here) covers the platform's three; the IDP side has no
#: equivalent public constant (`adapter/idp_client.py` reads these two
#: directly by name), so they are named here explicitly. This list is
#: deliberately every credential-SHAPED var this codebase reads, not just
#: the ones a given call site is known to touch today -- the whole point
#: of a redaction floor is that it must not need updating every time a
#: new `detail=`/status field is wired to a new upstream error.
_SENSITIVE_ENV_VAR_NAMES: tuple[str, ...] = (
    *REQUIRED_ENV_VARS,
    "IDP_CLIENT_ID",
    "IDP_CLIENT_SECRET",
)

#: Below this length a "secret" value is too short to redact usefully --
#: it would either match nothing (an unset/near-empty var) or risk
#: redacting incidental short substrings unrelated to the credential
#: itself. Chosen well under any real credential's length.
_MIN_REDACTABLE_SECRET_LENGTH = 6


def redact_secrets_for_log(value: str) -> str:
    """DEBT-54 A-1/A-3: a floor UNDER `sanitize_for_log`, not a
    replacement for it -- `sanitize_for_log` only escapes/quotes
    (`json.dumps`-style), it never removes content. Every currently
    vetted `detail=`/status call site in `facade.py` is safe "by
    construction" today (HARDEN-01's own words) -- this function is the
    defense-in-depth for the NEXT call site that isn't, so a credential
    embedded verbatim in an exception message (a future upstream error
    that echoes request content, say) cannot reach a log line just
    because nobody re-audited that one raiser. Checks the value against
    every currently-set credential env var this codebase reads
    (`_SENSITIVE_ENV_VAR_NAMES`) and replaces any exact-value occurrence
    with `<redacted>`. Cheap (a handful of substring checks), pure
    (reads `os.environ` but never writes/logs anything itself), and safe
    to call on any string, secret-bearing or not."""
    redacted = value
    for name in _SENSITIVE_ENV_VAR_NAMES:
        secret = os.environ.get(name)
        if secret and len(secret) >= _MIN_REDACTABLE_SECRET_LENGTH:
            redacted = redacted.replace(secret, "<redacted>")
    return redacted


def format_execution_failed_status_for_log(error: IDPExecutionFailedError) -> str:
    """Render `error.status` safe to interpolate into a single structured
    log line or telemetry field (DEBT-23, N5).

    DEBT-54 A-3 (Branca /harden, 2026-09-21): also passed through
    `redact_secrets_for_log` first -- the IDP-controlled status string is
    escaped/capped at construction (`adapter/errors.py`) and quoted here,
    but neither step removes content, so a credential value that somehow
    ended up echoed in that status string would otherwise survive to the
    log verbatim."""
    return sanitize_for_log(redact_secrets_for_log(error.status))


#: The directory one level above the `idp_regression` package (i.e. `src/`,
#: or this repo's checkout root when installed editable) -- used by
#: `frame_location` below to strip the absolute deployment path off a
#: traceback frame's filename.
_PACKAGE_PARENT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def frame_location(exc: BaseException) -> str:
    """Shared by `facade.py` and `cli.py` (previously duplicated as a
    private `_frame_location` in each): the last traceback frame's
    `filename:lineno:name` carries no golden value, no extracted value,
    no credential and no platform response body -- it is pure
    code-location metadata, safe under INV-02, unlike `str(exc)`.

    ⚠️ Fixed 2026-09-21 (Atchim gate suggestion): `frame.filename` alone is
    an ABSOLUTE path (e.g. `/Users/alex/.../facade.py`), which discloses
    this deployment's directory layout on every unexpected-exception log
    line. Rendered package-relative (relative to the directory containing
    `idp_regression`) instead, so the value logged is e.g.
    `idp_regression/orchestration/facade.py:426:run_eval` -- never an
    absolute machine path.

    ⚠️ Fixed again 2026-09-21 (same-day re-gate, live-reproduced):
    `os.path.relpath` does not clamp to `_PACKAGE_PARENT` -- it happily
    TRAVERSES above it. Whenever the innermost frame lives OUTSIDE
    `idp_regression` (the normal case for the unanticipated-exception
    catch-alls this feeds: the evaluation-platform SDK, httpx, the
    stdlib), the result is a string like
    `../../../../../../Users/alex/.../json/decoder.py:361:raw_decode` --
    the exact absolute-path disclosure the first fix above was written to
    close, just re-opened one level up. Any frame whose relative path
    would start with `..`, or whose drive/root differs entirely from
    `_PACKAGE_PARENT` (`relpath` raises `ValueError` on Windows for
    that), is rendered as `<external>/{basename}` instead -- code-location
    metadata for a third-party frame is not worth an absolute-path leak.
    A package-internal frame keeps the existing package-relative form.

    ⚠️ Fixed a third time 2026-09-21 (Branca `/harden` re-run #3, GAP-8,
    live-reproduced against `1e8e1aa`): the `<external>/` clamp above
    only covered an out-of-tree ABSOLUTE frame filename. A synthetic
    stdlib frame (`<string>` from `exec`/`compile`, `<frozen
    importlib._bootstrap>`, ...) is NOT absolute -- `os.path.relpath`
    silently joins a non-absolute argument onto `os.getcwd()` first,
    which is (a) yet another absolute-path disclosure route and (b)
    raises `FileNotFoundError` (an `OSError`) from `getcwd()` itself if
    the current working directory has since been deleted -- an
    exception escaping from INSIDE this exact catch-all-safety helper.
    A non-absolute filename is now recognised and routed to
    `<external>/{basename}` directly, without ever calling `relpath`.
    `OSError` is now also caught alongside `ValueError` around the
    remaining (absolute-frame) `relpath` call, and the whole filename
    computation is wrapped in a final `except Exception` so this
    function is TOTAL -- no traceback, no filename shape and no
    unanticipated `relpath` failure may ever raise out of it; the
    fallback is `<unavailable>`, never a crash inside a catch-all whose
    entire job is to make an unanticipated failure safe to log.

    ⚠️ Fixed a fourth time 2026-09-21 (Branca `/harden` §10.6, A-6,
    DEBT-58): this was the one orchestration log value interpolated raw
    (`%s`), never through `sanitize_for_log` like every other
    untrusted-shaped value at a log boundary in this codebase. A
    newline embedded in a frame filename split a log line and forged a
    fake `run_end outcome=success` line on a run that actually exited
    1. The computed `filename:lineno:name` string is now returned
    through `sanitize_for_log` (the same `json.dumps`-style
    quoting/escaping), so an embedded newline (or quote) can never
    produce a second physical line. The static fallback literals
    (`<no traceback>`, `<unavailable>`) carry no untrusted data and are
    returned as-is.

    ⚠️ Fixed a fifth time 2026-09-21 (Branca `/harden` §10.1/§10.5, P18,
    DEBT-58 -- the last third of A-5): `traceback.extract_tb(...)` used
    to be the ONE statement OUTSIDE the guarded body, on the assumption
    it was a pure computation over `exc.__traceback__`. It is not:
    `extract_tb` reads source lines through `linecache`, which does
    file I/O, and a genuine (unmocked) `RecursionError` inside it was
    reproduced escaping this function. It is now inside the same `try`
    as everything else, so the "no traceback, no filename shape and no
    unanticipated failure may ever raise out of this function" claim
    above is actually true, not just true for the filename computation."""
    try:
        frames = traceback.extract_tb(exc.__traceback__)
        if not frames:
            return "<no traceback>"
        frame = frames[-1]
        if not os.path.isabs(frame.filename):
            filename = f"<external>/{os.path.basename(frame.filename)}"
        else:
            try:
                filename = os.path.relpath(frame.filename, _PACKAGE_PARENT)
            except (ValueError, OSError):
                filename = f"<external>/{os.path.basename(frame.filename)}"
            else:
                if filename.split(os.sep, 1)[0] == os.pardir:
                    filename = f"<external>/{os.path.basename(frame.filename)}"
        return sanitize_for_log(f"{filename}:{frame.lineno}:{frame.name}")
    except Exception:  # noqa: BLE001 - this IS the catch-all's own safety net; must never raise
        return "<unavailable>"
