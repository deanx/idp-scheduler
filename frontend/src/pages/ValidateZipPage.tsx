import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import type {
  CompareRequest,
  ComparePlan,
  FloorRequest,
  Job,
  UploadReport,
  VersionDiscovery,
} from "../api";
import { useAsync } from "../hooks";
import { Badge, ErrorBox } from "../components";

/**
 * **The end-to-end use case (user decision, 2026-09-25).**
 *
 * "This corpus is already validated against the current Action version.
 * I changed the LLM and published a new version. Is it still valid?"
 *
 * Four steps, and the order is the safety property: the archive is
 * validated before anything is written, the versions are discovered
 * without spending extraction quota, the REAL `--plan` prices the job,
 * and only then does a button exist that spends money — carrying the
 * exact number the plan returned.
 *
 * This is the one page in this console that spends IDP quota. Every
 * other surface prints the command instead; that posture was reversed
 * here deliberately and only here.
 */
export function ValidateZipPage() {
  const [upload, setUpload] = useState<UploadReport | null>(null);
  const [org, setOrg] = useState("");
  const [action, setAction] = useState("");
  const [dataset, setDataset] = useState("");
  const [anchor, setAnchor] = useState("1.0.0");
  const [discovery, setDiscovery] = useState<VersionDiscovery | null>(null);
  const [trusted, setTrusted] = useState("");
  const [candidate, setCandidate] = useState("");
  const [manual, setManual] = useState(false);
  const [plan, setPlan] = useState<ComparePlan | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  // The floor is its own job with its own cost, so it carries its own
  // plan and its own approval.
  const [floorPlan, setFloorPlan] = useState<ComparePlan | null>(null);
  const [floorJob, setFloorJob] = useState<Job | null>(null);
  const [floorVersion, setFloorVersion] = useState<"trusted" | "candidate">("trusted");
  const [floorSample, setFloorSample] = useState(20);
  // T5: the flags the UI could not reach. A corpus over the script's own
  // 200-document default used to fail AFTER being uploaded and priced.
  const [maxDocuments, setMaxDocuments] = useState(200);
  const [allowPartial, setAllowPartial] = useState(false);
  const [repin, setRepin] = useState(false);
  const [glob, setGlob] = useState("");
  const [advanced, setAdvanced] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  // Asked before anything is uploaded or priced: a missing credential
  // must cost nothing, not the first extraction.
  const preflight = useAsync(() => api.preflight(), []);
  // **Fail CLOSED.** Blocked until the preflight resolves and says
  // otherwise — while it is loading, and if the fetch failed. The
  // earlier `preflight.data ? … : false` disabled the guard exactly when
  // it could not read its own status, which is the one moment a guard
  // has to hold (Zangado QA F-1). The server enforces the same check, so
  // this is the courtesy, not the control.
  const blocked = !preflight.data?.can_run_validation;

  const request = (): CompareRequest => ({
    upload_id: upload?.upload_id,
    dataset,
    org,
    action,
    trusted_version: trusted,
    candidate_version: candidate,
    max_documents: maxDocuments,
    allow_partial: allowPartial,
    repin,
    glob: glob || undefined,
  });

  const floorRequest = (): FloorRequest => ({
    upload_id: upload?.upload_id,
    org,
    action,
    version: floorVersion === "trusted" ? trusted : candidate,
    repeats: 2,
    max_documents: floorSample,
  });

  /** The floor report this session produced, if any. */
  const floorReport = floorJob?.summary?.floor_report ?? null;

  const guard = async (label: string, fn: () => Promise<void>) => {
    setBusy(label);
    setError(null);
    try {
      await fn();
    } catch (exc) {
      setError((exc as Error).message);
    } finally {
      setBusy(null);
    }
  };

  // Poll a running job. The console holds no job state across restarts,
  // so this is the only view of a batch in flight.
  const jobId = job?.id;
  const running = job?.status === "running";
  useEffect(() => {
    if (!jobId || !running) return;
    const timer = setInterval(() => {
      api.job(jobId).then(setJob).catch(() => undefined);
    }, 1500);
    return () => clearInterval(timer);
  }, [jobId, running]);

  const ready = Boolean(upload && org && action && dataset && trusted && candidate);

  return (
    <>
      <h1>Validate a corpus against a new Action version</h1>
      <p className="lede">
        Pins every document in the archive at the <strong>trusted</strong> version, then re-reads
        every one of them at the <strong>candidate</strong> — one command, one unpack, so the bytes
        compared are provably the bytes pinned. <strong>This is the one page here that spends IDP
        quota</strong>, and it will not start until you confirm the exact count.
      </p>

      {preflight.data && (
        <div className={`panel ${blocked ? "fail" : ""}`}>
          <div className="row">
            <Badge kind={blocked ? "fail" : "pass"}>
              {blocked ? "cannot run here" : "ready to run"}
            </Badge>
            <span className="small muted mono">workspace {preflight.data.workspace}</span>
          </div>
          {preflight.data.blockers.map((blocker) => (
            <p key={blocker} className="error small" style={{ marginBottom: 0 }}>
              {blocker}
            </p>
          ))}
          {blocked && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              Set them in <code>{preflight.data.env_searched[0]}</code> and restart the console.
              Nothing below will spend quota until this clears.
            </p>
          )}
          <details>
            <summary className="small muted">What is configured</summary>
            <div className="row small" style={{ marginTop: 8 }}>
              {Object.entries({ ...preflight.data.idp, ...preflight.data.platform }).map(
                ([name, ok]) => (
                  <span key={name} className="chip">
                    {ok ? "✓" : "✗"} {name}
                  </span>
                ),
              )}
            </div>
            <p className="small muted" style={{ marginBottom: 0 }}>
              Presence only — this console never reads a credential's value back to the browser.
              Results will be written to <code>{preflight.data.writes.run_artifacts}</code>.
            </p>
          </details>
        </div>
      )}

      <Step n={1} title="Upload the corpus" done={Boolean(upload)}>
        <UploadStep upload={upload} onUploaded={setUpload} onError={setError} />
      </Step>

      <Step n={2} title="Name the Action" done={Boolean(org && action && dataset)}>
        <div className="grid2">
          <Field label="Organisation / business group id" value={org} onChange={setOrg} placeholder="7f3e…" />
          <Field label="Action id" value={action} onChange={setAction} placeholder="invoice-extract" />
          <Field label="Dataset the per-file goldens live in" value={dataset} onChange={setDataset} placeholder="invoices-golden" />
          <Field
            label="A version you know exists (the search anchor)"
            value={anchor}
            onChange={setAnchor}
            placeholder="1.0.0"
          />
        </div>
      </Step>

      <Step n={3} title="Choose the two versions" done={Boolean(trusted && candidate)}>
        <p className="small muted" style={{ marginTop: 0 }}>
          IDP has no endpoint that lists versions, so this sweeps a small grid around your anchor
          with existence probes. Each probe is one HTTP call and <strong>spends no extraction
          quota</strong>.
        </p>
        <div className="row">
          <button
            disabled={!org || !action || !anchor || busy !== null}
            onClick={() =>
              guard("discover", async () => {
                setManual(false);
                setDiscovery(await api.discoverVersions(org, action, anchor));
              })
            }
          >
            {busy === "discover" ? "Probing…" : "Find versions"}
          </button>
          <button onClick={() => setManual((v) => !v)}>
            {manual ? "Use the picker" : "Type the versions instead"}
          </button>
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Probing is a convenience, not a requirement. With no IDP credentials on this machine, a
          rate limit, or a version scheme that is not strict semver, type the two versions instead —
          the comparison does not care how you named them.
        </p>

        {manual && (
          <div className="grid2" style={{ marginTop: 12 }}>
            <Field
              label="Trusted version — the corpus is already valid against this one"
              value={trusted}
              onChange={setTrusted}
              placeholder="1.0.0"
            />
            <Field label="Candidate version — the new LLM" value={candidate} onChange={setCandidate} placeholder="2.0.0" />
          </div>
        )}

        {discovery && !manual && (
          <>
            <div className="row small muted" style={{ marginTop: 10 }}>
              <span className="chip">{discovery.probes_used} probes</span>
              <span className="chip">0 extractions</span>
              {discovery.truncated && <Badge kind="warn">partial — probe budget reached</Badge>}
              {discovery.rate_limited && <Badge kind="fail">rate limited — list is incomplete</Badge>}
            </div>
            {(discovery.truncated || discovery.rate_limited) && (
              <p className="small muted">
                Absence below does not mean a version does not exist — the sweep stopped early.
              </p>
            )}
            {discovery.versions.length === 0 ? (
              <p className="error small">
                No versions found around {discovery.anchor}. Check the org, action and anchor.
              </p>
            ) : (
              <div className="grid2" style={{ marginTop: 12 }}>
                <VersionPicker
                  label="Trusted version — the corpus is already valid against this one"
                  versions={discovery.versions}
                  value={trusted}
                  exclude={candidate}
                  onChange={setTrusted}
                />
                <VersionPicker
                  label="Candidate version — the new LLM"
                  versions={discovery.versions}
                  value={candidate}
                  exclude={trusted}
                  onChange={setCandidate}
                />
              </div>
            )}
          </>
        )}
      </Step>

      <Step
        n={4}
        title="Measure the noise floor — recommended before comparing"
        done={Boolean(floorReport)}
      >
        <p className="small muted" style={{ marginTop: 0 }}>
          Pin/verify compares <strong>one</strong> reading at {trusted || "the trusted version"}{" "}
          against <strong>one</strong> reading at {candidate || "the candidate"}. If either version
          is nondeterministic, a <code>CHANGED</code> verdict cannot be told from the extractor
          disagreeing with <em>itself</em> — and a model swap is exactly when that is most likely.
          This reads a sample twice against one version and measures the difference.
        </p>

        <div className="row">
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="floor-version">Measure which version</label>
            <select
              id="floor-version"
              value={floorVersion}
              onChange={(e) => setFloorVersion(e.target.value as "trusted" | "candidate")}
            >
              <option value="trusted">the trusted version {trusted && `(${trusted})`}</option>
              <option value="candidate">the candidate {candidate && `(${candidate})`}</option>
            </select>
          </div>
          <div className="field" style={{ margin: 0 }}>
            <label htmlFor="floor-sample">Sample size</label>
            <input
              id="floor-sample"
              type="number"
              min={1}
              max={1000}
              value={floorSample}
              onChange={(e) => setFloorSample(Number(e.target.value))}
            />
          </div>
          <button
            style={{ marginTop: 16 }}
            disabled={!upload || !org || !action || !trusted || !candidate || blocked || busy !== null}
            onClick={() =>
              guard("floor-plan", async () => {
                setFloorJob(null);
                setFloorPlan(await api.floorPlan(floorRequest()));
              })
            }
          >
            {busy === "floor-plan" ? "Planning…" : "Plan the floor (spends nothing)"}
          </button>
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          {floorVersion === "trusted" ? (
            <>
              Measuring the <strong>trusted</strong> version answers “can my goldens be trusted at
              all” — a field it flips on is one whose pinned value was a coin toss, so every later
              verdict on it is noise. This is the documented order, and what{" "}
              <code>verify_document.py</code>’s own failure hint tells you to measure.
            </>
          ) : (
            <>
              Measuring the <strong>candidate</strong> answers the narrower “is <em>this</em> red
              reproducible”.
            </>
          )}
        </p>

        {floorPlan && !floorJob && (
          <div className="panel warn" style={{ marginTop: 12 }}>
            <div className="spread">
              <div>
                <strong>
                  Measuring the floor will spend {floorPlan.planned_extractions} more extractions.
                </strong>
                <p className="small muted" style={{ marginBottom: 0 }}>
                  On top of the comparison’s cost. Skipping it is legitimate — the result is simply
                  a verdict you cannot yet interpret.
                </p>
              </div>
              <button
                className="primary"
                disabled={busy !== null || blocked}
                onClick={() =>
                  guard("floor-start", async () =>
                    setFloorJob(
                      await api.floorStart(floorRequest(), floorPlan.planned_extractions),
                    ),
                  )
                }
              >
                {busy === "floor-start" ? "Starting…" : `Measure — spend ${floorPlan.planned_extractions}`}
              </button>
            </div>
            <pre className="command">{floorPlan.command}</pre>
          </div>
        )}

        {floorJob && (
          <JobPanel
            job={floorJob}
            onCancel={() => guard("cancel", async () => setFloorJob(await api.cancelJob(floorJob.id)))}
          />
        )}

        {floorReport && (
          <p className="small" style={{ marginBottom: 0 }}>
            Floor measured: <code>{floorReport}</code>. The comparison below will be read against
            it. <a href="#/noise-floor">See it per field →</a>
          </p>
        )}
      </Step>

      <Step n={5} title="Price it, then run it" done={job?.status === "succeeded"}>
        <details open={advanced} onToggle={(e) => setAdvanced(e.currentTarget.open)}>
          <summary className="small muted">Advanced — corpus ceiling, partial runs, re-pinning</summary>
          <div className="grid2" style={{ marginTop: 10 }}>
            <div className="field">
              <label htmlFor="max-docs">Document ceiling</label>
              <input
                id="max-docs"
                type="number"
                min={1}
                max={1000}
                value={maxDocuments}
                onChange={(e) => setMaxDocuments(Number(e.target.value))}
              />
              <span className="small muted">
                The script's own default is 200. A larger corpus fails without raising this.
              </span>
            </div>
            <div className="field">
              <label htmlFor="glob">File patterns</label>
              <input
                id="glob"
                placeholder="*.pdf,*.tif — blank means documents and images"
                value={glob}
                onChange={(e) => setGlob(e.target.value)}
                style={{ width: "100%" }}
              />
            </div>
          </div>
          <label className="row small" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={allowPartial}
              onChange={(e) => setAllowPartial(e.target.checked)}
            />
            Verify the files that pinned even if some failed
          </label>
          <p className="small muted" style={{ margin: "2px 0 8px 22px" }}>
            Off by default, and that default is load-bearing: the files that failed have{" "}
            <strong>no golden</strong>, so verifying would skip them silently and exit 0 on the
            rest — “every pinned file is still valid” would be true and useless.
          </p>
          <label className="row small" style={{ gap: 6 }}>
            <input type="checkbox" checked={repin} onChange={(e) => setRepin(e.target.checked)} />
            Re-read files that are already pinned
          </label>
          <p className="small muted" style={{ margin: "2px 0 0 22px" }}>
            Spends one extra extraction per already-pinned document. Without it, re-running after
            a failure retries only what failed.
          </p>
        </details>

        <button
          className="primary"
          disabled={!ready || blocked || busy !== null}
          onClick={() =>
            guard("plan", async () => {
              setJob(null);
              setPlan(await api.comparePlan(request()));
            })
          }
        >
          {busy === "plan" ? "Planning…" : "Plan (spends nothing)"}
        </button>

        {plan && !job && (
          <div className="panel warn" style={{ marginTop: 12 }}>
            <div className="spread">
              <div>
                <strong>
                  This will spend {plan.planned_extractions} IDP extractions against a live org.
                </strong>
                <p className="small muted" style={{ marginBottom: 0 }}>
                  {plan.planned_extractions / 2} documents pinned at {trusted}, then the same{" "}
                  {plan.planned_extractions / 2} verified at {candidate}. Real files, real quota,
                  billed to the organisation. Nothing has been spent yet.
                </p>
              </div>
              <button
                className="primary"
                disabled={busy !== null || blocked}
                onClick={() =>
                  guard("start", async () =>
                    setJob(await api.compareStart(request(), plan.planned_extractions)),
                  )
                }
              >
                {busy === "start" ? "Starting…" : `Run — spend ${plan.planned_extractions}`}
              </button>
            </div>
            <pre className="command">{plan.command}</pre>
            {plan.output.length > 0 && (
              <details>
                <summary className="small muted">Plan output</summary>
                <pre className="command">{plan.output.join("\n")}</pre>
              </details>
            )}
          </div>
        )}

        {job && (
          <JobPanel
            job={job}
            floorReport={floorReport}
            onCancel={() => guard("cancel", async () => setJob(await api.cancelJob(job.id)))}
          />
        )}
      </Step>

      {error && <ErrorBox message={error} />}
    </>
  );
}

