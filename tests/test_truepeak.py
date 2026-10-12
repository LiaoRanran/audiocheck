"""True Peak 与 DR 的回归测试。

两个都是**这次修掉的真 bug**，所以测试直接瞄准漏洞本身：

1. True Peak：早先每块独立过采样后从两头各削 10%，
   落在削掉区间里的真峰值**永久漏掉**。修法是块间保留重叠、只处理内部。
   → 测试：块大小任意变化时读数必须不变；且边界处的突发必须被测到。

2. DR：早先把「读文件的块」（65536 采样，48kHz 下 1.37 秒）当 RMS 块，
   而 TT / Pleasurize 规定是 **3 秒块**。既不是 3 秒、也不与曲目结构对齐。
   → 测试：手算可验证的素材；以及读块大小变化时读数必须不变。
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core import dsp, engine

FS = 48000


def _write(tmp_path, x, fs=FS):
    p = tmp_path / "t.wav"
    sf.write(str(p), np.column_stack([x, x]).astype(np.float32), fs, subtype="PCM_16")
    return str(p)


def _sine(n, hz, amp, fs=FS):
    t = np.arange(n) / fs
    return amp * np.sin(2 * np.pi * hz * t)


# --------------------------------------------------------------- True Peak
def _tone_fs6(seconds=1.0, fs=FS, amp=0.5):
    """fs/6 正弦，**长度取在过零点结束**（n ≡ 1 mod 6）。

    为什么必须这样：若信号在非零值处 abrupt 结束，带限重建在末端会产生
    吉布斯过冲（实测 0.433 → 0.522）。那是**测试信号的伪影**，不是被测对象的
    性质——真实音乐素材上 FIR 与 FFT 两种过采样的真峰值相差 < 0.01 dB。
    要测「采样间过冲」就必须把末端伪影排除掉。
    """
    n = int(fs * seconds)
    n += (1 - n) % 6                       # 调到 n ≡ 1 (mod 6)
    t = np.arange(n) / fs
    return amp * np.sin(2 * np.pi * (fs / 6) * t)


def test_true_peak_detects_intersample_overshoot(tmp_path):
    """真峰值必须**高于**采样峰值——这是 BS.1770 引入它的全部理由。

    fs/6 正弦把峰正好放在两个采样正中：采样只抓到 0.866·A，
    连续信号的实际峰是 A。差值 20log10(1/0.866) = 1.25 dB。

    ⚠ 不把上界钉死在 1.25 dB：文件被 DAC 播放时样本序列是**截断**的，
    带限重建在文件首尾会振铃，再多贡献约 0.4 dB。
    这与 FFT 法（假设信号周期循环）结论不同，是两种合理的物理解释；
    真实音乐素材上两者相差 < 0.01 dB，因为音乐不在高幅值处 abrupt 结束。
    """
    a = 0.5
    x = _tone_fs6(amp=a)
    p = _write(tmp_path, x)
    tp = dsp.true_peak(p)
    sp = 20 * np.log10(np.abs(sf.read(p)[0]).max())
    assert tp is not None
    assert (tp - sp) > 1.0, "真峰值应比采样峰值至少高 1 dB，实得 %.3f" % (tp - sp)
    assert (tp - sp) < 1.9, "超出预期上界，实得 %.3f" % (tp - sp)


def test_true_peak_matches_sample_peak_when_peak_lands_on_sample(tmp_path):
    """频率取 fs/12（周期 12 采样，4 的倍数）时峰正好落在采样点上，
    真峰值应等于采样峰值 —— 验证过采样没有引入增益误差。"""
    a = 0.5
    x = _sine(int(FS * 2), FS / 12, a)
    p = _write(tmp_path, x)
    tp = dsp.true_peak(p)
    sp = 20 * np.log10(np.abs(sf.read(p)[0]).max())
    assert abs(tp - sp) < 0.05, "峰落在采样点上时两者应相等：%.3f vs %.3f" % (tp, sp)


def test_true_peak_invariant_to_chunk_size(tmp_path):
    """块大小任意变化，真峰值读数必须不变。

    这是漏峰 bug 的直接回归：若每块独立过采样并削边，块越大削掉的绝对区间越大，
    读数会随 chunk 漂移。
    """
    rng = np.random.default_rng(11)
    x = (rng.standard_normal(int(FS * 20)) * 0.3).astype(np.float32)
    # 掺几个大瞬态，让边界处真的有峰可丢
    for i in (300000, 700000, 950000):
        x[i:i + 64] = _sine(64, FS / 6, 0.95)
    p = _write(tmp_path, x)
    big = dsp.true_peak(p, chunk=1 << 22)
    small = dsp.true_peak(p, chunk=60000)
    assert big is not None and small is not None
    assert abs(big - small) < 0.02, "大块 %.3f vs 小块 %.3f —— 边界在丢峰" % (big, small)


def test_true_peak_not_lost_at_chunk_boundary(tmp_path):
    """把突发**正好放在块边界的第一采样**上。

    旧实现会把块 2 的开头 10%（过采样后 40 万个采样）整段削掉，
    这个突发就永久消失；新实现靠块间重叠保住它。
    """
    n = 3 * 60000
    x = (np.random.default_rng(12).standard_normal(n) * 0.01).astype(np.float32)
    burst = _sine(64, FS / 6, 0.9)
    x[60000:60064] = burst          # chunk 2 的第一个采样
    x[120000:120064] = burst        # chunk 3 的第一个采样
    p = _write(tmp_path, x)
    tp = dsp.true_peak(p, chunk=60000)
    assert tp is not None
    assert tp > -0.6, "边界突发应被测到（≈ -0.92 dBFS），实得 %s" % tp


def test_true_peak_of_full_scale_is_not_clipped(tmp_path):
    """满刻度正弦的真峰值应约 0 dBFS，且不超过 +0.1（16bit 量化不应造出大过冲）。"""
    x = _sine(int(FS * 3), 1000, 0.999)
    tp = dsp.true_peak(_write(tmp_path, x))
    assert -0.15 < tp <= 0.1, "实得 %s" % tp


# ---------------------------------------------------------------------- DR
def test_dr_matches_analytical(tmp_path):
    """30 秒大幅 + 30 秒小幅，各占整数个 3 秒块 → DR 可手算。

    大幅段：幅度 0.5 正弦 → 峰值 −6.02 dBFS，RMS −9.03 dBFS
    20 个 3 秒块，最响 20% = 4 块，全来自大幅段 → DR = 3.01 dB
    """
    loud = _sine(int(FS * 30), 200, 0.5)
    x = np.concatenate([loud, loud * 0.001])
    r = engine.analyze(_write(tmp_path, x), only=["basic"])
    assert r["dr_db"] == pytest.approx(3.0, abs=0.15), "DR 应约 3.0 dB，实得 %s" % r["dr_db"]
    # 同时给出 mean20 变体；此处 4 个最响块等响，两者应一致
    assert r["dr_db_mean20"] == pytest.approx(3.0, abs=0.15)


def test_dr_invariant_to_read_block_size(tmp_path, monkeypatch):
    """改读块大小，DR 必须不变。

    旧实现把读块当 RMS 块，因此 BLOCK 从 65536 改成 4096 会直接改变结果——
    这个测试钉住"RMS 块与文件读取粒度解耦"。
    """
    import audiocheck.core.analyzers.basic as basic
    rng = np.random.default_rng(13)
    # 响度按 1.3 秒周期起伏：既不与 65536 采样（1.37s）也不与 4096 对齐
    n = int(FS * 30)
    t = np.arange(n) / FS
    env = 0.2 + 0.3 * (0.5 + 0.5 * np.sin(2 * np.pi * t / 1.3))
    x = (rng.standard_normal(n) * env).astype(np.float32)
    p = _write(tmp_path, x)

    r1 = engine.analyze(p, only=["basic"])
    monkeypatch.setattr(basic, "BLOCK", 4096)
    r2 = engine.analyze(p, only=["basic"])
    assert r1["dr_db"] == pytest.approx(r2["dr_db"], abs=0.05), \
        "DR 随读块大小漂移：%s vs %s" % (r1["dr_db"], r2["dr_db"])


def test_dr_low_for_loudness_war_material(tmp_path):
    """强压缩素材的 DR 应显著低于动态素材。"""
    rng = np.random.default_rng(14)
    n = int(FS * 24)
    quiet = (rng.standard_normal(n) * 0.02).astype(np.float32)
    loud = (rng.standard_normal(n) * 0.45).astype(np.float32)
    war = np.clip(np.concatenate([loud, quiet]) * 3.0, -1.0, 1.0).astype(np.float32)
    r = engine.analyze(_write(tmp_path, war), only=["basic"])
    assert r["dr_db"] < 8.0, "压缩素材 DR 应低，实得 %s" % r["dr_db"]
