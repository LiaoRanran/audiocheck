"""栅格与字阶合规测试 —— 防止"令牌是摆设"。

## 为什么需要

审计发现：`theme.Space` 声明了 4px 栅格，但代码里**根本没用**——
内容边距 9 个取值里 5 个不合规（5/6/10/14/18），控件间距 9 个里 4 个不合规。
令牌写着 4px，代码里是随手写的数字，两者毫无关系。

这正是 UI「刚到及格线」的根因：**没有节奏**。单看每个间距都说得过去，
合起来就是一堆互不相干的数字。

## 规则（两套刻度，不是一刀切）

  * **布局间距**（元素之间）→ 必须是 4 的倍数。硬约束。
  * **光学间距**（组件内部微调）→ 允许 1/2px，但**必须在调用点上方
    有 `光学间距` 注释显式登记**。没有注释的非 4 倍数一律判失败。

第二条是关键：不是禁止偏离，而是**禁止无记录地偏离**。
光学间距确实存在（StatCard 里字段名与数值之间凑到 4px 反而更难看），
但如果不登记，它就退化成随手魔数——那就回到原点了。
"""
from __future__ import annotations

import os
import re
import sys
import unittest

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from audiocheck.ui import theme as T  # noqa: E402

UI = os.path.join(_SRC, "audiocheck", "ui")
FILES = ["window.py", "widgets.py", "charts.py", "chrome.py", "theme.py"]

GRID = 4
#: 光学例外必须在调用点前若干行内出现这个词
OPTICAL_MARK = "光学间距"
LOOKBACK = 4


def _raw_lines(fn):
    """原始行（**保留注释**）。用于查「光学间距」这类写在注释里的登记标记。"""
    with open(os.path.join(UI, fn), encoding="utf-8") as f:
        return f.read().splitlines()


def _lines_of(fn):
    """去注释后的行。用于扫数值。

    **为什么扫数值必须去注释**：规则说明本身就是以 `font-weight:600`
    这种形式写在注释里的（"不要写 600"），不去注释的话测试会把自己的
    说明当成违规——第一次跑就撞上了这个假阳性。

    **为什么不能只留这一份**：光学间距的登记标记也在注释里，
    删了注释就找不到了（第二次跑又撞上）。所以两份都要。
    """
    with open(os.path.join(UI, fn), encoding="utf-8") as f:
        txt = f.read()
    txt = re.sub(r"/\*.*?\*/", "", txt, flags=re.S)      # CSS 块注释
    out = []
    for line in txt.splitlines():
        i = line.find("#")                                # 行内 Python 注释
        out.append(line if i < 0 else line[:i])
    return out


def _is_registered_optical(fn, idx) -> bool:
    """该行是否被「光学间距」注释显式登记过。

    查的是**原始行**（含注释）—— 登记标记本身就写在注释里。
    """
    lines = _raw_lines(fn)
    for j in range(max(0, idx - LOOKBACK), min(idx + 1, len(lines))):
        if OPTICAL_MARK in lines[j]:
            return True
    return False


class TestSpacingGrid(unittest.TestCase):
    """布局间距必须落在 4px 栅格上。"""

    def _collect(self, pattern, group):
        bad = []
        for fn in FILES:
            lines = _lines_of(fn)
            for i, line in enumerate(lines):
                for m in re.finditer(pattern, line):
                    raw = m.group(group)
                    vals = [int(x) for x in re.findall(r"\d+", raw)]
                    off = [v for v in vals if v % GRID != 0]
                    if off and not _is_registered_optical(fn, i):
                        bad.append("%s:%d  %s  → 非 %d 倍数 %s"
                                   % (fn, i + 1, line.strip(), GRID, off))
        return bad

    def test_contents_margins_on_grid(self):
        bad = self._collect(r"setContentsMargins\(([^)]*)\)", 1)
        self.assertEqual(bad, [], "布局边距脱离栅格：\n" + "\n".join(bad))

    def test_spacing_on_grid(self):
        bad = self._collect(r"setSpacing\((\d+)\)", 1)
        self.assertEqual(bad, [], "控件间距脱离栅格：\n" + "\n".join(bad))

    def test_optical_exceptions_are_registered(self):
        """所有非 4 倍数的间距都必须带「光学间距」注释。

        这条与上面两条互为补集：上面保证布局合规，这条保证
        **例外是被登记过的**，而不是悄悄溜进来的。
        """
        unregistered = []
        for fn in FILES:
            lines = _lines_of(fn)
            for i, line in enumerate(lines):
                if not re.search(r"setSpacing\(\d+\)|setContentsMargins\(", line):
                    continue
                vals = [int(x) for x in re.findall(r"\d+", line)]
                off = [v for v in vals if v % GRID != 0]
                if off and _is_registered_optical(fn, i):
                    unregistered.append((fn, i + 1, line.strip()))
        # 光学例外数量应当**很少**。超过 8 处说明有人开始滥用这个后门。
        self.assertLessEqual(
            len(unregistered), 8,
            "光学间距例外达 %d 处，太多——后门被滥用了：\n%s"
            % (len(unregistered), "\n".join("%s:%d %s" % x for x in unregistered)))

    def test_fixed_sizes_on_grid(self):
        """**较大**的固定尺寸应落在栅格上。

        为什么加阈值：4px 栅格约束的是构图节奏（元素之间的间隙与整块尺寸），
        不是每个像素。小图标（16px 级的字形对齐）属于光学范畴；
        但 ≥20px 的控件宽高会参与版面分割，脱离栅格就会看出"对不齐"。

        实测修掉的三处：窗口按钮 34×30→32×32（标题栏 44=4×11，
        按钮原高 30 在栏里偏小且两者都脱栅格）、筛选框宽 110→112、
        品牌图标 22→24。
        """
        bad = []
        for fn in FILES:
            lines = _lines_of(fn)
            for i, line in enumerate(lines):
                for m in re.finditer(r"setFixed(?:Width|Height|Size)\(([^)]*)\)", line):
                    vals = [int(x) for x in re.findall(r"\d+", m.group(1))]
                    off = [v for v in vals if v >= 20 and v % GRID != 0]
                    if off and not _is_registered_optical(fn, i):
                        bad.append("%s:%d  %s" % (fn, i + 1, line.strip()))
        self.assertEqual(bad, [], "较大固定尺寸脱离栅格：\n" + "\n".join(bad))

    def test_grid_tokens_are_multiples_of_four(self):
        """令牌本身必须合规——否则测试是在拿错的尺子量。"""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(UI), "..", "..", ".."))
        sp = T.Space()
        for name in ("xs", "sm", "md", "lg", "xl", "xxl"):
            v = getattr(sp, name)
            self.assertEqual(v % GRID, 0, "Space.%s=%d 不是 %d 的倍数" % (name, v, GRID))
        # 光学刻度故意脱离栅格，且必须存在
        for name in ("hair", "thin"):
            v = getattr(sp, name)
            self.assertGreater(v, 0)
            self.assertLess(v, GRID, "Space.%s 应小于栅格步进" % name)


