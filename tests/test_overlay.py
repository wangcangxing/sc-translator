"""译文悬浮框（常驻置顶）回归：按需结果累积、同文去重、上限淘汰、回话开关与文案。

全部离屏运行（conftest 已设 QT_QPA_PLATFORM=offscreen）：不抓屏、不联网、不真显示窗口。
"""

from __future__ import annotations

import os
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("SC_CI_SKIP_GUI", "") == "1", reason="环境跳过")


def _mk_ctrl(qapp, tmp_home, **kw):
    from sc_translator.app import AppController
    from sc_translator.settings import Settings

    s = Settings().load()
    s.theme = "dark"
    for k, v in kw.items():
        setattr(s, k, v)
    s.save()
    ctrl = AppController(qapp, settings=s)
    ctrl.init_ui()
    return ctrl


def _wait_for(qapp, pred, timeout_s: float = 3.0) -> bool:
    """跑事件循环直到 pred() 为真（后台线程的结果经 Qt 信号回主线程）。"""
    end = time.time() + timeout_s
    while time.time() < end:
        qapp.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    qapp.processEvents()
    return bool(pred())


# ---------------------------------------------------------------- 装配
def test_overlay_is_created_and_hidden_by_default(qapp, tmp_home):
    """浮窗随主窗口一起装配，且默认不显示（不按键不打扰）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    assert ctrl.overlay is not None, "译文悬浮框应随主窗口一起创建"
    assert not ctrl.overlay.isVisible(), "默认不应显示浮窗"
    assert ctrl.overlay.ctx is ctrl, "浮窗 ctx 应为 AppController"
    ctrl.shutdown()


def test_overlay_has_one_click_capture_button(qapp, tmp_home):
    """浮窗上有一键截图翻译：游戏里全局热键失灵时的鼠标入口（用户要求）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    calls = []
    ctrl.mainwin.on_snap_hotkey = lambda: calls.append(1)   # 替身：不真抓屏
    ctrl.overlay._snap_btn.click()
    assert calls == [1], "浮窗「截图翻译」按钮应复用主窗口的处理函数"
    ctrl.shutdown()


def _fake_focus(monkeypatch, current: int = 0x1234):
    """把前台窗口帮手换成替身：记录"还给了哪个 hwnd"。"""
    from sc_translator.ui import overlay as ovmod

    restored: list[int] = []
    monkeypatch.setattr(ovmod, "foreground_window", lambda: current)
    monkeypatch.setattr(ovmod, "restore_foreground", lambda hwnd: restored.append(hwnd) or True)
    return restored


def _track_now(ov, hwnd: int) -> None:
    """模拟轮询刚看到的外部前台窗口。"""
    import time as _t

    ov._last_fg = hwnd
    ov._last_fg_at = _t.monotonic()


def test_snap_button_returns_focus_to_game(qapp, tmp_home, monkeypatch):
    """点完一键翻译要把前台还给游戏（点浮窗会让本进程接管前台，游戏就收不到键盘）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.snap_region = {"physical": {"left": 0, "top": 0, "width": 10, "height": 10}}
    restored = _fake_focus(monkeypatch)
    _track_now(ctrl.overlay, 0x1234)                 # 刚看到游戏在前台
    ctrl.mainwin.on_snap_hotkey = lambda: None
    ctrl.overlay._snap_btn.click()
    assert restored == [0x1234], "有区域 + 刚从游戏切过来 → 应把前台还回去"
    ctrl.shutdown()


def test_snap_button_ignores_stale_foreign_window(qapp, tmp_home, monkeypatch):
    """桌面上用久了（上次外部前台早过期）→ 不动焦点，别把别的程序拽回前台。"""
    import time as _t

    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.snap_region = {"physical": {"left": 0, "top": 0, "width": 10, "height": 10}}
    restored = _fake_focus(monkeypatch)
    ctrl.overlay._last_fg = 0x1234
    ctrl.overlay._last_fg_at = _t.monotonic() - 10.0
    ctrl.mainwin.on_snap_hotkey = lambda: None
    ctrl.overlay._snap_btn.click()
    assert restored == [], "过期的跟踪值不该触发前台切换"
    ctrl.shutdown()


def test_grip_snap_button_triggers_and_returns_focus(qapp, tmp_home, monkeypatch):
    """穿透态下标题栏是隐藏的 → 一键翻译按钮必须在常驻小条上，且同样还焦点。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.snap_region = {"physical": {"left": 0, "top": 0, "width": 10, "height": 10}}
    restored = _fake_focus(monkeypatch)
    _track_now(ctrl.overlay, 0x1234)
    calls = []
    ctrl.mainwin.on_snap_hotkey = lambda: calls.append(1)
    grip_btn = ctrl.overlay._floating_grip._snap_btn
    assert grip_btn.text(), "常驻小条上的一键翻译按钮应有文案"
    grip_btn.click()
    assert calls == [1], "小条按钮应复用同一个处理函数"
    assert restored == [0x1234], "小条按钮同样要把前台还给游戏"
    ctrl.shutdown()


