"""时间定位视图回归（TimelineCanvas + window 接线）。

时间定位（C4.3）原本只在 CLI/JSON 里可见——内核产出了逐段时间序列，
GUI 却完全不画它，等于半交付。本文件钉住画布与接线的六条：

1. **VIEW_KEYS ↔ stack 映射**正确（与 test_ui_views 同手法）；
2. **数据来自结果不重读文件**：画布读 `r["timeline"]`（与 BandCanvas 同模式）；
3. **None 处断线**：lufs 缺口不跨点连线（"没测到"不许画成"测到了"）；
4. **问题段标注单一来源**：遮罩/红柱取内核清单
   `timeline_loudness_problems` / `timeline_problems`，画布不重造判据；
5. **空态如实**：无 timeline（浅模式/短文件）时显示 note 文本；
6. **0 dBFS 上限永远在量程内**（事实线；贴顶才看得出风险）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from PySide6.QtGui import QPaintEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from matplotlib.colors import to_rgb  # noqa: E402

from audiocheck.core.engine import analyze  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.charts import TimelineCanvas  # noqa: E402


def _pal(light=False):
    t = T.LIGHT if light else T.DARK
    return T.plot_palette(t.p, t)


def _row(start, end, lufs, tp, clip=0.0):
    """一段 timeline 行（字段与 core/analyzers/timeline.py 的 run() 产出一致）。"""
    return {"start": start, "end": end, "clip_pct": clip,
            "peak_dbfs": -6.0, "rms_dbfs": -9.0,
            "lufs": lufs, "true_peak_dbfs": tp}


class TestTimelineCanvas(unittest.TestCase):
    def setUp(self):
        self.cv = TimelineCanvas()
        self.cv.apply_theme(_pal())
        self.p = _pal()

    # ---------------------------------------------------------- 正常数据
    def test_normal_rows_draw_lines_and_bars(self):
        r = {"timeline": [_row(0, 10, -12.0, -1.0), _row(5, 15, -11.5, -0.8),
                          _row(10, 20, -12.3, -1.2)],
             "timeline_note": "时域逐段统计 + 逐段响度；逐段频谱未做"}
        self.cv.set_data(r)
        self.cv.redraw()
        # 上轴：LUFS 一条 + 真峰值一条 + 0 dBFS 参考线一条
        self.assertEqual(len(self.cv.ax1.lines), 3)
        # 下轴：三根削波柱
        self.assertEqual(len(self.cv.ax2.patches), 3)
        # 时间轴是 mm:ss 形式（%d:%02d）
        labels = [t.get_text() for t in self.cv.ax2.get_xticklabels()]
        self.assertTrue(any(":" in s for s in labels), labels)

    def test_ylim_always_includes_the_ceiling(self):
        """0 dBFS 永远在量程内——安静文件也要看得出离上限还有多远。"""
        r = {"timeline": [_row(0, 10, -30.0, -24.0)]}
        self.cv.set_data(r)
        self.cv.redraw()
        lo, hi = self.cv.ax1.get_ylim()
        self.assertLessEqual(lo, 0.0)
        self.assertGreaterEqual(hi, 0.0)

    # ---------------------------------------------------------- None 断线
    def test_none_lufs_breaks_the_line(self):
        """第 2 段 lufs=None ⇒ LUFS 断成两段（不跨缺口连线）。"""
        r = {"timeline": [_row(0, 10, -12.0, -1.0),
                          _row(5, 15, None, -2.0),      # 缺口
                          _row(10, 20, -11.0, -0.5)]}
        self.cv.set_data(r)
        self.cv.redraw()
        # ⚠ Line2D.get_color() 返回**传入时的原始规格**（hex 字符串），
        # 不是绘制时的 RGB 元组——必须先过 to_rgb 再比。
        accent, bad = to_rgb(self.p["accent"]), to_rgb(self.p["bad"])
        n_lufs = sum(1 for ln in self.cv.ax1.lines if to_rgb(ln.get_color()) == accent)
        n_tp = sum(1 for ln in self.cv.ax1.lines if to_rgb(ln.get_color()) == bad)
        self.assertEqual(n_lufs, 2, "LUFS 缺口处必须断线")
        self.assertEqual(n_tp, 1, "真峰值无缺口，一条线")

    def test_all_none_still_draws_ceiling(self):
        """两个序列全缺（全静音）⇒ 不崩，只剩参考线。"""
        r = {"timeline": [_row(0, 10, None, None)]}
        self.cv.set_data(r)
        self.cv.redraw()
        self.assertEqual(len(self.cv.ax1.lines), 1)   # 只有 0 dBFS 参考线

    # ---------------------------------------------------------- 问题段标注
    def test_problem_spans_and_bars_come_from_kernel_lists(self):
        """遮罩取 loudness_problems、红柱取 timeline_problems——不重造判据。"""
        r = {"timeline": [_row(0, 10, -12.0, 0.3, clip=2.0),      # 既顶限又削波
                          _row(5, 15, -12.0, -1.0, clip=0.0),
                          _row(10, 20, -12.0, -1.0, clip=0.0)],
             "timeline_loudness_problems": [{"start": 0.0, "end": 10.0,
                                             "true_peak_dbfs": 0.3,
                                             "text": "0:00–0:10 真峰值 0.3 dBFS"}],
             "timeline_problems": [{"start": 0.0, "end": 10.0, "clip_pct": 2.0,
                                    "text": "0:00–0:10 削波 2.000%"}]}
        self.cv.set_data(r)
        self.cv.redraw()
        # 上轴遮罩（axvspan 是 Polygon patch）
        self.assertGreaterEqual(len(self.cv.ax1.patches), 1)
        # 下轴红柱恰好一根（clip 问题段）
        n_bad = sum(1 for pt in self.cv.ax2.patches
                    if pt.get_facecolor()[:3] == to_rgb(self.p["bad"]))
        self.assertEqual(n_bad, 1)

    def test_clean_file_has_no_spans(self):
        """干净文件：无遮罩、无红柱（不许把正常段标成问题）。"""
        r = {"timeline": [_row(0, 10, -12.0, -1.0), _row(10, 20, -12.0, -1.0)]}
        self.cv.set_data(r)
        self.cv.redraw()
        self.assertEqual(len(self.cv.ax1.patches), 0)
        n_bad = sum(1 for pt in self.cv.ax2.patches
                    if pt.get_facecolor()[:3] == to_rgb(self.p["bad"]))
        self.assertEqual(n_bad, 0)

    # ---------------------------------------------------------- 空态
    def test_empty_shows_note_not_blank_plot(self):
        """无 timeline（浅模式/短文件）⇒ 显示 note 文本，不画曲线。"""
        r = {"timeline_note": "文件短于一个分段窗（10s），无法分段"}
        self.cv.set_data(r)
        self.cv.redraw()
        self.assertEqual(len(self.cv.ax1.lines), 0)
        self.assertTrue(self.cv.ax1.texts)
        self.assertIn("无法分段", self.cv.ax1.texts[0].get_text())

    def test_set_data_none_is_safe(self):
        self.cv.set_data(None)
        self.cv.redraw()
        self.assertEqual(len(self.cv.ax1.lines), 0)
        self.assertTrue(self.cv.ax1.texts)

    # ---------------------------------------------------------- 主题
    def test_light_theme_renders(self):
        cv = TimelineCanvas()
        cv.apply_theme(_pal(light=True))
        r = {"timeline": [_row(0, 10, -12.0, -1.0), _row(10, 20, -12.0, -1.0)]}
        cv.set_data(r)
        cv.redraw()
        self.assertEqual(len(cv.ax1.lines), 3)

    def test_theme_change_defers_canvas_redraw_then_completes(self):
        """换肤后画布重绘**延迟一帧**（P7 攻坚）：同步路径不重绘，
        paint 时用**新主题**重建——量图元颜色，不是"应该会画"。

        机制：`BaseCanvas.apply_theme`（有数据时）只置 `_stale` +
        `update()`；真正重建图元发生在下一帧的 `paintEvent`（_stale 钩子）。
        换肤同步重绘可见画布实测 ~190ms（频谱图重建全部图元）。

        ⚠ 两个坑（都是实测踩出来的，故本条如此写）：
          ① 离屏平台**不保证**通过事件循环投递 `update()` 的 paint（实测 6 轮 0 次，
             全量负载下更不稳）⇒ **显式调用 `paintEvent`**（钩子就在它开头）。
          ② **不能依赖 `MainWindow` 的初始配色**：`_restore()` 会从 QSettings 读上次
             主题，别的测试改过就变浅色，于是"深→浅"换肤根本没换色 ⇒ 偶发失败。
             故用**独立画布**并从**显式深色**出发（自检前提）。
        """
        cv = TimelineCanvas()
        cv.resize(720, 430)
        dark, light = _pal(light=False), _pal(light=True)
        cv.apply_theme(dark)
        cv.set_data({"timeline": [_row(0, 10, -12.0, -1.0), _row(10, 20, -12.0, -1.0)]})
        cv.redraw()                       # 首帧同步画（数据到达路径不变）
        old_accent = cv.ax1.lines[0].get_color()
        self.assertEqual(to_rgb(old_accent), to_rgb(dark["accent"]),
                         "前提不成立：首帧不是深色 accent")   # 自检，防口径漂移

        cv.apply_theme(light)
        self.assertTrue(cv._stale,
                        "换肤后画布应立即标脏（延迟重绘），而不是同步重建")

        # 完成那一帧：paintEvent 的 _stale 钩子 → redraw()（用新主题重建图元）。
        cv.paintEvent(QPaintEvent(cv.rect()))
        self.assertFalse(cv._stale, "paint 后画布应已重建")
        lu = [ln for ln in cv.ax1.lines if to_rgb(ln.get_color()) == to_rgb(old_accent)]
        self.assertEqual(lu, [], "LUFS 线仍是旧主题颜色：延迟重绘没生效")
        acc = [ln for ln in cv.ax1.lines if to_rgb(ln.get_color()) == to_rgb(light["accent"])]
        self.assertTrue(acc, "画完后没有新主题 accent 的线")
        cv.deleteLater()

    def test_theme_apply_without_data_only_stores_palette(self):
        """构造期 MainWindow 对每张画布 apply_theme——无数据时必须只存色板。"""
        cv = TimelineCanvas()
        cv.apply_theme(_pal())
        self.assertEqual(cv.p, self.p)
        self.assertFalse(cv._has_data())


class TestWindowWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def test_navigate_reaches_timeline_canvas(self):
        self.w._navigate("timeline")
        self.assertIsInstance(self.w.stack.currentWidget(), TimelineCanvas)

    def test_sidebar_has_timeline_item(self):
        # 侧边栏 key 与 VIEW_KEYS 一致（navigate 走 key）
        self.assertIn("timeline", W.MainWindow.VIEW_KEYS)
        self.assertEqual(W.MainWindow.VIEW_KEYS.index("timeline"),
                         self.w.stack.indexOf(self.w.cv_timeline))


def test_metric_rows_include_timeline_note(audio):
    """时间定位口径出现在指标页（tooltip 里给全文）。

    ⚠ 这条不能写成 unittest 方法：`audio` 是 pytest fixture，
    unittest.TestCase 的方法不吃 fixture 注入。
    """
    w = W.MainWindow()
    r = analyze(audio["cd_full"])     # 8s 文件 ⇒ "无法分段" 的 note
    rows = w._metric_rows(r)
    hit = [b for a, b in rows if a == "时间定位口径"]
    assert hit, rows
    assert "无法分段" in hit[0]


if __name__ == "__main__":
    unittest.main(verbosity=2)
