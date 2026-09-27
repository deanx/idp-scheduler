# `mulesoft_docs_prompts_example.json` — NOT a live capture

The `pages[].prompts` block is copied **verbatim from MuleSoft's documentation**
(docs.mulesoft.com/idp/integrating-idp-with-anypoint-studio; source:
github.com/mulesoft/docs-idp, `modules/ROOT/pages/integrating-idp-with-anypoint-studio.adoc`),
retrieved 2026-09-27. Only the surrounding `status` and the empty `fields`/`tables` were added so
the body is a complete response.

It is the best evidence available and it is still **not SR-1 evidence**. No action in the project's
org emits prompts, and none of the three real captures (`tests/fixtures/live/`) has a `prompts` key
or a `pages` wrapper. The first real response that carries prompts should replace this file as the
pin (DEBT-69(a)).
