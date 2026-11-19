"""API Key 的本地加密存储。

使用 Windows DPAPI（CryptProtectData / CryptUnprotectData）将密钥加密后落盘，
只有当前 Windows 用户在本机可解密，与参考项目的做法一致。
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import os

CRYPTPROTECT_UI_FORBIDDEN = 0x1
LOCAL_MACHINE = 0x4  # 不用；保持用户级


class DATA_BLOB(ctypes.Structure):
    _fields_ = [("cbData", wt.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _make_blob(data: bytes) -> DATA_BLOB:
    buf = (ctypes.c_ubyte * len(data))(*data) if data else (ctypes.c_ubyte * 1)(0)
    return DATA_BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_ubyte)))


def _blob_bytes(blob: DATA_BLOB) -> bytes:
    n = blob.cbData
    if n == 0:
        return b""
    addr = ctypes.cast(blob.pbData, ctypes.c_void_p).value
    arr = (ctypes.c_ubyte * n).from_address(addr)
    return bytes(arr)


def _free_blob(blob: DATA_BLOB) -> None:
    if blob.pbData:
        ctypes.windll.kernel32.LocalFree(blob.pbData)  # type: ignore[attr-defined]


def dpapi_available() -> bool:
    try:
        return hasattr(ctypes, "windll") and hasattr(ctypes.windll, "crypt32")
    except Exception:
        return False


def plaintext_fallback_allowed() -> bool:
    """是否允许把 API Key 明文落盘。

    默认**禁止**：过去只要 `dpapi_available()` 为假就写明文，Windows 上 crypt32
    偶发取不到时会静默把密钥写成明文。只有满足其一才放行：
    - 非 Windows 平台（本来就没有 DPAPI，明文是唯一可用方案）；
    - 显式设置环境变量 ``SC_TRANSLATOR_ALLOW_PLAINTEXT_KEY=1``（用户主动 opt-in）。
    """
    if os.name != "nt":
        return True
    return os.environ.get("SC_TRANSLATOR_ALLOW_PLAINTEXT_KEY", "") == "1"


# 显式声明 DPAPI 函数签名：use_last_error=True 让 ctypes 在调用前清零 last-error、
# 调用后自行保存，这样 get_last_error() 取到的才是**本次**调用的错误码
# （直接 windll 调用取到的是上一次其它 Win32 调用遗留的值）。参考 cpu_pin.py:46-52。
_DPAPI_PROTO = None
_CRYPT32 = None


def _prepare() -> bool:
    """惰性准备带签名的 CryptProtectData / CryptUnprotectData。"""
    global _DPAPI_PROTO, _CRYPT32
    if not dpapi_available():
        return False
    try:
        crypt32 = ctypes.windll.crypt32  # type: ignore[attr-defined]
        proto = ctypes.WINFUNCTYPE(
            wt.BOOL,
            ctypes.POINTER(DATA_BLOB),   # pDataIn
            ctypes.c_wchar_p,            # szDataDescr
            ctypes.c_void_p,             # pOptionalEntropy
            ctypes.c_void_p,             # pvReserved
            ctypes.c_void_p,             # pPromptStruct
            wt.DWORD,                    # dwFlags
            ctypes.POINTER(DATA_BLOB),   # pDataOut
            use_last_error=True,
        )
        _DPAPI_PROTO = proto
        _CRYPT32 = crypt32
        return True
    except Exception:  # noqa: BLE001
        return False


def protect(data: bytes) -> bytes:
    """加密 bytes -> bytes（进程外可持久化）。失败向上抛，绝不明文落盘。"""
    if not _prepare():
        raise RuntimeError("Windows DPAPI 不可用")
    pin = _make_blob(data)  # 输入引用 Python 内存，绝不能 LocalFree
    pout = DATA_BLOB()
    fn = _DPAPI_PROTO(("CryptProtectData", _CRYPT32))
    ok = fn(
        ctypes.byref(pin),
        ctypes.c_wchar_p("SCTranslator api key"),
        None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(pout),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _blob_bytes(pout)
    finally:
        _free_blob(pout)   # 只有输出 blob 由 crypt32 LocalAlloc 分配


def unprotect(data: bytes) -> bytes:
    if not _prepare():
        raise RuntimeError("Windows DPAPI 不可用")
    pin = _make_blob(data)
    pout = DATA_BLOB()
    fn = _DPAPI_PROTO(("CryptUnprotectData", _CRYPT32))
    ok = fn(
        ctypes.byref(pin),
        None, None, None, None,
        CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(pout),
    )
    if not ok:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        return _blob_bytes(pout)
    finally:
        _free_blob(pout)
