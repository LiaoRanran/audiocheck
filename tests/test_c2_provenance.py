"""C2 / P0-2 回归：**每个阈值都要有出处，或者明说没有**。

## 为什么值得常驻

`AGENTS.md` §3.2：

> 每个数字标出处，二手来源标可信度。

内核审查报告（2026-10-13）P0-2 的实测是：`config.py` 的阈值**没有一个写了来源**。
本模块把这条规矩变成机器判据——否则它只是一句标语，下一轮加阈值的人照样不写。

## 四类，不许含糊

| 类别 | 含义 | 用户可见处 |
|---|---|---|
| `标准` | 可引用的公开标准文本 | 直接说 |
| `一手文档` | 厂商/平台的官方文档 | 直接说 |
| `二手` | 公开但非一手（转述、社区资料） | 带「（二手来源）」 |
| `工程估计` | **查不到出处，是本项目的取值** | 带「（工程估计，无出处）」 |

⚠ **"工程估计"不是失败**——它是**诚实**。本项目的立场是"没有出处的阈值，
宁可标'参考'也不标'判据'"。所以本模块不要求"必须有出处"，
只要求"**不许假装有出处**"：标 `标准`/`一手文档` 的必须写出**具体**来源。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck import config  # noqa: E402

_VALID = {"标准", "一手文档", "二手", "工程估计"}

#: 声称有出处时，依据里**必须出现**的"具体性"标志。
#: 写"见某处"、"网上"、"资料"这种含糊说法**不算出处**——那正是审查报告批评的形态。
_CONCRETE_HINTS = ("标准", "官方", "IEC", "ITU", "EBU", "AES", "ISO", "Tech", "Red Book",
                   "BS.", "R128", "文档", "支持页", "规范")


class TestProvenance(unittest.TestCase):

    def test_every_threshold_field_is_registered(self):
        """`Thresholds` 的**每个**字段都要有出处登记（哪怕是"工程估计"）。"""
        fields = set(config.Thresholds().__dict__)
        registered = set(config.PROVENANCE)
        missing = fields - registered
        self.assertEqual(missing, set(),
                         "有阈值字段没有出处登记——要么补出处，要么明确标'工程估计'：%s"
                         % sorted(missing))

    def test_no_stale_registration(self):
        """登记里不许留已经不存在的字段（登记表会因此慢慢失真）。"""
        fields = set(config.Thresholds().__dict__)
        stale = set(config.PROVENANCE) - fields
        self.assertEqual(stale, set(), "出处登记里有已不存在的字段：%s" % sorted(stale))

    def test_categories_are_valid(self):
        bad = [(f, c) for f, (c, _) in config.PROVENANCE.items() if c not in _VALID]
        self.assertEqual(bad, [], "出处类别不合法（只许 %s）：%s" % (sorted(_VALID), bad))

    def test_claimed_sources_are_concrete(self):
        """标 `标准` / `一手文档` 的，依据里必须出现**具体来源**。

        这条是防"假装有出处"：写"见资料"、"网上查的"不算。本项目此前在
        `科研学习报告` 里犯过"拿消费级资料当同行评审依据"的错，所以卡在这里。
        """
        bad = []
        for f, (cat, src) in config.PROVENANCE.items():
            if cat in ("标准", "一手文档"):
                if len(src) < 12 or not any(h in src for h in _CONCRETE_HINTS):
                    bad.append((f, cat, src[:50]))
        self.assertEqual(bad, [], "声称有出处但依据不够具体：%s" % bad)

    def test_engineering_estimates_say_so_explicitly(self):
        """标 `工程估计` 的，依据里必须**明说**没有外部出处。

        防的是"用一段像样的话把工程取值包装成有依据"——那比老实标"无出处"更坏。
        """
        bad = []
        for f, (cat, src) in config.PROVENANCE.items():
            # ⚠ **不许写"同上"**：看单行的人不该需要往上翻。实测抓到过 4 条。
            if "同上" in src:
                bad.append((f, "写了『同上』，依据不自足"))
                continue
            if cat == "工程估计" and "无外部出处" not in src and "未取到" not in src \
                    and "本项目取值" not in src and "工程取值" not in src \
                    and "工程判断" not in src and "工程经验" not in src \
                    and "本项目留" not in src and "本项目留的" not in src:
                bad.append((f, src[:50]))
        self.assertEqual(bad, [], "标了'工程估计'但没说清为什么没有出处：%s" % bad)

    def test_suffix_is_empty_only_for_solid_sources(self):
        """降级措辞：只有 `标准`/`一手文档` 才允许不加限定词。"""
        for f in config.PROVENANCE:
            cat = config.provenance(f)[0]
            suf = config.provenance_suffix(f)
            if cat in ("标准", "一手文档"):
                self.assertEqual(suf, "", "%s 是 %s，不该加限定词" % (f, cat))
            else:
                self.assertNotEqual(suf, "", "%s 是 %s，用户可见处必须带限定词" % (f, cat))

    def test_summary_counts_match(self):
        s = config.provenance_summary()
        self.assertEqual(sum(s["counts"].values()), len(config.PROVENANCE))
        self.assertEqual(s["total_fields"], len(config.Thresholds().__dict__))
        # 工程估计必须是**显式列出**的（供 CLI/报告使用），不是只给个数
        self.assertEqual(sorted(s["engineering_estimates"]),
                         sorted(f for f, (c, _) in config.PROVENANCE.items()
                                if c == "工程估计"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
