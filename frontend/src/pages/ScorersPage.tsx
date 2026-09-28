import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import type { Outcome, Rule, Scorer, Vocabulary } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading } from "../components";

/**
 * Author a scorer as DATA, not code.
 *
 * `registry.py`'s rule — registered names only, never an importable path
 * from a flag — applies with more force to a browser form than to a CLI
 * argument, so a spec is a closed vocabulary of `when → then` rules with
 * no `eval` anywhere in the path. And because a spec is further from
 * review than Python is, it is held to the stronger form of the
 * escalation rule: it may tighten a verdict, never relax one to `match`,
 * and it may set `critical`, never clear it. The backend proves that per
 * spec before it saves.
 */
export function ScorersPage() {
  const { data, error, loading, reload } = useAsync(() => api.scorers(), []);
  const [editing, setEditing] = useState<Scorer | "new" | null>(null);

  return (
    <>
      <div className="spread">
        <div>
          <h1>Scorers</h1>
          <p className="lede">
            A run names a comparison strategy; it never imports one. These are the names{" "}
            <code>--classifier</code> accepts.
          </p>
        </div>
        <button className="primary" onClick={() => setEditing("new")}>
          New scorer
        </button>
      </div>

      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data?.errors.map((message) => (
        <div key={message} className="panel fail">
          <div className="error small">Spec could not be loaded — {message}</div>
        </div>
      ))}

      {editing && data && (
        <ScorerEditor
          vocabulary={data.vocabulary}
          initial={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            reload();
          }}
        />
      )}

      {data?.scorers.map((scorer) => (
        <div key={scorer.name} className="panel">
          <div className="spread">
            <div>
              <div className="row">
                <strong className="mono">{scorer.name}</strong>
                <Badge kind={scorer.kind === "shipped" ? "muted" : "info"}>{scorer.kind}</Badge>
                {scorer.is_default && <Badge kind="pass">default</Badge>}
                {scorer.base && <span className="chip">base: {scorer.base}</span>}
              </div>
              <p className="small muted" style={{ margin: "6px 0 0", maxWidth: "70ch" }}>
                {scorer.description}
              </p>
            </div>
            {scorer.kind === "custom" && (
              <div className="row">
                <button onClick={() => setEditing(scorer)}>Edit</button>
                <button
                  className="danger"
                  onClick={async () => {
                    await api.deleteScorer(scorer.name);
                    reload();
                  }}
                >
                  Delete
                </button>
              </div>
            )}
          </div>

          {scorer.rules && (
            <div style={{ marginTop: 10 }}>
              {scorer.rules.map((rule, index) => (
                <div key={index} className="small mono" style={{ padding: "3px 0" }}>
                  <span className="muted">when</span> {describe(rule.when)}{" "}
                  <span className="muted">→</span> <span className="v-wrong_value">{describe(rule.then)}</span>
                </div>
              ))}
            </div>
          )}

          <pre className="command">{`--classifier ${scorer.name}`}</pre>
        </div>
      ))}

      {data && !data.scorers.length && <Empty>No scorers registered.</Empty>}

      {data && (
        <p className="small muted">
          Custom specs live in <code>{data.scorer_dir}</code>. They hold rules, never extracted
          values — safe to commit, and committing them is what makes a custom gate reviewable.
        </p>
      )}
    </>
  );
}

function describe(clause: Record<string, unknown>): string {
  return Object.entries(clause)
    .map(([key, value]) => `${key}=${Array.isArray(value) ? `[${value.join("|")}]` : String(value)}`)
    .join(" and ");
}

const EMPTY_RULE: Rule = { when: {}, then: {} };

