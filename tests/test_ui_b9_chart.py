"""B9.3 图表区判据：色图 D（14）+ 曲线族同源（15）+ 渲染不变（16）
+ 直接标注五条（9，B9.3b）+ 特效断言（17，B9.3b）。

## 三条纪律（B7 踩过、B9.1/B9.2 又各踩一次，这里一并落实）

1. **teardown**：建了画布必须拆（`close()` + `deleteLater` + 派发 DeferredDelete）。
2. **计数走 `pump`**：`processEvents()` 不派发 DeferredDelete，旧对象还在树上，
   计数会翻倍（B9.2 实测数到 36 张卡）。
3. **扫描器先去注释**：说明性注释里会引用被禁的写法（B9.2 的
   `hb.addLayout(right, 0)` 就活在我自己写的注释里）。三次踩坑，一律先去注释。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import charts as C  # noqa: E402
from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

T.init_fonts(APP)


def pump(ms: int = 0) -> None:
    """走事件循环（**必须派发 DeferredDelete**——B9.2 的计数坑）。"""
    import perf_probe
    perf_probe.pump(APP, ms / 1000.0 if ms else 0.05)
CHARTS = os.path.join(_SRC, "audiocheck", "ui", "charts.py")


def _code(path: str, start: str = "", end: str = "") -> str:
    """读源码并**去掉注释与 docstring**——扫描判据一律走这里（纪律 3）。"""
    s = open(path, encoding="utf-8").read()
    if start:
        s = s[s.index(start):]
    if end:
        s = s[:s.index(end)]
    s = re.sub(r'"""(?:.|\n)*?"""', '""', s)
    s = re.sub(r"'''(?:.|\n)*?'''", "''", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return "\n".join(ln.split("#")[0] for ln in s.splitlines())


def _oklab_dist(a: str, b: str) -> float:
    """两个 hex 色的 OKLab 距离（含明度）——"两两可区分"用这个量。"""
    la, aa, ba = P.srgb_to_oklab(a)
    lb, ab, bb = P.srgb_to_oklab(b)
    return ((la - lb) ** 2 + (aa - ab) ** 2 + (ba - bb) ** 2) ** 0.5


def _hue_gap(a: float, b: float) -> float:
    return abs((a - b + 180.0) % 360.0 - 180.0)


class TestRoseDiamondColormap(unittest.TestCase):
    """判据 14：色图 D 的三条断言 + "不许手抄 hex"。"""

    def test_three_assertions_l_monotonic(self):
        """① L 单调递增——数据编码的底线（否则"更亮=更强"不成立）。"""
        Ls = [r[1] for r in C.ROSE_DIAMOND_LCH]
        for a, b in zip(Ls, Ls[1:]):
            self.assertLess(a, b, "L 不是单调递增：%s" % Ls)

    def test_three_assertions_hue_u_shape_within_family_arc(self):
        """② 色相 U 形，全程在项目族弧内（310→10→345），**无第三族**。"""
        hs = [r[3] for r in C.ROSE_DIAMOND_LCH]
        arc = (310.0, 350.0, 10.0, 345.0, 320.0, 330.0, 340.0)
        for h in hs:
            self.assertLess(min(_hue_gap(h, a) for a in arc), 40.0,
                            "色相 %.0f° 不在项目族弧内——出现了第三色相族" % h)
        # U 形：低 t 偏冷紫（>300）、中段走到玫瑰（<20）、高 t 回冷（>300）
        self.assertGreater(hs[0], 300.0)
        self.assertLess(hs[4], 20.0, "中段没走到玫瑰（U 形的谷底）")
        self.assertGreater(hs[-1], 300.0)

    def test_three_assertions_chroma_is_bell_shaped(self):
        """③ 彩度钟形：峰值 C≥0.11 落在 t0.4–0.65，两端 ≤0.035。"""
        cs = [r[2] for r in C.ROSE_DIAMOND_LCH]
        ts = [r[0] for r in C.ROSE_DIAMOND_LCH]
        peak = max(range(len(cs)), key=lambda i: cs[i])
        self.assertGreaterEqual(cs[peak], 0.11)
        self.assertGreaterEqual(ts[peak], 0.4)
        self.assertLessEqual(ts[peak], 0.65)
        self.assertLessEqual(cs[0], 0.035, "低端不是低彩（钻石身体应无彩）")
        self.assertLessEqual(cs[-1], 0.035, "高端不是低彩（表面应是冷白）")

    def test_hue_interpolation_takes_the_shortest_arc(self):
        """**最短弧是色图工程的通用律，不是实现细节。**

        LCH 插值跨 0/360 环绕时必须走最短弧，否则 350°→10° 的"直线"会穿过
        180°（青绿）——恰好把 U 形拐点插成判据②禁止的第三色相族。
        不转最短弧的 D 会**悄悄违反自己的定义**，而且②能抓到它。
        """
        for t in (0.38, 0.44, 0.50, 0.56):
            _L, _c, h = C._rd_lch_at(t)
            self.assertLess(min(_hue_gap(h, a) for a in (310, 350, 10, 345)),
                            40.0, "t=%.2f 插出色相 %.0f°（最短弧没走对？）" % (t, h))

    def test_no_hand_copied_hex_in_the_ramp(self):
        """仓库里**不许出现手抄的九档 hex**：hex 是 `oklch_to_srgb` 的产物。"""
        seg = _code(CHARTS, "ROSE_DIAMOND_LCH", "def rose_diamond_cmap")
        hexes = re.findall(r"#[0-9a-fA-F]{6}", seg)
        self.assertEqual(hexes, [], "规范表附近出现了手抄 hex：%s" % hexes)

    def test_cmap_is_generated_from_the_table(self):
        cm = C.rd_cmap()
        self.assertEqual(cm.name, "rose_diamond")
        self.assertIs(getattr(cm, "audiocheck_source", None), C.ROSE_DIAMOND_LCH,
                      "色图没有把来源表挂上——判据无法追溯它是从表生成的")

    def test_srgb_to_oklch_roundtrip_of_the_ramp_endpoints(self):
        """表的**端点**经生成路径后仍是低彩：这是"钻石身体/表面"的视觉前提。"""
        for t in (0.0, 1.0):
            col = C.rd_color(t)
            self.assertLessEqual(P.srgb_to_oklch(col)[1], 0.05,
                                 "t=%.1f 的产物彩度偏高（应为近无彩）" % t)


class TestCurveFamilySameSource(unittest.TestCase):
    """判据 15：曲线色 = D 色图按主题分段的六次采样。"""

    def test_six_samples_per_theme_from_the_table(self):
        for dark, key in ((True, "dark"), (False, "light")):
            fam = C.curve_colors(dark)
            self.assertEqual(len(fam), 6)
            for col, t in zip(fam, C.CURVE_T[key]):
                self.assertEqual(col, C.rd_color(t),
                                 "曲线色不是从色轨采样（t=%.2f）——两套配色会漂" % t)

    def test_sample_positions_are_the_measured_ones(self):
        """位点是**实测反解**的，不是照抄规范的。

        规范给 dark `{0.32, …}`，但它在两条硬断言下不合格（t=0.32 对暗底
        只有 2.96:1）。提示词授权"不足则拉宽 t 间距，实测后记账"。
        逐 0.01 扫完得到可行域：对比 ≥3:1 要求 **t≥0.33**，色距 ≥0.05 要求
        **t≤0.92**——两头都顶住。唯一过关的候选是下面这组。
        **所以这条断言钉的是"那组实测过的数"，改它必须重跑两条硬断言。**
        """
        self.assertEqual(tuple(C.CURVE_T["dark"]),
                         (0.40, 0.50, 0.60, 0.70, 0.80, 0.92),
                         "dark 位点变了——重跑 `out/_b93a_t.py` 两条硬断言再改")
        self.assertEqual(tuple(C.CURVE_T["light"]),
                         (0.04, 0.13, 0.22, 0.31, 0.40, 0.50),
                         "light 位点变了——重跑 `out/_b93a_t.py` 两条硬断言再改")

    def test_dark_samples_stay_inside_the_feasible_window(self):
        """可行域的**两端**都要在域内：起点的对比够、终点的色距够。

        这条比"位点等于某组数"更能表达意图：改位点只要还落在域里就合法，
        出域会被**当场**抓住并指出是哪一头破了。
        """
        bg = T.THEMES["dark"].p.plot_bg
        fam = C.curve_colors(True)
        self.assertGreaterEqual(min(P.contrast(c, bg) for c in fam), 3.0,
                                "dark 段有曲线对底色的对比不足 3:1（起点太暗？）")
        worst = min(_oklab_dist(a, b)
                    for i, a in enumerate(fam) for b in fam[i + 1:])
        self.assertGreaterEqual(worst, 0.05,
                                "dark 段有两条曲线色距不足 0.05（终点太亮？）")

    def test_light_samples_reverse_the_constraint(self):
        """亮底的约束**方向相反**：深色才读得清（t 太大就压不住白底）。

        规范给的 light `{0.06,…,0.66}` 实测最低对比只有 **1.85:1**——
        末端 0.66 已经接近色轨的白区。这条断言钉住"亮段必须整体下移"。
        """
        bg = T.THEMES["light"].p.plot_bg
        fam = C.curve_colors(False)
        self.assertGreaterEqual(min(P.contrast(c, bg) for c in fam), 3.0,
                                "light 段有曲线压不住亮底（位点太靠 t 高端？）")
        for tt in C.CURVE_T["light"]:
            self.assertLessEqual(tt, 0.53,
                                 "light 位点 %.2f 超出可行域（亮底要求 t ≤0.53）" % tt)

    def test_pairwise_distinguishable(self):
        """硬断言①：任意两条曲线的 OKLab 距离 ≥0.05。"""
        for dark in (True, False):
            fam = C.curve_colors(dark)
            worst = min((_oklab_dist(a, b), a, b)
                        for i, a in enumerate(fam) for b in fam[i + 1:])
            self.assertGreaterEqual(worst[0], 0.05,
                                    "%s 的两条曲线太近：%.3f（%s vs %s）"
                                    % ("dark" if dark else "light", *worst))

    def test_theme_adapted_contrast(self):
        """硬断言②：每条曲线对当前主题 `plot_bg` 的对比 ≥3:1（非文本判据）。"""
        for th, dark in (("dark", True), ("light", False)):
            bg = T.THEMES[th].p.plot_bg
            for col in C.curve_colors(dark):
                self.assertGreaterEqual(P.contrast(col, bg), 3.0,
                                        "%s 主题下曲线 %s 对 plot_bg 不足 3:1" % (th, col))

    def test_cycle_for_follows_the_background(self):
        """`cycle_for` 按**底色明度**判主题——传错 pal 会拿到反的那一族。"""
        dk = T.THEMES["dark"].p
        lt = T.THEMES["light"].p
        self.assertEqual(C.cycle_for({"bg": dk.bg}), tuple(C.curve_colors(True)))
        self.assertEqual(C.cycle_for({"bg": lt.bg}), tuple(C.curve_colors(False)))


class TestRenderUnchanged(unittest.TestCase):
    """判据 16：换色图**不许顺手改数据映射**。"""

    def test_spectrogram_keeps_nearest_and_range(self):
        # ⚠ 不切段：`"def "` 当右锚会在 `def _spectrogram` **自己那一行**
        # 就命中，切出空串（第一版这么死的）；而类名锚依赖实现改名。
        # 整个 charts.py 只有一处 `imshow`，扫全文更稳也更诚实。
        code = _code(CHARTS)
        self.assertIn('interpolation="nearest"', code,
                      "频谱图的 interpolation 被动了——数据映射不是配色")
        self.assertRegex(code, r"vmin=-90\b")
        self.assertRegex(code, r"vmax=0\b")

    def test_no_default_matplotlib_colormaps_left(self):
        code = _code(CHARTS)
        for bad in ('"magma"', '"viridis"', '"plasma"', '"inferno"', '"jet"'):
            self.assertNotIn(bad, code, "还在用 matplotlib 默认色图 %s" % bad)

    def test_imshow_uses_the_project_colormap(self):
        code = _code(CHARTS)
        self.assertEqual(len(re.findall(r"\bcmap=rd_cmap\(\)", code)), 1,
                         "频谱图的色图不是 rd_cmap()")
        self.assertIn("cmap = rd_cmap()", code, "瀑布图的色图不是 rd_cmap()")


# ============================ B9.3b：直接标注 + 特效 ============================

def _cmp_canvas(n_files: int = 3):
    """造一个装好 n 条曲线的对比画布（**必须由调用方 close**）。"""
    import numpy as np

    from audiocheck.ui import charts as _C
    cv = _C.CompareCanvas()
    cv.resize(900, 520)
    # ⚠ 画布吃的是**调色板字典**（`p["accent"]`），不是 `Theme` 对象——
    # 窗口侧经 `T.plot_palette(t.p, t)` 转换后传下来。第一版直接传 `T.DARK`，
    # 报 `'Theme' object is not subscriptable`。
    cv.apply_theme(T.plot_palette(T.DARK.p, T.DARK))
    items = []
    for i in range(n_files):
        f = np.geomspace(20.0, 20000.0, 240)
        s = -40.0 - i * 6.0 - 20.0 * np.exp(-f / (3000.0 + i * 900.0))
        items.append(({"file": "曲目%d.flac" % i, "sr": 44100,
                       "cutoff_khz": 16.0 + i, "cutoff_hz": 16000.0 + i * 500},
                      np.stack([f, s], axis=1)))
    cv.set_data(items)
    cv.redraw()
    pump(40)
    return cv


def _drop_cv(cv) -> None:
    """画布也必须拆（纪律 1）：matplotlib 的 figure 会跟着 Qt 对象一起留。"""
    from PySide6.QtCore import QCoreApplication, QEvent
    cv.close()
    cv.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


def _texts(cv):
    """画布上所有可见的 `Text`（含末端标注）。"""
    out = []
    for ax in cv.fig.axes:
        out.extend(ax.texts)
    return out


class TestDirectLabels(unittest.TestCase):
    """判据 9：直接标注五条（存在 / 不重叠 / 不出界 / 降级可见 / pick 保留）。"""

    def setUp(self):
        self.cv = _cmp_canvas(3)

    def tearDown(self):
        _drop_cv(self.cv)

    def test_labels_exist_and_are_colored_like_their_curve(self):
        """① 存在：每条可见曲线的右端有一个同色的名字。"""
        cv = self.cv
        self.assertFalse(cv.direct_degraded, "3 条曲线不该降级")
        self.assertEqual(len(cv.end_labels), len(cv._lines),
                         "末端标注数与曲线数不一致")
        for lab, ln in zip(cv.end_labels, cv._lines):
            self.assertEqual(_rgba(lab.get_color()), _rgba(ln.get_color()),
                             "标注颜色与曲线不一致——读者没法把两者对上")
            self.assertTrue(lab.get_text().strip(), "标注是空的")

    def test_labels_do_not_overlap(self):
        """② 不重叠：任意两个标注的屏幕矩形不相交（确定性避让的结果）。"""
        cv = self.cv
        cv.fig.canvas.draw()          # 需要 renderer 才能算 window_extent
        boxes = [(lab.get_text(), lab.get_window_extent(cv.fig.canvas.get_renderer()))
                 for lab in cv.end_labels]
        for i, (na, ba) in enumerate(boxes):
            for nb, bb in boxes[i + 1:]:
                inter = ba.intersection(ba, bb) if hasattr(ba, "intersection") else None
                overlap = inter is not None and inter.width > 1 and inter.height > 1
                self.assertFalse(overlap, "标注 %r 与 %r 压在一起" % (na, nb))

    def test_labels_stay_inside_the_plot_area(self):
        """③ 不出界：标注矩形必须落在坐标区（axes）内。"""
        cv = self.cv
        cv.fig.canvas.draw()
        rend = cv.fig.canvas.get_renderer()
        ab = cv.ax.get_window_extent(rend)
        for lab in cv.end_labels:
            bb = lab.get_window_extent(rend)
            self.assertGreaterEqual(bb.x0, ab.x0 - 1, "标注越出左边界")
            self.assertLessEqual(bb.x1, ab.x1 + 1, "标注越出右边界（被裁=读不到）")
            self.assertGreaterEqual(bb.y0, ab.y0 - 1, "标注越出下边界")
            self.assertLessEqual(bb.y1, ab.y1 + 1, "标注越出上边界")

    def test_degradation_is_visible_not_silent(self):
        """④ 超量降级**可见**：曲线数 > MAX_DIRECT 时回图例，且置标志。

        "降级不许静默"是这一条的全部意义：用户必须能从**代码状态**看出
        "现在走的是图例而不是末端标注"，而不是靠肉眼猜。
        """
        cv = _cmp_canvas(10)
        try:
            self.assertGreater(len(cv._lines), cv.MAX_DIRECT)
            self.assertTrue(cv.direct_degraded, "超量了却没置降级标志")
            self.assertEqual(cv.end_labels, [], "降级了却还在画末端标注")
            leg = getattr(cv, "_legend", None)
            self.assertIsNotNone(leg, "降级回图例，但图例不见了——那就真丢信息了")
        finally:
            _drop_cv(cv)

    def test_pick_still_works_and_labels_are_clickable(self):
        """⑤ pick 保留：点标注能开关对应曲线（B8-H1 明确要求"至少标注可点"）。"""
        cv = self.cv
        cv.fig.canvas.draw()
        lab = cv.end_labels[0]
        ln = cv._lines[0]
        before = ln.get_visible()
        cv._toggle_by_artist(lab)        # 与 pick_event 同一条路径
        self.assertNotEqual(ln.get_visible(), before, "点标注没有开关曲线")
        cv._toggle_by_artist(lab)
        self.assertEqual(ln.get_visible(), before, "再点一次应回到原状")


class TestEffects(unittest.TestCase):
    """判据 17：特效三招里的两招可机器判（第三招靠截图人工复核）。"""

    def test_cutoff_wash_alpha_within_budget(self):
        """截止淡染 alpha ≤0.10——写进断言，不是"我们调得很淡"。"""
        self.assertLessEqual(C.CUTOFF_FADE_ALPHA, 0.10)
        cv = _cmp_canvas(2)
        try:
            washes = [a for a in cv.ax.patches
                      if a.get_alpha() is not None and a.get_alpha() <= C.CUTOFF_FADE_ALPHA]
            self.assertTrue(washes, "绘图区里没有截止淡染（或 alpha 超预算）")
        finally:
            _drop_cv(cv)

    def test_panel_has_a_one_pixel_rim_light(self):
        """绘图面板上缘 1px 亮边：**量像素**，不是查代码里有没有那行。"""
        self.assertEqual(C.PANEL_RIM_H, 1)
        cv = _cmp_canvas(1)
        try:
            img = cv.grab().toImage()
            self.assertGreater(img.width(), 8)
            rim = img.pixelColor(6, 0).name()
            below = img.pixelColor(6, 4).name()
            self.assertNotEqual(rim, below, "上缘没有亮边（第 0 行与第 4 行同色）")
        finally:
            _drop_cv(cv)


def _rgba(col):
    """把 matplotlib 颜色归一成 `#rrggbb`，便于跨类型比较。"""
    from matplotlib.colors import to_hex
    return to_hex(col)


if __name__ == "__main__":
    unittest.main(verbosity=2)
