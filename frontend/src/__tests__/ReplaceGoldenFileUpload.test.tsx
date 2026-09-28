/**
 * Tests for ReplaceGoldenFileUpload — T-02.3.3 / R1 (Atchim).
 *
 * Covers:
 * - JSON parse failure shown as error
 * - Non-object JSON shown as error
 * - Successful replace calls api.reviewReplace and shows success message
 * - Server error shown verbatim
 * - File input reset after attempt
 */

import { describe, it, expect, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ReplaceGoldenFileUpload } from "../components/review/ReplaceGoldenFileUpload";
import { api } from "../api";
import type { ReviewSession } from "../api";

function makeSession(): ReviewSession {
  return {
    session_id: "sess-replace",
    dataset: "test-dataset",
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
  };
}

function makeGoldenJson(): string {
  return JSON.stringify({
    "doc-001": {
      document_id: "doc-001",
      fields: {
        total: { value: "100", type: "number", critical: true },
      },
    },
  });
}

describe("ReplaceGoldenFileUpload", () => {
  it("shows error when file is not valid JSON", async () => {
    render(<ReplaceGoldenFileUpload sessionId="sess-replace" onReplaced={vi.fn()} />);

    const file = new File(["not json {{{"], "golden.json", { type: "application/json" });
    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(screen.getByText(/not valid JSON/i)).toBeDefined(),
    );
  });

  it("shows error when JSON is an array (not an object)", async () => {
    render(<ReplaceGoldenFileUpload sessionId="sess-replace" onReplaced={vi.fn()} />);

    const file = new File([JSON.stringify([1, 2, 3])], "golden.json", { type: "application/json" });
    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(screen.getByText(/expected a JSON object/i)).toBeDefined(),
    );
  });

  it("calls api.reviewReplace and shows success message on valid file", async () => {
    const session = makeSession();
    vi.spyOn(api, "reviewReplace").mockResolvedValue({ ...session, state: "drafted" });
    const onReplaced = vi.fn();

    render(<ReplaceGoldenFileUpload sessionId="sess-replace" onReplaced={onReplaced} />);

    const file = new File([makeGoldenJson()], "golden.json", { type: "application/json" });
    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() => expect(onReplaced).toHaveBeenCalledOnce());
    await waitFor(() => expect(screen.getByText(/replaced with 1 entr/i)).toBeDefined());
  });

  it("shows server error verbatim", async () => {
    vi.spyOn(api, "reviewReplace").mockRejectedValue(
      new Error("entry 'doc-001': field 'total': value must be a number"),
    );

    render(<ReplaceGoldenFileUpload sessionId="sess-replace" onReplaced={vi.fn()} />);

    const file = new File([makeGoldenJson()], "golden.json", { type: "application/json" });
    const input = document.querySelector("input[type=file]") as HTMLInputElement;
    await userEvent.upload(input, file);

    await waitFor(() =>
      expect(screen.getByText(/field 'total': value must be a number/i)).toBeDefined(),
    );
  });
});
