/**
 * Tests for ReviewsPage (#/reviews) and ReviewSessionRow — T-02.3.5 / R1 (Atchim).
 *
 * Covers:
 * - Empty state message when no sessions
 * - Pending sessions rendered with state badges
 * - Links to the correct review detail URL
 * - State badge labels for all ReviewSessionState values
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { ReviewsPage, ReviewSessionRow } from "../pages/ReviewsPage";
import { api } from "../api";
import type { ReviewSession } from "../api";

function makeSession(overrides: Partial<ReviewSession> = {}): ReviewSession {
  return {
    session_id: "sess-001",
    dataset: "my-golden-set",
    org_id: "org-001",
    action_id: "act-001",
    trusted_version: "1.0.0",
    candidate_version: "2.0.0",
    document_dir: "/tmp/docs",
    archive_sha256: "aabb",
    approved_golden_hash: null,
    stage1_job_id: "job-1",
    stage2_job_id: null,
    state: "drafted",
    created_at: "2026-09-28T00:00:00Z",
    edited_document_ids: {},
    ...overrides,
  };
}

describe("ReviewSessionRow", () => {
  it("renders dataset name", () => {
    render(
      <table>
        <tbody>
          <ReviewSessionRow session={makeSession()} />
        </tbody>
      </table>,
    );
    expect(screen.getByText("my-golden-set")).toBeDefined();
  });

  it("renders version pair", () => {
    render(
      <table>
        <tbody>
          <ReviewSessionRow session={makeSession()} />
        </tbody>
      </table>,
    );
    expect(screen.getByText(/1\.0\.0.*2\.0\.0/)).toBeDefined();
  });

  it("links to the correct review detail URL", () => {
    render(
      <table>
        <tbody>
          <ReviewSessionRow session={makeSession({ session_id: "sess-xyz" })} />
        </tbody>
      </table>,
    );
    const link = screen.getByRole("link", { name: /open/i });
    expect((link as HTMLAnchorElement).href).toContain("#/validate/review/sess-xyz");
  });

  it.each([
    ["drafted", "awaiting review"],
    ["reviewed", "reviewed"],
    ["verifying", "verifying"],
    ["verified", "verified"],
    ["stale", "stale"],
    ["replace_failed", "replace failed"],
  ] as const)("state %s renders badge text %s", (state, expectedText) => {
    render(
      <table>
        <tbody>
          <ReviewSessionRow session={makeSession({ state })} />
        </tbody>
      </table>,
    );
    expect(screen.getByText(new RegExp(expectedText, "i"))).toBeDefined();
  });
});

describe("ReviewsPage", () => {
  it("shows empty state when no sessions", async () => {
    vi.spyOn(api, "reviews").mockResolvedValue({ sessions: [] });
    render(<ReviewsPage />);
    await waitFor(() => expect(screen.getByText(/no review sessions/i)).toBeDefined());
  });

  it("renders all sessions from api.reviews()", async () => {
    vi.spyOn(api, "reviews").mockResolvedValue({
      sessions: [
        makeSession({ session_id: "sess-001", dataset: "dataset-a", state: "drafted" }),
        makeSession({ session_id: "sess-002", dataset: "dataset-b", state: "verified" }),
      ],
    });

    render(<ReviewsPage />);

    await waitFor(() => {
      expect(screen.getByText("dataset-a")).toBeDefined();
      expect(screen.getByText("dataset-b")).toBeDefined();
    });
  });

  it("shows an error box when api.reviews() rejects", async () => {
    vi.spyOn(api, "reviews").mockRejectedValue(new Error("network error"));
    render(<ReviewsPage />);
    await waitFor(() => expect(screen.getByText(/network error/i)).toBeDefined());
  });
});
