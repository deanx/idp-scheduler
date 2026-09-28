/**
 * Tests for GoldenReviewTable + EditModal — T-02.3.7 / R1 (Atchim C1/C2).
 *
 * Key assertions:
 * - Field values from /values are rendered (C1 closed)
 * - EditModal is pre-populated with ALL current fields; editing one and submitting
 *   sends all fields in the PATCH body (C2 closed: no silent drop)
 * - Provenance (drafted/edited) is rendered distinctly
 * - 503 from /values renders an error, not a blank table
 * - Edit button is absent when session is not in an editable state
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { GoldenReviewTable } from "../components/review/GoldenReviewTable";
import { api } from "../api";
import type { ReviewSession, ReviewValues } from "../api";

// ---- helpers

function makeSession(
  overrides: Partial<ReviewSession> = {},
): ReviewSession {
  return {
    session_id: "sess-abc",
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
    ...overrides,
  };
}

function makeValues(overrides: Partial<ReviewValues> = {}): ReviewValues {
  return {
    session_id: "sess-abc",
    dataset: "test-dataset",
    platform_configured: true,
    documents: [
      {
        document_id: "doc-001",
        fields: [
          {
            name: "invoice_total",
            value: "1250.00",
            type: "number",
            confidence: 0.98,
            critical: true,
            provenance: "drafted",
          },
          {
            name: "invoice_date",
            value: "2024-06-28",
            type: "date",
            confidence: 0.90,
            critical: false,
            provenance: "drafted",
          },
        ],
        tables: {},
        prompts: {},
      },
    ],
    missing_from_platform: [],
    ...overrides,
  };
}

describe("GoldenReviewTable — field values rendered (C1)", () => {
  beforeEach(() => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues());
  });

  it("renders the document ID", async () => {
    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => expect(screen.getByText("doc-001")).toBeDefined());
  });

  it("renders field names and values", async () => {
    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => {
      expect(screen.getByText("invoice_total")).toBeDefined();
      expect(screen.getByText("1250.00")).toBeDefined();
      expect(screen.getByText("invoice_date")).toBeDefined();
      expect(screen.getByText("2024-06-28")).toBeDefined();
    });
  });

  it("renders type chips", async () => {
    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => {
      const chips = screen.getAllByText("number");
      expect(chips.length).toBeGreaterThanOrEqual(1);
    });
  });

  it("renders 'critical' badge for critical fields", async () => {
    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => expect(screen.getAllByText("critical").length).toBeGreaterThanOrEqual(1));
  });
});

describe("GoldenReviewTable — provenance rendering", () => {
  it("renders 'edited' badge for fields with edited provenance", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(
      makeValues({
        documents: [
          {
            document_id: "doc-001",
            fields: [
              { name: "total", value: "999", type: "number", confidence: 0.9, critical: true, provenance: "edited" },
              { name: "vat", value: "0.20", type: "number", confidence: 0.8, critical: false, provenance: "drafted" },
            ],
            tables: {},
            prompts: {},
          },
        ],
      }),
    );

    render(
      <GoldenReviewTable
        session={makeSession({ edited_document_ids: { "doc-001": ["total"] } })}
        onSessionUpdated={vi.fn()}
      />,
    );

    await waitFor(() => expect(screen.getByText("edited")).toBeDefined());
  });
});

describe("GoldenReviewTable — error states", () => {
  it("shows error message when /values returns 503", async () => {
    vi.spyOn(api, "reviewValues").mockRejectedValue(new Error("platform not configured"));

    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByText(/platform not configured/i)).toBeDefined(),
    );
  });

  it("shows empty-state error when documents list is empty", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues({ documents: [] }));

    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);

    await waitFor(() =>
      expect(screen.getByText(/no items on the platform/i)).toBeDefined(),
    );
  });
});

describe("GoldenReviewTable — edit button visibility", () => {
  it("shows Edit fields button when state is drafted", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues());
    render(<GoldenReviewTable session={makeSession({ state: "drafted" })} onSessionUpdated={vi.fn()} />);
    await waitFor(() => expect(screen.getByRole("button", { name: /edit fields/i })).toBeDefined());
  });

  it("hides Edit fields button when state is verified", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues());
    render(<GoldenReviewTable session={makeSession({ state: "verified" })} onSessionUpdated={vi.fn()} />);
    await waitFor(() => {
      // document ID must be present (table is rendered)
      expect(screen.getByText("doc-001")).toBeDefined();
      // but no edit button
      expect(screen.queryByRole("button", { name: /edit fields/i })).toBeNull();
    });
  });
});

describe("EditModal — pre-populated and PATCH body preserves all fields (C2)", () => {
  it("pre-populates the modal with all current fields from /values", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues());

    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => screen.getByRole("button", { name: /edit fields/i }));

    await userEvent.click(screen.getByRole("button", { name: /edit fields/i }));

    // Both fields from /values should appear in the modal's field-name inputs
    const nameInputs = screen.getAllByRole<HTMLInputElement>("textbox", { name: /field name row/i });
    const names = nameInputs.map((i) => i.value);
    expect(names).toContain("invoice_total");
    expect(names).toContain("invoice_date");
  });

  it("PATCH body contains ALL original fields when only one is edited (C2: no silent drop)", async () => {
    vi.spyOn(api, "reviewValues").mockResolvedValue(makeValues());

    const patchCalls: Array<{ sessionId: string; documentId: string; entry: unknown }> = [];
    vi.spyOn(api, "reviewPatchItem").mockImplementation((sessionId, documentId, entry) => {
      patchCalls.push({ sessionId, documentId, entry });
      return Promise.resolve(makeSession({ state: "drafted" }));
    });

    render(<GoldenReviewTable session={makeSession()} onSessionUpdated={vi.fn()} />);
    await waitFor(() => screen.getByRole("button", { name: /edit fields/i }));

    await userEvent.click(screen.getByRole("button", { name: /edit fields/i }));

    // Change only the first field's value (invoice_total)
    const valueInputs = screen.getAllByRole<HTMLInputElement>("textbox", { name: /value for/i });
    // Clear and type a new value for invoice_total
    await userEvent.clear(valueInputs[0]);
    await userEvent.type(valueInputs[0], "9999.00");
    // Blur to trigger validation
    fireEvent.blur(valueInputs[0]);

    await userEvent.click(screen.getByRole("button", { name: /save/i }));

    await waitFor(() => expect(patchCalls.length).toBeGreaterThan(0));

    const { entry } = patchCalls[0] as { entry: { fields: Record<string, unknown> } };
    // BOTH fields must be in the PATCH body — not just the edited one
    const fieldKeys = Object.keys(entry.fields);
    expect(fieldKeys).toContain("invoice_total");
    expect(fieldKeys).toContain("invoice_date");
  });
});
