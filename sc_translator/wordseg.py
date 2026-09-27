"""英文粘连词分词：RapidOCR 常把整行输出为无空格的一整段（尤其全小写），
翻译前先用词典(wordninja)把连续小写字母长段切分，显著提升翻译质量。

只处理 ≥8 个纯小写英文字母的连续片段（**全小写**才会命中：`Stanton` 这类首字母大写的
专名/人名不受影响），且满足两条才落地：
① 切出来至少两段；② **不出现无意义的单字母碎片**（`a`/`i` 除外）——`interdict or s`
这类碎片是"切坏了"的信号，遇到就整段保持原样。
无词库或出错时原样返回（不阻塞）。

阈值 14 → 8（用户反馈）：`takedisable`(11) / `whichgivesyou`(13) 这类粘连当时切不开。
调用方（ocr.py）会先切掉 `[频道] 玩家名:` 前缀，所以玩家名/ID 不在此列。

上限：只处理**全小写**连续片段 —— 整段无空格且含大写字母的长串（`Youcantakedisablethe…`）
不在此函数范围内（实测真实 OCR 在极小字号下会产出这种串）。
升级触发：真机上出现"整条消息挤成一坨、没有空格"的反馈时，再按同样规则
（长度阈值 + 单字母碎片否决）扩展到混合大小写 token。
"""

from __future__ import annotations

import logging
import re
import threading

log = logging.getLogger(__name__)

_lock = threading.Lock()
_wordninja = None
_load_attempted = False

_MIN_RUN = 8
#: 允许出现的单字母片段（英语里的 a / i；其它单字母基本是"切坏了"的碎片）
_ALLOWED_SINGLE = {"a", "i"}
_RUN_RE = re.compile(r"(?<![A-Za-z])([a-z]{%d,})(?![A-Za-z])" % _MIN_RUN)


def _ensure() -> bool:
    global _wordninja, _load_attempted
    if _wordninja is None and not _load_attempted:
        try:
            import wordninja

            _wordninja = wordninja
        except Exception as exc:  # noqa: BLE001
            log.warning("wordninja 不可用，粘连分词关闭: %s", exc)
        finally:
            _load_attempted = True
    return _wordninja is not None


def split_merged_lower(text: str) -> str:
    """把 text 中连续纯小写长段按单词切分（加空格）。"""
    if not text or len(text) < _MIN_RUN:
        return text
    if not _ensure():
        return text

    def _repl(m: "re.Match[str]") -> str:
        word = m.group(1)
        try:
            parts = _wordninja.split(word)
        except Exception:  # noqa: BLE001
            return word
        if len(parts) <= 1:
            return word
        # 单字母碎片 = 切坏了（实测 interdictors -> "interdict or s"、andromedas -> "andromeda s"；
        # 用户反馈里就有 "interdict or s" / "capac at or s"）→ 整段保持原样，
        # 宁可显示"粘连"也不要显示"被切错"。`a` / `i` 是正常英语单词，放行。
        # 上限：这一整段因此不再切分；升级触发：能拿到可靠英文词表时，改用"只接受
        # 全部碎片都在词表内"的切分（DP），而不是直接放弃。
        if any(len(p) == 1 and p.lower() not in _ALLOWED_SINGLE for p in parts):
            return word
        return " ".join(parts)

    try:
        return _RUN_RE.sub(_repl, text)
    except Exception as exc:  # noqa: BLE001
        log.debug("分词失败，原样返回: %s", exc)
        return text