def test_foreground_poll_records_foreign_window(qapp, tmp_home, monkeypatch):
    """轮询记下外部前台窗口（点击时它就是"该还回去的那个"）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    _fake_focus(monkeypatch, current=0xABCD)
    ctrl.overlay._poll_foreground()
    assert ctrl.overlay._last_fg == 0xABCD
    ctrl.shutdown()


def test_foreground_poll_skips_own_window(qapp, tmp_home, monkeypatch):
    """轮询必须跳过我们自己的窗口——否则点击时拿到的是自己，焦点就还不回去（真机踩过）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    _fake_focus(monkeypatch, current=int(ctrl.overlay.winId()))
    ctrl.overlay._poll_foreground()
    assert ctrl.overlay._last_fg == 0
    ctrl.shutdown()


def test_snap_button_keeps_focus_when_no_region(qapp, tmp_home, monkeypatch):
    """还没框选过区域时会进全屏框选，那一步需要焦点 → 不能把前台还回去。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.snap_region = None
    restored = _fake_focus(monkeypatch)
    _track_now(ctrl.overlay, 0x1234)
    ctrl.mainwin.on_snap_hotkey = lambda: None
    ctrl.overlay._snap_btn.click()
    assert restored == [], "进框选时不能抢走焦点"
    ctrl.shutdown()


def test_snap_button_does_not_restore_to_own_window(qapp, tmp_home, monkeypatch):
    """点击前的前台是本程序自己的窗口（纯桌面使用）→ 不还焦点，别把别的程序拽回来。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.snap_region = {"physical": {"left": 0, "top": 0, "width": 10, "height": 10}}
    own = int(ctrl.mainwin.winId())
    ctrl.overlay._last_fg = own
    restored = _fake_focus(monkeypatch, current=own)
    ctrl.mainwin.on_snap_hotkey = lambda: None
    ctrl.overlay._snap_btn.click()
    assert restored == [], "目标是我们自己时不该做任何前台切换"
    ctrl.shutdown()


def test_exchange_card_follows_theme_opacity(qapp, tmp_home):
    """用户反馈：调低不透明度后，回话区是一块不透明黑。

    根因是卡片自带不透明底色；样式必须由 theme.overlay_style 统一给出（才能随不透明度淡出）。
    """
    from PySide6.QtWidgets import QFrame

    from sc_translator.ui.theme import overlay_style

    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=True)
    ctrl.overlay._add_exchange("你好", "Hello", "English")
    cards = ctrl.overlay._exch_host.findChildren(QFrame, "ovExchCard")
    assert cards, "问答卡片应使用统一对象名 ovExchCard"
    assert not cards[0].styleSheet(), "卡片不该自带不透明底色"
    css = overlay_style("dark", 30, 13)
    assert "#ovExchCard" in css, "卡片样式应在 theme.overlay_style 里（才会随不透明度变化）"
    assert "#ovWrap QWidget" in css, "浮窗内部要统一压掉不透明底色（应用级 QWidget 规则会补黑底）"
    ctrl.shutdown()


def test_overlay_header_flat_by_default(qapp, tmp_home):
    """默认平铺：截图翻译/嘴臭/穿透/固定/✕ 都在，下拉菜单按钮不显示。

    注意：顶栏只在**固定态**存在（穿透态只有顶部小条），所以先固定再断言。
    """
    ctrl = _mk_ctrl(qapp, tmp_home)
    ov = ctrl.overlay
    ov.show_overlay()
    ov.set_pinned(True, notify_main=False)
    qapp.processEvents()
    for b in (ov._snap_btn, ov._spicy_btn, ov._ct_btn, ov._pin_btn, ov._btn_hide):
        assert b.isVisible(), "平铺态这些按钮都应可见"
    assert not ov._more_btn.isVisible(), "平铺态不该出现 ⋯ 菜单按钮"
    ctrl.shutdown()


