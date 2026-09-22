"""ADR-0004 A10 companion item (2026-09-22): submit-404 disambiguation.

The executions URL (`_executions_base_url`) is composed entirely from
`(org, action, version)` -- a 404 on submit means exactly one of those
three parameters does not resolve on this plane. This is a standalone
file, NOT an edit to `tests/adapter/test_idp_client.py` (out of scope
this round -- another agent owns that file and `adapter/normalize.py`
concurrently).

The message must name the three PARAMETER NAMES ("org", "action",
"version") and never the URL, the org value, or the token (ADR-0002's
rule, INV-02).
"""

from __future__ import annotations

from typing import Any

import pytest

from idp_regression.adapter import transport
from idp_regression.adapter.errors import IDPSubmitError
from idp_regression.adapter.idp_client import MuleSoftIDPAdapter

_ORG_ID = "org-e10ae12a-should-never-be-logged"
_ACTION_ID = "action-078ca317"
_VERSION = "1.0.0"


def _adapter(
    monkeypatch: pytest.MonkeyPatch, *, submit_result: tuple[int, dict[str, Any]]
) -> MuleSoftIDPAdapter:
    monkeypatch.setattr(
        transport,
        "post_json",
        lambda *a, **kw: (200, {"access_token": "tok-1", "expires_in": 300}),  # noqa: ARG005
    )
    monkeypatch.setattr(
        transport,
        "post_multipart_file",
        lambda *a, **kw: submit_result,  # noqa: ARG005
    )
    return MuleSoftIDPAdapter(
        client_id="cid",
        client_secret="csecret",
        region="us-east-2",
        org_id=_ORG_ID,
        terminal_statuses={"SUCCEEDED"},
        success_statuses={"SUCCEEDED"},
    )


def test_submit_404_names_org_action_version_not_the_url_or_org_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = _adapter(monkeypatch, submit_result=(404, {}))

    with pytest.raises(IDPSubmitError) as excinfo:
        adapter.extract("/documents/doc-1", _ACTION_ID, _VERSION)

    message = str(excinfo.value)
    assert "org" in message
    assert "action" in message
    assert "version" in message
    # ADR-0002's rule stands: never the URL, the org value, or the token.
    assert _ORG_ID not in message
    assert "idp-rt." not in message
    assert "/organizations/" not in message
    assert "tok-1" not in message


def test_submit_404_message_differs_from_the_generic_rejected_status_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation pin for the fix itself: deleting the `status == 404`
    branch makes 404 fall through to the generic
    `f"IDP submit call was rejected (status={status})"` message, which
    does NOT name org/action/version -- this must go RED if that branch
    is removed."""
    adapter = _adapter(monkeypatch, submit_result=(404, {}))

    with pytest.raises(IDPSubmitError) as excinfo:
        adapter.extract("/documents/doc-1", _ACTION_ID, _VERSION)

    assert "status=404" not in str(excinfo.value)


def test_a_different_4xx_status_still_uses_the_generic_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The 404 branch must not swallow every non-2xx status -- a 400
    still gets the ordinary status-carrying message."""
    adapter = _adapter(monkeypatch, submit_result=(400, {}))

    with pytest.raises(IDPSubmitError) as excinfo:
        adapter.extract("/documents/doc-1", _ACTION_ID, _VERSION)

    assert "status=400" in str(excinfo.value)
