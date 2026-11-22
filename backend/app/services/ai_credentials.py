from __future__ import annotations

import base64
import ctypes
import json
import os
from dataclasses import dataclass
from pathlib import Path

_DPAPI_PREFIX = "dpapi:"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data, len(data))
    return _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char))), buffer


def _protect(data: bytes) -> bytes | None:
    """使用 Windows DPAPI 以当前用户身份加密，失败时返回 None（绝不落盘明文）。"""
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_in, _buffer = _blob(data)
        blob_out = _DataBlob()
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        crypt32.CryptProtectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptProtectData.restype = ctypes.c_int
        ok = crypt32.CryptProtectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
    except Exception:
        return None


def _unprotect(data: bytes) -> bytes | None:
    if os.name != "nt":
        return None
    try:
        crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        blob_in, _buffer = _blob(data)
        blob_out = _DataBlob()
        kernel32.LocalFree.argtypes = [ctypes.c_void_p]
        kernel32.LocalFree.restype = ctypes.c_void_p
        crypt32.CryptUnprotectData.argtypes = [
            ctypes.POINTER(_DataBlob),
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.POINTER(_DataBlob),
        ]
        crypt32.CryptUnprotectData.restype = ctypes.c_int
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(blob_in),
            None,
            None,
            None,
            None,
            _CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(blob_out),
        )
        if not ok:
            return None
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            kernel32.LocalFree(ctypes.cast(blob_out.pbData, ctypes.c_void_p))
    except Exception:
        return None


@dataclass(slots=True)
class SavedAISettings:
    base_url: str = ""
    model: str = ""
    api_key: str = ""

    @property
    def has_key(self) -> bool:
        return bool(self.api_key)


class AISettingsStore:
    """在本机保存 AI 连接配置；密钥使用 DPAPI 加密，不写入数据库与日志。"""

    def __init__(self, path: Path):
        self.path = path

    def load(self) -> SavedAISettings:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return SavedAISettings()
        if not isinstance(payload, dict):
            return SavedAISettings()
        api_key = ""
        protected = payload.get("api_key")
        if isinstance(protected, str) and protected.startswith(_DPAPI_PREFIX):
            try:
                raw = base64.b64decode(protected[len(_DPAPI_PREFIX) :])
            except ValueError:
                raw = b""
            if raw:
                decrypted = _unprotect(raw)
                if decrypted:
                    api_key = decrypted.decode("utf-8", errors="replace")
        return SavedAISettings(
            base_url=str(payload.get("base_url") or ""),
            model=str(payload.get("model") or ""),
            api_key=api_key,
        )

    def save(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
    ) -> SavedAISettings:
        current = self.load()
        next_base = current.base_url if base_url is None else base_url
        next_model = current.model if model is None else model
        next_key = current.api_key if api_key is None else api_key
        payload: dict[str, str] = {
            "base_url": next_base,
            "model": next_model,
        }
        if next_key:
            protected = _protect(next_key.encode("utf-8"))
            if protected is not None:
                payload["api_key"] = _DPAPI_PREFIX + base64.b64encode(protected).decode("ascii")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(temporary, self.path)
        return SavedAISettings(base_url=next_base, model=next_model, api_key=next_key)
