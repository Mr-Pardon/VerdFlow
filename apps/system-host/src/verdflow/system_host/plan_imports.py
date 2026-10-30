"""One bounded import session shared by local VFP and curated v2 recommendations."""

from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread, Timer
from typing import Callable
from uuid import uuid4

from verdflow.planting import validate_binding_strategy
from verdflow.asset_package import inspect_asset_catalog_package
from verdflow.profile_package import ProfilePackageInspection, inspect_role_binding_profile_package
from verdflow.strategy_worker import StrategyPackage, open_strategy_package_archive

from .plan_import_library import PlanImportLibrary
from .resource_downloads import DownloadCancelled, MAX_PACKAGE_BYTES, StarterResource, download_package
from .resource_index import (
    IndexedResource, PlanMetadata, ResourceIndex, accepts_dependency, choose_strategy, load_index, plan_metadata,
)
from .starter_resources import ImportChoice, StarterImportItem, StarterImportOperation, StarterResourceService


@dataclass
class PreparedItem:
    choice: ImportChoice
    indexed: IndexedResource | None = None
    path: Path | None = None
    metadata: PlanMetadata | None = None
    inspection: ProfilePackageInspection | None = None
    generation: int = 0
    dependency: str = ""
    digest: str = ""
    explicit: bool = False


