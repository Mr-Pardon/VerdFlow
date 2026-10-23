"""Build the Windows x64 VerdFlow Studio portable distribution."""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
from urllib.request import urlopen
import zipfile

try:
    from tools.portable_licenses import stage_licenses, verify_licenses
except ModuleNotFoundError:
    from portable_licenses import stage_licenses, verify_licenses


ROOT = Path(__file__).resolve().parents[1]
RELEASE_ROOT = (ROOT / "dist" / "portable").resolve()
CACHE_ROOT = (ROOT / ".cache" / "portable").resolve()
PYTHON_VERSION = "3.13.3"
PYTHON_TAG = "313"
PYTHON_ARCHIVE = f"python-{PYTHON_VERSION}-embed-amd64.zip"
PYTHON_URL = (
    f"https://www.python.org/ftp/python/{PYTHON_VERSION}/{PYTHON_ARCHIVE}"
)
PYTHON_SHA256 = "59ff76e16e6597de47474fb22be69e7191a89116910d728ab735079b078e52db"
PYYAML_VERSION = "6.0.2"
REQUIREMENTS_PATH = ROOT / "tools" / "portable-requirements.txt"


def _default_update_repository() -> str | None:
    source = json.loads((ROOT / "apps/studio-electron-probe/updates.json").read_text(encoding="utf-8"))
    if source.get("schema") != "verdflow.update-source" or source.get("version") != "1":
        raise ValueError("Invalid desktop update-source configuration")
    return source.get("repository")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build the VerdFlow Studio portable Windows package"
    )
    update_source = parser.add_mutually_exclusive_group()
    update_source.add_argument(
        "--update-repository", default=_default_update_repository(),
        help="override the desktop's configured public GitHub owner/repository",
    )
    update_source.add_argument("--no-updates", dest="update_repository", action="store_const", const=None,
                              help="build without online application updates")
    parser.add_argument(
        "--smoke-test",
        action="store_true",
        help="launch the packaged app once before creating the ZIP",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    repository = arguments.update_repository
    if repository is not None and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repository):
        raise ValueError("Update repository must be owner/repository")
    _require_windows_x64()
    version = _workspace_version()
    bundle_name = f"VerdFlow-Studio-v{version}-win-x64"
    bundle_root = (RELEASE_ROOT / bundle_name).resolve()
    archive_path = (RELEASE_ROOT / f"{bundle_name}.zip").resolve()
    _require_child(bundle_root, RELEASE_ROOT)
    _require_child(archive_path, RELEASE_ROOT)

    _run(["corepack", "pnpm", "build:dts-live-webui"])
    _run(["corepack", "pnpm", "build"])

    RELEASE_ROOT.mkdir(parents=True, exist_ok=True)
    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    if bundle_root.exists():
        shutil.rmtree(bundle_root)
    archive_path.unlink(missing_ok=True)

    _stage_electron(bundle_root, version, repository)
    _stage_python(bundle_root)
    _write_release_files(bundle_root, version)
    _verify_embedded_python(bundle_root)
    _verify_release_layout(bundle_root)

    if arguments.smoke_test:
        _smoke_test(bundle_root)

    _write_update_manifest(bundle_root, version)
    _write_zip(bundle_root, archive_path)
    _write_update_descriptor(archive_path, version)
    print(
        json.dumps(
            {
                "status": "ready",
                "bundle": str(bundle_root),
                "archive": str(archive_path),
                "archive_sha256": _sha256(archive_path),
                "archive_bytes": archive_path.stat().st_size,
            },
            ensure_ascii=False,
        )
    )
    return 0


def _require_windows_x64() -> None:
    if sys.platform != "win32" or platform.machine().lower() not in {
        "amd64",
        "x86_64",
    }:
        raise RuntimeError("The portable builder supports Windows x64 only")


def _workspace_version() -> str:
    document = json.loads((ROOT / "package.json").read_text(encoding="utf-8"))
    version = document.get("version")
    if not isinstance(version, str) or not version.strip():
        raise RuntimeError("Workspace package.json has no version")
    return version.strip()


