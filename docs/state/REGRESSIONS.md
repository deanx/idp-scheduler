# Regression watch — defects promoted to permanent tests

Staff-gated: not every bug earns a permanent test. At `/qa`, Atchim (Staff Engineer) rules whether a confirmed defect represents a *class* worth pinning forever — a regression test, or (for an LLM feature) an eval golden-set entry. Pinned defects can't silently recur; a one-off isn't pinned so the suite doesn't bloat.

| ID | Defect (the class it guards against) | Origin (QA-N / bug card) | Kind | Permanent-test location | Status |
|----|--------------------------------------|--------------------------|------|-------------------------|--------|
