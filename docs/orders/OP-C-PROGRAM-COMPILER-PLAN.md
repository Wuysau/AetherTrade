# OP-C program compiler implementation plan

Implements the approved ADR-ORDER-001 and reference architecture §7–9/11.
The parent task assigned implementation directly; the public v2 contract and
original hashes are frozen. Python dataclasses remain the implementation medium.

- [x] Add parameterized failing tests for product routing, fields, exact sizing,
  incompatible combinations, revision drift, staged receipts and child origins.
- [x] Add internal typed product profiles with explicit provenance and revision
  binding. Do not infer product rules or qualification from schema presence.
- [x] Compile immutable request plans with fixed namespaces/operations and typed
  fields; retain the full original specs and hashes. Reuse exact grid helpers.
- [x] Distinguish one native mother request from observation-only venue children;
  local children remain staged and require independent command admission.
- [x] Verify targeted and existing contract/compiler regressions, record official
  source discrepancies and limitations, then commit reviewable changes.

Public contract gaps (native VP participation and lifecycle operations) are
reported to the parent; no public schema is altered or field silently dropped.
Financial reservation, permits, dispatch and market matching stay separate.
All actual native qualification remains NOT_RUN.
