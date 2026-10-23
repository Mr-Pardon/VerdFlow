from __future__ import annotations

import argparse
import json
from pathlib import Path, PurePosixPath
import zipfile

try:
    from strategy_versions import require_strategy_version
except ImportError:
    from verdflow.contracts import require_strategy_version


MANIFEST_NAME = "strategy-package.json"
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_PACKAGE_BYTES = 64 * 1024 * 1024
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
MANIFEST_KEYS = {
    "schema",
    "version",
    "package_id",
    "package_version",
    "runtime",
    "api_version",
    "strategy_file",
    "entry",
    "files",
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build a VerdFlow strategy source directory as a .vfs package."
    )
    parser.add_argument("source", type=Path, help="Strategy source directory")
    parser.add_argument("destination", type=Path, help="Output .vfs path")
    parser.add_argument("--previous", type=Path, help="Previous strategy source directory for compatibility checks")
    args = parser.parse_args()
    build(args.source, args.destination, previous=args.previous)


def build(source: Path, destination: Path, *, previous: Path | None = None) -> None:
    root = source.resolve(strict=True)
    if not root.is_dir():
        raise ValueError(f"Strategy source is not a directory: {root}")
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file() or manifest_path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError(f"Missing or oversized {MANIFEST_NAME}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict) or set(manifest) != MANIFEST_KEYS:
        raise ValueError("Strategy manifest fields are invalid")
    require_strategy_version(manifest.get("package_version"))
    if previous is not None:
        from check_compatibility import check_update
        check_update(previous, root)
    if (
        manifest.get("schema") != "verdflow.strategy-package"
        or manifest.get("version") != "1"
        or manifest.get("runtime") != "python"
        or manifest.get("api_version") != "1"
    ):
        raise ValueError("Strategy manifest contract is unsupported")
    raw_files = manifest.get("files")
    if not isinstance(raw_files, list) or not all(
        isinstance(item, str) for item in raw_files
    ):
        raise ValueError("Strategy manifest files must be an array of paths")
    listed = [_relative_path(item) for item in raw_files]
    if listed != sorted(set(listed)):
        raise ValueError("Strategy manifest files must be unique and sorted")
    actual = sorted(
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file()
        and path.relative_to(root).as_posix() != MANIFEST_NAME
        and "__pycache__" not in path.parts
        and path.suffix != ".pyc"
    )
    if actual != listed:
        raise ValueError(f"Strategy file closure differs from manifest: {actual}")

    total_bytes = manifest_path.stat().st_size
    for relative in listed:
        path = root / Path(relative)
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            raise ValueError(f"Invalid strategy file: {relative}")
        total_bytes += path.stat().st_size
    if total_bytes > MAX_PACKAGE_BYTES + MAX_MANIFEST_BYTES:
        raise ValueError("Strategy package exceeds the size limit")

    destination_path = destination.resolve()
    destination_path.parent.mkdir(parents=True, exist_ok=True)
    if destination_path.exists():
        raise ValueError(f"Destination already exists: {destination_path}")
    with zipfile.ZipFile(destination_path, mode="x", compression=zipfile.ZIP_STORED) as archive:
        for relative in [MANIFEST_NAME, *listed]:
            info = zipfile.ZipInfo(relative, date_time=ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, (root / Path(relative)).read_bytes())
    print(destination_path)


def _relative_path(value: str) -> str:
    if not value or "\\" in value or ":" in value:
        raise ValueError(f"Invalid strategy path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Invalid strategy path: {value!r}")
    return path.as_posix()


if __name__ == "__main__":
    main()
