"""Own the renderer files of one authenticated, tiled capture per session."""

from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from errno import ENOSPC
from pathlib import Path
from shutil import disk_usage
from tempfile import TemporaryDirectory
from threading import Lock, Timer
from time import monotonic
from typing import TYPE_CHECKING
from uuid import uuid4

from verdflow.dts_adapter import DtsLiveRegion, DtsLiveSelectionLimitError, LiveRegionDraftStore, normalize_live_regions

if TYPE_CHECKING:
    from verdflow.geospatial_worker import ActorFootprintTiles


class ActorCaptureError(ValueError):
    pass


def _cleanup(folder: TemporaryDirectory, attempts: int = 8) -> None:
    folder.cleanup()
    if attempts and Path(folder.name).exists():
        retry = Timer(0.5, _cleanup, (folder, attempts - 1))
        retry.daemon = True
        retry.start()


@dataclass
class _Capture:
    key: str
    folder: TemporaryDirectory
    points: tuple[tuple[float, float, float], ...]
    grid: "ActorFootprintTiles"
    height: float
    pending: deque[tuple[int, int]]
    scheduled: set[tuple[int, int]]
    scan: Iterator[tuple[int, int]] | None
    lock: Lock = field(default_factory=Lock)
    touched: float = field(default_factory=monotonic)
    stage: int = 0
    index: int = 0
    tiles: list[tuple[int, tuple[int, int]]] = field(default_factory=list)
    current: dict | None = None
    cell: tuple[int, int] = (0, 0)
    ready: bool = False


