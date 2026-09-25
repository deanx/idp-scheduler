import { api } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading } from "../components";

/**
 * What this workspace has spent quota on.
 *
 * Jobs used to live only in this process's memory, so a console restart
 * lost the record of a batch in flight while the extractions it spent
 * stayed spent. They are now written to the workspace (without their
 * output — that carries extracted values and already has a home in the
 * gitignored run artifact), and a job that was still running when a
 * console stopped is surfaced as INTERRUPTED.
 *
 * It is never resumed automatically: the extractions are gone either
 * way, and restarting would spend them again.
 */
export function JobsPage() {
  const { data, error, loading, reload } = useAsync(() => api.jobs(), []);
  const busy = useAsync(() => api.jobsBusy(), []);

  const kindOf = (verdict?: string, status?: string) =>
    verdict === "STILL VALID" || status === "succeeded"
      ? "pass"
      : verdict === "CHANGED"
        ? "fail"
        : verdict === "INTERRUPTED"
          ? "warn"
          : status === "running"
            ? "info"
            : "muted";

  return (
    <>
      <div className="spread">
        <div>
          <h1>Jobs</h1>
          <p className="lede">
            Every validation and noise-floor run this workspace has started, including ones from
            earlier console processes. Output is deliberately not kept here — open the run for the
            per-field detail.
          </p>
        </div>
        <button onClick={reload}>Refresh</button>
      </div>

      {busy.data?.busy && (
        <div className="panel warn">
          <strong>A quota-spending job is holding this workspace.</strong>
          <p className="small muted" style={{ marginBottom: 0 }}>
            One at a time, by design: two batches against the same pin store can interleave, and the
            second would pin goldens the first is still verifying against.
          </p>
        </div>
      )}

      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data && !data.jobs.length && <Empty>No jobs have run in this workspace.</Empty>}

      {data && data.jobs.length > 0 && (
        <div className="panel" style={{ padding: 0 }}>
          <table>
            <thead>
              <tr>
                <th>Job</th>
                <th>Kind</th>
                <th>Outcome</th>
                <th style={{ textAlign: "right" }}>Extractions</th>
                <th>Result</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job) => (
                <tr key={job.id}>
                  <td className="mono small">{job.id.slice(0, 12)}…</td>
                  <td className="small">{job.kind}</td>
                  <td>
                    <Badge kind={kindOf(job.summary?.verdict, job.status)}>
                      {job.summary?.verdict ?? job.status}
                    </Badge>
                    {job.exit_code != null && (
                      <span className="muted small"> exit {job.exit_code}</span>
                    )}
                  </td>
                  <td className="num">
                    {job.planned_extractions ?? "—"}
                    <div className="muted small">approved</div>
                  </td>
                  <td className="small">
                    {job.summary?.run_id ? (
                      <a
                        href={
                          `#/runs/${job.summary.run_id}` +
                          (job.summary.floor_report
                            ? `?baseline=${encodeURIComponent(job.summary.floor_report)}`
                            : "")
                        }
                      >
                        {job.summary.changed_documents ?? 0} of {job.summary.documents ?? 0}{" "}
                        changed →
                      </a>
                    ) : job.summary?.floor_report ? (
                      <a href="#/noise-floor">floor measured →</a>
                    ) : (
                      <span className="muted">
                        {job.summary?.verdict === "INTERRUPTED"
                          ? "interrupted by a console restart; not resumed"
                          : "—"}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="small muted">
        Approved counts are what the plan priced and the operator confirmed. A cancelled job does
        not refund what it had already extracted.
      </p>
    </>
  );
}
