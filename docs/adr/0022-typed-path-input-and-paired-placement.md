# ADR-0022: Typed Path Input and Paired Placement

Status: Accepted. Introduced in 1.1.0.

Open polylines are immutable PathSets, never zero-area Regions. A strategy declares
exactly one input_geometry kind; legacy strategies default to polygon. Existing
polygon behavior is preserved. The polygon workbench does not execute line-only
strategies; WebUI selects the corresponding drawing tool without deleting drafts.

Path-generated PointSet v3 uses path_id; polygon v2 remains unchanged. Project
schema migration backs up the database before adding explicit path references.
Historical artifacts and frozen strategy identities are not rewritten.

The generator produces positions, role keys and rotations. Assignment independently
selects assets and scales. Multiple role instances can occupy one station with
unique IDs and a shared group ID; this is not atomic delivery or parent/child scale
inheritance. Core contains no path-placement algorithm or asset-specific rules.

The initial path strategy uses cumulative XY distance, explicit start offset and
outgoing corner tangents, with optional tangent circular arcs. It does not force
a final station. A preview may show the evaluated line without changing the
stored input path. Sampling preserves orientation and resolves identical rays
consistently. Input and instance budgets, cancellation and owned native preview
cleanup remain mandatory.

Dynamic roles, dual-input strategies, terrain tilt, closed loops, transactional
groups and shared author helpers are deferred. Native Explorer verification is
separate from synthetic Core integration and mocked SDK/UI verification.
