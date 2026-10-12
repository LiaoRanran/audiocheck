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

# 必须在导入任何 Qt 模块**之前**设好。offline 模式下 Qt 会去连显示器，
# 没有就硬崩（实测：进程直接 abort，连 traceback 都打不全）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from audiocheck.ui import theme as T  # noqa: E402

UI = os.path.join(_SRC, "audiocheck", "ui")
FILES = ["window.py", "widgets.py", "charts.py", "chrome.py"]

GRID = 4
#: 光学例外必须在调用点前若干行内出现这个词
OPTICAL_MARK = "光学间距"
LOOKBACK = 4


def _lines_of(fn):
    with open(os.path.join(UI, fn), encoding="utf-8") as f:
        return f.read().splitlines()


def _is_registered_optical(lines, idx) -> bool:
    """该行是否被「光学间距」注释显式登记过。"""
    for j in range(max(0, idx - LOOKBACK), idx + 1):
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
                    if off and not _is_registered_optical(lines, i):
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
                if off and _is_registered_optical(lines, i):
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
                    if off and not _is_registered_optical(lines, i):
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
        """QSS 里出现的字号必须是声明过的级别，不能临时写一个新数字。"""
        import sys
        sys.path.insert(0, os.path.join(os.path.dirname(UI), "..", "..", ".."))
        ty = T.Type()
        allowed = {int(x) for x in (
            ty.micro, ty.caption, ty.body_sm, ty.body, ty.body_md,
            ty.title, ty.heading, ty.display)}
        q = T.qss(T.DARK)
        used = {int(m) for m in re.findall(r"font-size:(\d+)px", q)}
        extra = used - allowed
        self.assertEqual(extra, set(),
                         "QSS 出现了字阶之外的字号：%s（允许 %s）"
                         % (sorted(extra), sorted(allowed)))


if __name__ == "__main__":
    unittest.main(verbosity=2)
