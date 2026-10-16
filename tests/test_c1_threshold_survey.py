"""C1 / P0-1 回归：**每个阈值都必须有区分力**。

## 为什么值得常驻

`AGENTS.md` §3.4 的规矩是"新加任何阈值都要跑一次区分力检验"，
而本项目此前**一次都没跑过**（内核审查报告 P0-1）。规矩不落到测试上就只是标语：
下一轮加阈值的人照样不会跑。

这里把 `packaging/threshold_survey.py` 挂进测试——**任一新阈值若在语料上
全落一侧，当场失败**。实测耗时约 19 s（44 个合成文件 × 全量分析），可接受。

## 语料是合成集，不是真实曲库

`examples/` 里没有音频，所以普查**按每个阈值的两侧定向造样**
（带限 / 削波 / 近单声道 / 低 DR / 低 LRA / 位深填充 / 频段偏置…）。
合成文件写在临时目录，**不入库**。

⚠ 这条测试失败时**先怀疑语料，再怀疑阈值**：本项目实测的三次"无区分力"里，
**三次都是语料没造对**（详见 `docs/thresholds.md` 的踩坑记录）。
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)


class TestEveryThresholdDiscriminates(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        import threshold_survey
        cls.S = threshold_survey
        sys.argv = [sys.argv[0]]        # main() 用 argparse，清掉 pytest 的参数
        buf = io.StringIO()
        with redirect_stdout(buf):
            cls.rc = threshold_survey.main()
        cls.out = buf.getvalue()

    def test_corpus_is_not_empty(self):
        self.assertIn("成功分析", self.out)
        self.assertNotIn("成功分析 0", self.out, "语料一个都没分析成功")

    def test_no_field_is_blind(self):
        """**核心判据**：不许有任何字段"全落在一侧"。

        若这条挂了：先按 `docs/thresholds.md` 的踩坑记录查**语料**（三次都是语料错），
        确认语料覆盖了两侧之后再怀疑阈值本身。
        """
        blind = [ln for ln in self.out.splitlines() if "无区分力" in ln]
        self.assertEqual(blind, [], "有阈值在语料上判不出差别（先查语料覆盖）：\n  "
                         + "\n  ".join(blind))

    def test_cutoff_is_detected_on_all_synthetic_files(self):
        """检出率必须 100%：合成文件都是"有明确截止"的形态，检不出就是算法问题。"""
        line = [ln for ln in self.out.splitlines() if "截止点检出率" in ln]
        self.assertTrue(line)
        self.assertIn("100.0%", line[0], "有合成文件的截止点没被检出：%s" % line[0])

    def test_every_threshold_field_is_covered_by_the_map(self):
        """`Thresholds` 的每个字段都要在普查里有交代（映射或明确标为算法参数）。

        防的是"新增了阈值字段，但普查没跟上"——那样新阈值永远不会被检验，
        而这条规矩就白立了。
        """
        from audiocheck.config import Thresholds
        fields = {f for f in Thresholds().__dict__}
        covered = set(self.S.FIELD_MAP)
        missing = fields - covered
        self.assertEqual(missing, set(),
                         "Thresholds 里有字段没进普查映射（判定阈值要进，"
                         "算法参数要显式标 None）：%s" % sorted(missing))

    def test_negative_controls_prove_the_check_has_teeth(self):
        """**检验的检验**：这把尺子必须能报出明显的单侧/无样本。

        背景：本检查此前**从未命中过**——因为语料是按"跨每个阈值两侧"手工造的，
        "有区分力"这个结论由构造保证 ⇒ 检查无失败模式。`NEGATIVE_CONTROLS`
        用两条**不可能有区分力**的规格补上失败模式；它们若报不出来，
        说明检查恒真，"全部字段有区分力"就是空话。
        """
        self.assertIn("负面对照", self.out)
        self.assertIn("有牙齿", self.out, "负面对照没跑或没通过")
        self.assertNotIn("负面对照未通过", self.out, "尺子坏了：报不出单侧")
        self.assertEqual(self.rc, 0, "负面对照失败时应返回 1")

    def test_negative_control_specs_are_really_one_sided(self):
        """直接量 `_split`：对照规格在语料上确实只能落一侧（不是靠运气）。"""
        rows = []
        # 造两条最小样本，只测分流逻辑本身
        for v in (16.0, 19.0):
            rows.append({"_obs": {"cutoff_khz": v}})
        hi, lo, n = self.S._split("cutoff_khz", ">=", 999.0, rows)
        self.assertEqual((hi, n), (0, 2), "不可能达到的阈值却有人触发")
        hi2, lo2, n2 = self.S._split("__missing__", ">=", 0.0, rows)
        self.assertEqual(n2, 0, "不存在的观测量应给出 0 个有效样本")


if __name__ == "__main__":
    unittest.main(verbosity=2)
