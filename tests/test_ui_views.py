"""视图导航映射回归：VIEW_KEYS 必须与 QStackedWidget 的真实顺序一致。

为什么值得单独立一个测试文件：`VIEW_KEYS` 是一份**手写**的字符串列表，
而 `_stack()` 里 `addWidget` 的顺序是另一处事实。两者之间没有任何机制保证同步，
改一处忘另一处就会静默错位——实测症状：侧边栏点「元数据」显示的是对比画布，
真正的元数据页（两个 KVTable）永远到不了，而且**没有任何报错**。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])


class TestViewKeysMatchStack(unittest.TestCase):
    """VIEW_KEYS[i] 声称的视图，必须是 stack.widget(i) 实际的那个。"""

    @classmethod
    def setUpClass(cls):
        from audiocheck.ui import window as W
        cls.W = W
        cls.w = W.MainWindow()

    def test_every_key_maps_to_expected_widget(self):
        w, W = self.w, self.W
        # 期望的控件类型（按 key）
        from audiocheck.ui.charts import (
            BandCanvas,
            CompareCanvas,
            OverviewCanvas,
            SpectrogramCanvas,
            SpectrumCanvas,
            WaterfallCanvas,
        )
        expect = {
            "overview": None,            # 总览页是裸 QWidget，靠子控件区分
            "spectrum": SpectrumCanvas,
            "spectro": SpectrogramCanvas,
            "waterfall": WaterfallCanvas,
            "bands": BandCanvas,
            "compare": CompareCanvas,
            "batch": OverviewCanvas,
            "meta": None,                # 元数据页也是裸 QWidget
        }
        self.assertEqual(sorted(expect), sorted(W.MainWindow.VIEW_KEYS),
                         "期望集合与 VIEW_KEYS 不一致（有 key 增删？）")

        for i, key in enumerate(W.MainWindow.VIEW_KEYS):
            wd = w.stack.widget(i)
            if expect[key] is not None:
                self.assertIsInstance(
                    wd, expect[key],
                    "VIEW_KEYS[%d]=%r 应对应 %s，实际是 %s"
                    % (i, key, expect[key].__name__, type(wd).__name__))

    def test_meta_page_holds_the_two_tables(self):
        """元数据页必须真的装着 tb_meta / tb_metrics，否则「元数据」点了也白点。"""
        w, W = self.w, self.W
        page = w.stack.widget(W.MainWindow.VIEW_KEYS.index("meta"))
        tables = page.findChildren(QTableWidget)
        self.assertGreaterEqual(len(tables), 2,
                                "元数据页应含至少两个表格（容器标签 + 全部指标）")
        # 这两个表格必须就是 on_select 往里写数据的那两个
        self.assertTrue(any(t is w.tb_meta for t in tables))
        self.assertTrue(any(t is w.tb_metrics for t in tables))

    def test_overview_page_is_not_meta_page(self):
        """总览页与元数据页不能是同一个控件（曾因错位导致两者混淆）。"""
        w, W = self.w, self.W
        self.assertIsNot(w.stack.widget(W.MainWindow.VIEW_KEYS.index("overview")),
                         w.stack.widget(W.MainWindow.VIEW_KEYS.index("meta")))

    def test_navigate_reaches_the_right_widget(self):
        """_navigate(key) 之后，当前控件必须是对应那一个（端到端）。"""
        w, W = self.w, self.W
        from audiocheck.ui.charts import CompareCanvas
        for key in W.MainWindow.VIEW_KEYS:
            w._navigate(key)
            self.assertEqual(w.stack.currentIndex(), W.MainWindow.VIEW_KEYS.index(key))
        # 单独验一条最容易错的：compare 与 meta 不能互串
        w._navigate("compare")
        self.assertIsInstance(w.stack.currentWidget(), CompareCanvas)
        w._navigate("meta")
        self.assertIsNot(w.stack.currentWidget(), CompareCanvas)

    def test_empty_state_is_last_and_not_a_view_key(self):
        """空状态必须是最后一页，且不能被 VIEW_KEYS 覆盖到。"""
        w, W = self.w, self.W
        self.assertEqual(w._empty_idx, w.stack.count() - 1)
        self.assertLessEqual(len(W.MainWindow.VIEW_KEYS), w._empty_idx,
                             "VIEW_KEYS 不能占掉空状态那一页")


if __name__ == "__main__":
    unittest.main(verbosity=2)
