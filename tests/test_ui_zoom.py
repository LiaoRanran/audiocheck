"""图表缩放 / 平移：纯函数数学 + 画布行为。

## 为什么值得钉死

缩放是"差距清单"里最大的一条（`docs/ui_compare.md` A1），而它**看起来最不起眼**：
一个符号写反、一个锚点用错，图照样画得出来，只是放大后视野跑偏、
或滚轮往上滚是缩小。这类问题肉眼要放大好几次才撞见，所以这里全部用数字钉：

1. **对数与线性不是同一套公式**（`zoom_span` 的 log 分支）
2. **锚点不动**：鼠标指着的那一格频率，缩放前后必须是同一个数
3. **边界贴边不回弹**（`clamp_span`）
4. **刻度跟着视野走**（`pick_ticks`）——"能放大"不等于"读得了数"
5. 画布层：滚轮/键盘/双击真的改变 xlim，且换文件会回全距
"""
from __future__ import annotations

import math
import os
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from audiocheck.ui import zoom as Z  # noqa: E402


class TestZoomMath(unittest.TestCase):
    def test_anchor_stays_put_on_log_axis(self):
        """锚点在缩放前后必须是同一个数——这是"鼠标指着哪里就放大哪里"。"""
        for anchor in (100.0, 1000.0, 8000.0):
            for factor in (Z.ZOOM_IN, Z.ZOOM_OUT, 2.0):
                lo, hi = Z.zoom_span(20, 20000, factor, anchor, log=True)
                self.assertAlmostEqual(lo, anchor * (20 / anchor) ** (1 / factor),
                                       places=6)
                self.assertAlmostEqual(hi, anchor * (20000 / anchor) ** (1 / factor),
                                       places=6)

    def test_zoom_in_shrinks_span(self):
        lo, hi = Z.zoom_span(20, 20000, Z.ZOOM_IN, 1000, log=True)
        self.assertLess(math.log10(hi) - math.log10(lo), math.log10(20000 / 20))

    def test_zoom_out_grows_span(self):
        lo, hi = Z.zoom_span(20, 20000, Z.ZOOM_OUT, 1000, log=True)
        self.assertGreater(math.log10(hi) - math.log10(lo), math.log10(20000 / 20))

    def test_log_and_linear_differ(self):
        """同一句话在对数/线性轴上必须给出不同结果（否则有一套是错的）。"""
        lin = Z.zoom_span(20, 20000, 2.0, 1000, log=False)
        lg = Z.zoom_span(20, 20000, 2.0, 1000, log=True)
        self.assertNotAlmostEqual(lin[0], lg[0], places=3)
        # 线性版把低频端几乎留在原地（510 Hz），对数版按比例收两端（141 Hz）。
        # 频率轴跨三个数量级，用线性公式放大就等于"只放大了高频"。
        self.assertGreater(lin[0], lg[0])

    def test_degenerate_inputs_return_input(self):
        self.assertEqual(Z.zoom_span(100, 100, 2.0, 100, log=True), (100, 100))
        self.assertEqual(Z.zoom_span(20, 20000, 0.0, 100, log=True), (20, 20000))
        self.assertEqual(Z.zoom_span(20, 20000, -1.0, 100, log=True), (20, 20000))

    def test_pan_moves_whole_span(self):
        lo, hi = Z.pan_span(100, 200, 0.5, log=True)
        self.assertAlmostEqual(hi / lo, 200 / 100)          # 跨度不变
        self.assertGreater(lo, 100)                          # 往大的方向走

    def test_pan_is_clamped(self):
        lo, hi = Z.pan_span(100, 200, 99.0, log=True)
        self.assertLessEqual((hi / lo) / (200 / 100), 10 ** Z.PAN_MAX + 1e-9)

    def test_clamp_sticks_to_edge_instead_of_bouncing(self):
        """超出范围时贴边：继续往外拖应该停住，而不是弹回中间。"""
        lo, hi = Z.clamp_span(20000, 40000, 20, 20000, log=True)
        self.assertAlmostEqual(hi, 20000, places=3)
        self.assertLess(lo, hi)

    def test_clamp_returns_full_range_when_span_too_big(self):
        self.assertEqual(Z.clamp_span(1, 1e9, 20, 20000, log=True), (20, 20000))

    def test_zoom_step_never_leaves_data_range(self):
        lo, hi = 20.0, 20000.0
        for step in (-6, -3, -1, 1, 3, 6):
            lo, hi = Z.zoom_step(lo, hi, 20.0, 20000.0, step, 1000.0, log=True)
            self.assertGreaterEqual(lo, 20.0 - 1e-6)
            self.assertLessEqual(hi, 20000.0 + 1e-6)

    def test_zoom_step_direction(self):
        inn = Z.zoom_step(20.0, 20000.0, 20.0, 20000.0, 1, 1000.0, log=True)
        out = Z.zoom_step(20.0, 20000.0, 20.0, 20000.0, -1, 1000.0, log=True)
        self.assertLess(math.log10(inn[1]) - math.log10(inn[0]),
                        math.log10(out[1]) - math.log10(out[0]))


