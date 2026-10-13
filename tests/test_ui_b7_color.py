"""B7.1 色彩重构的判据：高彩面积、色相族、彩度区间、装饰无高彩。

## 这一份与前几份的区别：**判据落在"载体"上，不落在截图上**

B7 给的三条色彩判据（面积 3–4% / 单族 ≥85% / 彩度 0.10–0.20）都是**渲染结果**
的统计量，量它们要出图（`packaging/hue_family_report.py` 就是那把尺子，
数字记在 CHANGELOG）。但**单元测试不该依赖"跑一次分析 + 截一张图"**——
那会让 `pytest` 从 90 秒涨到几分钟，而且图的面积还随文件数飘。

所以这里断言的是**决定那些统计量的设计不变量**：

| 要做的事 | 可测的代理不变量 |
|---|---|
| 高彩面积 3–4% | 承载着色的那**一块**（判定结论条）必须 ≥2% 屏面积——面积来自它，不来自列表内容 |
| 单族 ≥85% | **除判定语义外，没有任何元素取用高彩**（deco 压到 C≤0.10 之下，卡片条退回中性） |
| 彩度 0.10–0.20 | 判定色与结论条底色逐条落在区间内 |
| 装饰无高彩 | 遍历 deco 六档，逐档断言 C ≤ 高彩口径 |

这样"改了实现但判据还绿"的空间就很小：想让面积掉下去，得先把结论条改小；
想让族占比掉下去，得先给某个装饰元素提高彩。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

T.init_fonts(APP)

#: 高彩口径。**真源在 `palette`**，量尺（`packaging/hue_family_report.py`）
#: 与这份测试都从那里取——各写一份 0.10 的话，口径改了会得到两个都能"自证"的结论。
HIGH_C = P.HIGH_CHROMA_C
#: 判定结论条必须占全屏的最小比例。2% 是"它能撑起 3–4% 目标"的下限：
#: 实测它自己贡献 ~2.5%，加上胶囊/按钮/仪表环才到 3.4%。
SLAB_MIN_AREA = 0.02


def _c(hexv: str) -> float:
    return P.srgb_to_oklch(hexv)[1]


class TestHighChromaQuota(unittest.TestCase):
    """判据 5：高彩的彩度必须落在锚点区间 0.10–0.20。"""

    def test_verdict_colors_are_in_the_band(self):
        for mode in ("dark", "light"):
            pal = P.build(T.BUILTIN_SEED, mode, "calm")
            for k in ("primary", "success", "warning", "danger"):
                v = _c(pal[k])
                self.assertGreater(v, 0.10, "%s/%s 的彩度 %.3f 低于高彩口径" % (mode, k, v))
                self.assertLessEqual(v, 0.20, "%s/%s 的彩度 %.3f 超出锚点上界" % (mode, k, v))

    def test_slabs_are_at_the_anchor_median(self):
        """结论条底色取锚点高彩的 P50（0.123）略下方——**不超锚点**。

        超了就是"改锚点"而不是"照锚点做"（勘误 §5.6 第 1 条）。
        """
        for mode in ("dark", "light"):
            pal = P.build(T.BUILTIN_SEED, mode, "calm")
            for k in ("success_tint", "warning_tint", "danger_tint",
                  "success_band", "warning_band", "danger_band"):
                v = _c(pal[k])
                self.assertLessEqual(v, 0.14,
                                     "%s/%s 彩度 %.3f 超过锚点 P50" % (mode, k, v))

    def test_split_also_lifted_the_dark_warning_gamut_limit(self):
        """B9.1 的拆分**顺带解掉了暗色 warning 的色域限制**（实测结论）。

        B7 时代的限制：`warning_slab` 要占"大面积高彩底"这个角色，就得坐到
        L0.38 才够深——而 sRGB 里 H85 在 L0.38 处的彩度上限只有 ~0.073，
        于是它**到不了**高彩口径（当时如实登记为"色域边界，不是缺陷"）。

        B9.1 把高彩挪到 4px 的带上之后，带子坐在 **L0.62**——那里 H85 有
        0.125 的彩度可用（实测 dark `warning_band` C=0.125）。
        **同一个色相，位置一换，限制就没了。**

        这条测试的价值在"锁住这个改善"：谁要是把带子挪回低明度，
        warning 会重新掉到口径之下。
        """
        pal = P.build(T.BUILTIN_SEED, "dark", "calm")
        v = _c(pal["warning_band"])
        self.assertGreaterEqual(v, HIGH_C,
                                "暗色的 warning_band 掉回口径之下（%.3f）——"
                                "带子是不是又被挪到低明度去了？" % v)
        # 三个判定档的带子都必须在口径之上（这才是"判定语义有视觉重量"）
        for k in ("success_band", "warning_band", "danger_band"):
            self.assertGreaterEqual(_c(pal[k]), HIGH_C, "%s 没到高彩口径" % k)


class TestHueFamilyDiscipline(unittest.TestCase):
    """判据 4 的**代理不变量**：除判定语义外，没有元素取用高彩。

    族占比掉到 64% 的根因不是 accent 变淡，而是**装饰色阶在另一个色相上也很彩**。
    所以"保证族占比"的可测做法是：把装饰色阶压到口径之下。
    """

    def test_deco_is_below_the_high_chroma_caliper(self):
        for mode in ("dark", "light"):
            pal = P.build(T.BUILTIN_SEED, mode, "calm")
            for i, col in enumerate(pal["deco"]):
                v = _c(col)
                self.assertLessEqual(v, HIGH_C + 1e-6,
                                     "%s 的 deco[%d] 彩度 %.3f ≥ 口径——"
                                     "它会把'一枚 accent'稀释成六个色相" % (mode, i, v))

    def test_deco_is_still_separable(self):
        """压到口径之下**不等于**可以糊成一团：六档仍要彼此可分。

        判据用色相间距（≥50°）：低彩下明度接近时，色相是唯一还能依靠的线索。
        """
        for mode in ("dark", "light"):
            pal = P.build(T.BUILTIN_SEED, mode, "calm")
            hs = sorted(P.srgb_to_oklch(c)[2] for c in pal["deco"])
            gaps = [b - a for a, b in zip(hs, hs[1:])] + [hs[0] + 360 - hs[-1]]
            self.assertGreaterEqual(min(gaps), 50.0,
                                    "%s 的装饰色阶有档位挨得太近：%s" % (mode, gaps))

    def test_pop_keeps_the_saturated_ring(self):
        """`pop` 档不受这条纪律约束——它的定义就是"带色"（用户当初要的缤纷档）。

        两档走的是**同一套判据**，差别只在参数；这一条把"允许多彩"与
        "默认不多彩"分开钉住，免得有人拿 calm 的纪律去改 pop。
        """
        pop = P.build(T.BUILTIN_SEED, "dark", "pop")
        self.assertGreater(max(_c(c) for c in pop["deco"]), HIGH_C + 0.05)


class TestDecorativeElementsHaveNoHighChroma(unittest.TestCase):
    """判据 7：装饰元素的高彩面积为 0。"""

    def test_card_strip_is_neutral(self):
        """扫的是**生成后**的 QSS：占位符已经被替换成色值，所以要拿色值比。

        第一次写成断言字面量 `"top_light" in body`——那永远为假：
        `qss()` 返回的是 `format()` 之后的结果，模板里的 `{top_light}`
        早就变成了 `#47474e`。判据要对着**真东西**断言。
        """
        import re
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", q):
                if "CardAccent" not in sel:
                    continue
                self.assertIn(theme.p.top_light, body,
                              "%s 的卡片顶条不是中性顶光：%s" % (theme.name, body.strip()))
                for d in theme.p.deco:
                    self.assertNotIn(d, body, "卡片顶条又用回了装饰色阶 %s" % d)

    def test_only_three_deco_steps_reach_qss(self):
        """QSS 里只留品牌条那三个**小段**（判据 6：deco 只做小色块）。

        另外三档只在 Python 侧用（侧边栏导航图标按顺序取满六档）——
        导航图标是 17×17 的小色块，品牌条是 3 段各 8px 宽，都够"小"。
        """
        q = T.qss(T.DARK)
        used = {d for d in T.DARK.p.deco if d in q}
        self.assertLessEqual(len(used), 3,
                             "QSS 里用了 %d 档 deco，超过'小色块'的额度" % len(used))


class TestVerdictBannerCarriesTheAccent(unittest.TestCase):
    """判据 3 的代理：**承载着色的那一块必须够大**。

    高彩面积不是靠"把很多东西染上色"凑出来的，而是靠**一块够大的判定语义面**。
    这条断言把"面积来自它"钉住：它塌到 2% 以下，3–4% 的目标就不成立。
    """

    @classmethod
    def setUpClass(cls):
        from audiocheck.ui import window as W
        cls.w = W.MainWindow()
        cls.w.resize(1756, 900)
        cls.w.show()

    @classmethod
    def tearDownClass(cls):
        """**必须真正销毁**，不能只 `close()`。

        踩到过一次：只 close 的话窗口还活着，后续测试的
        `QTimer.singleShot` **不会按时触发**——`test_ui_material` 的
        "连发 8 条通知必须全部回收"于是失败（8 != 0），而它单独跑是绿的。
        报错信息完全没有指向这里，靠"单独跑能过"才反推出来。
        教训：**建整窗的测试必须负责把它拆掉**，否则它会污染
        "依赖计时器"的一切后续测试。
        """
        cls.w.close()
        cls.w.deleteLater()
        cls.w = None
        from PySide6.QtCore import QCoreApplication, QEvent
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def test_banner_is_large_enough_to_carry_the_accent(self):
        w = self.w
        b = w.banner
        frac = (b.width() * b.height()) / float(w.width() * w.height())
        self.assertGreaterEqual(
            frac, SLAB_MIN_AREA,
            "判定结论条只占全屏 %.2f%%（要求 ≥%.0f%%）——"
            "它是高彩面积的唯一稳定来源" % (frac * 100, SLAB_MIN_AREA * 100))

    def test_banner_takes_its_color_from_the_qss_property(self):
        """底色走 app 级 QSS 的 `level` 属性选择器，不是 setStyleSheet。"""
        w = self.w
        self.assertEqual(w.banner.objectName(), "VerdictBanner")
        for lv in ("ok", "warn", "bad"):
            w.banner.setProperty("level", lv)
            self.assertEqual(w.banner.property("level"), lv)
        q = T.qss(T.DARK)
        for lv in ("ok", "warn", "bad"):
            self.assertIn('QFrame#VerdictBanner[level="%s"]' % lv, q,
                          "结论条缺 %s 档的 QSS 规则" % lv)

    def test_slab_rule_wins_over_the_card_rule(self):
        """结论条**不再叫 Card**——它和中性容器是两种东西。

        同名的话，`QFrame#Card` 的渐变背景会与 `#VerdictBanner` 的平色
        撞在同一元素上，谁赢取决于 QSS 里的书写顺序（这类"看起来对了"
        最不可靠）。
        """
        self.assertNotEqual(self.w.banner.objectName(), "Card")


class TestSlabContrast(unittest.TestCase):
    """结论条上的文字对比度（新增 7 对判据，见 `palette.CHECKS`）。"""

    def test_all_slab_pairs_pass(self):
        for mode in ("dark", "light"):
            pal = P.build(T.BUILTIN_SEED, mode, "calm")
            bad = [f for f in P.audit(pal)
                   if "slab" in f.pair and not f.ok]
            self.assertEqual(bad, [], "%s 的结论条判据不达标：%s"
                             % (mode, [f.line() for f in bad]))

    def test_slab_pairs_exist(self):
        """判据**在表里**，不是"看起来没问题"。"""
        pairs = [c[0] for c in P.CHECKS]
        for k in ("success_tint", "warning_tint", "danger_tint"):
            self.assertTrue(any(k in p and "正文" in p for p in pairs),
                            "CHECKS 里缺结论区正文的判据：%s" % k)
            self.assertTrue(any(k in p and "判定带" in p for p in pairs),
                            "CHECKS 里缺判定带的判据：%s" % k)
        self.assertTrue(any("结论区" in p and "可分辨" in p for p in pairs),
                        "CHECKS 里缺'结论区与卡片底可分辨'那条（带登记阈）")

    def test_caption_on_slab_uses_the_dedicated_dim_color(self):
        """压在结论区淡底上的 11px 次文必须换 `tint_dim`。

        `text_faint` 在结论条底色上只有 ~2.9:1（11px 小字要求 4.5）——
        这是**换肤看得见、单测看不见**的那类问题，所以在这里钉住。
        """
        q = T.qss(T.DARK)
        self.assertIn("#VerdictBanner QLabel#Caption", q)
        idx = q.index("#VerdictBanner QLabel#Caption")
        self.assertIn(T.DARK.p.tint_dim, q[idx:idx + 200])


if __name__ == "__main__":
    unittest.main(verbosity=2)
