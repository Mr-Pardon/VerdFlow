"""Polygonize verified highlights across a picked component or an entire Actor."""

from collections import defaultdict, deque
from collections.abc import Iterator
from dataclasses import dataclass, field
from math import floor
from pathlib import Path
from struct import unpack
from warnings import catch_warnings, simplefilter


ACTOR_CAPTURE_SIZE = 1024
ACTOR_CAPTURE_FRAMES = ("magenta", "cyan", "normal")
ACTOR_CAPTURE_WIDTH = 160
_HALO = 32
_CORE = ACTOR_CAPTURE_SIZE - 2 * _HALO
_SCALE = ACTOR_CAPTURE_WIDTH / ACTOR_CAPTURE_SIZE
_BOUNDS_PAD = 1
_DIRECTIONS = ((0, -1), (0, 1), (-1, 0), (1, 0))
_PNG_HEADER = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
_PNG_END = b"\x00\x00\x00\x00IEND\xae\x42\x60\x82"


def actor_capture_ready(path: Path) -> bool:
    """Do not read a partially written frame or follow a substituted path."""
    try:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise ValueError("Invalid actor capture path")
        with path.open("rb") as image:
            header = image.read(29)
            if len(header) < 29:
                return False
            size = ACTOR_CAPTURE_SIZE
            if (header[:16] != _PNG_HEADER or unpack(">II", header[16:24]) != (size, size)
                    or header[24:26] not in (b"\x08\x02", b"\x08\x06")):
                raise ValueError("Invalid actor capture grid")
            if image.seek(0, 2) > size * size * 5:
                raise ValueError("Actor capture exceeds the frame budget")
            image.seek(-len(_PNG_END), 2)
            return image.read() == _PNG_END
    except (FileNotFoundError, PermissionError):
        return False


@dataclass(frozen=True, slots=True)
class ActorFootprint:
    indices: tuple[int, ...]
    coordinates: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...]


