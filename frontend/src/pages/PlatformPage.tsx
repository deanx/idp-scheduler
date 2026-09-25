import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading } from "../components";

/**
 * Dashboards over what the evaluation platform holds.
 *
 * Read-only, and deliberately additive to the platform's own UI rather
 * than a replacement for it: the compare-runs grid, the golden editor
 * and the platform's own dashboards stay where they are. What this adds
 * is the same history rendered beside the local-only views (noise floor,
 * blind spots, pins) so an operator is not correlating two windows.
 *
 * It renders the deployment's limits as facts, not errors. This
 * `events_only` deployment has **no runs-list endpoint** — that 404 is
 * documented behaviour, and a dashboard that showed it in red would
 * teach people to ignore red.
 */
export function PlatformPage() {
  const caps = useAsync(() => api.platformCapabilities(), []);
  const [scoreName, setScoreName] = useState<string>("");
  const [days, setDays] = useState(30);
  const configured = caps.data?.configured;
  const trend = useAsync(
    () => (configured ? api.platformTrend(scoreName || undefined, days) : Promise.resolve(null)),
    [configured, scoreName, days],
  );
  const datasets = useAsync(
    () => (configured ? api.platformDatasets() : Promise.resolve(null)),
    [configured],
  );

  return (
    <>
      <h1>Platform history</h1>
      <p className="lede">
        Score history read back from the evaluation platform. Read-only — nothing here writes, and{" "}
        <strong>no value on this page feeds a gate</strong>: the gate is computed in process before
        any platform write (INV-08), so a dashboard that cannot load can never change a build.
      </p>

      {caps.loading && <Loading />}
      {caps.error && <ErrorBox message={caps.error} />}

      {caps.data && !caps.data.configured && (
        <div className="panel">
          <strong>The evaluation platform is not configured here.</strong>
          <p className="small muted" style={{ marginBottom: 0 }}>
            {caps.data.reason}. Every other page works without it — this one is the only view that
            reads the platform.
          </p>
        </div>
      )}

      {caps.data?.configured && (
        <div className="panel">
          <div className="row">
            <Badge kind={caps.data.reachable ? "pass" : "fail"}>
              {caps.data.reachable ? "reachable" : "unreachable"}
            </Badge>
            <Badge kind={caps.data.scores_v3 ? "pass" : "warn"}>
              scores v3 {caps.data.scores_v3 ? "available" : "unavailable"}
            </Badge>
            <Badge kind={caps.data.runs_list_endpoint ? "pass" : "muted"}>
              runs list {caps.data.runs_list_endpoint ? "available" : "not available"}
            </Badge>
          </div>
          {!caps.data.runs_list_endpoint && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              {caps.data.runs_list_detail} — expected on this deployment, not a fault. Run history
              below is therefore built from a bounded, filtered score poll, which is the read
              confirmed to work here.
            </p>
          )}
        </div>
      )}

      {configured && (
        <>
          <h2>Verdicts over time</h2>
          <div className="panel">
            <div className="row">
              <div className="field" style={{ margin: 0 }}>
                <label htmlFor="score-name">Score name</label>
                <input
                  id="score-name"
                  value={scoreName}
                  placeholder="all scores"
                  onChange={(e) => setScoreName(e.target.value)}
                />
              </div>
              <div className="field" style={{ margin: 0 }}>
                <label htmlFor="days">Window</label>
                <select id="days" value={days} onChange={(e) => setDays(Number(e.target.value))}>
                  <option value={7}>7 days</option>
                  <option value={30}>30 days</option>
                  <option value={90}>90 days</option>
                </select>
              </div>
            </div>

            {trend.loading && <Loading />}
            {trend.error && <ErrorBox message={trend.error} />}
            {trend.data && (
              <>
                <div className="row small muted" style={{ marginTop: 10 }}>
                  <span className="chip">{trend.data.observations} scores</span>
                  {trend.data.truncated && (
                    <span title="The page was bounded; earlier scores in this window are not shown.">
                      <Badge kind="warn">bounded page — not the whole window</Badge>
                    </span>
                  )}
                </div>
                {trend.data.series.length === 0 ? (
                  <Empty>No scores in this window.</Empty>
                ) : (
                  <TrendChart series={trend.data.series} />
                )}
              </>
            )}
          </div>

          {trend.data && Object.keys(trend.data.score_names).length > 0 && (
            <>
              <h3>Score names seen</h3>
              <div className="row">
                {Object.entries(trend.data.score_names).map(([name, count]) => (
                  <button
                    key={name}
                    className={scoreName === name ? "primary" : ""}
                    style={{ padding: "2px 8px", fontSize: 11.5 }}
                    onClick={() => setScoreName(scoreName === name ? "" : name)}
                  >
                    {name} <span className="muted">{count}</span>
                  </button>
                ))}
              </div>
            </>
          )}

          <h2>Golden sets on the platform</h2>
          {datasets.loading && <Loading />}
          {datasets.error && <ErrorBox message={datasets.error} />}
          {datasets.data && <DatasetList names={datasets.data.datasets} />}
        </>
      )}
    </>
  );
}

