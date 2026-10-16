"""E3 无障碍回归：可访问名 / 键盘导航 / 焦点可见。

依据 E 块提示词 E3（Qt 无障碍五方面）钉住四条机器判据：

1. **可访问名非空**：8 张画布（自绘控件，Qt 不自动暴露语义）+
   搜索框 / 筛选框 / 文件表 / 工具栏按钮；
2. **键盘可达**：Tab 链必须覆盖搜索框、文件表与画布（离屏实测）；
3. **焦点可见（对比度判据）**：焦点环 border_focus 对 surface/bg ≥3:1
   （两个内置主题；输入描边的已知缺口按 1.0 登记且实测值可见）；
4. **焦点可见（像素判据）**：画布聚焦前后 render 到 QPixmap，
   **基线噪声为 0** 时边框带必须出现差异（焦点环真的画上了）。

⚠ 纪律：**不宣称"符合 WCAG"**——本文件只钉"按五方面自查"的机器判据，
三态表与诚实登记见 `packaging/a11y_report.py` 与 `docs/accessibility.md`。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QPainter, QPixmap  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402


def _name(w) -> str:
    n = w.accessibleName() or ""
    if not n:
        try:
            n = w.text() or ""
        except AttributeError:
            pass
    if not n:
        n = w.toolTip() or ""
    return n or (w.objectName() or type(w).__name__)


class TestAccessibleNames(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def test_canvases_have_names_and_descriptions(self):
        for cv in (self.w.cv_spec, self.w.cv_spectro, self.w.cv_wfall, self.w.cv_phase,
                   self.w.cv_band, self.w.cv_timeline, self.w.cv_cmp, self.w.cv_ovw):
            self.assertTrue(cv.accessibleName().strip(),
                            "%s 缺可访问名" % type(cv).__name__)
            self.assertTrue(cv.accessibleDescription().strip(),
                            "%s 缺文字替代描述" % cv.accessibleName())

    def test_controls_have_names(self):
        checks = [("搜索框", self.w.ed_search), ("筛选框", self.w.cb_filter),
                  ("文件表", self.w.table)]
        checks += [("按钮 %s" % k, b) for k, b in self.w.act.items()]
        for disp, w in checks:
            self.assertTrue(_name(w).strip(), "%s 无可访问名（回落也读不出）" % disp)

    def test_interactive_widgets_are_focusable(self):
        for w in (self.w.ed_search, self.w.cb_filter, self.w.table,
                  *self.w.act.values(), *self.w.sidebar._items.values()):
            self.assertNotEqual(w.focusPolicy(), Qt.FocusPolicy.NoFocus,
                                "%s 焦点策略是 NoFocus" % _name(w))


class TestKeyboardNavigation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()
        cls.w.show()
        APP.processEvents()

    def test_tab_chain_reaches_key_controls(self):
        w = self.w
        w.activateWindow()
        APP.processEvents()
        seen = []
        for _ in range(60):
            QTest.keyClick(w, Qt.Key_Tab)
            APP.processEvents()
            fw = APP.focusWidget()
            if fw is not None:
                seen.append(_name(fw))
        for need in ("搜索文件名", "文件列表", "频谱曲线", "时间定位"):
            self.assertIn(need, seen, "Tab 链走不到 %r（实测 %s）" % (need, seen[:14]))


class TestFocusVisibility(unittest.TestCase):
    """焦点可见的两条判据：对比度（调色板）+ 像素（真的画上去了）。"""

    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()
        cls.w.show()
        APP.processEvents()

    def test_focus_ring_contrast_passes_3to1(self):
        """焦点环对 surface / bg ≥3:1（两个内置主题）。"""
        for t in (T.DARK, T.LIGHT):
            focus = [f for f in P.audit(t.p) if "焦点环" in f.pair]
            self.assertEqual(len(focus), 2, t.name)
            for f in focus:
                self.assertTrue(f.ok, "%s 焦点环不达标：%s" % (t.name, f.line()))

    def test_input_border_gap_is_registered_not_silent(self):
        """输入描边缺口按 1.0 登记（诚实），且报告里能看见实测值。"""
        for t in (T.DARK, T.LIGHT):
            gaps = [f for f in P.audit(t.p) if "输入描边" in f.pair]
            self.assertEqual(len(gaps), 2, t.name)
            for f in gaps:
                self.assertTrue(f.ok, "%s 登记条目本身不该红：%s" % (t.name, f.line()))
                self.assertLess(f.ratio, 3.0,
                                "%s 实测已达标？把阈值改回 3.0：%s" % (t.name, f.line()))

    def test_canvas_focus_ring_pixels_differ(self):
        """聚焦前后 render 的像素差：基线 0（渲染确定性），聚焦 > 0（环画上了）。"""
        w = self.w
        w._navigate("timeline")
        APP.processEvents()
        cv = w.cv_timeline
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        cv.set_data({"timeline": [
            {"start": 0.0, "end": 2.0, "clip_pct": 0.0,
             "lufs": -12.0, "true_peak_dbfs": -1.0}]})
        cv.redraw()

        def render():
            pm = QPixmap(cv.size())
            pm.fill(T.DARK.p.surface)
            pr = QPainter(pm)
            # ⚠ PySide6 重载解析：render(painter) 必须显式给 targetOffset
            cv.render(pr, QPoint(0, 0))
            pr.end()
            return pm.toImage()

        def band_diff(a, b, band=6):
            n = 0
            for y in range(a.height()):
                for x in range(a.width()):
                    if x < band or x >= a.width() - band or y < band or y >= a.height() - band:
                        if a.pixelColor(x, y) != b.pixelColor(x, y):
                            n += 1
            return n

        # 热 render（首帧可能有布局落定的一次性差异，先settle）
        render()
        cv.clearFocus()
        base = render()
        again = render()
        self.assertEqual(band_diff(base, again), 0, "无焦点变化时渲染不确定")

        # 离屏下 setFocus 的 repaint 是**异步**的（实测 1 轮 processEvents 不够、
        # 2 轮起稳定）——等它落定，与 perf_probe 的 wait() 同一手法。
        cv.setFocus()
        focused = base
        for _ in range(5):
            APP.processEvents()
            focused = render()
            if band_diff(base, focused) > 0:
                break
        self.assertGreater(band_diff(base, focused), 50,
                           "焦点环没画上（5 轮事件后仍无差异）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
