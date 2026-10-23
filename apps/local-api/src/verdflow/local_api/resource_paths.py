"""Stateless Local API resource paths; no Host or HTTP lifecycle."""

from __future__ import annotations
from urllib.parse import unquote


def project_resource(
    path: str,
) -> tuple[str, str, str, str | None] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) not in {6, 7} or parts[:3] != [
        "api",
        "v1",
        "projects",
    ]:
        return None
    if parts[4] not in {
        "jobs",
        "artifacts",
        "exports",
        "material-flows",
        "plant-exports",
        "plant-previews",
        "planting-composition-exports",
        "xml-outputs",
        "plant-runs",
        "scene-preparations",
    }:
        return None
    return (
        parts[3],
        parts[4],
        parts[5],
        parts[6] if len(parts) == 7 else None,
    )


def project_named_collection(
    path: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4]
        in {
            "tasks",
            "summary",
            "planting-composition",
            "xml-outputs",
        }
    ):
        return parts[3], parts[4]
    return None


def project_viewer_resource(
    path: str,
) -> tuple[str, str, str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 7
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] in {"region-sets", "point-sets"}
    ):
        return parts[3], parts[4], parts[5], parts[6]
    return None


def strategy_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 4
        and parts[:3] == ["api", "v1", "strategies"]
    ):
        return parts[3]
    return None


def strategy_preview_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if len(parts) == 4 and parts[:3] == ["api", "v1", "strategy-previews"]:
        return parts[3]
    return None


def strategy_action_resource(
    path: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "strategies"]
        and parts[4] in {"trust", "revoke"}
    ):
        return parts[3], parts[4]
    return None


def strategy_package_export_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "strategies"]
        and parts[4] == "export-package"
    ):
        return parts[3]
    return None


def authoring_kit_export_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "authoring-kits"]
        and parts[4] == "export"
    ):
        return parts[3]
    return None


def provider_action_resource(
    path: str,
    action: str,
) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "model-providers"]
        and parts[4] == action
    ):
        return parts[3]
    return None


def catalog_asset_resource(
    path: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 6
        and parts[:3] == ["api", "v1", "asset-catalogs"]
        and parts[5] == "assets"
    ):
        return parts[3], parts[4]
    return None


def catalog_package_export_resource(
    path: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 6
        and parts[:3] == ["api", "v1", "asset-catalogs"]
        and parts[5] == "export-package"
    ):
        return parts[3], parts[4]
    return None


def catalog_resource(
    path: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "asset-catalogs"]
    ):
        return parts[3], parts[4]
    return None


def library_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 4
        and parts[:3] == ["api", "v1", "library-resources"]
    ):
        return parts[3]
    return None


def profile_resource(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 4
        and parts[:3]
        == ["api", "v1", "role-binding-profiles"]
    ):
        return parts[3]
    return None


def profile_action_resource(
    path: str,
    action: str,
) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3]
        == ["api", "v1", "role-binding-profiles"]
        and parts[4] == action
    ):
        return parts[3]
    return None


def profile_package_export_resource(
    path: str,
) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3]
        == ["api", "v1", "role-binding-profiles"]
        and parts[4] == "export-package"
    ):
        return parts[3]
    return None


def plant_run_collection(path: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == "plant-runs"
    ):
        return parts[3]
    return None


def project_collection(path: str, kind: str) -> str | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 5
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == kind
    ):
        return parts[3]
    return None


def project_action_resource(
    path: str,
    kind: str,
    action: str,
) -> tuple[str, str] | None:
    parts = [unquote(part) for part in path.split("/") if part]
    if (
        len(parts) == 7
        and parts[:3] == ["api", "v1", "projects"]
        and parts[4] == kind
        and parts[6] == action
    ):
        return parts[3], parts[5]
    return None
