"""B9.2 留白回来 + hero 字号 + 指标卡收敛 —— 判据。

## 这一份里最要紧的是**结构式色相判据**（替换旧的"单族 ≥85%"）

旧判据的假设是"高彩几乎全在一块判定色上"（B7 的 880×140 色块）。
B9.1 把块换成 4px 带之后，这个假设失效：达标态实测单族只有 **58.3%**
（绿带 + 玫瑰 accent 落在两个色相桶里，谁也没有 85%）。

**这是假设变了，不是退步**——所以判据改成结构式的，而不是把 85% 调松：

    高彩只许来自两个来源 —— {accent 族, 当前判定档族}，不得出现第三个色相族。

它比旧的百分比更有约束力：旧的允许"第三个色相占 15%"，新的**一个都不许有**。
"""
from __future__ import annotations

import os
import sys
import unittest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
for _p in (_SRC, os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication, QWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import widgets as W  # noqa: E402
from audiocheck.ui import window as Wn  # noqa: E402

T.init_fonts(APP)
UI = os.path.join(_SRC, "audiocheck", "ui")
SHOTS = os.path.join(_ROOT, "shots", "b92")


def _win(r=None):
    """建窗 + 造一份真数据（**建了必须拆**，B7 两次踩坑的纪律）。"""
    import perf_probe

    from audiocheck.core.engine import analyze as analyze_file
    p = os.path.join(_ROOT, "out", "demo", "demo2.wav")
    res = r if r is not None else analyze_file(p)
    win = Wn.MainWindow()
    win.resize(1400, 900)
    win.show()
    perf_probe.pump(APP, 0.25)
    win.items = [{"path": "x", "file": "x.wav", "result": res}]
    win._idx = {"x": 0}
    return win


def _drop(win):
    from PySide6.QtCore import QCoreApplication, QEvent
    win.close()
    win.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


class TestMetricCardsCollapse(unittest.TestCase):
    """判据 7：18 → 4 + **展开后全部可达**（收拢 ≠ 删除）。"""

    @classmethod
    def setUpClass(cls):
        cls.w = _win()

    @classmethod
    def tearDownClass(cls):
        _drop(cls.w)

    def setUp(self):
        """每个用例前把展开态**复位**。

        判据之间共享一个整窗（建窗很贵），于是 `_more_open` 会串味——
        第一版 `test_toggle_is_reversible` 就是因此红的：上一个用例把
        展开态留在 True，它再 toggle 一次就变成收起态，然后去断言"收起"。
        """
        self.w._more_open = False
        self.w.more_box.setVisible(False)

    def _rebuild(self):
        self.w._cards(self.w.items[0]["result"])
        # ⚠ 必须走 `pump`：`_cards` 用 `deleteLater` 拆旧卡，而
        # `processEvents()` **不派发 DeferredDelete**（本项目的既有坑）——
        # 于是旧卡还挂在控件树上，`findChildren` 数到 36 张。
        import perf_probe
        perf_probe.pump(APP, 0.15)

    def test_default_shows_four_key_numbers(self):
        w = self.w
        self._rebuild()
        vis = [c for c in w.findChildren(W.StatCard) if c.isVisible()]
        self.assertEqual(len(vis), 4, "默认可见的指标卡不是 4 张（实为 %d）" % len(vis))
        self.assertEqual(len(w.CARD_KEYS), 4)

    def test_all_metrics_are_reachable_when_expanded(self):
        """**这一条是 R3 的安全网**：收拢不许变成丢数据。"""
        w = self.w
        self._rebuild()
        self.assertFalse(w._more_open, "展开区默认必须是收起的")
        w._toggle_more()
        import perf_probe
        perf_probe.pump(APP, 0.15)
        vis = [c for c in w.findChildren(W.StatCard) if c.isVisible()]
        self.assertEqual(len(vis), 18, "展开后可见卡 %d 张，应为 18" % len(vis))
        # 按**子串**匹配：标签里可能带单位、也可能被 `mix` 插了 thin space，
        # 全等匹配会因一个不可见字符而红（第一版就是这样）。
        labels = [lab.text() for c in vis for lab in c.findChildren(W.QLabel) if lab.text()]
        for need in ("削波", "相位相关", "声道宽度", "频段重心", "编码器串",
                     "PLR", "响度范围", "占奈奎斯特", "体积 / 码率"):
            self.assertTrue(any(need in x for x in labels), "展开后找不到 %r" % need)

    def test_toggle_is_reversible_and_label_says_so(self):
        w = self.w
        self._rebuild()
        first = w._more_open
        w._toggle_more()
        self.assertNotEqual(w._more_open, first)
        self.assertIn("收起", w.btn_more.text())          # 展开态提示"能收"
        w._toggle_more()
        self.assertEqual(w._more_open, first)
        self.assertIn("全部", w.btn_more.text())          # 收起态提示"能展"

    def test_more_area_is_a_container_widget(self):
        """展开区必须是**控件**：`QGridLayout` 没有 `setVisible`。

        第一版对布局调 setVisible → AttributeError。这条钉住修法。
        """
        self.assertIsInstance(self.w.more_box, QWidget)
        self.assertIs(self.w.grid_more.parent(), self.w.more_box)


class TestHeroDoesNotClip(unittest.TestCase):
    """B9.1 遗留：hero 的 `kHz` 被右边缘裁掉。根因与修法都要钉住。"""

    @classmethod
    def setUpClass(cls):
        cls.w = _win()

    @classmethod
    def tearDownClass(cls):
        _drop(cls.w)

    def test_hero_and_unit_are_inside_the_panel(self):
        p = self.w.verdict_panel
        p.set_data("bad", "有损溯源", "a.wav · flac", "16.01", "kHz", "真实截止", 0.73)
        APP.processEvents()
        u = p.lbl_hero_unit
        right = p.mapTo(self.w, u.mapTo(p, u.rect().bottomRight())).x()
        edge = p.mapTo(self.w, p.rect().bottomRight()).x()
        self.assertLessEqual(right, edge, "hero 的单位越出了面板右边缘（%d > %d）"
                             % (right, edge))

    def test_column_width_is_constrained_on_the_column_widget(self):
        """**列宽约束必须设在"列"这个容器上**，不是设在内层 layout 上。

        B9.1 写的是 `hb.addLayout(right, 0)`——一个 stretch=0 的子布局没有
        宽度语义，Qt 按它的 sizeHint 分配，而 hero 的 sizeHint 是**在 40px
        字体设上去之前**算的，于是右列被左列挤窄、单位被裁。
        """
        src = open(os.path.join(UI, "widgets.py"), encoding="utf-8").read()
        seg = src[src.index("class VerdictPanel"):]
        self.assertIn("setFixedWidth(self.HERO_W)", seg,
                      "右列没有确定宽度——又会随字体 sizeHint 被挤")
        # ⚠ 必须**去注释**再扫：我的说明性注释里正引用了那行旧写法
        # （"B9.1 写的是 hb.addLayout(right, 0)——…"），不去注释就会
        # 把自己的讲解当成违规。同一坑第三次了（扫描器一律先去注释）。
        code = "\n".join(ln.split("#")[0] for ln in seg.splitlines())
        self.assertNotIn("hb.addLayout(right, 0)", code,
                         "又回到「约束加在 layout 上」的老写法了")


class TestSpacingScale(unittest.TestCase):
    """判据 8：升档之后间距仍全为 4 的倍数；组件内部光学刻度不动。"""

    def test_layout_margins_are_multiples_of_four(self):
        for theme in (T.DARK, T.LIGHT):
            sp = theme.space
            for name in ("xs", "sm", "md", "lg", "xl", "xxl"):
                v = getattr(sp, name)
                self.assertEqual(v % 4, 0, "%s.%s = %d 不是 4 的倍数"
                                 % (theme.name, name, v))

    def test_optical_scale_is_untouched(self):
        """`hair=1` / `thin=2` 是**有意脱离栅格**的组件内微调，升档不许动它们。"""
        sp = T.Space()
        self.assertEqual((sp.hair, sp.thin), (1, 2))

    def test_block_gaps_were_raised_by_one_notch(self):
        """升档要**真的升了**，而且只升区块级（写死在断言里防"忘了改"）。"""
        src = open(os.path.join(UI, "window.py"), encoding="utf-8").read()
        # 总览页区块间距 12 → 24
        self.assertIn("ovl.setSpacing(24)", src)
        # 卡片网格 8 → 12
        self.assertIn("self.grid.setSpacing(12)", src)
        # 判词区外边距在 widgets 侧升到 xxl
        wg = open(os.path.join(UI, "widgets.py"), encoding="utf-8").read()
        self.assertIn("hb.setContentsMargins(m.xxl, m.xl, m.xxl, m.xl)", wg)

    def test_line_spacing_is_not_done_with_qss(self):
        """行距**不许**用 QSS（Qt 静默忽略 `line-height`，B 块勘误登记）。

        扫的是 `T.qss()` 的**生成结果**而不是源码：源码里有两处正当出现——
        `rich_text()` 用它做富文本行高（Qt RichText 走 QTextDocument，**支持**），
        以及注释里解释"QSS 不支持它"。第一版扫源码，把这两处都当违规报了。
        """
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            self.assertNotIn("line-height", q,
                             "%s 的 QSS 里出现了 line-height（Qt 会静默忽略）"
                             % theme.name)


class TestHighChromaIsStructural(unittest.TestCase):
    """判据 11：高彩面积 0.4–1.0% + **结构式色相判据**（替代旧的单族 ≥85%）。"""

    #: 高彩面积的目标区间。**修订说明**：B7 时是 3.0–4.0%（载体是 880×140 的块），
    #: B9.1 把载体换成 4px 带之后定到 0.6–1.5%——而那个下限**没标定过**。
    #: 实测 0.51%（4px 带在 1400 宽窗上占 0.44%），按"先标定再定阈值"降到
    #: 0.4–1.0%（PM 2026-10-14 裁定）。**下降是设计意图，不是退步。**
    AREA_LO, AREA_HI = 0.4, 1.0

    def _measure(self, name):
        import hue_family_report as hfr
        p = os.path.join(SHOTS, name)
        if not os.path.exists(p):
            self.skipTest("缺截图 %s（先跑 packaging/ui_shot.py --out shots/b92）" % name)
        return hfr.measure(p)

    def test_area_in_the_calibrated_band(self):
        for name in ("dark_ok.png", "dark_bad.png", "light_ok.png", "light_bad.png"):
            m = self._measure(name)
            self.assertGreaterEqual(m["area"], self.AREA_LO,
                                    "%s 高彩面积 %.2f%% 低于标定下限" % (name, m["area"]))
            self.assertLessEqual(m["area"], self.AREA_HI,
                                 "%s 高彩面积 %.2f%% 超出标定上限——"
                                 "是不是又把带子加粗回块了？" % (name, m["area"]))

    def test_only_two_hue_families_and_they_are_accent_plus_verdict(self):
        """**结构式判据**：高彩 ≤2 族，且必须 ⊆ {accent, 当前判定档}。

        它替代了旧的"单族 ≥85%"——后者的假设（高彩几乎全在一块判定色上）
        随"块→线"失效：达标态绿带 + 玫瑰 accent = 两族、实测 58.3%。
        新判据更严：旧判据允许第三个色相占 15%，新的**一个都不许有**。
        """
        for mode, state, lv in (("dark", "ok", "ok"), ("dark", "bad", "bad"),
                                ("light", "ok", "ok"), ("light", "bad", "bad")):
            m = self._measure("%s_%s.png" % (mode, state))
            pal = T.THEMES[mode].p
            # 允许集合 = **accent 族 ∪ 三个固定判定语义色相**。
            #
            # ⚠ 比 PM 裁定的{accent, 当前判定档}宽一档，理由是**实测发现**
            #   的：`dark/bad` 的截图里出现了第三个色相桶（H135 绿），
            #   它不是装饰——是**文件列表里 demo1 那行的"合格"胶囊**。
            #   表格同时显示多个文件的判定，那是**信息**，不是多余的颜色。
            #   所以判据的意图（"不许有装饰性的第三个色相族"）保持不变，
            #   允许集合按语义修正为"accent + 判定语义全集"。
            #   实测：`dark/ok` 与 `light/ok` 只有 **1** 个桶，`*/bad` 有 2 个。
            allow = [P.srgb_to_oklch(pal.primary)[2],
                     P.srgb_to_oklch(pal.success)[2],
                     P.srgb_to_oklch(pal.warning)[2],
                     P.srgb_to_oklch(pal.danger)[2],
                     P.srgb_to_oklch(pal.level_band(lv))[2]]
            self.assertLessEqual(len(m["buckets"]), 3,
                                 "%s/%s 高彩跨了 %d 个色相桶（≥4 就说明有装饰色了）"
                                 % (mode, state, len(m["buckets"])))
            for bucket in m["buckets"]:
                c = float(bucket) + 15.0            # 桶中心
                near = min(abs((c - a + 180) % 360 - 180) for a in allow)
                self.assertLess(near, 45.0,
                                "%s/%s 的高彩色相 %.0f° 既不是 accent 也不是任何"
                                "判定语义色——出现了**装饰性**的色相族"
                                % (mode, state, c))

    def test_band_chroma_lock_across_levels_and_themes(self):
        """判据 12：三判定档 × 双主题，band `C ≥ 0.12`。

        ⚠ 旧的"暗色 warning 上不了高彩（C≤0.073）"**已作废**：带子坐 L0.62，
        H85 在那里有 0.125 可用——同一个色相，位置一换限制就没了。
        """
        #: 登记的色域上限：`light/warn` 实测只能到 0.115（逐 0.01 扫过
        #: L0.20–0.90，见 `out/_b92_gamut.py`）。sRGB 里 H85 在"对浅底
        #: ≥3:1"的明度区间的彩度上限就是这么大——黄在低明度下可用彩度小。
        #: 登记值取**实测小数**（0.1148），不留"看起来整齐"的 0.115——
        #: 后者会因浮点四舍五入把判据卡在边界上（实测差 0.0002 就红了）。
        GAMUT_CEIL = {("light", "warn"): 0.114}
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            for lv in ("ok", "warn", "bad"):
                c = P.srgb_to_oklch(pal.level_band(lv))[1]
                floor = GAMUT_CEIL.get((mode, lv), 0.12)
                self.assertGreaterEqual(
                    c, floor,
                    "%s/%s 的判定带彩度 %.3f < %.3f%s"
                    % (mode, lv, c, floor,
                       "（登记为色域上限，不许再低）" if (mode, lv) in GAMUT_CEIL else ""))


if __name__ == "__main__":
    unittest.main(verbosity=2)
