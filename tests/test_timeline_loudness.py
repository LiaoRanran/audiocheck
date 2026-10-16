"""timeline 逐段响度维度回归（内核生长：时间定位补上响度轴）。

时间定位原来只有削波/峰值/RMS 三个时域数。本轮补上**逐段响度**
（ITU-R BS.1770：LUFS + 真峰值）——响度战争/压限/局部削峰的段级证据
因此可被时间定位。钉住六条：

1. **口径与整轨同一套数学**：整轨喂进 `segment_loudness`，LUFS 与
   `dsp.loudness` 完全一致（复用 k_weighting/_gated_loudness/true_peak，
   不是另写一套——dsp.py 是核心资产，不许分叉）；
2. **数值可解析追溯**：1kHz/0.5 幅度正弦 ⇒ LUFS = -0.691 + 10·log10(0.25)
   + K加权增益@1kHz(0.70dB) = -6.01，真峰值 ≈ -6.02（±0.05）；
3. **只报数值不判级**：note 保留"逐段频谱未做"，响度维度明标"只报数值"；
4. **顶到上限的段可定位**：后半段真峰值 ≥ 0 dBFS ⇒ 进
   `timeline_loudness_problems`，前半段（0.3 正弦，-10.5 dBFS）不误报；
5. **缺数据给 None 不静默 0**：短段（< 4 个 100ms 步）LUFS=None；
   数字静音段 LUFS/真峰值皆 None；
6. **CLI 可见**：真峰值问题段以"响度"前缀出现在文本报告里。
"""
from __future__ import annotations

import io
import os
import sys
from contextlib import redirect_stdout

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

import soundfile as sf  # noqa: E402

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core import dsp  # noqa: E402
from audiocheck.core.analyzers.timeline import segment_loudness  # noqa: E402
from audiocheck.core.engine import analyze  # noqa: E402


# ==================================================== 1+2. 口径等价与解析锚点
class TestSegmentLoudnessIsStandardMath:
    def test_segment_loudness_matches_whole_track(self, audio):
        """整轨喂进 segment_loudness ⇒ 与 dsp.loudness 完全同数。

        这是"复用标准原语"的**唯一硬证据**：分块（100ms 步/400ms 块）、
        滤波（k_weighting）、门限（_gated_loudness）全部走 dsp，
        任何一处分叉都会让两个数不等。tone 与噪声各测一次
        （tone 所有块等响、噪声考验门限）。
        """
        for key in ("tone_50", "cd_full"):
            p = audio[key]
            x, fs = sf.read(p, dtype="float32", always_2d=True)
            ref = dsp.loudness(p, fs, x.shape[1], x.shape[0])
            got = segment_loudness(x, fs)
            assert got["lufs"] == ref["lufs"], (key, got, ref)
            assert got["true_peak_dbfs"] == ref["true_peak_dbfs"], (key, got, ref)

    def test_tone_lufs_is_analytically_traceable(self, audio):
        """1kHz/0.5 幅度 ⇒ LUFS = -0.691 + 10·log10(2×0.5²/2) + 0.70 = -6.01。

        解析式可复算：双声道均方和 0.25；K 加权在 1kHz 处 +0.70 dB
        （高架从 1681Hz 起，1kHz 已有过渡——不是 0，实测 0.6977）；
        真峰值 = 20·log10(0.5) = -6.02（1kHz 远低于奈奎斯特，过采样不改变包络）。
        """
        x, fs = sf.read(audio["tone_50"], dtype="float32", always_2d=True)
        got = segment_loudness(x, fs)
        assert got["lufs"] == pytest.approx(-6.01, abs=0.05), got
        assert got["true_peak_dbfs"] == pytest.approx(-6.02, abs=0.05), got

    def test_loudness_rises_with_level(self):
        """同信号电平升 6.02dB ⇒ LUFS 升约 6.02（防符号/单位写反）。"""
        fs = 48000
        t = np.arange(int(fs * 3)) / fs
        lo = segment_loudness((0.25 * np.sin(2 * np.pi * 1000 * t))[:, None].astype(np.float32), fs)
        hi = segment_loudness((0.5 * np.sin(2 * np.pi * 1000 * t))[:, None].astype(np.float32), fs)
        assert hi["lufs"] - lo["lufs"] == pytest.approx(6.02, abs=0.05), (lo, hi)