/** A stacked bar per bucket. Inline SVG — no chart dependency for this. */
function TrendChart({ series }: { series: { bucket: string; counts: Record<string, number> }[] }) {
  const keys = [...new Set(series.flatMap((s) => Object.keys(s.counts)))].sort();
  const colour = (key: string) =>
    key === "match" || key === "PASS"
      ? "var(--pass)"
      : key === "FAIL" || key === "missing" || key === "wrong_value"
        ? "var(--fail)"
        : key === "wrong_format"
          ? "var(--warn)"
          : "var(--info)";
  const max = Math.max(...series.map((s) => Object.values(s.counts).reduce((a, b) => a + b, 0)), 1);
  const width = Math.max(series.length * 34, 260);

  return (
    <>
      <svg viewBox={`0 0 ${width} 180`} style={{ width: "100%", height: 180, marginTop: 12 }} role="img">
        {series.map((point, index) => {
          let offset = 0;
          const total = Object.values(point.counts).reduce((a, b) => a + b, 0);
          return (
            <g key={point.bucket} transform={`translate(${index * 34}, 0)`}>
              {keys
                .filter((key) => point.counts[key])
                .map((key) => {
                  const height = (point.counts[key] / max) * 140;
                  const y = 150 - offset - height;
                  offset += height;
                  return (
                    <rect key={key} x={6} y={y} width={22} height={height} fill={colour(key)} rx={2}>
                      <title>{`${point.bucket} · ${key}: ${point.counts[key]}`}</title>
                    </rect>
                  );
                })}
              <text x={17} y={166} textAnchor="middle" fontSize={8} fill="var(--muted)">
                {point.bucket.slice(5)}
              </text>
              <text x={17} y={150 - offset - 4} textAnchor="middle" fontSize={8} fill="var(--muted)">
                {total}
              </text>
            </g>
          );
        })}
      </svg>
      <div className="row small" style={{ marginTop: 6 }}>
        {keys.map((key) => (
          <span key={key} className="row" style={{ gap: 5 }}>
            <span style={{ width: 9, height: 9, background: colour(key), borderRadius: 2 }} />
            <span className="mono">{key}</span>
          </span>
        ))}
      </div>
    </>
  );
}

function DatasetList({ names }: { names: string[] }) {
  const [open, setOpen] = useState<string | null>(null);
  const items = useAsync(
    () => (open ? api.platformDataset(open) : Promise.resolve(null)),
    [open],
  );

  if (!names.length) return <Empty>No datasets on the platform.</Empty>;
  return (
    <>
      {names.map((name) => (
        <div key={name} className="panel">
          <div className="spread">
            <strong className="mono">{name}</strong>
            <button onClick={() => setOpen(open === name ? null : name)}>
              {open === name ? "Hide" : "Items"}
            </button>
          </div>
          {open === name && (
            <>
              {items.loading && <Loading />}
              {items.error && <ErrorBox message={items.error} />}
              {items.data && (
                <>
                  <p className="small muted">
                    {items.data.items_returned} shown
                    {items.data.total_items != null && ` of ${items.data.total_items}`}. Items come
                    from the paginated items endpoint — the dataset endpoint alone returns none, and
                    reading it instead is how a golden set silently looks empty.
                  </p>
                  <table>
                    <thead>
                      <tr>
                        <th>Document</th>
                        <th>Provenance</th>
                      </tr>
                    </thead>
                    <tbody>
                      {items.data.documents.map((doc) => (
                        <tr key={doc.id}>
                          <td className="mono">{doc.document_id ?? doc.id}</td>
                          <td className="small muted mono">
                            {doc.metadata ? JSON.stringify(doc.metadata) : "—"}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </>
              )}
            </>
          )}
        </div>
      ))}
    </>
  );
}
