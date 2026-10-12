"""主题一致性：全局 QSS 与 `self.theme` 必须始终指同一个主题。

## 为什么需要

`_restore()` 曾经直接 `self.theme = T.THEMES[n]` 而不调 `apply_theme()`。
`__init__` 的顺序是 `apply_theme(DARK)` → `_restore()`，于是：

    QApplication 的 QSS  = 深色（apply_theme(DARK) 设的）
    self.theme           = 浅色（_restore() 改的）

之后 `_cards()` 里 `StatCard(self.theme)` 拿**浅色**配色画在**深色**背景上：
数值渲成 `#141a26`（浅色主题的 text），背景 `#12151c` —— 几乎不可见。

用户直接可见，而且很难查：**界面能开、不报错、只是数字看不清**。
这份测试把"两个主题指示器必须一致"钉死，防止再有人直接赋值。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402


class TestThemeConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def test_theme_attr_matches_qss_after_restore(self):
        """`self.theme` 必须与真正生效的 QSS 同源。

        判据：把 `self.theme` 的 text 色拿去 QSS 里查，必须出现。
        早先 `_restore()` 直接赋值时会失败——QSS 是深色、self.theme 是浅色。
        """
        w = self.w
        qss = APP.styleSheet()
        self.assertIn(w.theme.p.text, qss,
                      "self.theme 的 text 色 %s 不在生效的 QSS 里 → "
                      "两个主题指示器不一致（_restore 直接赋值的老毛病）"
                      % w.theme.p.text)

    def test_apply_theme_is_the_only_way_to_switch(self):
        """切换主题必须同时改变量与 QSS —— 走 apply_theme 就自动满足。"""
        w = self.w
        for name in ("dark", "light"):
            w.apply_theme(T.THEMES[name])
            self.assertEqual(w.theme.name, name)
            self.assertIn(w.theme.p.text, APP.styleSheet(),
                          "切到 %s 后 QSS 没跟上" % name)
        w.apply_theme(T.DARK)          # 复位，避免影响其它测试

    def test_themes_are_distinguishable(self):
        """两个主题的 text 色必须不同，否则上面的判据失去意义。"""
        self.assertNotEqual(T.DARK.p.text, T.LIGHT.p.text)

    def test_state_card_colors_come_from_current_theme(self):
        """StatCard 的配色必须来自当前主题，不能是另一个主题的。"""
        from audiocheck.ui.widgets import StatCard
        w = self.w
        card = StatCard(w.theme)
        self.assertIn(w.theme.p.text, card.v.styleSheet())
        self.assertIn(w.theme.p.text_faint, card.k.styleSheet())

    def test_restore_does_not_bypass_apply_theme(self):
        """回归：`_restore` 里不能再出现裸的 `self.theme =` 赋值。"""
        import inspect
        src = inspect.getsource(W.MainWindow._restore)
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn("self.theme = T.THEMES", code,
                         "_restore 又直接给 self.theme 赋值了——必须走 apply_theme")
        self.assertIn("apply_theme", code)


if __name__ == "__main__":
    unittest.main(verbosity=2)