function Step({
  n,
  title,
  done,
  children,
}: {
  n: number;
  title: string;
  done: boolean;
  children: React.ReactNode;
}) {
  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 10 }}>
        <Badge kind={done ? "pass" : "muted"}>{done ? "✓" : n}</Badge>
        <strong>{title}</strong>
      </div>
      {children}
    </div>
  );
}

function Field({
  label,
  value,
  onChange,
  placeholder,
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  placeholder?: string;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <input value={value} placeholder={placeholder} onChange={(e) => onChange(e.target.value)} style={{ width: "100%" }} />
    </div>
  );
}

function VersionPicker({
  label,
  versions,
  value,
  exclude,
  onChange,
}: {
  label: string;
  versions: { version: string; status: string }[];
  value: string;
  exclude: string;
  onChange: (v: string) => void;
}) {
  return (
    <div className="field">
      <label>{label}</label>
      <div className="row" style={{ gap: 6 }}>
        {versions.map((v) => (
          <button
            key={v.version}
            className={value === v.version ? "primary" : ""}
            disabled={v.version === exclude}
            title={
              v.status === "unknown"
                ? "The probe could not classify this version. It may exist — it is shown rather than hidden."
                : "Confirmed to exist."
            }
            style={{ padding: "3px 9px", fontSize: 12 }}
            onClick={() => onChange(v.version)}
          >
            {v.version}
            {v.status === "unknown" && " ?"}
          </button>
        ))}
      </div>
    </div>
  );
}

