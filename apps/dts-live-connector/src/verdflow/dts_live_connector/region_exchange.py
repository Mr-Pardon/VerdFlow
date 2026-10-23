"""Local selection import/export through explicit native file dialogs."""

from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import tempfile
from threading import Lock
from typing import Callable
from uuid import uuid4

from verdflow.dts_adapter import DtsLiveRegion, normalize_live_regions


@dataclass(frozen=True, slots=True)
class RegionExportResult:
    saved: bool
    path: str | None
    region_count: int


@dataclass(frozen=True, slots=True)
class RegionImportResult:
    imported: bool
    source_name: str | None
    regions: tuple[DtsLiveRegion, ...]


class RegionExchangeBusyError(RuntimeError):
    pass


class RegionImportError(ValueError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class RegionExchangeService:
    def __init__(
        self, picker: Callable[[Path | None], Path | None] | None = None,
        import_picker: Callable[[Path | None], Path | None] | None = None,
    ) -> None:
        self._picker = picker or _choose_zip_destination
        self._import_picker = import_picker or _choose_shapefile_source
        self._last_directory: Path | None = None
        self._lock = Lock()

    def export_regions(self, regions: tuple[DtsLiveRegion, ...]) -> RegionExportResult:
        from verdflow.geospatial_worker import ShapefilePolygon, export_polygon_shapefile

        if not self._lock.acquire(blocking=False):
            raise RegionExchangeBusyError("A selection file dialog is already open")
        try:
            payload = export_polygon_shapefile(tuple(
                ShapefilePolygon(region.region_id, region.name, region.coordinates)
                for region in regions
            ))
            destination = self._picker(self._last_directory)
            if destination is None:
                return RegionExportResult(False, None, len(regions))
            destination = Path(destination)
            if destination.suffix.lower() != ".zip":
                raise ValueError("The export destination must end in .zip")
            # Replace only after a complete write; cancellation or IO failure leaves
            # any existing destination intact.
            temporary: Path | None = None
            try:
                with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".verdflow-", delete=False) as output:
                    temporary = Path(output.name)
                    output.write(payload)
                os.replace(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
            self._last_directory = destination.parent
            return RegionExportResult(True, str(destination), len(regions))
        finally:
            self._lock.release()


    def import_regions(self) -> RegionImportResult:
        from verdflow.geospatial_worker import ShapefileImportError, import_polygon_shapefile

        if not self._lock.acquire(blocking=False):
            raise RegionExchangeBusyError("A selection file dialog is already open")
        try:
            source = self._import_picker(self._last_directory)
            if source is None:
                return RegionImportResult(False, None, ())
            try:
                polygons = import_polygon_shapefile(Path(source))
            except ShapefileImportError as error:
                raise RegionImportError(error.code) from error
            try:
                regions = normalize_live_regions([
                    {"region_id": f"live_{uuid4()}", "name": polygon.name,
                     "coordinates": polygon.coordinates}
                    for polygon in polygons
                ])
            except ValueError as error:
                raise RegionImportError("shp_invalid_geometry") from error
            # Keep imported drafts within the existing JSON selection transport.
            if len(json.dumps([asdict(r) for r in regions]).encode("utf-8")) > 30 * 1024 * 1024:
                raise RegionImportError("shp_too_large")
            self._last_directory = Path(source).parent
            return RegionImportResult(True, Path(source).name, regions)
        finally:
            self._lock.release()


def _choose_zip_destination(initial_directory: Path | None) -> Path | None:
    return _choose_selection_file(initial_directory, save=True)


def _choose_shapefile_source(initial_directory: Path | None) -> Path | None:
    return _choose_selection_file(initial_directory, save=False)


def _choose_selection_file(initial_directory: Path | None, *, save: bool) -> Path | None:
    """Use Win32 directly so the portable Python runtime does not need Tcl/Tk."""
    if os.name != "nt":
        raise RuntimeError("Native selection file dialogs currently require Windows")
    import ctypes
    from ctypes import wintypes

    class OpenFileName(ctypes.Structure):
        _fields_ = [
            ("lStructSize", wintypes.DWORD), ("hwndOwner", wintypes.HWND),
            ("hInstance", wintypes.HINSTANCE), ("lpstrFilter", wintypes.LPCWSTR),
            ("lpstrCustomFilter", wintypes.LPWSTR), ("nMaxCustFilter", wintypes.DWORD),
            ("nFilterIndex", wintypes.DWORD), ("lpstrFile", wintypes.LPWSTR),
            ("nMaxFile", wintypes.DWORD), ("lpstrFileTitle", wintypes.LPWSTR),
            ("nMaxFileTitle", wintypes.DWORD), ("lpstrInitialDir", wintypes.LPCWSTR),
            ("lpstrTitle", wintypes.LPCWSTR), ("Flags", wintypes.DWORD),
            ("nFileOffset", wintypes.WORD), ("nFileExtension", wintypes.WORD),
            ("lpstrDefExt", wintypes.LPCWSTR), ("lCustData", wintypes.LPARAM),
            ("lpfnHook", ctypes.c_void_p), ("lpTemplateName", wintypes.LPCWSTR),
            ("pvReserved", ctypes.c_void_p), ("dwReserved", wintypes.DWORD),
            ("FlagsEx", wintypes.DWORD),
        ]

    dialog = ctypes.WinDLL("comdlg32", use_last_error=True)
    user = ctypes.WinDLL("user32", use_last_error=True)
    user.GetForegroundWindow.restype = wintypes.HWND
    choose = dialog.GetSaveFileNameW if save else dialog.GetOpenFileNameW
    choose.argtypes = [ctypes.POINTER(OpenFileName)]
    choose.restype = wintypes.BOOL
    dialog.CommDlgExtendedError.restype = wintypes.DWORD
    buffer = ctypes.create_unicode_buffer("verdflow_regions.zip" if save else "", 32768)
    options = OpenFileName()
    options.lStructSize = ctypes.sizeof(options)
    options.hwndOwner = user.GetForegroundWindow()
    options.lpstrFilter = (
        "Shapefile ZIP (*.zip)\0*.zip\0\0" if save else
        "Shapefile (*.shp;*.zip)\0*.shp;*.zip\0\0"
    )
    options.nFilterIndex = 1
    options.lpstrFile = ctypes.cast(buffer, wintypes.LPWSTR)
    options.nMaxFile = len(buffer)
    options.lpstrInitialDir = str(initial_directory) if initial_directory else None
    options.lpstrTitle = "\u5bfc\u51fa\u9009\u533a SHP" if save else "\u5bfc\u5165\u9009\u533a SHP"
    options.lpstrDefExt = "zip" if save else None
    # Explorer dialog, overwrite confirmation, existing parent, no cwd changes.
    options.Flags = 0x00080000 | 0x00000800 | 0x00000008 | (0x00000002 if save else 0x00001000)
    if not choose(ctypes.byref(options)):
        error = dialog.CommDlgExtendedError()
        if error:
            raise OSError(f"File dialog failed: {error}")
        return None
    return Path(buffer.value)
