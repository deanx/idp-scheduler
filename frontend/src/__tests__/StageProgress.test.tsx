/**
 * Tests for StageProgress state derivation (T-02.3.1, DoD automated tests).
 *
 * The deriveStages() pure function is tested directly so the assertions cover
 * behaviour without a DOM render. The component rendering is covered by the
 * integration snapshot below.
 */

import { describe, it, expect } from "vitest";
import { render, screen } from "@testing-library/react";
import { deriveStages, StageProgress } from "../components/review/StageProgress";
import type { Job } from "../api";

// ---- helpers

function makeJob(status: Job["status"]): Job {
  return {
    id: "job-1",
    kind: "pin-document",
    status,
    exit_code: status === "succeeded" ? 0 : status === "failed" ? 1 : null,
    planned_extractions: 10,
    command: "pin_document.py --all --yes",
    lines: [],
    summary: {},
  };
}

// ---- pure function tests

describe("deriveStages()", () => {
  it("shows uploading in-progress before upload is done", () => {
    const stages = deriveStages({ uploaded: false, planned: false, job: null });
    expect(stages.uploading).toBe("in-progress");
    expect(stages.validating).toBe("pending");
    expect(stages.drafting).toBe("pending");
  });

  it("marks uploading done and validating in-progress once uploaded", () => {
    const stages = deriveStages({ uploaded: true, planned: false, job: null });
    expect(stages.uploading).toBe("done");
    expect(stages.validating).toBe("in-progress");
    expect(stages.drafting).toBe("pending");
  });

  it("marks validating done and drafting pending when plan is obtained but no job yet", () => {
    const stages = deriveStages({ uploaded: true, planned: true, job: null });
    expect(stages.uploading).toBe("done");
    expect(stages.validating).toBe("done");
    expect(stages.drafting).toBe("pending");
  });

  it("marks drafting in-progress while job is running", () => {
    const stages = deriveStages({ uploaded: true, planned: true, job: makeJob("running") });
    expect(stages.drafting).toBe("in-progress");
  });

  it("marks drafting done when job succeeds", () => {
    const stages = deriveStages({ uploaded: true, planned: true, job: makeJob("succeeded") });
    expect(stages.uploading).toBe("done");
    expect(stages.validating).toBe("done");
    expect(stages.drafting).toBe("done");
  });

  it("marks drafting failed when job fails", () => {
    const stages = deriveStages({ uploaded: true, planned: true, job: makeJob("failed") });
    expect(stages.drafting).toBe("failed");
  });

  it("marks drafting failed when job is cancelled", () => {
    const stages = deriveStages({ uploaded: true, planned: true, job: makeJob("cancelled") });
    expect(stages.drafting).toBe("failed");
  });
});

// ---- component render tests

describe("StageProgress component", () => {
  it("renders the three stage labels", () => {
    render(<StageProgress uploaded={false} planned={false} job={null} />);
    expect(screen.getByText("Uploading")).toBeDefined();
    expect(screen.getByText("Validating")).toBeDefined();
    expect(screen.getByText("Drafting golden dataset")).toBeDefined();
  });

  it("has aria-label for assistive technology", () => {
    render(<StageProgress uploaded={true} planned={true} job={makeJob("succeeded")} />);
    expect(screen.getByLabelText("draft progress")).toBeDefined();
  });

  it("shows done badge when all stages complete", () => {
    render(<StageProgress uploaded={true} planned={true} job={makeJob("succeeded")} />);
    // Three "done" aria-label badges should exist
    const doneBadges = screen.getAllByLabelText("done");
    expect(doneBadges.length).toBe(3);
  });
});