def test_overlay_header_menu_mode_collapses_buttons(qapp, tmp_home):
    """勾选「顶栏收进下拉菜单」后只留 ⋯ 与 ✕：可点区域变小（用户反馈：浮窗会抢鼠标）。"""
    ctrl = _mk_ctrl(qapp, tmp_home, menu_header=True)
    ov = ctrl.overlay
    ov.show_overlay()
    ov.set_pinned(True, notify_main=False)
    qapp.processEvents()
    for b in (ov._snap_btn, ov._spicy_btn, ov._ct_btn, ov._pin_btn):
        assert not b.isVisible(), "菜单态这些平铺按钮应收起"
    assert ov._more_btn.isVisible(), "菜单态必须有 ⋯ 入口"
    assert ov._btn_hide.isVisible(), "✕（立刻隐藏浮窗）两种样式都留着"
    ctrl.shutdown()


def test_overlay_header_menu_has_same_actions(qapp, tmp_home):
    """收进菜单不能少功能：动作要与平铺按钮一一对应（只构建菜单，不真的弹出）。"""
    from sc_translator.i18n import t as _t

    ctrl = _mk_ctrl(qapp, tmp_home, menu_header=True)
    menu, handlers = ctrl.overlay._build_overlay_menu()
    labels = [a.text() for a in menu.actions() if a.text()]
    assert labels[0] == _t("ov.snap"), labels
    assert any(x in labels for x in (_t("ov.menu.spicy_on"), _t("ov.menu.spicy_off"))), labels
    assert any(x in labels for x in (_t("ov.click_through.on"), _t("ov.click_through.off"))), labels
    assert any(x in labels for x in (_t("ov.menu.pin"), _t("ov.menu.unpin"))), labels
    assert _t("ov.menu.hide") in labels, labels

    # 派发：选中「截图翻译」应走到主窗口那个入口（替身，不真抓屏）
    calls: list[int] = []
    ctrl.mainwin.on_snap_hotkey = lambda: calls.append(1)
    handlers[[a for a in handlers if a.text() == _t("ov.snap")][0]]()
    assert calls == [1]
    ctrl.shutdown()


def test_menu_header_setting_applies_without_restart(qapp, tmp_home):
    """主窗口设置里勾选后立刻生效（用户要求"自由选择平铺还是下拉菜单"）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ov = ctrl.overlay
    ov.show_overlay()
    ov.set_pinned(True, notify_main=False)
    qapp.processEvents()
    assert ov._snap_btn.isVisible()

    ctrl.mainwin._ov_menu_header.setChecked(True)
    qapp.processEvents()
    assert ctrl.settings.menu_header is True, "设置应落盘"
    assert not ov._snap_btn.isVisible() and ov._more_btn.isVisible()

    ctrl.mainwin._ov_menu_header.setChecked(False)
    qapp.processEvents()
    assert ctrl.settings.menu_header is False
    assert ov._snap_btn.isVisible() and not ov._more_btn.isVisible()
    ctrl.shutdown()


def test_push_lines_accumulates_and_dedupes(qapp, tmp_home):
    """两次截图翻译：同文只占一行，新文追加（历史累积，不是"整屏快照消失即删"）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    win = ctrl.mainwin
    win._push_overlay_rows([("Quantum travel to Crusader", "量子航行至十字军")])
    assert ctrl.overlay.isVisible(), "有新结果时应自动显示浮窗"
    win._push_overlay_rows(
        [
            ("Quantum travel to Crusader", "量子航行至十字军"),
            ("Bounty mission updated", "赏金任务已更新"),
        ]
    )
    assert list(ctrl.overlay._rows) == ["Quantum travel to Crusader", "Bounty mission updated"]
    ctrl.shutdown()


def test_max_entries_evicts_oldest(qapp, tmp_home):
    """超过 settings.max_entries 时淘汰最早的行（该设置此前是零读取的死配置）。"""
    ctrl = _mk_ctrl(qapp, tmp_home, max_entries=2)
    ctrl.mainwin._push_overlay_rows([("A", "甲"), ("B", "乙"), ("C", "丙")])
    assert list(ctrl.overlay._rows) == ["B", "C"]
    ctrl.shutdown()


