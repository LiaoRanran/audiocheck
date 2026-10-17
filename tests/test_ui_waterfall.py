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

import math
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import numpy as np  # noqa: E402
from matplotlib.backend_bases import MouseEvent  # noqa: E402
from mpl_toolkits.mplot3d import proj3d  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.charts import (  # noqa: E402
    READOUT_MAX_PX,
    ZOOM_MAX,
    ZOOM_MIN,
    WaterfallCanvas,
    _db_ticks,
    _freq_axis_ticks,
    _freq_major_pos_labels,
    _hz_label,
    _nearest_surface,
    _peak_hold_cols,
    _zoom_after,
)


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


class TestWaterfallAxes(unittest.TestCase):
    """数轴契约（2026-10-17 修）：dB 刻度**单调**、频轴与频谱图**同口径**、滚轮缩放可测。

    两个缺陷都是实测到的（`out/_axis_probe.py`）：
      * z 轴旧写法 `[round(vmin),−60,−40,−20,0]` 在 vmin ∈ (−60,0) 时**非单调**，
        低于 zlim 底的刻度被画到框外——而 vmin ∈ (−60,0) 正是**响度战**素材；
      * x 轴只标 10/100/1k/10k，比频谱图少 20/50/200/500/2k/5k/20k。
    """

    def test_db_ticks_monotonic_dedup_bounded(self):
        for vmin in (-90.0, -76.5, -57.5, -38.5, -20.5, -10.0, -1.0):
            zt = _db_ticks(vmin)
            self.assertEqual(zt, sorted(zt), "刻度必须升序：vmin=%s → %s" % (vmin, zt))
            self.assertEqual(len(zt), len(set(zt)), "刻度不许重复：%s" % zt)
            self.assertGreaterEqual(min(zt), vmin - 1e-9, "刻度不许低于 zlim 底：%s" % zt)
            self.assertIn(0.0, zt, "必须含 0：%s" % zt)
            self.assertLessEqual(max(zt), 0.0)

    def test_db_ticks_regression_loud_material(self):
        """响度战素材（vmin 落在 (−60,0)）不再出现非单调刻度。"""
        old = [round(-57.5), -60, -40, -20, 0]
        self.assertNotEqual(old, sorted(old), "旧写法确实是坏的（回归前提）")
        new = _db_ticks(-57.5)
        self.assertEqual(new, sorted(new))
        self.assertGreaterEqual(min(new), -57.5)

    def test_freq_axis_matches_spectrogram(self):
        fmax = 22050.0
        pos, lab = _freq_major_pos_labels(fmax)
        majors, _ = _freq_axis_ticks(fmax)
        self.assertEqual(len(pos), len(majors))
        for p, m in zip(pos, majors):
            self.assertAlmostEqual(p, math.log10(m), places=9)
        self.assertEqual(lab[-1], _hz_label(majors[-1], unit=True))
        self.assertTrue(all("Hz" not in s for s in lab[:-1]), "只有最大刻度带单位")
        for need in (20, 50, 200, 500, 2000, 5000, 20000):   # 旧写法缺的这些
            self.assertIn(math.log10(need), pos)

    def test_zoom_after_pure(self):
        self.assertGreater(_zoom_after(1.0, 1), 1.0)
        self.assertLess(_zoom_after(1.0, -1), 1.0)
        self.assertEqual(_zoom_after(1.0, 0), 1.0)
        self.assertEqual(_zoom_after(ZOOM_MAX, 10), ZOOM_MAX, "上限必须钳住")
        self.assertEqual(_zoom_after(ZOOM_MIN, -10), ZOOM_MIN, "下限必须钳住")

    def _drawn(self):
        cv = WaterfallCanvas()
        cv.resize(600, 400)
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        t, f, S = _synth_data()
        cv.set_data({"file": "x.wav"}, (t, f, S))
        cv.redraw()
        return cv, t, f

    def test_canvas_zticks_are_monotonic(self):
        cv, _t, _f = self._drawn()
        zt = list(cv.ax.get_zticks())
        self.assertEqual(zt, sorted(zt), "画布上的 z 刻度必须升序：%s" % zt)
        self.assertEqual(len(zt), len(set(zt)))
        self.assertGreaterEqual(min(zt), cv._vmin - 1e-9)

    def test_canvas_xticks_match_freq_majors(self):
        cv, _t, f = self._drawn()
        majors, _ = _freq_axis_ticks(float(f[-1]))
        self.assertEqual(len(cv.ax.get_xticks()), len(majors))

    def test_scroll_zoom_changes_and_resets(self):
        cv, _t, _f = self._drawn()

        class _Ev:
            inaxes = cv.ax
            step = 2

        z0 = cv._zoom
        cv._on_scroll(_Ev())
        self.assertGreater(cv._zoom, z0, "滚轮向前应放大")
        cv.reset_view()
        self.assertEqual(cv._zoom, cv._zoom0, "复位应回到初始 zoom")

    # ---- 时间峰值保持（2026-10-17 修）：瞬态不许因抽样而丢 ----
    def test_peak_hold_cols_keeps_transient(self):
        db = np.full((3, 10), -40.0)
        db[:, 5] = 0.0                        # 列 5 = 瞬态；step=2 ⇒ 抽样列是 0,2,4,6,8
        t = np.arange(10.0)
        db2, t2 = _peak_hold_cols(db, t, 2)
        self.assertEqual(db2.shape[1], 5)
        self.assertEqual(db2[:, 2].max(), 0.0, "列 5 落在第 2 组（列 4–5），突发必须保留")
        self.assertEqual(db2.max(), 0.0, "峰值保持后整张图仍含突发")
        self.assertAlmostEqual(t2[2], 4.5, msg="代表时间应取组内均值")

    def test_canvas_keeps_transient_in_dropped_column(self):
        """抽样的**奇数列**上的突发，必须仍出现在瀑布表面上（旧实现整条丢掉）。"""
        cv = WaterfallCanvas()
        cv.resize(600, 400)
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        t = np.linspace(0.0, 60.0, 120)
        f = np.geomspace(20.0, 20000.0, 180)
        S = np.full((len(f), len(t)), -20.0)
        S[:, 5] = 20.0                        # +40dB 突发，落在奇数（未抽样）列
        cv.set_data({"file": "x.wav"}, (t, f, S))
        cv.redraw()
        _lf, dbg, _ys, _tg = cv._surf
        self.assertGreater(dbg.max(), -10.0,
                           "峰值保持后突发应接近 0 dB（旧实现只有约 −40 dB）")


if __name__ == "__main__":
    unittest.main(verbosity=2)
