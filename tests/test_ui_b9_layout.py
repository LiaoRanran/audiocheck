"""B9.1 判定区降维 + 废环形仪表 —— 版面重构的第一块判据。

## 这一份判的是**结构**，不是颜色

B7 的失败在于"部件做得很好，但它不该存在"（诊断文档 §三·根因 1）：
270° 环形仪表有完整的设计理由注释、有缓动、有 `on_slab` 换色，
**每一处细节都对，整体是错的**。所以这一轮的判据不能只看"颜色对不对"，
必须把**结构**钉住：

* 环形/锥形渐变/圆头这些语汇**一个都不许留在布局里**（`test_no_ring_vocabulary`）；
* 占比必须表达为"数字 + 细分隔线 + 一个标记"；
* 判定语义拆成 tint（面）与 band（线）两个角色，**命名不许混**。

## 一个必须写下来的实测结论

`test_band_reaches_the_caliper`：B7 时代"暗色 warning 到不了高彩"是**色域边界**
（H85 在 L0.38 处彩度上限 ~0.073）。B9.1 把高彩从 880×140 的块挪到 4px 的带、
带子坐在 L0.62 之后——**同一个色相，位置一换，限制就没了**（实测 C=0.125）。
所以那条旧的"色域边界"判据在 B9 之后**必须改**，否则它会阻止一个已经发生的改善。
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

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import widgets as W  # noqa: E402

T.init_fonts(APP)
UI = os.path.join(_SRC, "audiocheck", "ui")


def _qss(theme=None) -> str:
    return T.qss(theme or T.DARK)


class TestRingVocabularyIsGone(unittest.TestCase):
    """判据 1：环形/锥形/圆头这些语汇一个都不许留在布局里。"""

    def test_gauge_class_is_deleted(self):
        """`Gauge` **整个删掉**，不是留着不用。

        诊断文档给了两个选项（"留 = 给 git 记录；删 = 防复用"）。
        选删：用户对它的判决是"怪异"，而它的 docstring 写着"环形仪表"这种
        看起来很正经的名字——留着迟早会被"复用"。git 里有完整历史。
        """
        self.assertFalse(hasattr(W, "Gauge"), "Gauge 类又回来了")

    def test_no_conical_gradient_anywhere(self):
        """锥形渐变是**仪表盘的专属语汇**，整个 UI 层都不许再有。

        这是最好用的一条：`QConicalGradient` 在重构前**只有** Gauge 用过，
        它一出现就说明"那个语汇回来了"。
        """
        for fn in os.listdir(UI):
            if not fn.endswith(".py"):
                continue
            src = open(os.path.join(UI, fn), encoding="utf-8").read()
            self.assertNotIn("QConicalGradient", src,
                             "%s 里还有锥形渐变（仪表语汇）" % fn)

    def test_no_arc_based_data_display(self):
        """不许用**弧**表达数据。

        ⚠ 判据要收窄到"数据表达"，这条踩过一次：第一版连 `theme.py` 的
        **矢量图标工厂**一起扫了，于是报了个假违规——`make_pixmap` 画
        "文件夹""盾牌"图标时用弧线完全正当，`chrome.py` 里窗口按钮的
        圆形字形也用 `Qt.RoundCap`。**假违规的归宿通常是"把规则删掉"**，
        所以范围必须写清楚：图标不是数据，弧画图标没问题，弧画**占比**不行。

        锚点：`drawArc` 在重构前只出现在 Gauge 的 `paintEvent` 里。
        """
        for fn in ("widgets.py", "window.py", "charts.py"):
            src = open(os.path.join(UI, fn), encoding="utf-8").read()
            self.assertNotIn("drawArc", src, "%s 里用弧表达数据了" % fn)

    def test_window_has_no_gauge_reference(self):
        src = open(os.path.join(UI, "window.py"), encoding="utf-8").read()
        self.assertNotIn("gauge", src.lower(), "window.py 还引用着仪表")


class TestSlabSplitIntoTwoRoles(unittest.TestCase):
    """判据 2/3/4：tint（面）与 band（线）两个角色，各自达标。"""

    LEVELS = ("ok", "warn", "bad")

    def test_no_ambiguous_slab_name(self):
        """命名不许混：`*_slab` 全部废除（它同时指"一块高彩底"和"判定语义面"）。"""
        for theme in (T.DARK, T.LIGHT):
            self.assertFalse(hasattr(theme.p, "level_slab"),
                             "level_slab 又回来了——两个角色必须分开")
            for lv in self.LEVELS:
                # 两个角色都必须**各自存在**（只删不建等于把判定区拆没了）
                self.assertTrue(theme.p.level_tint(lv))
                self.assertTrue(theme.p.level_band(lv))

    def test_tint_is_low_chroma_and_band_is_high(self):
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            for lv in self.LEVELS:
                t_c = P.srgb_to_oklch(pal.level_tint(lv))[1]
                b_c = P.srgb_to_oklch(pal.level_band(lv))[1]
                self.assertLessEqual(t_c, P.SLAB_TINT_C + 0.01,
                                     "%s/%s 的淡底太彩（%.3f）——面不该抢戏" % (mode, lv, t_c))
                self.assertGreaterEqual(b_c, P.HIGH_CHROMA_C,
                                        "%s/%s 的判定带没到高彩口径（%.3f）"
                                        % (mode, lv, b_c))

    def test_text_on_tint_passes_aa_and_tint_is_visible(self):
        """淡底上滚正文 ≥4.5；淡底与卡片底 ≥1.5（"看得出是一块"）。"""
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            for lv in self.LEVELS:
                tint = pal.level_tint(lv)
                self.assertGreaterEqual(P.contrast(pal.text, tint), 4.5,
                                        "%s/%s 正文压淡底不达标" % (mode, lv))
                self.assertGreaterEqual(P.contrast(tint, pal.surface), 1.5,
                                        "%s/%s 淡底与卡片底分不开" % (mode, lv))
                self.assertGreaterEqual(P.contrast(pal.tint_dim, tint), 4.5,
                                        "%s/%s 次文压淡底不达标" % (mode, lv))

    def test_band_is_readable_on_its_tint(self):
        """判定带要在淡底上读得出（非文本 3:1）——它是判定语义的图形载体。"""
        for mode in ("dark", "light"):
            pal = T.THEMES[mode].p
            for lv in self.LEVELS:
                self.assertGreaterEqual(
                    P.contrast(pal.level_band(lv), pal.level_tint(lv)), 3.0,
                    "%s/%s 判定带在淡底上读不出" % (mode, lv))

    def test_band_height_is_in_the_narrow_band(self):
        """带高 3–5px：**加粗回"块"就是走回头路**（B9 明确不做）。"""
        from dataclasses import fields

        from audiocheck.ui import theme as _T
        self.assertTrue(3 <= P.SLAB_BAND_H <= 5, "带高 %d 不在 3–5px" % P.SLAB_BAND_H)
        # 面板实际用的高度必须与常量同源（不许两处各写一个数）
        p = W.VerdictPanel(_T.DARK)
        self.assertEqual(p.band.height(), P.SLAB_BAND_H,
                         "面板的带高与 SLAB_BAND_H 不一致")
        self.assertIsNotNone(fields(_T.Type))


class TestHeroIsUnique(unittest.TestCase):
    """判据 5/6：hero 40px 且**一屏唯一**；破例理由必须留在 Type 里。"""

    @classmethod
    def setUpClass(cls):
        from audiocheck.ui import window as Wn
        cls.w = Wn.MainWindow()
        cls.w.resize(1400, 900)
        cls.w.show()
        APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        # **建了整窗就必须拆**（B7 踩过两次：不拆会让后续依赖计时器的测试失败）
        from PySide6.QtCore import QCoreApplication, QEvent
        cls.w.close()
        cls.w.deleteLater()
        cls.w = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def test_type_hero_exists_with_the_exemption_note(self):
        import inspect
        ty = T.Type()
        self.assertEqual(ty.hero, 40.0)
        src = inspect.getsource(T.Type)
        self.assertIn("hero", src)
        # 破例理由必须在 docstring 里（防未来轮当违规"修好"）
        self.assertIn("破例", src, "Type.hero 缺破例说明")
        self.assertIn("数据", src, "破例理由必须写清'hero 是数据不是层级'")

    def test_only_one_hero_sized_widget_on_screen(self):
        """运行时扫描：一屏**只准一个** hero 字号的部件。

        扫的是"实际渲染出来的部件"而不是源码——源码里有两处 QSS 规则
        （`#HeroBig` 与 `#VerdictText` 各一条）都不算违规，**长在屏幕上的**才算。
        """
        want = int(T.Type().hero)
        hits = []
        for lab in self.w.findChildren(W.QLabel):
            if round(lab.font().pointSizeF()) == want or int(
                    lab.font().pixelSize()) == want:
                hits.append(lab.objectName() or lab.text()[:12])
        self.assertEqual(len(hits), 1, "一屏出现了 %d 个 hero 级读数：%s" % (len(hits), hits))

    def test_hero_exists_in_the_panel(self):
        self.assertTrue(hasattr(self.w.verdict_panel, "lbl_hero"))
        self.assertEqual(self.w.verdict_panel.lbl_hero.objectName(), "HeroBig")


class TestVerdictPanelStructure(unittest.TestCase):
    """判据：判定区的结构就是小样那张图（带 / 方点 / 判词 / hero / 比例）。"""

    @classmethod
    def setUpClass(cls):
        from audiocheck.ui import window as Wn
        cls.w = Wn.MainWindow()
        cls.w.resize(1400, 900)
        cls.w.show()
        APP.processEvents()

    @classmethod
    def tearDownClass(cls):
        from PySide6.QtCore import QCoreApplication, QEvent
        cls.w.close()
        cls.w.deleteLater()
        cls.w = None
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def test_panel_pieces_exist(self):
        p = self.w.verdict_panel
        self.assertEqual(p.band.objectName(), "VerdictBand")
        self.assertEqual(p.dot.objectName(), "VerdictDot")
        self.assertEqual(p.lbl_verdict.objectName(), "VerdictText")
        self.assertIsInstance(p.ratio_bar, W.RatioBar)

    def test_set_data_wires_all_three_level_rules(self):
        """`set_data` 必须把 `level` 设到**容器 + 带 + 方点**三处。

        三处各有一条 QSS 属性选择器；漏一处就会出现"带子变了、底色没变"
        这种半更新——而它**不报错**。
        """
        p = self.w.verdict_panel
        p.set_data("bad", "有损溯源", "a.wav · flac · 16 位", "16.01", "kHz",
                   "真实截止", 0.73)
        for w in (p, p.band, p.dot):
            self.assertEqual(w.property("level"), "bad", "%s 的 level 没设上" % w)
        self.assertIn("73", p.lbl_ratio.text())

    def test_no_widget_level_stylesheet_in_the_panel(self):
        """判定区不许有部件级 setStyleSheet（欠账 #10：规则归主题）。

        B9.1 顺手收掉了判词那处（原来每次更新都刷一次字号与颜色）。
        """
        p = self.w.verdict_panel
        for lab in (p.lbl_verdict, p.lbl_file, p.lbl_hero, p.lbl_ratio):
            self.assertEqual(lab.styleSheet(), "",
                             "%s 带了部件级样式表" % (lab.objectName() or lab.text()[:8]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
