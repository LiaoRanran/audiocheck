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
#: **对话框里能点到的每一个 ACG 预设**都要能生成——用户会真的去点它们，
#: 而"某个预设种子生成不达标"这种失败只有在点下去的那一刻才看得见。
ACG_SEEDS = ("#e879a6", "#39c5bb", "#7c5cff", "#4f8cff", "#ff9f45", "#5fd6a0")


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


class TestAccent2(unittest.TestCase):
    """次级强调色：**冷 counterpart**（锚在中性阶色相），同明度同彩度。

    2026-10-13（B 块）改动：从"分裂互补 +150°"改为"锚定 `NEUTRAL_HUE`"。
    理由是配额：配色只许有**一枚暖 + 一枚冷**。原先 +150° 是从种子色出发转的，
    于是种子色一变第二枚也跟着跑（玫瑰种子会转到 164° 青绿，跟"冷"没关系）。
    """

    def test_hue_is_anchored_at_the_neutral_hue(self):
        pal = P.build("#f97083", "dark")
        h2 = P.srgb_to_oklch(pal["accent2"])[2]
        self.assertAlmostEqual(h2, P.NEUTRAL_HUE, delta=6.0,
                               msg="accent2 没有锚在仪器灰的色相上：%.1f°" % h2)

    def test_does_not_follow_the_seed_hue(self):
        """**核心断言**：换种子色时，冷的那枚不许跟着跑。"""
        seeds = ("#3d7dff", "#e0a92e", "#35c46a", "#f97083", "#8b5cf6")
        hues = {round(P.srgb_to_oklch(P.build(s, "dark")["accent2"])[2]) for s in seeds}
        for h in hues:
            self.assertAlmostEqual(h, P.NEUTRAL_HUE, delta=6.0,
                                   msg="accent2 跟着种子色跑了：%s" % sorted(hues))

    def test_two_accents_are_always_distinguishable(self):
        """**功能判据**：两条曲线永远分得开——色相与明度至少给一个。

        为什么不是"色相必须差 90°"：种子色落在冷区时（用户选了蓝紫），
        它与 accent2 的锚点只差二十几度，硬要求色相差就是要求做不到的事。
        数据可视化的标准退路是**换维度**——色相分不开就用明度。
        所以这里断言的是"某个维度分得开"，而 `test_same_visual_weight` 只在
        色相够开时才要求等重。
        """
        for mode in ("dark", "light"):
            for seed in ("#f97083", "#3d7dff", "#35c46a", "#e0a92e", "#5b6478"):
                pal = P.build(seed, mode)
                l1, _c1, h1 = P.srgb_to_oklch(pal["primary"])
                l2, _c2, h2 = P.srgb_to_oklch(pal["accent2"])
                gap = abs((h2 - h1 + 180) % 360 - 180)
                self.assertTrue(gap > 60.0 or abs(l1 - l2) >= 0.12,
                                "%s/%s 两条曲线既不同色相也不同明度（Δh=%.0f° ΔL=%.2f）"
                                % (mode, seed, gap, abs(l1 - l2)))

    def test_cool_seed_falls_back_to_lightness(self):
        """冷区种子触发明度退路；暖/绿区种子不该被误触发（那会白破坏等重）。"""
        blue = P.build("#3d7dff", "dark")
        l1 = P.srgb_to_oklch(blue["primary"])[0]
        l2 = P.srgb_to_oklch(blue["accent2"])[0]
        self.assertGreater(abs(l1 - l2), 0.12, "冷区种子没有走明度退路")
        rose = P.build("#f97083", "dark")
        r1 = P.srgb_to_oklch(rose["primary"])[0]
        r2 = P.srgb_to_oklch(rose["accent2"])[0]
        self.assertLess(abs(r1 - r2), 0.05, "暖区种子被误压了明度，等重被破坏")

    def test_same_visual_weight_as_primary(self):
        """明度与彩度都相近才有"等重"观感——否则一条曲线天然比另一条显眼。"""
        for mode in ("dark", "light"):
            pal = P.build("#f97083", mode)
            l1, c1, _ = P.srgb_to_oklch(pal["primary"])
            l2, c2, _ = P.srgb_to_oklch(pal["accent2"])
            self.assertLess(abs(l1 - l2), 0.12, "%s 明度不等重" % mode)
            if c1 > 0.05:                    # 近中性种子下彩度本身就没有意义
                self.assertLess(abs(c1 - c2) / c1, 0.35, "%s 彩度不等重" % mode)

    def test_visible_on_plot_background(self):
        for mode in ("dark", "light"):
            pal = P.build("#8b5cf6", mode)
            self.assertGreaterEqual(P.contrast(pal["accent2"], pal["plot_bg"]), 3.0)


