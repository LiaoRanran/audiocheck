"""色彩系统：OKLCH 生成 + WCAG 对比度。

## 为什么值得钉死

配色问题**不会报错**，只会让人"看不清"，而且几乎无法靠肉眼判定：
4.4:1 与 4.6:1 的差别看不出来，但一个是"不达标"、一个是"达标"。
所以这里的断言全部是**可复算的数字**，不是"看着还行"：

1. 色彩空间：OKLab 参考值来自 Ottosson 2020 原文（纯红/绿/蓝的 L、C、h）
   —— 公式写错时这几个数会立刻偏掉，比"看起来还挺蓝"可靠得多
2. 对比度：WCAG 2.1 的边界值（黑白 21:1、同色 1:1、#767676 对白 4.54）
3. 生成器：18 组种子（含无色相的灰、纯黑、纯白）× 深/浅两模式，
   生成时 `strict` 自审必须全过；同一输入必须给出同一结果
4. **两个内置主题也必须全部达标** —— 这条挡的是"以后有人手改一个色号，
   把判定胶囊又改回 2.99:1 而没人发现"
5. QSS 里不许再有**写死的颜色**：上一版 `QPushButton:hover` 写死 `#1c212b`，
   浅色主题下悬停按钮把深色文字画到近黑底上（1.1:1，字直接没了）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from audiocheck.ui import palette as P  # noqa: E402

# 生成器要用到的种子：含 4 个有彩色 + 灰/黑/白（无色相，走回退色相）+ 两个极端
SEEDS = ("#3d7dff", "#35c46a", "#e0a92e", "#f0524a", "#8b5cf6",
         "#000000", "#ffffff", "#808080", "#7f2a00")


class TestColorSpace(unittest.TestCase):
    """OKLab/OKLCH 换算：拿文献参考值当锚点。"""

    def test_reference_values_match_published(self):
        """纯红/绿/蓝的 OKLCH 值（Ottosson 2020，OKLab 原文表）。

        这几个数字是**外部锚点**：自己把矩阵抄错、把立方根写成平方根、
        把 gamma 曲线写反，都会在这里露出来，而不是等到某张图"看着怪"。
        """
        for hexv, L, C, h in (("#ff0000", 0.6280, 0.2577, 29.23),
                              ("#00ff00", 0.8664, 0.2948, 142.50),
                              ("#0000ff", 0.4520, 0.3132, 264.05)):
            got = P.srgb_to_oklch(hexv)
            self.assertAlmostEqual(got[0], L, places=3, msg="%s 的 L" % hexv)
            self.assertAlmostEqual(got[1], C, places=3, msg="%s 的 C" % hexv)
            self.assertAlmostEqual(got[2], h, places=1, msg="%s 的 h" % hexv)

    def test_achromatic_colors_have_zero_chroma(self):
        for hexv, L in (("#ffffff", 1.0), ("#000000", 0.0), ("#808080", 0.6)):
            lch = P.srgb_to_oklch(hexv)
            self.assertAlmostEqual(lch[0], L, places=3)
            self.assertAlmostEqual(lch[1], 0.0, places=4)

    def test_round_trip_is_lossless(self):
        """sRGB → OKLCH → sRGB 必须回到原色（不然生成器会悄悄漂色）。"""
        for hexv in ("#3d7dff", "#e0a92e", "#35c46a", "#12151c", "#f7f9fc", "#7f2a00"):
            self.assertEqual(P.oklch_to_srgb(P.srgb_to_oklch(hexv)), hexv)

    def test_out_of_gamut_keeps_hue(self):
        """超色域时保色相降 chroma，而不是把 RGB 硬夹（那会改成另一个颜色）。"""
        c = P.oklch_to_srgb((0.7, 0.4, 120.0))       # 0.4 的 chroma 远超 sRGB
        self.assertRegex(c, r"^#[0-9a-f]{6}$")
        self.assertLess(abs(P.srgb_to_oklch(c)[2] - 120.0), 1.0,
                        "降 chroma 之后色相漂了：%s" % c)


class TestTemperatureLayering(unittest.TestCase):
    """色温分层：暗处偏冷、亮处偏暖（自然光照的规律）。"""

    def test_dark_levels_go_cool_light_levels_go_warm(self):
        from audiocheck.ui.palette import _warm_cool_hue

        warm = 85.0                      # 琥珀种子（暖）
        deep = _warm_cool_hue(warm, 0.15)
        bright = _warm_cool_hue(warm, 0.95)
        self.assertLess(abs(deep - 250), abs(warm - 250), "暗部没有偏冷")
        self.assertLess(abs(bright - 60), abs(warm - 60), "亮部没有偏暖")

    def test_mid_tone_barely_moves(self):
        from audiocheck.ui.palette import _warm_cool_hue

        self.assertAlmostEqual(_warm_cool_hue(85.0, 0.5), 85.0, places=6)

    def test_rotation_follows_the_short_arc(self):
        """沿短弧转：绝不会绕到色环对面（0° 与 359° 不是"差 1°"就是"差 359°"）。"""
        from audiocheck.ui.palette import _warm_cool_hue

        h = _warm_cool_hue(340.0, 0.2)   # 冷端 250：短弧应从 340 往回走（经 0°）
        delta = min(abs(h - 340.0), 360 - abs(h - 340.0))
        self.assertLess(delta, 20.0, "转了长弧 → 色相会漂到另一个颜色")

    def test_cap_is_bounded(self):
        from audiocheck.ui.palette import _warm_cool_hue

        for level in (0.0, 0.05, 0.5, 0.95, 1.0):
            for hue in (0.0, 120.0, 250.0):
                raw = abs((_warm_cool_hue(hue, level) - hue + 180) % 360 - 180)
                self.assertLessEqual(raw, 0.16 * 180 + 1e-9, "偏移没有封顶")

    def test_dark_bg_is_cooler_than_light_bg_for_same_seed(self):
        """同一个种子：深色主题的底色应比浅色主题更靠冷端。"""
        dark_bg = P.srgb_to_oklch(P.build("#e0a92e", "dark")["bg"])[2]
        light_bg = P.srgb_to_oklch(P.build("#e0a92e", "light")["bg"])[2]
        self.assertLess(abs(dark_bg - 250), abs(light_bg - 250))


class TestContrast(unittest.TestCase):
    """WCAG 2.1 对比度的边界值。"""

    def test_extremes(self):
        self.assertAlmostEqual(P.contrast("#000000", "#ffffff"), 21.0, places=4)
        self.assertAlmostEqual(P.contrast("#ffffff", "#ffffff"), 1.0, places=4)

    def test_known_midpoint(self):
        """#767676 对白是经典"刚好过 AA"的灰（4.54:1）—— 拿它当标尺。"""
        self.assertAlmostEqual(P.contrast("#767676", "#ffffff"), 4.5422, places=3)

    def test_symmetric(self):
        self.assertAlmostEqual(P.contrast("#3d7dff", "#12151c"),
                               P.contrast("#12151c", "#3d7dff"), places=6)

    def test_fit_contrast_reaches_target_without_touching_hue(self):
        fg, bg = "#626c80", "#12151c"
        self.assertLess(P.contrast(fg, bg), 4.5)
        fixed = P.fit_contrast(fg, bg, 4.5, lighter=True)
        self.assertGreaterEqual(P.contrast(fixed, bg), 4.5)
        self.assertLess(abs(P.srgb_to_oklch(fixed)[2] - P.srgb_to_oklch(fg)[2]), 2.0,
                        "调明度时色相不该漂")