def test_clear_all_empties_rows(qapp, tmp_home):
    """清空：行全部移除、计数归零。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.mainwin._push_overlay_rows([("A", "甲"), ("B", "乙")])
    ctrl.overlay.clear_all()
    assert ctrl.overlay._rows == {}
    assert "0" in ctrl.overlay._count.text()
    ctrl.shutdown()


# ---------------------------------------------------------------- 开关
def test_reply_enabled_toggles_reply_panel(qapp, tmp_home):
    """reply_enabled 真正决定浮窗回话输入条显隐（此前该字段零读取）。

    注意用 isHidden() 而不是 isVisible()：父窗口未显示时，子控件的 isVisible() 恒为假。
    """
    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=False)
    assert ctrl.overlay._reply_panel.isHidden(), "关着时回话条应隐藏"

    ctrl.mainwin._ov_reply.setChecked(True)
    assert not ctrl.overlay._reply_panel.isHidden(), "打开后回话条应显示"
    assert ctrl.settings.reply_enabled is True, "开关应落盘"

    ctrl.mainwin._ov_reply.setChecked(False)
    assert ctrl.overlay._reply_panel.isHidden()
    assert ctrl.settings.reply_enabled is False
    ctrl.shutdown()


def test_overlay_reply_copies_only_when_auto_copy_on(qapp, tmp_home):
    """auto_copy_reply 真正决定浮窗回话是否自动进剪贴板（此前该字段零读取）。"""
    from PySide6.QtWidgets import QApplication

    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=True, auto_copy_reply=True)

    def fake_handler(text, target, done):
        done(True, f"[{target}]{text}")

    ctrl.translate_reply_async = fake_handler  # 同步替身，不联网
    ov = ctrl.overlay
    ov._reply_input.setText("你好吗")
    ov._send_reply()
    assert QApplication.clipboard().text() == "[English]你好吗"
    assert ov._exchanges == [("你好吗", "[English]你好吗", "English")], "应记录一条问答"

    QApplication.clipboard().setText("(未复制)")
    ctrl.mainwin._ov_autocopy.setChecked(False)
    assert ctrl.settings.auto_copy_reply is False
    ov._reply_input.setText("再来一次")
    ov._send_reply()
    assert QApplication.clipboard().text() == "(未复制)", "关掉自动复制后不应写入剪贴板"
    assert len(ov._exchanges) == 2
    ctrl.shutdown()


def test_translate_reply_async_reports_missing_key(qapp, tmp_home):
    """未配置 API Key 时，浮窗回话应回调失败并给出可读原因（不是抛异常）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    assert not ctrl.settings.load_api_key(), "临时数据目录里不应有 Key"
    got = []
    ctrl.translate_reply_async("你好", "English", lambda ok, val: got.append((ok, val)))
    assert got and got[0][0] is False
    assert isinstance(got[0][1], str) and got[0][1]
    ctrl.shutdown()


def test_translate_reply_async_uses_client(qapp, tmp_home):
    """有 Key 时走后台线程翻译，并在主线程回调 done(True, 译文)。"""

    class _FakeClient:
        def translate_reply(self, text, target, spicy=False):
            return f"[{target}]{text}"

    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.settings.save_api_key("sk-test")
    ctrl.make_client = lambda use_cache=True: _FakeClient()
    got = []
    ctrl.translate_reply_async("你好", "Korean", lambda ok, val: got.append((ok, val)))
    assert _wait_for(qapp, lambda: bool(got)), "应在超时前回调"
    assert got == [(True, "[Korean]你好")]
    ctrl.shutdown()


# ---------------------------------------------------------------- 文案/主题
def test_overlay_texts_follow_language(qapp, tmp_home):
    """浮窗文案走 i18n：切语言后 retranslate 应换文案（旧版是硬编码中文）。"""
    from sc_translator import i18n

    ctrl = _mk_ctrl(qapp, tmp_home)
    ov = ctrl.overlay
    i18n.set_language("zh_CN")
    ov.retranslate()
    zh = ov._title.text()
    i18n.set_language("en")
    ov.retranslate()
    en = ov._title.text()
    assert zh != en, (zh, en)
    assert not any("\u4e00" <= ch <= "\u9fff" for ch in en), en
    ctrl.shutdown()


