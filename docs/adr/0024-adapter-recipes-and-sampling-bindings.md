# ADR-0024: Adapter Capabilities, Input Recipes and Sampling Bindings

Status: Accepted. Built-in registry and explicit sampling bindings implemented.
Date: 2026-11-05.

## Context

Multiple input methods can share one elevation implementation. Binding elevation
implicitly to the importer or the first matching source prevents reuse and makes
future output targets unnecessarily dependent on input formats.

## Decision

An Adapter can provide multiple typed capabilities and recommended input recipes.
A recipe references registered preparation and elevation capabilities with explicit
resource slots; it is not arbitrary executable code or a general DAG. Capabilities
may come from different Adapters through public contracts.

Elevation remains a peer capability consuming generated points and an explicit
sampling binding. Immutable files, derived scene resources and live sessions have
different provenance and recovery requirements. Shared runtime resources do not
permit implicit source selection or fabricated reproducibility guarantees.

Output targets remain independent of input recipes. Product templates can recommend
or restrict a target, but target assets and assignments must be compatible before
export. Project offsets remain output-only. Each execution stage freezes the actual
capability and resource identities at its existing submission/confirmation boundary;
retries do not resolve newer defaults or silently substitute implementations.

Direct region sources and extraction from existing scenes keep their different
preconditions and common result finalization. FBX preparation and its specialized UI
remain supported. PathSet is not converted to RegionSet. The existing vector-plus-TIF
pair is one supported recipe, not a universal requirement on elevation providers.
Fixed geometry lineage, deferred output confirmation and current failure policies
remain in force during migration.

The normative semantics, ownership, transition and acceptance gates are specified in
[Adapter Workflow Contract](../architecture/ADAPTER_WORKFLOW_CONTRACT.md). Its logical
objects are not yet an HTTP protocol, a public plugin SDK or an executable manifest.

## Consequences

Capability registration, explicit sampling references, frozen execution bindings
and symmetric output registration require incremental implementation and regression
tests. Generic UI composition, merging region entry points and third-party package
installation are deferred. Discovery never grants execution trust or permissions.

## Validation

Future implementation must verify shared sampling across input recipes, unambiguous
same-format resources, unchanged point identities when resampling, target compatibility,
frozen recovery, reference-aware retention and existing FBX/geospatial/live workflows.
The built-in FBX, vector/TIF and live routes now use the controlled registry. Output
providers register additively, and new elevation commands carry explicit bindings in
v2 documents. Frozen workflow values are retained in Plant Run request v6,
continuation v2 and composition output request v2; previous document versions retain
their original behavior. Existing specialized scene entries use a centralized legacy
importer-to-recipe bridge until explicit input recipe selection is persisted.

Tests cover real GeoTIFF worker resampling, unchanged generated point identity,
frozen request reload and retention, registry rejection cases, and existing deferred
output/live workflows. Blender and DTS checks use protocol test doubles, not a new
real-scene acceptance run. Imagery input, generic UI, plugin installation and exact
cross-product-version code replay remain out of scope.