class TestGeneratedPalettes(unittest.TestCase):
    """种子色生成：两种模式 × 9 个种子必须全部达标且确定。"""

    def test_all_seeds_pass_strict_audit(self):
        for mode in ("dark", "light"):
            for seed in SEEDS:
                pal = P.build(seed, mode)          # 内部就是 strict=True
                bad = [f for f in P.audit(pal) if not f.ok]
                self.assertEqual(bad, [], "%s / %s 不达标：%s"
                                 % (mode, seed, [f.line() for f in bad]))

    def test_deterministic(self):
        self.assertEqual(P.build("#3d7dff", "dark"), P.build("#3d7dff", "dark"))

    def test_hue_follows_seed(self):
        """主色要能认出种子色：色相差不超过 10°。"""
        for seed in ("#3d7dff", "#35c46a", "#e0a92e", "#8b5cf6"):
            h_seed = P.srgb_to_oklch(seed)[2]
            h_prim = P.srgb_to_oklch(P.build(seed, "dark")["primary"])[2]
            delta = min(abs(h_seed - h_prim), 360 - abs(h_seed - h_prim))
            self.assertLess(delta, 10.0, "%s → 主色色相漂了 %.1f°" % (seed, delta))

    def test_achromatic_seed_does_not_invent_a_hue(self):
        """灰/黑种子不该生成"红色主色"（atan2(0,0) 恒为 0 的坑）。"""
        for seed in ("#000000", "#ffffff", "#808080"):
            h = P.srgb_to_oklch(P.build(seed, "dark")["primary"])[2]
            self.assertAlmostEqual(h, P.DEFAULT_HUE, delta=6.0)

    def test_modes_are_mirrored_in_lightness(self):
        """同一个种子：深色主题底色更深、文字更亮；两边的正文对比度都必须 ≥7。"""
        dark, light = P.build("#3d7dff", "dark"), P.build("#3d7dff", "light")
        L = lambda c: P.srgb_to_oklch(c)[0]  # noqa: E731
        self.assertLess(L(dark["bg"]), L(light["bg"]))
        self.assertGreater(L(dark["text"]), L(light["text"]))
        for pal in (dark, light):
            self.assertGreaterEqual(P.contrast(pal["text"], pal["surface"]), 7.0)

    def test_bad_mode_rejected(self):
        with self.assertRaises(ValueError):
            P.build("#3d7dff", "dim")


