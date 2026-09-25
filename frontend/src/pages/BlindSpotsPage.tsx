import { api } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading, when } from "../components";

/**
 * What the gate is NOT looking at.
 *
 * Calibration's rules are fail-open by design: a field the noise floor
 * says is unstable, or that is absent from most of the corpus, is
 * demoted out of `critical` so it cannot fail a build. That is the right
 * call and it is also the most dangerous fact about a green run, which
 * is why it gets a page rather than a line in a generation-time log.
 */
export function BlindSpotsPage() {
  const { data, error, loading } = useAsync(() => api.blindSpots(), []);

  return (
    <>
      <h1>Gate blind spots</h1>
      <p className="lede">
        Fields calibration demoted out of <code>critical</code>. A regression in one of these does{" "}
        <strong>not</strong> fail the build. Every rule that produces this list is fail-open, so the
        list is the honest half of a green run — and the evaluation platform has nowhere to show it.
      </p>

      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data && !data.calibrations.length && (
        <Empty>
          No <code>*.calibration.json</code> reports found. Run <code>scripts/calibrate_golden.py</code>{" "}
          over a drafted golden set.
        </Empty>
      )}

      {data?.calibrations.map((cal) => (
        <div key={cal.name} className={`panel ${cal.blind_spots.length ? "warn" : ""}`}>
          <div className="spread">
            <div>
              <strong className="mono small">{cal.name}</strong>
              <div className="small muted">
                {when(cal.generated_at)}
                {cal.corpus && ` · ${cal.corpus.documents} documents, ${cal.corpus.fields} fields`}
              </div>
            </div>
            <div className="row">
              {cal.noise_floor_applied ? (
                <Badge kind="muted">floor applied</Badge>
              ) : (
                <span title="Calibration ran without a noise floor: `critical` was decided on presence alone.">
                  <Badge kind="fail">NO floor applied</Badge>
                </span>
              )}
              <Badge kind={cal.blind_spots.length ? "warn" : "pass"}>
                {cal.blind_spots.length} blind spot{cal.blind_spots.length === 1 ? "" : "s"}
              </Badge>
            </div>
          </div>

          {cal.error && <div className="error small">{cal.error}</div>}

          {cal.blind_spots.length > 0 && (
            <>
              <h3>Not gated</h3>
              <table>
                <thead>
                  <tr>
                    <th>Field</th>
                    <th>Why it was demoted</th>
                  </tr>
                </thead>
                <tbody>
                  {cal.blind_spots.map((field) => {
                    const key = field.replace(/ \(table\)$/, "");
                    const detail = cal.fields[key]?.detail ?? cal.tables[key]?.detail;
                    return (
                      <tr key={field}>
                        <td className="mono">{field}</td>
                        <td className="small muted">{detail ?? "see the calibration report"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </>
          )}

          {cal.still_human.length > 0 && (
            <>
              <h3>What calibration cannot decide</h3>
              <ul className="small muted" style={{ marginTop: 0 }}>
                {cal.still_human.map((item) => (
                  <li key={item}>{item}</li>
                ))}
              </ul>
            </>
          )}

          {cal.thresholds && (
            <div className="row small muted" style={{ marginTop: 10 }}>
              {Object.entries(cal.thresholds).map(([key, value]) => (
                <span key={key} className="chip">
                  {key} = {value}
                </span>
              ))}
            </div>
          )}
        </div>
      ))}
    </>
  );
}
