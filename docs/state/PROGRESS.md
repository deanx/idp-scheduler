# Project progress log

The squad's persistent context. Every command reads this at start and appends ONE concise line at end.
Format: `- {YYYY-MM-DD} {command} {subject} → produced: {paths}; status: {next}; why: {reason — required for decisions & bounce-backs, pointing to the artifact (ADR-N / QA-N / TRIAGE-date)}`
This is the source of truth for **where** we are, **how** we got here (the sequence), and **why** (the `why:` clauses). Independent of git and of any chat session. `/standup` reconstructs the full narrative from it.

## Log (newest first)
