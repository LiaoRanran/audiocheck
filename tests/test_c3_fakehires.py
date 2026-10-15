"""C3 回归：假 Hi-Res 盲检测 —— **钉住负结果**。

## 这个测试文件的立场

C3 的交付是**一个负结论**：NYCU 2017 的差分对数谱特征，在本项目的自建合成验证集上
**不构成 `cutoff_hz` 之外的增量，且跨内容类型不泛化**。

把负结论钉成回归，是因为它比正结论更容易被下一次"想加智能"的冲动抹掉：

* 有人看到 `--only fakehires` 会想"打开试试" ⇒ 第一条判据保证它**默认关着**；
* 有人想"再调调特征" ⇒ 第二条判据给出**实测的判别依据**：
  真正分开两类的是"22 kHz 以上有没有真实能量"——而那正是 `cutoff_hz` 已在测的事。

## 复算

`python packaging/fakehires_eval.py`（造验证集、训练、打印混淆矩阵与系数）。
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "packaging"))


class TestFakeHiresNegativeResult(unittest.TestCase):
    """交付物是**负结论**，所以判据反过来写：保证它不会被当成正结论用。"""

    def test_analyzer_is_disabled_by_default(self):
        """❦ 默认关着 —— 输出一个测试集误报率 72% 的分类器比没有它更糟。"""
        from audiocheck.core.analyzers import fakehires
        self.assertFalse(fakehires.FakeHiresAnalyzer.enabled,
                         "C3 已判定『不构成截止点之外的增量且跨类型不泛化』，"
                         "分析器不该默认启用")

    def test_disabled_reason_is_recorded_in_source(self):
        """关掉的理由必须**写在源码里**（不许只写在提交信息/报告里）。"""
        import inspect

        from audiocheck.core.analyzers import fakehires
        src = inspect.getsource(fakehires)
        self.assertIn("enabled = False", src)
        self.assertIn("0.722", src, "关掉的理由里必须带上实测误报率，否则后人无法判断")

    def test_what_actually_discriminates_is_hi_band_energy(self):
        """★ 这一条给出**实测的判别依据**，是"别再调了"的证据。

        对三种内容类型（粉噪/白噪/类音乐），96 kHz 容器下：
        真的高解析内容在 25 kHz 以上有真实能量；升频得来的没有 —— 差 2～3 个量级。
        而 `cutoff_hz` 已经在测这件事。
        """
        import fakehires_eval as E
        import numpy as np

        def hi_energy(x, fs, hi_hz=25000.0):
            X = np.abs(np.fft.rfft(x * np.hanning(len(x))))
            fr = np.fft.rfftfreq(len(x), 1.0 / fs)
            return float(X[fr > hi_hz].sum() / (X.sum() + 1e-30))

        for src in ("pink", "white", "music"):
            gx, gfs, _gy = E.build_case(src, "genuine", 96000, 11)
            fx, ffs, _fy = E.build_case(src, "upsampled", 96000, 11)
            ge, fe = hi_energy(gx, gfs), hi_energy(fx, ffs)
            # 真的必须有能量，假的必须几乎没有
            self.assertGreater(ge, 0.01, "%s: genuine 的 25k 以上能量 %.5f 太低" % (src, ge))
            self.assertLess(fe, 0.005, "%s: upsampled 的 25k 以上能量 %.5f 太高" % (src, fe))

    def test_feature_is_deterministic(self):
        import numpy as np

        from audiocheck.core.analyzers.fakehires import diff_log_features
        x = np.random.default_rng(0).standard_normal(44100)
        a = diff_log_features(x, 44100)
        b = diff_log_features(x, 44100)
        np.testing.assert_allclose(a, b)
        self.assertTrue(np.all(np.isfinite(a)))
        self.assertAlmostEqual(float(np.linalg.norm(a)), 1.0, places=6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
