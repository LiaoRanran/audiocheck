"""B11-E1 回归：视图覆盖矩阵与代码一致（文档表 ↔ 代码断言，镜像 B6 手法）。

钉住三件实测事实（都是"先量后改"量出来的，不许再退回去）：

1. **频谱图覆盖全曲**（B14 已修，这里钉住不回头）；
2. **显示列宽 200ms**（不是原始 70ms——披露必须写显示值）；
3. **720 行的收益在 @1k 行距（2.01×），50Hz 占行不变（bin 限制）**
   ——B11 提示词原判据"50Hz 占行翻倍"被实测推翻，按实测改写。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core import views  # noqa: E402
from audiocheck.ui.charts import RESAMPLE_ROWS, _resample_rows  # noqa: E402

FS = 44100


def _song(path: str, seconds: float = 180.0):
    import soundfile as sf
    rng = np.random.default_rng(42)
    x = 0.2 * rng.standard_normal(int(FS * seconds)).astype(np.float32)
    sf.write(path, np.column_stack([x, x]), FS, subtype="PCM_16")
    return path


def _tone(path: str, hz: float = 50.0, seconds: float = 8.0):
    import soundfile as sf
    t = np.arange(int(FS * seconds)) / FS
    x = (0.5 * np.sin(2 * np.pi * hz * t)).astype(np.float32)
    x += 0.001 * np.random.default_rng(42).standard_normal(x.size).astype(np.float32)
    sf.write(path, np.column_stack([x, x]), FS, subtype="PCM_16")
    return path


def test_spectrogram_covers_whole_track(tmp_path):
    """覆盖矩阵第 1 行：3 分钟文件的时间轴 ≈ 0–180s（±1 显示列）。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "song.wav")))
    assert t[0] < 0.3 and abs(t[-1] - 180.0) < 0.3, (t[0], t[-1])
    assert len(t) <= 900, "显示列应被峰值保持压到 max_cols 内，实得 %d" % len(t)


def test_displayed_column_is_200ms_not_70ms(tmp_path):
    """披露口径：**显示列宽** ≈ 200ms（2580 原始列 ×70ms 压到 900 列）。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "song.wav")))
    dt = float(np.mean(np.diff(t)))
    assert 0.15 < dt < 0.26, "显示列宽应在 200ms 量级，实得 %.3fs" % dt
    # 原始 hop（75% 重叠 4096 窗）确实是 69.7ms——两条都在，别混
    assert abs(0.75 * 4096 / FS * 1000 - 69.7) < 0.5


def test_720_rows_halves_step_at_1k(tmp_path):
    """720 行的真实收益：@1kHz 行距减半（≥2×）。低频不是行的约束。"""
    t, f, S = views.spectrogram(_song(str(tmp_path / "song.wav")))
    steps = []
    for rows in (RESAMPLE_ROWS, 720):
        fl = np.geomspace(20.0, f[-1], rows)
        j = int(np.searchsorted(fl, 1000))
        steps.append((fl[j] / fl[j - 1] - 1.0) * 1000.0)
    assert steps[0] / steps[1] >= 2.0, steps


def test_50hz_occupancy_is_bin_limited_not_row_limited(tmp_path):
    """50Hz 占行数与行数无关（bin 泄漏决定）——实测 360/720 都是 7 行。

    这条钉住"提示词原判据被推翻"的事实：谁再把"50Hz 翻倍"写成目标，
    先来看这个数。低频细腻度受 STFT bin 限制（见 docs/views_coverage.md）。
    """
    t, f, S = views.spectrogram(_tone(str(tmp_path / "tone.wav")))
    db = S - np.nanmax(S)
    occ = []
    for rows in (RESAMPLE_ROWS, 720):
        rs = _resample_rows(db, f, np.geomspace(20.0, f[-1], rows))
        occ.append(int((rs.max(axis=1) > -40.0).sum()))
    assert occ[0] == occ[1], "50Hz 占行不应随行数变化（bin 限制）：%s" % occ
    assert occ[0] > 0


def test_coverage_doc_matches_code():
    """文档表 ↔ 代码断言：docs/views_coverage.md 的"渲染行"必须等于代码值。"""
    doc = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "docs", "views_coverage.md")
    assert os.path.exists(doc), "覆盖矩阵文档缺失（B11 判据 5）"
    text = open(doc, encoding="utf-8").read()
    assert str(RESAMPLE_ROWS) in text, "文档表里的渲染行数与代码不一致：RESAMPLE_ROWS=%d" % RESAMPLE_ROWS
    assert "200.0" in text, "文档必须记显示列宽 200ms（披露口径的底稿）"
