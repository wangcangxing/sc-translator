"""日志初始化：文件 + 可选内存/回调缓冲（供主窗口展示）。"""

from __future__ import annotations

import logging
import threading
from logging.handlers import RotatingFileHandler

from . import APP_NAME
from .paths import log_file

_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"

# 简单环形缓冲，供 UI 日志面板展示（避免直接注入 Qt）
class RingBufferHandler(logging.Handler):
    def __init__(self, capacity: int = 500):
        super().__init__()
        self._cap = capacity
        self._lines: list[str] = []
        self._lock = threading.Lock()

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            with self._lock:
                self._lines.append(msg)
                if len(self._lines) > self._cap:
                    del self._lines[: len(self._lines) - self._cap]
        except Exception:
            pass

    def snapshot(self) -> list[str]:
        with self._lock:
            return list(self._lines)


ring = RingBufferHandler()
ring.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)

    # 清掉自己之前装的 handler（按标记），但不提前 return：第三方（pytest/coverage 等）
    # 挂的 handler 要一并移除，否则会残留、重复输出。
    for h in list(root.handlers):
        if getattr(h, "_sc_translator_own", False):
            root.removeHandler(h)
            try:
                h.close()
            except Exception:  # noqa: BLE001
                pass
        else:
            root.removeHandler(h)

    # 重配时清空环形缓冲：否则切 DEBUG/INFO 后 UI 日志面板仍显示上一档的旧行
    with ring._lock:
        ring._lines.clear()

    fh = RotatingFileHandler(log_file(), maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter(_FMT))
    fh.setLevel(logging.DEBUG)
    fh._sc_translator_own = True  # type: ignore[attr-defined]
    root.addHandler(fh)

    ring.setLevel(level)
    ring._sc_translator_own = True  # type: ignore[attr-defined]
    root.addHandler(ring)

    logging.getLogger(APP_NAME).info("logging ready -> %s", log_file())
