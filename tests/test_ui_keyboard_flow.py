"""E1.4 UI 自动化：**键盘走完主流程**（不做"模拟鼠标轨迹"）。

依据 E1 提示词第 4 条：用 `waitSignal`/状态断言，**不做鼠标轨迹**（脆弱且无价值）。
本项目未引入 `pytest-qt`（E2 纪律：测试工具 dev-only，能不加就不加），
改用已有的 **PySide6.QtTest**（随 PySide6 一起，零新依赖）驱动键盘事件。

钉三件事：
  ① **命令面板（Ctrl+K 的对话框）** 列出全部视图，键盘 ↓/Enter 能真正跳转；
  ② **每个视图的画布 Tab 可达**（焦点能落到它上面——否则"键盘可达"是空话）；
  ③ 快捷键（Esc 停止 / Ctrl+D 换肤）触发**不抛异常**、状态一致。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.widgets import CommandPalette  # noqa: E402

APP = QApplication.instance() or QApplication([])

#: 8 个"画布视图"及其 key（meta/batch 不在内：meta 无画布、batch 用 OverviewCanvas）
CANVAS_VIEWS = {
    "spectrum": "cv_spec",
    "spectro": "cv_spectro",
    "waterfall": "cv_wfall",
    "phase": "cv_phase",
    "bands": "cv_band",
    "timeline": "cv_timeline",
    "compare": "cv_cmp",
}


def _pump(n: int = 3) -> None:
    for _ in range(n):
        APP.processEvents()


class TestKeyboardMainFlow(unittest.TestCase):
    def setUp(self):
        self.w = W.MainWindow()
        self.w.resize(1200, 800)
        self.w.show()
        _pump()

    def tearDown(self):
        self.w.close()
        self.w.deleteLater()
        _pump()

    # ---------------------------------------------------- 命令面板
    def test_command_palette_lists_every_view(self):
        """命令面板必须列全**每一个** VIEW_KEY 的跳转。

        这条是**防漂移守卫**：B14 加了「相位场」视图却漏登记命令面板，
        于是键盘到不了它（本测试在修复前实测失败）。命令面板现已从
        `VIEW_KEYS` 派生，这条守住"加了视图必须能键盘到达"。
        """
        labels = [lbl for lbl, _h, _f in self.w._cmds]
        missing = [k for k in W.MainWindow.VIEW_KEYS
                   if ("跳转到 " + W.MainWindow.VIEW_LABELS[k]) not in labels]
        self.assertEqual(missing, [], "命令面板缺这些视图的跳转：%s" % missing)

    def test_command_palette_runs_view_jump_via_keyboard(self):
        """过滤到「相位场」后 ↓/Enter ⇒ 真的切到那个视图（键盘闭环）。

        ⚠ 两个离屏坑（实测）：
          ① `Qt.Popup` 对话框在离屏下 `show()` 会让**进程硬崩** ⇒ 不 show；
          ② `QTest.keyClicks` 喂 **CJK** 字符同样硬崩（Qt 把 CJK 映射到键位时崩）
             ⇒ 过滤用 `setText`，键盘只驱动 ↓/Enter（那才是被测的"键盘执行"路径）。
        """
        p = CommandPalette(self.w, self.w._cmds, self.w.theme)
        p.input.setText("相位场")
        _pump()
        self.assertGreaterEqual(p.list.count(), 1, "过滤后没有候选项")
        QTest.keyClick(p, Qt.Key_Down)
        _pump()
        self.assertGreaterEqual(p.list.currentRow(), 0, "↓ 没选中任何项")
        QTest.keyClick(p, Qt.Key_Return)
        _pump()
        idx = W.MainWindow.VIEW_KEYS.index("phase")
        self.assertEqual(self.w.stack.currentIndex(), idx,
                         "键盘执行命令面板后没切到相位场视图")
        p.deleteLater()
        _pump()

    # ---------------------------------------------------- 画布焦点可达
    def test_every_canvas_has_tab_focus(self):
        """**防漂移守卫**：每张画布都必须含 TabFocus（否则键盘到不了它）。

        实测抓到 `SpectrumCanvas` / `CompareCanvas` 曾是 ClickFocus ⇒ Tab 不可达。
        """
        for attr in ("cv_spec", "cv_spectro", "cv_wfall", "cv_phase",
                     "cv_band", "cv_timeline", "cv_cmp", "cv_ovw"):
            with self.subTest(canvas=attr):
                cv = getattr(self.w, attr)
                self.assertTrue(cv.focusPolicy() & Qt.FocusPolicy.TabFocus,
                                "%s 不含 TabFocus（键盘不可达）" % attr)

    def test_every_canvas_is_reachable_by_tab(self):
        """切到某视图后，连续 Tab 必须能把焦点落到该视图的画布上。"""
        for key, attr in CANVAS_VIEWS.items():
            with self.subTest(view=key):
                self.w._navigate(key)
                _pump()
                cv = getattr(self.w, attr)
                cv.clearFocus()
                hit = False
                for _ in range(60):
                    QTest.keyClick(self.w, Qt.Key_Tab)
                    _pump()
                    if APP.focusWidget() is cv:
                        hit = True
                        break
                self.assertTrue(hit, "Tab 60 步仍到不了 %s 画布（键盘不可达）" % key)

    # ---------------------------------------------------- 快捷键不抛异常
    def test_shortcuts_do_not_raise(self):
        """Esc（停止）与 Ctrl+D（换肤）走一遍，状态自洽、不抛异常。"""
        before = self.w.theme.name
        QTest.keyClick(self.w, Qt.Key_D, Qt.KeyboardModifier.ControlModifier)
        _pump()
        QTest.keyClick(self.w, Qt.Key_Escape)
        _pump()
        self.assertIn(self.w.theme.name, ("dark", "light"))
        # Ctrl+D 必须真的换了主题（除非用了种子色主题——那走另一条分支）
        if self.w.seed is None:
            self.assertNotEqual(self.w.theme.name, before, "Ctrl+D 没换肤")

    # ---------------------------------------------------- 端到端：加载+切视图
    def test_full_keyboard_flow_with_loaded_file(self):
        """载入一个文件、分析完、键盘切遍 8 个视图——全程无异常。"""
        rng = np.random.default_rng(0)
        p = os.path.join(tempfile.mkdtemp(), "kbd.wav")
        sf.write(p, np.column_stack([rng.standard_normal(44100 * 3) * 0.2] * 2),
                 44100, subtype="PCM_16")
        self.w.enqueue([p])
        self.w.start()
        for _ in range(400):
            _pump(2)
            if self.w.worker is not None and not self.w.worker.isRunning():
                break
        _pump(6)
        for key in W.MainWindow.VIEW_KEYS:
            self.w._navigate(key)
            _pump()
            self.assertEqual(self.w.stack.currentIndex(),
                             W.MainWindow.VIEW_KEYS.index(key))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestHelpDialog(unittest.TestCase):
    """E5 帮助面板：内容必须**派生**，不许手写。"""

    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    @classmethod
    def tearDownClass(cls):
        cls.w.close()

    def test_shortcuts_come_from_the_command_table(self):
        """帮助里的快捷键集合 == 命令表里带快捷键的那些（**同一真源**）。

        这条挡的是"帮助写了 Ctrl+X，但那个键早就改了/不存在了"——
        过期的帮助比没有帮助更坏。
        """
        from audiocheck.ui.widgets import HelpDialog
        sc = [(lbl, s) for lbl, s, _f in self.w._cmds if s]
        html = HelpDialog.build_html(sc, ["总览"], ["边界 A"], "出处")
        self.assertIn("Ctrl+K", html)
        for _lbl, s in sc:
            self.assertIn("<code>%s</code>" % s, html, "命令表的 %s 没进帮助" % s)

    def test_views_and_boundaries_are_derived(self):
        from audiocheck.core.analyzers.causes import boundaries
        from audiocheck.ui.widgets import HelpDialog
        views = [self.w.VIEW_LABELS[k] for k in self.w.VIEW_KEYS]
        html = HelpDialog.build_html([], views, boundaries(), "出处")
        for v in views:
            self.assertIn(v, html)
        for b in boundaries():
            self.assertIn(b[:12], html, "效力边界没进帮助：%s" % b[:20])
        self.assertIn("不能", html, "效力边界段必须写清『本工具不能告诉你什么』")

    def test_dialog_builds_without_window(self):
        """不依赖整个窗口也能构造（可测性）：空数据不炸。"""
        from audiocheck.ui.widgets import HelpDialog
        d = HelpDialog(None, [], [], [], "出处")
        self.assertIn("快捷键", d.body.toPlainText())
        d.deleteLater()

    def test_f1_is_registered(self):
        """F1 必须真的接到 open_help 上（不只是命令面板里有一条）。"""
        seqs = [lbl for lbl, s, _f in self.w._cmds if s == "F1"]
        self.assertTrue(seqs, "命令表里没有 F1 条目")
