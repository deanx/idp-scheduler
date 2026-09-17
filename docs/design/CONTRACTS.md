# Cross-feature contracts

The interface between two features — an event, DTO, API surface, or shared schema one feature produces and another consumes. Consumer-driven: the consumer's expectation IS the contract; producer and consumer both test against it, so drift on either side breaks a test even when the other is mocked. Curated by Soneca (/design); tests written by Dengoso (/implement); verified by Zangado at /qa (touched contracts) and /signoff (all).

| ID | Contract (the interface) | Producer (UC/module) | Consumer(s) (UC/module) | Shape / schema ref | Contract-test location | Status |
|----|--------------------------|----------------------|-------------------------|--------------------|------------------------|--------|