def test_overlay_theme_refresh_does_not_crash(qapp, tmp_home):
    """主题/文案刷新（主窗口每次改设置都会回调）不应抛异常。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    ctrl.mainwin.refresh_overlay_controls()
    ctrl.overlay.apply_theme()
    ctrl.overlay.apply_click_through()
    ctrl.shutdown()


# ---------------------------------------------------------------- 显示位置开关
class _FakeRes:
    """替身：_show_snap_result 只用到 pairs()/error/elapsed_ms/ocr_ms。"""

    error = ""
    elapsed_ms = 0
    ocr_ms = 0

    def __init__(self, pairs):
        self._pairs = list(pairs)

    def pairs(self):
        return self._pairs


def test_both_output_switches_on_by_default(qapp, tmp_home):
    """默认两个显示位置都开：结果既进常驻浮窗，也弹鼠标旁快看浮窗。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    win = ctrl.mainwin
    assert win._snap_overlay.isChecked() and win._snap_popup.isChecked()
    win._show_snap_result(_FakeRes([("Quantum travel", "量子航行")]))
    assert list(ctrl.overlay._rows) == ["Quantum travel"]
    assert win._popup is not None and win._popup.isVisible()
    ctrl.shutdown()


def test_overlay_switch_off_keeps_results_in_main_window(qapp, tmp_home):
    """关掉「常驻悬浮窗」：结果不再进浮窗，但仍写主窗口结果区（手动复制路径）。"""
    ctrl = _mk_ctrl(qapp, tmp_home, snap_show_overlay=False)
    win = ctrl.mainwin
    win._show_snap_result(_FakeRes([("Quantum travel", "量子航行")]))
    assert ctrl.overlay._rows == {}, "关掉后不应再有浮窗行"
    assert win._snap_src.toPlainText() == "Quantum travel"
    assert win._snap_dst.toPlainText() == "量子航行"
    ctrl.shutdown()


def test_popup_switch_off_skips_cursor_popup(qapp, tmp_home):
    """关掉「鼠标旁浮窗」：不弹快看浮窗，但常驻浮窗与主窗口照常收到结果。"""
    ctrl = _mk_ctrl(qapp, tmp_home, snap_show_popup=False)
    win = ctrl.mainwin
    win._show_snap_result(_FakeRes([("Quantum travel", "量子航行")]))
    assert win._popup is None or not win._popup.isVisible()
    assert list(ctrl.overlay._rows) == ["Quantum travel"]
    assert win._snap_dst.toPlainText() == "量子航行"
    ctrl.shutdown()


def test_both_switches_off_only_main_window(qapp, tmp_home):
    """两个都关：只写主窗口结果区，两个浮窗都不出现（由用户手动复制）。"""
    ctrl = _mk_ctrl(qapp, tmp_home, snap_show_overlay=False, snap_show_popup=False)
    win = ctrl.mainwin
    win._show_snap_result(_FakeRes([("Quantum travel", "量子航行")]))
    assert ctrl.overlay._rows == {}
    assert not ctrl.overlay.isVisible()
    assert win._popup is None
    assert win._snap_dst.toPlainText() == "量子航行"
    ctrl.shutdown()


def test_overlay_checkbox_shows_and_hides_overlay(qapp, tmp_home):
    """勾选框即时生效：打开=立刻露出，关闭=立刻收起，并写进设置。"""
    ctrl = _mk_ctrl(qapp, tmp_home, snap_show_overlay=False)
    win = ctrl.mainwin
    win._snap_overlay.setChecked(True)
    assert ctrl.settings.snap_show_overlay is True
    assert ctrl.overlay.isVisible()
    win._snap_overlay.setChecked(False)
    assert ctrl.settings.snap_show_overlay is False
    assert not ctrl.overlay.isVisible()
    ctrl.shutdown()


# ---------------------------------------------------------------- 旋钮控件（第 12 轮）
def test_overlay_knob_widgets_reflect_settings(qapp, tmp_home):
    """原先"只能改 settings.json"的旋钮，现在都有控件且反映当前设置。"""
    ctrl = _mk_ctrl(
        qapp, tmp_home,
        snap_max_lines=25, snap_write_main=False,
        always_show=False, auto_hide_sec=5, max_entries=77,
        font_size=16, opacity=60, show_original=False, click_through=False,
    )
    win = ctrl.mainwin
    assert win._snap_max_lines.value() == 25
    assert win._snap_write_main.isChecked() is False
    assert win._ov_always.isChecked() is False
    assert win._ov_auto_hide.value() == 5
    assert win._ov_max_entries.value() == 77
    assert win._ov_font.value() == 16
    assert win._ov_opacity.value() == 60
    assert win._ov_show_original.isChecked() is False
    assert win._ov_click_through.isChecked() is False
    ctrl.shutdown()


