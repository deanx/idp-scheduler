import type { ReactNode } from "react";
import type { FloorStatus, Verdict } from "./api";

export function Badge({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge ${kind}`}>{children}</span>;
}

export function Gate({ value }: { value: "PASS" | "FAIL" | "INCOMPLETE" | "UNKNOWN" }) {
  const kind = value === "PASS" ? "pass" : value === "FAIL" ? "solid-fail" : "muted";
  return <Badge kind={kind}>{value}</Badge>;
}

export function VerdictText({ value }: { value: Verdict }) {
  return <span className={`mono v-${value}`}>{value}</span>;
}

/**
 * The floor label, in the words `show_run.py` uses.
 *
 * `above` is deliberately the alarming one and `within` deliberately is
 * NOT a pass — the floor explains a red, it never clears one, and the
 * exit code stays the gate's (INV-08). The tooltips say so, because a
 * green-looking "within floor" chip is exactly how that guarantee gets
 * misread by someone who never opened the ADR.
 */
export function Floor({ status }: { status: FloorStatus }) {
  if (!status) return null;
  const map: Record<string, [string, string, string]> = {
    above: ["fail", "above floor", "More disagreements than the extractor's own variance predicts. This is the signal."],
    within: ["warn", "within floor", "No more than the floor predicts. Explains the red — does NOT clear it; the gate still failed."],
    unknown: ["muted", "too few to tell", "Too few observations on one side to say either way."],
    no_floor: ["muted", "no floor", "This field was never observed in the baseline sample. An absence, not a pass."],
  };
  const [kind, label, title] = map[status] ?? ["muted", status, ""];
  return (
    <span title={title}>
      <Badge kind={kind}>{label}</Badge>
    </span>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Loading() {
  return <div className="empty">Loading…</div>;
}

export function ErrorBox({ message }: { message: string }) {
  return (
    <div className="panel fail">
      <div className="error">{message}</div>
    </div>
  );
}

export function Counts({ verdicts }: { verdicts: Record<string, number> }) {
  const entries = Object.entries(verdicts).filter(([, n]) => n > 0);
  if (!entries.length) return <span className="muted">—</span>;
  return (
    <span className="row" style={{ gap: 8 }}>
      {entries.map(([verdict, count]) => (
        <span key={verdict} className="mono small">
          <span className={`v-${verdict}`}>{verdict}</span> {count}
        </span>
      ))}
    </span>
  );
}

export function when(date?: string) {
  if (!date) return "—";
  return new Date(date).toLocaleString();
}
