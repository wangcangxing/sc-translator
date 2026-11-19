"""星际公民聊天中文编码（社区"输入法"码表）。

原理（与 SC 汉化盒子 / citizenwiki 社区输入法一致，本机实测验证）：

1. 汉化包把整块 ``码=汉字`` 追加进游戏的 ``global.ini``，夹在两行标记之间::

       _starcitizen_doctor_localization_community_input_method_version=<版本>
       00=一
       01=乙
       ...
       IH=你
       _starcitizen_doctor_localization_version=<版本>

2. 星际公民的本地化字符串支持 ``@KEY`` 引用语法（官方串里就有
   ``item_DescConstellation_Cargo_Prototype=@Constellation Cargo for PU Demo``），
   所以聊天里发 ``@IH``，客户端会查本地化键 ``IH`` 并渲染成「你」。

3. 码 = 汉字在该码表里的序号转 base36（``0-9A-Z``，最少两位），
   例如 ``IH``=665=你、``E8``=512=好、``AP``=385=吗。

编码规则（逐字照搬社区实现）：
- ``A-Z a-z 0-9`` 与 Unicode 标点/符号（含全角 ``，。？！``）原样直通，前面按需补一个空格；
- 汉字 → ``@码``，码与码之间无分隔；
- 码表里没有的字符（含空格、换行）→ 输出一个空格；
- 整条消息加 ``[zh] `` 前缀（社区约定：提示对方这是中文编码，需装汉化）；
- 首尾空白与连续空格会被归一化（见 encode 注释，仅影响可读性，不影响游戏端解析）。

关键约束：**码表必须与对局中客户端一致**，所以默认从本机已安装汉化的
``global.ini`` 里读取，而不是内置写死。
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from pathlib import Path
from typing import Iterable, Optional

log = logging.getLogger(__name__)

MARK_BEGIN = "_starcitizen_doctor_localization_community_input_method_version="
MARK_END = "_starcitizen_doctor_localization_version="
PREFIX = "[zh]"

# 解码时用于清理"编码器插进去的分隔空格"的全角标点集
_FULLWIDTH_PUNCT = "，。！？；：、（）【】〔〕《》〈〉「」『』“”‘’…—～·"

# ---------- 全局状态（与 glossary.py 同风格：进程内单份码表）----------
_c2t: dict[str, str] = {}   # 码 -> 汉字
_t2c: dict[str, str] = {}   # 汉字 -> 码
_meta: dict[str, object] = {"source": "", "version": "", "size": 0}


class GameCodeError(RuntimeError):
    """码表未就绪或内容非法。"""


# ------------------------------------------------------------------ 加载
def clear() -> None:
    _c2t.clear()
    _t2c.clear()
    _meta.update({"source": "", "version": "", "size": 0})


def configured() -> bool:
    return bool(_t2c)


def size() -> int:
    return len(_t2c)


def version() -> str:
    return str(_meta.get("version", ""))


def source() -> str:
    return str(_meta.get("source", ""))


def _commit(pairs: dict[str, str], src: str, ver: str = "") -> int:
    """装入码表（先清空再写入，保证不残留旧表）。"""
    _c2t.clear()
    _t2c.clear()
    for code, ch in pairs.items():
        if not code or not ch:
            continue
        _c2t[code] = ch
        _t2c.setdefault(ch, code)
    _meta.update({"source": src, "version": ver, "size": len(_t2c)})
    log.info("游戏码表就绪：%d 字%s（%s）", len(_t2c), f"，版本 {ver}" if ver else "", src)
    return len(_t2c)


def parse_ini_text(text: str) -> tuple[dict[str, str], str]:
    """从 global.ini 内容里切出码表块：返回 ({码: 汉字}, 版本)。"""
    pairs: dict[str, str] = {}
    ver = ""
    inside = False
    for raw in text.splitlines():
        line = raw.strip()
        if not inside:
            if line.startswith(MARK_BEGIN):
                inside = True
                ver = line.split("=", 1)[-1].strip()
            continue
        if line.startswith(MARK_END):
            break
        if not line or "=" not in line:
            continue
        code, ch = line.split("=", 1)
        code = code.strip().upper()
        if code and ch:
            pairs[code] = ch
    return pairs, ver


def load_global_ini(path: str | os.PathLike[str]) -> int:
    """从汉化后的 global.ini 载入码表。返回字数（0 = 该文件没有码表块）。"""
    p = Path(path)
    pairs, ver = parse_ini_text(p.read_text(encoding="utf-8", errors="replace"))
    if not pairs:
        clear()
        raise GameCodeError(f"{p.name} 里没有社区输入法码表块（需装带支持的汉化）")
    return _commit(pairs, str(p), ver)


def load_text(text: str, src: str = "<memory>") -> int:
    """从码表文件内容载入（测试/自定义用）。支持两种格式：

    - ``码=汉字`` 每行一条（社区数据文件格式）；
    - 完整 global.ini（自动切出标记块）。
    """
    if MARK_BEGIN in text:
        pairs, ver = parse_ini_text(text)
    else:
        pairs, ver = {}, ""
        for raw in text.splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            code, ch = line.split("=", 1)
            pairs[code.strip().upper()] = ch
    if not pairs:
        clear()
        raise GameCodeError("码表内容为空")
    return _commit(pairs, src, ver)


# ------------------------------------------------------------------ 定位 global.ini
_LANG_PREFER = ("chinese_(simplified)", "chinese_(traditional)")
_BRANCH_PREFER = ("LIVE", "PTU", "EPTU", "TECH-PREVIEW")

# 深度受限的通配（避免全盘递归）：<盘>:\[*\]*StarCitizen\<分支>\data\Localization\<语言>\global.ini
_GLOB_PATTERNS = (
    "StarCitizen/*/data/Localization/*/global.ini",
    "*/StarCitizen/*/data/Localization/*/global.ini",
    "*/*/StarCitizen/*/data/Localization/*/global.ini",
    "*/*/*/StarCitizen/*/data/Localization/*/global.ini",
)


def _drives() -> list[Path]:
    out: list[Path] = []
    for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
        p = Path(f"{letter}:\\")
        try:
            if p.exists():
                out.append(p)
        except OSError:
            continue
    return out


def _rank(path: Path) -> tuple:
    s = str(path).lower()
    lang = next((i for i, L in enumerate(_LANG_PREFER) if L.lower() in s), len(_LANG_PREFER))
    branch = next((i for i, B in enumerate(_BRANCH_PREFER) if f"\\{B.lower()}\\" in s), len(_BRANCH_PREFER))
    return (lang, branch, s)


def find_global_inis(roots: Optional[Iterable[str | os.PathLike[str]]] = None) -> list[Path]:
    """列出这些根目录下可能的 global.ini（由浅到深扫，不读取文件内容）。

    roots 为空时扫描本机所有盘符；传入 roots 时只扫这些目录（测试/高级用户用）。
    """
    out: list[Path] = []
    seen: set[str] = set()
    for cands in _iter_candidate_rounds(roots):
        for p in cands:
            key = str(p).lower()
            if key not in seen:
                seen.add(key)
                out.append(p)
    return out


def _iter_candidate_rounds(roots: Optional[Iterable[str | os.PathLike[str]]] = None):
    """按"通配深度由浅到深"分批产出候选路径。

    浅层先扫的好处：命中即停时不必去遍历深层目录（常见布局在第二层就能找到，
    能把自动检测从数秒降到百毫秒级）。
    """
    bases = [Path(r) for r in roots] if roots else _drives()
    for pat in _GLOB_PATTERNS:
        found: list[Path] = []
        for base in bases:
            try:
                if not base.exists():
                    continue
                found.extend(base.glob(pat))
            except OSError:
                continue
        uniq: dict[str, Path] = {}
        for p in found:
            try:
                if p.is_file():
                    uniq[str(p).lower()] = p
            except OSError:
                continue
        yield sorted(uniq.values(), key=_rank)


def autodetect(roots: Optional[Iterable[str | os.PathLike[str]]] = None) -> Optional[Path]:
    """自动检测：返回第一个真正带码表块的 global.ini（命中即停）。"""
    for cands in _iter_candidate_rounds(roots):
        for p in cands:
            try:
                pairs, _ = parse_ini_text(p.read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
            if pairs:
                return p
    return None


def resolve_path(configured_path: str = "") -> Optional[Path]:
    """解析可用码表来源：设置路径 > 环境变量 SC_GAMECODE_INI > 自动检测。"""
    for cand in (configured_path, os.environ.get("SC_GAMECODE_INI", "")):
        if cand:
            p = Path(cand)
            if p.is_file():
                return p
    return autodetect()


# ------------------------------------------------------------------ 编码 / 解码
def _is_passthrough(ch: str) -> bool:
    """社区实现：``^[a-zA-Z0-9\\p{P}\\p{S}]+$`` —— ASCII 字母数字 + Unicode 标点/符号。"""
    if ch.isascii() and ch.isalnum():
        return True
    return unicodedata.category(ch)[:1] in ("P", "S")


def encode(text: str) -> str:
    """中文 -> 游戏聊天码（含 ``[zh] `` 前缀）。码表未加载时抛 GameCodeError。"""
    if not _t2c:
        raise GameCodeError("游戏码表未加载：请先安装带社区输入法支持的汉化，或手动选择 global.ini")
    buf: list[str] = []
    left_safe = True          # 上一个字符是"直通"字符（不需要再加分隔空格）
    for ch in text:
        if _is_passthrough(ch):
            buf.append(ch if left_safe else " " + ch)
            left_safe = True
            continue
        code = _t2c.get(ch)
        if code:
            if left_safe:
                buf.append(" ")
            buf.append("@" + code)
        else:
            buf.append(" ")   # 码表未覆盖：与原实现一致，丢成空格
        left_safe = False
    out = "".join(buf)
    if not out.strip():
        return ""
    # 两处归一化（游戏端不关心空格，归一后人类可读且与原工具输出一致）：
    # 1) 原实现在首个码前会多写一个空格（左安全位初值）——trim 掉；
    # 2) 中英混排时"未匹配字符"和"ASCII 前补空格"会叠出双空格——压成一个。
    out = re.sub(r"[ \t]{2,}", " ", out).strip()
    return f"{PREFIX} {out}"


def has_zh_marker(text: str) -> bool:
    return text.lstrip().lower().startswith(PREFIX)


def looks_encoded(text: str) -> bool:
    """粗判：是否像一条游戏码消息（有 [zh] 前缀，或含至少一个有效码）。"""
    if has_zh_marker(text):
        return True
    if not _c2t:
        return False
    for m in re.finditer(r"@([0-9A-Za-z]{2,3})", text):
        if _resolve_code(m.group(1).upper()):
            return True
    return False


def _resolve_code(token: str) -> tuple[str, int] | None:
    """按编码器的确定性规则判定 token 是否是码。

    编码器的两条不变式让判定可以很严格：
    1. 码与码之间直接相连（``@IH@E8``），中间没有空格；
    2. 码与任何 ASCII 字符之间**必定**有一个空格。

    因此 ``@`` 后面若跟了 2 或 3 个字母数字，只有**整段**正好等于一个码才算码：
    ``@Bob``（3 个字母，不是码）不会被拆成 ``@BO``+``b``，从而避免把玩家名解成汉字。
    """
    if len(token) not in (2, 3):
        return None
    return (token, len(token)) if token in _c2t else None


def decode(text: str) -> str:
    """游戏聊天码 -> 中文。

    - 去掉开头的 ``[zh]`` 标记（大小写不敏感）；
    - ``@码`` 还原为汉字；不符合码规则的 ``@token`` 原样保留（例如玩家名 ``@Bob``）；
    - 编码时插入的填充空格会被压缩，CJK/全角标点两侧的多余空格也会去掉。
    """
    if not _c2t:
        raise GameCodeError("游戏码表未加载：请先安装带社区输入法支持的汉化，或手动选择 global.ini")
    s = re.sub(r"^\s*\[zh\]\s*", "", text.strip(), flags=re.I)
    out: list[str] = []
    i, n = 0, len(s)
    while i < n:
        if s[i] == "@":
            j, token = i + 1, ""
            while j < n and s[j].isascii() and s[j].isalnum():
                token += s[j].upper()
                j += 1
                if len(token) > 3:      # 超过 3 位一定不是码，按字面处理
                    break
            hit = _resolve_code(token) if token else None
            if hit:
                code, _used = hit
                out.append(_c2t[code])
                i = i + 1 + len(code)
                continue
            out.append(s[i])
            i += 1
            continue
        out.append(s[i])
        i += 1
    res = "".join(out)
    res = re.sub(r"[ \t]{2,}", " ", res)
    # 全角标点两侧的空格是编码器为了分隔码而插入的（中文排版本来也不加空格），去掉；
    # 中文词之间用户自己打的空格会被保留。
    res = re.sub(rf"[ \t]*([{_FULLWIDTH_PUNCT}])[ \t]*", r"\1", res)
    return res.strip()


def _collapse_adjacent_codes(s: str) -> str:
    """消除 OCR 偶尔在连续字符码之间误插入的空格（例如 ``@IH @E8`` -> ``@IH@E8``）。
    
    仅当前后两个 token 均为有效码时才合并，防止把类似 ``@Bob @Alice`` 等普通文本错误连接。
    """
    def rep(m: re.Match) -> str:
        c1, c2 = m.group(1).upper(), m.group(2).upper()
        if _resolve_code(c1) and _resolve_code(c2):
            return f"@{c1}@{c2}"
        return m.group(0)

    old = ""
    while old != s:
        old = s
        s = re.sub(r"@([0-9A-Za-z]{2,3})\s+@([0-9A-Za-z]{2,3})", rep, s)
    return s


def _has_bad_at_context(text: str) -> bool:
    """扫描 text 里的每个 ``@``，判断是否属于"不该解码"的上下文。

    命中任意一条即返回 True（调用方据此整条返回原文，避免部分解码）：
    - ``@`` 后跟 0 或 1 位字母数字（半截码 / 孤立 @），例如 ``@IH@E`` 的 ``@E``；
    - 邮箱/域名上下文：``@`` 前是普通字母（而非空格/开头/上一个 ``@码`` 的结尾），
      或 ``@码`` 后紧跟 ``.域名``，例如 ``a@IH.com``。

    合法码消息 ``@IH@E8@AP`` / ``@IH @E8`` 的每个 ``@`` 要么在开头/空格后，
    要么紧接上一个 ``@码`` 的结尾，不会命中。
    """
    i, n = 0, len(text)
    prev_was_code = False          # 前一个 token 是不是形如 @码 的码（决定紧邻的 @ 是否合法）
    while i < n:
        if text[i] != "@":
            prev_was_code = False
            i += 1
            continue
        j = i + 1
        while j < n and text[j].isascii() and text[j].isalnum():
            j += 1
        token_len = j - i - 1
        if token_len < 2:           # @E / @ / @后非字母数字：半截码，整条不解码
            return True
        if j < n and text[j] == "." and j + 1 < n and text[j + 1].isascii() and text[j + 1].isalnum():
            return True             # @IH.com 之类域名
        if i > 0 and text[i - 1].isascii() and text[i - 1].isalnum() and not prev_was_code:
            return True             # a@IH：@ 前是普通字母，不是码尾
        prev_was_code = 2 <= token_len <= 3
        i = j
    return False


def decode_chat(text: str) -> str:
    """对聊天/OCR 文本做字符码还原为汉字（保留 ``[频道] 玩家名:`` 前缀）。

    流程：
    1. 码表未加载或文本中不含 ``@`` 且无 ``[zh]`` 标记时直接返回原样，避免额外开销；
    2. 切分前缀 ``[频道] 玩家名:`` 与消息正文，只对正文（或无前缀时的全文）做解码；
    3. 正文含半截码或邮箱/域名上下文时整条返回原文，不部分解码；
    4. 若包含连续的字符码且中间夹杂 OCR 空格，先做折叠消除；
    5. 执行 decode 将 ``@xx`` 码还原为汉字；若解码过程异常则安全回退返回原文；
    6. 拼回消息前缀并返回。
    """
    if not configured() or not text:
        return text
    from .textutil import split_chat_prefix

    head, body = split_chat_prefix(text)
    target = body if head else text
    if "@" not in target and not has_zh_marker(target):
        return text
    if _has_bad_at_context(target):
        return text
    try:
        cleaned = _collapse_adjacent_codes(target)
        dec = decode(cleaned)
    except Exception as exc:  # noqa: BLE001
        log.debug("decode_chat 解码跳过: %s", exc)
        return text

    if not head:
        return dec
    sep = " " if body.startswith(" ") else ""
    return head + sep + dec


def status() -> dict[str, object]:
    """给 UI/自检用的状态摘要。"""
    return {
        "ready": configured(),
        "size": size(),
        "version": version(),
        "source": source(),
    }
