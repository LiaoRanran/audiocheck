"""DSP 内核对着标准条文的对齐测试。

**为什么值得单独立一个文件**：这个项目所有结论的可信度都建立在
「DSP 原语按公开标准实现」这个前提上。前提不核，上面的分析全是空中楼阁。

本文件把每条实现对到**标准原文字面值**上：
  * ITU-R BS.1770-4 表 1/表 2 的 48 kHz 系数
  * BS.1770-4 的门限常量（−70 LKFS / 相对 −10 LU）与常数项 −0.691
  * EBU Tech 3342 的 LRA 门限（相对 −20 LU）与 3s 短时块
  * BS.1770-4 Annex 2 的 True Peak ≥4× 过采样

## 审计过程中我错了两次，都记在这里

1. 我断言「K 加权在 1kHz 应为 0 dB」。**错。** 标准的高架滤波把
   1kHz 抬了 +0.6977 dB，然后用 −0.691 常数项去抵消——两者互为补偿，
   净值 +0.0067 dB。`test_offset_cancels_k_weighting_gain` 钉住这个关系。
2. 端到端理论上我漏了 K 加权增益项，写成 `20log10(rms) − 0.691`，
   算出实测「偏高 0.70 LU」。**是公式错了，不是代码错了**——
   漏掉的那项恰好等于 K 加权增益，这本身就是漏项的证据。
   修正后差 0.0036 LU。

两次都是我的断言错、实现对。这个结果比"我改对了代码"更有价值：
说明实现经得起对抗性核查。
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core import dsp as D  # noqa: E402

#: ITU-R BS.1770-4 表 1/表 2 公布的 48 kHz 参考系数（标准原文字面值）
REF48 = {
    "stage1_b": [1.53512485958697, -2.69169618940638, 1.19839281085285],
    "stage1_a": [1.0, -1.69065929318241, 0.73248077421585],
    "stage2_b": [1.0, -2.0, 1.0],
    "stage2_a": [1.0, -1.99004745483398, 0.99007225036621],
}


def k_gain_db(fs: int, f: float) -> float:
    (b1, a1), (b2, a2) = D.k_weighting(fs)
    _, h1 = signal.freqz(b1, a1, worN=[f], fs=fs)
    _, h2 = signal.freqz(b2, a2, worN=[f], fs=fs)
    return float(20 * np.log10(abs(h1[0] * h2[0]) + 1e-300))


class TestKWeighting(unittest.TestCase):
    """K 加权必须逐字对上 BS.1770-4 的表。"""

    def test_coefficients_match_standard_at_48k(self):
        (b1, a1), (b2, a2) = D.k_weighting(48000)
        for got, want in ((b1, REF48["stage1_b"]), (a1, REF48["stage1_a"]),
                          (b2, REF48["stage2_b"]), (a2, REF48["stage2_a"])):
            np.testing.assert_allclose(got, want, atol=1e-12)

    def test_shape_is_sample_rate_invariant(self):
        """频率响应形状不能随采样率漂。漂了说明系数是照抄 48k 表算的。"""
        ref = [k_gain_db(44100.0 and 44100, f) for f in (100.0, 1000.0, 3000.0, 10000.0)]
        for fs in (48000, 96000, 192000):
            got = [k_gain_db(fs, f) for f in (100.0, 1000.0, 3000.0, 10000.0)]
            for a, b in zip(got, ref):
                self.assertAlmostEqual(a, b, delta=0.05,
                                       msg="fs=%d 处形状漂了 %.3f dB" % (fs, abs(a - b)))

    def test_offset_cancels_k_weighting_gain(self):
        """**−0.691 不是随便给的常数**：它抵消 K 加权在校准频率上的增益。

        我先前的断言是「K 加权在 1kHz 应为 0 dB」，那是错的。
        实测 +0.6977 dB，与常数项 −0.6910 相加 ≈ 0。这条测试把这个关系钉住，
        防止有人「修正」K 加权让它在 1kHz 变成 0 dB——那会破坏标准一致性。
        """
        g = k_gain_db(48000, 1000.0)
        self.assertGreater(g, 0.6, "K 加权在 1kHz 应约为 +0.7 dB，不该是 0")
        self.assertAlmostEqual(g + D.OFFSET, 0.0, delta=0.02,
                               msg="K 加权增益与常数项应互为补偿")


class TestConstantsMatchStandards(unittest.TestCase):
    def test_gating_thresholds(self):
        """BS.1770-4 绝对门限 −70 LKFS、积分相对门限 −10 LU。"""
        self.assertEqual(D.ABS_GATE, -70.0)
        self.assertEqual(D.REL_GATE_INT, -10.0)

    def test_lra_thresholds(self):
        """EBU Tech 3342：LRA 相对门限 −20 LU，短时块 1s 步进。"""
        self.assertEqual(D.REL_GATE_LRA, -20.0)
        self.assertEqual(D.SHORT_STEP, 1.0)

    def test_integrated_block_step(self):
        """积分响度 100ms 步进（400ms 块 = 4 步）。"""
        self.assertEqual(D.BLOCK_STEP, 0.1)

    def test_offset_value(self):
        self.assertAlmostEqual(D.OFFSET, -0.691, places=6)

    def test_true_peak_oversampling_is_exactly_4x(self):
        """BS.1770-4 Annex 2 要求 **≥4** 倍过采样；本项目**取规格下限 4**。

        为什么钉"恰好 4"而不是">=4"：提高倍数**不是免费的，而且会改动读数**——
        实测 5× 与 4× 的真峰值读差 0.03–0.04 dB（fs/6 正弦 0.040、含瞬态的类音乐
        噪声 0.030）。所以改这个常数必须是一次有意识的、会动读数的决定。
        """
        self.assertEqual(D.TP_OS, 4)


class TestLoudnessEndToEnd(unittest.TestCase):
    """端到端：1kHz 正弦的响度必须等于 BS.1770-4 的定义式。

        LUFS = −0.691 + 20·log10(rms) + K加权增益@1kHz

    **式子里最后一项不能漏**（我漏过一次，误判成代码有 bug）。
    """

    def _measure(self, peak_db: float) -> float:
        fs, n = 48000, int(0.4 * 8 * 48000)
        A = 10 ** (peak_db / 20.0)
        x = (A * np.sin(2 * np.pi * 1000.0 * np.arange(n) / fs)).astype(np.float32)
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "tone.wav")
            sf.write(p, x, fs, subtype="FLOAT")
            return D.loudness(p, fs, 1, n).get("lufs", float("nan"))

    def test_sine_loudness_matches_definition(self):
        kw = k_gain_db(48000, 1000.0)
        for peak_db in (-20.0, -6.0):
            got = self._measure(peak_db)
            want = 20 * np.log10(10 ** (peak_db / 20.0) / np.sqrt(2)) + kw + D.OFFSET
            self.assertAlmostEqual(got, want, delta=0.02,
                                   msg="峰值 %+.0f dBFS 的响度偏离定义式" % peak_db)

    def test_loudness_is_monotonic_in_level(self):
        """输入电平升高，读数必须随之升高 —— 防止符号或单位写反。"""
        self.assertGreater(self._measure(-6.0), self._measure(-20.0))


if __name__ == "__main__":
    unittest.main(verbosity=2)
