# G-2 probe captures (2026-09-27)

Recorded live against the project's IDP org with three zero-quota existence probes
(an empty multipart POST to `.../actions/<action>/versions/<version>/executions`, no file), approved
by the user. Bodies are verbatim except where noted.

| File | Probe | Status | Body |
|---|---|---|---|
| `version_probe_exists.raw.json` (earlier capture) | real org, real action, existing version | 400 | `Invalid query parameter 'file'`, re-observed identically today as the control |
| `version_probe_wrong_org.raw.json` | a random, nonexistent org id; real action and version | 403 | `{"status":403,"title":"Forbidden"}`, with no `detail` |
| `version_probe_wrong_action.raw.json` | real org; a random, nonexistent action id | 404 | `Action Id: <id>`. **The random id is replaced by `00000000-0000-4000-8000-000000000000`**; nothing else changed |

What it establishes: IDP resolves the org, then the action, then the version, and only then
complains about the missing `file`. So the 400 that `classify_probe_response` reads as EXISTS only
comes back once all three have resolved. A wrong org or wrong action never produces it (G-2).
