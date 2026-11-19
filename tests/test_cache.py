"""翻译缓存键回归：同一原文、不同目标语言必须落到不同的缓存条目。

约定：API 代理会把 target 纳入 ``cache_model``（见 client.OpenAiCompatClient 的
``_cache_model`` / ``translate_line``），因此缓存键不能只由 (model, src, text) 决定，
否则「译成中文」和「译成英文」会互相串味、返回错误目标语言的结果。
这里用假传输层端到端验证：两次不同 target 的翻译必须各自请求一次、各自命中自己的缓存。
"""

from __future__ import annotations

from sc_translator.translate.cache import TranslationCache
from sc_translator.translate.client import ClientOptions, OpenAiCompatClient


class _Resp:
    status_code = 200
    text = ""

    def __init__(self, content=""):
        self._content = content

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _client(cache, translated_by_target):
    """假传输层：GET /models 定前缀，POST 按当前 target 返回对应译文并计数。"""
    calls = {"translate": []}

    def fake_get(url, headers=None, **kw):  # noqa: ARG001
        return _Resp("")

    def fake_post(url, payload=None, headers=None, **kw):  # noqa: ARG001
        system = (payload or {}).get("messages", [{}])[0].get("content", "")
        # 目标语言写在 system 提示词里（简体中文 / English / Japanese ...）
        target = next((t for t in ("简体中文", "English", "Japanese", "Korean") if t in system), "?")
        calls["translate"].append(target)
        return _Resp(translated_by_target[target])

    opts = ClientOptions(api_base="https://fake.invalid", api_key="k", model="m",
                         post=fake_post, get=fake_get)
    return OpenAiCompatClient(opts, cache=cache), calls


def test_same_text_different_target_uses_distinct_cache_entries(tmp_home):
    cache = TranslationCache(path=tmp_home / "cache.json")
    source = "quantum travel"   # 非中文，不会被 CJK 直通短路

    client, calls = _client(cache, {"简体中文": "量子旅行", "English": "Quantum travel EN"})
    # 第一次：译成中文
    assert client.translate_line(source, source_lang="en", target_lang="zh-CN") == "量子旅行"
    # 第二次：同样原文译成英文——不能命中上面那条中文缓存，必须真的请求并按英文译文返回
    assert client.translate_line(source, source_lang="en", target_lang="English") == "Quantum travel EN"
    assert calls["translate"] == ["简体中文", "English"], "不同 target 不应互相命中对方缓存"

    # 反向再请求一次：各自都应命中自己的缓存（不再新增请求）
    assert client.translate_line(source, source_lang="en", target_lang="zh-CN") == "量子旅行"
    assert client.translate_line(source, source_lang="en", target_lang="English") == "Quantum travel EN"
    assert calls["translate"] == ["简体中文", "English"], "重复请求应命中缓存，不增加 API 调用"
