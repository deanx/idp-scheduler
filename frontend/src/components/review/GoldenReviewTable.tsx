/**
 * GoldenReviewTable + EditDocumentModal — T-02.3.2 / T-02.3.7 (AC2, AC3)
 *
 * Shows the drafted golden dataset per document with full field values,
 * fetched from GET /api/reviews/{session_id}/values (CT-07).
 *
 * ### Why /values, not the identity-only platform dataset listing
 * insights.dataset_items() deliberately strips `expectedOutput` — it is
 * identity-only by design for dashboards. /values is a separate read-only
 * endpoint that sources values from the platform (not the local pin store,
 * which goes stale after any edit). The platform is the authoritative golden
 * source — it is what verify_document.py and INV-09(e) measure against.
 *
 * ### Editing a document
 * PATCH semantics are WHOLE-ITEM REPLACE: every field must be sent in the
 * body or it will be dropped from the platform item. The EditModal is
 * pre-populated with the COMPLETE current entry for that document (all fields
 * from /values) so a one-field correction does not silently drop the others.
 *
 * INV-02: this component renders field values on screen (that is its purpose
 * and the same disclosure posture as run artifacts); it NEVER logs or persists
 * a field value.
 */

import { useState } from "react";
import { api } from "../../api";
import type {
  FieldType,
  GoldenEntry,
  GoldenField,
  ReviewDocumentValues,
  ReviewFieldValue,
  ReviewSession,
} from "../../api";
import { Badge, ErrorBox, Loading } from "../../components";
import { useAsync } from "../../hooks";
import { EditableValueCell, validateFieldValue } from "./EditableValueCell";

// ---------------------------------------------------------------- types

interface FieldRow {
  name: string;
  type: FieldType;
  value: string;
  critical: boolean;
}

function toFieldRow(f: ReviewFieldValue): FieldRow {
  return { name: f.name, type: f.type, value: f.value, critical: f.critical };
}

function makeEmptyField(): FieldRow {
  return { name: "", type: "text", value: "", critical: false };
}

// ---------------------------------------------------------------- modal

interface EditModalProps {
  documentId: string;
  sessionId: string;
  /** Pre-populated from /values so PATCH sends the complete item. */
  initialFields: FieldRow[];
  onSaved: (session: ReviewSession) => void;
  onClose: () => void;
}