class TestBuiltinThemes(unittest.TestCase):
    """两个内置主题也必须全部达标 —— 挡"手改一个色号又改回去"。"""

    def test_builtin_themes_pass_audit(self):
        from audiocheck.ui import theme as T
        for name in ("dark", "light"):
            pal = T.THEMES[name].p
            bad = [f for f in P.audit(pal) if not f.ok]
            self.assertEqual(bad, [], "%s 主题有 %d 项不达标：\n%s"
                             % (name, len(bad), "\n".join(f.line() for f in bad)))

    def test_no_hardcoded_colors_left_in_qss_template(self):
        """**QSS 模板**里除了登记过的白字，不许再有写死的颜色。

        注意要查的是 `qss()` 的源码模板，不是它渲染出来的字符串——
        渲染后的字符串本来就该全是展开的色值，查它等于什么都没查。

        原来模板里 `QPushButton:hover { background:#1c212b }` 是写死的：
        浅色主题下悬停按钮变成近黑底 + 深色字（1.1:1），字直接消失。
        凡是"模板里写死的颜色"都是同一类 bug。
        """
        import inspect

        from audiocheck.ui import theme as T
        allowed = {"#fff"}          # 关闭按钮悬停：白字压在 danger 上，有判据盯着
        found = set(re.findall(r"#[0-9a-fA-F]{3,8}\b", inspect.getsource(T.qss))) - allowed
        self.assertEqual(found, set(),
                         "QSS 模板里出现写死的颜色 %s：应当改用令牌" % sorted(found))

    def test_theme_from_seed_builds_a_usable_theme(self):
        from audiocheck.ui import theme as T
        th = P.theme_from_seed("amber", "#e0a92e", "dark")
        self.assertIsInstance(th, T.Theme)
        self.assertEqual(th.name, "amber")
        self.assertEqual([f for f in P.audit(th.p) if not f.ok], [])
        self.assertIn(P.build("#e0a92e", "dark")["primary"], T.qss(th))


if __name__ == "__main__":
    unittest.main(verbosity=2)