class TestTicks(unittest.TestCase):
    """刻度必须跟着视野走——"能放大"不等于"读得了数"。"""

    def test_full_range_keeps_one_two_five(self):
        ticks = Z.pick_ticks(20, 20000, log=True)
        self.assertGreaterEqual(len(ticks), 8)
        self.assertIn(1000.0, ticks)
        self.assertIn(20000.0, ticks)

    def test_narrow_window_gets_linear_ticks(self):
        """放大到 100–200 Hz 时只剩 100/200 两个刻度是不够的。"""
        ticks = Z.pick_ticks(100, 200, log=True)
        self.assertGreaterEqual(len(ticks), 4)
        self.assertLessEqual(max(ticks), 200.0)
        self.assertGreaterEqual(min(ticks), 100.0)

    def test_ticks_inside_view(self):
        for lo, hi in ((20, 20000), (100, 200), (1000, 2000), (50, 5000)):
            for t in Z.pick_ticks(lo, hi, log=True):
                self.assertGreaterEqual(t, lo - 1e-6)
                self.assertLessEqual(t, hi + 1e-6)

    def test_linear_mode(self):
        ticks = Z.pick_ticks(0, 100, log=False)
        self.assertGreaterEqual(len(ticks), 4)
        self.assertEqual(ticks[0] % 20, 0)


class TestCanvasBehaviour(unittest.TestCase):
    """画布层：事件真的改变视野，且换文件会回全距。"""

    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])
        from audiocheck.ui import charts as C
        cls.C = C

    def _canvas(self):
        from audiocheck.ui import theme as T
        cv = self.C.SpectrumCanvas()
        cv.apply_theme(T.plot_palette(T.DARK.p))
        cv.r = {"sr": 44100, "cutoff_khz": 18.0, "cutoff_hz": 18000.0,
                "curve": [[f, -1.0 - (f / 20000.0) * 40.0] for f in
                          (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)]}
        cv.resize(640, 420)
        cv.show()
        cv.redraw()
        return cv

    def test_wheel_zoom_narrows_the_view(self):
        cv = self._canvas()
        full = cv.ax.get_xlim()
        cv._zoom(1, 1000.0)
        cv._view_timer.timeout.emit()          # 手动触发合并计时器，不等 28 ms
        zoomed = cv.ax.get_xlim()
        self.assertLess(math.log10(zoomed[1]) - math.log10(zoomed[0]),
                        math.log10(full[1]) - math.log10(full[0]))
        self.assertAlmostEqual(zoomed[0], 1000.0 * (20 / 1000.0) ** (1 / Z.ZOOM_IN), places=3)

    def test_keyboard_reset_restores_full_range(self):
        cv = self._canvas()
        full = cv.ax.get_xlim()
        cv._zoom(2, 1000.0)
        cv._view_timer.timeout.emit()
        self.assertNotAlmostEqual(cv.ax.get_xlim()[0], full[0], places=2)
        cv.reset_view()
        cv._view_timer.timeout.emit()
        self.assertAlmostEqual(cv.ax.get_xlim()[0], full[0], places=3)
        self.assertAlmostEqual(cv.ax.get_xlim()[1], full[1], places=3)

    def test_changing_file_resets_the_view(self):
        """换文件 = 换奈奎斯特：留着的缩放区会把新文件显示成空白低频段。"""
        cv = self._canvas()
        cv._zoom(2, 1000.0)
        cv._view_timer.timeout.emit()
        self.assertTrue(Z.is_zoomed(*cv._view_x, *cv._full_x, log=True))
        cv.set_data({"sr": 96000, "cutoff_khz": 40.0, "cutoff_hz": 40000.0,
                     "curve": [[f, -2.0] for f in (20, 1000, 20000, 40000)]})
        cv.redraw()
        self.assertEqual(cv._view_x, cv._full_x)
        self.assertAlmostEqual(cv.ax.get_xlim()[1], 48000.0, places=1)

    def test_compare_legend_toggle_hides_a_line(self):
        """点图例第二条 → 只关掉第二条（十条曲线叠着时最常用的操作）。"""
        from audiocheck.ui import theme as T
        cv = self.C.CompareCanvas()
        cv.apply_theme(T.plot_palette(T.DARK.p))
        cv.resize(640, 420)
        cv.show()
        items = []
        for k, sr in enumerate((44100, 44100, 44100)):
            items.append(({"file": "f%d.wav" % k, "sr": sr, "cutoff_khz": 18.0,
                           "cutoff_hz": 18000.0},
                          [[f, -1.0 - k] for f in (20, 1000, 5000, 10000, 18000)]))
        cv.set_data(items)
        cv.redraw()
        self.assertEqual(len(cv._lines), 3)
        self.assertEqual([ln.get_visible() for ln in cv._lines], [True, True, True])
        # 造一个落在**第二条图例文字**上的 pick 事件（鸭子类型，避免构造真 PickEvent）
        r = cv._legend.figure.canvas.get_renderer()
        bb = cv._legend.get_texts()[1].get_window_extent(renderer=r)

        class _Ev:
            pass

        class _Mouse:
            pass

        me = _Mouse()
        me.x, me.y = (bb.x0 + bb.x1) / 2, (bb.y0 + bb.y1) / 2
        me.inaxes = cv.ax
        ev = _Ev()
        ev.mouseevent = me
        cv._on_pick(ev)
        self.assertEqual([ln.get_visible() for ln in cv._lines], [True, False, True])
        # 再点一次应该开回来
        cv._on_pick(ev)
        self.assertEqual([ln.get_visible() for ln in cv._lines], [True, True, True])


if __name__ == "__main__":
    unittest.main(verbosity=2)
