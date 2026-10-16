"""字体：**按内容选**，以及"不许把中文推给逐字形回退"。

## 用户报的"字乱码"是怎么来的

等宽字体族（Consolas / DejaVu Sans Mono）**没有汉字字形**。中文落在它上面，
Qt 只能逐字形回退——落到哪个字体不可控，部分机器上直接是方框。
出事的两处都是"一刀切"：

1. `QTableWidget { font-family:{mono} }`：整张表等宽，而文件表有中文文件名、
   元数据表更是整页中文；
2. `KVTable.set_rows()` 按**键名**判断是否等宽——给中文**值**套了等宽字体。

修法：字体由 `theme.pick_text_font(text, …)` **按值的实际内容**决定
（纯 ASCII → 等宽，含 CJK → 中文比例字体），QSS 里不再对承载中文的容器设族名。
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtGui import QFont  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

# **必须先有 QApplication**：`T.init_fonts()` 会走 `QFontDatabase`，
# 而没有 QApplication 时那个调用是在 C 层直接 abort —— 进程终止、无 traceback
# （接手文档 §9 记过同样的坑；本文件第一次跑就是这么死的）。
APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402


def _is_mono(f: QFont) -> bool:
    return f.fixedPitch() or f.styleHint() == QFont.Monospace


class TestPickByContent(unittest.TestCase):
    def test_ascii_numbers_get_mono(self):
        for text in ("44.1 kHz", "-14.5 dB", "0.000%", "+2.35 dBTP", "[OK]"):
            self.assertTrue(_is_mono(T.pick_text_font(text, 12.0)), text)

    def test_cjk_text_gets_proportional(self):
        for text in ("不达标", "某电子厂", "编码器串", "合格"):
            self.assertFalse(_is_mono(T.pick_text_font(text, 12.0)), text)

    def test_mixed_text_follows_the_cjk_rule(self):
        """一个汉字就足以推翻等宽：混排里中方不在等宽族上，方框就没了。"""
        self.assertFalse(_is_mono(T.pick_text_font("flac · 16 位", 12.0)))

    def test_size_is_applied(self):
        self.assertAlmostEqual(T.pick_text_font("中文", 11.5).pointSizeF(), 11.5, places=2)
        self.assertAlmostEqual(T.pick_text_font("123", 11.5).pointSizeF(), 11.5, places=2)


class TestNoBlanketMono(unittest.TestCase):
    """QSS 模板里不许给"会承载中文"的容器设字体族。"""

    def _blocks(self):
        src = inspect.getsource(T.qss)
        return {m.group(1): m.group(2)
                for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", src)}

    def test_table_does_not_force_mono(self):
        for sel, body in self._blocks().items():
            if "QTableWidget" in sel or "QHeaderView" in sel:
                self.assertNotIn("font-family", body,
                                 "表格被整体设了字体族：中文文件名/元数据会走逐字形回退")

    def test_hero_value_does_not_force_mono(self):
        for sel, body in self._blocks().items():
            if "Hero" in sel:
                self.assertNotIn("font-family", body,
                                 "Hero 数值被 QSS 固定族名：厂牌/编码器串里的中文会变方框")


class TestRuntimeFontFamily(unittest.TestCase):
    """QSS 的字体族必须在**运行时解析**，且不许写假回退链。

    用户报的"整屏中文方框"就是这个：主题里写死 `"Microsoft YaHei UI","Microsoft YaHei",…`
    而 QSS 的 `font-family` **不支持多族回退**——族名对不上时 Qt 回退到默认字体，
    那个默认字体没有汉字字形，于是**全屏中文变方框**（离屏截图里复现过）。
    """

    def test_qss_has_no_fake_fallback_chain(self):
        for theme in (T.DARK, T.LIGHT):
            for line in T.qss(theme).splitlines():
                if "font-family" in line:
                    self.assertNotIn('","', line,
                                     "QSS 里出现了多族链（假回退）：%s" % line.strip())

    def test_runtime_family_overrides_the_builtin_default(self):
        old = T.ui_family()
        try:
            T.set_ui_family("Microsoft YaHei")
            self.assertIn('"Microsoft YaHei"', T.qss(T.DARK))
            self.assertNotIn('"Microsoft YaHei UI"', T.qss(T.DARK))
        finally:
            T.set_ui_family(old)

    def test_init_fonts_wires_all_three_steps(self):
        """B14 §4-①：契约**变了**，所以这条跟着重定（改判据报数）。

        旧契约（B9）：`init_fonts` 解析出中文字体 → `set_ui_family` 写进令牌 →
        QSS 里出现那个族名。三步都对，但它**只覆盖中文字体**：西文/数字仍在
        系统字体上，于是"字体在场"这件事从来没被断言过（B10/B10.5 的零字体教训）。

        新契约（B13/B14）：
          ① 装载 `assets/fonts/` 的 OFL 字体，**断言三族在手**（缺族 fail loudly）；
          ② `ui_family()` == `init_fonts()` 返回值 == app 主族（**唯一真源**）；
          ③ app 字体走 `setFamilies([Plex, Noto])` 真回退链，而 **QSS 不写
             font-family**——QSS 的单族名会覆盖 app 的回退链，把中文推给逐字形
             回退（不可控）。所以"QSS 无假多族链"= **没有 font-family**。
        """
        fam = T.init_fonts(APP)
        self.assertTrue(fam, "没解析出字体族")
        self.assertEqual(T.ui_family(), fam, "ui_family() 与 init_fonts() 不一致——"
                                             "契约要求唯一真源")
        # app 主族必须真的是它（不是"令牌写了但 app 没设"）
        self.assertEqual(APP.font().families()[0] if APP.font().families() else "",
                         T.BUNDLED_FAMILIES[0], "app 主族不是 Plex")
        # QSS 不许出现假多族链：要么没有 font-family（装载成功时），
        # 要么只有单族（降级路径）——**绝不许出现逗号分隔的多族**
        q = T.qss(T.DARK)
        self.assertNotRegex(q, r"font-family:\s*[^;]*,",
                            "QSS 里出现了多族假链（Qt 不可靠）")
        # 三族在场（这条才是"零字体教训"的真修）
        from PySide6.QtGui import QFontDatabase
        have = set(QFontDatabase.families())
        for want in T.BUNDLED_FAMILIES:
            self.assertIn(want, have, "发货字体缺族：%s" % want)


class TestConsoleEncoding(unittest.TestCase):
    """CLI 的编码兜底：宁可有问号，也不能因为编不出来就崩。"""

    def test_setup_console_is_idempotent_and_safe(self):
        from audiocheck import cli

        cli._setup_console()
        cli._setup_console()          # 重复调用不该出事（入口可能被多次调用）
        self.assertIsNotNone(sys.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
