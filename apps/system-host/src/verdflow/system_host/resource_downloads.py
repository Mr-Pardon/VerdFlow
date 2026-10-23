"""Bounded acquisition from the reviewed resource center; never executes packages."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from threading import Event
from time import monotonic
from typing import Callable
from urllib.parse import unquote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MANIFEST_URL = "https://mr-pardon.github.io/verdflow-resources/starter-resources.json"
PACKAGE_PREFIX = "/Mr-Pardon/verdflow-resources/releases/download/"
MAX_PACKAGE_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class StarterResource:
    id: str
    name: str
    kind: str
    version: str
    identity: str
    filename: str
    bytes: int
    sha256: str
    url: str
    page_url: str


@dataclass(frozen=True)
class StarterManifest:
    revision: str
    resources: tuple[StarterResource, ...]


class DownloadCancelled(Exception):
    pass


def require_url(url: str, *, manifest: bool = False, redirect: bool = False) -> None:
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443) or parsed.fragment:
        raise ValueError("Resource URL must be an approved HTTPS address")
    if manifest:
        if url != MANIFEST_URL:
            raise ValueError("Unexpected resource manifest address")
    elif parsed.hostname == "github.com":
        parts = parsed.path.removeprefix(PACKAGE_PREFIX).split("/")
        if not parsed.path.startswith(PACKAGE_PREFIX) or len(parts) != 2 or not all(parts) or parsed.query:
            raise ValueError("Resource must use this repository's fixed Release attachment")
    elif not redirect or parsed.hostname not in {"release-assets.githubusercontent.com", "objects.githubusercontent.com"}:
        raise ValueError("Unexpected resource download host")


class _Redirects(HTTPRedirectHandler):
    def __init__(self, manifest: bool) -> None:
        self.manifest = manifest

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        require_url(newurl, manifest=self.manifest, redirect=True)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def parse_manifest(payload: bytes) -> StarterManifest:
    value = json.loads(payload)
    if not isinstance(value, dict) or value.get("schemaVersion") != 1:
        raise ValueError("Unsupported starter manifest")
    revision = value.get("revision")
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{64}", revision):
        raise ValueError("Invalid starter revision")
    records = value.get("resources")
    if not isinstance(records, list) or len(records) > 32:
        raise ValueError("Invalid starter resource list")
    resources = []
    seen = set()
    for item in records:
        if not isinstance(item, dict):
            raise ValueError("Invalid starter resource")
        texts = ("id", "name", "kind", "version", "identity", "filename", "sha256", "url", "page_url")
        if any(not isinstance(item.get(key), str) or not 0 < len(item[key]) <= 1024 for key in texts):
            raise ValueError("Invalid starter resource fields")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,99}", item["id"]) or item["id"] in seen:
            raise ValueError("Invalid or duplicate resource id")
        seen.add(item["id"])
        suffix = {"assets": ".vfa", "strategies": ".vfs"}.get(item["kind"])
        filename = item["filename"]
        if not suffix or not filename.endswith(suffix) or re.search(r'[\\/:\x00-\x1f]', filename) or filename in {".", ".."}:
            raise ValueError("Only VFA and VFS packages can be imported")
        if type(item.get("bytes")) is not int or not 0 < item["bytes"] <= MAX_PACKAGE_BYTES:
            raise ValueError("Resource package size exceeds the limit")
        if not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
            raise ValueError("Missing package SHA-256")
        require_url(item["url"])
        if unquote(urlsplit(item["url"]).path.rsplit("/", 1)[-1]) != filename:
            raise ValueError("Resource filename does not match its URL")
        page = urlsplit(item["page_url"])
        if page.scheme != "https" or page.netloc != "mr-pardon.github.io" or page.path != "/verdflow-resources/":
            raise ValueError("Unexpected resource detail page")
        resources.append(StarterResource(**{key: item[key] for key in (*texts, "bytes")}))
    return StarterManifest(revision, tuple(resources))


def load_manifest() -> StarterManifest:
    try:
        request = Request(MANIFEST_URL, headers={"User-Agent": "VerdFlow", "Cache-Control": "no-cache"})
        with build_opener(_Redirects(True)).open(request, timeout=10) as response:
            payload = response.read(512 * 1024 + 1)
        if len(payload) > 512 * 1024:
            raise ValueError("Starter manifest is too large")
        return parse_manifest(payload)
    except Exception as error:
        raise ValueError("暂时无法读取推荐清单，请稍后重试，或从资源中心下载后手动导入。") from error


def download_package(item: StarterResource, path: Path, cancel: Event, progress: Callable[[int], None]) -> None:
    require_url(item.url)
    digest = hashlib.sha256()
    size = 0
    deadline = monotonic() + 300
    try:
        request = Request(item.url, headers={"User-Agent": "VerdFlow", "Accept-Encoding": "identity"})
        with build_opener(_Redirects(False)).open(request, timeout=10) as response, path.open("xb") as output:
            while True:
                if cancel.is_set():
                    raise DownloadCancelled()
                if monotonic() > deadline:
                    raise ValueError("下载耗时过长，请检查网络后重试。")
                chunk = response.read1(128 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > item.bytes:
                    raise ValueError("资源大小与清单不符，已停止导入。")
                digest.update(chunk)
                output.write(chunk)
                progress(size)
        if size != item.bytes or digest.hexdigest() != item.sha256:
            raise ValueError("资源完整性校验未通过，请刷新清单后重试。")
    except (ValueError, DownloadCancelled):
        raise
    except Exception as error:
        raise ValueError("资源下载失败，请检查网络后重试。") from error
