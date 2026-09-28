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
import json
import subprocess
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
from idp_regression.orchestration.version_discovery import (
    AnchorNotSemverError,
    discover_versions,
)
from idp_regression.platform.errors import TransportError as PlatformTransportError
from idp_regression.platform.insights import configuration_hint, insights_from_env
from idp_regression.ui import jobs, preflight, reader, uploads, workspace

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