class TestInstrumentGrey(unittest.TestCase):
    """仪器灰（B 块）：中性阶**不再跟随种子色相**，且彩度落在配额带内。

    这两条合起来就是"配额制配色"的落点：0.9 的界面面积是不带品牌色的冷灰，
    品牌色只出现在 accent 一族上。原先整张界面都被种子色染一遍，
    "一枚 accent"事实上是"一枚 accent + 一整套染色"。

    判据范围严格照提示词：**bg → surface_raised** 这一段（不含 grid / border /
    hover——那几个是"描边与叠加"，本来就要比表面更实一点才看得见）。
    """

    #: bg → surface_raised。这是"表面"，也是 90% 面积所在。
    SURFACES = ("bg", "surface", "surface_hi", "surface_raised")

    def test_neutral_hue_does_not_follow_the_seed(self):
        """**核心断言**：换种子色，背景灰不许变色相。

        这是"配额制"与"种子色系统"能够共存的前提——如果背景跟着种子跑，
        那"90% 中性 + 一枚 accent"就永远做不到。
        """
        seeds = ("#3d7dff", "#e0a92e", "#35c46a", "#f97083", "#8b5cf6", "#5b6478")
        for mode in ("dark", "light"):
            for key in self.SURFACES:
                hues = []
                for s in seeds:
                    _L, C, h = P.srgb_to_oklch(P.build(s, mode)[key])
                    if C > 0.002:            # 近无彩时色相无定义，跳过
                        hues.append(h)
                if len(hues) < 2:
                    continue    # 纯白（浅色主题的 surface_raised）没有色相可言
                self.assertLess(max(hues) - min(hues), 2.0,
                                "%s/%s 的色相跟着种子色跑了：%s"
                                % (mode, key, [round(h, 1) for h in hues]))

    def test_neutral_hue_sits_in_the_instrument_family(self):
        """中性阶的色相落在仪器灰家族内（锚点 286° ± 12°）。

        上限 12° 的来历：参考图的中性簇实测在 280–291°，色温分层
        （`WARM_COOL_MAX`）再各允许 ≤7° 的偏移。加在一起就是 ±12°。
        """
        for mode in ("dark", "light"):
            for seed in ("#f97083", "#3d7dff"):
                for key in self.SURFACES:
                    L, C, h = P.srgb_to_oklch(P.build(seed, mode)[key])
                    if C <= 0.002:
                        continue
                    off = abs((h - P.NEUTRAL_HUE + 180) % 360 - 180)
                    self.assertLessEqual(off, 12.0,
                                         "%s/%s/%s 跑出仪器灰家族：%.1f°（锚点 %.0f°）"
                                         % (mode, seed, key, h, P.NEUTRAL_HUE))

    def test_calm_surfaces_stay_inside_the_chroma_quota(self):
        """calm 档的中性阶彩度 ≤ `NEUTRAL_CHROMA_MAX`（0.0125）。

        这条是"配额制"里最容易悄悄破的一条：中性阶的 tint 是个乘数，
        有人把它乘大一点（为了"层次更明显"），画面就又开始发脏了。
        """
        for mode in ("dark", "light"):
            for seed in ("#f97083", "#3d7dff", "#35c46a", "#5b6478"):
                pal = P.build(seed, mode, "calm")
                for key in self.SURFACES:
                    C = P.srgb_to_oklch(pal[key])[1]
                    self.assertLessEqual(
                        C, P.NEUTRAL_CHROMA_MAX + 0.001,
                        "%s/%s/%s 超出中性彩度配额：%.4f" % (mode, seed, key, C))

    def test_high_chroma_area_is_quota_limited_by_construction(self):
        """"高彩面积 ≤5%"的**结构性**保证：高彩令牌只有固定的那么几个。

        为什么不在这里量像素面积：那需要渲染截图，属于 `palette_report.py`
        的活（它出报告）。这里钉的是**能不能超标**这道门：
        整张配色里 C>0.10 的令牌只允许是 accent 一族与语义色，
        中性阶/描边/文本一律不许进去——只要令牌表是这样，面积就不可能失控。
        """
        pal = P.build("#f97083", "dark", "calm")
        allowed = set()
        for k in ("primary", "primary_hi", "primary_text", "accent2",
                  "success", "warning", "danger", "info"):
            allowed.add(k)
        neutral = ("bg", "surface", "surface_hi", "surface_raised", "grid", "border",
                   "border_hi", "hover", "hover_hi", "text", "text_dim", "text_faint",
                   "text_disabled", "plot_bg", "selection", "top_light")
        for k in neutral:
            C = P.srgb_to_oklch(pal[k])[1]
            self.assertLessEqual(C, 0.06,
                                 "%s 的彩度 %.4f 太高——它属于大面积表面，不属于 accent"
                                 % (k, C))
        # 反向：accent 一族必须**真的**是高彩，否则"配额"没有对象
        for k in ("primary", "accent2"):
            self.assertGreater(P.srgb_to_oklch(pal[k])[1], 0.10,
                               "%s 不够高彩，它就不该占配额" % k)
        # 语义色的胶囊底（大面积）也必须低彩，否则三个判定胶囊会一起抢注意力。
        # 阈值 0.058 = 生成时请求的上限 0.055 + 8bit 量化与 oklab 往返的余量
        # （实测 danger_bg 落在 0.0553——不是超标，是量化噪声）。
        for k in ("success_bg", "warning_bg", "danger_bg", "info_bg"):
            self.assertLessEqual(P.srgb_to_oklch(pal[k])[1], 0.058,
                                 "%s（面积不小）彩度太高" % k)