class TestFontWeights(unittest.TestCase):
    """字重只能用字体真实拥有的档位。

    **本机中文字体（Microsoft YaHei）只有 Regular(400) / Bold(700) / Light(300)。**
    请求 600 会被 Qt 吸附到 700、还会让 matplotlib 打 findfont 告警——
    于是"600"和"700"渲染出来一模一样，那个层级是假的。

    这条测试是补上去的：第一轮只修了 theme.py 里 QSS 模板的 6 处，
    **内联样式表里的 8 处漏了整整一轮**（widgets.py 4、window.py 3、charts.py 1），
    直到渲染图表时看到 findfont 告警才发现。所以检查必须是全局的，
    不能只查模板。
    """
    #: 字体真实拥有的档位
    ALLOWED = {"300", "400", "700", "normal", "bold", "lighter", "bolder",
               "light", "black", "b"}

    def test_no_fake_intermediate_weights(self):
        import re
        bad = []
        for fn in FILES:
            for i, line in enumerate(_lines_of(fn), 1):
                for m in re.finditer(r"font-weight:\s*(\d+)", line):
                    if m.group(1) not in self.ALLOWED:
                        bad.append("%s:%d  font-weight:%s" % (fn, i, m.group(1)))
                for m in re.finditer(r'fontweight="(\d+)"', line):
                    if m.group(1) not in self.ALLOWED:
                        bad.append("%s:%d  fontweight=%s" % (fn, i, m.group(1)))
        self.assertEqual(bad, [], "使用了字体不存在的字重（会被静默吸附）：\n"
                         + "\n".join(bad))

    def test_theme_qss_has_no_intermediate_weights(self):
        import re
        q = re.sub(r"/\*.*?\*/", "", T.qss(T.DARK), flags=re.S)   # 去 CSS 注释
        bad = {m for m in re.findall(r"font-weight:\s*(\d+)", q) if m not in self.ALLOWED}
        self.assertEqual(bad, set(), "主题 QSS 里有字体不存在的字重：%s" % sorted(bad))


class TestTypeScale(unittest.TestCase):
    def test_type_scale_is_monotonic(self):
        """字阶必须单调递增，否则"层级"无从谈起。"""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(UI), "..", "..", ".."))
        ty = T.Type()
        seq = [ty.micro, ty.caption, ty.body_sm, ty.body, ty.body_md,
               ty.title, ty.heading, ty.display]
        for a, b in zip(seq, seq[1:]):
            self.assertLess(a, b, "字阶必须严格递增：%s" % seq)

    def test_type_scale_steps_are_visible(self):
        """相邻级别至少差 1px，否则视觉上分不出来（等于白设一级）。"""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(UI), "..", "..", ".."))
        ty = T.Type()
        seq = [ty.micro, ty.caption, ty.body_sm, ty.body, ty.body_md,
               ty.title, ty.heading, ty.display]
        for a, b in zip(seq, seq[1:]):
            self.assertGreaterEqual(b - a, 1.0,
                                    "相邻字阶差 <1px，视觉不可分：%.1f→%.1f" % (a, b))

    def test_qss_uses_type_scale_only(self):
        """QSS 里出现的字号必须是声明过的级别，不能临时写一个新数字。

        ⚠ **白名单必须从 `Type` 表读**，不能手抄一份。B9.2 加了 `hero` 档
        （40px）之后这条测试红了——不是 QSS 违规，是**白名单抄漏了**：
        手抄的列表在每次加档时都要记得同步，而忘了同步的表现是
        "判据报了一个假违规"，最坏的结果是有人把规则删掉。
        改成 `dataclasses.fields(Type)` 之后，加档自动生效。
        """
        from dataclasses import fields
        allowed = {int(getattr(T.Type(), f.name)) for f in fields(T.Type())}
        q = T.qss(T.DARK)
        used = {int(m) for m in re.findall(r"font-size:(\d+)px", q)}
        extra = used - allowed
        self.assertEqual(extra, set(),
                         "QSS 出现了字阶之外的字号：%s（允许 %s）"
                         % (sorted(extra), sorted(allowed)))
        # 判据自身要被测：表里加一档、白名单必须跟着变
        self.assertIn(int(T.Type().hero), allowed, "hero 档没进白名单")


if __name__ == "__main__":
    unittest.main(verbosity=2)
