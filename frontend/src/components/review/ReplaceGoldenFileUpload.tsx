/**
 * ReplaceGoldenFileUpload — T-02.3.3 (AC4)
 *
 * Lets the curator upload a complete golden.json to replace the entire
 * drafted set for this session. The server validates all entries via
 * provision_golden_dataset.py's existing all-or-nothing batch semantics;
 * any named validation error is rendered directly here as text — not
 * re-derived or swallowed.
 *
 * On success, the updated session dict is passed to onReplaced so the
 * parent can refresh the review state.
 */

import { useRef, useState } from "react";
import { api } from "../../api";
import type { GoldenEntry, ReviewSession } from "../../api";
import { ErrorBox } from "../../components";

export interface ReplaceGoldenFileUploadProps {
  sessionId: string;
  onReplaced: (session: ReviewSession) => void;
}

export function ReplaceGoldenFileUpload({ sessionId, onReplaced }: ReplaceGoldenFileUploadProps) {
  const input = useRef<HTMLInputElement>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [successMsg, setSuccessMsg] = useState<string | null>(null);

  const handleFile = async (file: File) => {
    setError(null);
    setSuccessMsg(null);
    setBusy(true);
    try {
      const text = await file.text();
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        setError("file is not valid JSON");
        return;
      }
      if (typeof parsed !== "object" || parsed === null || Array.isArray(parsed)) {
        setError("expected a JSON object whose keys are document IDs");
        return;
      }
      const entries = parsed as Record<string, GoldenEntry>;
      const updated = await api.reviewReplace(sessionId, entries);
      setSuccessMsg(`Replaced with ${Object.keys(entries).length} entries. Session state: ${updated.state}.`);
      onReplaced(updated);
    } catch (exc) {
      // The server names the first invalid entry in its error message —
      // render it verbatim so the curator sees exactly what is wrong.
      setError((exc as Error).message);
    } finally {
      setBusy(false);
      // Reset the file input so the same file can be re-submitted after corrections.
      if (input.current) input.current.value = "";
    }
  };

  return (
    <div style={{ marginTop: 16 }}>
      <h4 style={{ margin: "0 0 6px" }}>Replace entire golden set</h4>
      <p className="small muted" style={{ marginTop: 0 }}>
        Upload a <code>golden.json</code> whose keys are document IDs and whose
        values follow the golden schema. All entries are validated before anything
        is written — one invalid entry refuses the whole batch and the drafted set
        remains as the fallback.
      </p>
      <label
        className="dropzone"
        style={{ cursor: busy ? "wait" : "pointer" }}
        onClick={() => !busy && input.current?.click()}
      >
        <input
          ref={input}
          type="file"
          accept=".json,application/json"
          style={{ display: "none" }}
          disabled={busy}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void handleFile(file);
          }}
        />
        {busy ? "Replacing…" : "Choose a golden.json"}
      </label>
      {error && <ErrorBox message={error} />}
      {successMsg && (
        <p className="small" style={{ color: "var(--pass)", marginTop: 8 }}>
          {successMsg}
        </p>
      )}
    </div>
  );
}