function UploadStep({
  upload,
  onUploaded,
  onError,
}: {
  upload: UploadReport | null;
  onUploaded: (r: UploadReport) => void;
  onError: (m: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const input = useRef<HTMLInputElement>(null);

  const send = async (file: File) => {
    setBusy(true);
    try {
      onUploaded(await api.upload(file));
    } catch (exc) {
      onError((exc as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <label className="dropzone" onClick={() => input.current?.click()}>
        <input
          ref={input}
          type="file"
          accept=".zip,application/zip"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) void send(file);
          }}
        />
        {busy ? "Validating…" : upload ? `${upload.filename} — click to replace` : "Choose a .zip"}
      </label>
      {upload && (
        <div className="row small" style={{ marginTop: 10 }}>
          <Badge kind="pass">{upload.document_count} documents</Badge>
          {upload.rejected_entries.length > 0 && (
            <Badge kind="fail">{upload.rejected_entries.length} rejected</Badge>
          )}
          {upload.already_pinned > 0 && (
            <span
              className="chip"
              title="Already pinned documents are skipped rather than re-paid for."
            >
              {upload.already_pinned} already pinned
            </span>
          )}
          <span className="muted">
            pin + verify: <strong>{upload.cost.pin_and_verify}</strong> extractions
          </span>
        </div>
      )}
      {upload && upload.rejected_entries.length > 0 && (
        <ul className="small mono error" style={{ marginBottom: 0 }}>
          {upload.rejected_entries.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      )}
    </>
  );
}

function JobPanel({
  job,
  onCancel,
  floorReport = null,
}: {
  job: Job;
  onCancel: () => void;
  /** When present, the run link carries it — a CHANGED verdict is only
   *  interpretable against a floor, so the link opens already read
   *  against one. */
  floorReport?: string | null;
}) {
  const tail = useRef<HTMLPreElement>(null);
  useEffect(() => {
    tail.current?.scrollTo({ top: tail.current.scrollHeight });
  }, [job.lines.length]);

  const verdict = job.summary?.verdict;
  const kind =
    verdict === "STILL VALID" ? "pass" : verdict === "CHANGED" ? "fail" : verdict ? "warn" : "muted";

  return (
    <div className={`panel ${verdict === "CHANGED" ? "fail" : ""}`} style={{ marginTop: 12 }}>
      <div className="spread">
        <div>
          <div className="row">
            <Badge kind={job.status === "running" ? "info" : kind}>
              {job.status === "running" ? "running…" : (verdict ?? job.status)}
            </Badge>
            {job.planned_extractions != null && (
              <span className="chip">{job.planned_extractions} extractions approved</span>
            )}
            {job.exit_code != null && <span className="chip">exit {job.exit_code}</span>}
          </div>
          {verdict === "CHANGED" && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              The candidate version read at least one document differently. A non-zero exit here is
              the gate working, not the tool failing — the exit code <em>is</em> the gate.
            </p>
          )}
          {verdict === "RUN FAILED" && job.summary?.run_incomplete && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              The validation run aborted ({job.summary.run_incomplete}). Its partial results are
              not counted: the documents it did not reach were never measured.
            </p>
          )}
          {verdict === "RUN FAILED" && (
            <p className="small muted" style={{ marginBottom: 0 }}>
              The batch did not complete. Extractions already spent are not refunded; re-running
              skips documents that were pinned successfully.
            </p>
          )}
        </div>
        {job.status === "running" && (
          <button className="danger" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>

      {job.summary?.run_id ? (
        <div style={{ marginTop: 10 }}>
          <div className="row small">
            <span className="chip">{job.summary.documents ?? 0} documents</span>
            <span className="chip">still valid: {job.summary.still_valid_documents ?? 0}</span>
            <span className="chip">changed: {job.summary.changed_documents ?? 0}</span>
            <a
              href={
                `#/runs/${job.summary.run_id}` +
                (floorReport ? `?baseline=${encodeURIComponent(floorReport)}` : "")
              }
            >
              open the run{floorReport ? " against the floor" : ""} — every field, expected vs
              actual →
            </a>
          </div>
          {(job.summary.changed_document_ids?.length ?? 0) > 0 && (
            <>
              <h3>Documents the candidate read differently</h3>
              <ul className="small mono" style={{ margin: 0, paddingLeft: 18 }}>
                {job.summary.changed_document_ids?.map((id) => (
                  <li key={id}>{id}</li>
                ))}
              </ul>
              {job.summary.changed_document_ids_truncated && (
                <p className="small muted">…more; open the run for the full list.</p>
              )}
              <p className="small muted">
                Read these against a noise floor before calling them regressions — the candidate
                disagreeing with <em>itself</em> looks identical here.
              </p>
            </>
          )}
        </div>
      ) : (
        job.status !== "running" && (
          <p className="small muted" style={{ marginTop: 8, marginBottom: 0 }}>
            No run artifact was produced, so there are no per-document verdicts to show.
          </p>
        )
      )}

      <pre className="command" ref={tail} style={{ maxHeight: 320, overflowY: "auto" }}>
        {job.lines.join("\n") || "waiting for output…"}
      </pre>
    </div>
  );
}
