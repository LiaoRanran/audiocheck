"""有效位深 / 升位检测的回归测试。

关键：**测试信号必须真的模拟升位**。
libsndfile 写 PCM_24 时期望 int32 的**高 24 位**是有效值，
所以要把 16bit 样本放进 24bit 字的高 16 位，需传 `v16 << 16`。
（早先传 `v16 << 8` 只是把 16bit 值原样存成 24bit，低位并没有结构性为零。）
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from audiocheck.core.analyzers.bitdepth import BitDepthAnalyzer, analyze

FS = 48000


def _w(tmp_path, data24, subtype="PCM_24"):
    """data24: 24bit 字（int32）。写入时按 libsndfile 要求左移 8 位。"""
    p = tmp_path / "bd.wav"
    sf.write(str(p), np.column_stack([data24, data24]).astype(np.int32) << 8,
             FS, subtype=subtype)
    return str(p)


def _v16(n=FS, amp=16000):
    t = np.arange(n) / FS
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.int32)


def _run(path, subtype="PCM_24"):
    info = sf.info(path)
    return analyze(path, subtype, info.channels)


def test_true_24bit_detected(tmp_path):
    """真 24bit：低位由信号自身占用 → effective == 24，且不得判成升位。

    注意**不能**用均匀随机填低位来造"真 24bit"——那与抖动在统计上不可区分，
    会被正确地判为"无法区分"（见 test_dithered_upsample_is_inconclusive）。
    这里用非整数周期正弦，让低位由信号内容自然占据。
    """
    t = np.arange(FS) / FS
    v = ((2 ** 22) * np.sin(2 * np.pi * 1234.5 * t)).astype(np.int64)
    r = _run(_w(tmp_path, v))
    assert r["bit_nominal"] == 24
    assert r["bit_effective"] >= 22, "真 24bit 应测出 ≥22 位，实得 %s" % r["bit_effective"]
    # 注意不能断言 `"升位" not in verdict` —— "**未**检测到升位"也含"升位"子串。
    # 要判的是"有没有做出升位的判定"，即 verdict 里不得出现"疑似由"。
    assert "疑似由" not in r["bit_verdict"], "不得把真 24bit 判成升位"
    assert "未检测到升位" in r["bit_verdict"]


def test_upsampled_from_16bit_detected(tmp_path):
    """16bit 升位到 24bit：低 8 位恒为 0 → effective == 16。"""
    v = _v16().astype(np.int64) << 8          # 24bit 字 = v16<<8，低 8 位为 0
    r = _run(_w(tmp_path, v))
    assert r["bit_nominal"] == 24
    assert r["bit_effective"] <= 17, \
        "应测出约 16 位，实得 %s" % r["bit_effective"]
    assert "升位" in r["bit_verdict"]


def test_dithered_upsample_is_inconclusive(tmp_path):
    """加了抖动就不该判升位 —— 抖动正是为掩盖量化结构。"""
    rng = np.random.default_rng(4)
    v = _v16().astype(np.int64) << 8
    v = v + rng.integers(0, 256, size=v.size)  # 低 8 位随机 = 抖动
    r = _run(_w(tmp_path, v))
    assert r["bit_dither"] is True
    assert "无法进一步区分" in r["bit_verdict"]
    assert "疑似由" not in r["bit_verdict"], "加了抖动就不得判升位"
    # 主要结论仍是"没有升位证据"，不该被抖动注脚搅成"不适用"
    assert "未检测到升位" in r["bit_verdict"]


def test_true_16bit_in_16bit_container(tmp_path):
    """16bit 容器里的正常 16bit 文件：不应报升位。"""
    p = tmp_path / "b16.wav"
    sf.write(str(p), np.column_stack([_v16().astype(np.int16)] * 2), FS, subtype="PCM_16")
    r = _run(str(p), "PCM_16")
    assert r["bit_nominal"] == 16
    assert r["bit_effective"] >= 14, "实得 %s" % r["bit_effective"]


def test_quiet_signal_not_misreported_as_upsampled(tmp_path):
    """**安静信号不能被误判成升位** —— 这是按电平判断会犯的错。

    做法：24bit 容器里放一个只在高端使用的信号（低 8 位因电平低而少用），
    但**最响的样本**仍然用到全部 24 位。判据只统计最响样本，故应报真 24bit。
    """
    rng = np.random.default_rng(5)
    quiet = (_v16(amp=300).astype(np.int64) << 8) + rng.integers(0, 256, size=FS)
    loud = (_v16(amp=30000).astype(np.int64) << 8) + rng.integers(0, 256, size=FS)
    r = _run(_w(tmp_path, np.concatenate([quiet, loud])))
    assert r["bit_effective"] >= 22, "安静段不应把结论拉低，实得 %s" % r["bit_effective"]


def test_analyzer_via_registry(tmp_path):
    """通过分析器接口跑一遍，确认注册与 requires 正常。"""
    v = _v16().astype(np.int64) << 8
    p = _w(tmp_path, v)

    class _Ctx:
        path = p
        channels = 2
        # 字段名必须是 **bits**（soundfile 的 info.subtype）。
        # 写成 "subtype" 会让代码永远落到默认值 —— 那正是本次修掉的 bug。
        result = {"bits": "PCM_24"}

    r = BitDepthAnalyzer().run(_Ctx())
    assert r["bit_nominal"] == 24
    assert r["bit_effective"] <= 17
