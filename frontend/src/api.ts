// The whole HTTP surface, in one file, typed.
//
// Almost every call here is a READ of something on local disk, or the
// authoring of a scorer spec, and spends nothing. **Two do spend real
// IDP quota** — `compareStart` and `floorStart` — and both require an
// `approved_extractions` count echoed back from a real `--plan`, which
// the server re-computes and re-checks. Everything else prints the
// command for an operator to run at a terminal instead.

export type Verdict =
  | "match"
  | "missing"
  | "wrong_value"
  | "wrong_format"
  | "new_field"
  | "new_line";

export type FloorStatus = "above" | "within" | "unknown" | "no_floor" | null;

export interface RunSummary {
  run_id: string;
  recorded_at: string;
  documents?: number;
  failing_documents?: number;
  /** INCOMPLETE: the run aborted, or its artifact predates completeness
   *  recording — never a pass (DEBT-91). */
  gate?: RunGate;
  status?: RunStatus;
  abort_reason?: string | null;
  verdicts?: Record<string, number>;
  error?: string;
}

export type RunGate = "PASS" | "FAIL" | "INCOMPLETE";
/** `null` for an artifact written before completeness was recorded. */
export type RunStatus = "complete" | "aborted" | null;

export interface Leaf {
  label: string;
  field_key: string;
  verdict: Verdict;
  expected: string | null;
  actual: string | null;
  confidence: number | null;
  type: string | null;
  critical: boolean;
  gate_failing: boolean;
  floor_status: FloorStatus;
}

export interface RunDocument {
  document_id: string;
  gate: "PASS" | "FAIL" | "UNKNOWN";
  verdicts: Record<string, number>;
  rests_on_noise: boolean;
  leaves: Leaf[];
}

export interface FieldComparison {
  run_rate: number;
  run_observations: number;
  run_disagreements: number;
  floor_rate: number | null;
  floor_observations: number;
  status: Exclude<FloorStatus, null>;
}

export interface RunDetail {
  run_id: string;
  recorded_at: string;
  gate: RunGate;
  status: RunStatus;
  abort_reason: string | null;
  baseline: { name: string; summary: unknown; fields_with_floor: number } | null;
  field_comparison: Record<string, FieldComparison>;
  documents: RunDocument[];
}

export interface NoiseFloorSummary {
  name: string;
  generated_at?: string;
  action_id?: string;
  action_version?: string;
  repeats?: number;
  documents?: number;
  field_instability_rate?: number;
  field_observations?: number;
  fields?: number;
  error?: string;
}

export interface Calibration {
  name: string;
  generated_at?: string;
  corpus?: { documents: number; fields: number };
  noise_floor_applied?: boolean;
  thresholds?: Record<string, number>;
  blind_spots: string[];
  still_human: string[];
  fields: Record<string, { critical: boolean; detail?: string }>;
  tables: Record<string, { critical: boolean; detail?: string }>;
  error?: string;
}

export interface PinGroup {
  action_id: string;
  action_version: string;
  dataset: string | null;
  document_dir: string | null;
  documents: {
    document_id: string;
    pinned_at: string;
    has_raw_capture: boolean;
    fields: number | null;
  }[];
}

export interface Rule {
  when: Record<string, unknown>;
  then: Record<string, unknown>;
}

export interface Scorer {
  name: string;
  description: string;
  kind: "shipped" | "custom";
  is_default: boolean;
  scorer?: string;
  base?: string;
  rules?: Rule[];
}

export interface Vocabulary {
  conditions: string[];
  actions: string[];
  verdicts: Verdict[];
  actionable_verdicts: Verdict[];
  kinds: string[];
  field_types: string[];
  bases: string[];
}

export interface UploadReport {
  upload_id: string;
  filename: string;
  size_bytes: number;
  uploaded_at: string;
  patterns: string;
  documents: { name: string; pinned_at: { action_id: string; action_version: string }[] }[];
  document_count: number;
  skipped: string[];
  rejected_entries: string[];
  cost: Record<string, number>;
  already_pinned: number;
}

export interface UnpackReport {
  upload_id: string;
  document_dir: string;
  documents: string[];
  document_count: number;
  skipped: string[];
  next_commands: { label: string; why: string; command: string }[];
}

export interface DiscoveredVersion {
  version: string;
  status: "exists" | "unknown";
}

export interface VersionDiscovery {
  anchor: string;
  versions: DiscoveredVersion[];
  probes_used: number;
  truncated: boolean;
  rate_limited: boolean;
  spends_extraction_quota: false;
}

export interface ComparePlan {
  planned_extractions: number;
  command: string;
  output: string[];
  confirm_with: { approved_extractions: number };
}

export interface Job {
  id: string;
  kind: string;
  status: "planning" | "running" | "succeeded" | "failed" | "cancelled";
  exit_code: number | null;
  planned_extractions: number | null;
  command: string;
  lines: string[];
  summary: {
    /** `INTERRUPTED` is set on load, for a job that was still running
     *  when an earlier console stopped. It is never resumed. */
    verdict?: "STILL VALID" | "CHANGED" | "RUN FAILED" | "INTERRUPTED";
    /** Why a result is missing, when one is. */
    note?: string;
    artifact_discrepancy?: string;
    /** Set when the run artifact says the run aborted (DEBT-91): the
     *  reason, and no document counts. */
    run_incomplete?: string;
    /** From the run artifact and the authoritative gate — not scraped text. */
    run_id?: string | null;
    documents?: number | null;
    still_valid_documents?: number | null;
    changed_documents?: number | null;
    changed_document_ids?: string[];
    changed_document_ids_truncated?: boolean;
    /** Written by a noise-floor job; the run is read against it. */
    floor_report?: string;
    exit_code?: number;
  };
}

