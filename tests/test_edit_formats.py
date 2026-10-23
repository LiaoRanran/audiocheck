"""P0 往返测试的**格式覆盖**：WAV / FLAC / AIFF × PCM_16 / PCM_24 / FLOAT。

`docs/editor_roadmap.md` 的 P0 出口判据是"读→改→写→读，**未选中处逐位不变**"；
此前它只在 WAV 上验过（`tests/test_edit_render.py`）。本文件把 **FLAC / AIFF** 补上，
并钉住两条**实测发现**：

1. **0 dB（恒等）渲染是整文件逐位不变的**——三种容器都成立（不是"近似"）；
2. **源子类型装不进目标容器**时（`FLOAT` → `.flac`）必须在**动目标文件之前**拒绝，
   并给出修法（`subtype="PCM_24"`），而不是让 libsndfile 在建了一半文件后抛
   `Invalid combination of format, subtype and endian`（实测踩到）。
"""
from __future__ import annotations

import hashlib
import os

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core.edit import DehumOp, GainOp, Project, render

FS = 48000

#: (扩展名, 子类型)：libsndfile 对这七种组合都支持（实测，见本文件的历史）。
FORMATS = [
    ("wav", "PCM_16"), ("wav", "PCM_24"), ("wav", "FLOAT"),
    ("flac", "PCM_16"), ("flac", "PCM_24"),
    ("aiff", "PCM_16"), ("aiff", "PCM_24"),
]


def _material(seconds: float = 1.0, channels: int = 1) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    base = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 1000 * t)
    if channels == 1:
        return base
    return np.column_stack([base, 0.7 * base])


def _write(tmp_path, name: str, x: np.ndarray, subtype: str) -> str:
    p = tmp_path / name
    sf.write(str(p), x, FS, subtype=subtype)
    return str(p)


@pytest.mark.parametrize(("ext", "subtype"), FORMATS)
def test_gain_round_trip_keeps_outside_bit_identical(tmp_path, ext, subtype):
    """-6 dB 增益：**选区之外逐位不变**；选区内实测 −6.000 dB；子类型原样保留。"""
    src = _write(tmp_path, "a." + ext, _material(), subtype)
    dst = str(tmp_path / ("b." + ext))
    audit = render(Project(src, [GainOp(0.25, 0.5, -6.0)]), dst)
    assert audit["subtype"] == subtype, audit["subtype"]
    s, fs = sf.read(src, dtype="float64")
    y, fs2 = sf.read(dst, dtype="float64")
    assert fs2 == fs
    k0, k1 = 12000, 24000
    assert np.array_equal(y[:k0], s[:k0]), "选区之前被改了"
    assert np.array_equal(y[k1:], s[k1:]), "选区之后被改了"
    ratio = 20.0 * np.log10(np.sqrt(np.mean(y[k0:k1] ** 2)) / np.sqrt(np.mean(s[k0:k1] ** 2)))
    assert ratio == pytest.approx(-6.0, abs=0.02), "实测 %r dB" % ratio


@pytest.mark.parametrize(("ext", "subtype"),
                         [("wav", "PCM_16"), ("flac", "PCM_16"), ("aiff", "PCM_24")])
def test_zero_db_render_is_identical_over_the_whole_file(tmp_path, ext, subtype):
    """**恒等渲染（0 dB）必须整文件逐位不变**——三次量化不许引入漂移。"""
    src = _write(tmp_path, "a." + ext, _material(channels=2), subtype)
    dst = str(tmp_path / ("b." + ext))
    render(Project(src, [GainOp(0.25, 0.5, 0.0)]), dst)
    s, _ = sf.read(src, dtype="float64")
    y, _ = sf.read(dst, dtype="float64")
    assert np.array_equal(s, y)


def test_source_is_never_modified(tmp_path):
    """非破坏的硬保证：渲染前后**源文件字节不变**（sha256）。"""
    src = _write(tmp_path, "a.flac", _material(), "PCM_16")
    before = hashlib.sha256(open(src, "rb").read()).hexdigest()
    render(Project(src, [GainOp(0.0, 0.5, -3.0)]), str(tmp_path / "b.flac"))
    after = hashlib.sha256(open(src, "rb").read()).hexdigest()
    assert before == after


def test_dehum_on_flac_keeps_outside_bit_identical(tmp_path):
    """修复算子 × 格式：去嗡在 FLAC 上同样**选区之外逐位不变**（LTI 只动选区内）。"""
    t = np.arange(FS * 2) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t)
    for k in (1, 2, 3):
        x = x + 0.02 * np.sin(2 * np.pi * 50 * k * t) / k
    src = _write(tmp_path, "a.flac", x, "PCM_24")
    dst = str(tmp_path / "b.flac")
    audit = render(Project(src, [DehumOp(0.5, 1.5, 50.0)]), dst)
    assert audit["ops"][0]["harmonics_used"] == [1, 2, 3]
    s, _ = sf.read(src, dtype="float64")
    y, _ = sf.read(dst, dtype="float64")
    k0, k1 = 24000, 72000
    assert np.array_equal(y[:k0], s[:k0]) and np.array_equal(y[k1:], s[k1:])
    assert not np.array_equal(y[k0:k1], s[k0:k1])


def test_float_source_into_flac_is_refused_before_touching_the_target(tmp_path):
    """`FLOAT` → `.flac`：**导出前**拒绝，信息里给修法；目标文件**不许**被创建。"""
    src = _write(tmp_path, "a.wav", _material(), "FLOAT")
    dst = str(tmp_path / "b.flac")
    with pytest.raises(ValueError, match="PCM_24"):
        render(Project(src, [GainOp(0.1, 0.2, -3.0)]), dst)
    assert not os.path.exists(dst), "拒绝路径不许留下半个目标文件"


def test_float_source_into_flac_works_with_an_explicit_subtype(tmp_path):
    """显式给 `subtype="PCM_24"` ⇒ 同一对文件能导出，审计记实际子类型。"""
    src = _write(tmp_path, "a.wav", _material(), "FLOAT")
    dst = str(tmp_path / "b.flac")
    audit = render(Project(src, [GainOp(0.1, 0.2, -3.0)]), dst, subtype="PCM_24")
    assert audit["subtype"] == "PCM_24"
    y, _ = sf.read(dst, dtype="float64")
    assert y.shape == _material().shape