def _pixels(path: Path):
    import rasterio
    from rasterio.errors import NotGeoreferencedWarning

    if not actor_capture_ready(path):
        raise ValueError("Incomplete actor capture")
    with rasterio.Env(GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR"), catch_warnings():
        simplefilter("ignore", NotGeoreferencedWarning)
        with rasterio.open(path, driver="PNG") as source:
            return source.read((1, 2, 3), window=((_HALO, _HALO + _CORE),) * 2).astype("int16")


_Tile = tuple[int, int]
_Point3 = tuple[float, float, float]


@dataclass(eq=False)
class _Part:
    tile: _Tile
    seed: bool
    edges: tuple[tuple[tuple[float, float], ...], ...]
    geometry: object = None


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _polygons(part)


def _spans(geometry, axis: int):
    if geometry.geom_type == "LineString" and not geometry.is_empty:
        yield geometry.bounds[axis], geometry.bounds[axis + 2]
    elif hasattr(geometry, "geoms"):
        for part in geometry.geoms:
            yield from _spans(part, axis)


def _tile_parts(mask, tile: _Tile, seeds, keep_geometry: bool, *, keep_all: bool = False):
    from rasterio.features import shapes
    from rasterio.transform import Affine
    from shapely import make_valid
    from shapely.geometry import LineString, shape

    r, c = (v * _CORE for v in tile)
    y, x = r + _CORE, c + _CORE
    sides = (LineString([(c, r), (c, y)]), LineString([(x, r), (x, y)]),
             LineString([(c, r), (x, r)]), LineString([(c, y), (x, y)]))
    for data, _ in shapes(mask.astype("uint8"), mask=mask, transform=Affine.translation(c, r), connectivity=4):
        geometry = shape(data)
        for part in _polygons(geometry if geometry.is_valid else make_valid(geometry)):
            seeded = any(part.covers(seed) for seed in seeds)
            min_c, min_r, max_c, max_r = part.bounds
            touches = (min_c == c, max_c == x, min_r == r, max_r == y)
            if keep_all or seeded or any(touches):
                edges = tuple(tuple(_spans(part.intersection(side), int(i < 2))) if touches[i] else ()
                              for i, side in enumerate(sides))
                yield _Part(tile, seeded, edges, part if keep_geometry else None)


@dataclass
class _Reach:
    tiles: dict[_Tile, tuple[_Part, ...]] = field(default_factory=dict)
    reached: set[_Part] = field(default_factory=set)

    def add(self, tile: _Tile, parts) -> set[_Tile]:
        self.tiles[tile] = tuple(parts)

        def neighbors(part, side, adjacent):
            return (other for other in self.tiles.get(adjacent, ()) if any(
                max(lo, start) < min(hi, end) for lo, hi in part.edges[side]
                for start, end in other.edges[side ^ 1]
            ))

        queue = deque()
        for part in self.tiles[tile]:
            if part.seed or any(other in self.reached for side, adjacent in self.sides(tile)
                                for other in neighbors(part, side, adjacent)):
                queue.append(part)
        frontier = set()
        while queue:
            part = queue.popleft()
            if part in self.reached:
                continue
            self.reached.add(part)
            for side, adjacent in self.sides(part.tile):
                if not part.edges[side]:
                    continue
                if adjacent not in self.tiles:
                    frontier.add(adjacent)
                else:
                    queue.extend(other for other in neighbors(part, side, adjacent) if other not in self.reached)
        return frontier

    @staticmethod
    def sides(tile: _Tile):
        r, c = tile
        return enumerate((r + dr, c + dc) for dr, dc in _DIRECTIONS)


class ActorFootprintTiles:
    """Use one tiled grid for connected picks and complete Actor coverage."""

    def __init__(self, points: tuple[_Point3, ...], bounds: tuple[_Point3, _Point3] | None = None,
                 *, whole_actor: bool = False, clip: tuple[_Point3, ...] | None = None):
        from shapely.geometry import Point, Polygon, box

        self._x0, self._y0 = (v + _CORE * _SCALE / 2 for v in points[0][:2])
        self._seeds = tuple(Point((self._y0 - y) / _SCALE, (self._x0 - x) / _SCALE) for x, y, _ in points)
        self._by_tile = defaultdict(list)
        for seed in self._seeds:
            self._by_tile[(floor(seed.y / _CORE), floor(seed.x / _CORE))].append(seed)
        self.initial = tuple(self._by_tile)
        self._scout = _Reach()
        self._exact = _Reach()
        self._bounds = bounds
        self._whole_parts: list[_Part] = []
        self._coverage: tuple[range, range] | None = None
        self._clip: Polygon | None = None
        self._scan = None
        if whole_actor:
            if bounds is None:
                raise ValueError("Actor bounds required")
            lo, hi = bounds
            step = _CORE * _SCALE
            def axis_range(axis: int, origin: float) -> range:
                return range(floor((origin - hi[axis] - _BOUNDS_PAD) / step),
                             floor((origin - lo[axis] + _BOUNDS_PAD) / step) + 1)

            self._coverage = (axis_range(0, self._x0), axis_range(1, self._y0))

            if clip is not None:
                self._clip = Polygon(((self._y0 - y) / _SCALE, (self._x0 - x) / _SCALE) for x, y, _ in clip)
                if not self._clip.is_valid or self._clip.area <= 0:
                    raise ValueError("提取范围必须是有面积且不自交的多边形。")
                actor_box = box((self._y0 - hi[1] - _BOUNDS_PAD) / _SCALE,
                                (self._x0 - hi[0] - _BOUNDS_PAD) / _SCALE,
                                (self._y0 - lo[1] + _BOUNDS_PAD) / _SCALE,
                                (self._x0 - lo[0] + _BOUNDS_PAD) / _SCALE)
                self._scan = self._clip.intersection(actor_box)
                if self._scan.area <= 0:
                    raise ValueError("绘制范围与所选构件不相交。")

    def actor_tiles(self) -> Iterator[_Tile]:
        if self._coverage is not None:
            rows, columns = self._coverage
            if self._scan is not None:
                from shapely.prepared import prep

                min_c, min_r, max_c, max_r = self._scan.bounds
                hits = prep(self._scan)
                for row in range(floor(min_r / _CORE), floor(max_r / _CORE) + 1):
                    for column in range(floor(min_c / _CORE), floor(max_c / _CORE) + 1):
                        if hits.intersects(self._tile_box((row, column))):
                            yield row, column
                return
            for row in rows:
                for column in columns:
                    yield row, column

    @staticmethod
    def _tile_box(tile: _Tile):
        from shapely.geometry import box

        r, c = (value * _CORE for value in tile)
        return box(c, r, c + _CORE, r + _CORE)

    def _clips_requested_geometry(self, part: _Part, side: int, adjacent: _Tile) -> bool:
        if self._clip is None:
            return True
        from shapely.geometry import LineString

        if self._clip.intersection(self._tile_box(adjacent)).area <= 0:
            return False
        row, col = part.tile
        vertical = side < 2
        axis = ((col if vertical else row) + (side & 1)) * _CORE
        for lo, hi in part.edges[side]:
            edge = LineString([(axis, lo), (axis, hi)] if vertical else [(lo, axis), (hi, axis)])
            if self._clip.intersection(edge).length > 0:
                return True
        return False

    @staticmethod
    def may_have_actor(magenta: Path) -> bool:
        # A verified pixel must meet the brightness threshold in both highlight frames.
        return bool((_pixels(magenta).min(axis=0) > 177).any())

    def center(self, tile: _Tile) -> tuple[float, float]:
        r, c = tile
        return self._x0 - (r + 0.5) * _CORE * _SCALE, self._y0 - (c + 0.5) * _CORE * _SCALE

    def _near_actor(self, tile: _Tile) -> bool:
        if self._bounds is None:
            return True
        # A tile of slack avoids trusting bounds right at the renderer's edge.
        margin = _CORE * _SCALE * 1.5
        return all(lo - margin <= v <= hi + margin
                   for lo, hi, v in zip(self._bounds[0], self._bounds[1], self.center(tile)))

    def scout(self, tile: _Tile, magenta: Path) -> list[_Tile]:
        # Every final pixel must satisfy this predicate. Only connected bright pixels can expand the search.
        mask = _pixels(magenta).min(axis=0) > 177
        more = self._scout.add(tile, _tile_parts(mask, tile, self._by_tile.get(tile, ()), False))
        return sorted(tile for tile in more if self._near_actor(tile))

    def begin_exact(self) -> None:
        self._scout = _Reach()

    def verify(self, tile: _Tile, folder: Path) -> None:
        import numpy as np

        magenta, cyan, base = (_pixels(folder / f"{frame}.png") for frame in ACTOR_CAPTURE_FRAMES)
        shift = (magenta[0] - magenta[1]) - (cyan[0] - cyan[1])
        bright = np.minimum(magenta.min(axis=0), cyan.min(axis=0))
        change = np.minimum(np.abs(magenta - base).max(axis=0), np.abs(cyan - base).max(axis=0))
        mask = (shift > 26) & (bright > 177) & (change > 75)
        if self._coverage is None:
            self._exact.add(tile, _tile_parts(mask, tile, self._by_tile.get(tile, ()), True))
        else:
            self._whole_parts.extend(_tile_parts(mask, tile, (), True, keep_all=True))

    def finish(self) -> tuple[list[ActorFootprint], list[int], list[int]]:
        from shapely import union_all

        def ring(line):
            return tuple((self._x0 - r * _SCALE, self._y0 - c * _SCALE) for c, r in list(line.coords)[:-1])

        if self._coverage is not None:
            rows, cols = self._coverage
            if any(part.edges[side] and (row not in rows or col not in cols)
                   and self._clips_requested_geometry(part, side, (row, col))
                   for part in self._whole_parts for side, (row, col) in _Reach.sides(part.tile)):
                return [], [0], []
            regions = []
            for component in _polygons(union_all([part.geometry for part in self._whole_parts])):
                simplified = component.simplify(0.75, preserve_topology=True)
                if self._clip is not None:
                    simplified = simplified.intersection(self._clip)
                for part in _polygons(simplified):
                    if not part.is_empty and part.area > 0:
                        regions.append(ActorFootprint((0,), ring(part.exterior), tuple(map(ring, part.interiors))))
            return regions, [], [] if regions else [0]

        reached = sorted(self._exact.reached, key=lambda part: (part.tile, part.geometry.bounds))
        truncated = [part.geometry.representative_point() for part in reached if any(
            part.edges[side] and adjacent not in self._exact.tiles for side, adjacent in self._exact.sides(part.tile)
        )]
        regions, clipped = [], []
        missed = set(range(len(self._seeds)))
        for component in _polygons(union_all([part.geometry for part in reached])):
            indices = tuple(i for i in sorted(missed) if component.covers(self._seeds[i]))
            if not indices:
                continue
            missed.difference_update(indices)
            if any(component.covers(point) for point in truncated):
                clipped.extend(indices)
                continue
            simplified = component.simplify(0.75, preserve_topology=True)
            if not all(simplified.covers(self._seeds[i]) for i in indices):
                simplified = component
            regions.append(ActorFootprint(indices, ring(simplified.exterior), tuple(map(ring, simplified.interiors))))
        return regions, sorted(clipped), sorted(missed)
