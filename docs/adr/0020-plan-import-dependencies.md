# ADR-0020: Plan Import Dependencies

Status: Accepted. Introduced in 1.0.2. Extends [ADR-0018](0018-curated-starter-resources.md) and preserves [ADR-0016](0016-compatible-planting-plans.md).

## Decision

Local VFP imports and curated recommendations share a SystemHost-owned preflight, confirmation and import operation. Local API requires management authentication; Studio displays typed results without downloading or unpacking packages itself. Core, strategy generators and exchange formats do not acquire network dependencies.

Installed compatible strategies are preferred, including untrusted packages for import only. When missing, the resource index proposes the latest published stable version in the same major, not below the saved baseline. The exact baseline must match its original fingerprint; exceptional versions follow ADR-0016. Downloaded packages must pass identity, fingerprint, role and parameter checks before installation. Import never grants or restores execution trust.

Confirmation freezes package bytes, exact authorized downloads and overwrite permissions, not all future local compatibility resolution. A newly installed valid local strategy may be reused before commit without another download or rewriting the plan. Execution tasks still freeze their exact resolved version and fingerprint.

Plan candidate catalog links and legacy catalog hints identify optional source catalogs by target type and catalog ID. Missing sources can be recommended once, unchecked by default. Skipping, offline discovery or a failed catalog import does not block a self-contained plan. Existing catalogs are preserved. Importing a source never updates saved vegetation snapshots, weights, scales or parameters and never downloads PAK files.

The resource center publishes a v2 recommendation manifest and a separate inspected dependency index. The v1 manifest remains an explicit assets/strategies projection. Package metadata comes from static inspection of actual attachments, not author-maintained URLs or website relationships. Controlled HTTPS sources, archive bounds, hashes and ownership checks do not constitute signatures or a code safety audit.

Conflicting plans require explicit replacement approval and an atomic generation check. Independent resources can succeed despite another failure; installed shared dependencies are not rolled back. Cancellation stops outstanding acquisition and subsequent imports, while an atomic commit already entered may finish. Staging, pending confirmations, polling and host threads have bounded lifetimes and paired cleanup.

## Validation

Tests cover compatibility, untrusted reuse, exact download authorization, local VFS recovery, catalog skipping and failures, concurrent edits, partial success, authentication and cleanup. Real published packages are imported into an isolated empty Library without executing strategies or modifying existing user data. Both manifest protocols remain independently parseable.

See [resource import API](../architecture/RESOURCE_IMPORTS.md) for transport details.
