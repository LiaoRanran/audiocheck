"""B3 交互与动效：时长令牌、清理路径、触发清单、状态族 —— 全是静态可判据。

## 为什么动效值得一份独立测试

动效是**唯一一类"改错了不会报错、也不会崩"**的代码：时长写 260 而不是令牌，
界面照样动；动画对象没回收，内存只涨 2.4 MB（`mem_probe` 数出来的那次
泄漏，光看内存是看不见的）；状态族漏了 `:disabled`，输入框看起来只是
"颜色有点怪"。这些都得靠**判据**挡，靠肉眼评审挡不住。

本文件管三件事：

1. **时长**：动画时长只许来自 `theme.Motion`（或 `motion.FAST|BASE|SLOW`）；
   节流/去抖间隔**不是**动画时长，但必须具名（见 `Motion` 的 docstring）。
2. **清理路径**：全仓的动画对象只许在 `motion.py` 里创建——那是唯一有
   `isValid` + `deleteLater` 清理的地方。别处再造一个就是又开一条泄漏面。
3. **触发清单**：只允许 hover / focus / 状态变化 / 数据到达四类触发；
   禁止常驻动画（判据：所有 `QTimer` 都必须是单发的），
   且动效不许伸进图表模块（画布一次重画是百毫秒量级）。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

UI = os.path.join(_SRC, "audiocheck", "ui")
FILES = sorted(f for f in os.listdir(UI) if f.endswith(".py"))


def _code(fn: str) -> list[tuple[int, str]]:
    """(行号, 去注释与 docstring 的代码行)。行号保持**原始**行号。

    第一版按"先去掉 docstring 再 enumerate"写，行号整体前移了几十行——
    报出来的位置全是错的，反而比不报更费时间。
    """
    txt = open(os.path.join(UI, fn), encoding="utf-8").read()
    # 把 docstring 的内容替换成等长的空白（保住行号与列位置）
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))

    txt = re.sub(r'"""(?:.|\n)*?"""', blank, txt)
    txt = re.sub(r"'''(?:.|\n)*?'''", blank, txt)
    out = []
    for i, line in enumerate(txt.splitlines(), 1):
        j = line.find("#")
        out.append((i, line if j < 0 else line[:j]))
    return out


def _scan(pattern: str) -> list[str]:
    """在 ui/ 全部源文件里扫一个正则，返回 "文件:行  内容"。"""
    pat = re.compile(pattern)
    hits = []
    for fn in FILES:
        for i, line in _code(fn):
            if pat.search(line):
                hits.append("%s:%d  %s" % (fn, i, line.strip()[:78]))
    return hits


class TestDurationTokens(unittest.TestCase):
    """动效时长只许来自令牌。"""

    def test_no_handwritten_animation_duration(self):
        """`setDuration(数字)` 与 `motion.*(ms=数字)` 一律不许出现。

        "手写毫秒字面量"是散点，散落在八处之后，"调整体手感"就变成全局搜。
        本项目的先例：`Motion` 三个令牌曾经只是声明、没有落点。
        """
        bad = _scan(r"setDuration\(\s*\d")
        bad += _scan(r"\bms\s*=\s*\d+")
        # `ax.plot(..., ms=4)` 是 matplotlib 的 **marker size**，不是毫秒。
        # 第一次扫的时候它被当成违规报出来——判据要写准，否则它会被"顺手注释掉"。
        bad = [b for b in bad if "ax.plot" not in b and "plot(" not in b]
        self.assertEqual(bad, [], "出现了手写的动效时长：\n" + "\n".join(bad))

    def test_throttle_intervals_are_named(self):
        """节流/去抖间隔必须具名（不是"必须进 Motion 表"）。

        它们各自与一段具体性能事实绑定（28 ms = 人眼看不出合并、
        120 ms = 与文件到达同量级），搬到远处的令牌表里反而离家。
        但**裸数字**不行：看不见依据的数字会被随手改。
        """
        bad = _scan(r"setInterval\(\s*\d")
        self.assertEqual(bad, [], "出现了裸的节流间隔：\n" + "\n".join(bad))

    def test_all_animation_entry_points_use_the_table(self):
        """`motion` 的四个入口都吃 `ms`，且默认值必须是表里的值。"""
        from audiocheck.ui import motion
        from audiocheck.ui.theme import Motion
        self.assertEqual((motion.FAST, motion.BASE, motion.SLOW),
                         (Motion.fast, Motion.base, Motion.slow),
                         "motion 的时长别名与 Motion 表不一致")
        import inspect
        for fn in (motion.fade, motion.pulse, motion.stagger, motion.tween):
            sig = inspect.signature(fn)
            self.assertIn("ms", sig.parameters, "%s 没有 ms 参数" % fn.__name__)
            default = sig.parameters["ms"].default
            self.assertIn(default, (Motion.fast, Motion.base, Motion.slow),
                          "%s 的默认时长 %r 不在表里" % (fn.__name__, default))


class TestSingleCleanupPath(unittest.TestCase):
    """全仓的动画对象只许在 motion.py 里创建。"""

    def test_animations_are_only_created_in_motion(self):
        """别处再造 `QPropertyAnimation` 就是又开一条泄漏面。

        `motion._run` 是唯一挂 `isValid` + `deleteLater` 的地方。
        B1 结束时 Toast 与仪表环都还各自持有自建动画，`mem_probe` 的
        "动画数不增长"一条就是为这类问题设的。
        """
        bad = []
        for fn in FILES:
            if fn == "motion.py":
                continue
            for i, line in _code(fn):
                if re.search(r"QPropertyAnimation\(|QSequentialAnimationGroup\("
                             r"|QParallelAnimationGroup\(|QAbstractAnimation\b", line):
                    bad.append("%s:%d  %s" % (fn, i, line.strip()[:78]))
        self.assertEqual(bad, [], "motion 之外出现了自建动画：\n" + "\n".join(bad))

    def test_no_repeating_timers(self):
        """禁止常驻动画：所有 `QTimer` 都必须是单发的。

        常驻计时器是"动效变装饰"的入口——它会在没人看的时候持续吃 CPU，
        而本项目在自由线程解释器上单核性能只有常规桌面的 1/3（见 perf_probe 头部）。

        判据是"每个 `QTimer(` 后面 6 行内出现 `setSingleShot(True)`"：
        这是本仓一致的写法（三处合并计时器都这么写），偏离即失败。
        """
        bad = []
        for fn in ("window.py", "charts.py", "widgets.py", "material.py", "motion.py"):
            code = _code(fn)
            for idx, (i, line) in enumerate(code):
                if "QTimer(" not in line or "singleShot(" in line:
                    continue
                window = "\n".join(ln for _n, ln in code[idx:idx + 6])
                if "setSingleShot(True)" not in window:
                    bad.append("%s:%d  %s" % (fn, i, line.strip()[:70]))
        self.assertEqual(bad, [], "出现了可能常驻的计时器（没有设成单发）：\n"
                         + "\n".join(bad))

    def test_motion_never_touches_the_canvas_modules(self):
        """动效不许伸进 `charts.py`。

        画布一次重画是百毫秒量级（`perf_probe.py` 有数：频谱 90 ms /
        频谱图 470 ms / 瀑布 370 ms），拿它做动画会直接顶穿帧预算。
        这条是 `motion.py` 开头写明的那条边界，这里把它变成判据。
        """
        self.assertEqual(_scan(r"motion\.(fade|pulse|stagger|tween)\(")
                         and [b for b in _scan(r"motion\.(fade|pulse|stagger|tween)\(")
                              if b.startswith("charts.py")],
                         [], "charts.py 里出现了动效调用")


class TestTriggerWhitelist(unittest.TestCase):
    """触发清单封闭：只允许 hover / focus / 状态变化 / 数据到达。"""

    def test_pulse_is_used_at_exactly_one_place(self):
        """脉冲提醒只许用在**判定态**一处（有疑 / 不达标）。

        动效一旦变成常驻装饰就会持续吃 CPU，也会被习惯性忽略——
        "信息驱动"是它还有意义的前提。
        """
        calls = [b for b in _scan(r"motion\.pulse\(") if not b.startswith("motion.py")]
        self.assertEqual(len(calls), 1, "pulse 的调用点不是 1 处：%s" % calls)
        self.assertIn("window.py", calls[0], "pulse 用在了判定态之外的地方")

    def test_pulse_is_guarded_by_the_verdict_level(self):
        """它必须真的被"有疑 / 不达标"守着，而不是无条件脉。"""
        src = open(os.path.join(UI, "window.py"), encoding="utf-8").read()
        i = src.index("motion.pulse(")
        guard = src[max(0, i - 400):i]
        self.assertIn('lv in ("warn", "bad")', guard,
                      "pulse 前面没有判定态的守卫——它会变成常驻装饰")

    def test_stagger_is_used_for_content_arrival(self):
        """错峰浮现只用在"内容到达"（一屏卡片）。"""
        calls = [b for b in _scan(r"motion\.stagger\(") if not b.startswith("motion.py")]
        self.assertEqual(len(calls), 1, "stagger 的调用点不是 1 处：%s" % calls)
        self.assertIn("cards", calls[0], "stagger 用在了非内容到达的地方")


class TestStateFamily(unittest.TestCase):
    """QSS 状态族：每个交互控件该有的状态都得有。

    为什么用表而不是"看着补"：漏一个状态**不会报错**。少 `:disabled` 时，
    一个不可编辑的输入框与可编辑的长得一模一样，用户会去点它、然后以为界面卡了。
    """

    #: 选择器 → 必须出现过的状态。缺一个就失败。
    #: 只列**这个控件真的会经历**的状态：给标签页加 `:pressed` 是凑数。
    REQUIRED = {
        "#NavItem": (":hover", ":pressed"),
        "QPushButton": (":hover", ":pressed", ":disabled"),
        "QToolButton": (":hover", ":pressed", ":disabled"),
        "QLineEdit": (":focus", ":disabled"),
        "QComboBox": (":focus", ":disabled"),
        "QSpinBox": (":focus", ":disabled"),
        "QCheckBox": (":checked", ":disabled"),
        "QTabBar::tab": (":hover", ":selected", ":disabled"),
        "QMenu::item": (":selected", ":disabled"),
        "QScrollBar::handle": (":hover", ":pressed"),
        "#WinBtn": (":hover", ":pressed"),
        "#PaletteList::item": (":selected",),
    }

    def _rules(self):
        from audiocheck.ui import theme as T
        q = re.sub(r"/\*.*?\*/", "", T.qss(T.DARK), flags=re.S)
        return re.findall(r"([^{}]+)\{([^{}]*)\}", q)

    def test_every_interactive_selector_has_its_states(self):
        rules = self._rules()
        bad = []
        for sel, states in self.REQUIRED.items():
            sels = [s.strip() for s, _b in rules if sel in s]
            have = {st for st in states for s in sels if st in s}
            missing = [st for st in states if st not in have]
            if missing:
                bad.append("%s 缺 %s（现有规则 %d 条）" % (sel, " ".join(missing), len(sels)))
        self.assertEqual(bad, [], "状态族有缺口：\n" + "\n".join(bad))

    def test_no_empty_rule_bodies(self):
        """"空态扫描"：不许有声明为空的规则。

        `#Something:hover { }` 是一条**看起来做了、实际没有**的规则——
        它会在评审时让人以为这个状态处理过了。本项目对这类"静默无效"
        已经很熟：QSS 不支持 `line-height`、`font-variant-numeric` 时也是
        不报错、不生效。
        """
        bad = []
        from audiocheck.ui import theme as T
        for theme in (T.DARK, T.LIGHT):
            q = re.sub(r"/\*.*?\*/", "", T.qss(theme), flags=re.S)
            for sel, body in re.findall(r"([^{}]+)\{([^{}]*)\}", q):
                if not body.strip():
                    bad.append("%s：%r 是空规则" % (theme.name, sel.strip()[:40]))
        self.assertEqual(bad, [], "\n".join(bad))

    def test_pressed_states_are_not_identical_to_hover(self):
        """按下态必须与悬停态**不同**，否则按下没有任何反馈。

        这条挡的是"为了让状态族扫描通过，把 `:pressed` 写成与 `:hover`
        一模一样的规则"——扫描会过，反馈还是没有。
        """
        # 收集成 (选择器, 声明) 的**扁平**列表：早先写成 value 是 list 的 dict，
        # 集合推导解开的是 list、不可哈希，直接 TypeError。
        flat = [(sel.strip(), body.strip()) for sel, body in self._rules()]
        bad = []
        for want in ("#NavItem", "QPushButton", "QScrollBar::handle", "#WinBtn"):
            hover = {b for s, b in flat if want in s and ":hover" in s}
            pressed = {b for s, b in flat if want in s and ":pressed" in s}
            if hover and pressed and hover & pressed:
                bad.append("%s 的 :pressed 与 :hover 是同一份声明" % want)
        self.assertEqual(bad, [], "\n".join(bad))


if __name__ == "__main__":
    unittest.main(verbosity=2)