def _stage_electron(bundle_root: Path, version: str, repository: str | None = None) -> None:
    electron_dist = (
        ROOT
        / "apps"
        / "studio-electron-probe"
        / "node_modules"
        / "electron"
        / "dist"
    ).resolve()
    renderer_dist = (ROOT / "apps" / "studio" / "dist").resolve()
    if not (electron_dist / "electron.exe").is_file():
        raise RuntimeError("Electron runtime is missing; run pnpm install first")
    if not (renderer_dist / "index.html").is_file():
        raise RuntimeError("Studio production build is missing")

    shutil.copytree(electron_dist, bundle_root)
    electron_executable = bundle_root / "electron.exe"
    electron_executable.replace(bundle_root / "VerdFlow Studio.exe")
    branding = ROOT / "assets" / "branding" / "generated" / "brand"
    shutil.copytree(branding, bundle_root / "resources" / "branding")
    _run([
        "node", str(ROOT / "tools" / "windows_branding.mjs"),
        str(bundle_root / "VerdFlow Studio.exe"), str(branding / "app.ico"), version,
    ])
    default_app = bundle_root / "resources" / "default_app.asar"
    default_app.unlink(missing_ok=True)

    app_root = bundle_root / "resources" / "app"
    app_root.mkdir(parents=True)
    (app_root / "updates.json").write_text(json.dumps({
        "schema": "verdflow.update-source", "version": "1", "repository": repository,
    }, indent=2) + "\n", encoding="utf-8")
    shutil.copytree(
        ROOT / "apps" / "studio-electron-probe" / "src",
        app_root / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    (app_root / "package.json").write_text(
        json.dumps(
            {
                "name": "verdflow-studio-portable",
                "version": version,
                "private": True,
                "type": "module",
                "main": "src/main.mjs",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    shutil.copytree(renderer_dist, bundle_root / "resources" / "studio")


def _stage_python(bundle_root: Path) -> None:
    archive = CACHE_ROOT / PYTHON_ARCHIVE
    _download_verified(PYTHON_URL, archive, PYTHON_SHA256)
    wheels = _download_runtime_wheels()
    runtime = bundle_root / "resources" / "python"
    runtime.mkdir(parents=True)
    with zipfile.ZipFile(archive) as source:
        source.extractall(runtime)
    site_packages = runtime / "Lib" / "site-packages"
    site_packages.mkdir(parents=True)
    for wheel in wheels:
        with zipfile.ZipFile(wheel) as source:
            source.extractall(site_packages)
    _merge_verdflow_sources(site_packages / "verdflow")

    pth_files = tuple(runtime.glob("python*._pth"))
    if len(pth_files) != 1:
        raise RuntimeError("Embedded Python _pth file was not found")
    pth_files[0].write_text(
        f"python{PYTHON_TAG}.zip\n.\nLib\\site-packages\nimport site\n",
        encoding="ascii",
    )


def _download_runtime_wheels() -> tuple[Path, ...]:
    wheel_root = (CACHE_ROOT / "wheels").resolve()
    _require_child(wheel_root, CACHE_ROOT)
    if wheel_root.exists():
        shutil.rmtree(wheel_root)
    wheel_root.mkdir(parents=True)
    _run(
        [
            sys.executable,
            "-m",
            "pip",
            "download",
            "--require-hashes",
            "--no-deps",
            "--only-binary=:all:",
            "--platform",
            "win_amd64",
            "--python-version",
            "3.13",
            "--implementation",
            "cp",
            "--abi",
            "cp313",
            "--requirement",
            str(REQUIREMENTS_PATH),
            "--dest",
            str(wheel_root),
        ]
    )
    wheels = tuple(sorted(wheel_root.glob("*.whl"), key=lambda item: item.name.lower()))
    expected_hashes = {
        line.split("sha256:", 1)[1].strip()
        for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines()
        if "--hash=sha256:" in line
    }
    actual_hashes = {_sha256(wheel) for wheel in wheels}
    if len(wheels) != len(expected_hashes) or actual_hashes != expected_hashes:
        raise RuntimeError("Portable runtime wheels do not match the locked requirements")
    return wheels


def _merge_verdflow_sources(destination: Path) -> None:
    destination.mkdir(parents=True)
    source_roots: list[Path] = []
    for group in ("apps", "adapters", "packages"):
        source_roots.extend(
            sorted((ROOT / group).glob("*/src/verdflow"), key=lambda item: item.as_posix())
        )
    if not source_roots:
        raise RuntimeError("No VerdFlow Python source roots were found")
    for source_root in source_roots:
        for source in sorted(source_root.rglob("*"), key=lambda item: item.as_posix()):
            if not source.is_file() or "__pycache__" in source.parts:
                continue
            relative = source.relative_to(source_root)
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists() and target.read_bytes() != source.read_bytes():
                raise RuntimeError(f"Conflicting VerdFlow package file: {relative}")
            shutil.copy2(source, target)


def _write_release_files(bundle_root: Path, version: str) -> None:
    source_manifest = ROOT / "source-manifest.json"
    if source_manifest.is_file():
        source_identity = {"source_manifest_sha256": _sha256(source_manifest)}
    else:
        source_identity = {
            "commit": _git_output(["rev-parse", "HEAD"]),
            "working_tree_dirty": bool(_git_output(["status", "--porcelain"])),
        }
    stage_licenses(ROOT, bundle_root, python_version=PYTHON_VERSION)
    (bundle_root / "README.txt").write_text(
        f"VerdFlow Studio v{version} Windows 免安装版\n"
        "\n"
        "1. 请先完整解压，再运行 VerdFlow Studio.exe。\n"
        "2. 项目索引、运行数据和日志统一写入 %LOCALAPPDATA%\\VerdFlow。\n"
        "3. 更新程序包不需要复制或迁移用户数据。\n"
        "4. 请勿在 ZIP 内直接运行。\n"
        "5. Blender 与 DTS Explorer 仍需在 Studio 设置中配置本机安装路径。\n"
        "6. 模型 API Key 由 Windows 凭据管理器保存，不写入普通数据文件。\n"
        "7. 本包未签名，Windows 可能显示未知发布者提醒。\n"
        "8. 使用手册：https://mr-pardon.github.io/verdflow-site/\n"
        "9. 作者与许可见 AUTHORS.md、LICENSE、NOTICE；运行库声明见 third-party/README.md。\n",
        encoding="utf-8-sig",
    )
    (bundle_root / "release.json").write_text(
        json.dumps(
            {
                "schema": "verdflow.portable-release",
                "version": "1",
                "product_version": version,
                **source_identity,
                "built_at": datetime.now(UTC).isoformat(),
                "platform": "windows-x64",
                "python_version": PYTHON_VERSION,
                "pyyaml_version": PYYAML_VERSION,
                "python_requirements_sha256": _sha256(REQUIREMENTS_PATH),
            },
            ensure_ascii=False,
            sort_keys=True,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
def _verify_embedded_python(bundle_root: Path) -> None:
    python = bundle_root / "resources" / "python" / "python.exe"
    # Match Electron's piped stdin lifecycle; a plain import misses Windows
    # native-library deadlocks after the parent-disconnect watcher starts.
    with subprocess.Popen(
        [
            str(python),
            "-c",
            (
                "from verdflow.local_api.__main__ import _portable_shutdown_event; "
                "stopped = _portable_shutdown_event(); "
                "import time; time.sleep(0.1); "
                "import yaml; import jsonschema; import verdflow.local_api; "
                "import verdflow.system_host; import verdflow.strategy_worker; "
                "import verdflow.model_api.worker_entry; "
                "import verdflow.blender_worker; import numpy; import rasterio; "
                "import shapefile; import shapely; import verdflow.geospatial_worker; "
                "stopped.set(); print('portable-python-ready', flush=True)"
            ),
        ],
        cwd=bundle_root,
        env={
            **os.environ,
            "VERDFLOW_SHUTDOWN_ON_STDIN_CLOSE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        },
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ) as process:
        try:
            # Keep stdin open until imports finish instead of communicate()'s
            # early EOF, which would hide the startup/import regression.
            process.wait(timeout=30)
        except subprocess.TimeoutExpired as error:
            process.kill()
            process.communicate(timeout=5)
            raise RuntimeError("Portable native imports stalled with the parent pipe open") from error
        stdout, stderr = process.communicate(timeout=5)
        if process.returncode != 0:
            raise RuntimeError(
                "Portable Python verification failed: "
                + stderr.decode("utf-8", errors="replace")
            )
        if b"portable-python-ready" not in stdout:
            raise RuntimeError("Portable Python verification did not finish")


def _verify_release_layout(bundle_root: Path) -> None:
    if (bundle_root / "UserData").exists():
        raise RuntimeError("Portable bundle must not contain a root UserData directory")
    if not (bundle_root / "resources" / "branding" / "app.ico").is_file():
        raise RuntimeError("Portable bundle is missing its application icon")
    verify_licenses(bundle_root)


def _smoke_test(bundle_root: Path) -> None:
    executable = bundle_root / "VerdFlow Studio.exe"
    smoke_data = (CACHE_ROOT / "smoke-user-data").resolve()
    _require_child(smoke_data, CACHE_ROOT)
    if smoke_data.exists():
        shutil.rmtree(smoke_data)
    environment = dict(os.environ)
    environment["VERDFLOW_PORTABLE_SMOKE_TEST"] = "1"
    environment["VERDFLOW_DESKTOP_DIAGNOSTICS"] = "1"
    environment["VERDFLOW_DATA_ROOT"] = str(smoke_data)
    try:
        subprocess.run(
            [str(executable)],
            cwd=bundle_root,
            env=environment,
            check=True,
            timeout=60,
        )
    finally:
        if smoke_data.exists():
            shutil.rmtree(smoke_data)


def _write_zip(bundle_root: Path, destination: Path) -> None:
    with zipfile.ZipFile(
        destination,
        mode="x",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=6,
        allowZip64=True,
    ) as archive:
        for source in sorted(bundle_root.rglob("*"), key=lambda item: item.as_posix()):
            if source.is_file():
                archive.write(source, source.relative_to(bundle_root.parent).as_posix())


def _write_update_manifest(bundle_root: Path, version: str) -> None:
    files = []
    for source in sorted(bundle_root.rglob("*")):
        if source.is_symlink():
            raise ValueError("Linked files are not allowed in a portable update")
        if source.is_file() and source != bundle_root / "update-manifest.json":
            files.append({"path": source.relative_to(bundle_root).as_posix(),
                          "bytes": source.stat().st_size, "sha256": _sha256(source)})
    (bundle_root / "update-manifest.json").write_text(json.dumps({
        "schema": "verdflow.portable-files", "version": "1",
        "product_version": version, "platform": "windows-x64", "files": files,
    }, indent=2) + "\n", encoding="utf-8")


def _write_update_descriptor(archive: Path, version: str) -> None:
    digest = _sha256(archive)
    (archive.parent / "VerdFlow-update-win-x64.json").write_text(json.dumps({
        "schema": "verdflow.portable-update", "version": "1",
        "product_version": version, "platform": "windows-x64",
        "archive_name": archive.name, "archive_bytes": archive.stat().st_size,
        "archive_sha256": digest,
    }, indent=2) + "\n", encoding="utf-8")
    archive.with_suffix(".zip.sha256").write_text(f"{digest}  {archive.name}\n", encoding="ascii")


def _download_verified(url: str, destination: Path, expected_hash: str) -> None:
    if destination.is_file() and _sha256(destination) == expected_hash:
        return
    destination.unlink(missing_ok=True)
    temporary = destination.with_suffix(destination.suffix + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with urlopen(url, timeout=60) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output)
        if _sha256(temporary) != expected_hash:
            raise RuntimeError(f"Downloaded file hash mismatch: {destination.name}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)


def _run(command: list[str], *, cwd: Path = ROOT) -> None:
    executable = shutil.which(command[0]) or command[0]
    subprocess.run([executable, *command[1:]], cwd=cwd, check=True)


def _git_output(arguments: list[str]) -> str:
    git = shutil.which("git") or "git"
    return subprocess.run(
        [git, *arguments],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_child(path: Path, parent: Path) -> None:
    if path == parent or not path.is_relative_to(parent):
        raise RuntimeError(f"Release path escapes its controlled root: {path}")


if __name__ == "__main__":
    raise SystemExit(main())
