import { useState } from "react";
import { api } from "../api";
import type { UnpackReport, UploadReport } from "../api";
import { Badge, ErrorBox } from "../components";

/**
 * Upload a corpus, validate it, and see what it would cost.
 *
 * The order matters and is the design: validate first (every
 * trust-boundary check, no byte written), cost it second, unpack only on
 * an explicit second act. Uploading an archive to look at it is not
 * consent to write its contents to disk, and it is certainly not consent
 * to spend quota — which is why the last step here prints commands
 * rather than running them.
 */
export function UploadPage() {
  const [report, setReport] = useState<UploadReport | null>(null);
  const [unpacked, setUnpacked] = useState<UnpackReport | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [over, setOver] = useState(false);

  const send = async (file: File) => {
    setBusy(true);
    setError(null);
    setUnpacked(null);
    try {
      setReport(await api.upload(file));
    } catch (exc) {
      setReport(null);
      setError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <h1>Upload a corpus</h1>
      <p className="lede">
        A ZIP is validated before anything is written: path traversal and symlinks are refused,
        archive junk dropped, non-documents skipped with a note, and the whole archive refused past
        an entry-count, size or compression-ratio cap. Nothing here spends IDP quota.
      </p>

      <label
        className={`dropzone ${over ? "over" : ""}`}
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          const file = e.dataTransfer.files[0];
          if (file) void send(file);
        }}
      >
        <input
          type="file"
          accept=".zip,application/zip"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
          }}
        />
        {busy ? "Validating…" : "Drop a .zip here, or click to choose one"}
      </label>

      {error && <ErrorBox message={error} />}

      {report && (
        <>
          <div className="panel" style={{ marginTop: 14 }}>
            <div className="spread">
              <div>
                <strong className="mono">{report.filename}</strong>
                <div className="small muted">
                  {(report.size_bytes / 1e6).toFixed(1)} MB · {report.document_count} documents ·{" "}
                  {report.already_pinned} already pinned
                </div>
              </div>
              <div className="row">
                {report.rejected_entries.length > 0 && (
                  <Badge kind="fail">{report.rejected_entries.length} rejected</Badge>
                )}
                <Badge kind={report.document_count ? "pass" : "muted"}>
                  {report.document_count ? "accepted" : "no documents"}
                </Badge>
              </div>
            </div>

            <h3>What this would cost</h3>
            <div className="row">
              {Object.entries(report.cost).map(([label, count]) => (
                <span key={label} className="chip">
                  {label.replace(/_/g, " ")}: <strong>{count}</strong> extractions
                </span>
              ))}
            </div>
            <p className="small muted" style={{ marginBottom: 0 }}>
              Each extraction is real org quota against real files. The count is shown before there is
              any way to start one.
            </p>
          </div>

          {report.rejected_entries.length > 0 && (
            <div className="panel fail">
              <h3 style={{ marginTop: 0 }}>Rejected entries</h3>
              <ul className="small mono" style={{ margin: 0, paddingLeft: 18 }}>
                {report.rejected_entries.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
              <p className="small muted" style={{ marginBottom: 0 }}>
                An archive that tried this is worth knowing about, so it is reported rather than
                quietly filtered.
              </p>
            </div>
          )}

          <div className="panel" style={{ padding: 0 }}>
            <table>
              <thead>
                <tr>
                  <th>Document</th>
                  <th>Already pinned at</th>
                </tr>
              </thead>
              <tbody>
                {report.documents.map((doc) => (
                  <tr key={doc.name}>
                    <td className="mono">{doc.name}</td>
                    <td>
                      {doc.pinned_at.length ? (
                        doc.pinned_at.map((pin) => (
                          <span key={`${pin.action_id}/${pin.action_version}`} className="chip" style={{ marginRight: 6 }}>
                            {pin.action_id} / {pin.action_version}
                          </span>
                        ))
                      ) : (
                        <span className="muted small">not pinned — pinning costs one extraction</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>

          {report.skipped.filter((s) => !report.rejected_entries.includes(s)).length > 0 && (
            <div className="panel">
              <h3 style={{ marginTop: 0 }}>Skipped (not documents)</h3>
              <ul className="small mono muted" style={{ margin: 0, paddingLeft: 18 }}>
                {report.skipped
                  .filter((s) => !report.rejected_entries.includes(s))
                  .slice(0, 40)
                  .map((line) => (
                    <li key={line}>{line}</li>
                  ))}
              </ul>
            </div>
          )}

          {!unpacked && report.document_count > 0 && (
            <button
              className="primary"
              disabled={busy}
              onClick={async () => {
                setBusy(true);
                try {
                  setUnpacked(await api.unpack(report.upload_id));
                } catch (exc) {
                  setError((exc as Error).message);
                } finally {
                  setBusy(false);
                }
              }}
            >
              Unpack {report.document_count} documents to disk
            </button>
          )}
        </>
      )}

      {unpacked && (
        <div className="panel" style={{ borderColor: "var(--accent)" }}>
          <h3 style={{ marginTop: 0 }}>Unpacked</h3>
          <p className="small">
            {unpacked.document_count} documents, owner-only, in:
            <br />
            <code>{unpacked.document_dir}</code>
          </p>
          <p className="small muted">
            This is the directory <code>IDP_DOCUMENT_DIR</code> must point at for a later run — the
            golden set names these files and nothing copies them again.
          </p>
          <h3>Next, at a terminal</h3>
          {unpacked.next_commands.map((step) => (
            <div key={step.label} style={{ marginBottom: 12 }}>
              <strong className="small">{step.label}</strong>
              <div className="small muted">{step.why}</div>
              <pre className="command">{step.command}</pre>
            </div>
          ))}
          <p className="small muted" style={{ marginBottom: 0 }}>
            These are printed, not run. Every one spends real quota, and its <code>--yes</code>{" "}
            belongs in front of the person who pays for it.
          </p>
        </div>
      )}
    </>
  );
}
