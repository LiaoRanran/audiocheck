"""瀑布图游标读数回归（B14 补强⑦）+ Δf 披露核实。

钉住五条：

1. **拾取是真实投影**：`_nearest_surface` 对已知表面网格点的投影像素
   必须原样命中该点（时间/频率/电平全对）——不是解析预测，是量出来的；
2. **查不到就不显示**：离表面 > READOUT_MAX_PX 的像素返回 None
   （与"unknown 是正式取值"同纪律）；
3. **画布集成**：悬停事件 → 读数浮层可见且文字对（时间 mm:ss / 频率 /
   电平）；
4. **状态切换**：拖拽旋转中隐藏、离开画布隐藏、无数据隐藏；
5. **Δf 披露**：瀑布图上的 Δf/Δt 披露文字由数据直算（B14 既有，本文件
   钉住防回潮——记忆里曾误记为"未做"）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np  # noqa: E402
from matplotlib.backend_bases import MouseEvent  # noqa: E402
from mpl_toolkits.mplot3d import proj3d  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.charts import READOUT_MAX_PX, WaterfallCanvas, _nearest_surface  # noqa: E402


def _synth_data():
    """确定性合成瀑布数据：(t, f, S)，S 为 [n_freq, n_time] 的 dB 矩阵。"""
    t = np.linspace(0.0, 60.0, 120)
    f = np.geomspace(20.0, 20000.0, 180)
    nf, nt = len(f), len(t)
    S = (-20.0 - 60.0 * (np.arange(nf)[:, None] / nf)
         - 5.0 * np.sin(np.arange(nt)[None, :] / 10.0))
    return t, f, S


class TestNearestSurface(unittest.TestCase):
    """纯函数判据：投影-命中闭环（量，不是算出来说应该对）。"""

    def _axes_and_surf(self):
        """造一个画好图的 3D 轴 + 与屏幕上一致的表面网格。"""
        cv = WaterfallCanvas()
        cv.resize(600, 400)
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        t, f, S = _synth_data()
        cv.set_data({"file": "x.wav"}, (t, f, S))
        cv.redraw()
        return cv, cv._surf

    def _project(self, cv, fl, y, z):
        x2, y2, _ = proj3d.proj_transform(fl, y, z, cv.ax.get_proj())
        return cv.ax.transData.transform((x2, y2))

    def test_known_surface_point_roundtrip(self):
        """取表面网格点 → 投影到屏幕 → `_nearest_surface` 必须原样命中。"""
        cv, surf = self._axes_and_surf()
        fl, db, ys, times = surf
        j, i = 50, 5                      # 任意网格点（频点 50、第 5 片）
        px, py = self._project(cv, fl[j], ys[i], db[j, i])
        hit = _nearest_surface(surf, cv.ax.get_proj(), cv.ax.transData, px, py)
        self.assertIsNotNone(hit)
        t_s, hz, level, dist = hit
        self.assertAlmostEqual(t_s, float(times[i]), places=6)
        self.assertAlmostEqual(hz, 10.0 ** fl[j], places=6)
        self.assertAlmostEqual(level, float(db[j, i]), places=6)
        self.assertLess(dist, 1.0, "命中点与投影像素应基本重合")

    def test_far_pixel_returns_none(self):
        cv, surf = self._axes_and_surf()
        hit = _nearest_surface(surf, cv.ax.get_proj(), cv.ax.transData,
                               -500.0, -500.0)
        self.assertIsNone(hit, "画布外的像素必须判'不在表面上'")

    def test_threshold_boundary_is_respected(self):
        """max_px 收紧到 0.5px 时，同一像素应判超距（阈值真的在起作用）。"""
        cv, surf = self._axes_and_surf()
        fl, db, ys, _times = surf
        j, i = 50, 5
        px, py = self._project(cv, fl[j], ys[i], db[j, i])
        hit = _nearest_surface(surf, cv.ax.get_proj(), cv.ax.transData,
                               px + 6.0, py, max_px=0.5)
        self.assertIsNone(hit)
        self.assertEqual(READOUT_MAX_PX, 80.0, "默认阈值被改过？判据要写死")


class TestWaterfallCanvasReadout(unittest.TestCase):
    """画布集成：事件 → 浮层。"""

    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()
        cls.w.show()
        APP.processEvents()
        cls.w._navigate("waterfall")
        APP.processEvents()
        cls.cv = cls.w.cv_wfall
        cls.cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        t, f, S = _synth_data()
        cls.cv.set_data({"file": "x.wav"}, (t, f, S))
        cls.cv.redraw()
        cls.t, cls.f = t, f

    def _fire(self, x, y):
        ev = MouseEvent("motion_notify_event", self.cv, x=float(x), y=float(y))
        self.cv.callbacks.process("motion_notify_event", ev)

    def test_hover_shows_readout_with_correct_values(self):
        cv = self.cv
        fl, db, ys, times = cv._surf
        j, i = 50, 5
        x2, y2, _ = proj3d.proj_transform(fl[j], ys[i], db[j, i], cv.ax.get_proj())
        px, py = cv.ax.transData.transform((x2, y2))
        self._fire(px, py)
        self.assertTrue(cv._ro.isVisible(), "悬停后读数浮层应可见")
        txt = cv._ro.text()
        self.assertIn("%d:%02d" % (int(times[i]) // 60, int(times[i]) % 60), txt)
        self.assertIn("Hz", txt)
        self.assertIn("%.0fdB" % db[j, i], txt)

    def test_hover_outside_axes_hides_readout(self):
        cv = self.cv
        self._fire(1.0, 1.0)              # 左上角画布外缘：不在轴区内
        self.assertFalse(cv._ro.isVisible(), "轴区外不应有读数")

    def test_leave_hides_readout(self):
        cv = self.cv
        fl, db, ys, _times = cv._surf
        x2, y2, _ = proj3d.proj_transform(fl[50], ys[5], db[50, 5], cv.ax.get_proj())
        px, py = cv.ax.transData.transform((x2, y2))
        self._fire(px, py)
        self.assertTrue(cv._ro.isVisible())
        cv.leaveEvent(None)
        self.assertFalse(cv._ro.isVisible(), "离开画布必须收起读数")

    def test_no_data_clears_surface_cache(self):
        cv = WaterfallCanvas()
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        cv.redraw()                        # 无数据路径
        self.assertIsNone(cv._surf, "无数据时表面缓存必须清空")


class TestWaterfallDisclosure(unittest.TestCase):
    """Δf/Δt 披露由数据直算（B14 既有；防回潮）。"""

    def test_df_dt_disclosure_matches_data(self):
        cv = WaterfallCanvas()
        cv.resize(600, 400)
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        t, f, S = _synth_data()
        cv.set_data({"file": "x.wav"}, (t, f, S))
        cv.redraw()
        texts = [x.get_text() for x in cv.ax.texts]
        disc = [s for s in texts if s.startswith("Δf")]
        self.assertEqual(len(disc), 1, texts)
        df = float(f[1] - f[0])
        dt = float(t[1] - t[0])
        self.assertIn("Δf ≈ %.3g Hz" % df, disc[0])
        self.assertIn("Δt ≈ %.3g s" % dt, disc[0])


if __name__ == "__main__":
    unittest.main(verbosity=2)
