# Resource Imports

SystemHost owns acquisition and import operations under [ADR-0020](../adr/0020-plan-import-dependencies.md). Local API is the authenticated transport; Studio only presents choices and progress. Existing Library and package readers remain deterministic and network-free.

## API

All routes require management authentication.

| Request | Input | Result |
| --- | --- | --- |
| POST /api/v1/resource-imports/prepare | paths (up to 32 VFP), or recommended=true; optional strategy_paths (up to 8 VFS) | 202 operation |
| POST /api/v1/resource-imports/confirm | operation_id, optional_ids, overwrite_ids | 202 operation |
| GET /api/v1/starter-resources/import | operation_id query | operation |
| DELETE /api/v1/starter-resources/import | operation_id | cancellation state |

Preflight does not write Library. Local files are copied into private staging before inspection. Confirmations expire after 15 minutes. Cancel an old preflight before retrying with a local strategy. Optional catalogs and replacement permissions default to unchecked; the server only accepts IDs from the displayed choices.

Operation fields are operation_id, status, items, choices, warnings and error. States are preparing, awaiting_confirmation, running, cancelling, completed, partial, failed and cancelled. Choices contain id/name/kind/version/bytes/optional/conflict/blocked/message/page_url. Items include download progress and pending/downloading/importing/imported/existing/blocked/failed/skipped/cancelled states. A partial result is not whole-batch success.

Plan commits check the generation observed during preflight. Concurrent changes require another confirmation. Strategy trust remains independent. Missing source catalogs do not block a plan, and a catalog created after confirmation is not overwritten.

## Discovery

The fixed resource root is https://mr-pardon.github.io/verdflow-resources/. Downloads use that repository's versioned Release attachments and approved GitHub CDN redirects. Plans cannot supply arbitrary download URLs.

- starter-resources.json: schemaVersion=1, assets and strategies only.
- starter-resources-v2.json: schemaVersion=2, revision and up to 32 selected resources of all three kinds.
- import-index.json: schemaVersion=1, revision and up to 1024 inspected published resource versions.

Shared resource fields are id/name/kind/version/identity/filename/bytes/sha256/url/page_url/status. Strategies add strategy_hash; assets add target_type. Plans add plan with profile_id/semantic_hash/strategy_id/strategy_version/strategy_hash/catalogs; each catalog has target_type/catalog_id. Metadata must match actual downloaded packages. Unknown publication states or ambiguous identities fail closed.

Documents are limited to 2 MiB, packages to 256 MiB and VFP to 8 MiB. Local staging is limited to 512 MiB and authorized batch downloads to 1 GiB. Existing controlled downloader timeouts apply. Index metadata is a preflight hint, not a substitute for actual plan and strategy validation.

The resource repository maintains collections/starter.json and derives both recommendation protocols. tools/inspect_resource_center.py creates static package inspection receipts; changed archives require renewed inspection. These receipts contain public metadata, not local paths, execution output or signatures. Resource selection can change independently of application releases; plan recommendations require a v2-capable client.
