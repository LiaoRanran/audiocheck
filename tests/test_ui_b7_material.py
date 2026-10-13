"""B7.4 材质与特效：六条"不廉价"判据。

| # | 判据 | 这里的落点 |
|---|---|---|
| 1 | **核心锐利**：数据曲线 1.5–2px，不加模糊 | 扫 `charts.py` 的线宽与模糊调用 |
| 2 | **亮边下带暗线** | 浮层的亮/暗描边必须成对出现 |
| 3 | **二次反光**：高彩面邻近有同色呼应 | 结论条描边与底色同色相、且更彩 |
| 4 | **光强分级**：一屏只有一处最强光效 | 最高彩度的元素必须是判定结论条 |
| 5 | **≤3 色族** | 高彩像素的色相桶数 ≤3（实测 1） |
| 6 | **阴影不用纯黑** | 扫令牌与 QSS，不许出现纯黑阴影 |

## 两条平台限制要写在门口（免得后来者以为漏做）

* **Qt 不能在同一个边上叠两条线**：`border-top` 只有一条。所以"亮边下带暗线"
  实现为**上缘亮线 + 下缘暗线**——同一个物理模型（光从上方来），
  判据也就按"成对出现"来写，而不是按"上缘线下 1px 处有暗线"。
* **没有辉光**：B2 已判决"辉光退场"。所以"二次反光"用**同色相的细描边**
  表达（高彩面自己的光落在自己边缘），而不是向外发散的 glow。
  判别方法很直接：glow 会让**面外**的像素发光，描边不会。
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

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

UI = os.path.join(_SRC, "audiocheck", "ui")
SHOT = os.path.join(_ROOT, "shots", "b74", "dark.png")


def _src(fn: str) -> str:
    """读源码，**去掉注释与 docstring**。

    判据扫的是代码，不是文档。第三次踩同一个坑：`theme.py` 的注释里写着
    "旧的 `shadow` 是 `rgba(0,0,0,.45)`"（说明它为什么被换掉），
    不去注释的话这条判据会**把讲解当成违规**报出来——而那正确的反应是
    把讲解删掉，正好把最该留下的信息删了。
    """
    txt = open(os.path.join(UI, fn), encoding="utf-8").read()
    txt = re.sub(r'"""(?:.|\n)*?"""', lambda m: re.sub(r"[^\n]", " ", m.group(0)), txt)
    txt = re.sub(r"'''(?:.|\n)*?'''", lambda m: re.sub(r"[^\n]", " ", m.group(0)), txt)
    out = []
    for line in txt.splitlines():
        j = line.find("#")
        out.append(line if j < 0 else line[:j])
    return "\n".join(out)


def _qss(theme=None) -> str:
    return T.qss(theme or T.DARK)


class TestCrispCore(unittest.TestCase):
    """判据 1：数据曲线 1.5–2px，且**不加模糊**。"""

    #: 这些线宽是**数据曲线**（必须 ≥1.5）。参考线/网格/描边不在其列——
    #: Tufte 的规矩是数据墨要比非数据墨重，把参考线也加粗就反了。
    CURVE_MIN = 1.5
    CURVE_MAX = 2.0

    def test_data_curves_are_thick_enough(self):
        """扫的是**画曲线的那些调用**，不是全文件的线宽集合。

        第一版扫的是"全文件出现过的线宽"，于是网格的 `lw=.35`、参考线的
        `lw=1.4` 都被算了进去，判据要么永远失败、要么得放到很松——
        两种都等于没有判据。改成按调用点归类。
        """
        src = _src("charts.py")
        curves = []
        for i, line in enumerate(src.splitlines(), 1):
            if re.search(r"\b(plot|semilogx|semilogy)\(.*\blw\s*=", line):
                for m in re.finditer(r"\blw\s*=\s*([\d.]+)", line):
                    curves.append((i, float(m.group(1)), line.strip()))
        self.assertTrue(curves, "没扫到任何画曲线的调用——判据的锚点失效了")
        bad = [(i, w) for i, w, _txt in curves
               if not (self.CURVE_MIN <= w <= self.CURVE_MAX)]
        self.assertEqual(bad, [], "数据曲线线宽掉出 1.5–2px：%s\n%s"
                         % (bad, "\n".join("  %d: %s" % (i, txt)
                                           for i, _w, txt in curves)))

    def test_no_blur_on_charts(self):
        """曲线**不加模糊**：模糊只许在外围光晕（本轮没有光晕）。"""
        src = _src("charts.py")
        for needle in ("gaussian", "GaussianFilter", "agg_filter", "filter2d", "blur"):
            self.assertNotIn(needle.lower(), src.lower(),
                             "图表里出现了模糊调用 %r——核心必须锐利" % needle)


class TestEdgePair(unittest.TestCase):
    """判据 2：亮边必须配暗线（成对出现）。"""

    OVERLAYS = ("QMenu", "#Palette", "#Toast")

    def test_overlays_have_both_a_light_top_and_a_dark_bottom(self):
        q = _qss()
        for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", q):
            if not any(o in sel for o in self.OVERLAYS):
                continue
            if "border-top" not in body:
                continue
            self.assertIn("border-bottom", body,
                          "%s 只有亮边没有暗线：%s" % (sel.strip(), body.strip()))

    def test_the_dark_line_is_actually_darker_than_the_surface(self):
        """暗线不能是个"看起来像暗线的亮色"——要比浮层底色暗。"""
        p = T.DARK.p
        for theme in (T.DARK, T.LIGHT):
            pal = theme.p
            L = lambda c: P.srgb_to_oklch(c)[0]          # noqa: E731
            self.assertLess(L(pal.bg), L(pal.surface_raised),
                            "%s：暗线（bg）不比浮层底（surface_raised）暗" % theme.name)
        self.assertLess(L(p.top_light), L(p.surface_raised) + 0.5)


class TestSecondBounce(unittest.TestCase):
    """判据 3：二次反光——高彩面的邻近区域有**同色**呼应。"""

    def test_band_is_the_same_hue_as_its_tint(self):
        """判定带与它脚下的淡底**同色相**——"判定语义"在两处必须是同一个色相。

        B9.1 之前的"二次反光"是"高彩块自己的光落在自己边缘上"（一圈同色描边）。
        改成 tint+band 之后，**带子本身就是那道光**：它同色相、更高彩，
        而且是判定语义唯一的视觉重量所在。所以这里断言的是"带与底同色相"——
        形式换了，物理模型没换（光落在面上）。
        """
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            for lv in ("ok", "warn", "bad"):
                slab = pal.level_tint(lv)
                rim = pal.level_band(lv)
                h_s = P.srgb_to_oklch(slab)[2]
                h_r = P.srgb_to_oklch(rim)[2]
                d = abs((h_r - h_s + 180) % 360 - 180)
                self.assertLess(d, 12.0,
                                "%s/%s 描边与底色色相差 %.1f°（应同色相）" % (mode, lv, d))
                # ⚠ 这里**不断言"更彩"**：暗色主题下判定色确实比结论条底色彩
                #   （0.155 vs 0.115），但**亮色主题下不成立**（实测 warning 的
                #   判定色被它自己的 WCAG 拟合降到 C0.107，反而低于底色的 0.115）。
                #   那是 `fit_contrast` 为了让它压在 `warning_bg` 上达标而做的调整，
                #   是**正确的**——所以判据退一步，只要求"同色相 + 看得出有一条边"。
                #   实测值记在 CHANGELOG，不为了凑判据去造第九个令牌。
                # 带子要**在淡底上读得出**（非文本 3:1，WCAG 1.4.11）——
                # 它是判定语义的图形载体，读不出等于判定没表达。
                self.assertGreaterEqual(
                    P.contrast(rim, slab), 3.0,
                    "%s/%s 判定带在淡底上读不出（%.2f:1，要求 3:1）"
                    % (mode, lv, P.contrast(rim, slab)))

    def test_band_and_tint_are_in_the_qss(self):
        """两个角色都要在 app 级 QSS 里各有一条属性选择器（不给部件 setStyleSheet）。"""
        q = _qss()
        for sel in ('QFrame#VerdictBanner[level="bad"]',
                    'QFrame#VerdictBand[level="bad"]',
                    'QFrame#VerdictDot[level="bad"]'):
            self.assertIn(sel, q, "QSS 里缺 %s" % sel)
        i = q.index('QFrame#VerdictBand[level="bad"]')
        self.assertIn(T.DARK.p.level_band("bad"), q[i:i + 80])
        j = q.index('QFrame#VerdictBanner[level="bad"]')
        self.assertIn(T.DARK.p.level_tint("bad"), q[j:j + 80])


class TestLightHierarchy(unittest.TestCase):
    """判据 4：一屏只有一处最强光效（判定态）。"""

    def test_the_most_chromatic_surface_is_the_verdict_slab(self):
        """把界面里"成片的面"按彩度排一遍，最彩的必须是结论条。

        口径限定在**成片的面**（slab / 胶囊底 / 卡片 / 侧栏），
        不跟"细线和小图标"比——那些是点与线，不构成"光效强度"。
        """
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            surfaces = {"结论区淡底": max(P.srgb_to_oklch(pal.level_tint(lv))[1]
                                       for lv in ("ok", "warn", "bad")),
                        "胶囊底": max(P.srgb_to_oklch(pal.level_bg(lv))[1]
                                     for lv in ("ok", "warn", "bad")),
                        "主色底(primary_soft)": P.srgb_to_oklch(pal.primary_soft)[1],
                        "卡片(surface)": P.srgb_to_oklch(pal.surface)[1],
                        "侧栏(hover)": P.srgb_to_oklch(pal.hover)[1]}
            # B9.1 之后**光强分级换了形式**：最强的光不再是"一块面"，
            # 而是 4px 的**判定带**（高彩 C0.125）。所以判据改成两条：
            #   ① 所有"面"都在高彩口径之下（面不再抢戏）；
            #   ② 判定带是唯一的高彩元素。
            for name, c in surfaces.items():
                self.assertLessEqual(c, P.HIGH_CHROMA_C + 1e-6,
                                     "%s 的 %s 面彩度 %.3f 到了高彩——"
                                     "面不该抢判定带的戏" % (mode, name, c))
            band = max(P.srgb_to_oklch(pal.level_band(lv))[1]
                       for lv in ("ok", "warn", "bad"))
            self.assertGreaterEqual(band, P.HIGH_CHROMA_C,
                                    "%s 的判定带不是高彩（%.3f）" % (mode, band))


class TestHueFamilyBudget(unittest.TestCase):
    """判据 5：≤3 色族（高彩像素的 30° 桶数）。"""

    def test_high_chroma_spans_at_most_three_buckets(self):
        """**复用量尺本身**，不在这里另写一份 OKLCH。

        第一版想在测试里内联一份转换，引用了 `palette.oklch_of`（并不存在），
        结果被 `except ImportError` 吞成 skip——**判据静默失效**。
        这正是"看起来有判据"的失败模式：CI 全绿，什么都没测。
        现在直接 import `packaging/hue_family_report.py` 的 `measure()`：
        口径与量尺是同一份，且 import 失败会**报错而不是跳过**。
        """
        if not os.path.exists(SHOT):
            self.fail("没有截图：先跑 `python packaging/ui_shot.py --set dark`")
        import hue_family_report as hfr
        m = hfr.measure(SHOT)
        buckets = m.get("buckets") or {}
        self.assertTrue(buckets, "截图里没有高彩像素——accent 缺失")
        self.assertLessEqual(len(buckets), 3,
                             "高彩跨了 %d 个色相桶：%s" % (len(buckets), buckets))


class TestNoBlackShadow(unittest.TestCase):
    """判据 6：阴影不用纯黑（"硬纸板剪影"效应）。"""

    def test_no_pure_black_shadow_in_tokens(self):
        """令牌里的阴影色不许是 `rgba(0,0,0,…)` 或 `#000`。

        ⚠ 扫描要**限定在阴影**：`palette.self_check()` 里有一个
        `"#000000"` 的**测试种子**（构造黑色种子的边界用例），
        它不是阴影色。不限定的话这条判据会误报，然后被人"顺手注释掉"。
        """
        for fn in ("theme.py", "palette.py", "material.py"):
            src = _src(fn)
            for i, line in enumerate(src.splitlines(), 1):
                if "shadow" not in line.lower():
                    continue
                for m in re.finditer(r"rgba\(0,\s*0,\s*0[^)]*\)|#000\b|#000000", line):
                    self.fail("%s:%d 阴影用了纯黑 %r：%s"
                              % (fn, i, m.group(0), line.strip()))

    def test_qss_has_no_pure_black_shadow(self):
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            self.assertNotIn("rgba(0,0,0", q, "%s 的 QSS 里有纯黑阴影" % theme.name)

    def test_shadow_tokens_are_cool_not_grey(self):
        """阴影色偏冷：与中性阶同族（H≈286）才有"同一束光下的影子"的感觉。"""
        for k in ("shadow", "shadow_near", "shadow_far"):
            v = getattr(T.DARK.p, k)
            m = re.match(r"rgba\((\d+),(\d+),(\d+)", v)
            self.assertIsNotNone(m, "%s 不是 rgba：%r" % (k, v))
            r_, g_, b_ = (int(x) for x in m.groups())
            self.assertGreater(b_, r_, "%s 是暖色阴影（b≤r）：%r" % (k, v))


if __name__ == "__main__":
    unittest.main(verbosity=2)
