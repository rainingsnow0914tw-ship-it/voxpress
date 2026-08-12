"""Single-instance ownership using a Windows session-scoped named mutex."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

ERROR_ALREADY_EXISTS = 183


class SingleInstance:
    def __init__(self, name: str = "Local\\VoxPress") -> None:
        self.name = name
        self._handle = None
        self._kernel32 = None

    def acquire(self) -> bool:
        if sys.platform != "win32":
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL

        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            raise OSError(ctypes.get_last_error(), "CreateMutexW failed")
        if ctypes.get_last_error() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._kernel32 = kernel32
        self._handle = handle
        return True

    def release(self) -> None:
        if self._handle is None or self._kernel32 is None:
            return
        try:
            self._kernel32.CloseHandle(self._handle)
        finally:
            self._handle = None
            self._kernel32 = None

    def __enter__(self):
        if not self.acquire():
            raise RuntimeError("VoxPress is already running")
        return self

    def __exit__(self, *_exc_info) -> None:
        self.release()
