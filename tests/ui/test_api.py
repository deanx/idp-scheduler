"""The HTTP surface.

The invariant this file exists for is the first test: **no endpoint can
spend IDP quota or write to the evaluation platform.** The console reads
local artifacts and authors gate configuration; the commands that cost
money are printed for an operator to run at a terminal, where the `--yes`
and the extraction count are already in front of them. A localhost-bound,
unauthenticated port is the wrong place for a button that bills a
customer's org.
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

fastapi = pytest.importorskip("fastapi", reason="the `ui` extra is not installed")
from fastapi.testclient import TestClient  # noqa: E402

from idp_regression.orchestration.run_artifact import artifact_envelope  # noqa: E402
from idp_regression.ui import workspace  # noqa: E402
from idp_regression.ui.api import create_app  # noqa: E402


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A console whose workspace IS `tmp_path`.

    The workspace is resolved once at startup in production (a server's
    working directory cannot change under it), so a test that only
    `chdir`s would leave the console reading the real repo -- which is
    how the cwd bug in `workspace.py` hid in the first place.
    """
    monkeypatch.chdir(tmp_path)
    previous = workspace.workspace_root()
    workspace.set_workspace(tmp_path)
    try:
        yield TestClient(create_app(scorer_dir=tmp_path / "scorers"))
    finally:
        workspace.set_workspace(previous)


def post_zip(client: TestClient, entries: dict[str, bytes]) -> dict[str, Any]:
    """Upload an archive built from `entries` and return the report."""
    payload = make_zip(entries)
    body: dict[str, Any] = client.post(
        "/api/uploads", files={"file": ("corpus.zip", payload, "application/zip")}
    ).json()
    return body


