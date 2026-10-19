"""内容带宽估计的判据（`core/analyzers/spectrum.content_bandwidth`）。

为什么这一层要有判据：它回答的是"**编辑侧该拿什么当带限先验 `B`**"——一个**会直接进算式**
的数（`DeclipOp` 的带限投影按 `B` 砍带外）。所以它的口径必须**用真值钉住**，
而合成语料（`core/edit/corpus.py`）正好把 `B` 的真值给全了。

实测基线（`packaging/bandwidth_probe.py`，2026-10-19，48 kHz）：
* 干净砖墙网格 `B ∈ {6,8,10,12,16,18,20,21} kHz` ⇒ 读数**全部** `source="cutoff"`，
  最大相对偏差 **0.49%**（判据取 ≤2%，留 4 倍余量；`detect_cutoff` 自身的校准口径
  见 `docs/theory.md §1.6`：合成砖墙网格 ≤1.3%）。
* **moderate 削波**后砖墙证据**还在**（8k → 8001、18k → 18000）⇒ 自动定带宽可用。
* **severe 削波**后证据**没了**（读数退回奈奎斯特）⇒ 自动定带宽**如实拒绝**
  （去削波退化成逐位不变）——这是本轮量出来的**效力边界**，不是缺陷。
* 没有砖墙（满带宽 / 自然滚降）⇒ 一律返回奈奎斯特，**不许**拿能量滚降点冒充带限。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core.analyzers.spectrum import content_bandwidth  # noqa: E402
from audiocheck.core.context import AnalysisContext, io_path  # noqa: E402
from audiocheck.core.edit import corpus, declip  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0                    # canonical 谱的门槛实测在 0.90–0.95 s 之间（见探针 ⑦）


def _spectrum(y: np.ndarray, tmp_path, name: str):
    """走**生产口径**取 canonical 谱（不在这里抄一遍 Welch 参数——那会变成两把尺子）。"""
    p = str(tmp_path / name)
    sf.write(p, np.asarray(y).astype(np.float32), FS, subtype="FLOAT")
    return AnalysisContext(path=io_path(p), deep=False, src_path=p).spectrum()


def _natural_rolloff(fc: float = 5000.0, order: int = 1, seed: int = 3) -> np.ndarray:
    """**自然滚降**（一阶低通，~6 dB/倍频程）——**没有砖墙**，但能量滚降点会给出一个低值。"""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(SECONDS * FS))
    sos = signal.butter(order, fc / (FS / 2.0), btype="low", output="sos")
    y = signal.sosfilt(sos, x)
    return (y / np.abs(y).max() * 0.95)[:, None]


# ------------------------------------------------------------------ ① 有砖墙 ⇒ 用截止读数
def test_brick_wall_grid_is_recovered_within_two_percent(tmp_path):
    """干净砖墙网格：读数必须**认得出砖墙**（`source="cutoff"`）且偏差 ≤2%（实测最大 0.49%）。"""
    for b in (6000.0, 8000.0, 10000.0, 12000.0, 16000.0, 18000.0, 20000.0, 21000.0):
        y = corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=b, seed=1)
        f, db = _spectrum(y, tmp_path, "b%05.0f.wav" % b)
        info = content_bandwidth(f, db, FS)
        assert info["source"] == "cutoff", (b, info)
        assert abs(info["bandwidth_hz"] / b - 1.0) <= 0.02, (b, info["bandwidth_hz"])
        assert info["cutoff_hz"] == info["bandwidth_hz"]


def test_moderate_clipping_keeps_the_brick_wall_evidence(tmp_path):
    """削过一刀（moderate）后砖墙证据**还在**（削波谐波没把阻带填到推翻判据）。"""
    for b in (8000.0, 18000.0):
        p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=b, seed=1),
                           "moderate")
        f, db = _spectrum(p["clipped"], tmp_path, "m%05.0f.wav" % b)
        info = content_bandwidth(f, db, FS)
        assert info["source"] == "cutoff", (b, info)
        assert abs(info["bandwidth_hz"] / b - 1.0) <= 0.01, (b, info["bandwidth_hz"])


def test_severe_clipping_destroys_the_evidence_and_we_refuse(tmp_path):
    """**效力边界（实测）**：severe 档上砖墙证据被削波谐波填掉 ⇒ 自动读数**退回奈奎斯特**。

    这不是缺陷，是**如实拒绝**：`B = 奈奎斯特` ⇒ 去削波逐位不变（`noop`），
    而不是拿一个错的 `B` 去硬砍。想修 severe 得显式给 `B`，或等"稀疏族"落地。
    """
    for b in (8000.0, 18000.0):
        p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=b, seed=1),
                           "severe")
        f, db = _spectrum(p["clipped"], tmp_path, "s%05.0f.wav" % b)
        info = content_bandwidth(f, db, FS)
        assert info["source"] == "nyquist" and info["bandwidth_hz"] == FS / 2, (b, info)


# ------------------------------------------------------------------ ② 没有砖墙 ⇒ 不作约束
def test_full_band_and_natural_rolloff_both_return_nyquist(tmp_path):
    """满带宽与**自然滚降**都不构成"带限证据" ⇒ 一律返回奈奎斯特（不许拿滚降点冒充）。

    ⚠ 自然滚降素材的能量滚降点会给 ~12.5 kHz，而真实内容到 ~20 kHz
    （`packaging/bandwidth_probe.py`）——拿它当 `B` 会把 SDR **修坏**（端到端实测 −1.5 dB）。
    """
    full = corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=FS / 2, seed=4)
    f1, db1 = _spectrum(full, tmp_path, "full.wav")
    info1 = content_bandwidth(f1, db1, FS)
    assert info1["source"] == "nyquist" and info1["bandwidth_hz"] == FS / 2 and info1["cutoff_hz"] is None

    f2, db2 = _spectrum(_natural_rolloff(), tmp_path, "roll.wav")
    info2 = content_bandwidth(f2, db2, FS)
    assert info2["source"] == "nyquist" and info2["bandwidth_hz"] == FS / 2


def test_short_or_missing_spectrum_falls_back_to_nyquist():
    """`f`/`db` 为 `None` 或太短 ⇒ 回落奈奎斯特（`detect_cutoff` 的既有行为，不抛错）。"""
    for f, db in ((None, None), (np.arange(10.0), np.zeros(10))):
        info = content_bandwidth(f, db, FS)
        assert info["source"] == "nyquist" and info["bandwidth_hz"] == FS / 2


# ------------------------------------------------------------------ ③ 参数必须真的传下去
def test_thresholds_are_honoured(tmp_path):
    """`thresholds` 必须**真的生效**：把 `cutoff_min_hz` 抬到奈奎斯特之上 ⇒ 判不出截止。

    （口径来源 = `config.Thresholds`；用 `_DEFAULTS` 的默认值会让用户改设置静默无效。）
    """
    y = corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1)
    f, db = _spectrum(y, tmp_path, "thr.wav")
    assert content_bandwidth(f, db, FS)["source"] == "cutoff"
    assert content_bandwidth(f, db, FS, Thresholds(cutoff_min_hz=25000.0))["source"] == "nyquist"


# ------------------------------------------------------------------ ④ 量出来的 B 真能修好
def test_the_measured_bandwidth_actually_repairs(tmp_path):
    """闭环：拿**测量层给的** `B` 去做去削波，SDR 必须真提升（实测 +34.2 dB）。"""
    p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1),
                       "moderate")
    src = str(tmp_path / "src.wav")
    sf.write(src, p["clipped"].astype(np.float32), FS, subtype="FLOAT")
    a, _ = sf.read(src, dtype="float64", always_2d=True)
    f, db = _spectrum(p["clipped"], tmp_path, "again.wav")
    b = content_bandwidth(f, db, FS)["bandwidth_hz"]
    out, _ = declip.reconstruct(a, None, FS, b)
    assert sdr_db(out, p["clean"]) - sdr_db(a, p["clean"]) > 20.0