def test_overlay_knob_edits_persist_and_apply(qapp, tmp_home):
    """改控件立即落盘，并按需即时生效（样式重刷、穿透态同步）。"""
    ctrl = _mk_ctrl(qapp, tmp_home)
    win = ctrl.mainwin

    win._ov_max_entries.setValue(300)
    assert ctrl.settings.max_entries == 300
    # 调小上限要立即裁剪已有行，而不是等下次截图
    ctrl.mainwin._push_overlay_rows([(f"L{i}", f"译{i}") for i in range(5)])
    assert len(ctrl.overlay._rows) == 5
    win._ov_max_entries.setValue(2)
    assert len(ctrl.overlay._rows) == 2, list(ctrl.overlay._rows)
    win._ov_font.setValue(18)
    assert ctrl.settings.font_size == 18
    win._ov_opacity.setValue(70)
    assert ctrl.settings.opacity == 70
    win._ov_auto_hide.setValue(9)
    assert ctrl.settings.auto_hide_sec == 9
    win._snap_max_lines.setValue(80)
    assert ctrl.settings.snap_max_lines == 80
    win._snap_write_main.setChecked(False)
    assert ctrl.settings.snap_write_main is False
    win._ov_always.setChecked(False)
    assert ctrl.settings.always_show is False

    # 穿透开关：取消勾选 = 进入固定态；重新勾选 = 回到穿透态
    win._ov_click_through.setChecked(False)
    assert ctrl.settings.click_through is False and ctrl.overlay.pinned() is True
    win._ov_click_through.setChecked(True)
    assert ctrl.settings.click_through is True and ctrl.overlay.pinned() is False
    ctrl.shutdown()


def test_show_original_toggle_rerenders_existing_rows(qapp, tmp_home):
    """「显示原文」关掉后应**立即重渲染已有行**，而不是等下次截图。"""
    from PySide6.QtWidgets import QLabel

    ctrl = _mk_ctrl(qapp, tmp_home, show_original=True)
    win = ctrl.mainwin
    win._push_overlay_rows([("Hello there", "你好")])
    row = ctrl.overlay._rows["Hello there"]
    lbl = row.findChild(QLabel)
    assert "Hello there" in lbl.text()

    win._ov_show_original.setChecked(False)
    assert "Hello there" not in lbl.text(), lbl.text()
    assert ctrl.settings.show_original is False
    ctrl.shutdown()


