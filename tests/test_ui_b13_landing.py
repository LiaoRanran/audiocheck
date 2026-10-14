"""B13 落地轮 · 阶段 1 判据：字体落地 + QSS 静默忽略项 + 圆角收敛。

这一份的作用是把"前端不留尾巴"变成机器可执行的四条：
① 发货字体（OFL）在场 —— 缺族 fail loudly；
② 许可证随包 —— 红线④；
③ QSS 不许依赖被静默忽略的属性（`line-height` / `font-variant-numeric`）；
④ 圆角必须是令牌或**已登记**的裸值（新出现的裸数字即失败）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import palette as P  # noqa: E402
from audiocheck.ui import theme as T  # noqa: E402

FONTS = os.path.join(_ROOT, "src", "audiocheck", "assets", "fonts")
UI = os.path.join(_ROOT, "src", "audiocheck", "ui")


def _no_comments(s: str) -> str:
    """去注释 + 去 docstring 再扫（本项目的既有纪律：扫描器先去注释）。"""
    s = re.sub(r'"""(?:.|\n)*?"""', '""', s)
    s = re.sub(r"'''(?:.|\n)*?'''", "''", s)
    s = re.sub(r"/\*.*?\*/", "", s, flags=re.S)
    return "\n".join(ln.split("#")[0] for ln in s.splitlines())


class TestBundledFonts(unittest.TestCase):

    def test_bundled_families_present(self):
        """① 三个族必须在手：`IBM Plex Sans` / `IBM Plex Mono` / `Noto Sans SC`。

        `addApplicationFont` 失败时返回 -1 **且不抛异常**，所以必须断言 ——
        否则"装载了"只是希望，界面会静默回到系统字体（本项目踩过三次）。
        """
        T.load_bundled_fonts()
        have = set(__import__("PySide6.QtGui", fromlist=["QFontDatabase"])
                   .QFontDatabase.families())
        for fam in T.BUNDLED_FAMILIES:
            self.assertIn(fam, have, "发货字体缺族：%s" % fam)

    def test_ofl_licenses_shipped(self):
        """② 许可证必须随包（红线④：GR 的商业 .otf 只量测，发货走 OFL）。"""
        lic = [f for f in os.listdir(FONTS) if re.search(r"ofl|licen", f, re.I)]
        self.assertGreaterEqual(len(lic), 2, "缺许可证文件（实为 %s）" % lic)
        text = ""
        for f in lic:
            text += open(os.path.join(FONTS, f), encoding="utf-8",
                            errors="replace").read()
        self.assertRegex(text, r"OFL|Open Font License|SIL", "许可证里没看到 OFL 字样")

    def test_no_business_font_in_shipping_assets(self):
        """红线④的另一半：**商业字体文件不许出现在发货目录**。"""
        bad = [f for f in os.listdir(FONTS)
               if re.search(r"helvetica|haas|neue", f, re.I)]
        self.assertEqual(bad, [], "发货目录里出现了商业字体：%s" % bad)


class TestQssSilentIgnores(unittest.TestCase):
    """③ B13 §2A 点名要的扫描判据：防回归。"""

    def test_generated_qss_has_no_silently_ignored_properties(self):
        for theme in (T.DARK, T.LIGHT):
            q = T.qss(theme)
            for prop in ("line-height", "font-variant-numeric"):
                self.assertNotIn(prop, q, "%s 的 QSS 里出现 %s（Qt 静默忽略）"
                                 % (theme.name, prop))

    def test_widget_level_stylesheets_have_no_silently_ignored_properties(self):
        """控件级 `setStyleSheet` 也要扫——QSS 的静默忽略不分层级。

        ⚠ **只扫 `setStyleSheet(...)` 的实参**，不是整个文件：`theme.rich_text()`
        里的 `line-height` 是**富文本**（Qt RichText 走 `QTextDocument`，**支持**
        它；只有 QSS 忽略）。第一版扫全文，把这一处正当用法当违规报了——
        与"扫描器先去注释"同族：**扫描面必须是属性真正生效的那一层**。
        """
        hits = []
        call = re.compile(r"setStyleSheet\(\s*(.{0,400}?)\)\s*(\n|$)", re.S)
        for fn in sorted(os.listdir(UI)):
            if not fn.endswith(".py"):
                continue
            src = _no_comments(open(os.path.join(UI, fn), encoding="utf-8").read())
            for m in call.finditer(src):
                arg = m.group(1)
                for prop in ("line-height", "font-variant-numeric"):
                    if prop in arg:
                        hits.append("%s: setStyleSheet 用了 %s" % (fn, prop))
        self.assertEqual(hits, [], "控件级样式表里出现了静默忽略项：%s" % hits)


class TestRadiusTokens(unittest.TestCase):
    """④ 圆角收敛（B13 §2B）：新出现的裸半径即失败。

    B13 要求最终"除 `Radius.*` 外不许出现裸圆角数字"。现状里还有三处历史裸值，
    **先登记在案**（阶段 2 收敛；登记而不是删判据——删了就没有防回归了）。
    """

    #: **B14 §3B 之后：零裸半径**。B13 那版是登记制（2/3/5 各有处置说明），
    #: 本轮把它们全收进 `Radius`（nano=2 / tiny=3 / instrument=5）。
    #: 登记表清空 = 判据变严：再出现任何一个裸数字即失败。
    REGISTERED: dict = {}

    def test_every_naked_radius_is_registered(self):
        """⚠ **扫的是 QSS 模板源，不是生成结果**。

        第一版扫 `qss(theme)` 的输出，报出 `[4, 6, 8, 12]` —— 那是**令牌替换
        的正常产物**（`{lg}` → 8px）。裸半径的定义是"**模板里写了字面数字**，
        而不是引用令牌"，所以只能看模板：生成器干什么都不奇怪，模板才是人写的。
        """
        # ⚠ **不能过 `_no_comments`**：QSS 模板本身就是三引号字符串，会被它
        # 当成 docstring 抹掉——第一版因此扫到**空集**，判据"假绿"。
        # 只去 CSS 注释即可（`/* … */`）。
        src = open(os.path.join(UI, "theme.py"), encoding="utf-8").read()
        a = src.index("def qss(")
        b = src.index(".format(", a)
        template = re.sub(r"/\*.*?\*/", "", src[a:b], flags=re.S)
        found = {}
        for m in re.finditer(r"border-radius:\s*([0-9]+)px", template):
            found[int(m.group(1))] = found.get(int(m.group(1)), 0) + 1
        unregistered = sorted(v for v in found if v not in self.REGISTERED)
        self.assertEqual(unregistered, [],
                         "QSS 模板里出现了未登记的裸圆角：%s（请并入 Radius 令牌，"
                         "或在 REGISTERED 里登记并说明阶段 2 的处置）" % unregistered)
        # 登记表不许虚挂：登记了却已不存在的值要清掉（否则它会一直"合法"）
        for v in self.REGISTERED:
            self.assertIn(v, found, "登记的裸圆角 %dpx 已不存在——请从登记表移除" % v)

    def test_template_radius_found_exactly(self):
        """把实测到的裸半径写进断言：**它变了就要有人说话**（报数纪律的机械化）。"""
        src = open(os.path.join(UI, "theme.py"), encoding="utf-8").read()
        body = src[src.index("def qss("):src.index(".format(", src.index("def qss("))]
        template = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
        got = sorted({int(m.group(1))
                      for m in re.finditer(r"border-radius:\s*([0-9]+)px", template)})
        self.assertEqual(got, sorted(self.REGISTERED),
                         "QSS 模板的裸半径集合是 %s，登记表是 %s"
                         % (got, sorted(self.REGISTERED)))


class TestBorderWeight(unittest.TestCase):
    """③ 边框"可辨不抢"：border 不许比网格线重太多（B14 §3B）。

    基准是**已登记的网格线对比**（B9 立的"可辨不抢"口径）。实测
    （`out/_b14_3b2.py`）：dark border/bg = 1.30、grid/bg = 1.18；
    light border/bg = 1.18、grid/bg = 1.16。边框定边界、网格是背景纹理，
    前者略强是正常的，但不该**明显**强——强到跟数据墨抢就是设计错误。
    容差取 1.15×（实测余量：dark 1.30 ≤ 1.36、light 1.18 ≤ 1.33）。
    """

    def test_border_is_not_heavier_than_the_gridline(self):
        for theme in (T.DARK, T.LIGHT):
            p = theme.p
            grid = P.contrast(p.grid, p.bg)
            border = P.contrast(p.border, p.bg)
            self.assertLessEqual(border, grid * 1.15,
                                 "%s：边框 %.2f:1 比网格线 %.2f:1 的 1.15 倍还重"
                                 % (theme.name, border, grid))

    def test_hairline_scales_with_dpr(self):
        """DPR≥2 → 0.5px（否则 1 逻辑 px 渲染成 2 物理 px，发丝线糊成两根）。"""
        self.assertEqual(T.hairline_px(1.0), "1px")
        self.assertEqual(T.hairline_px(1.5), "1px")
        self.assertEqual(T.hairline_px(2.0), "0.5px")
        self.assertEqual(T.hairline_px(3.0), "0.5px")

    def test_panel_has_the_double_line_border(self):
        """双线边框在 QSS 里**真的写着**（`border-top` 在 `border` 简写之后）。"""
        q = T.qss(T.DARK)
        card = q[q.index("QFrame#Card"):]
        card = card[:card.index("}")]
        self.assertIn("border-top:1px solid", card,
                      "#Card 没有上缘亮边——双线边框没统一")
        # 顺序：border-top 必须出现在 border 简写之后（否则被重置，静默失效）
        self.assertGreater(card.index("border-top"), card.index("border:1px"),
                           "border-top 写在 border 简写之前——会被静默重置")


if __name__ == "__main__":
    unittest.main(verbosity=2)