function EditModal({
  documentId,
  sessionId,
  initialFields,
  onSaved,
  onClose,
}: EditModalProps) {
  const [fields, setFields] = useState<FieldRow[]>(
    initialFields.length > 0 ? initialFields : [makeEmptyField()],
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState(false);

  const updateField = (i: number, patch: Partial<FieldRow>) => {
    setFields((prev) => prev.map((f, idx) => (idx === i ? { ...f, ...patch } : f)));
  };

  const removeField = (i: number) => {
    setFields((prev) => prev.filter((_, idx) => idx !== i));
  };

  const canSubmit = fields.every(
    (f) => f.name.trim() !== "" && validateFieldValue(f.value, f.type) === null,
  );

  const handleSubmit = async () => {
    if (!canSubmit) return;
    setError(null);
    setBusy(true);
    try {
      const golden: Record<string, GoldenField> = {};
      for (const f of fields) {
        golden[f.name.trim()] = { value: f.value, type: f.type, critical: f.critical };
      }
      const entry: GoldenEntry = { document_id: documentId, fields: golden };
      const updated = await api.reviewPatchItem(sessionId, documentId, entry);
      setSaved(true);
      onSaved(updated);
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label={`Edit golden entry for ${documentId}`}
      style={{
        position: "fixed",
        inset: 0,
        background: "rgba(0,0,0,0.6)",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        zIndex: 100,
      }}
    >
      <div
        className="panel"
        style={{ width: "min(700px, 96vw)", maxHeight: "85vh", overflowY: "auto", padding: 20 }}
      >
        <div className="spread" style={{ marginBottom: 12 }}>
          <h3 style={{ margin: 0 }}>Edit golden entry — {documentId}</h3>
          <button onClick={onClose}>Close</button>
        </div>

        <p className="small muted">
          This is a <strong>whole-item replace</strong>: every field listed here
          is sent as the complete golden entry. Fields removed from this list will
          be dropped from the platform item. The form is pre-populated with the
          current values — make only the corrections you intend.
        </p>

        {saved && (
          <p className="small" style={{ color: "var(--pass)" }}>
            Saved. The session provenance has been updated.
          </p>
        )}

        <table style={{ width: "100%", borderCollapse: "collapse", marginBottom: 12 }}>
          <thead>
            <tr>
              {(["Field name", "Type", "Value", "Critical", ""] as const).map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: "left",
                    padding: "4px 6px",
                    borderBottom: "1px solid var(--line)",
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {fields.map((f, i) => (
              <tr key={i}>
                <td style={{ padding: "4px 6px" }}>
                  <input
                    value={f.name}
                    placeholder="field_name"
                    style={{ width: "100%", fontFamily: "var(--mono)", fontSize: 12 }}
                    aria-label={`field name row ${i + 1}`}
                    onChange={(e) => updateField(i, { name: e.target.value })}
                  />
                </td>
                <td style={{ padding: "4px 6px" }}>
                  <select
                    value={f.type}
                    aria-label={`field type row ${i + 1}`}
                    onChange={(e) => updateField(i, { type: e.target.value as FieldType })}
                  >
                    <option value="text">text</option>
                    <option value="number">number</option>
                    <option value="date">date</option>
                    <option value="id">id</option>
                  </select>
                </td>
                <td style={{ padding: "4px 6px", minWidth: 160 }}>
                  <EditableValueCell
                    fieldName={`row-${i}`}
                    value={f.value}
                    fieldType={f.type}
                    onChange={(v) => updateField(i, { value: v })}
                    disabled={busy}
                  />
                </td>
                <td style={{ padding: "4px 6px", textAlign: "center" }}>
                  <input
                    type="checkbox"
                    checked={f.critical}
                    aria-label={`critical row ${i + 1}`}
                    onChange={(e) => updateField(i, { critical: e.target.checked })}
                  />
                </td>
                <td style={{ padding: "4px 6px" }}>
                  <button
                    aria-label={`remove field row ${i + 1}`}
                    disabled={fields.length === 1}
                    style={{ padding: "2px 6px" }}
                    onClick={() => removeField(i)}
                  >
                    ✕
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>

        <button
          style={{ marginBottom: 12 }}
          onClick={() => setFields((prev) => [...prev, makeEmptyField()])}
        >
          + Add field
        </button>

        {error && <ErrorBox message={error} />}

        <div className="row" style={{ marginTop: 8 }}>
          <button
            className="primary"
            disabled={!canSubmit || busy}
            onClick={() => void handleSubmit()}
          >
            {busy ? "Saving…" : "Save (whole-item replace)"}
          </button>
          <button onClick={onClose}>Cancel</button>
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- document row

interface DocumentRowProps {
  doc: ReviewDocumentValues;
  sessionId: string;
  canEdit: boolean;
  onSaved: (session: ReviewSession) => void;
}

function DocumentRow({ doc, sessionId, canEdit, onSaved }: DocumentRowProps) {
  const [editing, setEditing] = useState(false);

  const editedCount = doc.fields.filter((f) => f.provenance === "edited").length;
  const provenance =
    editedCount > 0
      ? { label: `edited (${editedCount} field${editedCount === 1 ? "" : "s"})`, kind: "warn" }
      : { label: "drafted", kind: "muted" };

  return (
    <>
      {/* Document header row */}
      <tr style={{ borderTop: "1px solid var(--line)" }}>
        <td
          className="mono small"
          colSpan={2}
          style={{ padding: "6px 8px", fontWeight: 600 }}
        >
          {doc.document_id}
        </td>
        <td style={{ padding: "6px 8px" }}>
          <Badge kind={provenance.kind}>{provenance.label}</Badge>
        </td>
        <td style={{ padding: "6px 8px" }}>
          {canEdit && (
            <button
              style={{ padding: "2px 8px", fontSize: 12 }}
              aria-label={`edit fields for ${doc.document_id}`}
              onClick={() => setEditing(true)}
            >
              Edit fields
            </button>
          )}
        </td>
      </tr>
      {/* Field rows */}
      {doc.fields.map((f, i) => (
        <tr
          key={i}
          style={{ background: f.provenance === "edited" ? "var(--warn-bg, rgba(255,200,0,0.05))" : undefined }}
        >
          <td className="mono small muted" style={{ padding: "3px 8px 3px 20px" }}>
            {f.name}
          </td>
          <td className="small" style={{ padding: "3px 8px" }}>
            {f.value || <span className="muted">—</span>}
          </td>
          <td className="small muted" style={{ padding: "3px 8px" }}>
            <span className="chip">{f.type}</span>
            {f.critical && <span className="chip" style={{ marginLeft: 4 }}>critical</span>}
            {f.provenance === "edited" && (
              <span style={{ marginLeft: 4 }}><Badge kind="warn">edited</Badge></span>
            )}
          </td>
          <td />
        </tr>
      ))}
      {doc.fields.length === 0 && (
        <tr>
          <td colSpan={4} className="small muted" style={{ padding: "3px 8px 3px 20px" }}>
            No fields in this document&apos;s golden entry.
          </td>
        </tr>
      )}
      {editing && (
        <EditModal
          documentId={doc.document_id}
          sessionId={sessionId}
          initialFields={doc.fields.map(toFieldRow)}
          onSaved={(sess) => {
            onSaved(sess);
            setEditing(false);
          }}
          onClose={() => setEditing(false)}
        />
      )}
    </>
  );
}

// ---------------------------------------------------------------- public component

export interface GoldenReviewTableProps {
  session: ReviewSession;
  onSessionUpdated: (session: ReviewSession) => void;
}

export function GoldenReviewTable({ session, onSessionUpdated }: GoldenReviewTableProps) {
  const canEdit = session.state === "drafted" || session.state === "replace_failed";

  // Fetch full field values from the new /values endpoint (CT-07).
  // Sourced from the platform — the authoritative, post-edit-accurate source.
  const valuesAsync = useAsync(
    () => api.reviewValues(session.session_id),
    [session.session_id],
  );

  if (valuesAsync.loading) return <Loading />;

  if (valuesAsync.error) {
    return (
      <div>
        <ErrorBox
          message={`Could not load the drafted field values for session '${session.session_id}': ${valuesAsync.error}. Ensure the evaluation platform is reachable and configured.`}
        />
        <p className="small muted">
          You can still use the whole-file replacement below to correct the golden set
          without reading back the current values.
        </p>
      </div>
    );
  }

  const valuesData = valuesAsync.data;
  const documents = valuesData?.documents ?? [];

  if (documents.length === 0) {
    return (
      <ErrorBox
        message={`Dataset '${session.dataset}' has no items on the platform. This should not be reachable — stage 1 refuses an empty corpus. The review session may be corrupt or the dataset may have been deleted.`}
      />
    );
  }

  const missing = valuesData?.missing_from_platform ?? [];

  return (
    <div>
      <div className="row small muted" style={{ marginBottom: 8, gap: 10 }}>
        <span className="chip">{documents.length} documents</span>
        <span className="chip">dataset: {session.dataset}</span>
        {missing.length > 0 && (
          <span className="chip" style={{ color: "var(--fail)" }}>
            {missing.length} document{missing.length === 1 ? "" : "s"} missing from platform
          </span>
        )}
      </div>

      <div style={{ overflowX: "auto" }}>
        <table style={{ width: "100%", borderCollapse: "collapse", minWidth: 600 }}>
          <thead>
            <tr style={{ borderBottom: "1px solid var(--line)" }}>
              {(["Document / Field", "Value", "Type / Provenance", ""] as const).map((h) => (
                <th
                  key={h}
                  style={{
                    textAlign: "left",
                    padding: "6px 8px",
                    color: "var(--muted)",
                    fontWeight: 500,
                  }}
                >
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {documents.map((doc) => (
              <DocumentRow
                key={doc.document_id}
                doc={doc}
                sessionId={session.session_id}
                canEdit={canEdit}
                onSaved={onSessionUpdated}
              />
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
