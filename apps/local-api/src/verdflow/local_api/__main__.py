"""Run the standalone VerdFlow loopback service."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from threading import Event, Thread
import time
from typing import BinaryIO
from urllib.request import urlopen

from verdflow.system_host import (
    SYSTEM_API_PROTOCOL_VERSION,
    SystemEndpointInfo,
    SystemHost,
    SystemHostAlreadyRunningError,
    SystemHostConfig,
    discover_system_endpoint,
)

from .server import LocalApiConfig, LocalApiServer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the VerdFlow local API")
    parser.add_argument("--library-path", type=Path, required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8768)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    management_token = os.environ.get(
        "VERDFLOW_LOCAL_API_MANAGEMENT_TOKEN"
    )
    shutdown_requested = _portable_shutdown_event()
    system_config = SystemHostConfig(library_path=arguments.library_path)
    system = SystemHost(system_config)
    try:
        system.start()
    except SystemHostAlreadyRunningError as error:
        endpoint = _wait_for_reusable_endpoint(
            system_config,
            initial=error.endpoint,
        )
        if endpoint is None:
            print(
                json.dumps(
                    {
                        "status": "error",
                        "error": "system_host_running_without_healthy_endpoint",
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            return 2
        print(
            json.dumps(
                {
                    "status": "ready",
                    "base_url": endpoint.base_url,
                    "instance_id": endpoint.instance_id,
                    "reused": True,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return 0
    try:
        with LocalApiServer(
            system,
            LocalApiConfig(
                host=arguments.host,
                port=arguments.port,
                management_token=management_token,
            ),
        ) as server:
            print(
                json.dumps(
                    {
                        "status": "ready",
                        "base_url": server.base_url,
                        "instance_id": system.status().instance_id,
                        "reused": False,
                    },
                    ensure_ascii=False,
                ),
                flush=True,
            )
            try:
                while not shutdown_requested.wait(1.0):
                    pass
                return 0
            except KeyboardInterrupt:
                return 0
    finally:
        system.close()


def _wait_for_reusable_endpoint(
    config: SystemHostConfig,
    *,
    initial: SystemEndpointInfo | None,
    timeout_seconds: float = 5.0,
) -> SystemEndpointInfo | None:
    deadline = time.monotonic() + timeout_seconds
    endpoint = initial
    while True:
        if endpoint is not None and _endpoint_is_healthy(endpoint):
            return endpoint
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)
        try:
            endpoint = discover_system_endpoint(
                config.resolved_system_path()
            )
        except ValueError:
            endpoint = None


def _endpoint_is_healthy(endpoint: SystemEndpointInfo) -> bool:
    if endpoint.protocol_version != SYSTEM_API_PROTOCOL_VERSION:
        return False
    try:
        with urlopen(
            endpoint.base_url + "/api/v1/health",
            timeout=1.0,
        ) as response:
            if response.status != 200:
                return False
            document = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return (
        isinstance(document, dict)
        and document.get("instance_id") == endpoint.instance_id
        and document.get("system_root_id") == endpoint.system_root_id
    )


def _portable_shutdown_event() -> Event:
    event = Event()
    if os.environ.get("VERDFLOW_SHUTDOWN_ON_STDIN_CLOSE") != "1":
        return event
    stream = getattr(sys.stdin, "buffer", None)
    if stream is None:
        raise RuntimeError("Portable shutdown requires a binary stdin stream")
    Thread(
        target=(
            _wait_for_windows_shutdown_pipe
            if sys.platform == "win32"
            else _wait_for_shutdown_stream
        ),
        args=(stream, event),
        name="verdflow-portable-shutdown",
        daemon=True,
    ).start()
    return event


def _wait_for_shutdown_stream(stream: BinaryIO, event: Event) -> None:
    try:
        stream.read()
    finally:
        event.set()


def _wait_for_windows_shutdown_pipe(stream: BinaryIO, event: Event) -> None:
    import ctypes
    from ctypes import wintypes
    import msvcrt

    try:
        # An idle blocking stdin read can deadlock a later OpenBLAS DLL load.
        # Poll the inherited pipe and only drain bytes already available.
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        peek_pipe = kernel.PeekNamedPipe
        peek_pipe.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD),
            ctypes.POINTER(wintypes.DWORD),
        ]
        peek_pipe.restype = wintypes.BOOL
        read_file = kernel.ReadFile
        read_file.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        read_file.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(stream.fileno())
        buffer = ctypes.create_string_buffer(4096)
        count = wintypes.DWORD()
        available = wintypes.DWORD()
        while not event.is_set():
            if not peek_pipe(handle, None, 0, None, ctypes.byref(available), None):
                error = ctypes.get_last_error()
                if error in {109, 232}:  # Broken or closing pipe means parent EOF.
                    return
                raise ctypes.WinError(error)
            if available.value == 0:
                event.wait(0.1)
                continue
            if not read_file(
                handle, buffer, min(available.value, len(buffer)),
                ctypes.byref(count), None,
            ):
                error = ctypes.get_last_error()
                if error in {109, 232}:
                    return
                raise ctypes.WinError(error)
            if count.value == 0:
                return
    finally:
        event.set()


if __name__ == "__main__":
    raise SystemExit(main())
