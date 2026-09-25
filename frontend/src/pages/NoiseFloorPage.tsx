import { useState } from "react";
import { api } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading, when } from "../components";

/** The floor itself: how much the extractor disagrees with ITSELF. */
export function NoiseFloorPage() {
  const list = useAsync(() => api.noiseFloors(), []);
  const [selected, setSelected] = useState<string | null>(null);
  const detail = useAsync(() => (selected ? api.noiseFloor(selected) : Promise.resolve(null)), [selected]);

  return (
    <>
      <h1>Noise floor</h1>
      <p className="lede">
        The incumbent extracted the same documents twice against <em>one</em> version. Everything it
        disagreed with itself about is noise, not regression. Measure this before comparing two
        versions, or the floor's coin-flips get baked into a golden set as "expected".
      </p>

      {list.loading && <Loading />}
      {list.error && <ErrorBox message={list.error} />}
      {list.data && !list.data.noise_floors.length && (
        <Empty>
          No reports in <code>.idp-regression-noise-floor/</code>. Run{" "}
          <code>scripts/noise_floor.py --plan</code> to size it first.
        </Empty>
      )}

      {list.data?.noise_floors.map((floor) => (
        <div key={floor.name} className="panel">
          <div className="spread">
            <div>
              <strong className="mono small">{floor.name}</strong>
              <div className="small muted">
                {when(floor.generated_at)}
                {floor.action_version && ` · version ${floor.action_version}`}
                {floor.repeats && ` · ${floor.repeats} repeats`}
                {floor.documents && ` · ${floor.documents} documents`}
              </div>
            </div>
            <div className="row">
              {floor.field_instability_rate != null && (
                <Badge kind={floor.field_instability_rate > 0.02 ? "warn" : "muted"}>
                  {(floor.field_instability_rate * 100).toFixed(2)}% floor
                </Badge>
              )}
              <button onClick={() => setSelected(selected === floor.name ? null : floor.name)}>
                {selected === floor.name ? "Hide fields" : "Per field"}
              </button>
            </div>
          </div>

          {selected === floor.name && (
            <>
              {detail.loading && <Loading />}
              {detail.error && <ErrorBox message={detail.error} />}
              {detail.data?.interpretation && (
                <ul className="small muted">
                  {detail.data.interpretation.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>
              )}
              {detail.data && (
                <table style={{ marginTop: 8 }}>
                  <thead>
                    <tr>
                      <th>Field</th>
                      <th style={{ textAlign: "right" }}>Instability</th>
                      <th style={{ textAlign: "right" }}>Unstable</th>
                      <th style={{ textAlign: "right" }}>Observations</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.data.fields.map((f) => (
                      <tr key={f.field}>
                        <td className="mono">{f.field}</td>
                        <td className="num">{(f.instability_rate * 100).toFixed(2)}%</td>
                        <td className="num">{f.unstable}</td>
                        <td className="num muted">
                          {f.observations}
                          {f.observations < 20 && (
                            <span title="Below 20 observations, a rate is not reported as above or within the floor.">
                              {" "}
                              ⚠
                            </span>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </>
          )}
        </div>
      ))}
    </>
  );
}
