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

import os

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core import dsp, engine

FS = 48000


def _write(tmp_path, x, fs=FS):
    p = tmp_path / "t.wav"
    sf.write(str(p), np.column_stack([x, x]).astype(np.float32), fs, subtype="PCM_16")
    # ⚠ 2026-10-17：本文件曾**偶发** `FileNotFoundError`，至今**不可复现**（§6 欠账 #7）。
    # 按"不猜着改"的规矩：不改任何逻辑，只加**可观测性**——写完立刻确认文件在并报大小。
    # 这样它若再来一次，日志能立刻区分两种成因：
    #   * 这条断言炸 ⇒ **写没落盘**（测试侧/磁盘/杀软），与产品无关；
    #   * 断言通过而后面打开时炸 ⇒ **文件在两次调用之间消失**，才可能是产品侧。
    assert p.exists(), "sf.write 返回后文件却不存在：%s" % p
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


# --------------------------------------------- 2026-10-17：真峰值扫描（整轨+逐段共用）
def test_true_peak_scan_global_matches_independent_oversampling(tmp_path):
    """★ 合并实现后，整轨读数必须与**另一条路径**（整段一次过采样）互证。

    `true_peak()` 现在只是 `true_peak_scan()` 的一个入口——所以不能拿它自己对自己
    （那是同义反复）。这里用 `dsp._true_peak_of`（整段过采样、无分块）当独立参照：
    单块情形下两者必须一致。**两条不同路径互证**才说明"合并没动读数"。
    """
    rng = np.random.default_rng(21)
    x = (rng.standard_normal(int(FS * 20)) * 0.3).astype(np.float32)
    for i in (300000, 700000, 950000):
        x[i:i + 64] = _sine(64, FS / 6, 0.95)
    p = _write(tmp_path, x)
    big = dsp.true_peak_scan(p, None, chunk=1 << 22)["global"]        # 单块
    # 参照要拿**从文件读回来的**样本（`_write` 是 PCM_16，量化会动峰值——
    # 拿原始 float 当参照会得到一个假的 0.5 dB 差）
    back, _ = sf.read(p, dtype="float32", always_2d=True)
    ref = 20 * np.log10(dsp._true_peak_of(back) + 1e-12)
    assert abs(big - ref) < 0.01, "整轨 %.3f vs 独立参照 %.3f" % (big, ref)
    # 分块数变化不得漂移（借上下文 + 只取中段那套细节）
    small = dsp.true_peak_scan(p, None, chunk=60000)["global"]
    assert abs(big - small) < 0.02, "大块 %.3f vs 小块 %.3f" % (big, small)


def test_true_peak_scan_spans_locate_hot_region(tmp_path):
    """各段真峰值必须**定位到该段**：热段 ≥0 dBFS、冷段远低于它。

    这条同时钉住 **帧 → 4× 采样索引的映射**——映射错一位不会报错，
    只会把热段算成冷段（安静地给错数，本项目最忌讳的失败方式）。
    """
    fs = 44100
    t = np.arange(int(fs * 4)) / fs
    clean = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    hot = np.clip(np.random.default_rng(7).standard_normal(fs * 4) * 6.0, -1, 1).astype(np.float32)
    x = np.concatenate([clean, hot])                 # 前 4 秒冷、后 4 秒热
    p = _write(tmp_path, x, fs)
    seg = fs
    spans = [(0, seg), (2 * seg, 3 * seg), (4 * seg, 5 * seg)]     # 冷 / 冷 / 热
    got = dsp.true_peak_scan(p, spans)["spans"]
    assert got[0] < -6.0, "冷段（0.3 正弦 ≈ −10.5 dBFS）实得 %s" % got[0]
    assert got[2] >= 0.0, "热段应顶到上限，实得 %s" % got[2]
    assert got[0] < got[2]


def test_true_peak_scan_reports_truncation(tmp_path):
    """读不满（截断）⇒ `incomplete=True` + `frames_read` < 头部声称；不许当完好。"""
    x = _sine(int(FS * 30), 440, 0.2)
    full = str(tmp_path / "full.flac")
    sf.write(full, np.column_stack([x, x]).astype(np.float32), FS, subtype="PCM_16")
    cut = str(tmp_path / "cut.flac")
    with open(full, "rb") as f, open(cut, "wb") as g:
        g.write(f.read(int(os.path.getsize(full) * 0.7)))
    got = dsp.true_peak_scan(cut, [(0, 48000)])
    assert got["incomplete"] is True
    assert 0 < got["frames_read"] < sf.info(cut).frames
    assert got["global"] is not None          # 按已读部分照样给值


def test_loudness_honours_explicit_true_peak_sentinel(tmp_path):
    """★ 哨兵语义：`true_peak_dbfs=None` 是**合法结果**（静音没有真峰值），
    不许被当成"没传"而重算一遍——"用值域兼表状态"正是本项目踩过的老坑。

    传 `None` ⇒ 结果里**没有** true_peak_dbfs / plr；不传（哨兵）⇒ 自己扫。
    """
    x = _sine(int(FS * 6), 1000, 0.5)
    p = _write(tmp_path, x)
    n = int(FS * 6)
    assert "true_peak_dbfs" not in dsp.loudness(p, FS, 2, n, true_peak_dbfs=None)
    auto = dsp.loudness(p, FS, 2, n)
    assert auto.get("true_peak_dbfs") is not None, auto


def test_lra_requires_at_least_four_short_term_blocks(tmp_path):
    """★ LRA 守卫边界：短时块 **≥4** 才算 LRA（`dsp.loudness` 里 `st_ms.shape[0] >= 4`）。

    短时块 = 3s 窗 / 1s 步（`w=30`、`step_ms` 按 100ms 步）⇒
    - 时长 **3.2 s** ⇒ 短时块 3 个 ⇒ `lra is None`（样本太少不下结论）；
    - 时长 **3.35 s** ⇒ 短时块恰好 **4** 个 ⇒ `lra` 有值。

    把守卫的 `>=` 改成 `>` 会让"恰好 4 块"的文件**丢掉 LRA**（安静地少一个指标）。
    """
    for secs, has in ((3.2, False), (3.35, True)):
        n = int(FS * secs)
        t = np.arange(n) / FS
        x = 0.4 * np.sin(2 * np.pi * 200 * t)
        r = dsp.loudness(_write(tmp_path, x), FS, 2, n)
        assert (r.get("lra") is not None) is has, \
            "%.2fs 的 lra=%r（期望 has=%s）" % (secs, r.get("lra"), has)


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
