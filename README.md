# IDP Regression Testing — SDD hand-off package

This is the initial requirements/product package for building the **custom application**
that drives regression testing of MuleSoft Anypoint IDP extraction output. It is written
to be handed to **Claude Code** and consumed by the **Lemon Studio SDD** pipeline you have
installed (the `/scaffold-it → /discover → /design → /plan → /implement → /qa` flow).

## What's in here

| File | Feeds | Purpose |
|---|---|---|
| `CLAUDE.md` | `/scaffold-it` (adapt mode) | Pre-filled `## Domain`, `## Stack`, `## Tooling`, `## Rigor`. Drop at repo root. |
| `SCAFFOLD-ANSWERS.md` | `/scaffold-it` interview | Ready answers to every setup question, so setup is a paste, not an interview. |
| `docs/init/PRD-idp-regression.md` | `/brief`, `/discover` | Product brief: problem, goals, scope, personas, epics, success criteria. |
| `docs/init/discovery-notes.md` | `/discover`, `/design` | Domain knowledge, decisions already made, and the platform trade-off. |
| `docs/init/requirements-spec.md` | `/discover`, `/design` | The three-layer requirements spec, data contracts, functional + non-functional requirements. |
| `docs/init/use-cases-seed.md` | `/discover` | Candidate use cases in Given/When/Then with `EX-n` example tables. |
| `docs/init/design-inputs.md` | `/design` | Architecture, ADR seeds, NFRs, diagrams the architect turns into ADRs. |
| `docs/init/stories-backlog-seed.md` | `/plan` | Candidate stories grouped by epic, each with a Definition of Done. |
| `docs/init/glossary.md` | all stages | Ubiquitous language — one word per concept, used everywhere. |

The `docs/init/` filenames are chosen to match the SDD discovery globs (`*PRD*`, `*iscovery*`,
`*stor*`), so Feliz finds them automatically.

## How to use it

1. **Copy** this package's contents into your (empty or existing) git repo root.
2. In Claude Code, run **`/scaffold-it`**. It detects an existing `CLAUDE.md` and enters
   *adapt mode* — it fills in the squad's own sections and preserves the domain/stack
   sections here. Use `SCAFFOLD-ANSWERS.md` for anything it still asks.
3. Run **`/discover`** for the first use case (start with `UC: run a baseline regression`
   from `use-cases-seed.md`). Feliz reads `docs/init/` and turns the seed into a formal UC.
4. Run **`/design`**, then **`/plan`**, then **`/implement`** per story.

## Important: these are *inputs*, not final artifacts

The SDD pipeline regenerates the formal use cases, ADRs, and stories itself, with its own
review gates. Everything in `docs/init/` is high-quality **source material** for that
process — the "what and why" — not the squad's output. Where a use case or story here is
drafted by an assistant rather than authored by you, treat it as `[approved]`-provenance
at best: the pipeline will (correctly) ask you for at least one example in your own words
before it builds. The one decision still genuinely open — **Langfuse vs Opik** — is flagged
throughout and should be resolved during `/design` against your own workload.

## Prior art

A validated spike already exists (`idp-regression-spike.zip`, produced earlier) with a
working `classifier/` (11/11 tests passing), `adapter/`, and platform-run scripts for both
Langfuse and Opik. Use it as the implementation skeleton — the classifier and gate are the
lowest-risk place to start and are already proven.
