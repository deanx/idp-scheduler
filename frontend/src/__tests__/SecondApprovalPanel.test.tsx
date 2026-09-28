/**
 * Tests for SecondApprovalPanel confirm-echo interaction (T-02.3.4, DoD automated tests).
 *
 * The panel must require the curator to type back the EXACT planned extraction count
 * before the start button is enabled — the same behaviour as compare/start and
 * floor/start (the existing approved-extractions-echo pattern).
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { SecondApprovalPanel } from "../components/review/SecondApprovalPanel";
import type { VerifyPlan, ReviewSession } from "../api";

// ---- helpers

function makePlan(extractions: number): VerifyPlan {
  const session: ReviewSession = {
    session_id: "sess-abc123",
    dataset: "test-dataset",
    org_id: "org-1",
    action_id: "invoice-extract",
    trusted_version: "1.0.0",
    candidate_version: "2.0.0",
    document_dir: "/tmp/docs",
    archive_sha256: "abc123",
    approved_golden_hash: "hash-xyz",
    stage1_job_id: "job-1",
    stage2_job_id: null,
    state: "reviewed",
    created_at: "2026-09-28T10:00:00Z",
    edited_document_ids: {},
  };
  return {
    planned_extractions: extractions,
    command: `verify_document.py --all --version 2.0.0 --dataset test-dataset --yes`,
    output: [],
    confirm_with: { approved_extractions: extractions, session_id: "sess-abc123" },
    session,
  };
}

// ---- tests: no plan

describe("SecondApprovalPanel — no plan yet", () => {
  it("renders the Price stage 2 button when no plan is present", () => {
    render(
      <SecondApprovalPanel
        plan={null}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    expect(screen.getByText(/Price stage 2/i)).toBeDefined();
  });

  it("disables the button when blocked", () => {
    render(
      <SecondApprovalPanel
        plan={null}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={true}
      />,
    );
    const btn = screen.getByText(/Price stage 2/i) as HTMLButtonElement;
    expect(btn.disabled).toBe(true);
  });
});

// ---- tests: plan obtained, echo required

describe("SecondApprovalPanel — confirm echo interaction", () => {
  it("shows the planned extraction count in the label", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    // Multiple elements contain "40" — the count appears in the description, label, and button.
    // Verify at least two occurrences (description + input label) and the aria-label.
    expect(screen.getAllByText(/40/).length).toBeGreaterThanOrEqual(2);
    expect(screen.getByLabelText(/confirm 40 extractions/i)).toBeDefined();
  });

  it("start button is disabled when echo input is empty", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });

  it("start button is disabled when echo does not match the count", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 40 extractions/i);
    fireEvent.change(echoInput, { target: { value: "39" } });
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });

  it("start button is disabled when echo is the right number but wrong string (extra chars)", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 40 extractions/i);
    fireEvent.change(echoInput, { target: { value: "40x" } });
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });

  it("start button is enabled when echo exactly matches the count", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 40 extractions/i);
    fireEvent.change(echoInput, { target: { value: "40" } });
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(false);
  });

  it("calls onStart with the session_id and count when the confirmed start button is clicked", () => {
    const onStart = vi.fn();
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={onStart}
        busy={false}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 40 extractions/i);
    fireEvent.change(echoInput, { target: { value: "40" } });
    const startBtn = screen.getByText(/Run stage 2/i);
    fireEvent.click(startBtn);
    expect(onStart).toHaveBeenCalledOnce();
    expect(onStart).toHaveBeenCalledWith("sess-abc123", 40);
  });

  it("does not call onStart when echo is wrong and button is clicked", () => {
    const onStart = vi.fn();
    render(
      <SecondApprovalPanel
        plan={makePlan(40)}
        onPlan={vi.fn()}
        onStart={onStart}
        busy={false}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 40 extractions/i);
    fireEvent.change(echoInput, { target: { value: "99" } });
    // Button should be disabled, but also test that onStart is not called
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
    fireEvent.click(startBtn);
    expect(onStart).not.toHaveBeenCalled();
  });

  it("start button is disabled while busy regardless of correct echo", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(10)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={true}
        blocked={false}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 10 extractions/i);
    fireEvent.change(echoInput, { target: { value: "10" } });
    const startBtn = screen.getByText(/Starting…/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });

  it("start button is disabled when blocked even with correct echo", () => {
    render(
      <SecondApprovalPanel
        plan={makePlan(10)}
        onPlan={vi.fn()}
        onStart={vi.fn()}
        busy={false}
        blocked={true}
      />,
    );
    const echoInput = screen.getByLabelText(/confirm 10 extractions/i);
    fireEvent.change(echoInput, { target: { value: "10" } });
    const startBtn = screen.getByText(/Run stage 2/i) as HTMLButtonElement;
    expect(startBtn.disabled).toBe(true);
  });
});