# ==================================================== 3. 只报数值不判级
def test_note_marks_loudness_as_numbers_only(tmp_path):
    """note 必须同时保留两句老规矩、并如实标出响度维度"只报数值"。"""
    fs = 48000
    x = (np.random.default_rng(5).standard_normal(fs * 40) * 0.2).astype(np.float32)
    p = tmp_path / "long.wav"
    sf.write(str(p), np.column_stack([x, x]), fs, subtype="PCM_16")
    r = analyze(str(p))
    note = r["timeline_note"]
    assert "逐段频谱未做" in note, note          # C4.3 老规矩不许丢
    assert "逐段响度" in note and "只报数值" in note, note


# ==================================================== 4. 顶到上限的段可定位
def test_true_peak_problem_located_in_hot_half(tmp_path):
    """后半段顶到满刻度 ⇒ 真峰值问题定位到后半段，前半段（0.3 正弦
    -10.5 dBFS）不误报。clean 用正弦而非噪声：0.2σ 噪声的样本峰可达
    0.98，真峰值贴着 0 dBFS，会偶发误报。"""
    fs = 44100
    rng = np.random.default_rng(7)
    t = np.arange(fs * 5) / fs
    clean = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    hot = np.clip(rng.standard_normal(fs * 5) * 6.0, -1.0, 1.0).astype(np.float32)
    p = tmp_path / "halftone.wav"
    sf.write(str(p), np.column_stack([np.concatenate([clean, hot])] * 2),
             fs, subtype="PCM_16")
    r = analyze(str(p), thresholds=Thresholds(seg_seconds=2.0, seg_overlap=0.5,
                                              seg_min_count=4))
    probs = r["timeline_loudness_problems"]
    assert probs, "后半段顶到满刻度没有被定位出来：%s" % r.get("timeline_note")
    assert all(q["start"] >= 2.5 for q in probs), [q["span"] for q in probs]
    assert all("真峰值" in q["text"] and "顶到上限" in q["text"] for q in probs)
    assert all("疑似" in q["text"] for q in probs)      # 顶到上限是事实，削峰是推断
    # 每一行都带两个数值（可追溯），且热段读数 ≥ 0
    rows = r["timeline"]
    assert all("lufs" in row and "true_peak_dbfs" in row for row in rows)
    hot_rows = [row for row in rows if row["start"] >= 3.5]
    assert hot_rows and all(row["true_peak_dbfs"] >= 0.0 for row in hot_rows)
    assert r["verdict"], "整轨结论必须保留（与问题段并列，不是替代）"


# ==================================================== 5. None 是正式取值
def test_short_segment_gets_true_peak_but_no_lufs():
    """段长 < 4 个 100ms 步 ⇒ LUFS=None（门限积分无意义），真峰值照报。

    None 是正式取值（C0-4）：不许静默降级成 0.0。
    """
    fs = 48000
    t = np.arange(int(fs * 0.2)) / fs           # 200ms：hop=4800，只有 2 步
    x = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    got = segment_loudness(x[:, None], fs)
    assert got["lufs"] is None
    assert got["true_peak_dbfs"] == pytest.approx(-6.02, abs=0.05), got


def test_digital_silence_yields_none_not_zero():
    """数字静音 ⇒ LUFS 与真峰值皆 None（不静默 0）。"""
    x = np.zeros((48000 * 2, 2), dtype=np.float32)
    got = segment_loudness(x, 48000)
    assert got["lufs"] is None and got["true_peak_dbfs"] is None


def test_empty_segment_returns_none_pair():
    """空段 ⇒ 两个 None，不崩。"""
    assert segment_loudness(np.zeros((0, 2), dtype=np.float32), 48000) == {
        "lufs": None, "true_peak_dbfs": None}


# ==================================================== 6. CLI 可见
def test_cli_prints_loudness_problems(tmp_path):
    """真峰值问题段出现在文本报告里（前缀"响度"），与削波段并列。"""
    from audiocheck.cli import show  # noqa: PLC0415
    fs = 44100
    rng = np.random.default_rng(7)
    t = np.arange(fs * 5) / fs
    clean = (0.3 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    hot = np.clip(rng.standard_normal(fs * 5) * 6.0, -1.0, 1.0).astype(np.float32)
    p = tmp_path / "halftone.wav"
    sf.write(str(p), np.column_stack([np.concatenate([clean, hot])] * 2),
             fs, subtype="PCM_16")
    r = analyze(str(p), thresholds=Thresholds(seg_seconds=2.0, seg_overlap=0.5,
                                              seg_min_count=4))
    buf = io.StringIO()
    with redirect_stdout(buf):
        show(r, quiet=False)
    out = buf.getvalue()
    assert "响度" in out and "顶到上限" in out, out
