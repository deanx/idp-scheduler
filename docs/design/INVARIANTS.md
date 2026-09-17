# System invariants — cross-cutting functional properties

Properties no single story owns but the whole system must always hold — tenant isolation, referential integrity, "the ledger always balances", "every order has exactly one payment". Each has an executable check that runs at /qa for touched stories and system-wide at /signoff. A story that violates an invariant blocks Done. Curated by Soneca (/design).

| ID | Invariant (must always hold) | Why it matters | Check (test/assertion) | Scope (data/modules it spans) | Status |
|----|------------------------------|----------------|------------------------|-------------------------------|--------|
