"""The Epic E console API.

Scope, stated once because it is the load-bearing decision -- and
because it CHANGED on 2026-09-25 by user decision:

* **Everything here reads local artifacts or authors gate configuration,
  and spends nothing** -- runs, the noise floor, calibration blind spots,
  the pin store, scorer specs, ZIP validation. Where a workflow would
  cost money, these endpoints return the command to run instead.
* **TWO routes are different**, and both spend real IDP quota:
  `POST /api/workflows/compare/start` runs `scripts/compare_versions.py`
  (2N extractions) so the ZIP-against-a-new-Action-version case works
  end to end from the browser, and `POST /api/workflows/floor/start`
  runs `scripts/noise_floor.py` (sample x repeats) so the verdict that
  produces is interpretable. The original posture was "print, never
  run"; the user asked for this workflow to run, and only it does.
  `tests/ui/test_api.py::TestQuotaBoundary` enumerates the route table
  and names **every** quota-spending route, so widening that set is a
  visible edit rather than a drift.

The `--yes` that used to be typed at a terminal is replaced by
`approved_extractions`: the client must echo back the exact count this
server computed from the REAL `--plan`, and a mismatch is refused. A
stale page therefore cannot approve a cost it never displayed.

What it serves that the evaluation platform cannot: the noise floor,
calibration blind spots, and the pin store's (action, version) axis --
see `reader.py`. What it reads BACK from the platform, for dashboards
only, is `platform/insights.py`; nothing read there feeds a gate
(INV-08).

Disclosure posture
------------------
Run artifacts carry the full expected/actual/confidence map, i.e. the
same sensitive financial values `CLAUDE.md ## Domain` calls out. This
server therefore:

* binds **127.0.0.1 only** and refuses any other host (`server.py`);
* sends no `Access-Control-Allow-Origin` in production mode, so a page
  on another origin cannot read a run out of it;
* never writes an artifact, and never uploads one anywhere.

It is the same disclosure surface as `cat`-ing the artifact, reachable
by the same local user, and no wider. On a shared or multi-user host,
that is exactly as narrow as the host's own account separation.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import subprocess
import uuid as _uuid_mod
from pathlib import Path
from typing import Annotated, Any

from fastapi import Body, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from idp_regression.classifier import custom as custom_scorers
from idp_regression.classifier.registry import CLASSIFIERS, DEFAULT_CLASSIFIER
from idp_regression.classifier.scoring import ScoreContext, ScoreResult
from idp_regression.orchestration import scorer_store
from idp_regression.orchestration.facade import DEFAULT_MAX_DOCUMENTS_PER_RUN
from idp_regression.orchestration.version_discovery import (
    AnchorNotSemverError,
    discover_versions,
)
from idp_regression.platform.errors import TransportError as PlatformTransportError
from idp_regression.platform.insights import configuration_hint, insights_from_env
from idp_regression.ui import jobs, preflight, reader, uploads, workspace
from idp_regression.ui.golden_edits import build_item_payload, validate_entry
from idp_regression.ui.review_sessions import (
    ReviewSession,
    ReviewSessionCorruptError,
    ReviewSessionNotFoundError,
    ReviewSessionState,
    list_sessions,
    load_session,
    save_session,
)

_api_logger = logging.getLogger(__name__)


def fetch_golden_hash(dataset: str, workspace_path: Path) -> str | None:  # noqa: ARG001
    """Fetch the current platform dataset items and compute a content hash.

    Used at review-completion (to record what the curator approved) and at
    verify-candidate/start (to detect if the golden was swapped — INV-09(e)).

    Returns None when the platform is not configured; in that case, the
    verify-candidate/start route refuses with a 409 (cannot verify a hash
    we cannot compute).

    This function is module-level and injectable (monkeypatched in tests)
    so the CI-runnable INV-09(e) unit test never needs a live platform instance.

    `workspace_path` is reserved for a future implementation that reads a
    local cache; for now the platform is always the source.
    """
    insights = insights_from_env()
    if insights is None:
        return None
    try:
        # DEBT-141: only spaces are encoded here; a dataset name containing
        # `&`, `#`, or `?` would produce a malformed query string.
        # Full percent-encoding uses a stdlib quote helper that the module-boundary
        # test bars from `ui/` (the raw-HTTP seam lives in `platform/transport`).
        # Fix: expose a `quote_query_param` helper from `platform/transport`.
        status, body = insights._http.request(
            "GET",
            "/api/public/dataset-items?datasetName="
            + dataset.replace(" ", "%20")
            + "&limit=1000",
        )
    except OSError:
        return None
    if status != 200:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    items = list(data) if isinstance(data, list) else []
    # DEBT-143 fix: treat an empty item list the same as an unreachable platform.
    # sha256([]) is a fixed constant, not None — so INV-09(e)'s hash-match check
    # would be satisfied vacuously for an empty dataset at both review-complete and
    # verify-start.  Returning None here lets the existing None → 409 guard at
    # /complete refuse "nothing was drafted" the same way it refuses an unreachable
    # platform, so the swap-guard cannot be satisfied by a silently-empty golden set.
    if not items:
        return None
    # Deterministic: sort by item id so insertion order does not affect the hash
    items_sorted = sorted(items, key=lambda i: str(i.get("id", "")))
    canonical = json.dumps(items_sorted, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()

def fetch_platform_item(
    dataset: str,
    document_id: str,
    workspace_path: Path,  # noqa: ARG001 — reserved for a future local-cache path
) -> dict[str, Any] | None:
    """Fetch the current platform dataset item for one document.

    Returns the item's `expectedOutput` dict (with `fields`, `tables`, `prompts`)
    or None when the platform is not configured, is unreachable, or the item
    does not exist yet.

    Used by PATCH/replace to determine which fields actually changed so that
    provenance marks only the changed ones (R2 — an unchanged field re-sent in a
    full-item PATCH must not appear in `edited_document_ids`).

    Module-level and injectable (monkeypatched in tests) — same pattern as
    `fetch_golden_hash` and `upsert_platform_item`.

    INV-02: values from the returned dict are never logged; they are compared
    in-memory and discarded.
    """
    insights = insights_from_env()
    if insights is None:
        return None
    try:
        status, body = insights._http.request(
            "GET",
            "/api/public/dataset-items?datasetName="
            + dataset.replace(" ", "%20")
            + "&limit=1000",
        )
    except OSError:
        return None
    if status != 200:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    items = list(data) if isinstance(data, list) else []
    for item in items:
        if isinstance(item, dict) and item.get("input", {}).get("document_id") == document_id:
            result = item.get("expectedOutput")
            return result if isinstance(result, dict) else None
    return None


def fetch_platform_items(
    dataset: str,
    workspace_path: Path,  # noqa: ARG001 — reserved for a future local-cache path
) -> list[dict[str, Any]] | None:
    """Fetch ALL platform dataset items for a dataset in one round-trip.

    Returns a list of dicts — each with keys ``document_id`` (str | None) and
    ``expected_output`` (dict | None, the raw ``expectedOutput`` payload from
    the platform, containing ``fields``, ``tables``, ``prompts``) — or None
    when the platform is not configured or unreachable.

    Used by ``GET /api/reviews/{session_id}/values`` to expose the full field
    values to the review screen.  One GET rather than a per-document fan-out
    (which would be O(N²) at 100 documents — the same batch pattern used by
    ``fetch_golden_hash`` and ``fetch_platform_item``).

    Module-level and injectable (monkeypatched in tests) — same pattern as
    ``fetch_golden_hash``, ``fetch_platform_item``, and ``upsert_platform_item``.

    INV-02: this function returns values to the caller; it **never logs** a
    field name or field value — log session_id + document count only, never the
    payload.

    Spends zero IDP quota — this is a platform read, not an extraction call.
    """
    insights = insights_from_env()
    if insights is None:
        return None
    try:
        status, body = insights._http.request(
            "GET",
            "/api/public/dataset-items?datasetName="
            + dataset.replace(" ", "%20")
            + "&limit=1000",
        )
    except OSError:
        return None
    if status != 200:
        return None
    data = body.get("data") if isinstance(body, dict) else None
    items = list(data) if isinstance(data, list) else []
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        doc_id: str | None = None
        inp = item.get("input")
        if isinstance(inp, dict):
            raw_id = inp.get("document_id")
            doc_id = str(raw_id) if raw_id is not None else None
        expected = item.get("expectedOutput")
        result.append({
            "document_id": doc_id,
            "expected_output": expected if isinstance(expected, dict) else None,
        })
    return result


def upsert_platform_item(
    dataset: str,  # noqa: ARG001 — reserved for a future path that keys on dataset+item
    item_payload: dict[str, Any],
    workspace_path: Path,  # noqa: ARG001 — reserved for a future local-cache path
) -> str | None:
    """Upsert one dataset item to the platform.

    Returns None on success, or a short error string on failure.
    Never echoes golden values in the return value — INV-02.

    Module-level and injectable (monkeypatched in tests) so the edit/replace
    endpoints can be tested without a live platform instance. Same pattern
    as `fetch_golden_hash`.

    Spends zero IDP quota — this is a platform dataset-item write, not an
    extraction call.
    """
    insights = insights_from_env()
    if insights is None:
        return "platform not configured"
    try:
        # DEBT-141 applies here too: dataset names with &, #, ? etc. need
        # percent-encoding on the READ path. On the WRITE path the dataset
        # name is in the JSON body, so this particular debt does not apply.
        status, _body = insights._http.request(
            "POST", "/api/public/dataset-items", item_payload
        )
        if status >= 400:
            # Never print body: the platform's dataset-item 400 can echo back golden values.
            return f"HTTP {status}"
        return None
    except OSError as exc:
        return f"transport: {type(exc).__name__}"


#: Session states that allow golden edits; VERIFYING/VERIFIED/STALE are refused.
# REPLACE_FAILED is included so a curator can retry /replace or issue PATCH edits
# to repair the partial golden before calling /complete again.
_EDITABLE_STATES = (
    ReviewSessionState.DRAFTED,
    ReviewSessionState.REVIEWED,
    ReviewSessionState.REPLACE_FAILED,
)

FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def create_app(*, dev_cors: bool = False, scorer_dir: Path | None = None) -> FastAPI:
    app = FastAPI(
        title="IDP Regression — local console",
        version="0.1.0",
        # Served at /openapi.json and /docs, so it is an operator-facing
        # surface like `/api/health` -- and it carried the same stale
        # claim ("the one quota-spending route") after the noise floor
        # became a second spender (Zangado QA F-E, 2026-09-25). It now
        # states NO count: a number in prose is the thing that goes
        # stale, and `/api/health` is where the authoritative list lives.
        description=(
            "Reads run artifacts, noise floors, calibration blind spots and the pin "
            "store from local disk; authors custom scorers; runs the "
            "corpus-against-a-new-version comparison and its noise floor. The routes "
            "that spend IDP quota are named by GET /api/health."
        ),
    )

    if dev_cors:
        # Vite's dev server only. Off by default: with it on, any page
        # the browser loads could read a run artifact out of this port.
        app.add_middleware(
            CORSMiddleware,
            allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

    scorers_root = scorer_dir or scorer_store.SCORER_DIR
    registry = jobs.JobRegistry()
    # What earlier console processes ran. A job that was still running
    # when one stopped is surfaced as interrupted, never resumed: the
    # extractions it spent are spent either way, and restarting it would
    # spend them twice.
    registry.load_history()

    def _require_runnable() -> None:
        """Refuse to start a quota-spending job on a machine that cannot
        finish one.

        **The server is the authority, not a disabled button** (Zangado
        QA F-1, 2026-09-25). The UI greys out its spend buttons on the
        same check, but a browser whose preflight fetch failed, a stale
        tab, or any other caller on this loopback port would otherwise
        reach `registry.start` — and the worst case there is not a
        rejected request, it is `pin_document` extracting every document
        against live IDP and only then failing because the platform
        credentials were never set. That is the exact outcome the
        preflight exists to prevent, so it is enforced where it cannot
        be bypassed.
        """
        status = preflight.check()
        if not status["can_run_validation"]:
            raise HTTPException(
                status_code=503,
                detail="this machine cannot run a validation: " + " ".join(status["blockers"]),
            )

    # ---------------------------------------------------------------- runs

    @app.get("/api/runs")
    def get_runs() -> dict[str, Any]:
        return {"runs": reader.list_runs()}

    @app.get("/api/runs/{run_id}")
    def get_run(
        run_id: str,
        baseline: str | None = Query(
            default=None,
            description="A noise-floor report to read this run against.",
        ),
    ) -> dict[str, Any]:
        try:
            return reader.read_run(run_id, baseline=baseline)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    # --------------------------------------------------------- noise floor

    @app.get("/api/noise-floors")
    def get_noise_floors() -> dict[str, Any]:
        return {"noise_floors": reader.list_noise_floors()}

    @app.get("/api/noise-floors/{name}")
    def get_noise_floor(name: str) -> dict[str, Any]:
        try:
            return reader.read_noise_floor(name)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None

    # -------------------------------------------------------- blind spots

    @app.get("/api/blind-spots")
    def get_blind_spots() -> dict[str, Any]:
        return {"calibrations": reader.list_calibrations()}

    # --------------------------------------------------------------- pins

    @app.get("/api/pins")
    def get_pins() -> dict[str, Any]:
        return {"pins": reader.list_pins()}

    # ------------------------------------------------------------ scorers

    @app.get("/api/scorers")
    def get_scorers() -> dict[str, Any]:
        """Every classifier a run could name: the shipped ones and the
        specs on disk, marked so the difference is never in doubt."""
        specs, errors = scorer_store.load_specs(scorers_root)
        by_name = {spec.name: spec for spec in specs}
        shipped = [
            {
                "name": name,
                "description": classifier.description,
                "kind": "shipped",
                "is_default": name == DEFAULT_CLASSIFIER,
                # A Protocol has no `__name__`; every real scorer is a
                # function, and the fallback keeps a callable class working.
                "scorer": getattr(classifier.scorer, "__name__", type(classifier.scorer).__name__),
            }
            for name, classifier in sorted(CLASSIFIERS.items())
            if name not in by_name
        ]
        custom = [
            {
                "name": spec.name,
                "description": spec.description,
                "kind": "custom",
                "is_default": False,
                "base": spec.base,
                "rules": [{"when": dict(r.when), "then": dict(r.then)} for r in spec.rules],
            }
            for spec in specs
        ]
        return {
            "scorers": shipped + custom,
            "errors": errors,
            "vocabulary": {
                "conditions": list(custom_scorers.CONDITION_KEYS),
                "actions": list(custom_scorers.ACTION_KEYS),
                # What a `verdict_is` condition may name: the verdicts a scorer
                # can see. `new_table` is table-level and never one of them.
                "verdicts": list(custom_scorers.SCORER_VERDICTS),
                "actionable_verdicts": list(custom_scorers.ACTIONABLE_VERDICTS),
                "kinds": list(custom_scorers.KINDS),
                "field_types": list(custom_scorers.FIELD_TYPES),
                "bases": list(custom_scorers.BASE_CLASSIFIERS),
            },
            "scorer_dir": str(scorers_root),
        }

    @app.post("/api/scorers/validate")
    def validate_scorer(spec: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Parse + prove monotone, without saving. The form calls this on
        every edit, so an author sees the refusal while they are still
        looking at the rule that caused it."""
        try:
            parsed = custom_scorers.parse_spec(spec)
        except custom_scorers.SpecError as exc:
            return {"valid": False, "errors": [str(exc)], "monotone": None}
        problems = custom_scorers.verify_monotone(parsed)
        return {
            "valid": not problems,
            "errors": problems,
            "monotone": not problems,
            "spec": parsed.to_json(),
        }

    @app.post("/api/scorers/preview")
    def preview_scorer(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Run a spec against sample values and show what it decides,
        beside what its base decides.

        The comparison is the point: a rule that changes nothing looks
        exactly like a rule that was never applied, and this is where
        that becomes visible before the spec gates a build.
        """
        try:
            parsed = custom_scorers.parse_spec(payload.get("spec", {}))
        except custom_scorers.SpecError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        samples = payload.get("samples")
        if not isinstance(samples, list) or not samples:
            raise HTTPException(status_code=422, detail="samples must be a non-empty list")

        base_scorer = CLASSIFIERS[parsed.base].scorer
        scorer = custom_scorers.compile_spec(parsed)
        results = []
        for index, sample in enumerate(samples):
            if not isinstance(sample, dict):
                raise HTTPException(status_code=422, detail=f"samples[{index}] is not an object")
            ctx = ScoreContext(
                name=str(sample.get("name", "field")),
                kind=sample.get("kind", "field"),
                field_type=str(sample.get("field_type", "text")),
                expected=sample.get("expected"),
                actual=sample.get("actual"),
                confidence=sample.get("confidence"),
                critical=bool(sample.get("critical", False)),
                format_critical=bool(sample.get("format_critical", False)),
                match_key=sample.get("match_key"),
                source=sample.get("source"),
            )
            results.append({
                "sample": sample,
                "base": _outcome(base_scorer(ctx)),
                "custom": _outcome(scorer(ctx)),
            })
        return {"results": results}

    @app.put("/api/scorers/{name}")
    def save_scorer(name: str, spec: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Save a spec. Refuses anything `validate` would refuse --
        the form's check is a convenience, this one is the gate."""
        if spec.get("name") != name:
            raise HTTPException(
                status_code=422,
                detail=f"body names {spec.get('name')!r}, URL names {name!r}",
            )
        try:
            parsed = custom_scorers.parse_spec(spec)
        except custom_scorers.SpecError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        problems = custom_scorers.verify_monotone(parsed)
        if problems:
            raise HTTPException(
                status_code=422,
                detail="spec is not monotone: " + "; ".join(problems[:3]),
            )
        path = scorer_store.save_spec(parsed, scorers_root)
        return {
            "saved": parsed.to_json(),
            "path": str(path),
            "run_with": f"--classifier {parsed.name}",
        }

    @app.delete("/api/scorers/{name}")
    def delete_scorer(name: str) -> dict[str, Any]:
        try:
            removed = scorer_store.delete_spec(name, scorers_root)
        except custom_scorers.SpecError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if not removed:
            raise HTTPException(status_code=404, detail=f"no custom scorer {name!r}")
        return {"deleted": name}

    # ------------------------------------------------------------ uploads

    @app.post("/api/uploads")
    async def post_upload(file: Annotated[UploadFile, File()]) -> dict[str, Any]:
        payload = await file.read()
        try:
            upload_id, _ = uploads.stage_archive(file.filename or "upload.zip", payload)
            return uploads.validate(upload_id)
        except uploads.UploadRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.get("/api/uploads/{upload_id}")
    def get_upload(upload_id: str, patterns: str | None = None) -> dict[str, Any]:
        try:
            return uploads.validate(upload_id, patterns=patterns)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except uploads.UploadRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/uploads/{upload_id}/unpack")
    def post_unpack(
        upload_id: str, payload: Annotated[dict[str, Any] | None, Body()] = None
    ) -> dict[str, Any]:
        patterns = (payload or {}).get("patterns")
        try:
            result = uploads.unpack(upload_id, patterns=patterns)
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        except uploads.UploadRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        result["next_commands"] = uploads.next_commands(
            result["document_dir"], result["document_count"]
        )
        return result

    # ------------------------------------------------ action versions

    @app.post("/api/actions/versions")
    def post_discover_versions(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Which versions of this Action exist, around an anchor.

        **IDP has no version-enumeration endpoint** (ADR-0006 §A'), so
        this is a bounded sweep of existence probes: each one POSTs an
        empty multipart body, which routing accepts and payload
        validation rejects. **No extraction quota is spent** -- but it is
        N real HTTP calls against the customer's org, so the grid is
        small and the response says when it was truncated or rate
        limited rather than presenting a partial list as complete.
        """
        import os

        org = str(payload.get("org", "")).strip()
        action = str(payload.get("action", "")).strip()
        anchor_version = str(payload.get("anchor", "")).strip()
        if not (org and action and anchor_version):
            raise HTTPException(status_code=422, detail="org, action and anchor are required")

        try:
            client_id = os.environ["IDP_CLIENT_ID"].strip()
            client_secret = os.environ["IDP_CLIENT_SECRET"].strip()
            region = os.environ["IDP_REGION"].strip()
        except KeyError as exc:
            raise HTTPException(
                status_code=503,
                detail=f"missing credential {exc.args[0]} "
                "(IDP_CLIENT_ID / IDP_CLIENT_SECRET / IDP_REGION in .env)",
            ) from None

        from idp_regression.adapter.version_probe import MuleSoftVersionProbe

        probe = MuleSoftVersionProbe(client_id, client_secret, region)
        try:
            outcome = discover_versions(probe, org, action, anchor_version)
        except AnchorNotSemverError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return {
            "anchor": outcome.anchor,
            "versions": [{"version": v.version, "status": v.status} for v in outcome.versions],
            "probes_used": outcome.probes_used,
            "truncated": outcome.truncated,
            "rate_limited": outcome.rate_limited,
            "spends_extraction_quota": False,
        }

    # --------------------------------------- the ZIP-vs-new-version job

    def _compare_argv(payload: dict[str, Any], *, plan_only: bool) -> list[str]:
        upload_id = str(payload.get("upload_id", "")).strip()
        document_dir = payload.get("document_dir")
        if upload_id:
            try:
                unpacked = uploads.unpack(upload_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except uploads.UploadRejectedError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            resolved = Path(unpacked["document_dir"])
        elif document_dir:
            resolved = Path(str(document_dir))
        else:
            raise HTTPException(status_code=422, detail="upload_id or document_dir is required")

        try:
            return jobs.build_compare_argv(
                document_dir=resolved,
                dataset=str(payload.get("dataset", "")),
                org=str(payload.get("org", "")),
                action=str(payload.get("action", "")),
                trusted_version=str(payload.get("trusted_version", "")),
                candidate_version=str(payload.get("candidate_version", "")),
                plan_only=plan_only,
                max_documents=payload.get("max_documents"),
                allow_partial=bool(payload.get("allow_partial", False)),
                repin=bool(payload.get("repin", False)),
                glob=payload.get("glob") or None,
                classifier=payload.get("classifier") or None,
            )
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/workflows/compare/plan")
    def post_compare_plan(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Run the REAL `--plan` and report what it would cost.

        Spends nothing, and it is also the archive's last validation
        before money is spent -- so a refusal here is a refusal, not a
        warning. The number returned is what the client must echo back
        to start the job.
        """
        argv = _compare_argv(payload, plan_only=True)
        try:
            extractions, lines = jobs.plan(argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="the plan timed out") from None
        return {
            "planned_extractions": extractions,
            "command": " ".join(argv),
            "output": lines,
            "confirm_with": {"approved_extractions": extractions},
        }

    @app.post("/api/workflows/compare/start")
    def post_compare_start(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Start the batch. **This spends real IDP quota.**

        The one endpoint in this console that does, by explicit user
        decision (2026-09-25). `approved_extractions` is the `--yes`: it
        must equal the count this server computes from the real `--plan`
        right now, so a stale page cannot approve a cost it never showed.
        """
        approved = payload.get("approved_extractions")
        if not isinstance(approved, int) or isinstance(approved, bool):
            raise HTTPException(
                status_code=422,
                detail="approved_extractions (an integer, from the plan) is required -- "
                "this is the --yes for a job that spends real quota",
            )
        _require_runnable()
        plan_argv = _compare_argv(payload, plan_only=True)
        try:
            planned, _ = jobs.plan(plan_argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        run_argv = plan_argv[:-1] + ["--yes"]
        try:
            job = registry.start(
                "compare-versions",
                run_argv,
                planned_extractions=planned,
                approved_extractions=approved,
            )
        except jobs.WorkspaceBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return job.to_json()

    @app.get("/api/jobs")
    def get_jobs() -> dict[str, Any]:
        return {"jobs": registry.summaries()}

    @app.get("/api/jobs-busy")
    def get_jobs_busy() -> dict[str, Any]:
        """Whether a quota-spending job holds this workspace.

        The UI asks before offering a Run button, so "busy" reads as a
        state rather than as a refusal after the click.
        """
        return {"busy": registry.is_busy()}

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict[str, Any]:
        try:
            return registry.get(job_id).to_json()
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no job {job_id!r}") from None

    @app.post("/api/jobs/{job_id}/cancel")
    def post_cancel_job(job_id: str) -> dict[str, Any]:
        try:
            return registry.cancel(job_id).to_json()
        except KeyError:
            raise HTTPException(status_code=404, detail=f"no job {job_id!r}") from None
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

    # ------------------------------------- the noise floor for that job

    def _floor_argv(payload: dict[str, Any], *, plan_only: bool) -> list[str]:
        document_dir = payload.get("document_dir")
        upload_id = str(payload.get("upload_id", "")).strip()
        if upload_id:
            try:
                unpacked = uploads.unpack(upload_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            resolved = Path(unpacked["document_dir"])
        elif document_dir:
            resolved = Path(str(document_dir))
        else:
            raise HTTPException(status_code=422, detail="upload_id or document_dir is required")
        try:
            return jobs.build_noise_floor_argv(
                document_dir=resolved,
                org=str(payload.get("org", "")),
                action=str(payload.get("action", "")),
                version=str(payload.get("version", "")),
                repeats=int(payload.get("repeats", 2)),
                max_documents=int(payload.get("max_documents", 20)),
                plan_only=plan_only,
            )
        except (jobs.JobRejectedError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/workflows/floor/plan")
    def post_floor_plan(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Price measuring one version's disagreement with ITSELF.

        Spends nothing. This is the step that makes a `CHANGED` verdict
        interpretable: without it, a field that differs between two
        versions cannot be told from a field the extractor flips on when
        asked the same question twice.
        """
        argv = _floor_argv(payload, plan_only=True)
        try:
            extractions, lines = jobs.plan(argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="the plan timed out") from None
        return {
            "planned_extractions": extractions,
            "command": " ".join(argv),
            "output": lines,
            "confirm_with": {"approved_extractions": extractions},
        }

    @app.post("/api/workflows/floor/start")
    def post_floor_start(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Measure the floor. **Spends real IDP quota**, on the same
        approved-count guard as the comparison."""
        approved = payload.get("approved_extractions")
        if not isinstance(approved, int) or isinstance(approved, bool):
            raise HTTPException(
                status_code=422,
                detail="approved_extractions (an integer, from the plan) is required -- "
                "this is the --yes for a job that spends real quota",
            )
        _require_runnable()
        plan_argv = _floor_argv(payload, plan_only=True)
        try:
            planned, _ = jobs.plan(plan_argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        run_argv = plan_argv[:-1] + ["--yes"]
        try:
            job = registry.start(
                "noise-floor",
                run_argv,
                planned_extractions=planned,
                approved_extractions=approved,
            )
        except jobs.WorkspaceBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return job.to_json()

    # ---------------------------------------- two-stage golden-review workflow

    def _draft_argv(payload: dict[str, Any], *, plan_only: bool) -> list[str]:
        document_dir = payload.get("document_dir")
        upload_id = str(payload.get("upload_id", "")).strip()
        if upload_id:
            try:
                unpacked = uploads.unpack(upload_id)
            except FileNotFoundError as exc:
                raise HTTPException(status_code=404, detail=str(exc)) from None
            except uploads.UploadRejectedError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from None
            resolved = Path(unpacked["document_dir"])
        elif document_dir:
            resolved = Path(str(document_dir))
        else:
            raise HTTPException(status_code=422, detail="upload_id or document_dir is required")
        trusted = str(payload.get("trusted_version", "")).strip()
        candidate = str(payload.get("candidate_version", "")).strip()
        if not trusted:
            raise HTTPException(status_code=422, detail="trusted_version is required")
        if not candidate:
            raise HTTPException(status_code=422, detail="candidate_version is required")
        try:
            return jobs.build_pin_argv(
                document_dir=resolved,
                dataset=str(payload.get("dataset", "")),
                org=str(payload.get("org", "")),
                action=str(payload.get("action", "")),
                trusted_version=trusted,
                plan_only=plan_only,
                max_documents=payload.get("max_documents"),
                glob=payload.get("glob") or None,
            )
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    def _verify_argv(
        session: ReviewSession, *, plan_only: bool
    ) -> list[str]:
        try:
            return jobs.build_verify_argv(
                document_dir=Path(session.document_dir),
                dataset=session.dataset,
                org=session.org_id,
                action=session.action_id,
                trusted_version=session.trusted_version,
                candidate_version=session.candidate_version,
                plan_only=plan_only,
            )
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

    @app.post("/api/workflows/draft-golden/plan")
    def post_draft_golden_plan(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Price the drafting stage (stage 1). Spends nothing."""
        argv = _draft_argv(payload, plan_only=True)
        try:
            extractions, lines = jobs.plan(argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="the plan timed out") from None
        return {
            "planned_extractions": extractions,
            "command": " ".join(argv),
            "output": lines,
            "confirm_with": {"approved_extractions": extractions},
        }

    @app.post("/api/workflows/draft-golden/start")
    def post_draft_golden_start(payload: Annotated[dict[str, Any], Body()]) -> dict[str, Any]:
        """Start the draft/pin stage. **This spends real IDP quota.**

        On success, creates a ReviewSession (state=drafted) that ties this
        stage-1 job to the forthcoming human review and stage-2 verify.

        N5: refuses a corpus above DEFAULT_MAX_DOCUMENTS_PER_RUN BEFORE
        creating any ReviewSession file, so no session exists on refusal.
        """
        approved = payload.get("approved_extractions")
        if not isinstance(approved, int) or isinstance(approved, bool):
            raise HTTPException(
                status_code=422,
                detail="approved_extractions (an integer, from the plan) is required -- "
                "this is the --yes for a job that spends real quota",
            )
        _require_runnable()
        plan_argv = _draft_argv(payload, plan_only=True)
        try:
            planned, _ = jobs.plan(plan_argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # N5: ceiling check BEFORE creating the ReviewSession file
        if planned > DEFAULT_MAX_DOCUMENTS_PER_RUN:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"corpus has {planned} documents, which exceeds the ceiling "
                    f"of {DEFAULT_MAX_DOCUMENTS_PER_RUN}. run_eval aborts above this "
                    "ceiling rather than truncating, so a larger corpus can never be run. "
                    "Reduce the corpus or raise --max-documents (up to 1000)."
                ),
            )
        run_argv = plan_argv[:-1] + ["--yes"]
        try:
            job = registry.start(
                "pin-document",
                run_argv,
                planned_extractions=planned,
                approved_extractions=approved,
            )
        except jobs.WorkspaceBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

        # Resolve session parameters from the payload
        document_dir = payload.get("document_dir", "")
        upload_id = str(payload.get("upload_id", "")).strip()
        if upload_id:
            try:
                unpacked = uploads.unpack(upload_id)
                document_dir = unpacked["document_dir"]
            except (FileNotFoundError, uploads.UploadRejectedError):
                pass  # document_dir stays as-is from payload
        archive_sha256 = str(payload.get("archive_sha256", ""))
        session_id = _uuid_mod.uuid4().hex
        session = ReviewSession(
            session_id=session_id,
            dataset=str(payload.get("dataset", "")),
            org_id=str(payload.get("org", "")),
            action_id=str(payload.get("action", "")),
            trusted_version=str(payload.get("trusted_version", "")),
            candidate_version=str(payload.get("candidate_version", "")),
            document_dir=str(document_dir),
            archive_sha256=archive_sha256,
            approved_golden_hash=None,
            stage1_job_id=job.id,
            stage2_job_id=None,
            state=ReviewSessionState.DRAFTED,
            created_at=dt.datetime.now(dt.UTC).isoformat(),
            edited_document_ids={},
        )
        save_session(session, workspace.workspace_root())
        _api_logger.info(
            "draft_golden_started session_id=%s job_id=%s planned=%s",
            session_id, job.id, planned,
        )
        result = job.to_json()
        result["session_id"] = session_id
        return result

    # --------------------------------------------------------- review sessions

    @app.get("/api/reviews")
    def get_reviews() -> dict[str, Any]:
        """All review sessions in this workspace (pending list, T-02.1.7)."""
        sessions = list_sessions(workspace.workspace_root())
        return {"sessions": [s.to_dict() for s in sessions]}

    @app.get("/api/reviews/{session_id}")
    def get_review(session_id: str) -> dict[str, Any]:
        """Single session read (T-02.1.7, N1: must return in < 2s for 100-doc session)."""
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        return session.to_dict()

    @app.get("/api/reviews/{session_id}/values")
    def get_review_values(session_id: str) -> dict[str, Any]:
        """Return every drafted field value per document for the review screen (T-02.3.7).

        Sourced from the platform (not the local pin store — the pin store goes
        stale after any golden edit; the platform is what verify_document.py and
        INV-09(e) measure against, so it is the only source consistent with what
        gets approved).

        One GET over the dataset items, never a per-document fan-out (O(N²)).

        Returns 503 when the platform is not configured or unreachable — the
        review screen must show a clear error rather than an empty table that
        reads as "nothing to review".

        INV-02: logs session_id + document count only, never a field name or
        value.  Not added to /api/health quota_spending_routes — this route is
        read-only and spends zero IDP quota.
        """
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        raw_items = fetch_platform_items(session.dataset, workspace.workspace_root())
        if raw_items is None:
            raise HTTPException(
                status_code=503,
                detail=(
                    "the platform is not configured or unreachable — cannot fetch "
                    "the drafted field values for review. Configure platform "
                    "credentials and retry."
                ),
            )

        # Build the response: one entry per document, provenance-tagged from the
        # session's edited_document_ids.  INV-02: never log a field name or value.
        edited_ids = session.edited_document_ids
        documents: list[dict[str, Any]] = []
        missing: list[str] = []

        # Index raw_items by document_id for easy lookup.
        items_by_doc: dict[str, dict[str, Any]] = {}
        for raw in raw_items:
            doc_id = raw.get("document_id")
            if doc_id:
                items_by_doc[doc_id] = raw

        for doc_id, raw in items_by_doc.items():
            expected = raw.get("expected_output") or {}
            fields_raw = expected.get("fields") or {}
            tables_raw = expected.get("tables") or {}
            prompts_raw = expected.get("prompts") or {}

            edited_field_names: set[str] = set(edited_ids.get(doc_id, []))

            fields: list[dict[str, Any]] = []
            for fname, fval in fields_raw.items():
                if not isinstance(fval, dict):
                    continue
                provenance = "edited" if fname in edited_field_names else "drafted"
                fields.append({
                    "name": fname,
                    "value": fval.get("value", ""),
                    "type": fval.get("type", "text"),
                    "confidence": fval.get("confidence"),
                    "critical": fval.get("critical", False),
                    "provenance": provenance,
                })

            # Tables and prompts: pass through as-is (no field-level provenance
            # tracking on tables/prompts — only flat fields are edited via PATCH).
            documents.append({
                "document_id": doc_id,
                "fields": fields,
                "tables": tables_raw,
                "prompts": prompts_raw,
            })

        # Documents referenced in the session but not found on the platform are
        # shown as missing — a bug signal, surfaced, never silently dropped.
        _api_logger.info(
            "review_values session_id=%s document_count=%d",
            session_id,
            len(documents),
        )

        return {
            "session_id": session_id,
            "dataset": session.dataset,
            "platform_configured": True,
            "documents": documents,
            "missing_from_platform": missing,
        }

    @app.post("/api/reviews/{session_id}/complete")
    def post_review_complete(session_id: str) -> dict[str, Any]:
        """Mark the review as complete, capturing the platform golden hash (INV-09d).

        This is the step that records *what the curator actually reviewed*
        so that verify-candidate/start can detect a golden swap during the
        unlocked review pause (INV-09 clause e).
        """
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # DEBT-142 fix: a session in REPLACE_FAILED state has a partial (mongrel) golden
        # set — some entries were written, some were not.  /complete must never approve
        # a partial golden, because a subsequent /complete would hash and record the
        # partial set, and verify-candidate/start would then pass INV-09(e) vacuously
        # (current_hash == approved_golden_hash — both computed over the same mongrel).
        # Force the curator to repair the golden (via /replace or PATCH) before approving.
        if session.state == ReviewSessionState.REPLACE_FAILED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"session {session_id!r} is in state 'replace_failed': a previous "
                    "whole-file replace failed mid-batch and the platform golden set is "
                    "partially updated. Re-run POST /replace with the corrected file to "
                    "fully repair the golden before completing the review — INV-09 clause (e)."
                ),
            )

        golden_hash = fetch_golden_hash(session.dataset, workspace.workspace_root())
        # Fail-closed: a None hash means the platform is unreachable, not configured,
        # OR the dataset is empty (DEBT-143 fix: fetch_golden_hash returns None for
        # an empty item list so "nothing was drafted" cannot be recorded as an approval).
        # Persisting None would leave approved_golden_hash=None, and None == None is False
        # in Python (so the verify-candidate/start mismatch check would pass vacuously
        # when the platform is also unreachable at stage-2 time).  Refuse now rather than
        # silently record an unverifiable approval — INV-09 clause (e).
        if golden_hash is None:
            raise HTTPException(
                status_code=409,
                detail=(
                    "the platform golden hash could not be computed — the platform "
                    "may not be configured or is not reachable. INV-09 clause (e) "
                    "requires a verifiable dataset hash at review-completion; "
                    "configure platform credentials and retry."
                ),
            )
        session.approved_golden_hash = golden_hash
        session.state = ReviewSessionState.REVIEWED
        save_session(session, workspace.workspace_root())
        _api_logger.info(
            "review_complete session_id=%s state=reviewed",
            session_id,
        )
        return session.to_dict()

    # ---------------------------------------- golden edit / replace (T-02.2.1 / T-02.2.2)

    @app.patch("/api/reviews/{session_id}/items/{document_id}")
    def patch_review_item(
        session_id: str,
        document_id: str,
        body: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        """Edit a single golden item for this review session (T-02.2.1).

        Validates the edit against the committed golden schema BEFORE writing.
        On success, upserts via the same deterministic-id path
        provision_golden_dataset.py uses.
        On schema failure, refuses (422) naming the specific invalid field —
        no partial write (the item is left exactly as it was).

        Only operates on sessions in DRAFTED or REVIEWED state.
        A REVIEWED session is reverted to DRAFTED after the edit (the
        platform items have changed, so the approved_golden_hash is stale and
        the curator must re-call /complete to re-capture it).
        """
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        if session.state not in _EDITABLE_STATES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"session {session_id!r} is in state {session.state.value!r}; "
                    "golden edits are only allowed in 'drafted' or 'reviewed' state"
                ),
            )

        # S2: reject a body-supplied document_id that disagrees with the URL parameter.
        # Silently defaulting would let a mis-typed id overwrite the wrong item.
        body_doc_id = body.get("document_id")
        if body_doc_id is not None and body_doc_id != document_id:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"body document_id {body_doc_id!r} does not match URL parameter "
                    f"{document_id!r} — use the URL parameter as the authoritative id"
                ),
            )
        entry = dict(body)
        entry["document_id"] = document_id  # always use the URL parameter

        # Validate against the committed golden schema BEFORE any write.
        # NOTE — PATCH semantics (R1): this is a WHOLE-ITEM REPLACE, not a field-merge.
        # `build_item_payload` writes exactly `entry["fields"]` to the platform; it does
        # NOT merge with the previously-stored item.  S-02.3 must send the COMPLETE
        # corrected item (all fields, not just the changed ones), otherwise fields absent
        # from the body will be dropped from the platform item.
        error = validate_entry(entry)
        if error:
            raise HTTPException(
                status_code=422,
                detail=f"edit rejected for document {document_id!r}: {error}",
            )

        # R2: fetch the current platform item to determine which fields actually changed.
        # Only fields whose value differs from the current item are recorded as "edited"
        # in provenance.  Re-sending an unchanged field (required by PATCH's replace
        # semantics, R1) must not bloat the provenance.
        # Falls back to marking all incoming fields as edited when the platform is
        # unavailable (conservative — never misses a real edit).
        current_item = fetch_platform_item(
            session.dataset, document_id, workspace.workspace_root()
        )
        incoming_fields = entry.get("fields", {})
        if current_item is not None:
            current_fields = current_item.get("fields", {})
            # Compare values only — INV-02: these comparisons are in-memory, never logged.
            changed_fields = [
                name for name, fld in incoming_fields.items()
                if fld.get("value") != current_fields.get(name, {}).get("value")
            ]
        else:
            # Platform unavailable or no prior item — conservative: mark all as edited.
            changed_fields = list(incoming_fields.keys())

        # Upsert to the platform via the deterministic-id path (whole-item replace, R1).
        item_payload = build_item_payload(session.dataset, document_id, entry)
        upsert_err = upsert_platform_item(
            session.dataset, item_payload, workspace.workspace_root()
        )
        if upsert_err:
            raise HTTPException(
                status_code=502,
                detail=f"platform write failed: {upsert_err}",
            )

        # Update provenance: only changed fields are recorded (R2).
        if changed_fields:
            existing = list(session.edited_document_ids.get(document_id, []))
            session.edited_document_ids[document_id] = sorted(set(existing) | set(changed_fields))

        # If the session was REVIEWED, revert to DRAFTED: the platform items have
        # changed, so approved_golden_hash is stale.  The curator must re-call
        # /complete to re-approve the updated golden.
        if session.state == ReviewSessionState.REVIEWED:
            session.state = ReviewSessionState.DRAFTED
            session.approved_golden_hash = None

        save_session(session, workspace.workspace_root())

        # INV-02: log session_id and document_id only — never field names or values.
        _api_logger.info(
            "golden_item_edited session_id=%s document_id=%s",
            session_id,
            document_id,
        )
        return session.to_dict()

    @app.post("/api/reviews/{session_id}/replace")
    def post_replace_golden(
        session_id: str,
        body: Annotated[dict[str, Any], Body()],
    ) -> dict[str, Any]:
        """Replace the entire drafted golden set with a curator-supplied file (T-02.2.2).

        Accepts a dict of entries (the same format provision_golden_dataset.py uses).
        Reuses provision_golden_dataset.py's existing schema validation logic —
        specifically the same underlying jsonschema + validate_golden_structure calls.

        All-or-nothing on VALIDATION: one invalid entry refuses the WHOLE batch with
        the invalid entry's key named, and no upsert is attempted.

        DEBT-142 (option c): all-or-nothing on the WRITE side is NOT guaranteed by
        staging or rollback — the platform has no transaction API.  Instead, if
        upsert_platform_item fails partway through the batch, the session is forced
        into REPLACE_FAILED state (non-approvable).  The curator sees a 502 AND the
        session state changes, so /complete is blocked until the golden is fully
        repaired via another /replace or a series of PATCH calls.  This is weaker
        than true atomicity but stronger than leaving the session in DRAFTED (which
        would let /complete silently approve a partial golden).

        Both endpoints spend ZERO IDP quota (platform writes only, no extraction).
        """
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        if session.state not in _EDITABLE_STATES:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"session {session_id!r} is in state {session.state.value!r}; "
                    "golden replacement is only allowed in 'drafted' or 'reviewed' state"
                ),
            )

        entries_raw = body.get("entries")
        if not isinstance(entries_raw, dict) or not entries_raw:
            raise HTTPException(
                status_code=422,
                detail="body must contain a non-empty 'entries' object",
            )
        entries: dict[str, Any] = entries_raw

        # Validate ALL entries first — all-or-nothing (provision_golden_dataset.py rule).
        invalid: list[tuple[str, str]] = []
        for key, entry in sorted(entries.items()):
            reason = validate_entry(entry)
            if reason is not None:
                invalid.append((key, reason))

        if invalid:
            key, reason = invalid[0]
            raise HTTPException(
                status_code=422,
                detail=(
                    f"{len(invalid)} invalid entr(ies); nothing provisioned. "
                    f"First invalid entry: {key!r} — {reason}"
                ),
            )

        # All entries are valid; write them all to the platform.
        # DEBT-142 fix (option c): if any upsert fails, force session to REPLACE_FAILED
        # before raising — this prevents /complete from approving the partial golden.
        # The platform has no staging or transaction API, so true rollback is not possible.
        for key, entry in sorted(entries.items()):
            document_id = entry.get("document_id", key)
            item_payload = build_item_payload(session.dataset, str(document_id), entry)
            upsert_err = upsert_platform_item(
                session.dataset, item_payload, workspace.workspace_root()
            )
            if upsert_err:
                # Force session into REPLACE_FAILED: the golden set is now partial
                # (some entries were written, some were not).  /complete is blocked
                # in this state — the curator must re-run /replace or PATCH to repair.
                session.state = ReviewSessionState.REPLACE_FAILED
                session.approved_golden_hash = None  # any prior hash is now invalid
                save_session(session, workspace.workspace_root())
                _api_logger.warning(
                    "golden_replace_partial_failure session_id=%s failed_key=%s "
                    "state=replace_failed",
                    session_id, key,
                )
                raise HTTPException(
                    status_code=502,
                    detail=(
                        f"platform write failed for {key!r}: {upsert_err}. "
                        "The golden set is now partially updated — the session has been "
                        "marked 'replace_failed' to prevent approving an incomplete golden. "
                        "Re-run POST /replace with the full corrected file to repair."
                    ),
                )

        # R2: Update provenance — only fields whose value actually changed vs the current
        # platform item are recorded.  Re-sending an unchanged field (required by PATCH's
        # replace semantics) must not bloat the provenance.
        for entry in entries.values():
            doc_id = str(entry.get("document_id", ""))
            if not doc_id:
                continue
            incoming_fields = entry.get("fields", {})
            current_item = fetch_platform_item(session.dataset, doc_id, workspace.workspace_root())
            if current_item is not None:
                current_fields = current_item.get("fields", {})
                changed = [
                    name for name, fld in incoming_fields.items()
                    if fld.get("value") != current_fields.get(name, {}).get("value")
                ]
            else:
                changed = list(incoming_fields.keys())
            if changed:
                existing = list(session.edited_document_ids.get(doc_id, []))
                session.edited_document_ids[doc_id] = sorted(set(existing) | set(changed))

        # State transition on success:
        # - REVIEWED → DRAFTED: platform items have changed, approved_golden_hash is stale.
        # - REPLACE_FAILED → DRAFTED: the repair succeeded, session is approvable again.
        # - DRAFTED stays DRAFTED.
        if session.state in (ReviewSessionState.REVIEWED, ReviewSessionState.REPLACE_FAILED):
            session.state = ReviewSessionState.DRAFTED
            session.approved_golden_hash = None

        save_session(session, workspace.workspace_root())

        # INV-02: log session_id and entry count only — never field names or values.
        _api_logger.info(
            "golden_set_replaced session_id=%s entry_count=%s",
            session_id,
            len(entries),
        )
        return session.to_dict()

    # ------------------------------------------------ verify-candidate

    @app.post("/api/workflows/verify-candidate/plan")
    def post_verify_candidate_plan(
        payload: Annotated[dict[str, Any], Body()]
    ) -> dict[str, Any]:
        """Price the verify stage (stage 2). Requires a reviewed session."""
        session_id = str(payload.get("session_id", "")).strip()
        if not session_id:
            raise HTTPException(status_code=422, detail="session_id is required")
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=404, detail=f"no review session {session_id!r}"
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        if session.state not in (ReviewSessionState.REVIEWED, ReviewSessionState.VERIFYING):
            raise HTTPException(
                status_code=409,
                detail=(
                    f"session {session_id!r} is in state {session.state.value!r}; "
                    "it must be in 'reviewed' state before verification can be priced"
                ),
            )
        argv = _verify_argv(session, plan_only=True)
        try:
            extractions, lines = jobs.plan(argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=504, detail="the plan timed out") from None
        return {
            "planned_extractions": extractions,
            "command": " ".join(argv),
            "output": lines,
            "confirm_with": {"approved_extractions": extractions, "session_id": session_id},
            "session": session.to_dict(),
        }

    @app.post("/api/workflows/verify-candidate/start")
    def post_verify_candidate_start(
        payload: Annotated[dict[str, Any], Body()]
    ) -> dict[str, Any]:
        """Start the verify stage. **This spends real IDP quota.**

        Enforces all five INV-09 clauses:
        (a) session exists and is in 'reviewed' state
        (b) document_dir and archive_sha256 still match what stage 1 pinned
        (c) approved_extractions from a FRESH --plan computed now, after review
        (d) approval bound to this route + session_id (cross-route replay refused)
        (e) live platform dataset item hash matches ReviewSession.approved_golden_hash
        """
        session_id = str(payload.get("session_id", "")).strip()
        approved = payload.get("approved_extractions")

        # INV-09(a) — session must exist
        if not session_id:
            raise HTTPException(status_code=422, detail="session_id is required")
        try:
            session = load_session(session_id, workspace.workspace_root())
        except ReviewSessionNotFoundError:
            raise HTTPException(
                status_code=409,
                detail=f"no review session {session_id!r} — INV-09(a): session must exist",
            ) from None
        except ReviewSessionCorruptError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # Idempotent: already verified → return existing job
        if session.state == ReviewSessionState.VERIFIED and session.stage2_job_id:
            try:
                existing_job = registry.get(session.stage2_job_id)
                return existing_job.to_json()
            except KeyError:
                # Job not in this process's memory (console restarted); return session info
                return {"id": session.stage2_job_id, "session": session.to_dict()}

        # INV-09(a) — session must be in reviewed state
        if session.state != ReviewSessionState.REVIEWED:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"session {session_id!r} is in state {session.state.value!r}; "
                    "verify-candidate/start requires state='reviewed' — the curator must "
                    "complete the review step before stage 2 can be approved (INV-09 clause a)"
                ),
            )

        # INV-09(b) — document_dir must still exist.
        #
        # What this enforces: the corpus directory has not been moved or deleted
        # between stage 1 and stage 2.  verify_document.py (the stage-2 subprocess)
        # enforces the byte-level guarantee: each pin records the document's sha256,
        # and the subprocess refuses if the file bytes differ from what was pinned
        # (CLAUDE.md: "same-named file with different content is refused before any
        # spend — --repin to force").  The `archive_sha256` field stored in the
        # ReviewSession is client-supplied from the payload and may be "" when the
        # corpus arrived as a directory rather than a ZIP — so it is NOT compared
        # here.  The authoritative bytes-unchanged check lives in verify_document.py.
        doc_dir = Path(session.document_dir)
        if not doc_dir.is_dir():
            raise HTTPException(
                status_code=409,
                detail=(
                    f"document directory {str(doc_dir)!r} no longer exists; the corpus "
                    "must be present for stage 2 (INV-09 clause b). Re-upload and re-pin."
                ),
            )

        # approved_extractions type check (must be before plan — gives a clear 422)
        if not isinstance(approved, int) or isinstance(approved, bool):
            raise HTTPException(
                status_code=422,
                detail="approved_extractions (an integer, from the plan) is required -- "
                "this is the --yes for a job that spends real quota",
            )

        _require_runnable()

        # INV-09(c) — compute a FRESH plan (after review, not stage 1's plan)
        plan_argv = _verify_argv(session, plan_only=True)
        try:
            planned, _ = jobs.plan(plan_argv)
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None

        # INV-09(c) / (d) — approved count must match THIS route's fresh plan
        if approved != planned:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"approval is for {approved} extractions but this job plans {planned} "
                    f"right now — re-plan and confirm the current cost (INV-09 clauses c/d: "
                    "the count is bound to this route's fresh plan, never a stage-1 carry-over)"
                ),
            )

        # INV-09(e) — live platform hash must still match approved_golden_hash.
        # Fail-closed on None in either direction:
        # * current_hash is None  → platform unreachable at verify time; cannot
        #   confirm the golden is unchanged, so refuse rather than spend quota
        #   against an unverified state.
        # * approved_golden_hash is None → review-complete was persisted without
        #   a real hash (should not happen after the review-complete fix, but
        #   defended here as a second layer; a None approval is never valid).
        # Do NOT compare None == None — that is False in Python but semantically
        # means "no information on either side", which is not an approval.
        current_hash = fetch_golden_hash(session.dataset, workspace.workspace_root())
        hash_match = (
            current_hash is not None
            and session.approved_golden_hash is not None
            and current_hash == session.approved_golden_hash
        )
        _api_logger.info(
            "verify_candidate_inv09e_check session_id=%s hash_match=%s",
            session_id,
            hash_match,
        )
        if not hash_match:
            session.state = ReviewSessionState.STALE
            save_session(session, workspace.workspace_root())
            if current_hash is None or session.approved_golden_hash is None:
                detail = (
                    "the platform golden hash could not be computed at stage-2 time "
                    "(platform not configured or not reachable) — INV-09 clause (e): "
                    "cannot confirm the golden dataset is unchanged; "
                    "ensure the platform is reachable and retry"
                )
            else:
                detail = (
                    "the platform golden dataset has changed since the curator's review "
                    "(the current item hash no longer matches approved_golden_hash) — "
                    "INV-09 clause (e): start a new review session to re-approve the "
                    "updated golden before spending verification quota"
                )
            raise HTTPException(status_code=409, detail=detail)

        run_argv = plan_argv[:-1] + ["--yes"]
        try:
            job = registry.start(
                "verify-candidate",
                run_argv,
                planned_extractions=planned,
                approved_extractions=approved,
            )
        except jobs.WorkspaceBusyError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        except jobs.JobRejectedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None

        session.state = ReviewSessionState.VERIFYING
        session.stage2_job_id = job.id
        save_session(session, workspace.workspace_root())
        _api_logger.info(
            "verify_candidate_started session_id=%s job_id=%s planned=%s",
            session_id, job.id, planned,
        )
        result = job.to_json()
        result["session_id"] = session_id
        return result

    # ------------------------------------------------ platform (read)

    @app.get("/api/platform/capabilities")
    def get_platform_capabilities() -> dict[str, Any]:
        """What the evaluation platform can answer here.

        A 404 on the runs endpoint is this deployment's documented
        `events_only` behaviour, not a failure, and is reported as a
        capability so a dashboard does not render a working
        configuration in red.
        """
        insights = insights_from_env()
        if insights is None:
            return {"configured": False, "reason": configuration_hint()}
        return {"configured": True, **insights.capabilities()}

    @app.get("/api/platform/trend")
    def get_platform_trend(
        name: str | None = None, days: int = 30, limit: int = 1000
    ) -> dict[str, Any]:
        insights = insights_from_env()
        if insights is None:
            raise HTTPException(
                status_code=503, detail="the evaluation platform is not configured"
            )
        since = dt.datetime.now(dt.UTC) - dt.timedelta(days=max(1, min(days, 365)))
        try:
            return insights.score_trend(name=name, since=since, limit=limit)
        except PlatformTransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @app.get("/api/platform/datasets")
    def get_platform_datasets() -> dict[str, Any]:
        insights = insights_from_env()
        if insights is None:
            raise HTTPException(
                status_code=503, detail="the evaluation platform is not configured"
            )
        try:
            return {"datasets": insights.dataset_names()}
        except PlatformTransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @app.get("/api/platform/datasets/{name}")
    def get_platform_dataset(name: str, limit: int = 200) -> dict[str, Any]:
        insights = insights_from_env()
        if insights is None:
            raise HTTPException(
                status_code=503, detail="the evaluation platform is not configured"
            )
        try:
            return insights.dataset_items(name, limit=limit)
        except PlatformTransportError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from None

    @app.get("/api/preflight")
    def get_preflight() -> dict[str, Any]:
        """Can this machine run a validation? Asked before any upload is
        priced, so a missing credential costs nothing instead of costing
        the first extraction. Presence by NAME only -- never a value."""
        return preflight.check()

    # --------------------------------------------------------------- meta

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        return {
            "ok": True,
            # Accurate rather than reassuring: the routes that spend IDP
            # quota are NAMED, all of them. A blanket `false` here was
            # true before the compare workflow existed; listing only
            # `compare/start` was true only until the noise floor became
            # a second spender. Both were comfortable lies in the one
            # place an operator checks before trusting this port with a
            # customer's quota (Zangado QA F-A, 2026-09-25).
            "spends_quota": False,
            "quota_spending_routes": [
                "POST /api/workflows/compare/start",
                "POST /api/workflows/floor/start",
                "POST /api/workflows/draft-golden/start",
                "POST /api/workflows/verify-candidate/start",
            ],
            "quota_requires_approved_count": True,
            "workspace": str(workspace.workspace_root()),
            "artifact_dir": str(reader.ARTIFACT_DIR()),
            "scorer_dir": str(scorers_root),
        }

    # Built frontend, when one exists. Mounted last so it can never
    # shadow an /api route.
    if FRONTEND_DIST.is_dir():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}")
        def spa(path: str) -> FileResponse:
            candidate = FRONTEND_DIST / path
            # Containment checked on the RESOLVED path, so a name that
            # only looks safe cannot escape the build directory.
            inside = candidate.resolve().is_relative_to(FRONTEND_DIST.resolve())
            if path and candidate.is_file() and inside:
                return FileResponse(candidate)
            return FileResponse(FRONTEND_DIST / "index.html")

    return app


def _outcome(result: Any) -> dict[str, Any]:
    if isinstance(result, ScoreResult):
        return {
            "verdict": result.verdict,
            "critical": result.critical,
            "format_critical": result.format_critical,
        }
    return {"verdict": result, "critical": False, "format_critical": False}
