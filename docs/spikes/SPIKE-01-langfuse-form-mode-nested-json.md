# SPIKE-01: Langfuse form mode — nested-JSON golden editing

**Status:** Done 2026-09-19. Verdict: partially confirmed, with the no-code Curator claim refuted. Resolved by ADR-0005 (Atchim APPROVED); S-01.5 QA ⚠️ Pass with follow-ups.
**Date:** 2026-09-17
**Owner:** Soneca (design) + Dengoso (execution) — to be scheduled by Dunga
**Blocks:** ADR-0001's platform decision is **PROVISIONAL on this spike**. If SPIKE-01 refutes the form-mode capability, the platform decision re-opens.

## Goal

Confirm or refute whether a self-hosted Langfuse instance, in **form mode**, renders a representative **nested** golden set as **schema-validated editable form fields** — not just flat/shallow key-value pairs. This is the load-bearing capability on which ADR-0001's "Langfuse for the Curator persona" decision rests.

The seed (`docs/init/use-cases-seed.md` EX-C1-1) labels the form-mode capability as an **assumption** ("EX-C1-1 assumes Langfuse form mode"). The Atchim review of ADR-0001 flagged that the whole decision hinges on an uncited claim presented as fact. This spike closes that gap with a live observation.

## Why it matters

The **Curator** is a named, non-engineer persona in `## Domain`. UC-C1 (curator edits a nested golden in the platform UI) is only feasible without code if the platform UI can render and validate nested expected outputs. If Langfuse form mode only handles flat JSON, the Curator-persona differentiator collapses and the platform decision may flip to Opik (where the editing path is explicitly SDK/custom-surface, but at least the limitation is honest).

This is the one assumption whose falsification could **flip** ADR-0001. All other ADR-0001 considerations (self-hosting, score-volume cost, app-tracked versioning) hold on either platform.

## Method

1. **Stand up a live self-hosted Langfuse** (Docker, per `## Stack` — the platform server runs in Docker; the custom app does not). Pin the Langfuse version in the lock file; record the version in this spike's result.
2. **Define a representative nested golden** as a dataset item's `expected_output`. It must exercise every shape the real golden schema (DATA-MODEL-01) uses:
   - Top-level `fields`: a mix of types — `number` (`total`), `date` (`invoice_date`), `id` (`invoice_number`), `text` (`notes`). Each field `{value, type, critical}`.
   - `tables`: at least one table (`line_items`) with `match_key`, `critical`, `type`, and a `rows[]` array where each row is a `{column: {value}}` map — i.e. a list of dicts keyed by column name (the post-revision ADR-0002 `tables: dict[str, list[dict[str, FieldValue]]]` shape). Include ≥ 2 rows.
   - `prompts`: at least one entry keyed by `prompt` string, with `answer.value`, `source`.
3. **Enable form mode** for the dataset in the Langfuse UI (the exact mechanism — schema upload vs. inference — is part of what the spike observes; record what the UI required).
4. **Attempt the Curator interaction** from EX-C1-1 / EX-C1-2:
   - Edit a nested `fields.total` value (`1150.00` → `1250.00`) and save; confirm the next read returns `1250.00`.
   - Edit a `tables.line_items[].<column>` value inside a row.
   - Type an invalid value into a `number` field (`twelve fifty`) and confirm the UI **rejects** it via schema validation (EX-C1-2), not by silently storing a string.
   - Add a new row to `tables.line_items` and confirm it saves in the correct shape.

## What "confirmed" looks like

Form mode renders the nested golden — `fields` of mixed types AND `tables[].rows[]` of column-keyed dicts AND `prompts[]` — as **schema-validated editable form fields**: each nested value is individually editable, type-checked, and rejected on schema violation. The Curator can perform EX-C1-1 and EX-C1-2 without touching code or the SDK.

**Outcome → ADR-0001 stands.** The PROVISIONAL label is removed; the platform decision is Langfuse, confirmed.

## What "refuted" looks like

Form mode only renders **flat or shallow** JSON (top-level key → scalar; no nested-object/table editing, or tables render as an opaque JSON blob the Curator cannot edit field-by-field). Nested editing falls back to raw JSON in a text box with no schema validation, or to the SDK.

**Outcome → ADR-0001 reverts to Option C (defer) and re-opens the platform decision Langfuse vs Opik.** In the re-decision, the Opik editing path (SDK / custom surface) is evaluated symmetrically: if neither platform gives the Curator a no-code nested-editing surface, the decision becomes "build a custom Curator UI (Epic E scope) on whichever platform has the better API" vs. "accept SDK-only golden editing for now." That re-decision gets its own ADR (supersedes ADR-0001).

## Fallback decision (if refuted)

Re-decide Langfuse vs Opik with the Opik SDK/custom-surface editing path evaluated on the same nested golden. Do **not** silently keep Langfuse on a falsified premise. The re-decision ADR supersedes ADR-0001 and re-resolves ASM-03 / ASM-05.

## Time-box

Half a day (stand-up + golden load + interaction). If form mode is confirmed in the first interaction, stop — no need to exhaustively test every field type. If the first interaction reveals a partial limitation (some nested shapes work, others don't), spend the box characterising exactly which shapes break and record it; the re-decision ADR needs that detail.

## Deliverable

A short result appended to this file: Langfuse version tested, what the UI required to enable form mode, a screenshot or textual description of the nested rendering, and the verdict (confirmed / partially confirmed / refuted) with the specific shapes that did or did not render as schema-validated form fields. The result updates ADR-0001's PROVISIONAL label and ASM-05's status.
## Result (2026-09-19)

**Langfuse tested:** 4.38.0 OSS, self-hosted (Docker). **Form-mode mechanism:** a JSON Schema set on the dataset (`expectedOutputSchema` / `inputSchema`, via API or dataset settings); no inference. **Rendering:** the Edit dialog is a raw JSON code editor (CodeMirror) with the schema in a hover card, not generated form fields.

**Verdict: PARTIALLY CONFIRMED, and the no-code Curator capability is REFUTED.**
- **Confirmed for every shape** (`fields` of mixed types, `tables.line_items.rows[]` including add-row and cell edit, `prompts[]`):
  - nested storage and exact round-trip;
  - schema validation enforced on the server for API and UI writes;
  - an invalid value is rejected and the stored item stays unchanged.
- **Refuted for every shape:**
  - no shape renders as individually editable form fields;
  - the UI blocks Save with no inline explanation, and item-level errors carry no JSON path.

Full findings, evidence and risks: `docs/spikes/SPIKE-2026-09-19-langfuse-form-mode.md`. **Next:** Soneca `/design` re-decision ADR, weighing (1) Langfuse plus a custom schema-driven Curator form in Epic E, (2) JSON editing for the MVP, and (3) Opik, evaluated the same way. **Update:** ADR-0005 is now written and APPROVED. It keeps Langfuse, with raw-JSON editing guarded by the schema for the MVP and a schema-driven Curator form in Epic E. ADR-0001 is superseded and ASM-05 is resolved.

**How the EX-C1 interactions were run:**
- **Via API:** the nested-field edit, table-cell edit and new-row add, each followed by read-back.
- **In the live UI:** the invalid-value rejection. Save was blocked and the stored value stayed unchanged.

Both paths write through the same schema-validated endpoint, and the UI offers only a single raw-JSON editor, so the result applies to both.
