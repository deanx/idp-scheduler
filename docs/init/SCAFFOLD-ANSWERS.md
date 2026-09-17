# /scaffold-it — ready answers

Paste these as Mestre asks. (With `CLAUDE.md` already present, scaffold enters *adapt mode*
and skips most of these — this is the fallback if it asks.)

| Step | Question | Answer |
|---|---|---|
| 0 | Project language | **1 — English** |
| 1 | Project name | **IDP Regression Tester** |
| 2 | Trello board mirror? | **no** — boardless, state in `docs/state/` |
| 3 | Stack | **5 — Other**: "Python 3.13 library + CLI (no web framework); optional web remediation UI later" |
| 3a | Python version | **3.13** if installed, else target the installed version (don't declare 3.13 while another is installed) |
| 3b | LSP | recommend **pyright**; record `not detected — recommended` unless an LSP MCP is connected |
| 3c | Solo or team | **solo** (change if others will run the squad) |
| 3d | Rigor profile | **2 — standard** |
| 6 | Initial docs | the files already in `docs/init/` — describe each from the table in `README.md` |
| 6b | Domain | already written in `CLAUDE.md ## Domain` — confirm it |

## Note on the stack answer

The Lemon Studio scaffold assumes a React + backend fullstack and will try to generate
`apps/web` and `apps/api` skeletons. This project's core is a **Python library + CLI**, not a
web app. When scaffold offers to generate app skeletons:

- Keep the **Python backend** skeleton (`apps/api` or a top-level package — your call).
- **Decline the React frontend** for now; the remediation UI is Epic E and is deferred.
- If scaffold insists on a structure, prefer a single Python package: `src/idp_regression/`
  with `adapter/`, `classifier/`, `orchestration/`, `platform/` submodules (mirrors the spike).
