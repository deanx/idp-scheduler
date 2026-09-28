/**
 * ReviewsPage — #/reviews — T-02.3.5 (AC6)
 *
 * Lists all review sessions in this workspace so the curator can see what
 * is waiting for their action. No delete/expiry action is offered —
 * cleanup UX is deferred per ADR-0008 "Open items for /plan".
 */

import type { ReviewSession, ReviewSessionState } from "../api";
import { api } from "../api";
import { Badge, Empty, ErrorBox, Loading, when } from "../components";
import { useAsync } from "../hooks";

// ---------------------------------------------------------------- row

const STATE_LABEL: Record<ReviewSessionState, string> = {
  drafted: "awaiting review",
  reviewed: "reviewed — ready for stage 2",
  verifying: "verifying…",
  verified: "verified",
  stale: "stale (golden changed)",
  replace_failed: "replace failed — needs repair",
};

const STATE_KIND: Record<ReviewSessionState, string> = {
  drafted: "warn",
  reviewed: "info",
  verifying: "info",
  verified: "pass",
  stale: "fail",
  replace_failed: "fail",
};

export function ReviewSessionRow({ session }: { session: ReviewSession }) {
  return (
    <tr>
      <td style={{ padding: "8px 10px" }}>
        <a href={`#/validate/review/${session.session_id}`} className="mono small">
          {session.dataset}
        </a>
      </td>
      <td className="small muted" style={{ padding: "8px 10px" }}>
        {session.action_id}
      </td>
      <td className="small mono" style={{ padding: "8px 10px" }}>
        {session.trusted_version} → {session.candidate_version}
      </td>
      <td style={{ padding: "8px 10px" }}>
        <Badge kind={STATE_KIND[session.state]}>{STATE_LABEL[session.state]}</Badge>
      </td>
      <td className="small muted" style={{ padding: "8px 10px" }}>
        {when(session.created_at)}
      </td>
      <td style={{ padding: "8px 10px" }}>
        <a href={`#/validate/review/${session.session_id}`}>Open →</a>
      </td>
    </tr>
  );
}

// ---------------------------------------------------------------- page

export function ReviewsPage() {
  const sessions = useAsync(() => api.reviews(), []);

  return (
    <>
      <h1>Pending reviews</h1>
      <p className="lede">
        Golden datasets drafted in the two-stage wizard and waiting for curator action.
        A session here means IDP quota was spent to draft the golden and verification
        has not yet been approved.
      </p>

      {sessions.loading && <Loading />}
      {sessions.error && <ErrorBox message={sessions.error} />}

      {sessions.data && sessions.data.sessions.length === 0 && (
        <Empty>
          No review sessions. Start the two-stage wizard from{" "}
          <a href="#/validate">Validate a corpus</a> to create one.
        </Empty>
      )}

      {sessions.data && sessions.data.sessions.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table style={{ width: "100%", borderCollapse: "collapse" }}>
            <thead>
              <tr style={{ borderBottom: "1px solid var(--line)" }}>
                {(["Dataset", "Action", "Versions", "State", "Created", ""] as const).map(
                  (h) => (
                    <th
                      key={h}
                      style={{
                        textAlign: "left",
                        padding: "6px 10px",
                        color: "var(--muted)",
                        fontWeight: 500,
                      }}
                    >
                      {h}
                    </th>
                  ),
                )}
              </tr>
            </thead>
            <tbody>
              {sessions.data.sessions.map((s) => (
                <ReviewSessionRow key={s.session_id} session={s} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
