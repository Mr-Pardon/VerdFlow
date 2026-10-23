"""System secret storage with a Windows Credential Manager implementation."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
import re
from threading import RLock
from typing import Protocol


_SECRET_REFERENCE = re.compile(r"^[a-z0-9][a-z0-9._/-]{0,127}$")
_TARGET_PREFIX = "VerdFlow/"
_ERROR_NOT_FOUND = 1168
_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_MAX_SECRET_BYTES = 2048


class SecretStoreError(RuntimeError):
    """A system credential operation failed without exposing a secret value."""


class SecretNotFoundError(SecretStoreError):
    def __init__(self, secret_ref: str) -> None:
        super().__init__(f"Secret is not configured: {secret_ref}")
        self.secret_ref = secret_ref


class SecretStore(Protocol):
    def resolve(self, secret_ref: str) -> str: ...

    def contains(self, secret_ref: str) -> bool: ...

    def set(self, secret_ref: str, value: str) -> None: ...

    def delete(self, secret_ref: str) -> bool: ...


class InMemorySecretStore:
    """Deterministic test implementation; never selected as production fallback."""

    def __init__(self) -> None:
        self._values: dict[str, str] = {}
        self._lock = RLock()

    def resolve(self, secret_ref: str) -> str:
        normalized = _validate_secret_ref(secret_ref)
        with self._lock:
            try:
                return self._values[normalized]
            except KeyError as error:
                raise SecretNotFoundError(normalized) from error

    def contains(self, secret_ref: str) -> bool:
        normalized = _validate_secret_ref(secret_ref)
        with self._lock:
            return normalized in self._values

    def set(self, secret_ref: str, value: str) -> None:
        normalized = _validate_secret_ref(secret_ref)
        secret = _validate_secret(value)
        with self._lock:
            self._values[normalized] = secret

    def delete(self, secret_ref: str) -> bool:
        normalized = _validate_secret_ref(secret_ref)
        with self._lock:
            return self._values.pop(normalized, None) is not None


class UnavailableSecretStore:
    """Fail closed when no platform credential implementation exists."""

    def _raise(self) -> None:
        raise SecretStoreError(
            "No operating-system SecretStore is available on this platform"
        )

    def resolve(self, secret_ref: str) -> str:
        _validate_secret_ref(secret_ref)
        self._raise()

    def contains(self, secret_ref: str) -> bool:
        _validate_secret_ref(secret_ref)
        return False

    def set(self, secret_ref: str, value: str) -> None:
        _validate_secret_ref(secret_ref)
        _validate_secret(value)
        self._raise()

    def delete(self, secret_ref: str) -> bool:
        _validate_secret_ref(secret_ref)
        self._raise()


class _CredentialW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", wintypes.FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


class WindowsCredentialSecretStore:
    """Store generic credentials for the current Windows user."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise SecretStoreError("Windows Credential Manager is unavailable")
        self._advapi = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
        pointer = ctypes.POINTER(_CredentialW)
        self._advapi.CredWriteW.argtypes = [pointer, wintypes.DWORD]
        self._advapi.CredWriteW.restype = wintypes.BOOL
        self._advapi.CredReadW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(pointer),
        ]
        self._advapi.CredReadW.restype = wintypes.BOOL
        self._advapi.CredDeleteW.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
        ]
        self._advapi.CredDeleteW.restype = wintypes.BOOL
        self._advapi.CredFree.argtypes = [ctypes.c_void_p]
        self._advapi.CredFree.restype = None

    def resolve(self, secret_ref: str) -> str:
        normalized = _validate_secret_ref(secret_ref)
        credential_pointer = ctypes.POINTER(_CredentialW)()
        if not self._advapi.CredReadW(
            _target(normalized),
            _CRED_TYPE_GENERIC,
            0,
            ctypes.byref(credential_pointer),
        ):
            error = ctypes.get_last_error()
            if error == _ERROR_NOT_FOUND:
                raise SecretNotFoundError(normalized)
            raise SecretStoreError(
                f"Windows Credential Manager read failed: {error}"
            )
        try:
            credential = credential_pointer.contents
            payload = ctypes.string_at(
                credential.CredentialBlob,
                credential.CredentialBlobSize,
            )
            try:
                value = payload.decode("utf-8")
            except UnicodeDecodeError as error:
                raise SecretStoreError(
                    "Stored Windows credential is not valid UTF-8"
                ) from error
            return _validate_secret(value)
        finally:
            self._advapi.CredFree(credential_pointer)

    def contains(self, secret_ref: str) -> bool:
        try:
            self.resolve(secret_ref)
            return True
        except SecretNotFoundError:
            return False

    def set(self, secret_ref: str, value: str) -> None:
        normalized = _validate_secret_ref(secret_ref)
        payload = _validate_secret(value).encode("utf-8")
        blob = (ctypes.c_ubyte * len(payload)).from_buffer_copy(payload)
        credential = _CredentialW()
        credential.Type = _CRED_TYPE_GENERIC
        credential.TargetName = _target(normalized)
        credential.CredentialBlobSize = len(payload)
        credential.CredentialBlob = ctypes.cast(
            blob,
            ctypes.POINTER(ctypes.c_ubyte),
        )
        credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "VerdFlow"
        if not self._advapi.CredWriteW(ctypes.byref(credential), 0):
            error = ctypes.get_last_error()
            raise SecretStoreError(
                f"Windows Credential Manager write failed: {error}"
            )

    def delete(self, secret_ref: str) -> bool:
        normalized = _validate_secret_ref(secret_ref)
        if self._advapi.CredDeleteW(
            _target(normalized),
            _CRED_TYPE_GENERIC,
            0,
        ):
            return True
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return False
        raise SecretStoreError(
            f"Windows Credential Manager delete failed: {error}"
        )


def platform_secret_store() -> SecretStore:
    if os.name == "nt":
        return WindowsCredentialSecretStore()
    return UnavailableSecretStore()


def _target(secret_ref: str) -> str:
    return _TARGET_PREFIX + secret_ref


def _validate_secret_ref(value: object) -> str:
    if not isinstance(value, str) or not _SECRET_REFERENCE.fullmatch(value):
        raise ValueError("secret_ref is invalid")
    return value


def _validate_secret(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("secret value is required")
    if "\x00" in value:
        raise ValueError("secret value contains a null byte")
    encoded = value.encode("utf-8")
    if len(encoded) > _MAX_SECRET_BYTES:
        raise ValueError("secret value exceeds size limit")
    return value
