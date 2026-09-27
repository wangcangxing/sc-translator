"""全局热键（RegisterHotKey + Qt 原生消息过滤）。

热键即使游戏在前台也生效（需同用户会话）。注册失败只记日志，不影响运行。
"""

from __future__ import annotations

import ctypes
import logging
from ctypes import wintypes
from typing import Callable, Optional

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008

_k32 = ctypes.windll.user32 if hasattr(ctypes, "windll") else None


def register(hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> bool:
    if _k32 is None:
        return False
    return bool(_k32.RegisterHotKey(wintypes.HWND(hwnd), hotkey_id, modifiers, vk))


def unregister(hwnd: int, hotkey_id: int) -> None:
    if _k32 is None:
        return
    _k32.UnregisterHotKey(wintypes.HWND(hwnd), hotkey_id)


def foreground_window() -> int:
    """当前前台窗口句柄（0 = 取不到 / 非 Windows）。"""
    if _k32 is None:
        return 0
    try:
        return int(_k32.GetForegroundWindow())
    except Exception:  # noqa: BLE001
        return 0


def restore_foreground(hwnd: int) -> bool:
    """把前台窗口还给 ``hwnd``（点浮窗按钮后把键盘焦点还给游戏）。

    为什么需要：点浮窗上的一键翻译按钮会让**本进程**接管前台，游戏随即收不到键盘；
    而抓屏/翻译都在后台线程跑、结果自己会显示出来，所以点完就该立刻把前台还回去。

    权限：Windows 只允许"当前前台进程"（我们刚被点过）或"刚收到输入的进程"改前台，
    正常能直接成功；失败时再用 AttachThreadInput 把本线程的输入队列临时接到目标线程重试
    （这是绕开 SetForegroundWindow 前台锁定限制的标准做法）。
    """
    if _k32 is None or not hwnd:
        return False
    try:
        u32 = _k32
        if not u32.IsWindow(wintypes.HWND(hwnd)):
            return False
        if int(u32.GetForegroundWindow()) == int(hwnd):
            return True
        if u32.SetForegroundWindow(wintypes.HWND(hwnd)):
            return True
        if not hasattr(ctypes, "windll"):
            return False
        cur = int(ctypes.windll.kernel32.GetCurrentThreadId())
        fg = int(u32.GetForegroundWindow())
        attached: list[int] = []
        for tid in (
            int(u32.GetWindowThreadProcessId(wintypes.HWND(fg), None)),
            int(u32.GetWindowThreadProcessId(wintypes.HWND(hwnd), None)),
        ):
            if tid and tid != cur and u32.AttachThreadInput(cur, tid, True):
                attached.append(tid)
        try:
            return bool(u32.SetForegroundWindow(wintypes.HWND(hwnd)))
        finally:
            for tid in attached:
                u32.AttachThreadInput(cur, tid, False)
    except Exception:  # noqa: BLE001
        return False


class QtHotkeyFilter(QAbstractNativeEventFilter):
    """捕获 WM_HOTKEY 并把回调投递到 GUI 线程。"""

    def __init__(self) -> None:
        super().__init__()
        self.handlers: dict[int, Callable[[], None]] = {}
        self._seen_msgs = 0

    def nativeEventFilter(self, eventType, message):  # noqa: N802 (Qt 命名)
        try:
            self._seen_msgs += 1
            if self._seen_msgs == 1:
                log.debug("原生事件过滤器已启用（eventType=%r）", eventType)
            if eventType != b"windows_generic_MSG" and eventType != "windows_generic_MSG":
                return False, 0
            msg = wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                handler = self.handlers.get(int(msg.wParam))
                log.info("收到 WM_HOTKEY id=%s（已注册: %s）", msg.wParam, sorted(self.handlers))
                if handler is not None:
                    try:
                        handler()
                    except Exception as exc:  # noqa: BLE001
                        log.warning("热键回调异常: %s", exc)
                    return True, 0
        except Exception as exc:  # noqa: BLE001
            log.debug("热键消息解析跳过: %s", exc)
        return False, 0


class HotkeyService:
    """注册/注销一组全局热键。"""

    def __init__(self, hwnd: int) -> None:
        self._hwnd = hwnd
        self._filter = QtHotkeyFilter()
        self._ids: list[int] = []

    def install(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.instance().installNativeEventFilter(self._filter)

    def add(self, hotkey_id: int, modifiers: int, vk: int, handler: Callable[[], None]) -> bool:
        if not register(self._hwnd, hotkey_id, modifiers, vk):
            log.warning("热键注册失败 id=%s vk=0x%02X（可能被其它程序占用）", hotkey_id, vk)
            return False
        self._filter.handlers[hotkey_id] = handler
        self._ids.append(hotkey_id)
        return True

    def remove(self) -> None:
        from PySide6.QtWidgets import QApplication

        app = QApplication.instance()
        if app is not None:
            try:
                app.removeNativeEventFilter(self._filter)
            except Exception:  # noqa: BLE001
                pass
        for hid in self._ids:
            unregister(self._hwnd, hid)
        self._ids.clear()
