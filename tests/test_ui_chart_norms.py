"""B7.2 字号令牌化 + B7.3 图表规范。

## 这一份最要紧的一条：**令牌要有落点，不只是"表里有一项"**

`Type.chart_tick` 加进表里、`charts.py` 里也不再出现数字字面量——
这两件事都做到了，**仍然可能什么都没变**：

量这一步时我踩过一次，值得写下来。第一版探针用 `cv.draw()` 而不是
`cv.redraw()`：`FigureCanvasQTAgg.draw()` 只渲染当前 figure，而
`_ax_style()`（设字号的地方）是在 `redraw()` 的构建路径里才被调用的。
于是探针量的是一个**从没被样式化过的空 figure**，改前改后都读到
matplotlib 的默认值 10.0——差点据此得出"字号变大了但没有代价"的结论。

所以 `test_chart_tick_reaches_the_axes` 是**画完之后把字号读回来**，
对着真东西断言。这类"看起来接上了"的失败在本项目已出现过三次
（QSS 多族回退、`line-height`、`font-variant-numeric`）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import charts as C  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

T.init_fonts(APP)

UI = os.path.join(_SRC, "audiocheck", "ui")
#: 扫字号的文件：图表与控件（主题里的字号本来就来自 Type 表）
SCANNED = ("charts.py", "widgets.py")


def _code(fn: str) -> list[tuple[int, str]]:
    txt = open(os.path.join(UI, fn), encoding="utf-8").read()
    txt = re.sub(r'"""(?:.|\n)*?"""', lambda m: re.sub(r"[^\n]", " ", m.group(0)), txt)
    out = []
    for i, line in enumerate(txt.splitlines(), 1):
        j = line.find("#")
        out.append((i, line if j < 0 else line[:j]))
    return out


def _type_values() -> set[float]:
    """`Type` 表里的全部字号——**这是"表内"的唯一判据来源**。"""
    from dataclasses import fields
    return {getattr(T.Type(), f.name) for f in fields(T.Type())}


class TestFontScaleScan(unittest.TestCase):
    """判据 1：图表/控件里出现 Type 表外的字号字面量即失败。"""

    def test_no_font_size_literals_outside_the_table(self):
        allowed = _type_values()
        bad = []
        for fn in SCANNED:
            for i, line in _code(fn):
                for m in re.finditer(r"(?:fontsize|labelsize|font-size)\s*[:=]\s*([\d.]+)",
                                     line):
                    if float(m.group(1)) not in allowed:
                        bad.append("%s:%d  %s" % (fn, i, line.strip()[:76]))
        self.assertEqual(bad, [], "出现了 Type 表外的字号字面量：\n" + "\n".join(bad))

    def test_the_scan_would_catch_a_violation(self):
        """**判据本身要被判据测**：造一个违规样本，确认扫描抓得到。

        这条挡的是"扫描写错了、于是永远绿"。本项目在 `test_ui_grid` 上
        吃过一次同类问题：规则写得太松，一个违规都抓不到。
        """
        allowed = _type_values()
        sample = 'ax.text(0, 0, "x", fontsize=7.8)'
        hit = [m.group(1) for m in
               re.finditer(r"(?:fontsize|labelsize|font-size)\s*[:=]\s*([\d.]+)", sample)]
        self.assertTrue(hit, "扫描规则匹配不到样例句")
        self.assertNotIn(float(hit[0]), allowed, "7.8 竟然在 Type 表里？那这条判据失效")

    def test_chart_tick_is_the_only_below_micro_tier(self):
        """新增档只有一档（`chart_tick`），且它确实小于 `micro`。

        "表外字号 7 种降到 0 种"的做法是**合并到一档**，不是每档补一个令牌——
        逐个补令牌等于把散点搬进表里，秩序还是没建立。
        """
        ty = T.Type()
        below = {f: getattr(ty, f) for f in ("micro", "caption") if getattr(ty, f) < 10}
        self.assertEqual(below, {}, "表里还有低于 10 的非图表档")
        self.assertLess(ty.chart_tick, ty.micro)


class TestChartTickHasLanding(unittest.TestCase):
    """判据 2：`chart_tick` 被图表真的用上（**画完再读回来**）。"""

    @classmethod
    def setUpClass(cls):
        import perf_probe

        from audiocheck.core.engine import analyze as analyze_file
        d = os.path.join(_ROOT, "out", "memprobe")
        os.makedirs(d, exist_ok=True)
        cls.r = analyze_file(perf_probe.make_wav(os.path.join(d, "b72_test.wav")))
        cls.pp = T.plot_palette(T.DARK.p, T.DARK)
        cls._made: list = []

    @classmethod
    def tearDownClass(cls):
        """**必须把画布拆掉**——又踩了一次"建了不拆"的坑。

        症状与 B7.1 那次一模一样：后面 `test_ui_material` 的
        "连发 8 条通知必须全部回收"失败（8 != 0，`QTimer.singleShot` 不按时触发），
        而它单独跑是绿的。定位方式也一样：把本文件的类逐个和后一个文件配对跑，
        发现只有带**真画布**的那个类会污染（另一段也调了 `analyze_file`，却干净）
        —— 所以不是"分析过"，是**画布留着没销毁**。
        """
        from PySide6.QtCore import QCoreApplication, QEvent
        for cv in cls._made:
            cv.setParent(None)
            cv.deleteLater()
        cls._made = []
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def _ticks(self, cv, attr="ax"):
        self._made.append(cv)
        cv.apply_theme(self.pp)
        cv.resize(880, 420)
        cv.set_data([self.r] if attr == "axh" else self.r)
        cv.redraw()                     # ← 不是 draw()！见本文件开头的说明
        return getattr(cv, attr)

    def test_chart_tick_reaches_the_axes(self):
        want = T.Type().chart_tick
        for name, cv, attr in (("频谱曲线", C.SpectrumCanvas(), "ax"),
                               ("频段能量", C.BandCanvas(), "ax"),
                               ("总览", C.OverviewCanvas(), "axh")):
            ax = self._ticks(cv, attr)
            got = {round(t.get_fontsize(), 1) for t in ax.get_xticklabels()
                   if t.get_text()}
            self.assertEqual(got, {want},
                             "%s 的刻度字号是 %s，不是令牌值 %.1f" % (name, got, want))

    def test_plot_palette_carries_the_type_scale(self):
        """画布拿到的字号来自 `Type` 表——**同一条通道**，不是另抄一份。"""
        pp = T.plot_palette(T.DARK.p, T.DARK)
        ty = T.DARK.type
        self.assertEqual(pp["fs_tick"], ty.chart_tick)
        self.assertEqual(pp["fs_micro"], ty.micro)
        self.assertEqual(pp["fs_caption"], ty.caption)
        self.assertEqual(pp["fs_hero"], ty.heading)

    def test_plot_palette_without_a_theme_still_uses_the_table(self):
        """不给主题时退回 `Type()` 的默认值——**仍然来自同一张表**。

        这条挡的是"省略参数就退回一套私有数字"：那种写法会让
        "字号只来自 Type 表"在两条路径上说法不一致。
        """
        pp = T.plot_palette(T.DARK.p)
        self.assertEqual(pp["fs_tick"], T.Type().chart_tick)


class TestGridContrastUntouched(unittest.TestCase):
    """判据 10：网格线对比**不许被"顺手加强"**（它已达标）。"""

    def test_grid_contrast_stays_below_tufte_limit(self):
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            r = T.PLOT_GRID_CONTRAST_MAX if hasattr(T, "PLOT_GRID_CONTRAST_MAX") else 1.5
            got = P_contrast(pal.grid, pal.plot_bg)
            self.assertLessEqual(got, r,
                                 "%s 的网格线对比 %.2f:1 超过 %.1f（加强就是倒退）"
                                 % (mode, got, r))

    def test_grid_is_a_real_token_not_a_hardcoded_color(self):
        self.assertTrue(hasattr(T.DARK.p, "grid"))


def P_contrast(a, b):
    from audiocheck.ui.palette import contrast
    return contrast(a, b)


class TestInformativeNonDataInk(unittest.TestCase):
    """B7.3 第 4 条：**信息性非数据墨必须保留**（与 Tufte 相反的本项目要求）。

    要被"保留"的东西有具体形状，所以可以断言它们还在：
      * 频段分界与 Bark 带（`参考线`）——判定"低频糊没糊"要靠它；
      * 单位（Hz / dB / kHz）——没有单位的读数是废数；
      * 效力边界（免责声明 / `advice`）——这是宪法 3.3 的要求。

    照 Tufte 字面把参考线删掉，等于把判据的一部分删掉。
    """

    def test_reference_lines_and_units_still_drawn(self):
        src = open(os.path.join(UI, "charts.py"), encoding="utf-8").read()
        for needle, why in (
                ("axvline", "频段分界/Bark 参考线"),
                ("xlabel", "轴单位"),
                ("Hz", "频率单位"),
                ("dB", "电平单位"),
                ("kHz", "截止单位"),
        ):
            self.assertIn(needle, src, "图表里少了 %s（%s）" % (needle, why))

    def test_disclaimer_path_is_alive(self):
        """效力边界文字：GUI 显示 advice、CLI/report 有免责声明。"""
        w = open(os.path.join(UI, "window.py"), encoding="utf-8").read()
        self.assertIn("advice", w, "界面不再显示效力边界（advice）")
        self.assertIn("lbl_advice", w)


class TestDataInkCalibration(unittest.TestCase):
    """B7.3 第 2 条：data-ink ratio **先标定，不先定阈值**。

    口径：把画布渲染到缓冲，在绘图区内数像素——
      * **总墨** = 与 `plot_bg` 明显不同的像素（网格/参考线/文字/数据都算）；
      * **数据墨** = 落在数据系列颜色附近的像素（曲线、柱、标记）。

    本轮只**记录**这个数（进 CHANGELOG），断言仅限"两侧都非空"这种
    逻辑上必须成立的事——按提示词要求，阈值要等标定之后再定。
    """

    @classmethod
    def setUpClass(cls):
        import numpy as np
        import perf_probe

        from audiocheck.core.engine import analyze as analyze_file
        cls.np = np
        d = os.path.join(_ROOT, "out", "memprobe")
        os.makedirs(d, exist_ok=True)
        cls.r = analyze_file(perf_probe.make_wav(os.path.join(d, "b73_ink.wav")))

    def _ratio(self, cv):
        np = self.np
        from audiocheck.ui.palette import parse_hex
        cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
        cv.resize(880, 420)
        cv.set_data(self.r)
        cv.redraw()
        ax = cv.ax
        fig = cv.figure
        fig.canvas.draw()
        buf = np.asarray(fig.canvas.buffer_rgba())[:, :, :3].astype(int)
        h, w = buf.shape[:2]
        b = ax.get_position()
        y0, y1 = int((1 - b.y1) * h), int((1 - b.y0) * h)
        x0, x1 = int(b.x0 * w), int(b.x1 * w)
        sub = buf[y0:y1, x0:x1]
        bg = np.array(parse_hex(T.DARK.p.plot_bg)) * 255.0
        ink = (np.abs(sub - bg).sum(axis=2) > 18)
        total = int(ink.sum())
        if not total:
            return 0.0, 0
        # 数据系列颜色：主色 / 次强调 / 装饰阶 / 判定色
        cols = [T.DARK.p.primary, T.DARK.p.accent2, *T.DARK.p.deco,
                T.DARK.p.success, T.DARK.p.warning, T.DARK.p.danger]
        mask = np.zeros(sub.shape[:2], dtype=bool)
        for c in cols:
            rgb = np.array(parse_hex(c)) * 255.0
            mask |= (np.abs(sub - rgb).sum(axis=2) < 40)
        return float((mask & ink).sum()) / total, total

    def test_data_ink_is_present(self):
        """只断言"两侧都非空"这种逻辑上必须成立的事。

        ⚠ **标定结果与它推翻的东西**：频谱曲线的 data-ink ratio 实测只有
        **1.4%**（曲线约 1600 像素 / 绘图区总墨约 11.7 万像素）。
        第一版我在这条里写了 `> 2%` 的门槛，然后它就红了——那正是
        "**先定阈值再标定**"的错误动作，而 B7.3 第 2 条明确禁止这么做。

        标定之后才看清：这里的低比值**不是缺陷**。绘图区里 98% 的墨是
        **Bark 临界频带底纹与网格**——而 B7.3 第 4 条要求它们必须保留
        （判定"低频糊没糊"要靠在图上比对频带，那是判据的一部分，不是装饰）。
        所以本图**不设 Tufte 式的高比值目标**：数据墨本来就该是一条细线，
        非数据墨在这里承载信息。这一条就是"先量后定"的产出。
        """
        for name, cv in (("频谱曲线", C.SpectrumCanvas()),
                         ("频段能量", C.BandCanvas())):
            ratio, total = self._ratio(cv)
            self.assertGreater(total, 0, "%s 绘图区里没有墨——图是空的" % name)
            self.assertGreater(ratio, 0.002,
                               "%s 的数据墨只占 %.2f%%——曲线几乎看不见" % (name, ratio * 100))
            self.assertLess(ratio, 0.98,
                            "%s 的数据墨占了 %.1f%%——那说明网格/文字没被算进总墨"
                            % (name, ratio * 100))
            print("\n  [标定] %s data-ink ratio = %.2f%%（总墨 %d 像素）"
                  % (name, ratio * 100, total))


if __name__ == "__main__":
    unittest.main(verbosity=2)
