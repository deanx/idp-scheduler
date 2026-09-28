/**
 * StageProgress — AC1 (T-02.3.1)
 *
 * Shows three named, sequential stages during the draft-golden job so the
 * curator sees distinct progress rather than one undifferentiated spinner.
 * The stages map to:
 *   1. Uploading   — the ZIP has been accepted by the console
 *   2. Validating  — the --plan call priced the corpus (validates the archive)
 *   3. Drafting    — pin_document.py is running / has finished
 */

import type { Job } from "../../api";

export type StageStatus = "pending" | "in-progress" | "done" | "failed";

export interface DraftStages {
  uploading: StageStatus;
  validating: StageStatus;
  drafting: StageStatus;
}

/**
 * Derive stage statuses from the current wizard state. This is a pure
 * function so the automated test can exercise it without a DOM.
 */
export function deriveStages(opts: {
  uploaded: boolean;
  planned: boolean;
  job: Job | null;
}): DraftStages {
  const { uploaded, planned, job } = opts;

  if (!uploaded) {
    return { uploading: "in-progress", validating: "pending", drafting: "pending" };
  }
  if (!planned) {
    return { uploading: "done", validating: "in-progress", drafting: "pending" };
  }
  if (!job) {
    return { uploading: "done", validating: "done", drafting: "pending" };
  }
  const draftStatus: StageStatus =
    job.status === "succeeded"
      ? "done"
      : job.status === "failed" || job.status === "cancelled"
        ? "failed"
        : "in-progress";
  return { uploading: "done", validating: "done", drafting: draftStatus };
}

const STATUS_LABEL: Record<StageStatus, string> = {
  pending: "pending",
  "in-progress": "…",
  done: "✓",
  failed: "✗",
};

const STATUS_CLASS: Record<StageStatus, string> = {
  pending: "muted",
  "in-progress": "info",
  done: "pass",
  failed: "fail",
};

function StageStep({ label, status }: { label: string; status: StageStatus }) {
  return (
    <div className="row" style={{ gap: 8, alignItems: "center" }}>
      <span
        className={`badge ${STATUS_CLASS[status]}`}
        style={{ minWidth: 24, textAlign: "center" }}
        aria-label={status}
      >
        {STATUS_LABEL[status]}
      </span>
      <span className={status === "pending" ? "muted" : undefined}>{label}</span>
    </div>
  );
}

export interface StageProgressProps {
  uploaded: boolean;
  planned: boolean;
  job: Job | null;
}

export function StageProgress({ uploaded, planned, job }: StageProgressProps) {
  const stages = deriveStages({ uploaded, planned, job });
  return (
    <div
      className="row"
      style={{ gap: 20, flexWrap: "wrap" }}
      aria-label="draft progress"
    >
      <StageStep label="Uploading" status={stages.uploading} />
      <span className="muted" aria-hidden>→</span>
      <StageStep label="Validating" status={stages.validating} />
      <span className="muted" aria-hidden>→</span>
      <StageStep label="Drafting golden dataset" status={stages.drafting} />
    </div>
  );
}
