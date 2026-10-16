"""主题一致性：生效的 QSS 与 `self.theme` 必须始终指同一个主题。

## 为什么需要

`_restore()` 曾经直接 `self.theme = T.THEMES[n]` 而不调 `apply_theme()`。
`__init__` 的顺序是 `apply_theme(DARK)` → `_restore()`，于是：

    生效的 QSS   = 深色（apply_theme(DARK) 设的）
    self.theme   = 浅色（_restore() 改的）

之后 `_cards()` 里 `StatCard(self.theme)` 拿**浅色**配色画在**深色**背景上：
数值渲成 `#141a26`（浅色主题的 text），背景 `#12151c` —— 几乎不可见。

用户直接可见，而且很难查：**界面能开、不报错、只是数字看不清**。
这份测试把"两个主题指示器必须一致"钉死，防止再有人直接赋值。

> P7 换肤攻坚（2026-10-16）后，QSS 的承载从 QApplication 移到
> MainWindow 窗口（实测窗口级 setStyleSheet 54ms vs 应用级
> 227–776ms——应用级会强制整个 app 的样式对象重建）。本文件的探针
> 随之从 `APP.styleSheet()` 改为 `w.styleSheet()`：不变量不变，
> 读取位置随架构口径更新。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import QCoreApplication, QEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402


class TestThemeConsistency(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def test_theme_attr_matches_qss_after_restore(self):
        """`self.theme` 必须与真正生效的 QSS 同源。

        判据：把 `self.theme` 的 text 色拿去 QSS 里查，必须出现。
        早先 `_restore()` 直接赋值时会失败——QSS 是深色、self.theme 是浅色。

        ⚠ P7 换肤攻坚后**探针从应用级改到窗口级**：样式表挂在 MainWindow
        上（实测窗口级 54ms vs 应用级 227–776ms），生效的 QSS 是
        `w.styleSheet()` 而不是 `APP.styleSheet()`。不变量本身没变，
        变的是承载位置（口径变更，非实现回归）。
        """
        w = self.w
        qss = w.styleSheet()
        self.assertIn(w.theme.p.text, qss,
                      "self.theme 的 text 色 %s 不在生效的 QSS 里 → "
                      "两个主题指示器不一致（_restore 直接赋值的老毛病）"
                      % w.theme.p.text)

    def test_apply_theme_is_the_only_way_to_switch(self):
        """切换主题必须同时改变量与 QSS —— 走 apply_theme 就自动满足。"""
        w = self.w
        for name in ("dark", "light"):
            w.apply_theme(T.THEMES[name])
            self.assertEqual(w.theme.name, name)
            self.assertIn(w.theme.p.text, w.styleSheet(),
                          "切到 %s 后窗口 QSS 没跟上" % name)
        w.apply_theme(T.DARK)          # 复位，避免影响其它测试

    def test_themes_are_distinguishable(self):
        """两个主题的 text 色必须不同，否则上面的判据失去意义。"""
        self.assertNotEqual(T.DARK.p.text, T.LIGHT.p.text)

    def test_state_card_colors_come_from_current_theme(self):
        """StatCard 的配色必须来自当前主题，不能是另一个主题的。"""
        from audiocheck.ui.widgets import StatCard
        w = self.w
        card = StatCard(w.theme)
        self.assertIn(w.theme.p.text, card.v.styleSheet())
        self.assertIn(w.theme.p.text_faint, card.k.styleSheet())

    def test_switch_repaints_inline_styled_widgets(self):
        """换肤必须重刷**自己 setStyleSheet** 的控件（指标卡 / 判定条）。

        这些控件的颜色不来自 QSS，而是各自 `setStyleSheet` 写死的。
        换肤时若不重刷，浅色主题下会留着深色主题的文字色：实测指标卡数值
        几乎看不见，而"切换主题"这个动作看起来是成功的——最能骗过眼睛的一类 bug。
        """
        from audiocheck.ui.widgets import StatCard
        w = self.w
        r = {"file": "a.wav", "path": "C:/a.wav", "sr": 44100, "bits": "PCM_16",
             "format": "WAV", "seconds": 12, "size_mb": 1.0, "channels": 2,
             "level": "ok", "verdict": "合格", "cutoff_khz": 20.0,
             "curve": [[20.0, -1.0], [20000.0, -2.0]]}
        w.items = [{"path": r["path"], "file": "a.wav", "result": r}]
        w._idx = {r["path"]: 0}
        w._last_sig = (r["path"], id(r))
        w.apply_theme(T.DARK)
        w._refresh_selection_theme()
        w.apply_theme(T.LIGHT)
        # 旧指标卡走 deleteLater()，而 processEvents 不派发延迟删除事件
        # （详见 packaging/mem_probe.py 头部）——不手动派发就会连旧卡片一起数进去。
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        cards = w.findChildren(StatCard)
        self.assertTrue(cards, "总览页应当有指标卡")
        self.assertTrue(all(T.LIGHT.p.text in c.v.styleSheet() for c in cards),
                        "切换主题后指标卡数值仍是旧主题的颜色")
        self.assertNotIn(T.DARK.p.text, w.lbl_verdict.styleSheet(),
                         "判定条文字仍是旧主题的颜色")
        w.items, w._idx, w._last_sig = [], {}, None
        w.apply_theme(T.DARK)

    def test_restore_does_not_bypass_apply_theme(self):
        """回归：`_restore` 里不能再出现裸的 `self.theme =` 赋值。"""
        import inspect
        src = inspect.getsource(W.MainWindow._restore)
        code = "\n".join(ln for ln in src.splitlines()
                         if not ln.strip().startswith("#"))
        self.assertNotIn("self.theme = T.THEMES", code,
                         "_restore 又直接给 self.theme 赋值了——必须走 apply_theme")
        self.assertIn("apply_theme", code)

    # ---------------------------------------------------------------- 装饰色阶

    def test_every_dialog_preset_builds(self):
        """对话框里每一个预设种子（含 ACG 那一排）都必须能生成。

        挡的是"加了个预设但它生成不达标"——那种失败只有用户点下去的那一刻
        才看得见，而那时他看到的是一句"换一个试试"。
        """
        from audiocheck.ui import palette as P
        from audiocheck.ui.widgets import SeedDialog
        presets = [h for h, _ in SeedDialog.PRESETS + SeedDialog.ACG_PRESETS]
        self.assertTrue(presets, "对话框应当有预设")
        for hexv in presets:
            for mode in ("dark", "light"):
                for vibe in ("calm", "pop"):
                    pal = P.build(hexv, mode, vibe)
                    self.assertEqual([f for f in P.audit(pal) if not f.ok], [],
                                     "%s / %s / %s 不达标" % (hexv, mode, vibe))
                    self.assertEqual([f for f in P.deco_checks(pal) if not f.ok], [],
                                     "%s / %s / %s 装饰色阶不可见" % (hexv, mode, vibe))

    def test_sidebar_nav_icons_use_the_deco_ramp(self):
        """侧边栏导航项按顺序取自装饰色阶。

        这是"五颜六色"在界面上最显眼的一处。并列关系不设主次，
        所以断言的是"按色阶顺序排"，而不是"谁该是哪个颜色"。
        色阶只有 6 档而导航项有 9 个，绕回是设计如此（同一档颜色
        在两个位置上重复，代表它们是同一类东西）。
        """
        w = self.w
        ramp = w.theme.p.deco
        accents = [b._accent for b in w.sidebar._items.values()]
        self.assertTrue(accents, "侧边栏应当有导航项")
        self.assertEqual(accents, [ramp[i % len(ramp)] for i in range(len(accents))],
                         "导航图标没有按装饰色阶顺序取色")
        self.assertGreaterEqual(len(set(accents[:len(ramp)])), 3,
                                "前几项就重复了，色阶没铺开")

    def test_metric_card_strip_is_not_deco(self):
        """指标卡的顶条**不许**再用装饰色阶（B7.1 判据 6）。

        这是从"有 deco 色条"改成"没有 deco 色条"的**反向断言**——留一条
        明确的反向测试，比删掉旧测试更有用：它挡的是"以后有人觉得卡条
        空着可惜，又把色阶接回去"。
        来历：18 张卡的色条连成 6 条横贯全屏的彩带，实测吃掉全图高彩的
        42.4%，却把主族占比压到 64.1%（判据要求 ≥85%）。
        """
        from audiocheck.ui.widgets import StatCard
        w = self.w
        r = {"file": "a.wav", "path": "C:/a.wav", "sr": 44100, "bits": "PCM_16",
             "format": "WAV", "seconds": 12, "size_mb": 1.0, "channels": 2,
             "level": "ok", "verdict": "合格", "cutoff_khz": 20.0,
             "curve": [[20.0, -1.0], [20000.0, -2.0]]}
        w.items = [{"path": r["path"], "file": "a.wav", "result": r}]
        w._idx = {r["path"]: 0}
        w._last_sig = (r["path"], id(r))
        w._cards(r)
        cards = w.findChildren(StatCard)
        self.assertTrue(cards, "总览页应当有指标卡")
        self.assertIsNone(cards[0].accent.property("slot"),
                          "卡条又按槽位取色了（B7.1 已把它改成中性顶光）")
        q = T.qss(w.theme)
        import re as _re
        for sel, body in _re.findall(r"([^{}]+)\{([^{}]*)\}", q):
            if "CardAccent" in sel:
                self.assertNotIn("deco", body,
                                 "CardAccent 的规则里还有 deco：%s" % sel.strip())
        w.items, w._idx, w._last_sig = [], {}, None
        w.apply_theme(T.DARK)

    def test_toggle_vibe_switches_between_calm_and_pop(self):
        """Ctrl+M 在克制/缤纷之间切换，且不改变深浅。

        断言用**表面彩度**而不是"看起来变了"：pop 的表面必须比 calm 更带色。
        （不能跟内置 DARK 比——内置主题本身就是 pop 档生成的。）
        """
        w = self.w
        before = (w.seed, w.seed_mode, w.seed_vibe, w.theme.name)
        try:
            w.toggle_vibe()
            self.assertEqual(w.seed_vibe, "pop")
            self.assertEqual(w.theme.name, "seed")
            self.assertEqual(w.seed_mode, "dark", "切风格档不该改变深浅")
            chroma_pop = _chroma(w.theme.p.surface)
            w.toggle_vibe()
            self.assertEqual(w.seed_vibe, "calm")
            self.assertGreater(chroma_pop, _chroma(w.theme.p.surface),
                               "切到 pop 后表面没有更带色")
        finally:
            w.seed, w.seed_mode, w.seed_vibe, _ = before
            w.apply_theme(T.THEMES[before[3]])


def _chroma(hexv: str) -> float:
    from audiocheck.ui.palette import srgb_to_oklch
    return srgb_to_oklch(hexv)[1]


if __name__ == "__main__":
    unittest.main(verbosity=2)
