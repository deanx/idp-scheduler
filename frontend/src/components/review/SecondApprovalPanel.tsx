/**
 * SecondApprovalPanel — T-02.3.4 (AC5)
 *
 * Renders the stage-2 (verify-candidate) approval gate. Mirrors the exact
 * approved-extractions-echo UX already used by compare/start and floor/start:
 * the curator must type back the EXACT planned_extractions count before the
 * start button is enabled. A stale plan from stage 1 is never accepted —
 * the count is from a fresh verify-candidate/plan call made AFTER review.
 *
 * The panel is only shown when the session is in "reviewed" state. The
 * server enforces INV-09 clauses (a)-(e); this UI is the courtesy layer.
 */

import { useState } from "react";
import type { VerifyPlan } from "../../api";

export interface SecondApprovalPanelProps {
  plan: VerifyPlan | null;
  onPlan: () => void;
  onStart: (sessionId: string, approvedExtractions: number) => void;
  busy: boolean;
  blocked: boolean;
}

export function SecondApprovalPanel({
  plan,
  onPlan,
  onStart,
  busy,
  blocked,
}: SecondApprovalPanelProps) {
  const [echo, setEcho] = useState("");

  const planned = plan?.planned_extractions ?? 0;
  const sessionId = plan?.confirm_with.session_id ?? "";
  // The echo must match exactly as a number string (trimmed).
  const confirmed = echo.trim() !== "" && Number(echo.trim()) === planned;

  return (
    <div className="panel warn">
      <h3 style={{ marginTop: 0 }}>Stage 2 — Verify candidate version</h3>
      <p className="small muted" style={{ marginTop: 0 }}>
        You have reviewed the drafted golden dataset. This second approval prices and
        starts the verification run — a separate, independent spend from stage 1.
        The cost is computed from a fresh plan taken <strong>now</strong>, after your
        review, and must be confirmed by typing the exact count back.
      </p>

      {!plan ? (
        <button
          disabled={busy || blocked}
          onClick={onPlan}
        >
          {busy ? "Planning…" : "Price stage 2 (spends nothing)"}
        </button>
      ) : (
        <>
          <p>
            <strong>
              Stage 2 will spend {planned} IDP extraction
              {planned !== 1 ? "s" : ""} against a live org.
            </strong>
          </p>
          <pre className="command">{plan.command}</pre>
          {plan.output.length > 0 && (
            <details>
              <summary className="small muted">Plan output</summary>
              <pre className="command">{plan.output.join("\n")}</pre>
            </details>
          )}
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="verify-echo">
              Type <strong>{planned}</strong> to confirm this spend:
            </label>
            <input
              id="verify-echo"
              type="text"
              value={echo}
              aria-label={`confirm ${planned} extractions`}
              placeholder={String(planned)}
              style={{ width: 120 }}
              onChange={(e) => setEcho(e.target.value)}
            />
          </div>
          <div className="row" style={{ marginTop: 10 }}>
            <button
              className="primary"
              disabled={!confirmed || busy || blocked}
              onClick={() => {
                if (confirmed) onStart(sessionId, planned);
              }}
            >
              {busy ? "Starting…" : `Run stage 2 — spend ${planned}`}
            </button>
            <button
              disabled={busy}
              onClick={() => {
                setEcho("");
                onPlan();
              }}
            >
              Re-plan
            </button>
          </div>
        </>
      )}
    </div>
  );
}
