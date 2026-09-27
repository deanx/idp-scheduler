import { useState } from "react";
import { api } from "../api";
import type { RunDocument } from "../api";
import { useAsync } from "../hooks";
import { Badge, Counts, ErrorBox, Floor, Gate, Loading, VerdictText, when } from "../components";

/**
 * One run, read against the noise floor.
 *
 * This is the view the evaluation platform cannot host: the floor is a
 * corpus-level statistic measured against ONE version, and the platform
 * has no place to hold it and no way to join it to a run.
 */
export function RunDetailPage({
  runId,
  initialBaseline = "",
}: {
  runId: string;
  /** Pre-selected when a finished job links here: a CHANGED verdict is
   *  only interpretable against a floor, so the link carries one. */
  initialBaseline?: string;
}) {
  const [baseline, setBaseline] = useState<string>(initialBaseline);
  const [onlyFailing, setOnlyFailing] = useState(true);
  const floors = useAsync(() => api.noiseFloors(), []);
  const run = useAsync(() => api.run(runId, baseline || undefined), [runId, baseline]);

  const documents: RunDocument[] = run.data?.documents ?? [];
  const shown = onlyFailing ? documents.filter((d) => d.gate === "FAIL") : documents;
  const explained = documents.filter((d) => d.rests_on_noise);

  return (
    <>
      <div className="spread">
        <div>
          <h1 className="mono">{runId.slice(0, 16)}…</h1>
          <p className="lede" style={{ marginBottom: 12 }}>
            Recorded {when(run.data?.recorded_at)}. {documents.length} documents,{" "}
            {documents.filter((d) => d.gate === "FAIL").length} failing.
          </p>
        </div>
        {run.data && <Gate value={run.data.gate} />}
      </div>

      {run.data && run.data.gate === "INCOMPLETE" && (
        <div className="panel fail">
          <strong>This run did not finish, so it is not a pass.</strong>{" "}
          {run.data.status === "aborted"
            ? `It aborted (${run.data.abort_reason ?? "reason not recorded"}); the documents below are only those classified before the abort.`
            : "Its artifact predates completeness recording, so it cannot say whether every document was measured."}
        </div>
      )}

      <div className="panel">
        <div className="row">
          <div>
            <label htmlFor="baseline">Read against noise floor</label>
            <select id="baseline" value={baseline} onChange={(e) => setBaseline(e.target.value)}>
              <option value="">— none (verdicts only) —</option>
              {(floors.data?.noise_floors ?? []).map((f) => (
                <option key={f.name} value={f.name}>
                  {f.name}
                  {f.field_instability_rate != null && ` — ${(f.field_instability_rate * 100).toFixed(1)}% floor`}
                </option>
              ))}
            </select>
          </div>
          <label className="row small" style={{ marginTop: 16, gap: 6 }}>
            <input type="checkbox" checked={onlyFailing} onChange={(e) => setOnlyFailing(e.target.checked)} />
            Only gate-failing documents
          </label>
        </div>
        {!floors.loading && !(floors.data?.noise_floors ?? []).length && (
          <p className="small muted" style={{ marginBottom: 0, marginTop: 10 }}>
            No floor reports yet. Run <code>scripts/noise_floor.py</code> first — without one, a red
            build cannot be told from the extractor disagreeing with itself.
          </p>
        )}
      </div>

      {run.loading && <Loading />}
      {run.error && <ErrorBox message={run.error} />}

      {baseline && explained.length > 0 && (
        <div className="panel warn">
          <strong>
            {explained.length} failing document{explained.length === 1 ? "" : "s"} rest
            {explained.length === 1 ? "s" : ""} entirely on fields the floor explains.
          </strong>
          <p className="small muted" style={{ marginBottom: 0 }}>
            Every cell their gate fails on is <em>within</em> the floor. That is an explanation, not a
            pass — the exit code is still the gate's (INV-08), and a failing cell the floor cannot
            speak to keeps its failure real.
          </p>
        </div>
      )}

      {baseline && run.data && Object.keys(run.data.field_comparison).length > 0 && (
        <>
          <h2>Fields against the floor</h2>
          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Field</th>
                  <th style={{ textAlign: "right" }}>This run</th>
                  <th style={{ textAlign: "right" }}>Floor</th>
                  <th style={{ textAlign: "right" }}>Floor obs.</th>
                  <th>Status</th>
                </tr>
              </thead>
              <tbody>
                {Object.entries(run.data.field_comparison)
                  .sort(([, a], [, b]) => rank(b.status) - rank(a.status) || b.run_rate - a.run_rate)
                  .map(([field, cmp]) => (
                    <tr key={field}>
                      <td className="mono">{field}</td>
                      <td className="num">
                        {(cmp.run_rate * 100).toFixed(1)}%{" "}
                        <span className="muted">
                          ({cmp.run_disagreements}/{cmp.run_observations})
                        </span>
                      </td>
                      <td className="num">{cmp.floor_rate == null ? "—" : `${(cmp.floor_rate * 100).toFixed(1)}%`}</td>
                      <td className="num muted">{cmp.floor_observations || "—"}</td>
                      <td>
                        <Floor status={cmp.status} />
                      </td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      <h2>Documents</h2>
      {shown.map((doc) => (
        <DocumentPanel key={doc.document_id} doc={doc} showFloor={Boolean(baseline)} />
      ))}
      {!shown.length && !run.loading && (
        <div className="panel muted">
          {onlyFailing ? "No gate-failing documents in this run." : "No documents in this run."}
        </div>
      )}
    </>
  );
}

function rank(status: string): number {
  return { above: 3, no_floor: 2, unknown: 1, within: 0 }[status] ?? 0;
}

function DocumentPanel({ doc, showFloor }: { doc: RunDocument; showFloor: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const leaves = expanded ? doc.leaves : doc.leaves.filter((l) => l.verdict !== "match");

  return (
    <div className={`panel ${doc.gate === "FAIL" ? "fail" : ""}`}>
      <div className="spread">
        <div>
          <strong className="mono">{doc.document_id}</strong>
          <div className="row small" style={{ marginTop: 4 }}>
            <Counts verdicts={doc.verdicts} />
            {doc.rests_on_noise && (
              <span title="Every cell this document's gate fails on is within the floor.">
                <Badge kind="warn">red rests on noise</Badge>
              </span>
            )}
          </div>
        </div>
        <div className="row">
          <Gate value={doc.gate} />
          <button onClick={() => setExpanded((v) => !v)}>
            {expanded ? "Differences only" : `All ${doc.leaves.length} fields`}
          </button>
        </div>
      </div>

      {leaves.length > 0 ? (
        <table style={{ marginTop: 10 }}>
          <thead>
            <tr>
              <th>Field</th>
              <th>Verdict</th>
              <th>Expected</th>
              <th>Actual</th>
              <th style={{ textAlign: "right" }}>Conf.</th>
              <th>Gate</th>
              {showFloor && <th>Floor</th>}
            </tr>
          </thead>
          <tbody>
            {leaves.map((leaf) => (
              <tr key={leaf.label}>
                <td className="mono">
                  {leaf.label}
                  {leaf.type && <span className="muted small"> · {leaf.type}</span>}
                </td>
                <td>
                  <VerdictText value={leaf.verdict} />
                </td>
                <td className="val">{leaf.expected ?? <span className="muted">—</span>}</td>
                <td className="val">{leaf.actual ?? <span className="muted">—</span>}</td>
                <td className="num">{leaf.confidence == null ? "—" : leaf.confidence.toFixed(2)}</td>
                <td>
                  {leaf.gate_failing ? (
                    <Badge kind="fail">fails</Badge>
                  ) : leaf.critical ? (
                    <Badge kind="muted">critical</Badge>
                  ) : (
                    <span className="muted small">not gated</span>
                  )}
                </td>
                {showFloor && (
                  <td>
                    <Floor status={leaf.floor_status} />
                  </td>
                )}
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <p className="muted small" style={{ marginBottom: 0, marginTop: 8 }}>
          Every field matched.
        </p>
      )}
    </div>
  );
}
