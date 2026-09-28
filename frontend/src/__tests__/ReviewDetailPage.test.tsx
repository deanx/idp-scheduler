/**
 * Tests for ReviewDetailPage state machine transitions — T-02.3.2 / R1 (Atchim).
 *
 * Covers the per-state UI:
 * - stale       → error message about INV-09
 * - replace_failed → warning banner + review UI still shown
 * - drafted     → review table + complete-review button
 * - reviewed    → review table + SecondApprovalPanel (no complete button)
 * - verifying   → verifying panel
 * - verified    → done message with link to Runs
 *
 * Does NOT test live polling or the actual API calls (those are integration
 * concerns). Each test stubs api.reviewSession() to return the desired state
 * and api.preflight() to return a runnable machine, then asserts the correct
 * UI branch is rendered.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ReviewDetailPage } from "../pages/ReviewDetailPage";
import { api } from "../api";
import type { ReviewSession, ReviewSessionState } from "../api";

function makeSession(state: ReviewSessionState): ReviewSession {
  return {
    session_id: "sess-detail",
    dataset: "test-dataset",
    org_id: "org-001",
    action_id: "act-001",
    trusted_version: "1.0.0",
    candidate_version: "2.0.0",
    document_dir: "/tmp/docs",
    archive_sha256: "aabb",
    approved_golden_hash: state === "reviewed" || state === "verified" ? "abc123" : null,
    stage1_job_id: "job-stage1",
    stage2_job_id: state === "verifying" || state === "verified" ? "job-stage2" : null,
    state,
    created_at: "2026-09-28T00:00:00Z",
    edited_document_ids: {},
  };
}

beforeEach(() => {
  // Default: machine can run (preflight OK)
  vi.spyOn(api, "preflight").mockResolvedValue({
    can_run_validation: true,
    blockers: [],
    idp: {},
    platform: {},
    env_file: null,
    env_searched: [],
    workspace: "/tmp/ws",
    writes: { run_artifacts: "/tmp/ws/run-artifacts", pin_store: "/tmp/ws/pins", uploads: "/tmp/ws/uploads" },
  });
  // Default /values: one document with one field
  vi.spyOn(api, "reviewValues").mockResolvedValue({
    session_id: "sess-detail",
    dataset: "test-dataset",
    platform_configured: true,
    documents: [
      {
        document_id: "doc-001",
        fields: [
          { name: "total", value: "100", type: "number", confidence: 0.9, critical: true, provenance: "drafted" },
        ],
        tables: {},
        prompts: {},
      },
    ],
    missing_from_platform: [],
  });
});

describe("ReviewDetailPage — stale state", () => {
  it("shows INV-09 stale error message", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("stale"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByText(/INV-09/)).toBeDefined(),
    );
  });

  it("does NOT show complete-review button when stale", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("stale"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() => screen.getByText(/INV-09/));
    expect(screen.queryByRole("button", { name: /complete review/i })).toBeNull();
  });
});

describe("ReviewDetailPage — replace_failed state", () => {
  it("shows partial-update warning banner", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("replace_failed"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByText(/partially updated/i)).toBeDefined(),
    );
  });

  it("still renders the review table alongside the warning", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("replace_failed"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    // Both the warning and the table must be visible
    await waitFor(() => screen.getByText(/partially updated/i));
    // The review table section heading is visible alongside the warning
    await waitFor(() => screen.getByText(/drafted golden dataset/i));
  });

  it("complete-review button is disabled when replace_failed", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("replace_failed"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() => {
      const btn = screen.queryByRole("button", { name: /complete review/i });
      // Button may exist but must be disabled
      if (btn) {
        expect((btn as HTMLButtonElement).disabled).toBe(true);
      }
    });
  });
});

describe("ReviewDetailPage — drafted state", () => {
  it("renders 'Complete review' button", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("drafted"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /complete review/i })).toBeDefined(),
    );
  });

  it("does NOT render SecondApprovalPanel in drafted state", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("drafted"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() => screen.getByRole("button", { name: /complete review/i }));
    // The "Price stage 2" button belongs to SecondApprovalPanel — not shown in drafted state
    expect(screen.queryByRole("button", { name: /price stage 2/i })).toBeNull();
  });
});

describe("ReviewDetailPage — reviewed state", () => {
  it("renders SecondApprovalPanel (Price stage 2 button)", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("reviewed"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByRole("button", { name: /price stage 2/i })).toBeDefined(),
    );
  });

  it("does NOT render complete-review button when reviewed", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("reviewed"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() => screen.getByRole("button", { name: /price stage 2/i }));
    expect(screen.queryByRole("button", { name: /complete review/i })).toBeNull();
  });
});

describe("ReviewDetailPage — verifying state", () => {
  it("shows verifying panel", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("verifying"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByText(/stage 2 is running/i)).toBeDefined(),
    );
  });
});

describe("ReviewDetailPage — verified state", () => {
  it("shows stage 2 complete message and Runs link", async () => {
    vi.spyOn(api, "reviewSession").mockResolvedValue(makeSession("verified"));

    render(<ReviewDetailPage sessionId="sess-detail" />);

    await waitFor(() =>
      expect(screen.getByText(/stage 2 complete/i)).toBeDefined(),
    );
    expect(screen.getByRole("link", { name: /runs/i })).toBeDefined();
  });
});

describe("ReviewDetailPage — load error", () => {
  it("shows error when session fetch fails", async () => {
    vi.spyOn(api, "reviewSession").mockRejectedValue(new Error("session not found"));

    render(<ReviewDetailPage sessionId="sess-does-not-exist" />);

    await waitFor(() =>
      expect(screen.getByText(/session not found/i)).toBeDefined(),
    );
  });
});