# ---------------------------------------------------------------- 逐区域穿透（第 17 轮）
def test_passthrough_keeps_rows_and_reply_interactive(qapp, tmp_home):
    """穿透态下：译文滚动区与回话输入条仍归浮窗处理，其余区域才穿透。

    真实故障：旧实现用整窗 WS_EX_TRANSPARENT / WA_TransparentForMouseEvents，
    于是"打开鼠标穿透时回话功能失效、无法滚动"。
    """
    from PySide6.QtCore import QPoint, Qt

    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=True, click_through=True)
    ov = ctrl.overlay
    ov.show_overlay()
    ov.push_lines([{"key": f"L{i}", "text": f"L{i}", "translated": f"译{i}", "pending": False}
                   for i in range(8)])
    qapp.processEvents()

    assert ov.pinned() is False, "click_through=True 应为穿透态"
    # 整窗级"鼠标透明"必须关掉，否则子控件收不到事件（这正是两个 bug 的根因）
    assert ov.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents) is False

    inside = lambda w: ov.is_interactive_point(w.mapTo(ov, QPoint(w.width() // 2, w.height() // 2)))
    assert inside(ov._scroll), "译文滚动区在穿透态下必须仍可滚"
    assert inside(ov._reply_panel), "回话条在穿透态下必须仍可用"
    assert not ov.is_interactive_point(QPoint(3, ov.height() // 2)), "边距空白应穿透给下面的窗口"

    # 固定态：整窗可交互（边距也算）
    ov.set_pinned(True)
    assert ov.is_interactive_point(QPoint(3, ov.height() // 2))
    ctrl.shutdown()


def test_passthrough_state_survives_reply_sync(qapp, tmp_home):
    """穿透状态不能被回话条的同步路径改回去（曾表现为"穿透开关点了等于没点"）。"""
    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=True)
    win, ov = ctrl.mainwin, ctrl.overlay

    win._ov_click_through.setChecked(True)        # 用户勾上「鼠标穿透」
    assert ctrl.settings.click_through is True
    assert ov.pinned() is False

    win.refresh_overlay_controls()                # 任何同步路径都不得重新固定
    assert ov.pinned() is False, "同步回话条时不应把穿透改回固定"
    assert ctrl.settings.click_through is True

    ov.set_reply_enabled(True)                     # 显式同步同样不得改状态
    assert ov.pinned() is False
    ctrl.shutdown()


def test_overlay_click_through_button_toggles_and_syncs(qapp, tmp_home):
    """浮窗标题栏上的「穿透」按钮：能来回切，并与主窗口勾选双向同步。"""
    ctrl = _mk_ctrl(qapp, tmp_home, reply_enabled=True, click_through=True)
    win, ov = ctrl.mainwin, ctrl.overlay
    ov.show_overlay()
    qapp.processEvents()

    assert ov.pinned() is False
    ov._ct_btn.click()                            # 穿透 -> 固定
    assert ov.pinned() is True
    assert ctrl.settings.click_through is False
    assert win._ov_click_through.isChecked() is False, "主窗口勾选应同步"
    assert "穿" in ov._ct_btn.text() or "Through" in ov._ct_btn.text(), ov._ct_btn.text()

    ov._ct_btn.click()                            # 固定 -> 穿透
    assert ov.pinned() is False
    assert ctrl.settings.click_through is True
    assert win._ov_click_through.isChecked() is True
    ctrl.shutdown()


def test_pending_rows_show_source_text_even_without_show_original(qapp, tmp_home):
    """分阶段反馈：「识别中…」行必须总带原文，译文回来后按同一个 key 原地替换。"""
    from PySide6.QtWidgets import QLabel

    ctrl = _mk_ctrl(qapp, tmp_home, show_original=False)
    win, ov = ctrl.mainwin, ctrl.overlay
    ov.show_overlay()

    win._show_snap_partial(["Quantum travel to Crusader"])
    row = ov._rows["Quantum travel to Crusader"]
    lbl = row.findChild(QLabel)
    assert "Quantum travel to Crusader" in lbl.text(), lbl.text()
    assert ov._row_data["Quantum travel to Crusader"]["pending"] is True
    assert "1" in win._status.text(), win._status.text()      # 状态栏「已识别 1 行，正在翻译…」

    win._push_overlay_rows([("Quantum travel to Crusader", "量子航行至十字军")])
    assert "量子航行至十字军" in row.findChild(QLabel).text(), row.findChild(QLabel).text()
    assert ov._row_data["Quantum travel to Crusader"]["pending"] is False
    assert len(ov._rows) == 1, "同一行不得重复占位"
    ctrl.shutdown()


def test_push_lines_scrolls_to_bottom(qapp, tmp_home):
    """每次新结果都要贴到列表最底部（旧实现「永远差一屏」）。

    旧写法是插完行再用 QTimer.singleShot(0) 取 sb.maximum()，那一刻新行还没布局完，
    拿到的是**旧**范围：实测推 30 行后 value=0、再推 30 行才跳到上一批的底部。
    """
    ctrl = _mk_ctrl(qapp, tmp_home)
    ov = ctrl.overlay
    ov.show_overlay()
    qapp.processEvents()
    sb = ov._scroll.verticalScrollBar()

    def push(n: int, prefix: str) -> None:
        ov.push_lines([{"key": f"{prefix}{i}", "text": f"{prefix}{i}", "translated": f"译{i}",
                        "pending": False} for i in range(n)])
        for _ in range(6):
            qapp.processEvents()
            time.sleep(0.01)

    push(30, "A")
    assert sb.maximum() > 0, "行足够多时应该有可滚动范围"
    assert sb.value() == sb.maximum(), (sb.value(), sb.maximum())
    push(30, "B")
    assert sb.value() == sb.maximum(), f"第二批结果不得差一屏：{sb.value()}/{sb.maximum()}"

    # 用户往回滚之后，新结果仍要把视图带回最底部（按需翻译：按一次热键就是看最新）
    sb.setValue(max(0, sb.maximum() // 2))
    qapp.processEvents()
    push(1, "C")
    assert sb.value() == sb.maximum(), f"新结果应回到最底部：{sb.value()}/{sb.maximum()}"
    ctrl.shutdown()