class PlanImportService(StarterResourceService):
    def __init__(self, importer: Callable[[StarterResource, Path], bool], library: PlanImportLibrary,
                 strategy_installer: Callable[[StrategyPackage], bool]) -> None:
        super().__init__(importer)
        self.library = library
        self._strategy_installer = strategy_installer
        self._prepared: dict[str, PreparedItem] = {}
        self._temporary: TemporaryDirectory | None = None
        self._expiry: Timer | None = None
        self._index_revision = ""

    def prepare(self, paths: tuple[str, ...], *, recommended: bool = False,
                strategy_paths: tuple[str, ...] = ()) -> StarterImportOperation:
        if len(paths) > 32 or len(strategy_paths) > 8 or (recommended and paths) or (not recommended and not paths):
            raise ValueError("请选择 1 到 32 个方案文件，或读取推荐清单。")
        with self._lock:
            if self._closed or (self._operation and self._operation.status in {
                "preparing", "awaiting_confirmation", "running", "cancelling",
            }):
                raise ValueError("已有资源导入操作，请先完成或取消。")
            self._release()
            self._cancel = Event()
            self._operation = StarterImportOperation(str(uuid4()), "preparing", ())
            self._temporary = TemporaryDirectory(prefix="verdflow-import-")
            self._thread = Thread(target=self._prepare, args=(paths, recommended, strategy_paths),
                                  name="resource-preflight", daemon=True)
            self._thread.start()
            return self._operation

    def _stage(self, source: str, suffix: str) -> Path:
        self._check_cancel()
        path = Path(source).expanduser().resolve(strict=True)
        limit = 8 * 1024 * 1024 if suffix == ".vfp" else MAX_PACKAGE_BYTES
        if not path.is_file() or path.stat().st_size > limit:
            raise ValueError("导入文件超出大小限制。")
        if sum(p.stat().st_size for p in Path(self._temporary.name).iterdir()) + path.stat().st_size > 512 * 1024 * 1024:
            raise ValueError("本地导入总大小超出 512 MiB，请分批导入。")
        target = Path(self._temporary.name) / (uuid4().hex + suffix)
        with path.open("rb") as reader, target.open("xb") as writer:
            size = 0
            while chunk := reader.read(128 * 1024):
                self._check_cancel()
                size += len(chunk)
                if size > limit:
                    raise ValueError("导入文件超出大小限制。")
                writer.write(chunk)
        return target

    def _check_cancel(self) -> None:
        if self._cancel.is_set():
            raise DownloadCancelled()

    def _prepare(self, paths: tuple[str, ...], recommended: bool, strategy_paths: tuple[str, ...]) -> None:
        warnings: list[str] = []
        try:
            selected = load_index(starter=True) if recommended else ResourceIndex("", ())
            entries: dict[str, PreparedItem] = {}
            for item in selected.resources:
                r = item.resource
                entries[r.id] = PreparedItem(ImportChoice(r.id, r.name, r.kind, r.version, r.bytes, page_url=r.page_url),
                                            indexed=item, metadata=item.plan, explicit=True)
            for source in paths:
                key = "plan-" + uuid4().hex
                try:
                    path = self._stage(source, ".vfp")
                    inspected = inspect_role_binding_profile_package(path)
                    meta = plan_metadata(inspected)
                    entries[key] = PreparedItem(ImportChoice(key, inspected.profile.display_name, "plans"), path=path,
                                                metadata=meta, inspection=inspected, digest=inspected.package_hash)
                except DownloadCancelled:
                    raise
                except Exception as error:
                    entries[key] = PreparedItem(ImportChoice(key, Path(source).name, "plans", blocked=True, message=str(error)))
            if not entries:
                raise ValueError("当前暂无推荐资源。")
            plan_ids: set[str] = set()
            states = {}
            for key, entry in entries.items():
                if entry.metadata:
                    if entry.metadata.profile_id in plan_ids:
                        raise ValueError("本批次存在相同身份的多个方案，请分别导入并确认。")
                    plan_ids.add(entry.metadata.profile_id)
                    state = self.library.inspect(entry.metadata, entry.inspection)
                    states[key] = state
                    entry.generation = state.generation
                    entry.choice = replace(entry.choice, conflict=state.generation > 0 and not state.same_content,
                                           message=(f"本地兼容策略 v{state.strategy_version}" if state.strategy_version else "需要补齐策略"))
            local: list[PreparedItem] = []
            for source in strategy_paths:
                path = self._stage(source, ".vfs")
                with open_strategy_package_archive(path) as opened:
                    p = opened.package
                    key = "local-strategy-" + p.package_hash.removeprefix("sha256:")
                    resource = StarterResource(key, p.strategy["display_name"], "strategies", p.package_version,
                                               p.package_id, path.name, path.stat().st_size,
                                               hashlib.sha256(path.read_bytes()).hexdigest(), "", "")
                    local.append(PreparedItem(ImportChoice(key, resource.name, "strategies", resource.version),
                                               indexed=IndexedResource(resource, p.package_hash), path=path))
            need_index = any(not s.strategy_version or s.missing_catalogs for s in states.values())
            index = ResourceIndex("", ())
            if need_index:
                try:
                    index = load_index()
                    self._index_revision = index.revision
                except Exception:
                    warnings.append("暂时无法查询资源中心；可选择本地策略包。可选资产库不影响方案导入。")
            self._check_cancel()
            resources = {}
            for item in (*selected.resources, *index.resources):
                identity = (item.resource.kind, item.target_type, item.resource.identity, item.resource.version)
                previous = resources.get(identity)
                if previous and previous != item:
                    raise ValueError("推荐清单与资源索引不一致，请刷新后重试。")
                resources[identity] = item
            for key, state in states.items():
                entry = entries[key]
                meta = entry.metadata
                if not state.strategy_version or any(accepts_dependency(
                        meta, p.indexed.resource.identity, p.indexed.resource.version, p.indexed.strategy_hash) for p in local):
                    candidate = choose_strategy(meta, tuple(p.indexed for p in local))
                    if candidate:
                        dependency = next(p for p in local if p.indexed == candidate)
                    else:
                        candidate = choose_strategy(meta, tuple(resources.values()))
                        dependency = None
                    if candidate:
                        r = candidate.resource
                        dep_key = r.id
                        if dep_key in entries and entries[dep_key].indexed != candidate:
                            dep_key = r.id + "-" + r.sha256
                        if dep_key not in entries:
                            entries[dep_key] = dependency or PreparedItem(
                                ImportChoice(dep_key, r.name, "strategies", r.version, r.bytes, page_url=r.page_url), indexed=candidate)
                        entry.dependency = dep_key
                        entry.choice = replace(entry.choice, message=f"保存于 v{meta.strategy_version}；补齐兼容策略 v{r.version}")
                    else:
                        entry.choice = replace(entry.choice, blocked=True,
                                               message="未找到可获取的兼容策略，请选择本地 .vfs 后重新检查。")
                for catalog in state.missing_catalogs:
                    matches = [r for r in resources.values() if r.resource.kind == "assets"
                               and r.target_type == catalog.target_type and r.resource.identity == catalog.catalog_id]
                    if len(matches) == 1:
                        r = matches[0].resource
                        if r.id not in entries:
                            entries[r.id] = PreparedItem(ImportChoice(r.id, r.name, "assets", r.version, r.bytes,
                                optional=True, message="可选来源资产库；跳过不影响方案使用", page_url=r.page_url), indexed=matches[0])
                    else:
                        warnings.append(f"来源资产库 {catalog.catalog_id} 暂无唯一可获取条目，可跳过。")
            if len(entries) > 128:
                raise ValueError("本批资源过多，请分批导入。")
            with self._lock:
                self._check_cancel()
                self._prepared = entries
                self._operation = replace(self._operation, status="awaiting_confirmation",
                    choices=tuple(e.choice for e in entries.values()), warnings=tuple(dict.fromkeys(warnings)),
                    items=tuple(StarterImportItem(e.choice.id, e.choice.name, total_bytes=e.choice.bytes,
                                status="blocked" if e.choice.blocked else "pending") for e in entries.values()))
                self._expiry = Timer(900, self.cancel, args=(self._operation.operation_id,))
                self._expiry.daemon = True
                self._expiry.start()
        except Exception as error:
            with self._lock:
                self._operation = replace(self._operation,
                    status="cancelled" if isinstance(error, DownloadCancelled) else "failed",
                    error="" if isinstance(error, DownloadCancelled) else str(error))
                self._release()

    def confirm(self, operation_id: str, optional_ids: tuple[str, ...], overwrite_ids: tuple[str, ...]) -> StarterImportOperation:
        with self._lock:
            operation = self.get(operation_id)
            if operation.status != "awaiting_confirmation":
                raise ValueError("导入预检查已结束或过期，请重新检查。")
            optional = {key for key, e in self._prepared.items() if e.choice.optional}
            conflicts = {key for key, e in self._prepared.items() if e.choice.conflict and not e.choice.blocked}
            if not set(optional_ids) <= optional or not set(overwrite_ids) <= conflicts:
                raise ValueError("确认包含未展示的资源或覆盖操作。")
            chosen = {key for key, e in self._prepared.items()
                      if not e.choice.blocked and (not e.choice.optional or key in optional_ids)
                      and (not e.choice.conflict or key in overwrite_ids)}
            # Dependencies of skipped plans are not installed unless explicitly recommended themselves.
            required = {self._prepared[key].dependency for key in chosen if self._prepared[key].metadata}
            chosen = {key for key in chosen if self._prepared[key].choice.kind != "strategies"
                      or self._prepared[key].explicit or key in required}
            if sum(self._prepared[key].choice.bytes for key in chosen) > 1024 * 1024 * 1024:
                raise ValueError("本批下载超过 1 GiB，请减少可选资源或分批导入。")
            for index, item in enumerate(operation.items):
                if item.id not in chosen and item.status != "blocked":
                    self._update(index, status="skipped")
            if self._expiry:
                self._expiry.cancel()
            self._operation = replace(self._operation, status="running")
            self._thread = Thread(target=self._run_plans, args=(chosen,), name="plan-imports", daemon=True)
            self._thread.start()
            return self._operation

    def _path(self, entry: PreparedItem, index: int) -> Path:
        if entry.path:
            return entry.path
        r = entry.indexed.resource
        path = Path(self._temporary.name) / (uuid4().hex + {"plans": ".vfp", "assets": ".vfa", "strategies": ".vfs"}[r.kind])
        self._update(index, status="downloading")
        download_package(r, path, self._cancel, lambda size: self._update(index, downloaded_bytes=size))
        entry.path = path
        return path

    def _run_plans(self, chosen: set[str]) -> None:
        positions = {item.id: i for i, item in enumerate(self._operation.items)}
        inspections: dict[str, ProfilePackageInspection] = {}
        failed: set[str] = set()
        try:
            # Fetch and verify VFP facts before installing any dependency on their behalf.
            for key in chosen:
                e = self._prepared[key]
                if not e.metadata:
                    continue
                try:
                    self._check_cancel()
                    inspected = inspect_role_binding_profile_package(self._path(e, positions[key]))
                    if plan_metadata(inspected) != e.metadata or (e.digest and inspected.package_hash != e.digest):
                        raise ValueError("方案内容与预检查不一致，请重新检查。")
                    inspections[key] = inspected
                except DownloadCancelled:
                    raise
                except Exception as error:
                    failed.add(key)
                    self._update(positions[key], status="failed", message=str(error))
            for kind in ("strategies", "plans", "assets"):
                for key, e in self._prepared.items():
                    if key not in chosen or key in failed or e.choice.kind != kind:
                        continue
                    self._check_cancel()
                    index = positions[key]
                    try:
                        if kind == "plans":
                            self._update(index, status="importing")
                            existing, message = self.library.commit(inspections[key], e.generation)
                        elif kind == "assets" and e.choice.optional and self.library.catalog_exists(
                                e.indexed.target_type, e.indexed.resource.identity):
                            existing, message = True, "保留已存在的本地资产库。"
                        else:
                            path = self._path(e, index)
                            self._check_cancel()
                            if kind == "strategies":
                                with open_strategy_package_archive(path) as opened:
                                    p = opened.package
                                    if (p.package_id, p.package_version, p.package_hash) != (
                                            e.indexed.resource.identity, e.indexed.resource.version, e.indexed.strategy_hash):
                                        raise ValueError("策略身份、版本或内容指纹与确认内容不符。")
                                    dependents = [k for k in inspections if self._prepared[k].dependency == key and k not in failed]
                                    for plan_key in dependents:
                                        try:
                                            validate_binding_strategy(p.strategy, json.loads(inspections[plan_key].profile.canonical_json))
                                        except Exception as error:
                                            failed.add(plan_key)
                                            self._update(positions[plan_key], status="failed", message=str(error))
                                    if dependents and all(k in failed for k in dependents):
                                        raise ValueError("策略与待导入方案不兼容，未安装。")
                                    if not e.explicit and not dependents:
                                        self._update(index, status="skipped", message="依赖此策略的方案未通过检查，未安装。")
                                        continue
                                    self._check_cancel()
                                    self._update(index, status="importing")
                                    existing = self._strategy_installer(p)
                            else:
                                inspected_asset = inspect_asset_catalog_package(path)
                                if inspected_asset.catalog.target_type != e.indexed.target_type:
                                    raise ValueError("资产库目标平台与索引不符，未导入。")
                                self._update(index, status="importing")
                                existing = self._importer(e.indexed.resource, path)
                            message = "策略信任状态保持不变。" if kind == "strategies" else ""
                        self._update(index, status="existing" if existing else "imported", message=message)
                    except Exception as error:
                        if isinstance(error, DownloadCancelled):
                            raise
                        failed.add(key)
                        self._update(index, status="failed", message=str(error))
        except DownloadCancelled:
            pass
        except Exception as error:
            with self._lock:
                self._operation = replace(self._operation, error=str(error))
        finally:
            with self._lock:
                items = tuple(replace(item, status="cancelled") if item.status in {"pending", "downloading", "importing"} else item
                              for item in self._operation.items)
                errors = bool(self._operation.error) or any(i.status in {"failed", "blocked"} for i in items)
                success = any(i.status in {"existing", "imported"} for i in items)
                status = "cancelled" if self._cancel.is_set() else "partial" if errors and success else "failed" if errors else "completed"
                self._operation = replace(self._operation, status=status, items=items)
                self._release()

    def cancel(self, operation_id: str) -> StarterImportOperation:
        with self._lock:
            operation = self.get(operation_id)
            if operation.status in {"preparing", "running", "awaiting_confirmation"}:
                self._cancel.set()
                waiting = operation.status == "awaiting_confirmation"
                self._operation = replace(operation, status="cancelled" if waiting else "cancelling")
                if waiting:
                    self._release()
            return self._operation

    def _release(self) -> None:
        if self._expiry:
            self._expiry.cancel()
            self._expiry = None
        if self._temporary:
            self._temporary.cleanup()
            self._temporary = None
        self._prepared = {}

    def close(self) -> None:
        super().close()
        with self._lock:
            self._release()
