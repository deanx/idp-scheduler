import { api } from "../api";
import { useAsync } from "../hooks";
import { Counts, Empty, ErrorBox, Gate, Loading, when } from "../components";

export function RunsPage({ navigate }: { navigate: (to: string) => void }) {
  const { data, error, loading } = useAsync(() => api.runs(), []);

  return (
    <>
      <h1>Runs</h1>
      <p className="lede">
        Every run artifact on this machine, newest first. The gate shown is{" "}
        <code>overall_gate</code>'s — the same function the run itself used, never re-derived
        from verdict words. Open one to read it against a noise floor.
      </p>
      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data && !data.runs.length && (
        <Empty>
          No artifacts in <code>.idp-regression-run-artifacts/</code>. Has a run completed?
        </Empty>
      )}
      {data && data.runs.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Run</th>
                <th>Recorded</th>
                <th style={{ textAlign: "right" }}>Docs</th>
                <th style={{ textAlign: "right" }}>Failing</th>
                <th>Gate</th>
                <th>Verdicts</th>
              </tr>
            </thead>
            <tbody>
              {data.runs.map((run) => (
                <tr key={run.run_id} style={{ cursor: run.error ? "default" : "pointer" }}>
                  <td>
                    {run.error ? (
                      <span className="mono muted">{run.run_id}</span>
                    ) : (
                      <a href={`#/runs/${run.run_id}`} onClick={() => navigate(`/runs/${run.run_id}`)} className="mono">
                        {run.run_id.slice(0, 12)}…
                      </a>
                    )}
                    {run.error && <div className="error small">{run.error}</div>}
                  </td>
                  <td className="small muted">{when(run.recorded_at)}</td>
                  <td className="num">{run.documents ?? "—"}</td>
                  <td className="num">{run.failing_documents ?? "—"}</td>
                  <td>{run.gate ? <Gate value={run.gate} /> : null}</td>
                  <td>{run.verdicts ? <Counts verdicts={run.verdicts} /> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