function ScorerEditor({
  vocabulary,
  initial,
  onClose,
  onSaved,
}: {
  vocabulary: Vocabulary;
  initial: Scorer | null;
  onClose: () => void;
  onSaved: () => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [description, setDescription] = useState(initial?.description ?? "");
  const [base, setBase] = useState(initial?.base ?? "regression");
  const [rules, setRules] = useState<Rule[]>(initial?.rules?.length ? initial.rules : [EMPTY_RULE]);
  const [validation, setValidation] = useState<{ valid: boolean; errors: string[] } | null>(null);
  const [preview, setPreview] = useState<{ sample: Record<string, unknown>; base: Outcome; custom: Outcome }[] | null>(null);
  const [saveError, setSaveError] = useState<string | null>(null);

  const spec = useMemo(
    () => ({ name, description, base, rules: rules.filter((r) => Object.keys(r.when).length && Object.keys(r.then).length) }),
    [name, description, base, rules],
  );

  useEffect(() => {
    if (!spec.name || !spec.rules.length) {
      // eslint-disable-next-line react-hooks/set-state-in-effect
      setValidation(null);
      return;
    }
    let live = true;
    const timer = setTimeout(() => {
      api
        .validateScorer(spec)
        .then((result) => live && setValidation(result))
        .catch((exc: Error) => live && setValidation({ valid: false, errors: [exc.message] }));
    }, 250);
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [spec]);

  const update = (index: number, patch: Partial<Rule>) =>
    setRules((current) => current.map((rule, i) => (i === index ? { ...rule, ...patch } : rule)));

  return (
    <div className="panel" style={{ borderColor: "var(--accent)" }}>
      <div className="spread">
        <h2 style={{ marginTop: 0 }}>{initial ? `Edit ${initial.name}` : "New scorer"}</h2>
        <button onClick={onClose}>Close</button>
      </div>

      <div className="grid2">
        <div className="field">
          <label htmlFor="scorer-name">Name — becomes the <code>--classifier</code> value</label>
          <input
            id="scorer-name"
            value={name}
            disabled={Boolean(initial)}
            placeholder="confidence-floor"
            onChange={(e) => setName(e.target.value)}
            style={{ width: "100%" }}
          />
        </div>
        <div className="field">
          <label htmlFor="scorer-base">Base — the reviewed comparison this tightens</label>
          <select id="scorer-base" value={base} onChange={(e) => setBase(e.target.value)} style={{ width: "100%" }}>
            {vocabulary.bases.map((b) => (
              <option key={b} value={b}>
                {b}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="field">
        <label htmlFor="scorer-desc">Description</label>
        <input
          id="scorer-desc"
          value={description}
          placeholder="Fail a match IDP was not confident about."
          onChange={(e) => setDescription(e.target.value)}
          style={{ width: "100%" }}
        />
      </div>

      <h3>Rules — first match wins</h3>
      {rules.map((rule, index) => (
        <RuleEditor
          key={index}
          rule={rule}
          vocabulary={vocabulary}
          onChange={(patch) => update(index, patch)}
          onRemove={rules.length > 1 ? () => setRules((r) => r.filter((_, i) => i !== index)) : undefined}
        />
      ))}
      <button onClick={() => setRules((r) => [...r, { when: {}, then: {} }])}>Add rule</button>

      {validation && (
        <div className={`panel ${validation.valid ? "" : "fail"}`} style={{ marginTop: 14 }}>
          {validation.valid ? (
            <div className="row">
              <Badge kind="pass">valid</Badge>
              <Badge kind="pass">monotone</Badge>
              <span className="small muted">
                Proved over every context shape: this spec can only make the gate stricter.
              </span>
            </div>
          ) : (
            <ul className="error small" style={{ margin: 0, paddingLeft: 18 }}>
              {validation.errors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="row" style={{ marginTop: 14 }}>
        <button
          disabled={!validation?.valid}
          onClick={async () => {
            try {
              const result = await api.previewScorer(spec, SAMPLES);
              setPreview(result.results);
            } catch (exc) {
              setSaveError((exc as Error).message);
            }
          }}
        >
          Preview against samples
        </button>
        <button
          className="primary"
          disabled={!validation?.valid}
          onClick={async () => {
            setSaveError(null);
            try {
              await api.saveScorer(spec.name, spec);
              onSaved();
            } catch (exc) {
              setSaveError((exc as Error).message);
            }
          }}
        >
          Save
        </button>
        {saveError && <span className="error small">{saveError}</span>}
      </div>

      {preview && (
        <>
          <h3>Base vs this spec</h3>
          <p className="small muted" style={{ marginTop: -4 }}>
            A rule that changes nothing looks exactly like a rule that was never applied. This is
            where that becomes visible — before the spec gates a build.
          </p>
          <table>
            <thead>
              <tr>
                <th>Sample</th>
                <th>Expected</th>
                <th>Actual</th>
                <th style={{ textAlign: "right" }}>Conf.</th>
                <th>{base}</th>
                <th>this spec</th>
              </tr>
            </thead>
            <tbody>
              {preview.map((row, index) => {
                const changed =
                  row.base.verdict !== row.custom.verdict || row.base.critical !== row.custom.critical;
                return (
                  <tr key={index}>
                    <td className="mono small">{String(row.sample.name)}</td>
                    <td className="val">{String(row.sample.expected ?? "—")}</td>
                    <td className="val">{String(row.sample.actual ?? "—")}</td>
                    <td className="num">{row.sample.confidence == null ? "—" : String(row.sample.confidence)}</td>
                    <td>
                      <OutcomeCell outcome={row.base} />
                    </td>
                    <td style={changed ? { background: "color-mix(in srgb, var(--warn) 14%, transparent)" } : undefined}>
                      <OutcomeCell outcome={row.custom} />
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </>
      )}
    </div>
  );
}

function OutcomeCell({ outcome }: { outcome: Outcome }) {
  return (
    <span className="row" style={{ gap: 6 }}>
      <span className={`mono v-${outcome.verdict}`}>{outcome.verdict}</span>
      {outcome.critical && <Badge kind="fail">critical</Badge>}
      {outcome.format_critical && <Badge kind="warn">format</Badge>}
    </span>
  );
}

/** Deliberately covers agreement, disagreement, emptiness and low confidence. */
const SAMPLES = [
  { name: "total", field_type: "number", expected: "1250.00", actual: "1,250.00", confidence: 0.99 },
  { name: "total", field_type: "number", expected: "1250.00", actual: "1250.00", confidence: 0.42 },
  { name: "total", field_type: "number", expected: "1250.00", actual: "1350.00", confidence: 0.98 },
  { name: "invoice_date", field_type: "date", expected: "2024-06-28", actual: "28/06/2024", confidence: 0.9 },
  { name: "po_number", field_type: "id", expected: "PO-77", actual: "", confidence: null },
  { name: "po_number", field_type: "id", expected: "", actual: "PO-99", confidence: 0.3 },
];

function RuleEditor({
  rule,
  vocabulary,
  onChange,
  onRemove,
}: {
  rule: Rule;
  vocabulary: Vocabulary;
  onChange: (patch: Partial<Rule>) => void;
  onRemove?: () => void;
}) {
  const setWhen = (key: string, value: unknown) => {
    const when = { ...rule.when };
    if (value === "" || value === undefined) delete when[key];
    else when[key] = value;
    onChange({ when });
  };
  const setThen = (key: string, value: unknown) => {
    const then = { ...rule.then };
    if (value === "" || value === undefined || value === false) delete then[key];
    else then[key] = value;
    onChange({ then });
  };

  return (
    <div className="rule">
      <div className="rule-head">
        <strong className="small">WHEN all of…</strong>
        {onRemove && (
          <button className="danger small" onClick={onRemove}>
            Remove
          </button>
        )}
      </div>

      <div className="grid2">
        <div className="field">
          <label>base verdict is one of</label>
          <div className="row" style={{ gap: 6 }}>
            {vocabulary.verdicts.map((verdict) => {
              const list = (rule.when.verdict_is as string[] | undefined) ?? [];
              const on = list.includes(verdict);
              return (
                <button
                  key={verdict}
                  className={on ? "primary" : ""}
                  style={{ padding: "2px 8px", fontSize: 11.5 }}
                  onClick={() => {
                    const next = on ? list.filter((v) => v !== verdict) : [...list, verdict];
                    setWhen("verdict_is", next.length ? next : "");
                  }}
                >
                  {verdict}
                </button>
              );
            })}
          </div>
        </div>

        <div className="field">
          <label htmlFor="conf">confidence below (0–1)</label>
          <input
            id="conf"
            type="number"
            min={0}
            max={1}
            step={0.05}
            value={(rule.when.confidence_below as number | undefined) ?? ""}
            onChange={(e) => setWhen("confidence_below", e.target.value === "" ? "" : Number(e.target.value))}
          />
          <span className="small muted">A missing confidence is not "below" — use its own condition.</span>
        </div>

        <div className="field">
          <label htmlFor="ftype">field type</label>
          <select id="ftype" value={(rule.when.field_type as string) ?? ""} onChange={(e) => setWhen("field_type", e.target.value)}>
            <option value="">any</option>
            {vocabulary.field_types.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="kind">kind</label>
          <select id="kind" value={(rule.when.kind as string) ?? ""} onChange={(e) => setWhen("kind", e.target.value)}>
            <option value="">any</option>
            {vocabulary.kinds.map((k) => (
              <option key={k} value={k}>
                {k}
              </option>
            ))}
          </select>
        </div>

        <div className="field">
          <label htmlFor="names">field names (comma separated)</label>
          <input
            id="names"
            placeholder="total, subtotal"
            value={((rule.when.name_in as string[] | undefined) ?? []).join(", ")}
            onChange={(e) => {
              const list = e.target.value.split(",").map((s) => s.trim()).filter(Boolean);
              setWhen("name_in", list.length ? list : "");
            }}
            style={{ width: "100%" }}
          />
        </div>

        <div className="field">
          <label htmlFor="regex">field name matches (regex)</label>
          <input
            id="regex"
            placeholder="^(total|tax)$"
            value={(rule.when.name_matches as string) ?? ""}
            onChange={(e) => setWhen("name_matches", e.target.value)}
            style={{ width: "100%" }}
          />
        </div>
      </div>

      <div className="row small" style={{ gap: 14, marginBottom: 10 }}>
        {(["expected_empty", "actual_empty", "confidence_missing", "critical_in_golden"] as const).map((key) => (
          <label key={key} className="row" style={{ gap: 5, margin: 0 }}>
            <input
              type="checkbox"
              checked={rule.when[key] === true}
              onChange={(e) => setWhen(key, e.target.checked ? true : "")}
            />
            {key}
          </label>
        ))}
      </div>

      <div className="rule-head" style={{ borderTop: "1px solid var(--line)", paddingTop: 10 }}>
        <strong className="small">THEN…</strong>
      </div>
      <div className="row">
        <div className="field" style={{ margin: 0 }}>
          <label htmlFor="verdict">set verdict to</label>
          <select id="verdict" value={(rule.then.verdict as string) ?? ""} onChange={(e) => setThen("verdict", e.target.value)}>
            <option value="">leave as the base decided</option>
            {vocabulary.actionable_verdicts.map((v) => (
              <option key={v} value={v}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <label className="row small" style={{ gap: 5, marginTop: 16 }}>
          <input type="checkbox" checked={rule.then.critical === true} onChange={(e) => setThen("critical", e.target.checked)} />
          make it gate-failing (critical)
        </label>
        <label className="row small" style={{ gap: 5, marginTop: 16 }}>
          <input
            type="checkbox"
            checked={rule.then.format_critical === true}
            onChange={(e) => setThen("format_critical", e.target.checked)}
          />
          format_critical
        </label>
      </div>
      <p className="small muted" style={{ margin: "8px 0 0" }}>
        <code>match</code> is not offered: a spec may tighten a verdict, never relax one. Escalation is
        one-way, so the worst a spec can do is fail a build that would otherwise have passed.
      </p>
    </div>
  );
}
