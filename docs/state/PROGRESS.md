# Project progress log

The squad's persistent context. Every command reads this at start and appends ONE concise line at end.
Format: `- {YYYY-MM-DD} {command} {subject} → produced: {paths}; status: {next}; why: {reason — required for decisions & bounce-backs, pointing to the artifact (ADR-N / QA-N / TRIAGE-date)}`
This is the source of truth for **where** we are, **how** we got here (the sequence), and **why** (the `why:` clauses). Independent of git and of any chat session. `/standup` reconstructs the full narrative from it.

## Log (newest first)
- 2026-09-17 /scaffold-it project setup → produced: CLAUDE.md (adapt mode, adds External services + Squad agents), docs/{init,prd,use-cases,adr,specs,qa,state,spikes,triage,change,design}, src/idp_regression/{adapter,classifier,orchestration,platform}, pyproject.toml, uv.lock, git init; status: ready for /discover; why: solo mode, boardless per SCAFFOLD-ANSWERS.md; Mestre pre-load check flagged missing External services/Squad agents sections, now added.
