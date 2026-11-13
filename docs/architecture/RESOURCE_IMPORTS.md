# Resource Imports

SystemHost owns acquisition and import operations under [ADR-0020](../adr/0020-plan-import-dependencies.md). Local API is the authenticated transport; Studio only presents choices and progress. Existing Library and package readers remain deterministic and network-free.

## API

All routes require management authentication.

| Request | Input | Result |
| --- | --- | --- |
| POST /api/v1/resource-imports/prepare | paths (up to 32 VFP), recommended=true, or selection={resource_id,version,revision}; optional strategy_paths (up to 8 VFS) | 202 operation |
| POST /api/v1/resource-imports/confirm | operation_id, optional_ids, overwrite_ids | 202 operation |
| GET /api/v1/starter-resources/import | operation_id query | operation |
| DELETE /api/v1/starter-resources/import | operation_id | cancellation state |
| GET /api/v1/resource-center | optional refresh=1 | display resources and local state |
| GET /api/v1/resource-center/detail | id, optional version | description, versions and reviewed import availability |
| GET /api/v1/resource-center/strategy-updates | optional refresh=1 | newer stable strategy versions and detail targets |

Preflight does not write Library. Local files are copied into private staging before inspection. Confirmations expire after 15 minutes. Cancel an old preflight before retrying with a local strategy. Optional catalogs and replacement permissions default to unchecked; the server only accepts IDs from the displayed choices.

The three entry modes are mutually exclusive. A native resource selection pins an exact resource version and index revision, not a download URL. Preflight reloads the reviewed index and rejects a changed revision before downloading. It then uses the same confirmation, dependency, verification and cancellation pipeline as recommended resources. Strategy versions coexist; same-ID/version content conflicts are blocked, and trust is never inherited.

## Native Browsing

Studio renders a native list and detail view, not a remote iframe. SystemHost owns bounded metadata retrieval and the read-only comparison with Library; neither Core nor Library acquires network responsibilities. The public catalog and versioned descriptions are presentation data, not installation authority. Import requires matching identity, version, hash and byte size in the reviewed import index.

Catalog/index metadata is cached for five minutes in memory. Immutable revisioned descriptions have a 24-document LRU. Refresh failures retain already loaded display content with a dismissible warning; import still requires a fresh preflight. No background update scheduler is introduced. Unmounted pages ignore late responses; closing an active import requests cancellation through the existing operation lifecycle.

Markdown disables raw HTML. Images are restricted to revisioned raster assets under the fixed resource origin; external links pass through the desktop shell's allowlist. PAK files remain external and are not downloaded or installed by VerdFlow. Strategy Library only links a newer stable version to its native resource details, where users can read release notes before choosing to import. A major-version hint does not promise plan compatibility.

Operation fields are operation_id, status, items, choices, warnings and error. States are preparing, awaiting_confirmation, running, cancelling, completed, partial, failed and cancelled. Choices contain id/name/kind/version/bytes/optional/conflict/blocked/message/page_url. Items include download progress and pending/downloading/importing/imported/existing/blocked/failed/skipped/cancelled states. A partial result is not whole-batch success.

Plan commits check the generation observed during preflight. Concurrent changes require another confirmation. Strategy trust remains independent. Missing source catalogs do not block a plan, and a catalog created after confirmation is not overwritten.

Direct local VFA import in the Asset Library reuses identical content. Different content with the same target and catalog ID requires an explicit overwrite; Studio pins the package hash, current catalog hash, and generation when confirming. A provider change cannot be overwritten. Existing plan snapshots are not modified.

## Discovery

The fixed resource root is https://mr-pardon.github.io/verdflow-resources/. Downloads use that repository's versioned Release attachments and approved GitHub CDN redirects. Plans cannot supply arbitrary download URLs.

- starter-resources.json: schemaVersion=1, assets and strategies only.
- starter-resources-v2.json: schemaVersion=2, revision and up to 32 selected resources of all three kinds.
- import-index.json: schemaVersion=1, revision and up to 1024 inspected published resource versions.

Shared resource fields are id/name/kind/version/identity/filename/bytes/sha256/url/page_url/status. Strategies add strategy_hash; assets add target_type. Plans add plan with profile_id/semantic_hash/strategy_id/strategy_version/strategy_hash/catalogs; each catalog has target_type/catalog_id. Metadata must match actual downloaded packages. Unknown publication states or ambiguous identities fail closed.

Documents are limited to 2 MiB, packages to 256 MiB and VFP to 8 MiB. Local staging is limited to 512 MiB and authorized batch downloads to 1 GiB. Existing controlled downloader timeouts apply. Index metadata is a preflight hint, not a substitute for actual plan and strategy validation.

The resource repository maintains collections/starter.json and derives both recommendation protocols. tools/inspect_resource_center.py creates static package inspection receipts; changed archives require renewed inspection. These receipts contain public metadata, not local paths, execution output or signatures. Resource selection can change independently of application releases; plan recommendations require a v2-capable client.
