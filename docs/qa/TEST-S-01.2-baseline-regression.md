# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ❌ INVALIDATED
**Source:** /implement (Atchim REQUEST CHANGES)
**Date:** 2026-09-19
**Commit:** f54f48f437547bc1a73959480dfdcba3ed8c2749
**Author:** alex@divinocosta.com.br
**Atchim TDD gate:** REQUEST CHANGES, see findings below
**Independence:** ✅ structural (Dengoso sonnet, reviewed by Atchim opus)
**Static:** ✅ clean (mypy strict + ruff). NOTE: CT-01 hides a real mypy error behind `# type: ignore` (R6)

## Findings (Atchim, 2026-09-19)

### Required
1. **Name check too loose** (`normalize.py:22,66`): `$` matches before a trailing newline, so `"total\n"` is accepted; there is also no length cap (DoD line 82).
2. **Raw exceptions escape `normalize()`:** `OverflowError` on a huge-int confidence (`:96`); `UnicodeEncodeError` on a lone surrogate (`:83`), which carries the PII value.
3. **Raw exceptions escape `extract()` through the transport** (`transport.py:108-131`): RemoteDisconnected, ConnectionResetError, IncompleteRead, UnicodeDecodeError and RecursionError. A missing file raises `FileNotFoundError` with the path in the message.
4. **A missing or null status polls to timeout** (`idp_client.py:179-192`), but ADR-0004 #17 says it must abort. `test_idp_client.py:247` pins the wrong behaviour.
5. **The poll ignores non-2xx except 401/403** (`:174-178`): a 404/400 keeps polling (ADR-0004 #5 says hard failure), and an error body's `status` is read as the execution status.
6. **CT-01 type mismatch hidden by `# type: ignore[arg-type]`** (`test_normalize_contract.py:122`): Required vs NotRequired keys make the TypedDicts incompatible.
7. **`success_statuses` unused in `normalize()`** (`:36`): `normalize({"status":"FAILED"})` returns a result, against ADR-0002:125.
8. **Hollow tests:** the monotonic-clock test can't reach `time.time` (the `TokenCache` default→`time.time` mutant survived, so INV-07 "checked" is unearned); the submit-failure secret-log test passes trivially and the token sits in `__cause__`; the dropped-403 and unsanitized-transport-log mutants survived.

### Suggestions
- Size-cap tests should use pinned literals, with at-limit-accepted and multibyte cases.
- A frozen fake clock can hang the suite; use a clock that advances, or pytest-timeout.
- The poll budget can overshoot: the per-GET timeout and the sleep aren't clamped to the remaining budget.
- Prompt `source` and `resp.read()` are unbounded.
- The multipart filename doesn't escape `"`/CRLF.
- Validate that success statuses are a subset of terminal statuses.

### Design calls
- `MalformedIDPOutputError` → `hard_failure`, with `reason` as a structured field.
- Mid-poll 401 is S-01.4's, but the refresh-and-retry must live in the adapter's poll loop (debt).
- The synthetic fixture is OK for unit tests; the CT-01 real-fixture item stays open until S-01.6.

## History
- Initial stamp (no prior stamp for S-01.2)
