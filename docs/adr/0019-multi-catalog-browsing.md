# ADR 0019: Multi-catalog browsing without merged catalogs

Status: Accepted

## Context

Users may combine plants from independently managed catalogs. Requiring a separate search for each catalog makes that workflow unnecessarily repetitive. A broader search scope must not become an execution dependency or modify saved planting configurations.

## Decision

SystemHost provides one read-only, paginated query across a compatible catalog scope. One target type and Provider are required. Asset names, source IDs and tags are searchable; collection filters remain qualified by their source catalog. Results carry source names and server-generated candidate identities, so colliding catalog-local IDs remain distinct.

Studio and the DTS WebUI share the `asset-browser-ui` package. Application-owned transports and thumbnails are injected. Catalog checkboxes affect browsing only: deselecting a catalog preserves selected plants and their weight/scale snapshots. The local preference is keyed by target/Provider and never enters a VFP or execution request. Default scope prefers a draft's source catalogs, then all compatible catalogs, when no local preference exists.

Pagination is global across the selected scope, with stable catalog/ordinal ordering and revision checks when catalog content changes. Missing or unreadable catalogs produce explicit partial-result warnings. SystemHost caches a bounded number of immutable catalog indexes; frontends do not fetch full catalogs for this browser.

Search requests are debounced. Closed or replaced views discard late replies, release observers and listeners, and abort browser fetches where the transport supports cancellation. Search does not create jobs or execute strategies.

## Consequences

Catalogs remain independently managed. Core, strategy responsibilities, package formats and immutable execution snapshots are unchanged. Same-model duplicates remain visible with an advisory warning, not silently merged. A source removed from browsing does not invalidate a self-contained configuration.

## Validation

Tests cover source-ID collisions, catalog-qualified categories, incompatible Providers, missing sources, updates between pages, request cleanup and preservation of selected candidates. The shared browser is exercised in the workbench, plan editor and live WebUI.
