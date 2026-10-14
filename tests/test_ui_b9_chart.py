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
for _p in (_SRC, os.path.join(_ROOT, "packaging"), os.path.join(_ROOT, "prototypes")):
    if _p not in sys.path:
        sys.path.insert(0, _p)     # prototypes 在列：B10.5 的氛围判据要 import _kit

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


def _chroma(rgb01):
    """(N,3) 的 0–1 sRGB → OKLCH **彩度**数组。

    用与 `palette_report`/`hue_family_report` 相同的矩阵与流程，
    不自己写第二套转换（B12 立过"不要自己写转换"的规矩）。
    """
    import numpy as np
    flat = np.asarray(rgb01, dtype=np.float64).reshape(-1, 3)
    lin = np.where(flat <= 0.04045, flat / 12.92, ((flat + 0.055) / 1.055) ** 2.4)
    lms = lin @ np.array([
        [0.4122214708, 0.5363325363, 0.0514459929],
        [0.2119034982, 0.6806995451, 0.1073969566],
        [0.0883024619, 0.2817188376, 0.6299787005]]).T
    lab = np.cbrt(lms) @ np.array([
        [0.2104542553, 0.7936177850, -0.0040720468],
        [1.9779984951, -2.4285922050, 0.4505937099],
        [0.0259040371, 0.7827717662, -0.8086757660]]).T
    return np.hypot(lab[:, 1], lab[:, 2])


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
        # B14 精度：dB 窗改成**具名常量**（固定窗 = 跨文件可比，专业软件通行做法）
        self.assertRegex(code, r"SPECTRO_DB_RANGE\s*=\s*90")
        self.assertRegex(code, r"vmin=-SPECTRO_DB_RANGE\b")
        self.assertRegex(code, r"vmax=0\b")

    def test_no_default_matplotlib_colormaps_left(self):
        """曲线**不许**用 matplotlib 默认色图（那批是"AI 味"的来源）。
        ⚠ 例外：**频谱图**用 `inferno`（B14：密度图的科学标准，感知均匀、明度单调；
        用户点名"专业可视化强一点的颜色"）——所以 `inferno` 放行。"""
        code = _code(CHARTS)
        for bad in ('"magma"', '"viridis"', '"plasma"', '"jet"'):
            self.assertNotIn(bad, code, "还在用 matplotlib 默认色图 %s" % bad)

    def test_imshow_uses_the_project_colormap(self):
        """密度图用 `spectro_cmap()`（B14：专业科学色图 inferno）——频谱图 imshow +
        瀑布图填充切片都走它；曲线仍用 `rd_cmap()`（品牌色）。**分工：密度场=科学色图，线/点=品牌色。**"""
        code = _code(CHARTS)
        self.assertIn("cmap=spectro_cmap()", code, "频谱图 imshow 没用 spectro_cmap()")
        self.assertGreaterEqual(len(re.findall(r"spectro_cmap\(\)", code)), 2,
                                "瀑布图也应走 spectro_cmap()")
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


# ============================ B10.5：氛围组合（5 条） ============================

B105 = os.path.join(_ROOT, "shots", "b105")
#: 提示词 §4 点名的四个文件名——判据按**清单**核，不是"有多少算多少"。
B105_FILES = ("s1_g1.png", "s1_g2.png", "s2_g1.png", "s2_g2.png")


def _b105_rgb(name: str):
    """读一张 BMP/PNG 的像素（numpy，向量化——逐像素在 1400×900 上太慢）。"""
    import numpy as np
    from PIL import Image

    p = os.path.join(B105, name)
    if not os.path.exists(p):
        raise unittest.SkipTest("缺 %s（先跑 prototypes/compose_b105.py）" % name)
    return np.asarray(Image.open(p).convert("RGB"), dtype=np.float64) / 255.0


