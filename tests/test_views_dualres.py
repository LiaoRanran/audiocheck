"""C7 双分辨率 STFT（视图层精度·极致化）实测判据。

复算：`python -m pytest tests/test_views_dualres.py -q`

核心事实（先量后改，来自 packaging/lowfreq_eval.py 的评估）：
- 4096 窗（默认）在 <546Hz 是"行距细过 bin"，低频糊是 bin 粗造成的，
  加行数治不了（E1 实测 50Hz 占行 360/720 不变）。
- 16384 窗 Δf 2.69Hz，可分辨间距 30→10Hz（3×）。

本文件钉住"实现确实带来了这 3× 低频分辨率"，且**不改动高频轴、不改动判定**。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core import views  # noqa: E402

FS = 44100
CUTOFF = views.DUAL_RES_CUTOFF_HZ


def _song(path, seconds: float = 30.0):
    rng = np.random.default_rng(7)
    x = 0.2 * rng.standard_normal(int(FS * seconds)).astype(np.float32)
    sf.write(path, np.column_stack([x, x]), FS, subtype="PCM_16")
    return path


def _tone_pair(path, f1, f2, seconds: float = 8.0):
    n = int(FS * seconds)
    t = np.arange(n) / FS
    x = (np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)).astype(np.float32)
    x *= 0.5 / np.max(np.abs(x))
    sf.write(path, np.column_stack([x, x]), FS, subtype="PCM_16")
    return path


def _peak_valley(f, db, fa, fb):
    """两音位与它们之间谷的峰谷比（dB）。同 bin 视为不可分 ⇒ 返回 0。"""
    j1 = int(np.argmin(np.abs(f - fa)))
    j2 = int(np.argmin(np.abs(f - fb)))
    lo, hi = min(j1, j2), max(j1, j2)
    if hi - lo < 1:
        return 0.0
    peak = max(float(db[j1].max()), float(db[j2].max()))
    valley = float(db[lo:hi + 1].min())
    return peak - valley


def test_off_returns_uniform_grid(tmp_path):
    """对照：单分辨率返回均匀频率轴，Δf = fs/nperseg。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "s.wav")), dual_resolution=False)
    assert np.all(np.diff(f) > 0), "f 必须单调递增"
    d = np.diff(f)
    assert abs(d.mean() - FS / views.DEFAULT_VIEW_NPERSEG) < 0.5
    assert len(f) == views.DEFAULT_VIEW_NPERSEG // 2 + 1


def test_on_returns_monotonic_f(tmp_path):
    """双分辨率返回非均匀但**仍单调递增**的 f（下游 searchsorted 的契约）。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "s.wav")), dual_resolution=True)
    assert np.all(np.diff(f) > 0), "合并后的非均匀 f 必须仍单调递增"
    assert len(f) == S.shape[0], "f 长度必须与矩阵行数一致"


def test_low_band_is_finer_with_dual_res(tmp_path):
    """机制：低频带内频率轴密度 ≥3×（可分辨间距 30→10Hz 的根）。"""
    song = _song(str(tmp_path / "s.wav"))
    _, f_off, _ = views.spectrogram(song, dual_resolution=False)
    _, f_on, _ = views.spectrogram(song, dual_resolution=True)
    band = (60.0, 120.0)
    m_on = (f_on >= band[0]) & (f_on <= band[1])
    m_off = (f_off >= band[0]) & (f_off <= band[1])
    assert m_on.sum() >= 3 * m_off.sum(), \
        "低频带细轴 bin 数应 ≥3×：on=%d off=%d" % (m_on.sum(), m_off.sum())


def test_high_band_unchanged_by_dual_res(tmp_path):
    """高于 cutoff 的频率轴必须与单分辨率主网格逐点一致（不引入 fake 细节）。"""
    song = _song(str(tmp_path / "s.wav"))
    _, f_off, _ = views.spectrogram(song, dual_resolution=False)
    _, f_on, _ = views.spectrogram(song, dual_resolution=True)
    hi_on = f_on[f_on >= CUTOFF]
    hi_off = f_off[f_off >= CUTOFF]
    assert np.allclose(hi_on, hi_off, rtol=1e-6), "高频粗轴不应被双分辨率改动"


def test_dual_res_auto_off_when_nperseg_ge_16384(tmp_path):
    """用户已选 ≥16384 窗时，双分辨率自动退化为单分辨率（避免冗余 pass）。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "s.wav")),
                                 nperseg=views.DUAL_RES_NPERSEG, dual_resolution=True)
    assert len(f) == views.DUAL_RES_NPERSEG // 2 + 1


def test_two_low_tones_more_resolvable_with_dual_res(tmp_path):
    """15Hz 低频间距：双分辨率明显更可分（两音间更多 bin + 更深峰谷）。

    这正是 C7 要根治的"低频糊"——4096 窗在该间距合并两音、16384 窗分开。
    """
    p = _tone_pair(str(tmp_path / "tt.wav"), 80.0, 95.0)
    _, f0, S0 = views.spectrogram(p, dual_resolution=False)
    db0 = (S0 - np.nanmax(S0)).astype(float)
    _, f1, S1 = views.spectrogram(p, dual_resolution=True)
    db1 = (S1 - np.nanmax(S1)).astype(float)

    j1_0 = int(np.argmin(np.abs(f0 - 80.0)))
    j2_0 = int(np.argmin(np.abs(f0 - 95.0)))
    j1_1 = int(np.argmin(np.abs(f1 - 80.0)))
    j2_1 = int(np.argmin(np.abs(f1 - 95.0)))

    assert (max(j1_1, j2_1) - min(j1_1, j2_1)) > (max(j1_0, j2_0) - min(j1_0, j2_0)), \
        "双分辨率应在两音间给出更多 bin（更细）"

    pv0 = _peak_valley(f0, db0, 80.0, 95.0)
    pv1 = _peak_valley(f1, db1, 80.0, 95.0)
    assert pv1 > pv0 + 1.0, \
        "双分辨率应露出更深的峰谷（更可分）：on=%.1f off=%.1f" % (pv1, pv0)


def test_align_time_keeps_shape():
    """`_align_time` 只沿时间轴最近邻重采样，形状正确。"""
    S = np.arange(5 * 10, dtype=np.float32).reshape(5, 10)
    t_src = np.linspace(0, 1, 10)
    t_dst = np.linspace(0, 1, 4)
    out = views._align_time(S, t_src, t_dst)
    assert out.shape == (5, 4)