export interface Preflight {
  can_run_validation: boolean;
  blockers: string[];
  idp: Record<string, boolean>;
  platform: Record<string, boolean>;
  env_file: string | null;
  env_searched: string[];
  workspace: string;
  writes: { run_artifacts: string; pin_store: string; uploads: string };
}

export interface PlatformCapabilities {
  configured: boolean;
  reason?: string;
  reachable?: boolean;
  scores_v3?: boolean;
  runs_list_endpoint?: boolean;
  runs_list_detail?: string;
  is_gate_source?: false;
}

export interface ScoreTrend {
  bucket: string;
  series: { bucket: string; counts: Record<string, number> }[];
  score_names: Record<string, number>;
  observations: number;
  truncated: boolean;
}

export interface FloorRequest {
  upload_id?: string;
  document_dir?: string;
  org: string;
  action: string;
  /** The ONE version being measured against itself. */
  version: string;
  repeats?: number;
  max_documents?: number;
}

export interface CompareRequest {
  upload_id?: string;
  document_dir?: string;
  dataset: string;
  org: string;
  action: string;
  trusted_version: string;
  candidate_version: string;
  /** The script's own default is 200; a bigger corpus needs this. */
  max_documents?: number;
  /** Verify the files that pinned even if some failed. Off by default. */
  allow_partial?: boolean;
  /** Re-read already-pinned files — one extra extraction each. */
  repin?: boolean;
  glob?: string;
}

export class ApiError extends Error {}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* the status line is the best we have */
    }
    throw new ApiError(detail);
  }
  return (await response.json()) as T;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

export const api = {
  runs: () => call<{ runs: RunSummary[] }>("/api/runs"),
  run: (id: string, baseline?: string) =>
    call<RunDetail>(`/api/runs/${encodeURIComponent(id)}${baseline ? `?baseline=${encodeURIComponent(baseline)}` : ""}`),
  noiseFloors: () => call<{ noise_floors: NoiseFloorSummary[] }>("/api/noise-floors"),
  noiseFloor: (name: string) =>
    call<{ name: string; summary: Record<string, unknown>; interpretation: string[]; fields: { field: string; instability_rate: number; observations: number; unstable: number }[] }>(
      `/api/noise-floors/${encodeURIComponent(name)}`,
    ),
  blindSpots: () => call<{ calibrations: Calibration[] }>("/api/blind-spots"),
  pins: () => call<{ pins: PinGroup[] }>("/api/pins"),
  scorers: () =>
    call<{ scorers: Scorer[]; errors: string[]; vocabulary: Vocabulary; scorer_dir: string }>("/api/scorers"),
  validateScorer: (spec: unknown) =>
    call<{ valid: boolean; errors: string[]; monotone: boolean | null }>("/api/scorers/validate", json(spec)),
  previewScorer: (spec: unknown, samples: unknown[]) =>
    call<{ results: { sample: Record<string, unknown>; base: Outcome; custom: Outcome }[] }>(
      "/api/scorers/preview",
      json({ spec, samples }),
    ),
  saveScorer: (name: string, spec: unknown) =>
    call<{ saved: unknown; path: string; run_with: string }>(`/api/scorers/${encodeURIComponent(name)}`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(spec),
    }),
  deleteScorer: (name: string) =>
    call<{ deleted: string }>(`/api/scorers/${encodeURIComponent(name)}`, { method: "DELETE" }),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return call<UploadReport>("/api/uploads", { method: "POST", body: form });
  },
  unpack: (id: string) => call<UnpackReport>(`/api/uploads/${encodeURIComponent(id)}/unpack`, json({})),

  discoverVersions: (org: string, action: string, anchor: string) =>
    call<VersionDiscovery>("/api/actions/versions", json({ org, action, anchor })),
  comparePlan: (request: CompareRequest) =>
    call<ComparePlan>("/api/workflows/compare/plan", json(request)),
  compareStart: (request: CompareRequest, approved_extractions: number) =>
    call<Job>("/api/workflows/compare/start", json({ ...request, approved_extractions })),
  floorPlan: (request: FloorRequest) =>
    call<ComparePlan>("/api/workflows/floor/plan", json(request)),
  floorStart: (request: FloorRequest, approved_extractions: number) =>
    call<Job>("/api/workflows/floor/start", json({ ...request, approved_extractions })),
  jobs: () => call<{ jobs: Omit<Job, "lines">[] }>("/api/jobs"),
  jobsBusy: () => call<{ busy: boolean }>("/api/jobs-busy"),
  job: (id: string) => call<Job>(`/api/jobs/${encodeURIComponent(id)}`),
  cancelJob: (id: string) => call<Job>(`/api/jobs/${encodeURIComponent(id)}/cancel`, json({})),

  preflight: () => call<Preflight>("/api/preflight"),
  platformCapabilities: () => call<PlatformCapabilities>("/api/platform/capabilities"),
  platformTrend: (name?: string, days = 30) =>
    call<ScoreTrend>(`/api/platform/trend?days=${days}${name ? `&name=${encodeURIComponent(name)}` : ""}`),
  platformDatasets: () => call<{ datasets: string[] }>("/api/platform/datasets"),
  platformDataset: (name: string) =>
    call<{ dataset: string; items_returned: number; total_items: number | null; documents: { id: string; document_id: string | null; metadata: unknown }[] }>(
      `/api/platform/datasets/${encodeURIComponent(name)}`,
    ),
};

export interface Outcome {
  verdict: Verdict;
  critical: boolean;
  format_critical: boolean;
}