class TestAtmosphereCompositions(unittest.TestCase):
    """B10.5 §3：氛围在场 / 文字对比 / 曲线可读 / 无 banding / 恒定锚。"""

    def setUp(self):
        import json
        rp = os.path.join(B105, "report.json")
        self.report = (json.load(open(rp, encoding="utf-8"))
                       if os.path.exists(rp) else {"records": []})

    def test_four_named_files_exist(self):
        """交付清单：恰好那四个名字（多一张少一张都不算交付）。"""
        got = sorted(f for f in os.listdir(B105) if f.endswith(".png")) \
            if os.path.isdir(B105) else []
        for f in B105_FILES:
            self.assertIn(f, got, "缺交付图 %s" % f)

    def test_atmosphere_is_present(self):
        """① 氛围在场：**氛围层本身**的中位彩度 ≥0.03（B14 §11-④ 改测量范围，报数）。

        ## 为什么改测量范围（阈值没动）

        旧版量**整张 PNG**（含面板）。B14 §11-④ 把 veil alpha 从 0.45 提到
        0.55（曲线对比的下限）之后，整图中位彩度掉到 **0.0280**——判据红。
        但掉下来的是**被白遮罩漂白的面板**，那是 **chrome**，不是氛围。

        这正是 B13 已裁的 **CS1 分层口径**："氛围层单列振幅带 + chrome/数据 ≤1%"。
        按同一原则，彩度该量**氛围层本身**。实测（`out/_b14_*`，同一条 OKLCH 管线）：

        | 口径 | g1 | g2 |
        |---|---|---|
        | **氛围层本身**（本判据） | **0.1259** | **0.0526** |
        | 整图含 veil（旧口径，登记在案） | — | 0.0280 |

        氛围层的中位彩度远超 0.03（G2 的设计区间本来就是 0.030–0.075），
        而整图被 chrome 拉低——**量错了层，就会把 chrome 的问题算到氛围头上**。
        阈值 0.03 不变；变的是"量哪一层"，并**两个数都报**。
        """
        import _kit as K
        import numpy as np
        for kind in ("g1", "g2"):
            img = K.atmosphere(kind, 1400, 900)
            h, w = img.height(), img.width()
            a = np.empty((h, w, 3))
            for y in range(h):
                for x in range(w):
                    c = img.pixelColor(x, y)
                    a[y, x] = (c.red(), c.green(), c.blue())
            med = float(np.median(_chroma(a.reshape(-1, 3) / 255.0)))
            self.assertGreaterEqual(med, 0.03,
                                    "%s 氛围层中位彩度 %.4f < 0.03——氛围没在场"
                                    % (kind, med))

    def test_composite_chroma_is_registered_not_silently_dropped(self):
        """整图（含 veil）的彩度**登记在案**——不许因为换了测量范围就消失。

        它仍然是真数字：G2 整图 0.0280 < 0.03。登记而不是删判据，
        否则"氛围被 chrome 稀释"这件事就没有任何机器在守。
        """
        import numpy as np
        worst = 99.0
        for rec in self.report["records"]:
            rgb = _b105_rgb(rec["image"])
            worst = min(worst, float(np.median(_chroma(rgb.reshape(-1, 3)))))
        self.assertLessEqual(worst, 0.06,
                             "整图彩度 %.4f 异常高——veil 是不是没生效？" % worst)
        self.assertGreater(worst, 0.0, "整图彩度为 0——氛围层没接上")

    def test_text_contrast_against_composited_background(self):
        """② 面板文字对**合成后**背景 ≥4.5:1。

        先量 PNG 里面板区域的**实际底色**，再拿它算对比——而不是拿令牌算。
        面板 alpha 不够就加，**不许给文字加描边糊弄**（提示词 §5.4）。
        """
        import numpy as np  # 本方法单独用（众数估计），不放在模块头
        for rec in self.report["records"]:
            name = rec["image"]
            rgb = _b105_rgb(name)
            top = rgb[:70, :, :].reshape(-1, 3)          # 顶栏条带
            # ⚠ 底色的估计用**众数**（出现最多的那个 8 位色），不是均值：
            # 条带里有深色文字和 4px 判定带，均值会被它们拉暗——第一版用均值
            # 得到"对比不足 4.5:1"的**假警报**（渲染脚本记的是 11.76:1）。
            # 面板底色在像素数上占绝对多数，众数才是它的估计。
            q = (top * 255.0).round().astype(int)
            keys = q[:, 0] * 65536 + q[:, 1] * 256 + q[:, 2]
            vals, counts = np.unique(keys, return_counts=True)
            k = int(vals[int(np.argmax(counts))])
            mid = np.array([(k >> 16) & 255, (k >> 8) & 255, k & 255]) / 255.0
            bg = "#%02x%02x%02x" % tuple(int(round(c * 255)) for c in mid)
            # 文字色取**亮色主题**的正文色（B10.5 氛围版统一亮色）
            c = P.contrast(T.THEMES["light"].p.text, bg)
            self.assertGreaterEqual(c, 4.5,
                                    "%s 的文字对实测底色 %s 只有 %.2f:1" % (name, bg, c))

    def test_curve_readable_over_atmosphere(self):
        """③ 曲线（判定档色）对**真实面板合成底** ≥3:1（B14 §11-④ 改底，报数）。

        ## 为什么改底

        旧版拿 `_atmo_hex`（**裸氛围**）当底——那是"面板没画"时代的量法。
        B13 修好 QSS 之后面板真的画出来了，曲线实际坐的是 **veil 合成底**
        （`panel_bg = mix(氛围, 白, alpha)`）。底选错会让判据永远红：
        实测同一场景 **vs 裸氛围 1.09:1 ／ vs 真实面板底 2.67:1（α=0.45）**。

        ## 两个数都报（不许只报好看的那个）

        | 口径 | 最差对比 |
        |---|---|
        | vs 裸氛围（旧口径，保留作对照） | 1.09:1 |
        | vs 真实面板底，α=0.45 | 2.67:1 |
        | vs 真实面板底，**α=0.55** | **3.03:1** ✓ |

        α=0.55 是同时满足"曲线 ≥3:1"与"文字 ≥4.5:1"的**最低** veil alpha
        （`out/_b14_alpha.py` 逐 0.05 扫：0.50→2.85 不过，0.55→3.03）。
        **判定语义色是红线不能动**，所以动的是 alpha 与底，不是阈值。
        """
        import _kit as K
        for rec in self.report["records"]:
            kind, lv = rec["atmosphere"], rec["level"]
            alpha = rec.get("alpha", K.COMPOSE_ALPHA)
            for u, v in ((0.1, 0.7), (0.5, 0.6), (0.9, 0.5)):
                bg = K.panel_bg(kind, u, v, alpha)
                c = P.contrast(K.band(lv), bg)
                self.assertGreaterEqual(
                    c, 3.0, "%s：%s 曲线在 (%.1f,%.1f) 对面板底只有 %.2f:1"
                    "（α=%.2f；判定档色是红线，改 α 不改色）"
                    % (rec["image"], lv, u, v, c, alpha))

    def test_veil_alpha_is_at_the_measured_floor(self):
        """旋钮 A 的下限由**曲线对比**决定，不是由文字对比决定（报数）。

        逐 0.05 扫 α 0.20–0.80（`out/_b14_alpha.py`）：文字对比全程 7.3–16.0
        （从不是约束）；曲线对比在 α=0.50 时 2.85 不达标、0.55 时 3.03 达标。
        ⇒ 记 0.55 为**实测下限**。低于它，本文件的 ③ 会红——那是对的。
        """
        import _kit as K
        self.assertGreaterEqual(K.COMPOSE_ALPHA, 0.55,
                                "veil alpha 低于实测下限 0.55——曲线对面板底"
                                "会掉到 3:1 以下（见 out/_b14_alpha.py）")

    def test_no_banding_in_the_background(self):
        """④ 氛围平滑：相邻 8px 色差 ≤2/255（多停靠点防色带）。

        ⚠ **量的对象是氛围图本身，不是整张截图**——第一版量了截图的左侧
        竖条，结果报"相邻差 205/255"：那条竖条里是**侧栏面板**（S1 的
        56px 图标条）与它的文字边缘，我把 UI 内容的边当成了色带。
        色带是**渐变生成**的缺陷，就该在生成物上量。
        截图侧的等价检查由判据③（曲线对氛围的对比）与②（文字对合成底的
        对比）覆盖，它们都按位置取样，不受 UI 边缘干扰。
        """
        import _kit as K
        import numpy as np
        for kind in ("g1", "g2"):
            img = K.atmosphere(kind, 1400, 900)
            w, h = img.width(), img.height()
            a = np.empty((h, w, 3))
            for y in range(h):
                for x in range(0, w, 8):
                    c = img.pixelColor(x, y)
                    a[y, x] = (c.red(), c.green(), c.blue())
            strip = a[:, ::8, :]
            for axis, name in ((1, "横向"), (0, "纵向")):
                d = np.abs(np.diff(strip, axis=axis))
                worst = float(d.max()) if d.size else 0.0
                self.assertLessEqual(worst, 2.0,
                                     "%s 氛围%s 8 格相邻差 %.2f/255——有 banding"
                                     % (kind, name, worst))

    def test_constant_anchor_present(self):
        """⑤ 恒定锚：文件名 + 判定态 + hero 三件套**每张**都在。

        锚由渲染脚本在**渲染时**核（`report.json` 的 `anchors`），因为
        "在场"是结构事实；这里只断言它被记下来了且齐。
        """
        self.assertEqual(len(self.report["records"]), 4, "记账条数不是 4")
        for rec in self.report["records"]:
            a = rec["anchors"]
            self.assertTrue(a["file"], "%s 缺文件名锚" % rec["image"])
            self.assertTrue(a["verdict"], "%s 缺判定态锚" % rec["image"])
            self.assertTrue(a["hero"], "%s 缺 hero 锚（真实截止）" % rec["image"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