class TestMaterialTokens(unittest.TestCase):
    """材质令牌：双档阴影 + 顶光高亮。

    **Qt 的 QSS 没有 box-shadow**（实测不报错也不生效），所以阴影是"参数令牌 +
    效果对象"两段式：令牌在这里被钉，落点在 `ui/shadow.py`。
    """

    def test_two_shadow_tiers_are_ordered(self):
        """near 与 far 必须是**有方向差别**的两档，不是同一档的两个名字。

        far = 离观察者更远 → 更虚（blur 更大）、更淡（alpha 更小）、
        投得更远（dy 更大）。三条都单调，否则两档只是重复。
        """
        near, far = P.SHADOW_TIERS["near"], P.SHADOW_TIERS["far"]
        self.assertGreater(far["blur"], near["blur"], "far 不够虚")
        self.assertLess(far["alpha"], near["alpha"], "far 不够淡")
        self.assertGreater(far["dy"], near["dy"], "far 没有投得更远")

    def test_shadow_is_top_lit(self):
        """顶光：dy 为正（往下投）、dx 略负（光从左上来）。两档一致。"""
        for name, cfg in P.SHADOW_TIERS.items():
            self.assertGreater(cfg["dy"], 0, "%s 档的 dy 不为正：光从下面来了" % name)
            self.assertLessEqual(cfg["dx"], 0, "%s 档的 dx 为正：光从右上来了" % name)

    def test_shadow_color_is_not_pure_black(self):
        """阴影色取**偏冷的中性**，不是纯黑：黑色压在冷灰底上会发灰雾。"""
        for mode in ("dark", "light"):
            pal = P.build("#f97083", mode)
            for k in ("shadow_near", "shadow_far"):
                rgb = re.findall(r"[\d.]+", pal[k])
                self.assertEqual(len(rgb), 4, "%s 不是 rgba()：%r" % (k, pal[k]))
                r, g, b = (int(float(x)) for x in rgb[:3])
                self.assertLess(b, 255, "%s 是纯黑" % k)
                self.assertGreaterEqual(b, max(r, g), "%s 不够冷（b 不是最大分量）" % k)

    def test_top_light_is_lighter_than_the_border(self):
        """上缘高光要比描边亮一档——否则"顶光"看不出来，只是一条多余的线。

        浅色主题下方向相反（要**更白**才算被照亮），所以断言的是"离得很开"：
        深色下高光更亮、浅色下更接近白。两种模式都要求 |ΔL| ≥ 0.03。
        """
        for mode in ("dark", "light"):
            pal = P.build("#f97083", mode)
            L_hl = P.srgb_to_oklch(pal["top_light"])[0]
            L_bd = P.srgb_to_oklch(pal["border_hi"])[0]
            self.assertGreater(abs(L_hl - L_bd), 0.03,
                               "%s 的顶光与描边几乎一样（ΔL=%.3f），看不出高光"
                               % (mode, abs(L_hl - L_bd)))
            if mode == "dark":
                self.assertGreater(L_hl, L_bd, "深色主题的上缘高光该更亮")
            else:
                self.assertLess(L_hl, L_bd, "浅色主题的上缘高光该更白")


