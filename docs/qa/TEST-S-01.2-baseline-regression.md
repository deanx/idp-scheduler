# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED WITH FINDINGS
**Source:** /test re-stamp #4 (Wave C — delta re-gate of the fix for re-stamp #3's F-1..F-4; fresh-instance gate per `docs/process/REVIEW-RULES.md` R1–R3, P1–P7)
**Files:** `src/idp_regression/adapter/{__init__,errors,idp_client,normalize,oauth,token_cache,transport,types,version_probe}.py` — the nine files, named (P5), all pinned to HEAD below
**Sequence:** one delta commit, `6715968` — `src/` fix and its tests landed together (`git show --stat`, not self-reported). The commit message states each mutant was run red first (T1, T2, I1, O1, O2, M4); this gate re-derived its own matrix rather than trusting that list, and every one of those six is killed here by exactly the test the delta added.
**Date:** 2026-09-28
**Commit:** `671596898ec6f87218b39dce95b755dddd716c96` (`feat/S-01.2-idp-adapter`)
**Author:** alex@divinocosta.com.br (solo)
**Atchim TDD gate:** PASSED
**Independence (R3):** fresh Atchim instance on **Fable 5.1**; implementer of `6715968` is **Opus 5.5** (different model). This instance issued **no** APPROVE, REQUEST CHANGES, PASS or FAIL on `6715968`, on `597b912`, or on any earlier S-01.2 commit, and did not run re-stamp #3 (a different instance, committed as `507f58c`). ✅ structural (different models, new instance).
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp is required regardless of the `prototype` profile; a `src/` fix never qualifies for `re-gate skipped`. **Static:** mypy strict clean (151 files) · ruff clean.

## Delta gated
`597b912..6715968 -- src/idp_regression/adapter/` — 3 of 9 files, +8/−3: `transport.py` (`_send`: `UnicodeError` routed to the static no-detail path), `idp_client.py` (`_fetch_token` guard → `ch.isascii() and ch.isprintable()`), `oauth.py` (`fetch_access_token` guard, same). Tests: `tests/adapter/test_transport.py` (+2), `tests/adapter/test_oauth.py` (**new**, +2), `tests/adapter/test_normalize.py` (+2, one parametrized 3×5), `tests/adapter/test_idp_client.py` (+1 param). `.gitleaksignore` (+1 fingerprint). No live IDP or platform call was made; `.env` was not sourced.

