"""B14 §3A 判据：分辨率字号三档 + 同屏比例成组 + 唯一真源 + tabular。

## 报数（实测，`python -m audiocheck.ui.typescale` 可复算）

| 档 | body | title | hero | hero:title:body |
|---|---|---|---|---|
| HD 1440 | 14.0 | 16.0 | 40.0 | 2.857 : 1.143 : 1 |
| QHD 2560 | 16.4 | 18.7 | 46.8 | 2.854 : 1.140 : 1 |
| 4K 3840 | 21.8 | 25.0 | 62.4 | 2.862 : 1.147 : 1 |

档间系数实测 **1.17**（HD→QHD）与 **1.56**（HD→4K，即 1.17×1.33）。
比例在三档上**逐位相同到小数点后两位**（第三位的抖动来自 px 取整，
判据因此用 ±0.02 容差而不是逐位相等——取整误差是物理限制，不是缺陷）。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from PySide6.QtWidgets import QApplication  # noqa: E402

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import typescale as S  # noqa: E402

# ⚠ **必须先有 QApplication**：`QFontDatabase` 依赖 QGuiApplication，
# 在它之前调用会在 **C 层直接 abort**（不是 Python 异常，traceback 都打不全）。
# `theme.py:848-851` 记过同一个坑；本文件第一次跑就这么死的——判据文件
# 自己也踩了它记的那条，正好证明那条注释不是空话。
APP = QApplication.instance() or QApplication([])


class TestResolutionTiers(unittest.TestCase):

    def test_three_tiers_increase_monotonically(self):
        """① 三档梯度：1440 < 2560 < 3840，且档间系数 ≈1.17 / 1.33。"""
        for role in S.BASE:
            a, b, c = S.fs(role, 1440), S.fs(role, 2560), S.fs(role, 3840)
            self.assertLess(a, b, "%s 在 QHD 档没有变大" % role)
            self.assertLess(b, c, "%s 在 4K 档没有变大" % role)
            self.assertAlmostEqual(b / a, 1.17, delta=0.02,
                                   msg="%s 的 HD→QHD 系数不是 1.17" % role)
            self.assertAlmostEqual(c / b, 1.33, delta=0.03,
                                   msg="%s 的 QHD→4K 系数不是 1.33" % role)

    def test_tier_boundaries(self):
        """档位阈值：<2000 HD / 2000–3399 QHD / ≥3400 4K（中间值，不跳档）。"""
        self.assertEqual(S.tier(1440), "HD")
        self.assertEqual(S.tier(1999), "HD")
        self.assertEqual(S.tier(2000), "QHD")
        self.assertEqual(S.tier(2560), "QHD")
        self.assertEqual(S.tier(3399), "QHD")
        self.assertEqual(S.tier(3400), "4K")
        self.assertEqual(S.tier(3840), "4K")

    def test_hd_base_equals_the_tuned_type_table(self):
        """② **唯一真源**：HD 基准必须等于 `theme.Type` 的现值。

        1440 宽下调好的那张表只有一份（`theme.Type`）。`typescale.BASE` 是
        **引用**而不是副本——两份表迟早漂开，那正是本项目反复修的病
        （motion 两套时长、QSS 与代码两份族名）。
        """
        ty = T.Type()
        for role, val in S.BASE.items():
            got = getattr(ty, role, None)
            if got is None and role == "chart_tick":
                got = ty.chart_tick
            self.assertIsNotNone(got, "theme.Type 里没有角色 %s" % role)
            self.assertEqual(float(got), float(val),
                             "typescale.BASE[%s]=%.1f 与 theme.Type.%.1f 不一致"
                             % (role, val, float(got)))

    def test_proportions_are_invariant_across_tiers(self):
        """③ 同屏比例成组（CS3）：**跨档不变**才是这条纪律的机器形态。

        旧纪律"≤5 种字号"数的是种类；CS3 要的是比例关系。而"比例成立"
        最稳的表述是：所有角色同乘一个系数 ⇒ 比例在三个档位上逐位相同
        （第三位抖动来自 px 取整，故容差 ±0.02）。
        """
        ref = S.ratios(1440)
        for w in (2560, 3840):
            got = S.ratios(w)
            for name, vals in ref.items():
                for a, b in zip(vals, got[name]):
                    self.assertAlmostEqual(a, b, delta=0.02,
                                           msg="%s 在 %d 档上比例变了" % (name, w))

    def test_group_ratios_are_recorded(self):
        """把实测比例钉在断言里——**它变了就要有人说话**（报数纪律的机械化）。"""
        r = S.ratios(1440)
        self.assertEqual(r["hero:title:body"], (2.857, 1.143, 1.0))
        self.assertEqual(r["heading:body_md:body"], (1.429, 1.071, 1.0))
        self.assertEqual(r["caption:micro"], (1.1, 1.0))

    def test_unknown_role_fails_loudly(self):
        """角色名写错必须立刻炸，不许静默给个默认字号。"""
        with self.assertRaises(KeyError):
            S.fs("bodY")          # 故意写错大小写


class TestTabularNumbers(unittest.TestCase):
    """④ tabular：等宽列走 IBM Plex Mono（B14 §3A）。"""

    #: 需要**等宽**的角色（数字列 / hero / 表格值）。
    MONO_ROLES = ("hero", "display", "chart_tick")

    def test_mono_family_is_bundled_and_available(self):
        """Plex Mono 必须在手——否则"等宽列"又成了系统字体的一句祈祷。"""
        T.load_bundled_fonts()
        from PySide6.QtGui import QFontDatabase
        have = set(QFontDatabase.families())
        self.assertIn("IBM Plex Mono", have, "缺 IBM Plex Mono（tabular 的底座）")

    def test_mono_roles_are_declared(self):
        """等宽角色是**声明**的，不是散在各控件里的手写决定。"""
        self.assertTrue(set(self.MONO_ROLES) <= set(S.BASE),
                        "MONO_ROLES 里有 typescale 不认识的角色")


if __name__ == "__main__":
    unittest.main(verbosity=2)