class ActorCaptures:
    def __init__(self) -> None:
        self._lock = Lock()
        self._sessions: dict[str, _Capture] = {}
        self._closed = False

    def reopen(self) -> None:
        with self._lock:
            self._closed = False

    def reserve(self, session: str, data: dict) -> dict:
        from verdflow.geospatial_worker import ActorFootprintTiles

        if set(data) - {"points", "bounds", "scope", "clip"}:
            raise ActorCaptureError("拾取出图包含不支持的参数。")
        scope = data.get("scope", "point")
        if scope not in ("point", "actor"):
            raise ActorCaptureError("拾取范围无效。")
        raw = data.get("points")
        if not isinstance(raw, list) or not 1 <= len(raw) <= 4096:
            raise ActorCaptureError("拾取点请求无效。")
        if scope == "actor" and len(raw) != 1:
            raise ActorCaptureError("整构件提取只接受一个场景点。")
        points = tuple(_vector(point, 3) for point in raw)
        bounds = data.get("bounds")
        if bounds is not None:
            if not isinstance(bounds, list) or len(bounds) != 2:
                raise ActorCaptureError("构件范围无效。")
            lo, hi = (_vector(bound, 3) for bound in bounds)
            if all(lo[i] <= hi[i] for i in range(3)) and all(
                lo[i] - 1 <= point[i] <= hi[i] + 1 for point in points for i in range(2)
            ):
                bounds = (lo, hi)
            else:
                bounds = None
        if scope == "actor" and bounds is None:
            raise ActorCaptureError("无法确定完整构件范围，请重新拾取。")
        clip = None
        if "clip" in data:
            raw_clip = data["clip"]
            if scope != "actor" or not isinstance(raw_clip, list) or len(raw_clip) < 3:
                raise ActorCaptureError("提取范围必须是至少包含三个顶点的构件选区。")
            clip = tuple(_vector(point, 3) for point in raw_clip)
        height = max(point[2] for point in points) + 380
        if bounds is not None:
            height = max(height, bounds[1][2] + 100)
        try:
            grid = ActorFootprintTiles(points, bounds, whole_actor=scope == "actor", clip=clip)
        except ValueError as error:
            raise ActorCaptureError(str(error)) from error
        with self._lock:
            if self._closed:
                raise ActorCaptureError("拾取服务已关闭。")
            stale = [(owner, capture.key) for owner, capture in self._sessions.items()
                     if monotonic() - capture.touched > 180 and not capture.lock.locked()]
        for owner, key in stale:
            self.discard(owner, key, idle_only=True)
        with self._lock:
            if self._closed:
                raise ActorCaptureError("拾取服务已关闭。")
            if session in self._sessions:
                raise ActorCaptureError("上一次拾取仍在处理，请稍后重试。")
            capture = _Capture(uuid4().hex, TemporaryDirectory(prefix="verdflow-actor-", ignore_cleanup_errors=True),
                               points, grid, height, deque(grid.initial), set(grid.initial),
                               grid.actor_tiles() if scope == "actor" else None)
            self._sessions[session] = capture
            return {"capture_id": capture.key}

    @contextmanager
    def _active(self, session: str, key: object):
        with self._lock:
            capture = self._sessions.get(session)
        if capture is None or key != capture.key:
            raise ActorCaptureError("拾取出图已失效，请重新提取。")
        with capture.lock:
            with self._lock:
                if self._sessions.get(session) is not capture:
                    raise ActorCaptureError("拾取出图已失效，请重新提取。")
            capture.touched = monotonic()
            try:
                yield capture
            finally:
                capture.touched = monotonic()

    def next_tile(self, session: str, key: object, frame: object) -> dict | None:
        from verdflow.geospatial_worker import ACTOR_CAPTURE_FRAMES, ACTOR_CAPTURE_SIZE, ACTOR_CAPTURE_WIDTH

        with self._active(session, key) as capture:
            if capture.stage == len(ACTOR_CAPTURE_FRAMES) or frame != ACTOR_CAPTURE_FRAMES[capture.stage]:
                raise ActorCaptureError("拾取出图阶段无效。")
            if capture.current is not None and not capture.ready:
                return capture.current
            capture.current = None
            if capture.stage == 0:
                cell = (next(capture.scan, None) if capture.scan is not None else
                        capture.pending.popleft() if capture.pending else None)
                tile = capture.index
            elif capture.index < len(capture.tiles):
                tile, cell = capture.tiles[capture.index]
            else:
                cell = None
            if cell is None:
                capture.stage += 1
                capture.index = 0
                if capture.stage == 1:
                    capture.grid.begin_exact()
                return None
            if disk_usage(capture.folder.name).free < 64 * 1024**2:
                raise ActorCaptureError("临时磁盘空间不足，无法继续拾取出图。")
            folder = Path(capture.folder.name) / str(tile)
            folder.mkdir(exist_ok=True)
            capture.cell = cell
            capture.current = {
                "tile": tile,
                "location": [*capture.grid.center(cell), capture.height],
                "size": ACTOR_CAPTURE_SIZE, "width": ACTOR_CAPTURE_WIDTH,
                "path": (folder / f"{frame}.png").as_posix(),
            }
            capture.ready = False
            return capture.current

    def frame_ready(self, session: str, key: object, frame: object, tile: object) -> bool:
        from verdflow.geospatial_worker import ACTOR_CAPTURE_FRAMES, actor_capture_ready

        with self._active(session, key) as capture:
            current = capture.current
            if (capture.stage == len(ACTOR_CAPTURE_FRAMES) or frame != ACTOR_CAPTURE_FRAMES[capture.stage]
                    or current is None or type(tile) is not int or tile != current["tile"]):
                raise ActorCaptureError("拾取出图阶段无效。")
            if capture.ready:
                return True
            path = Path(current["path"])
            try:
                if not actor_capture_ready(path):
                    return False
                cell = capture.cell
                if frame == "magenta":
                    if capture.scan is not None:
                        if capture.grid.may_have_actor(path):
                            capture.tiles.append((tile, cell))
                        else:
                            try:
                                path.unlink()
                                path.parent.rmdir()
                            except PermissionError:
                                pass
                    else:
                        capture.tiles.append((tile, cell))
                        for neighbor in capture.grid.scout(cell, path):
                            if neighbor not in capture.scheduled:
                                capture.pending.append(neighbor)
                                capture.scheduled.add(neighbor)
                elif frame == "normal":
                    capture.grid.verify(cell, path.parent)
                    try:
                        for name in ACTOR_CAPTURE_FRAMES:
                            (path.parent / f"{name}.png").unlink()
                        path.parent.rmdir()
                    except PermissionError:
                        # Explorer can briefly retain the PNG handle after the final chunk is written.
                        # Keep the completed geometry; discarding the capture retries any leftover files.
                        pass
            except (OSError, ValueError) as error:
                raise ActorCaptureError("DTS 写出的正交影像不完整、格式无效或无法处理。") from error
            capture.ready = True
            capture.index += 1
            return True

    def resolve(self, session: str, key: object, drafts: Callable[[], LiveRegionDraftStore]) -> dict:
        from verdflow.geospatial_worker import ACTOR_CAPTURE_FRAMES

        try:
            with self._active(session, key) as capture:
                if capture.stage != len(ACTOR_CAPTURE_FRAMES):
                    raise ActorCaptureError("拾取出图尚未完成。")
                results, clipped, missed = capture.grid.finish()
                if capture.scan is not None:
                    if clipped:
                        raise ActorCaptureError("构件轮廓超出 Explorer 提供的范围，未加入不完整的区域。")
                    if not results:
                        return {"dataset_id": None, "region_count": 0}

                    def regions():
                        z = capture.points[0][2]
                        for i, result in enumerate(results, 1):
                            ring = result.coordinates
                            bbox = (min(x for x, _ in ring), min(y for _, y in ring), z,
                                    max(x for x, _ in ring), max(y for _, y in ring), z)
                            yield DtsLiveRegion(
                                f"live_{uuid4().hex}", f"拾取绿地 {i}", tuple((x, y, z) for x, y in ring), bbox,
                                tuple(tuple((x, y, z) for x, y in hole) for hole in result.holes),
                            )

                    dataset, count = drafts().register(regions())
                    return {"dataset_id": dataset, "region_count": count}
                rows = []
                for result in results:
                    z = capture.points[result.indices[0]][2]
                    rows.append({"region_id": f"live_{uuid4().hex}", "name": "拾取绿地",
                                 "coordinates": [(x, y, z) for x, y in result.coordinates],
                                 "holes": [[(x, y, z) for x, y in ring] for ring in result.holes]})
                regions = normalize_live_regions(rows) if rows else []
                return {"matches": [{"indices": result.indices, "region": asdict(region)}
                                    for result, region in zip(results, regions, strict=True)],
                        "clipped": clipped, "missed": missed}
        except (ActorCaptureError, DtsLiveSelectionLimitError) as error:
            raise ActorCaptureError(str(error)) from error
        except OSError as error:
            if error.errno == ENOSPC:
                raise ActorCaptureError("临时磁盘空间不足，未登记本次提取的选区。请释放空间后重试。") from error
            raise ActorCaptureError("无法读写 DTS 拾取出图，请重试。") from error
        except (ValueError, ImportError) as error:
            raise ActorCaptureError("无法转换 DTS 拾取出图，请重试。") from error
        finally:
            self.discard(session, key)

    def discard(self, session: str, key: object, *, idle_only: bool = False) -> None:
        with self._lock:
            capture = self._sessions.get(session)
        if capture is None or capture.key != key:
            return
        with capture.lock:
            if idle_only and monotonic() - capture.touched <= 180:
                return
            with self._lock:
                if self._sessions.get(session) is not capture:
                    return
                del self._sessions[session]
            _cleanup(capture.folder)

    def close_session(self, session: str) -> None:
        with self._lock:
            capture = self._sessions.get(session)
        if capture is not None:
            self.discard(session, capture.key)

    def close(self) -> None:
        with self._lock:
            self._closed = True
            sessions = list(self._sessions.items())
        for session, capture in sessions:
            self.discard(session, capture.key)


def _vector(value: object, count: int) -> tuple:
    if not isinstance(value, list) or len(value) != count or any(
        type(v) not in (int, float) or not -1e9 < v < 1e9 for v in value
    ):
        raise ActorCaptureError("拾取坐标无效。")
    return tuple(value)