## Mechanical floor — actual numbers at `6715968`, `PYTHONDONTWRITEBYTECODE=1`, no stray `__pycache__` afterwards
| gate | result |
|---|---|
| `pytest -q` | ✅ **1907 passed, 15 skipped**, 1 warning, 43.7 s (was 1884 at `597b912`: +23 = the delta's tests) |
| `mypy src tests scripts` (strict) | ✅ no issues, 151 files |
| `ruff check src tests scripts` | ✅ clean |
| `gitleaks git --no-banner .` (398 commits) | ✅ **no leaks found** — re-stamp #3's F-4 hit is now fingerprinted; narrowness verified below |
| Readability / Architecture / Performance axes | skipped: prototype profile |

## Mutation matrix (R1 — each mutant names the invariant it came from; 7 of 16 sit on lines the delta did NOT change)
Method (P2/P4): each of the six files copied to the scratchpad first (`restamp-s012b/`), mutated by exact single-occurrence string replacement, `pytest -q -p no:cacheprovider -x tests/adapter tests/orchestration` run **one mutant at a time**, restored with `cp`, verified byte-identical with `shasum` (all 16 ✅; the whole `adapter/` tree re-checked against the pre-gate `shasum` list at the end). The survivor was re-run against the full suite.

| # | file · symbol | mutant | invariant / declared contract it attacks | line changed by delta? | result |
|---|---|---|---|---|---|
| T1 | `transport._send` | drop the `isinstance(exc, UnicodeError)` routing | INV-02: a token character/offset never reaches a message or log | yes | ✅ killed (`test_a_non_latin1_header_value_is_rejected_without_its_detail`) |
| T2 | `transport._send` | route the `"Invalid header"` `ValueError` through the redacted-detail path | INV-02 defence-in-depth (re-stamp #3 F-3, the formerly-equivalent M19) | yes | ✅ killed (`test_a_rejected_header_is_reported_statically_never_through_redact`) — **M19 is no longer equivalent** |
| T3 | `transport._send` | raise the no-detail error **inside** the `except ValueError` (deferred-raise structure removed) | INV-02: `__context__` chain must be empty | **no** | ✅ killed (`test_crlf_in_header_value_raises_typed_error_without_the_secret`) |
| T4 | `transport._log_and_raise_transport_error` | `redact()` dropped | INV-02 on the detail path | **no** | ✅ killed (`test_bearer_token_never_appears_in_a_redacted_transport_error`) |
| T5 | `transport._send` | `UnicodeError` → `UnicodeDecodeError` | the branch must catch the *encode* error `http.client` actually raises | yes | ✅ killed (1) |
| I1 | `idp_client._fetch_token` | `isascii()` dropped | RFC 6750 b64token is ASCII; a non-latin-1 token must be refused at fetch | yes | ✅ killed (`…rejected_at_fetch[tok€en]`) |
| I2 | `idp_client._fetch_token` | token guard removed | header-injection primary defence | yes | ✅ killed (`…rejected_at_fetch[tok\r\ninjected]`) |
| I3 | `idp_client._fetch_token` | raise inside `except IDPTransportError` | INV-02 `__context__` chain | **no** | ✅ killed (`test_secrets_never_appear_in_a_token_fetch_failure_log`) |
| O1 | `oauth.fetch_access_token` | `isascii()` dropped | as I1 | yes | ✅ killed (`test_a_token_outside_printable_ascii_is_refused_without_echoing_it[tok€en]`) |
| O2 | `oauth.fetch_access_token` | token guard removed | as I2 | yes | ✅ killed (`…[tok\r\ninjected]`) |
| O3 | `oauth.fetch_access_token` | `isprintable()` dropped (ASCII-only check) | CR/LF/NUL must still be refused | yes | ✅ killed (`…[tok\r\ninjected]`) |
| **O4** | `oauth.fetch_access_token` | raise inside `except IDPTransportError` (deferred-raise removed) | INV-02 `__context__` chain — the exact mirror of I3 | **no** | ❌ **SURVIVED — full suite 1907 passed** (see F-1) |
| N1 | `normalize._coerce_cell` | `str` guard removed (re-stamp #3's M4) | `FieldValue.value: str \| None` (R2 value-type obligation) | **no** | ✅ killed (`test_a_non_string_cell_value_is_refused[field-1]`) — **M4 is now killed** |
| N2 | `normalize._merge_prompts` | prompt answer bypasses `_coerce_cell` | same obligation on the third `_coerce_cell` caller | **no** | ✅ killed (`test_a_prompt_answers_confidence_is_read_like_a_fields`) |
| N3 | `normalize._coerce_cell` | `bool` accepted (an `int` subclass) | R2: the parametrization must include the subclass trap | **no** | ✅ killed (`…[field-True]`) |
| C1 | `token_cache._refresh` | typed error re-wrapped `from exc` | chain hygiene (re-stamp #3 P-3) | **no** | ✅ killed (`test_secrets_never_appear_in_a_token_fetch_failure_log`) |

**Equivalent mutants:** none. (T5 is close to trivial but not equivalent: the delta's test raises the encode error, so it distinguishes.)

**Non-equivalent survivor:** O4. Under the mutant, `IDPAuthenticationError.__context__` holds the `IDPTransportError` (verified directly: `__context__ = IDPTransportError('POST https://anypoint... failed: <redacted detail>')`). The message it carries is already `redact()`ed at the transport boundary, so this is a **pin gap on a defence-in-depth layer, not a live leak** — the same class re-stamp #3's F-3 named for `_send`, now on `oauth.py`. `idp_client.py` has the pin (I3 killed); its acknowledged ~30-line twin does not.

## "Try to break it" probes — real `http.client`, `127.0.0.1:9`, no live calls, no `.env`
- **P-1 (RFC 6750 vs the guard):** b64token = `ALPHA / DIGIT / "-" / "." / "_" / "~" / "+" / "/"` then `*"="` — every character is ASCII-printable, so `isascii() and isprintable()` **cannot refuse a valid token** (`test_a_printable_ascii_token_is_accepted` pins `abc.DEF-123_~+/=`). In the other direction the guard is a strict superset of what `http.client` rejects: CR/LF/NUL (`isprintable` false) and non-latin-1 (`isascii` false) are refused at fetch; tab, obs-fold `\n `, DEL and latin-1 `é` are *accepted* by `http.client` (header goes to the wire — probed) and are refused by the guard anyway. Nothing the guard accepts can make `putheader` raise with detail.
- **P-2 (every `_send` path, header + token):** non-latin-1 `€`, CR, bad header **name** → `request headers were rejected`, chain length 1, no character/offset/`latin-1` in message or log. tab / obs-fold / NUL / DEL / latin-1 → connection refused, redacted detail, `SECRET` absent from message, log and the 3-deep `URLError` chain. `post_empty_multipart` (the probe transport) with `€` → same static path. `unknown url type` → detail path, URL only.
- **P-3 (classification nit, not a leak):** a **non-ASCII URL** makes `putrequest`'s `request.encode('ascii')` raise `UnicodeEncodeError`, which the new branch now reports as `request headers were rejected`. Misleading wording; the URL is already in the message and carries no secret, and every URL component reaching `_send` is validated against `^[A-Za-z0-9._-]` first, so unreachable in shipped code. Noted as N-1.
- **P-4 (gitleaks narrowness):** with `.gitleaksignore` **removed from the tree** (copied to scratchpad, `rm`, scan, `cp` back, `shasum` OK) the scan reports **exactly 2** findings, both `generic-api-key`, both the two fingerprints in the file: `353549d1…:tests/platform/test_langfuse_adapter.py:305` (DEBT-45) and `188f36a3…:demopack/env.demo.example:22`, whose captured "secret" is literally `IDP_REGION=us-east-1` — the triage in the ignore-file comment is exact. The entry is `commit:file:rule:line`, no `paths`/allowlist stanza. (`--gitleaks-ignore-path <nonexistent>` silently falls back to the repo's file — 0 findings either way — so a future narrowness check must remove the file, as here.)

## Findings
| # | severity | file · symbol | scenario | suggested fix |
|---|---|---|---|---|
| **F-1** | **Low** (weak pin, INV-02 defence-in-depth) | `src/idp_regression/adapter/oauth.py` · `fetch_access_token` deferred-raise after `except IDPTransportError` | O4: collapsing the deferred raise into the `except` survives all 1907 tests; `__context__` then carries the (redacted) transport error. `_fetch_token`'s twin is pinned by `test_secrets_never_appear_in_a_token_fetch_failure_log`; `test_oauth.py` pins `__context__ is None` only on the guard path. | In `tests/adapter/test_oauth.py`: monkeypatch `transport.post_json` to raise `IDPTransportError("… Bearer SECRET …")`, assert `IDPAuthenticationError` with `__context__ is None`, `__cause__ is None`, `SECRET` absent from `str(exc)`. Run it red against O4 first (P7). Hand to Dunga: DEBT row, R1 "mirror of a pinned twin" class. |
| N-1 | nit | `src/idp_regression/adapter/transport.py` · `_send` `except ValueError` | P-3: a `UnicodeEncodeError` from the request line (non-ASCII URL) is labelled "request headers were rejected". Wording only; no secret, unreachable via validated ids. | Optional: word the static reason as `request line or headers were rejected`, or leave as is with a comment. |

Debt to surface to Dunga (`/debt add`): F-1. Re-stamp #3's F-1..F-4 are **closed by `6715968`** as verified here (N1/T1/T2/P-4); its P-3 `from exc` hygiene note stays as previously handed over (C1 shows the typed path is pinned).

## What this stamp does NOT cover — read before trusting a green build
1. Everything re-stamp #3 listed still holds: the `prompts` shape is unverified against a live response (SR-1 clause 3, DEBT-69(a) open — ⛔ do not fabricate a capture); live IDP submit/poll unexercised; the 10 s poll floor equals the default.
2. The `UnicodeError` branch in `_send` is **dead in shipped paths** now that both token producers refuse non-ASCII — it is pinned only through a monkeypatched `_urlopen`. That is the intended defence-in-depth, stated so nobody later "removes dead code" and reopens F-2.
3. Readability / Architecture / Performance axes: `skipped: prototype profile`.

## Freshness (P5) — explicit file list pinned to `6715968`
`git diff --stat 597b912..HEAD -- src/idp_regression/adapter/__init__.py src/idp_regression/adapter/errors.py src/idp_regression/adapter/idp_client.py src/idp_regression/adapter/normalize.py src/idp_regression/adapter/oauth.py src/idp_regression/adapter/token_cache.py src/idp_regression/adapter/transport.py src/idp_regression/adapter/types.py src/idp_regression/adapter/version_probe.py` → 3 files changed, +8/−3 (`idp_client.py`, `oauth.py`, `transport.py`; the other six unchanged).
**Added-files check:** `git diff --name-status --diff-filter=ADR 597b912..HEAD -- src/idp_regression/adapter/` → none; `git ls-files` vs on-disk `*.py` → identical; `git status --untracked-files=all` on `src/idp_regression/adapter/`, `tests/adapter/`, `tests/fixtures/` → clean. Tests added in the delta: `tests/adapter/test_oauth.py` (new file, in scope of this gate).
HEAD blob ids: `__init__.py` `79030c4` · `errors.py` `1bbd4eb` · `idp_client.py` `f2395ae` · `normalize.py` `e935942` · `oauth.py` `9f4d17e` · `token_cache.py` `67df578` · `transport.py` `1fbf455` · `types.py` `b56e82e` · `version_probe.py` `3f0381f`.
Working tree after the gate: `git status --short` → only `docs/state/STATE.json` (pre-existing) plus this stamp; no `__pycache__` outside `.venv`/`frontend`.

## Verdict
✅ **PASSED WITH FINDINGS.** The delta answers all four of re-stamp #3's findings with real pins: M4 is killed (N1), the formerly-equivalent M19 is killed (T2), the `UnicodeEncodeError` route is closed both at the source (I1/O1) and in `_send` (T1), and the gitleaks fingerprint is exactly one commit × file × rule × line. 15 of 16 invariant-derived mutants killed, each by a single named test; the one survivor (O4) is a defence-in-depth pin gap on `oauth.py` mirroring a pin `idp_client.py` already has, with no live leak behind it. No fail-open and no INV-02 leak was found by hand-probing every header path through the real `http.client`.

## History
- 2026-09-22 — ✅ PASSED (superseded, stale per DEBT-46)
- 2026-09-23 @ `a5805ec` — ✅ PASSED (/test re-gate, DEBT-46; superseded)
- 2026-09-28 @ `597b912` — ✅ PASSED WITH FINDINGS (re-stamp #3, Wave C; F-1..F-4 → fixed in `6715968`; superseded by this stamp)
- 2026-09-28 @ `6715968` — ✅ PASSED WITH FINDINGS (this stamp, re-stamp #4, Wave C)

<details>
<summary>Prior stamp (2026-09-28 @ <code>597b912</code>, re-stamp #3) — preserved verbatim, including its own History and the nested 2026-09-23 stamp</summary>

# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED WITH FINDINGS
**Source:** /test re-stamp #3 (Wave C — DEBT-46 staleness, DEBT-72, DEBT-77; fresh-instance gate per `docs/process/REVIEW-RULES.md` R1–R3, P1–P7)
**Files:** `src/idp_regression/adapter/{__init__,errors,idp_client,normalize,oauth,token_cache,transport,types,version_probe}.py` — the nine files, named (P5), all pinned to HEAD below
**Sequence:** delta commits `c1a36cb` (feat + tests + docs fixture in one commit), `771a661` (tests for gate findings F-1..F-5, after the feat by design), `57f5cca` (G-2: probe comment + tests + two live captures in one commit). Ordering read from `git show --stat`, not self-reported.
**Date:** 2026-09-28
**Commit:** `597b912f652406c38276f886442304452191216e` (`feat/S-01.2-idp-adapter`)
**Author:** alex@deanx.com.br (solo)
**Atchim TDD gate:** PASSED
**Independence (R3):** fresh Atchim instance on **Fable 5.1**; implementer of every post-`a5805ec` change is **Opus 5.5** (different model). This instance issued **no** APPROVE, REQUEST CHANGES, PASS or FAIL on `c1a36cb`, `771a661`, `57f5cca`, or any Wave-B commit in `a5805ec..HEAD`, and did not run the previous gate on this story. ✅ structural (different models, new instance).
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp is required regardless of the `prototype` profile. The wire-contract path is held to **SR-1** (`docs/state/REGRESSIONS.md`). **Static:** mypy strict clean (150 files) · ruff clean.

## Delta gated
`a5805ec..HEAD -- src/idp_regression/adapter/` — 7 of 9 files changed, +274/−25 (`errors.py`, `types.py` unchanged). Commits: `771a661`, `c1a36cb`, `57f5cca`, `398a7e8`, `46c76a5`, `6932bed`, `cbfbe30`, `3c8ed7a`, `9bf9062`, `f3b0b65` (adapter-touching ones).
- **DEBT-69(a)** `normalize._merge_prompts`: MuleSoft's documented `prompts` map `{name: {prompt, source, answer:{value}}}`; a non-empty list is `invalid_page`; empty `[]`/`{}` is "no prompts"; the map's name is charset-checked (`_SAFE_PROMPT_PATTERN`) and otherwise unused (keyed downstream by question text, DEBT-118).
- **G-2** `version_probe.classify_probe_response`: EXISTS branch stays unbound, now justified by two recorded live probes (403 wrong-org, 404 `Action Id: <id>` wrong-action).
- **Wave B**: DEBT-21 leg 2 (submit 401/403 → `IDPAuthenticationError`), DEBT-49(b) (transport-failure poll retry shares the bounded budget), DEBT-79 (`_per_call_timeout_seconds`), DEBT-71 (`IDP_POLL_INTERVAL_SECONDS`), DEBT-22 leg 2 (`FAILED` terminal by default), DEBT-25(a)/(b) (case-insensitive + `%3D` redaction; header-rejection vs other `ValueError`), DEBT-52 (`O_NOFOLLOW`), DEBT-54 A-2 (`MAX_DOCUMENT_BYTES`), `REQUIRED_ENV_VARS`, `IDPVersionProbe.last_status_code` in the Protocol.

## SR-1 ruling on the docs-example fixture
`tests/adapter/fixtures/mulesoft_docs_prompts_example.json` is a **documentation example, not a capture**, and every place that cites it says so: its README, `normalize.py:357`, ADR-0002's 2026-09-27 amendment, `REGRESSIONS.md` SR-1 prose, `DEBT.md` DEBT-69 cell, and `docs/qa/TEST-DEBT-69A-PROMPTS-2026-09-27.md`. **No claim leans on it as live.** The `prompts` shape therefore remains **UNVERIFIED under SR-1 clause (3)** and DEBT-69(a) stays open, correctly. G-2, by contrast, is closed against **recorded live responses** (`version_probe_wrong_org.raw.json`, `version_probe_wrong_action.raw.json`, added in `57f5cca`) — M9/M10 below are killed by those captures, which is what SR-1 asks for.

## Mechanical floor — actual numbers at `597b912`, `PYTHONDONTWRITEBYTECODE=1`, `__pycache__` cleared afterwards
| gate | result |
|---|---|
| `pytest -q` | ✅ **1884 passed, 15 skipped**, 1 warning, 43.8 s |
| `mypy src tests scripts` (strict) | ✅ no issues, 150 files |
| `ruff check src tests scripts` | ✅ clean |
| `pip-audit` | ✅ no known vulnerabilities (only the local package itself "not on PyPI") |
| `gitleaks detect` (396 commits) | ⚠️ **1 hit, triaged FALSE POSITIVE, outside adapter scope** — `demopack/env.demo.example:22` at `188f36a3`, rule `generic-api-key`, on `IDP_CLIENT_SECRET=` with an **empty** value (placeholder). Not in `.gitleaksignore`. See F-4. |
| Readability / Architecture / Performance axes | skipped: prototype profile |

## Mutation matrix (R1 — each mutant names the invariant it came from; 9 of 22 sit on lines the delta did NOT change)
Method (P2/P4): each file copied to the scratchpad first, mutated by exact-string replacement, `pytest -q -p no:cacheprovider tests/adapter tests/orchestration` (843 tests) run **one mutant at a time**, restored with `cp`, verified byte-identical with `shasum` (all 22 ✅). Survivors re-run against the full suite.

| # | file · symbol | mutant | invariant / declared contract it attacks | line changed by delta? | result |
|---|---|---|---|---|---|
| M1 | `normalize._merge_prompts` | non-dict `prompts` silently dropped instead of `invalid_page` | prompt shape guard fail-closed | yes | ✅ killed (1: `test_the_undocumented_list_form_is_rejected`) |
| M2 | `normalize._merge_prompts` | empty-list tolerance widened to ANY list | same — a populated legacy list must not vanish into a green run | yes | ✅ killed (1) |
| M3 | `normalize._merge_prompts` | prompt NAME charset guard removed | `_SAFE_PROMPT_PATTERN` on the new key axis | yes | ✅ killed (4, incl. F-3's INV-02 echo test) |
| **M4** | `normalize._coerce_cell` | `value` non-`str` accepted (guard removed) | `FieldValue.value: str \| None` (`types.py`, R2 value-type obligation) | **no** | ❌ **SURVIVED — full suite 1883 passed** (see F-1) |
| M5 | `normalize._merge_tables` | `MAX_TABLE_ROWS` cap removed | bounded table size (ADR-0002) | no | ✅ killed (2) |
| M6 | `normalize.normalize` | non-success `status` accepted | "normalize only emits for a success status" — the live `failed-execution.raw.json` kills it | no | ✅ killed (2, incl. the live FAILED capture) |
| M7 | `normalize._merge_prompts` | pages-vs-pages `duplicate_prompt` removed | R-2 rule unchanged by the map shape | no | ✅ killed (1: F-1's test) |
| M8 | `normalize._coerce_confidence` | D2 scale-ambiguity guard removed | REG-11 D2 | no | ✅ killed (3) |
| M9 | `version_probe.classify_probe_response` | any 400 → EXISTS | probe classification, D6 default branch | no | ✅ killed (3) |
| M10 | `version_probe.classify_probe_response` | any 404 → ABSENT (pattern + binding removed) | R3 binding; **live wrong-action capture** among the killers | no | ✅ killed (6, incl. `test_a_live_wrong_action_response_is_never_exists_nor_absent`) |
| M11 | `version_probe.classify_probe_response` | 404 ABSENT no longer bound to the probed `version` | R3 version binding | no | ✅ killed (1) |
| M12 | `idp_client._poll` | transport-failure retry no longer counts toward `poll_retry_max_attempts` | poll/retry budget (DEBT-49(b)) | yes | ✅ killed (1: `test_poll_transport_error_retry_budget_exhaustion_raises_hard_failure`) |
| M13 | `idp_client._submit` | 401/403 → generic `IDPSubmitError` | DEBT-21 leg 2 typed error | yes | ✅ killed (2) |
| M14 | `idp_client._poll` | any terminal status returns the body | terminal vs success allowlist (BR9) | no | ✅ killed (4) |
| M15 | `idp_client._poll_retry_sleep_seconds` | backoff not capped to the remaining budget | retry budget ⊂ poll deadline (INV-07) | no | ✅ killed (1) |
| M16 | `idp_client.make_idp_adapter` | `FAILED` dropped from default terminal set | DEBT-22 leg 2 | yes | ✅ killed (1) |
| M17 | `token_cache.TokenCache.get` | refresh margin ignored | token cache expiry (ADR-0004 #7) | no | ✅ killed (1) |
| M18 | `transport._NoRedirectHandler` | follows 3xx (re-sends Bearer) | no-redirect opener (REG-07) | no | ✅ killed (1: `…token_never_reaches_the_target`) |
| M19 | `transport._send` | header-rejection `ValueError` routed through the redacted detail path | INV-02 no-detail defence-in-depth | yes | ⚪ **survived — EQUIVALENT under current guards** (see F-3) |
| M20 | `transport.post_multipart_file` | size cap removed | DEBT-54 A-2 | yes | ✅ killed (1) |
| M21 | `transport.post_multipart_file` | `O_NOFOLLOW` dropped | DEBT-52 symlink refusal | yes | ✅ killed (1) |
| M22 | `transport._BEARER_PATTERN` | case-sensitive again | DEBT-25(a) redaction | yes | ✅ killed (1) |

**Equivalent mutants:** M19 only. Both token producers (`idp_client._fetch_token`, `oauth.fetch_access_token`) reject any non-`isprintable()` character before a token can reach a header, so `http.client` can never raise "Invalid header value" for a Bearer token in shipped code paths, and when forced (the CRLF test) `redact()`'s `Bearer\s+\S+` happens to cover the bytes-repr. The no-detail branch is real defence-in-depth but no test can currently tell it from `redact()`.

**Non-equivalent survivor:** M4 — a genuine pin gap on an unchanged line, exactly R1's "mutate above/around the diff" case and R2's "value type still hand-written" case.

## "Try to break it" probes (no live calls, no `.env`)
- **P-1 (found, F-2):** `transport.get_json(url, headers={"Authorization": "Bearer abcSECRET€def"})` against `127.0.0.1:9` — `http.client.putheader` encodes latin-1 **before** `_is_illegal_header_value`, raising `UnicodeEncodeError` (a `ValueError` subclass). DEBT-25(b)'s new branch reports it with detail: message and log carry `'latin-1' codec can't encode character '€' in position 16`. One codepoint of the token plus its offset, no `__cause__`/`__context__`. Before DEBT-25(b) every `ValueError` took the no-detail path. `€` passes `isprintable()`.
- **P-2:** `"prompts": null` at top level → `invalid_page` (fail-closed, correct). `"answer"` missing → `invalid_cell`. Duplicate question under two names in one map → `duplicate_prompt`. All as documented.
- **P-3:** `token_cache._refresh` still chains `from exc` for a non-typed fetch failure (unchanged line). Every shipped `fetch` converts everything it can raise to typed errors first, so unreachable today; noted, not a finding.
- **P-4:** after a transport failure on the post-refresh GET inside `_poll_get_with_auth_retry`, `_poll`'s local `token` stays stale for one iteration → a 401 → a second refresh. Correct, one wasted token round-trip; cosmetic.

## Findings
| # | severity | file · symbol | scenario | suggested fix |
|---|---|---|---|---|
| **F-1** | **Medium** (pin gap, R2) | `src/idp_regression/adapter/normalize.py` · `_coerce_cell` value-type guard | M4: an `int`/`float`/`bool`/`list`/`dict` cell `value` is accepted and reaches `NormalizedOutput` typed as `str \| None` — nothing in 1884 tests notices. The guard is correct at HEAD; a "simplification" can delete it silently, and the classifier's canonicalisation would then compare a non-string. | Parametrized test in `tests/adapter/test_normalize.py` over the non-`str` types, derived from `get_type_hints(FieldValue)["value"]`, asserting `reason == "invalid_cell_value"` for fields, table cells **and** prompt answers (all go through `_coerce_cell`). Same for `PromptValue.answer`. Hand to Dunga: DEBT row, R2 class. |
| **F-2** | **Low** (INV-02, regression introduced by DEBT-25(b) in `cbfbe30`/`6932bed`) | `src/idp_regression/adapter/transport.py` · `_send` `except ValueError` | P-1: a printable, non-latin-1 token codepoint and its position reach the message and the log. Realistic only with a hostile/broken token endpoint (Bearer tokens are ASCII `b64token`), so not blocking. | Either route `isinstance(exc, UnicodeError)` to `_log_and_raise_transport_error_without_detail(req, "request headers were rejected")`, or tighten both token guards to `ch.isascii() and ch.isprintable()` (RFC 6750 grammar) — the second closes it at the source and makes the branch unreachable. Add the P-1 test (`tests/adapter/test_transport.py`), red first (P7). |
| **F-3** | **Low** (weak pin) | `src/idp_regression/adapter/transport.py` · `_send` header-rejection branch | M19 equivalent: no test distinguishes the static no-detail message from `redact()`. If `redact()`'s Bearer regex is ever narrowed, the CRLF test would still pass via the no-detail path, and vice versa — two layers, one witness. | A test that monkeypatches `_urlopen` to raise `ValueError("Invalid header value b'Bearer\\tSECRET'")` (a repr `Bearer\s+\S+` cannot match) and asserts `SECRET` is absent from `str(exc)` and the log. |
| **F-4** | **Low** (process, out of adapter scope) | `demopack/env.demo.example:22` @ `188f36a3` | `gitleaks` flags `IDP_CLIENT_SECRET=` (empty placeholder) as `generic-api-key`. False positive, but the scan is red and the matrix says the secret scan is never profile-driven. | Fingerprint it in `.gitleaksignore` (`188f36a31ab9d8a5db72e8dae91dccc860524096:demopack/env.demo.example:generic-api-key:22`) with a triage note, per the DEBT-45 precedent — never a path allowlist. |

Debt to surface to Dunga (`/debt add`): F-1 (R2 value-type pins for `FieldValue.value`/`PromptValue.answer`), F-2, F-3, F-4; plus the P-3 `from exc` chain in `token_cache._refresh` as a hygiene note.

## What this stamp does NOT cover — read before trusting a green build
1. **The `prompts` shape is still unverified against a live response** (SR-1 clause 3, DEBT-69(a) open). The docs-example pin is better than a parser-authored fixture and is not evidence of the wire. ⛔ Do not fabricate a capture to close this.
2. `IDP_POLL_INTERVAL_SECONDS` now exists (DEBT-71) but the 10 s floor still equals the default, so the constructor floor is exercised only by tests, never by a production value below it.
3. Live IDP submit/poll remains unexercised by this stamp (never called; `.env` never sourced).
4. Readability / Architecture / Performance axes: `skipped: prototype profile`.
5. ⚠️ The pattern the prior stamp named still holds: the one genuine INV-02 slip in this delta (F-2) was found by a hand probe, not by the suite.

## Freshness (P5) — explicit file list pinned to `597b912`
`git diff --stat a5805ec..HEAD -- src/idp_regression/adapter/__init__.py src/idp_regression/adapter/errors.py src/idp_regression/adapter/idp_client.py src/idp_regression/adapter/normalize.py src/idp_regression/adapter/oauth.py src/idp_regression/adapter/token_cache.py src/idp_regression/adapter/transport.py src/idp_regression/adapter/types.py src/idp_regression/adapter/version_probe.py` → 7 files changed, +274/−25 (`errors.py`, `types.py` unchanged).
**Added-files check:** `git diff --name-status --diff-filter=ADR a5805ec..HEAD -- src/idp_regression/adapter/` → none; `git ls-files` vs on-disk `*.py` → identical; `git status --untracked-files=all` on `src/idp_regression/adapter/`, `tests/adapter/`, `tests/fixtures/` → clean.
HEAD blob ids: `__init__.py` `79030c4` · `errors.py` `1bbd4eb` · `idp_client.py` `d8db8e3` · `normalize.py` `e935942` · `oauth.py` `a688d14` · `token_cache.py` `67df578` · `transport.py` `c74c2b2` · `types.py` `b56e82e` · `version_probe.py` `3f0381f`.

## Verdict
✅ **PASSED WITH FINDINGS.** Floor clean at the expected numbers; 20 of 22 invariant-derived mutants killed with tight blast radii (1–6 tests each), one survivor is equivalent (M19), one is a real pin gap on an unchanged line (M4 → F-1) but the guarded behaviour is correct at HEAD. The DEBT-69(a) parser is fail-closed on every malformed shape probed and its documentation-example pin is honestly labelled everywhere; G-2 is closed against recorded live responses. No fail-open was found in this delta. F-1..F-4 are non-blocking and are handed to Dunga for the register.

## History
- 2026-09-22 — ✅ PASSED (superseded, stale per DEBT-46)
- 2026-09-23 @ `a5805ec` — ✅ PASSED (/test re-gate, DEBT-46; superseded by this stamp)
- 2026-09-28 @ `597b912` — ✅ PASSED WITH FINDINGS (this stamp, re-stamp #3, Wave C)

<details>
<summary>Prior stamp (2026-09-23 @ <code>a5805ec</code>) — preserved verbatim</summary>

# /test stamp — SPEC-01 (S-01.2 IDP adapter + normalize)

**Status:** ✅ PASSED
**Source:** /test re-gate (DEBT-46 staleness — `adapter/` moved substantially since 2026-09-22)
**Date:** 2026-09-23 · **Commit gated:** `a5805ec` (`feat/S-01.2-idp-adapter`)
**Rigor:** SPEC-01 header is `Risk: high` ⇒ this stamp is required regardless of the `prototype`
profile. **Profile ≠ risk level.**

## DEBT-44 independence
The auditing instance issued **no** APPROVE, REQUEST CHANGES or review verdict on `6755fa1`,
`6489560`, `17a34c4`, `97f3d13`, `71b1bbf`, `a06c01f` or `a5805ec`. Fresh instance, no prior
context on the delta — which is why it ran this gate. No self-gating occurred.

## Delta gated
`367065b..a5805ec -- src/idp_regression/adapter/` — 5 files, +617/−37: `normalize.py` (REG-11 D1
union rule, D2 confidence scale, R-1/R-2 collision precedence), `version_probe.py` (new),
`idp_client.py` (`?valueOnly=false` incl. retry paths, 10 s poll floor), `oauth.py` (new),
`transport.py` (`post_empty_multipart`).

**Freshness check re-baselined against an explicit FILE LIST, not the directory** — closes
DEBT-77, which correctly observed the directory-scoped check fires red for a non-reason the
moment a story adds a module (`version_probe.py` did exactly that). **Scope ruling:**
`version_probe.py` is IN scope — adapter-package code reusing the adapter's transport and token
cache by design (ADR-0006 §A′.1 D14); excluding it would leave a new fail-open-capable module
with no `/test` gate at all.

## Mechanical floor — actual numbers at `a5805ec`, `__pycache__` cleared
| gate | result |
|---|---|
| `pytest -q` | ✅ **1085 passed, 15 skipped**, 28.26 s |
| `mypy src tests` (strict) | ✅ no issues, 94 files |
| `ruff check src tests` | ✅ clean |
| `pip-audit` | ✅ no known vulnerabilities |
| `gitleaks` | **skipped: prototype profile** — not independently attested by this stamp |

## Mutants verified by hand (reverted, run, restored byte-identical, `shasum`-verified)
| # | mutation | observed |
|---|---|---|
| M1 | resurrect the original REG-11 D1 fail-open (`raw.get("pages", [])`, drop the top-level container) | ✅ **40 failed** |
| M2 | swap the two `logical_pages` blocks (invert union collision precedence) | ✅ **exactly 2 failed** — surgical, not incidental |
| M3 | `_coerce_confidence` out-of-range raise → `return None` (the D2 fail-open) | ✅ **6 failed** across both scales |
| M4 | drop the R3 echo binding (any 404 reads ABSENT) | ✅ **3 failed** |

**REG-11's family is terminally pinned.** M1 proves a regression to the original shape is caught
40 ways; M2 proves the precedence the union made newly observable is caught too. The arbitration
is gone, not moved one level deeper again.

## Vacuity sampling — two findings, stamped OVER, not hidden
**G-1 — the live capture is NOT load-bearing on the confidence scale.** The fixture-shape pin
asserts the ENVELOPE (`"pages" not in LIVE_FIXTURE`) and nothing else. Mechanically rewriting
`tests/fixtures/live/seed-001-clean.raw.json` to convert every `"confidenceScore": 99.0` (the real
0–100 wire key) into legacy `"confidence": 0.99` — i.e. back into the hand-authored shape REG-11 D2
was about — left the suite **fully green at 1085**. So half of REG-11 has no fixture-shape pin: a
"tidy up the fixture" pass can delete the project's only evidence of the real confidence key and
scale without one test noticing. **SR-1 violation in substance** — load-bearing for the envelope,
decorative for the scale. Control probe: reshaping into `pages[]` correctly turned exactly one test
RED, so the D1 half works. Fix is one line beside the existing assertion.

**G-2 — `classify_probe_response`'s EXISTS branch is unbound, and its test's parameters are
vacuous.** Verified directly at HEAD:
```
classify_probe_response(400, "Invalid query parameter 'file'",
                        action_id='totally-different-action', version='0.0.0')  -> exists
classify_probe_response(404, "...Id: other and version 9.9.9 not found",
                        action_id='mine', version='1.0.0')                      -> unknown
```
The 400 body carries no echo to bind against, so this is arguably unbindable — but the test's
arguments give false assurance of a binding that does not exist, and nothing pins "EXISTS is
deliberately unbound", so a later reviewer cannot tell the asymmetry is a decision. **Live
consequence:** a wrong-org or wrong-action 400 reads as EXISTS, so the detector can report "a new
version exists" after a silent credential/org swap. The negative-control probe mitigates it;
mitigation is not a pin.

## What this stamp does NOT cover — read before trusting a green build
1. **No prompt-bearing live capture exists.** The whole `prompts` branch — `_merge_prompts`,
   `_SAFE_PROMPT_PATTERN`, `duplicate_prompt`, the R-2 `allow_override` relaxation — is pinned only
   against hand-authored fixtures. DEBT-69(a). ⛔ Do not fabricate a capture to close this.
2. **No multi-page live capture exists.** The `pages[]` path is now the legacy branch and nothing
   real has ever exercised it. DEBT-69(b). The old "compounding confidence" warning is **struck and
   re-verified by mutation at HEAD** — one `_coerce_confidence`, both paths through the same loop —
   so Wave-0's PROVISIONAL flag on that strike is **lifted**.
3. **No live capture of the wrong-org 404.** R3's mismatch→UNKNOWN branch is pinned by synthetic
   input only. SR-1 clause (3).
4. **G-1 and G-2**, above.
5. **The 10 s poll floor is unreachable in production** — `DEFAULT == MIN == 10.0` and no env var,
   so the guard hardened in `6755fa1` is dead code on the only path that matters. DEBT-71/78.
6. `gitleaks` skipped; live IDP submit/poll unexercised until S-01.6.
7. ⚠️ **All four defects this module produced in two days were fail-open, and not one was found by
   the test suite** — every one came from mutation testing or independent review, exactly as
   `CLAUDE.md ## Rigor` warns. Green here means "the MVP's own tests pass", not "this gate can be
   trusted to gate another team's prompt changes."

## Verdict
✅ **PASSED.** Floor clean at the expected numbers; the REG-11 D1/D2 fixes and the R-1/R-2
precedence rules genuinely constrain (four mutants killed with tight blast radii, hand-verified);
the union rule is terminal. G-1 and G-2 are pin/coverage gaps, not defects in shipped behaviour,
and are named rather than absorbed.

## History
- 2026-09-22 — ✅ PASSED (superseded, stale per DEBT-46)
- 2026-09-23 @ `a5805ec` — ✅ PASSED (this stamp)

</details>

</details>