class TestVibeLayers(unittest.TestCase):
    """风格档：`calm` 与 `pop` 的差别必须是**可测量的**，不是形容词。

    "更鲜艳"这种说法在测试里等于没说。这里用两个可复算的量钉住它：
    表面的 chroma（彩度）与表面之间的色相位移（色彩推移）。
    """

    SEEDS = ("#3d7dff", "#e879a6", "#35c46a", "#8b5cf6")

    def test_pop_surfaces_are_more_chromatic(self):
        """pop 的表面必须比 calm 更带色（不然"缤纷"没有落地）。

        **浅色主题只查 bg / surface_hi**：sRGB 在接近白色的区域几乎没有
        彩度空间（实测 L=0.995 时请求 C=0.020，出来只剩 0.0026），
        所以"浅色主题的表面更带色"这句话在 L≈1.0 的两层上**不成立**——
        那不是 bug，是色域的物理边界。宁可在测试里限定范围，也不假装全成立。
        """
        for seed in self.SEEDS:
            calm, pop = P.build(seed, "dark"), P.build(seed, "dark", "pop")
            for key in ("bg", "surface", "surface_hi", "surface_raised"):
                self.assertGreater(
                    P.srgb_to_oklch(pop[key])[1], P.srgb_to_oklch(calm[key])[1],
                    "dark / %s / %s：pop 的表面没有更带色" % (seed, key))
            calm, pop = P.build(seed, "light"), P.build(seed, "light", "pop")
            for key in ("bg", "surface_hi"):
                self.assertGreater(
                    P.srgb_to_oklch(pop[key])[1], P.srgb_to_oklch(calm[key])[1],
                    "light / %s / %s：pop 的表面没有更带色" % (seed, key))

    def test_calm_surfaces_keep_one_hue(self):
        """calm 档的表面几乎不动色相——"克制"就是单一色相。

        阈值为什么是 15° 而不是 0：chroma 只有 0.012 时，色相本身**定义不良**
        （a/b 分量与 8 bit 量化噪声同量级，实测抖动 ±10°）。这不是 bug，
        是"接近中性灰"的必然结果，所以这里断言的是"小"，不是"零"。
        """
        for seed in self.SEEDS:
            pal = P.build(seed, "dark")
            hs = [P.srgb_to_oklch(pal[k])[2] for k in ("bg", "surface", "surface_hi",
                                                       "surface_raised")]
            drift = max(hs) - min(hs)
            self.assertLess(drift, 15.0, "calm 表面色相推移 %.1f°：%s"
                            % (drift, [round(h, 1) for h in hs]))

    def test_pop_shifts_hue_far_more_than_calm(self):
        """两档的差别必须落在同一个可测量上：表面色相的推移幅度。"""
        def drift(pal) -> float:
            hs = [P.srgb_to_oklch(pal[k])[2] for k in ("bg", "surface", "surface_hi",
                                                       "surface_raised")]
            return max(hs) - min(hs)

        for seed in self.SEEDS:
            calm, pop = P.build(seed, "dark"), P.build(seed, "dark", "pop")
            self.assertGreater(drift(pop), drift(calm) * 2.0,
                               "%s：pop 的色相推移没有明显大于 calm" % seed)
            self.assertGreater(drift(pop), 20.0, "%s：pop 的色相推移太弱" % seed)

    def test_pop_surfaces_shift_hue_with_depth(self):
        """pop 档：bg → surface_raised 色相单调上移，且位移有界。

        有界很重要：色相推移失控会变成"每一层一个颜色"，层级就不再是层级，
        而是一块调色板。上限 40° 是拍的上限，但**方向与单调性**是可断言的。
        比较沿**短弧**做：种子色相在 0° 附近时，357°→6° 也是"上移 9°"。
        """
        def rise(a: float, b: float) -> float:
            return (b - a + 180.0) % 360.0 - 180.0        # 沿短弧的有符号差

        for seed in self.SEEDS:
            pal = P.build(seed, "dark", "pop")
            hs = [P.srgb_to_oklch(pal[k])[2] for k in ("bg", "surface", "surface_hi",
                                                       "surface_raised")]
            for a, b in zip(hs, hs[1:]):
                self.assertGreater(rise(a, b), 0.0,
                                   "pop 表面色相没有随深度上移：%s"
                                   % [round(h, 1) for h in hs])
            self.assertLess(sum(rise(a, b) for a, b in zip(hs, hs[1:])), 40.0,
                            "色相推移失控：%s" % [round(h, 1) for h in hs])

    def test_both_vibes_pass_strict_audit(self):
        for mode in ("dark", "light"):
            for vibe in P.VIBES:
                for seed in SEEDS + ACG_SEEDS:
                    pal = P.build(seed, mode, vibe)
                    bad = [f for f in P.audit(pal) if not f.ok]
                    self.assertEqual(bad, [], "%s / %s / %s 不达标：%s"
                                     % (vibe, mode, seed, [f.line() for f in bad]))
                    bad = [f for f in P.deco_checks(pal) if not f.ok]
                    self.assertEqual(bad, [], "%s / %s / %s 装饰色阶不可见：%s"
                                     % (vibe, mode, seed, [f.line() for f in bad]))

    def test_vibe_is_orthogonal_to_mode(self):
        """明暗与风格是两根轴：pop 也有浅色，浅色也仍然是浅色。"""
        dark, light = P.build("#3d7dff", "dark", "pop"), P.build("#3d7dff", "light", "pop")
        L = lambda c: P.srgb_to_oklch(c)[0]                       # noqa: E731
        self.assertLess(L(dark["bg"]), L(light["bg"]))
        self.assertGreater(L(dark["text"]), L(light["text"]))

    def test_bad_vibe_rejected(self):
        with self.assertRaises(ValueError):
            P.build("#3d7dff", "dark", "vivid")

    def test_calm_is_the_default(self):
        self.assertEqual(P.build("#3d7dff", "dark"), P.build("#3d7dff", "dark", "calm"))


