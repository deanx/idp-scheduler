# Waivers

Explicit, auditable skips of the automated quality gates (the `hooks/` PreToolUse gates).
A waiver is a decision — record who authorised it and why. One line each:
- `WAIVE-QA S-NN.X: <reason>, <who>, <YYYY-MM-DD>` — lets that story move to Done without a PASSED stamp + QA report.
- `WAIVE-HARDEN S-NN.X: <reason>, <who>, <YYYY-MM-DD>` — lets that story move to Done without a passing /harden containment report, even when the design marked it REQUIRED.
- `WAIVE-EVALS S-NN.X: <reason>, <who>, <YYYY-MM-DD>` — lets that story move to Done without a passing LLM-eval suite, even when the design marked LLM-Evals: REQUIRED.
- `WAIVE-OBS S-NN.X: <reason>, <who>, <YYYY-MM-DD>` — lets that story move to Done without verified telemetry, even when the design marked Observability: REQUIRED.
- `WAIVE-RELEASE: <reason>, <who>, <YYYY-MM-DD>` — lets a release command run without a 🟢 GO /signoff.

## Waivers (newest first)
