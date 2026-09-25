import { api } from "../api";
import { useAsync } from "../hooks";
import { Badge, Empty, ErrorBox, Loading, when } from "../components";

/**
 * The pin store, as the tree it is.
 *
 * The paths ARE the relationship: `goldens/<action>/<version>/<doc>.json`.
 * The same document pinned at two versions shows as two rows here, which
 * is the whole point — a dataset name is flat and cannot say that.
 */
export function PinsPage() {
  const { data, error, loading } = useAsync(() => api.pins(), []);
  const groups = data?.pins ?? [];
  const byDocument = new Map<string, number>();
  for (const group of groups) {
    for (const doc of group.documents) {
      byDocument.set(doc.document_id, (byDocument.get(doc.document_id) ?? 0) + 1);
    }
  }

  return (
    <>
      <h1>Pinned documents</h1>
      <p className="lede">
        Each golden is one document read by one trusted action version. Verify re-reads it under a new
        version through the ordinary gate and prints <code>STILL VALID</code> / <code>CHANGED</code>.
      </p>

      {loading && <Loading />}
      {error && <ErrorBox message={error} />}
      {data && !groups.length && (
        <Empty>
          Nothing pinned. <code>scripts/pin_document.py --file &lt;doc&gt; …</code> records the trusted
          version's reading of a file as its golden.
        </Empty>
      )}

      {groups.map((group) => (
        <div key={`${group.action_id}/${group.action_version}`} className="panel">
          <div className="spread">
            <div>
              <strong className="mono">{group.action_id}</strong>
              <span className="muted"> / </span>
              <strong className="mono">{group.action_version}</strong>
              <div className="small muted">
                {group.dataset && <>dataset <code>{group.dataset}</code> · </>}
                {group.documents.length} document{group.documents.length === 1 ? "" : "s"}
                {group.document_dir && (
                  <>
                    {" "}
                    · from <code>{group.document_dir}</code>
                  </>
                )}
              </div>
            </div>
          </div>

          <table style={{ marginTop: 10 }}>
            <thead>
              <tr>
                <th>Document</th>
                <th>Pinned</th>
                <th style={{ textAlign: "right" }}>Fields</th>
                <th>Raw capture</th>
                <th>Also pinned at</th>
              </tr>
            </thead>
            <tbody>
              {group.documents.map((doc) => (
                <tr key={doc.document_id}>
                  <td className="mono">{doc.document_id}</td>
                  <td className="small muted">{when(doc.pinned_at)}</td>
                  <td className="num">{doc.fields ?? "—"}</td>
                  <td>
                    {doc.has_raw_capture ? (
                      <Badge kind="muted">kept</Badge>
                    ) : (
                      <span
                        className="muted small"
                        title="IDP drops a result after 24 hours; without a capture, re-drafting costs another extraction."
                      >
                        none
                      </span>
                    )}
                  </td>
                  <td>
                    {(byDocument.get(doc.document_id) ?? 1) > 1 ? (
                      <Badge kind="info">{(byDocument.get(doc.document_id) ?? 1) - 1} other version</Badge>
                    ) : (
                      <span className="muted small">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <pre className="command">
            {`.venv/bin/python scripts/verify_document.py --all --dataset ${group.dataset ?? "<name>"} \\
  --action ${group.action_id} --trusted-version ${group.action_version} --version <new-v> --yes`}
          </pre>
        </div>
      ))}
    </>
  );
}
