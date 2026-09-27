"""轻量文本工具（不依赖 numpy/cv2 等重型库，打包体积友好）。

原先 _cjk_ratio 放在 ocr.py 里，导致纯文字翻译路径间接依赖 OCR 全栈
（numpy / opencv / rapidocr）；现在抽到本模块，打包时可将 OCR 全部排除。
"""

from __future__ import annotations

import re

#: SC 聊天行的"消息头"：``[频道] 玩家名:``。频道括号可能是 OCR 认歪的 ``【(（``，
#: 右括号也可能丢（实测出现过 ``[全局Maelb:``），所以右括号写成可选。
CHAT_HEAD_RE = re.compile(r"^\s*[\[【(（][^\]】)）]{1,16}[\]】)）]?\s*[^:：]{0,32}[:：]")


def split_chat_prefix(text: str) -> tuple[str, str]:
    """把 ``[频道] 玩家名:正文`` 切成 ``(前缀, 正文)``；不是聊天行时返回 ``("", 原文)``。

    用途（两处都靠它保护玩家名/频道名）：
    - 术语表预替换与兜底替换只作用于**正文**（否则玩家叫 Cpt_Andromeda 会被译成「Cpt_仙女座」）；
    - 粘连词分词只作用于**正文**（玩家名/ID 不该被拆开）。
    """
    m = CHAT_HEAD_RE.match(text or "")
    if not m:
        return "", text or ""
    return m.group(0), (text or "")[m.end():]


def cjk_ratio(text: str) -> float:
    """汉字占比（0~1）。用于判断某段文字是否已经是中文。"""
    if not text:
        return 0.0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    return cjk / len(text)
