/**
 * ReviewDetailPage — #/validate/review/<session_id>
 *
 * The main review screen (T-02.3.2 / T-02.3.3 / T-02.3.4 / AC2–AC6).
 *
 * State machine (mirrors ReviewSessionState from the server):
 *   drafted        → review UI + complete-review button
 *   replace_failed → same as drafted + warning about partial write
 *   reviewed       → SecondApprovalPanel (stage 2 approval)
 *   verifying      → job panel for stage 2 (polling)
 *   verified       → verdict + link to run
 *   stale          → error: golden changed during review pause, start over
 */

import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type { Job, ReviewSession, VerifyPlan } from "../api";
import { Badge, ErrorBox, Loading } from "../components";
import { useAsync } from "../hooks";
// useAsync is used for the preflight check (blocking quota-spending while
// the machine cannot run). Session is loaded via a manual effect so API
// mutation callbacks can update it without a full reload.
import { GoldenReviewTable } from "../components/review/GoldenReviewTable";
import { ReplaceGoldenFileUpload } from "../components/review/ReplaceGoldenFileUpload";
import { SecondApprovalPanel } from "../components/review/SecondApprovalPanel";

// ---------------------------------------------------------------- stage-2 job panel

function Stage2JobPanel({ job, onCancel }: { job: Job; onCancel: () => void }) {
  const tail = useRef<HTMLPreElement>(null);
  useEffect(() => {
    tail.current?.scrollTo({ top: tail.current.scrollHeight });
  }, [job.lines.length]);

  const verdict = job.summary?.verdict;
  const kind =
    verdict === "STILL VALID" ? "pass"
    : verdict === "CHANGED"   ? "fail"
    : verdict                 ? "warn"
    :                           "muted";

  return (
    <div className={`panel ${verdict === "CHANGED" ? "fail" : ""}`} style={{ marginTop: 12 }}>
      <div className="spread">
        <div>
          <div className="row">
            <Badge kind={job.status === "running" ? "info" : kind}>
              {job.status === "running" ? "running…" : (verdict ?? job.status)}
            </Badge>
            {job.planned_extractions != null && (
              <span className="chip">{job.planned_extractions} extractions approved</span>
            )}
            {job.exit_code != null && <span className="chip">exit {job.exit_code}</span>}
          </div>
          {verdict === "CHANGED" && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              The candidate version read at least one document differently. This is the
              gate working — a non-zero exit is the correct signal.
            </p>
          )}
        </div>
        {job.status === "running" && (
          <button className="danger" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
      {job.summary?.run_id && (
        <div style={{ marginTop: 8 }}>
          <div className="row small">
            <span className="chip">{job.summary.documents ?? 0} documents</span>
            <a href={`#/runs/${job.summary.run_id}`}>open the run →</a>
          </div>
        </div>
      )}
      <pre className="command" ref={tail} style={{ maxHeight: 280, overflowY: "auto" }}>
        {job.lines.join("\n") || "waiting for output…"}
      </pre>
    </div>
  );
}

// ---------------------------------------------------------------- page

export interface ReviewDetailPageProps {
  sessionId: string;
}

export function ReviewDetailPage({ sessionId }: ReviewDetailPageProps) {
  // ALL hooks at the top — no conditional hook calls.
  const [session, setSession] = useState<ReviewSession | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [verifyPlan, setVerifyPlan] = useState<VerifyPlan | null>(null);
  const [stage2Job, setStage2Job] = useState<Job | null>(null);

  const preflight = useAsync(() => api.preflight(), []);

  // Load session on mount and on reload. We track session in local state so
  // API mutation callbacks (complete, edit, etc.) can update it without a
  // full reload. The initial fetch is done via a fire-and-forget on mount.
  useEffect(() => {
    api.reviewSession(sessionId)
      .then(setSession)
      .catch((exc: Error) => setLoadError(exc.message));
  // Run once on mount / when sessionId changes.
  }, [sessionId]);

  // Poll stage2 job while it's running.
  const stage2JobId = stage2Job?.id;
  const stage2Running = stage2Job?.status === "running";
  useEffect(() => {
    if (!stage2JobId || !stage2Running) return;
    const timer = setInterval(() => {
      api.job(stage2JobId).then(setStage2Job).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [stage2JobId, stage2Running]);

  // Poll session while verifying (to catch state transitions).
  const sessionState = session?.state;
  useEffect(() => {
    if (sessionState !== "verifying") return;
    const timer = setInterval(() => {
      api.reviewSession(sessionId).then(setSession).catch(() => undefined);
    }, 2000);
    return () => clearInterval(timer);
  }, [sessionId, sessionState]);

  // Derived state — safe to compute unconditionally.
  const blocked = !preflight.data?.can_run_validation;

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setActionError(null);
    try {
      await fn();
    } catch (exc) {
      setActionError((exc as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const handleComplete = () =>
    guard("complete", async () => {
      const updated = await api.reviewComplete(session!.session_id);
      setSession(updated);
      setVerifyPlan(null);
    });

  const handlePlan = () =>
    guard("plan", async () => {
      const plan = await api.verifyCandidatePlan(session!.session_id);
      setVerifyPlan(plan);
    });

  const handleStart = (approved: number) =>
    guard("start", async () => {
      const job = await api.verifyCandidateStart(session!.session_id, approved);
      setStage2Job(job);
      const updated = await api.reviewSession(session!.session_id);
      setSession(updated);
    });

  const handleCancelStage2 = () =>
    guard("cancel", async () => {
      if (!stage2Job) return;
      const cancelled = await api.cancelJob(stage2Job.id);
      setStage2Job(cancelled);
    });

  // ---- render ----

  if (!session && !loadError) return <Loading />;
  if (loadError) return <ErrorBox message={loadError} />;
  if (!session) return <ErrorBox message={`Session ${sessionId} not found.`} />;

  const state = session.state;
  const isEditable = state === "drafted" || state === "replace_failed" || state === "reviewed";

  return (
    <>
      <h1>Review golden dataset</h1>

      {/* Session summary banner */}
      <div className="panel" style={{ marginBottom: 16 }}>
        <div className="row" style={{ flexWrap: "wrap", gap: 10, marginBottom: 6 }}>
          <Badge
            kind={
              state === "drafted" || state === "replace_failed"
                ? "warn"
                : state === "reviewed"
                  ? "info"
                  : state === "verified"
                    ? "pass"
                    : state === "stale"
                      ? "fail"
                      : "muted"
            }
          >
            {state}
          </Badge>
          <span className="small muted mono">session {session.session_id.slice(0, 8)}…</span>
        </div>
        <div className="grid2 small" style={{ gap: "4px 16px" }}>
          <span className="muted">
            Dataset: <strong style={{ color: "var(--text)" }}>{session.dataset}</strong>
          </span>
          <span className="muted">
            Action: <code>{session.action_id}</code>
          </span>
          <span className="muted">
            Trusted: <code>{session.trusted_version}</code>
          </span>
          <span className="muted">
            Candidate: <code>{session.candidate_version}</code>
          </span>
        </div>
        <div className="row small muted" style={{ marginTop: 6 }}>
          <span>Stage 1 job: <code>{session.stage1_job_id.slice(0, 8)}…</code></span>
          {session.stage2_job_id && (
            <span>Stage 2 job: <code>{session.stage2_job_id.slice(0, 8)}…</code></span>
          )}
          <a href="#/reviews">← Back to pending reviews</a>
        </div>
      </div>

      {/* STALE */}
      {state === "stale" && (
        <ErrorBox
          message={
            "INV-09: The platform golden dataset changed while this session was paused " +
            "(the current item hash no longer matches the hash recorded at review completion). " +
            "Start a new review session from the wizard to re-approve the updated golden — " +
            "verification cannot proceed against a golden the curator never reviewed."
          }
        />
      )}

      {/* REPLACE_FAILED warning (edit UI still shown below) */}
      {state === "replace_failed" && (
        <div className="panel fail" style={{ marginBottom: 12 }}>
          <strong>Golden set is partially updated.</strong>
          <p className="small" style={{ marginBottom: 0 }}>
            A previous whole-file replacement failed partway through the batch. The platform
            dataset now holds a partial golden set. Re-upload a corrected{" "}
            <code>golden.json</code> below. A successful replace restores the session to
            &lsquo;drafted&rsquo; state.
          </p>
        </div>
      )}

      {/* Review table — shown when review is in progress or complete */}
      {isEditable && (
        <>
          <h2 style={{ marginBottom: 8 }}>
            {state === "reviewed" ? "Reviewed golden dataset" : "Drafted golden dataset"}
          </h2>
          {state !== "reviewed" && (
            <p className="small muted" style={{ marginTop: 0 }}>
              Inspect and correct the drafted golden values before approving stage 2.
            </p>
          )}

          <GoldenReviewTable session={session} onSessionUpdated={setSession} />

          <ReplaceGoldenFileUpload sessionId={session.session_id} onReplaced={setSession} />

          {actionError && <ErrorBox message={actionError} />}
        </>
      )}

      {/* Complete-review button — only shown when state is drafted/replace_failed */}
      {(state === "drafted" || state === "replace_failed") && (
        <div style={{ marginTop: 20 }}>
          <button
            className="primary"
            disabled={busy !== null || state === "replace_failed"}
            title={
              state === "replace_failed"
                ? "Repair the golden set using the replacement upload above before completing the review"
                : undefined
            }
            onClick={() => void handleComplete()}
          >
            {busy === "complete" ? "Recording review…" : "Complete review"}
          </button>
          <p className="small muted" style={{ marginTop: 6 }}>
            Records a hash of the current platform items. Stage 2 will not spend quota
            until you separately approve its cost.
          </p>
        </div>
      )}

      {/* Second approval panel — shown only when state is reviewed */}
      {state === "reviewed" && (
        <SecondApprovalPanel
          plan={verifyPlan}
          busy={busy !== null}
          blocked={blocked}
          onPlan={() => void handlePlan()}
          onStart={(_sid, approved) => void handleStart(approved)}
        />
      )}

      {/* Verifying — stage 2 is running */}
      {state === "verifying" && (
        <>
          <div className="panel">
            <div className="row">
              <Badge kind="info">Verifying</Badge>
              <span className="small muted">Stage 2 is running.</span>
            </div>
            <p className="small muted" style={{ marginBottom: 0 }}>
              The candidate version is being verified against the golden dataset you
              approved. The exit code IS the gate — a non-zero exit means the candidate
              changed at least one document.
            </p>
          </div>
          {stage2Job ? (
            <Stage2JobPanel job={stage2Job} onCancel={() => void handleCancelStage2()} />
          ) : (
            session.stage2_job_id && (
              <p className="small muted" style={{ marginTop: 8 }}>
                Job <code>{session.stage2_job_id.slice(0, 8)}…</code> started (console
                may have restarted). Check <a href="#/jobs">Jobs</a> for live output.
              </p>
            )
          )}
        </>
      )}

      {/* Verified — done */}
      {state === "verified" && (
        <div className="panel">
          <Badge kind="pass">Stage 2 complete</Badge>
          <p className="small muted" style={{ marginTop: 6, marginBottom: 0 }}>
            Verification finished. Open <a href="#/runs">Runs</a> to see the full
            per-document verdict.
            {session.stage2_job_id && (
              <> Stage 2 job: <code>{session.stage2_job_id.slice(0, 8)}…</code></>
            )}
          </p>
        </div>
      )}
    </>
  );
}