def make_zip(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in entries.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


class TestQuotaBoundary:
    #: EVERY route allowed to spend IDP quota (user decision,
    #: 2026-09-25). Everything else prints the command instead. Kept as a
    #: named constant so widening it is a visible edit -- and asserted
    #: against `/api/health`, so the set and what the console TELLS an
    #: operator about it cannot drift apart (Zangado QA F-A).
    #:
    #: S-02.1 (2026-09-28): two new routes added for the two-stage
    #: golden-review workflow (T-02.1.8). The set grows from 2 to 4.
    SPENDS_QUOTA = {
        ("/api/workflows/compare/start", ("POST",)),
        # The noise floor spends quota too -- it reads a sample twice.
        # Guarded on identical terms (approved count + server-side
        # preflight); listed so that stays a deliberate choice.
        ("/api/workflows/floor/start", ("POST",)),
        # Stage 1 of the two-stage golden-review workflow (ADR-0008):
        # pin every document at the trusted version (N extractions).
        ("/api/workflows/draft-golden/start", ("POST",)),
        # Stage 2 of the two-stage golden-review workflow (ADR-0008):
        # verify every pinned document at the candidate version (N extractions).
        ("/api/workflows/verify-candidate/start", ("POST",)),
    }

    def test_only_the_named_routes_can_start_an_extraction(self, client: TestClient) -> None:
        """Enumerated rather than asserted per-endpoint, so a future
        route has to be added to this list deliberately."""
        routes: list[tuple[str, set[str]]] = [
            (getattr(route, "path", ""), getattr(route, "methods", set()))
            for route in create_app().routes
        ]
        paths = {
            (path, tuple(sorted(methods))) for path, methods in routes if path.startswith("/api")
        }
        allowed = {
            ("/api/runs", ("GET",)),
            ("/api/runs/{run_id}", ("GET",)),
            ("/api/noise-floors", ("GET",)),
            ("/api/noise-floors/{name}", ("GET",)),
            ("/api/blind-spots", ("GET",)),
            ("/api/pins", ("GET",)),
            ("/api/scorers", ("GET",)),
            ("/api/scorers/validate", ("POST",)),
            ("/api/scorers/preview", ("POST",)),
            ("/api/scorers/{name}", ("DELETE",)),
            ("/api/scorers/{name}", ("PUT",)),
            ("/api/uploads", ("POST",)),
            ("/api/uploads/{upload_id}", ("GET",)),
            ("/api/uploads/{upload_id}/unpack", ("POST",)),
            ("/api/health", ("GET",)),
            ("/api/preflight", ("GET",)),
            ("/api/workflows/floor/plan", ("POST",)),
            ("/api/workflows/floor/start", ("POST",)),
            ("/api/actions/versions", ("POST",)),
            ("/api/workflows/compare/plan", ("POST",)),
            ("/api/workflows/compare/start", ("POST",)),
            ("/api/jobs", ("GET",)),
            ("/api/jobs-busy", ("GET",)),
            ("/api/jobs/{job_id}", ("GET",)),
            ("/api/jobs/{job_id}/cancel", ("POST",)),
            ("/api/platform/capabilities", ("GET",)),
            ("/api/platform/trend", ("GET",)),
            ("/api/platform/datasets", ("GET",)),
            ("/api/platform/datasets/{name}", ("GET",)),
            # S-02.1 (2026-09-28) — two-stage golden-review workflow
            ("/api/workflows/draft-golden/plan", ("POST",)),
            ("/api/workflows/draft-golden/start", ("POST",)),
            ("/api/workflows/verify-candidate/plan", ("POST",)),
            ("/api/workflows/verify-candidate/start", ("POST",)),
            ("/api/reviews", ("GET",)),
            ("/api/reviews/{session_id}", ("GET",)),
            ("/api/reviews/{session_id}/complete", ("POST",)),
        }
        assert paths == allowed, (
            "a new route appeared. Confirm whether it spends IDP quota or writes to the "
            "evaluation platform, then add it here (and to SPENDS_QUOTA if it does)."
        )
        assert paths >= self.SPENDS_QUOTA

    def test_health_names_the_quota_spending_route_rather_than_denying_one_exists(
        self, client: TestClient
    ) -> None:
        """A blanket `spends_quota: false` was true before the compare
        workflow existed. Leaving it would be a comfortable lie in the
        one place an operator might check."""
        body = client.get("/api/health").json()
        # Check all four spending routes are declared (S-02.1 added two more)
        declared = set(body["quota_spending_routes"])
        assert "POST /api/workflows/compare/start" in declared
        assert "POST /api/workflows/floor/start" in declared
        assert "POST /api/workflows/draft-golden/start" in declared
        assert "POST /api/workflows/verify-candidate/start" in declared
        assert body["quota_requires_approved_count"] is True

    def test_no_operator_facing_prose_states_a_quota_route_COUNT(
        self, client: TestClient
    ) -> None:
        """A number in prose is the thing that goes stale.

        `/api/health` said "one route" after the noise floor became a
        second spender; the OpenAPI description served at `/docs` said it
        too and survived the first fix, because nothing bound it to
        anything (Zangado QA F-A then F-E). Neither surface states a
        count any more -- `/api/health` carries the authoritative list,
        and this test keeps prose out of the business of counting.
        """
        from idp_regression.ui.api import create_app as _create_app

        description = _create_app().openapi()["info"]["description"]
        forbidden = ("one quota-spending", "the single quota", "only route that spends")
        for phrase in forbidden:
            assert phrase not in description, (
                f"the OpenAPI description states a route count ({phrase!r}); "
                "counts in prose go stale -- point at /api/health instead"
            )
        assert "/api/health" in description, (
            "if the description will not count, it must say where the real list is"
        )

    def test_health_names_every_route_the_boundary_test_knows_spends_quota(
        self, client: TestClient
    ) -> None:
        """The two must never drift. Adding a spender and forgetting the
        health payload leaves the one endpoint an operator audits denying
        that it spends -- and a green test cementing the denial, which is
        exactly what happened when the noise floor was added."""
        declared = set(client.get("/api/health").json()["quota_spending_routes"])
        known = {f"{methods[0]} {path}" for path, methods in self.SPENDS_QUOTA}
        assert declared == known

    def test_version_discovery_declares_that_it_spends_no_extraction_quota(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An existence probe is an HTTP call, not an extraction. The
        response says so, because a version picker that quietly cost
        money per click would be found out on the invoice."""
        monkeypatch.delenv("IDP_CLIENT_ID", raising=False)
        response = client.post(
            "/api/actions/versions", json={"org": "o", "action": "a", "anchor": "1.0.0"}
        )
        # Without credentials it is a 503, never a silent attempt.
        assert response.status_code == 503
        assert "IDP_CLIENT_ID" in response.json()["detail"]

    def test_the_classifier_field_reaches_build_compare_argv(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`ValidateZipPage`'s scorer picker sends `classifier` in the
        compare request; `_compare_argv` must forward it, not silently
        drop it, to `jobs.build_compare_argv`."""
        from idp_regression.ui import jobs

        captured: dict[str, Any] = {}
        real = jobs.build_compare_argv

        def _spy(**kwargs: Any) -> list[str]:
            captured.update(kwargs)
            return real(**kwargs)

        monkeypatch.setattr(jobs, "build_compare_argv", _spy)

        client.post(
            "/api/workflows/compare/plan",
            json={
                "document_dir": ".",
                "dataset": "d",
                "org": "o",
                "action": "a",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
                "classifier": "my-pinned-rule",
            },
        )

        assert captured["classifier"] == "my-pinned-rule"

    def test_starting_a_job_without_an_approved_count_is_refused(
        self, client: TestClient
    ) -> None:
        """`approved_extractions` IS the `--yes`. Its absence must never
        default to "go ahead"."""
        response = client.post(
            "/api/workflows/compare/start",
            json={
                "document_dir": ".",
                "dataset": "d",
                "org": "o",
                "action": "a",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
            },
        )
        assert response.status_code == 422
        assert "approved_extractions" in response.json()["detail"]

    @pytest.mark.parametrize("approved", [True, "40", 40.0, None])
    def test_an_approval_that_is_not_an_integer_is_refused(
        self, client: TestClient, approved: object
    ) -> None:
        """`True` is the interesting one: in Python it equals 1, so a
        bool slipping through could approve a one-extraction cost for an
        arbitrary job."""
        response = client.post(
            "/api/workflows/compare/start",
            json={
                "document_dir": ".",
                "dataset": "d",
                "org": "o",
                "action": "a",
                "trusted_version": "1.0.0",
                "candidate_version": "2.0.0",
                "approved_extractions": approved,
            },
        )
        assert response.status_code == 422

    def test_cors_is_off_unless_asked_for(self, tmp_path: Path) -> None:
        """With CORS on, any page the browser loads could read a run
        artifact -- and those carry the extracted financial values."""
        app = create_app(scorer_dir=tmp_path)
        assert not any(
            "CORS" in type(m.cls).__name__ or "CORS" in str(m.cls)
            for m in app.user_middleware
        )


class TestPlatformReads:
    def test_an_unconfigured_platform_is_reported_not_an_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(key, raising=False)
        body = client.get("/api/platform/capabilities").json()
        assert body["configured"] is False
        assert body["reason"]

    def test_platform_pages_503_when_unconfigured_rather_than_showing_an_empty_chart(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for key in ("LANGFUSE_HOST", "LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY"):
            monkeypatch.delenv(key, raising=False)
        assert client.get("/api/platform/trend").status_code == 503
        assert client.get("/api/platform/datasets").status_code == 503


class TestReads:
    def test_empty_state_is_a_200_with_nothing_in_it(self, client: TestClient) -> None:
        assert client.get("/api/runs").json() == {"runs": []}
        assert client.get("/api/pins").json() == {"pins": []}
        assert client.get("/api/noise-floors").json() == {"noise_floors": []}
        assert client.get("/api/blind-spots").json() == {"calibrations": []}

    def test_an_unknown_run_is_a_404_not_a_500(self, client: TestClient) -> None:
        assert client.get("/api/runs/nope").status_code == 404

    def test_an_unknown_baseline_is_a_404(self, client: TestClient, tmp_path: Path) -> None:
        artifacts = tmp_path / ".idp-regression-run-artifacts"
        artifacts.mkdir()
        (artifacts / "r1.json").write_text(
            json.dumps(artifact_envelope(
                "r1", {"a.pdf": {"total": {"verdict": "match", "critical": False}}},
                status="complete",
            )),
            encoding="utf-8",
        )
        assert client.get("/api/runs/r1?baseline=absent").status_code == 404


class TestScorers:
    SPEC = {
        "name": "confidence-floor",
        "description": "Fail a match IDP was not confident about.",
        "base": "regression",
        "rules": [
            {"when": {"confidence_below": 0.8, "verdict_is": ["match"]},
             "then": {"verdict": "wrong_value", "critical": True}}
        ],
    }

    def test_the_shipped_classifiers_are_listed_with_the_vocabulary(
        self, client: TestClient
    ) -> None:
        body = client.get("/api/scorers").json()
        assert {s["name"] for s in body["scorers"]} == {"regression", "pinned-file"}
        assert [s["name"] for s in body["scorers"] if s["is_default"]] == ["regression"]
        assert "match" not in body["vocabulary"]["actionable_verdicts"]

    def test_validate_save_list_delete(self, client: TestClient) -> None:
        assert client.post("/api/scorers/validate", json=self.SPEC).json() == {
            "valid": True, "errors": [], "monotone": True, "spec": self.SPEC
        }
        assert client.put("/api/scorers/confidence-floor", json=self.SPEC).status_code == 200
        names = {s["name"] for s in client.get("/api/scorers").json()["scorers"]}
        assert names == {"regression", "pinned-file", "confidence-floor"}
        assert client.delete("/api/scorers/confidence-floor").status_code == 200
        assert client.delete("/api/scorers/confidence-floor").status_code == 404

    def test_validate_reports_the_refusal_instead_of_raising(self, client: TestClient) -> None:
        """The form calls this on every keystroke; a 422 per character
        would be noise. The refusal is the payload."""
        body = client.post("/api/scorers/validate", json={**self.SPEC, "name": "Not Kebab"}).json()
        assert body["valid"] is False
        assert "kebab-case" in body["errors"][0]

    def test_saving_a_relaxing_spec_is_refused(self, client: TestClient) -> None:
        relaxing = {
            **self.SPEC,
            "rules": [{"when": {"kind": "field"}, "then": {"verdict": "match"}}],
        }
        response = client.put("/api/scorers/confidence-floor", json=relaxing)
        assert response.status_code == 422
        assert "a verdict the gate fails on" in response.json()["detail"]

    def test_saving_a_spec_that_rewrites_a_failure_into_new_field_is_refused(
        self, client: TestClient
    ) -> None:
        """Wave C S-01.1 re-stamp F-1, at the console: the unauthenticated
        loopback port must refuse a spec that would turn a FAIL into a PASS."""
        relaxing = {
            **self.SPEC,
            "rules": [{"when": {"verdict_is": ["wrong_value", "missing"]},
                       "then": {"verdict": "new_field"}}],
        }
        response = client.put("/api/scorers/confidence-floor", json=relaxing)
        assert response.status_code == 422

    def test_the_editor_is_offered_only_verdicts_a_rule_can_use(self, client: TestClient) -> None:
        vocabulary = client.get("/api/scorers").json()["vocabulary"]
        assert set(vocabulary["actionable_verdicts"]) == {"missing", "wrong_value"}
        assert "new_table" not in vocabulary["verdicts"]

    def test_a_body_that_names_a_different_scorer_is_refused(self, client: TestClient) -> None:
        assert client.put("/api/scorers/other", json=self.SPEC).status_code == 422

    def test_preview_shows_the_base_beside_the_spec(self, client: TestClient) -> None:
        body = client.post("/api/scorers/preview", json={
            "spec": self.SPEC,
            "samples": [
                {"name": "total", "field_type": "number", "expected": "1.00",
                 "actual": "1.00", "confidence": 0.4},
                {"name": "total", "field_type": "number", "expected": "1.00",
                 "actual": "1.00", "confidence": 0.99},
            ],
        }).json()
        assert body["results"][0]["base"]["verdict"] == "match"
        assert body["results"][0]["custom"] == {
            "verdict": "wrong_value", "critical": True, "format_critical": False,
        }
        assert body["results"][1]["custom"]["verdict"] == "match"

    def test_preview_refuses_an_invalid_spec_before_running_anything(
        self, client: TestClient
    ) -> None:
        response = client.post("/api/scorers/preview", json={"spec": {}, "samples": [{}]})
        assert response.status_code == 422

    def test_a_broken_spec_file_is_reported_without_hiding_the_registry(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        (tmp_path / "scorers").mkdir(parents=True, exist_ok=True)
        (tmp_path / "scorers" / "broken.json").write_text("{not json", encoding="utf-8")
        body = client.get("/api/scorers").json()
        assert body["errors"] and "broken.json" in body["errors"][0]
        assert {s["name"] for s in body["scorers"]} == {"regression", "pinned-file"}


class TestUploads:
    def test_a_clean_archive_validates_and_is_costed_before_anything_is_written(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        body = post_zip(client, {"a/inv-001.pdf": b"%PDF-1.4", "b/inv-002.pdf": b"%PDF-1.4"})
        assert body["document_count"] == 2
        assert body["cost"]["pin_and_verify"] == 4
        assert not (tmp_path / ".idp-regression-uploads" / "documents").exists(), (
            "validation must not write documents to disk"
        )

    def test_a_traversal_entry_is_rejected_and_named(self, client: TestClient) -> None:
        body = post_zip(client, {"../evil.pdf": b"%PDF", "ok.pdf": b"%PDF"})
        assert any("path traversal" in note for note in body["rejected_entries"])
        assert [d["name"] for d in body["documents"]] == ["ok.pdf"]

    def test_a_non_document_is_skipped_with_its_reason(self, client: TestClient) -> None:
        body = post_zip(client, {"notes.xlsx": b"x", "ok.pdf": b"%PDF"})
        assert any("not a document" in note for note in body["skipped"])
        assert body["document_count"] == 1

    def test_two_documents_sharing_a_basename_are_disambiguated_never_overwritten(
        self, client: TestClient
    ) -> None:
        body = post_zip(client, {"jan/a.pdf": b"%PDF-1", "feb/a.pdf": b"%PDF-2"})
        assert body["document_count"] == 2
        assert len({d["name"] for d in body["documents"]}) == 2

    def test_a_file_that_is_not_a_zip_is_a_422(self, client: TestClient) -> None:
        response = client.post(
            "/api/uploads", files={"file": ("x.zip", b"not a zip", "application/zip")}
        )
        assert response.status_code == 422
        assert "readable zip" in response.json()["detail"]

    def test_unpacking_is_a_separate_explicit_act(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        upload = post_zip(client, {"inv-001.pdf": b"%PDF-1.4"})
        body = client.post(f"/api/uploads/{upload['upload_id']}/unpack", json={}).json()
        assert body["document_count"] == 1
        written = (
            tmp_path / ".idp-regression-uploads" / "documents"
            / upload["upload_id"] / "inv-001.pdf"
        )
        assert written.is_file()
        assert oct(written.stat().st_mode)[-3:] == "600", (
            "a customer document on local disk is owner-only"
        )

    def test_unpacking_prints_the_commands_rather_than_running_them(
        self, client: TestClient
    ) -> None:
        upload = post_zip(client, {"inv-001.pdf": b"%PDF-1.4"})
        body = client.post(f"/api/uploads/{upload['upload_id']}/unpack", json={}).json()
        commands = " ".join(step["command"] for step in body["next_commands"])
        assert "noise_floor.py" in commands and "pin_document.py" in commands
        assert body["document_dir"] in commands

    @pytest.mark.parametrize("bad", ["deadbeef", "a" * 31, "a" * 33, "zz" * 16])
    def test_an_id_that_is_not_an_upload_id_is_a_404(self, client: TestClient, bad: str) -> None:
        """The stored name is a generated hex id, never the client's
        filename, and anything that is not one is refused before a path
        is built from it."""
        assert client.get(f"/api/uploads/{bad}").status_code == 404

    @pytest.mark.parametrize("bad", ["..", "../etc/passwd", "a/b", ""])
    def test_a_traversing_id_never_reaches_a_filesystem_path(
        self, bad: str, tmp_path: Path
    ) -> None:
        """Asserted below HTTP: an HTTP client normalises `..` out of a
        URL before it is sent, so the route test above cannot reach this
        guard. The guard is what matters, so it is tested where it is."""
        from idp_regression.ui import uploads

        with pytest.raises(FileNotFoundError):
            uploads._archive_path(bad, tmp_path)

    def test_the_spa_fallback_cannot_serve_a_file_outside_the_build(
        self, client: TestClient
    ) -> None:
        """The catch-all resolves the candidate and checks containment
        before serving it; a traversal falls through to index.html."""
        response = client.get("/..%2f..%2f..%2fetc%2fpasswd")
        assert response.status_code in (200, 404)
        assert "root:" not in response.text
