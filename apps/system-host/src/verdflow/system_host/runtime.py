"""Owner-thread runtimes used by SystemHost."""

from __future__ import annotations

from concurrent.futures import Future
from datetime import UTC, datetime
from pathlib import Path
from queue import Empty, Queue
from threading import RLock, Thread
from typing import Callable, TypeVar, cast
from uuid import uuid4

from verdflow.core_host import (
    CoreHost,
    CoreHostConfig,
    CoreHostExtensionFactory,
)
from verdflow.library_store import LibraryStore
from verdflow.model_api import ModelApiProviderRegistry, SecretResolver
from verdflow.project_store import ProjectQueryStore
from verdflow.contracts import (
    JobInfo,
    JobStatus,
    MaterialFlowExecutionInfo,
    MaterialFlowExecutionStage,
    MaterialFlowExecutionStatus,
    PlantRunExecutionInfo,
    PlantRunExecutionStage,
    PlantRunExecutionStatus,
    ScenePreparationInfo,
    ScenePreparationStage,
    ScenePreparationStatus,
)

from .models import ProjectSessionInfo, ProjectSessionState


ResultT = TypeVar("ResultT")
_STOP = object()


class LibraryRuntime:
    """Keep the system LibraryStore connection on one dedicated thread."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._queue: Queue[object] = Queue()
        self._ready: Future[None] = Future()
        self._thread = Thread(
            target=self._run,
            name="verdflow-library-owner",
            daemon=True,
        )

    def start(self, timeout_seconds: float) -> None:
        self._thread.start()
        self._ready.result(timeout=timeout_seconds)

    def call(
        self,
        callback: Callable[[LibraryStore], ResultT],
        *,
        timeout_seconds: float,
    ) -> ResultT:
        future: Future[object] = Future()
        self._queue.put((callback, future))
        return cast(ResultT, future.result(timeout=timeout_seconds))

    def close(self, timeout_seconds: float) -> None:
        if not self._thread.is_alive():
            return
        self._queue.put(_STOP)
        self._thread.join(timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Library owner thread did not stop in time")

    def _run(self) -> None:
        try:
            with LibraryStore.open(self._root) as store:
                self._ready.set_result(None)
                while True:
                    item = self._queue.get()
                    if item is _STOP:
                        return
                    callback, future = cast(
                        tuple[Callable[[LibraryStore], object], Future[object]],
                        item,
                    )
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(callback(store))
                        except BaseException as error:
                            future.set_exception(error)
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)


class ProjectQueryRuntime:
    """Keep read-only project monitoring on a thread independent of workers."""

    def __init__(self, root: Path) -> None:
        self._root = root.resolve()
        self._queue: Queue[object] = Queue()
        self._ready: Future[None] = Future()
        self._thread = Thread(
            target=self._run,
            name=f"verdflow-project-query-{uuid4().hex[:8]}",
            daemon=True,
        )

    def start(self, timeout_seconds: float) -> None:
        self._thread.start()
        self._ready.result(timeout=timeout_seconds)

    def call(
        self,
        callback: Callable[[ProjectQueryStore], ResultT],
        *,
        timeout_seconds: float,
    ) -> ResultT:
        future: Future[object] = Future()
        self._queue.put((callback, future))
        return cast(ResultT, future.result(timeout=timeout_seconds))

    def close(self, timeout_seconds: float) -> None:
        if not self._thread.is_alive():
            return
        self._queue.put(_STOP)
        self._thread.join(timeout_seconds)
        if self._thread.is_alive():
            raise TimeoutError("Project query thread did not stop in time")

    def _run(self) -> None:
        try:
            with ProjectQueryStore.open(self._root) as store:
                self._ready.set_result(None)
                while True:
                    item = self._queue.get()
                    if item is _STOP:
                        return
                    callback, future = cast(
                        tuple[
                            Callable[[ProjectQueryStore], object],
                            Future[object],
                        ],
                        item,
                    )
                    if future.set_running_or_notify_cancel():
                        try:
                            future.set_result(callback(store))
                        except BaseException as error:
                            future.set_exception(error)
        except BaseException as error:
            if not self._ready.done():
                self._ready.set_exception(error)


class ProjectRuntime:
    """Own one CoreHost and all of its SQLite access on one thread."""

    def __init__(
        self,
        config: CoreHostConfig,
        *,
        library_path: Path,
        create_name: str | None,
        idle_cycle_seconds: float,
        model_api_provider_registry: ModelApiProviderRegistry | None = None,
        model_api_secret_resolver: SecretResolver | None = None,
        extension_factories: tuple[CoreHostExtensionFactory, ...] = (),
    ) -> None:
        self.session_id = str(uuid4())
        self._config = config
        self._library_path = library_path.resolve()
        self._create_name = create_name
        self._idle_cycle_seconds = idle_cycle_seconds
        self._model_api_provider_registry = model_api_provider_registry
        self._model_api_secret_resolver = model_api_secret_resolver
        self._extension_factories = extension_factories
        self._queue: Queue[object] = Queue()
        self._ready: Future[ProjectSessionInfo] = Future()
        self._state_lock = RLock()
        self._control_lock = RLock()
        self._info: ProjectSessionInfo | None = None
        self._cancel_signal: Callable[[JobInfo], bool] | None = None
        self._query = ProjectQueryRuntime(config.project_path)
        self._thread = Thread(
            target=self._run,
            name=f"verdflow-project-{self.session_id[:8]}",
            daemon=True,
        )

    def start(self, timeout_seconds: float) -> ProjectSessionInfo:
        self._thread.start()
        info = self._ready.result(timeout=timeout_seconds)
        try:
            self._query.start(timeout_seconds)
        except BaseException:
            self.close(timeout_seconds)
            raise
        return info

    def query(
        self,
        callback: Callable[[ProjectQueryStore], ResultT],
        *,
        timeout_seconds: float,
    ) -> ResultT:
        return self._query.call(callback, timeout_seconds=timeout_seconds)

    def call(
        self,
        callback: Callable[[CoreHost], ResultT],
        *,
        timeout_seconds: float,
    ) -> ResultT:
        """Run a short write command on the CoreHost owner thread."""
        future: Future[object] = Future()
        self._queue.put((callback, future))
        try:
            return cast(ResultT, future.result(timeout=timeout_seconds))
        except TimeoutError:
            future.cancel()
            raise

    def submit(
        self,
        callback: Callable[[CoreHost], ResultT],
    ) -> Future[ResultT]:
        """Queue long owner-thread work without blocking a transport callback."""
        future: Future[ResultT] = Future()
        self._queue.put((callback, future))
        return future

    def request_cancel(
        self,
        job_id: str,
        *,
        timeout_seconds: float,
    ) -> tuple[JobInfo, bool]:
        job = self.query(
            lambda store: store.get_job(job_id),
            timeout_seconds=timeout_seconds,
        )
        if job.status in {
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.INTERRUPTED,
            JobStatus.SUCCEEDED,
        }:
            return job, False
        with self._control_lock:
            signal = self._cancel_signal
        if signal is None:
            raise RuntimeError("Project cancellation control is unavailable")
        return job, signal(job)

    def request_plant_run_cancel(
        self,
        execution_id: str,
        *,
        timeout_seconds: float,
    ) -> tuple[PlantRunExecutionInfo, bool, bool]:
        """Signal the current Worker and enqueue owner-thread convergence."""
        execution = self.query(
            lambda store: store.get_plant_run_execution(execution_id),
            timeout_seconds=timeout_seconds,
        )
        if execution.status in {
            PlantRunExecutionStatus.CANCEL_REQUESTED,
            PlantRunExecutionStatus.CANCELLED,
            PlantRunExecutionStatus.FAILED,
            PlantRunExecutionStatus.SUCCEEDED,
        }:
            return execution, False, False

        worker_signal_sent = False
        job_id = _plant_run_stage_job_id(execution)
        if job_id is not None:
            job = self.query(
                lambda store: store.get_job(job_id),
                timeout_seconds=timeout_seconds,
            )
            if job.status not in {
                JobStatus.CANCELLED,
                JobStatus.FAILED,
                JobStatus.INTERRUPTED,
                JobStatus.SUCCEEDED,
            }:
                with self._control_lock:
                    signal = self._cancel_signal
                if signal is None:
                    raise RuntimeError(
                        "Project cancellation control is unavailable"
                    )
                worker_signal_sent = signal(job)

        future: Future[object] = Future()
        self._queue.put(
            (
                lambda host: host.cancel_plant_run_execution(execution_id),
                future,
            )
        )
        return execution, True, worker_signal_sent

    def request_material_flow_cancel(
        self,
        execution_id: str,
        *,
        timeout_seconds: float,
    ) -> tuple[MaterialFlowExecutionInfo, bool, bool]:
        execution = self.query(
            lambda store: store.get_material_flow_execution(execution_id),
            timeout_seconds=timeout_seconds,
        )
        if execution.status in {
            MaterialFlowExecutionStatus.CANCEL_REQUESTED,
            MaterialFlowExecutionStatus.CANCELLED,
            MaterialFlowExecutionStatus.FAILED,
            MaterialFlowExecutionStatus.SUCCEEDED,
        }:
            return execution, False, False

        worker_signal_sent = False
        job_id = _material_flow_stage_job_id(execution)
        if job_id is not None:
            job = self.query(
                lambda store: store.get_job(job_id),
                timeout_seconds=timeout_seconds,
            )
            if job.status not in {
                JobStatus.CANCELLED,
                JobStatus.FAILED,
                JobStatus.INTERRUPTED,
                JobStatus.SUCCEEDED,
            }:
                with self._control_lock:
                    signal = self._cancel_signal
                if signal is None:
                    raise RuntimeError(
                        "Project cancellation control is unavailable"
                    )
                worker_signal_sent = signal(job)

        future: Future[object] = Future()
        self._queue.put(
            (
                lambda host: host.cancel_material_flow_execution(execution_id),
                future,
            )
        )
        return execution, True, worker_signal_sent

    def request_scene_preparation_cancel(
        self,
        execution_id: str,
        *,
        timeout_seconds: float,
    ) -> tuple[ScenePreparationInfo, bool, bool]:
        execution = self.query(
            lambda store: store.get_scene_preparation(execution_id),
            timeout_seconds=timeout_seconds,
        )
        if execution.status in {
            ScenePreparationStatus.CANCEL_REQUESTED,
            ScenePreparationStatus.CANCELLED,
            ScenePreparationStatus.FAILED,
            ScenePreparationStatus.SUCCEEDED,
        }:
            return execution, False, False

        worker_signal_sent = False
        job_id = _scene_preparation_stage_job_id(execution)
        if (
            job_id is None
            and execution.stage is ScenePreparationStage.MATERIAL_FLOW
            and execution.material_flow_execution_id is not None
        ):
            child = self.query(
                lambda store: store.get_material_flow_execution(
                    execution.material_flow_execution_id
                ),
                timeout_seconds=timeout_seconds,
            )
            job_id = _material_flow_stage_job_id(child)
        if job_id is not None:
            job = self.query(
                lambda store: store.get_job(job_id),
                timeout_seconds=timeout_seconds,
            )
            if job.status not in {
                JobStatus.CANCELLED,
                JobStatus.FAILED,
                JobStatus.INTERRUPTED,
                JobStatus.SUCCEEDED,
            }:
                with self._control_lock:
                    signal = self._cancel_signal
                if signal is None:
                    raise RuntimeError(
                        "Project cancellation control is unavailable"
                    )
                worker_signal_sent = signal(job)

        future: Future[object] = Future()
        self._queue.put(
            (
                lambda host: host.cancel_scene_preparation(execution_id),
                future,
            )
        )
        return execution, True, worker_signal_sent

    def snapshot(self) -> ProjectSessionInfo:
        with self._state_lock:
            if self._info is not None:
                return self._info
        return ProjectSessionInfo(
            session_id=self.session_id,
            project_id="",
            name=self._create_name or "",
            project_path=str(self._config.project_path.resolve()),
            state=ProjectSessionState.STARTING,
            opened_at="",
        )

    def close(self, timeout_seconds: float) -> None:
        failure: BaseException | None = None
        if self._thread.is_alive():
            self._set_state(ProjectSessionState.CLOSING)
            self._queue.put(_STOP)
            self._thread.join(timeout_seconds)
            if self._thread.is_alive():
                failure = TimeoutError(
                    f"Project owner thread did not stop in time: {self.session_id}"
                )
        try:
            self._query.close(timeout_seconds)
        except BaseException as error:
            if failure is None:
                failure = error
        if failure is not None:
            raise failure

    def _run(self) -> None:
        library: LibraryStore | None = None
        host: CoreHost | None = None
        try:
            library = LibraryStore.open(self._library_path)
            if self._create_name is None:
                host = CoreHost.open(
                    self._config,
                    strategy_library=library,
                    model_api_provider_registry=(
                        self._model_api_provider_registry
                    ),
                    model_api_secret_resolver=self._model_api_secret_resolver,
                    extension_factories=self._extension_factories,
                )
            else:
                host = CoreHost.create(
                    self._config,
                    project_name=self._create_name,
                    strategy_library=library,
                    model_api_provider_registry=(
                        self._model_api_provider_registry
                    ),
                    model_api_secret_resolver=self._model_api_secret_resolver,
                    extension_factories=self._extension_factories,
                )
            host.start()
            with self._control_lock:
                self._cancel_signal = host.service_loop.registry.request_cancel
            project = host.application.project_info()
            info = ProjectSessionInfo(
                session_id=self.session_id,
                project_id=project.project_id,
                name=project.name,
                project_path=str(self._config.project_path.resolve()),
                state=ProjectSessionState.ACTIVE,
                opened_at=datetime.now(UTC).isoformat(),
            )
            with self._state_lock:
                self._info = info
            self._ready.set_result(info)

            while True:
                try:
                    item = self._queue.get(timeout=self._idle_cycle_seconds)
                except Empty:
                    host.service_loop.run_once()
                    continue
                if item is _STOP:
                    break
                callback, future = cast(
                    tuple[Callable[[CoreHost], object], Future[object]], item
                )
                if future.set_running_or_notify_cancel():
                    try:
                        future.set_result(callback(host))
                    except BaseException as error:
                        future.set_exception(error)
        except BaseException as error:
            self._set_state(
                ProjectSessionState.FAILED,
                error=_exception_chain_message(error),
            )
            if not self._ready.done():
                self._ready.set_exception(error)
        finally:
            with self._control_lock:
                self._cancel_signal = None
            try:
                if host is not None:
                    host.close()
            finally:
                if library is not None:
                    library.close()
            if self.snapshot().state is not ProjectSessionState.FAILED:
                self._set_state(ProjectSessionState.CLOSED)

    def _set_state(
        self,
        state: ProjectSessionState,
        *,
        error: str | None = None,
    ) -> None:
        with self._state_lock:
            current = self._info
            self._info = ProjectSessionInfo(
                session_id=self.session_id,
                project_id=current.project_id if current else "",
                name=current.name if current else (self._create_name or ""),
                project_path=str(self._config.project_path.resolve()),
                state=state,
                opened_at=current.opened_at if current else "",
                error=error,
            )


def _exception_chain_message(error: BaseException) -> str:
    messages: list[str] = []
    current: BaseException | None = error
    while current is not None and len(messages) < 8:
        message = str(current).strip() or type(current).__name__
        if message not in messages:
            messages.append(message)
        current = current.__cause__ or current.__context__
    return ": ".join(messages)


def _plant_run_stage_job_id(
    execution: PlantRunExecutionInfo,
) -> str | None:
    return {
        PlantRunExecutionStage.GENERATION: execution.generation_job_id,
        PlantRunExecutionStage.ASSIGNMENT: execution.assignment_job_id,
        PlantRunExecutionStage.REVIEW: None,
        PlantRunExecutionStage.ELEVATION: execution.elevation_job_id,
        PlantRunExecutionStage.EXPORT: execution.export_job_id,
        PlantRunExecutionStage.COMPLETED: None,
    }[execution.stage]


def _material_flow_stage_job_id(
    execution: MaterialFlowExecutionInfo,
) -> str | None:
    return {
        MaterialFlowExecutionStage.ANALYSIS: execution.analysis_job_id,
        MaterialFlowExecutionStage.CONFIRMATION: None,
        MaterialFlowExecutionStage.EXTRACTION: execution.extraction_job_id,
        MaterialFlowExecutionStage.COMPLETED: None,
    }[execution.stage]


def _scene_preparation_stage_job_id(
    execution: ScenePreparationInfo,
) -> str | None:
    return {
        ScenePreparationStage.INSPECTION: execution.inspection_job_id,
        ScenePreparationStage.MATERIAL_FLOW: None,
        ScenePreparationStage.COMPLETED: None,
    }[execution.stage]