class TestDecoRamp(unittest.TestCase):
    """装饰色阶：并列的多色要"彼此可分、彼此等重、都看得见"。

    这三件事全是几何/对比度问题，可以钉数字；"好不好看"钉不了，也不该钉。
    """

    def test_six_steps_evenly_spaced(self):
        for seed in ("#3d7dff", "#e879a6", "#35c46a"):
            pal = P.build(seed, "dark", "pop")
            hs = sorted(P.srgb_to_oklch(c)[2] for c in pal["deco"])
            gaps = [b - a for a, b in zip(hs, hs[1:])] + [hs[0] + 360 - hs[-1]]
            self.assertEqual(len(pal["deco"]), 6)
            self.assertGreaterEqual(min(gaps), 50.0,
                                    "有色相挨得太近（%.1f°），并列色不可分" % min(gaps))
            self.assertLessEqual(max(gaps), 70.0, "色相间距不均：%s" % gaps)

    def test_equal_visual_weight(self):
        """等 L：谁都不比谁显眼（否则第 3 条曲线天生比第 1 条弱）。"""
        for mode in ("dark", "light"):
            pal = P.build("#3d7dff", mode, "pop")
            ls = [P.srgb_to_oklch(c)[0] for c in pal["deco"]]
            self.assertLess(max(ls) - min(ls), 0.12,
                            "%s 档装饰色阶明度差 %.3f" % (mode, max(ls) - min(ls)))

    def test_visible_on_every_background_it_is_drawn_on(self):
        for mode in ("dark", "light"):
            for seed in ("#3d7dff", "#e879a6", "#000000"):
                pal = P.build(seed, mode, "pop")
                for f in P.deco_checks(pal):
                    self.assertGreaterEqual(f.ratio, P.DECO_MIN_CONTRAST,
                                            "%s：%s" % (seed, f.line()))

    def test_pop_is_more_saturated_than_calm(self):
        for mode in ("dark", "light"):
            calm, pop = P.build("#3d7dff", mode), P.build("#3d7dff", mode, "pop")
            self.assertGreater(sum(P.srgb_to_oklch(c)[1] for c in pop["deco"]),
                               sum(P.srgb_to_oklch(c)[1] for c in calm["deco"]))

    def test_achromatic_seed_falls_back_to_default_hue(self):
        """灰种子：整条色阶**锚定**在回退色相上，不会得到随机起点。

        注意断言的是"第 1 档 ≈ DEFAULT_HUE"，而不是"每一档都 ≈"——
        装饰色阶本来就是从锚点等分出去的，第 2 档在锚点 +60° 是设计如此。
        """
        pal = P.build("#808080", "dark", "pop")
        anchor = P.srgb_to_oklch(pal["deco"][0])[2]
        self.assertLess(abs(anchor - P.DEFAULT_HUE), 6.0,
                        "色阶没有锚定在回退色相上：%.1f°" % anchor)

    def test_ramp_does_not_leak_into_semantics(self):
        """装饰色阶**不能**替换语义色：合格/有疑/不达标的色相是固定的。

        挡的是"顺手把 success 改成 deco 里某个好看的颜色"——
        那会让"合格"这个结论换个种子就换个颜色。
        """
        for seed in ("#3d7dff", "#e879a6"):
            pal = P.build(seed, "dark", "pop")
            for key, hue in P.LEVEL_HUE.items():
                got = P.srgb_to_oklch(pal[key])[2]
                self.assertLess(min(abs(got - hue), 360 - abs(got - hue)), 8.0,
                                "%s 的色相漂了：%.1f°（应 %.1f°）" % (key, got, hue))


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

    def test_builtin_themes_have_a_deco_ramp(self):
        """内置主题必须自带装饰色阶，否则界面上的彩色图标/色条会集体失效。

        它们是**生成**的（`palette.build(BUILTIN_SEED, mode, 'pop')`），
        所以这条同时挡"有人把内置主题改回手写色号、忘了补 deco"。
        """
        from audiocheck.ui import theme as T
        for name in ("dark", "light"):
            pal = T.THEMES[name].p
            self.assertEqual(len(pal.deco), 6, "%s 主题的装饰色阶不是 6 档" % name)
            bad = [f for f in P.deco_checks(pal) if not f.ok]
            self.assertEqual(bad, [], "%s 主题装饰色阶不可见：\n%s"
                             % (name, "\n".join(f.line() for f in bad)))

    def test_builtin_theme_is_not_hand_written(self):
        """内置主题必须是算出来的：换种子或换档位，它得跟着变。"""
        from audiocheck.ui import theme as T
        for mode in ("dark", "light"):
            generated = P.build(T.BUILTIN_SEED, mode, T.BUILTIN_VIBE)
            for k, v in generated.items():
                self.assertEqual(v, getattr(T.THEMES[mode].p, k),
                                 "%s 主题的 %s 与生成结果不一致" % (mode, k))

    def test_qss_exposes_deco_tokens(self):
        """QSS 里必须真的用到装饰色阶（品牌条），且色值来自当前主题。"""
        import inspect

        from audiocheck.ui import theme as T
        self.assertIn("#BrandBar", inspect.getsource(T.qss))
        q = T.qss(T.DARK)
        self.assertIn(T.DARK.p.deco[0], q, "品牌条没用上装饰色阶")

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
