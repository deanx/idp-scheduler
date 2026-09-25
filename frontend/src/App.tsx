import { useRoute } from "./hooks";
import { RunsPage } from "./pages/RunsPage";
import { RunDetailPage } from "./pages/RunDetailPage";
import { NoiseFloorPage } from "./pages/NoiseFloorPage";
import { BlindSpotsPage } from "./pages/BlindSpotsPage";
import { PinsPage } from "./pages/PinsPage";
import { ScorersPage } from "./pages/ScorersPage";
import { UploadPage } from "./pages/UploadPage";
import { ValidateZipPage } from "./pages/ValidateZipPage";
import { PlatformPage } from "./pages/PlatformPage";
import { JobsPage } from "./pages/JobsPage";

const NAV = [
  ["/validate", "Validate a corpus"],
  ["/runs", "Runs"],
  ["/noise-floor", "Noise floor"],
  ["/blind-spots", "Gate blind spots"],
  ["/pins", "Pinned documents"],
  ["/scorers", "Scorers"],
  ["/upload", "Inspect a ZIP"],
  ["/jobs", "Jobs"],
  ["/platform", "Platform history"],
] as const;

export function App() {
  const [route, navigate] = useRoute();
  // `#/runs/<id>` or `#/runs/<id>?baseline=<report>` — the second form
  // lets a finished job link straight to its run ALREADY read against the
  // noise floor, which is the only form in which a CHANGED verdict can
  // be interpreted.
  const runMatch = route.match(/^\/runs\/([^?]+)(?:\?baseline=(.*))?$/);

  return (
    <div className="layout">
      <aside className="sidebar">
        <div className="brand">
          IDP Regression
          <small>local console</small>
        </div>
        <nav className="nav">
          {NAV.map(([path, label]) => (
            <a
              key={path}
              href={`#${path}`}
              className={route === path || (path === "/runs" && runMatch) ? "active" : ""}
            >
              {label}
            </a>
          ))}
        </nav>
        <p className="nav-note">
          Reads artifacts on this machine and writes nothing to the evaluation platform. Only{" "}
          <a href="#/validate">Validate a corpus</a> spends IDP quota, and only after you confirm
          the exact count.
        </p>
      </aside>

      <main className="main">
        {runMatch ? (
          <RunDetailPage
            runId={decodeURIComponent(runMatch[1])}
            initialBaseline={runMatch[2] ? decodeURIComponent(runMatch[2]) : ""}
          />
        ) : route === "/noise-floor" ? (
          <NoiseFloorPage />
        ) : route === "/blind-spots" ? (
          <BlindSpotsPage />
        ) : route === "/pins" ? (
          <PinsPage />
        ) : route === "/scorers" ? (
          <ScorersPage />
        ) : route === "/upload" ? (
          <UploadPage />
        ) : route === "/validate" ? (
          <ValidateZipPage />
        ) : route === "/jobs" ? (
          <JobsPage />
        ) : route === "/platform" ? (
          <PlatformPage />
        ) : (
          <RunsPage navigate={navigate} />
        )}
      </main>
    </div>
  );
}
