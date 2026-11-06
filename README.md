# TradingAgents SEC/XBRL public CI pilot

This repository contains **only a reviewed, small testable subset** of the
Apache-2.0-licensed TradingAgents code, published for independent deterministic
SEC/XBRL regression testing. It has an **independent clean Git history**.

The original TradingAgents research framework is by
[TauricResearch/TradingAgents](https://github.com/TauricResearch/TradingAgents).
See [LICENSE](LICENSE) for the Apache-2.0 terms.

## Exactly what is tested

- SEC filing candidate selection and report-date/acceptance-date eligibility.
- Numeric XBRL instance parsing, IFRS/US-GAAP concepts, and exact periods.
- Explicit accession/index/XML binding with constructed offline fixtures.
- Source-file SHA-256 checks before executing the published test subset.

The tests use **constructed submissions metadata and XML**, sometimes with
publicly known SEC accession/issuer labels or example financial amounts.
They are not original SEC submissions or retrieved issuer documents.

**Not covered:** the full private application, financial statement completeness,
real SEC-source authenticity, historical first-publication proof, analyst or
trading workflows, model-generated conclusions, live APIs, paid AI calls, real
brokerage accounts, or releases. No original SEC downloads are present here.

This public CI result applies only to the exact snapshot identified in
[ci-publication-manifest.json](ci-publication-manifest.json).
Any associated private development remains authoritative and independently
subject to its own tests, review and release approvals.

No credentials or private repository access are required by this repository.
