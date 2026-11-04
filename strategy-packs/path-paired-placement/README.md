# 沿线树池种植

Polyline-only strategy, version 1.0.0. The generator uses cumulative XY arc-length
interpolation in the existing standard-library-only worker. Open paths only; distance carries across vertices. Exact corners
use the outgoing tangent when rounding is off. A positive corner radius inserts
tangent circular arcs without changing the input path. Each corner consumes at
most half of either adjacent segment; straight and reversing corners are left
alone. Distance follows the resulting lines and arcs. The start offset is
explicit; endpoints are not forced.

The optional preview hook samples the evaluated path for display. It is not
part of the formal PointSet.

Each station emits independently identified vegetation (`vegetation`) and optional
tree pits (`planter`) with a shared group ID. Roles do not select assets, scales or
targets. Configure random vegetation scales and fixed tree-pit scales in the binding.
The tree pit's local +Y follows the evaluated tangent; vegetation yaw is seeded and uniform over
a full turn.
Disabling one role never changes the remaining instances, IDs or orientation.
Output is path PointSet v3; the current polygon workbench cannot use this pack.

The generator caps total emitted instances at 100,000. Models should use compatible
local axes and ground-level pivots. Group IDs do not imply atomic DTS delivery.
