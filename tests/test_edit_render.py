"""P0 地基判据：非破坏编辑内核（`core/edit/`）。

每条判据对应 `docs/editor_roadmap.md §5` 的一条"极致/专业"定义：
  1. **无损往返**：未被编辑的采样**逐位不变**（浮点与整型各测一遍）。
  2. **测量互证**：改完用**同一个测量内核**再量一次，证明确实只改了目标（见 `test_measurement_crosscheck`）。
  3. 选区口径与 `ops.GainOp` 的 docstring **逐位对齐**（半开区间 `[t0,t1)`）。
  4. 非破坏硬保证：**不许覆盖源**；削波**不静默**，要如实上报。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import GainOp, Project, render, render_gain  # noqa: E402

FS = 48000


def _sample_at(t: float, fs: int = FS) -> int:
    """与 `render._sample_at` 同一口径（半开区间左端）。"""
    return int(math.ceil(t * fs - 1e-9))


def _tone(seconds: float = 2.0, fs: int = FS) -> np.ndarray:
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 1234 * t)
    return np.column_stack([x, x * 0.9])


def _burst(seconds: float = 2.0, fs: int = FS) -> np.ndarray:
    """安静 0.1 + 在 [0.5,1.0) 一声 0.9 的突发（供测量互证用：峰值只在选区内）。"""
    n = int(seconds * fs)
    x = np.full(n, 0.1)
    x[int(0.5 * fs):int(1.0 * fs)] = 0.9
    return np.column_stack([x, x])


def _write(path, x, subtype: str = "FLOAT", fs: int = FS) -> str:
    sf.write(str(path), x.astype(np.float32), fs, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


# --------------------------------------------------- 1. 无损往返（浮点）
def test_no_op_is_bit_exact(tmp_path):
    """`gain_db == 0.0` 是 no-op：**整轨逐位不变**（浮点 subtype）。"""
    src = _write(tmp_path / "src.wav", _tone())
    p = Project(src, [GainOp(0.2, 1.8, 0.0)])
    assert p.is_identity()
    audit = render(p, str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav")), "no-op 竟然改了采样"
    assert audit["ops"][0]["applied_frames"] == 0


def test_empty_ops_is_bit_exact(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    render(Project(src), str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav"))


# --------------------------------------------------- 2. 选区：只动选中处
def test_gain_only_touches_selection(tmp_path):
    """−6 dB 施加于 `[0.5, 1.0)`：**区外逐位不变**，区内恰为 ∓6 dB。"""
    src = _write(tmp_path / "src.wav", _tone())
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")

    assert np.array_equal(a[:k0], b[:k0]), "选区**之前**的采样被动了"
    assert np.array_equal(a[k1:], b[k1:]), "选区**之后**的采样被动了"
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), rtol=1e-6,
                               err_msg="选区内的实际增益不是 −6 dB")
    assert audit["ops"][0]["applied_frames"] == k1 - k0 == int(0.5 * FS)


def test_selection_edge_convention(tmp_path):
    """半开区间：`[t0, t1)` —— 首样本受影响、`t1` 处那个样本**不**受影响。
    改口径（比如把 `ceil` 换成 `int`）会让这条红。"""
    src = _write(tmp_path / "src.wav", _tone())
    render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")
    assert not np.array_equal(a[k0], b[k0]), "区间**左端**的第一个样本应当受影响"
    assert not np.array_equal(a[k1 - 1], b[k1 - 1]), "区间**右端**的前一个样本应当受影响"
    assert np.array_equal(a[k1], b[k1]), "半开区间：t1 处的样本不应受影响"


def test_ops_compose_in_order(tmp_path):
    """多个操作按加入顺序依次作用；互不重叠的选区各自独立。"""
    src = _write(tmp_path / "src.wav", _tone())
    p = Project(src, [GainOp(0.0, 0.5, -3.0), GainOp(1.0, 1.5, 3.0)])
    render(p, str(tmp_path / "dst.wav"))
    a, b = _read(src), _read(tmp_path / "dst.wav")
    r0, r1 = _sample_at(0.5), _sample_at(1.0)
    np.testing.assert_allclose(b[:r0], a[:r0] * 10.0 ** (-3.0 / 20.0), rtol=1e-6)
    np.testing.assert_allclose(b[r1:_sample_at(1.5)], a[r1:_sample_at(1.5)] * 10.0 ** (3.0 / 20.0),
                               rtol=1e-6)
    assert np.array_equal(a[r0:r1], b[r0:r1]), "两个选区之间的采样被动了"


# --------------------------------------------------- 3. 无损往返（整型 PCM）
def test_pcm16_roundtrip_is_bit_exact(tmp_path):
    """整型 PCM_16 源：no-op 导出必须**逐位不变**（读→写不能悄悄重量化）。"""
    src = _write(tmp_path / "src.wav", _tone(), subtype="PCM_16")
    render(Project(src), str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav")), "PCM_16 往返不逐位相等"


def test_pcm16_gain_outside_is_bit_exact(tmp_path):
    """整型源 + 选区增益：**区外仍逐位不变**（区内允许 1 LSB 量化误差）。"""
    src = _write(tmp_path / "src.wav", _tone(), subtype="PCM_16")
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")
    assert np.array_equal(a[:k0], b[:k0])
    assert np.array_equal(a[k1:], b[k1:])
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), atol=2.0 / 32768)


# --------------------------------------------------- 4. 审计与非破坏保证
def test_audit_record_contents(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    assert audit["samplerate"] == FS and audit["channels"] == 2
    assert audit["subtype"] == "FLOAT"
    assert audit["frames"] == int(2.0 * FS)
    assert abs(audit["duration_s"] - 2.0) < 1e-6
    assert audit["ops"][0]["kind"] == "gain" and audit["ops"][0]["gain_db"] == -6.0
    assert audit["clipped_frames"] == 0


def test_refuses_to_overwrite_source(tmp_path):
    """非破坏硬保证：目标 == 源 → 直接拒绝（`overwrite=True` 也不行）。"""
    src = _write(tmp_path / "src.wav", _tone())
    with pytest.raises(ValueError):
        render_gain(src, src, 0.0, 1.0, -6.0)
    with pytest.raises(ValueError):
        render_gain(src, src, 0.0, 1.0, -6.0, overwrite=True)


def test_refuses_existing_dest_without_overwrite(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    dst = _write(tmp_path / "dst.wav", _tone())
    with pytest.raises(FileExistsError):
        render_gain(src, dst, 0.0, 1.0, -6.0)
    render_gain(src, dst, 0.0, 1.0, -6.0, overwrite=True)      # 显式允许才覆盖


def test_clipping_is_reported_not_silent(tmp_path):
    """整数 subtype 上 +6 dB 必然削波：**必须如实上报**（不许静默钳位）。"""
    loud = np.full((int(0.5 * FS), 2), 0.9)
    src = _write(tmp_path / "src.wav", np.vstack([loud, loud]), subtype="PCM_16")
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.0, 0.5, 6.0)
    assert audit["clipped_frames"] > 0, "削波没有被统计（静默钳位=糊弄）"
    out = _read(tmp_path / "dst.wav")
    assert np.abs(out).max() <= 1.0 + 1e-9


def test_truncated_source_is_reported_not_crashed(tmp_path):
    """截断源（真实曲库里就有这种下载没下全的文件）：渲染**不许 traceback**，
    要如实记 `read_incomplete`，并把已读部分照常写出。"""
    src = str(tmp_path / "src.flac")
    sf.write(src, _tone(3.0).astype(np.float32), FS, subtype="PCM_16")
    raw = open(src, "rb").read()
    open(src, "wb").write(raw[:int(len(raw) * 0.6)])          # 砍掉 40%
    dst = str(tmp_path / "dst.wav")
    audit = render_gain(src, dst, 0.0, 1.0, -3.0, subtype="PCM_16")   # 不许抛
    assert audit["read_incomplete"] is True, "截断没有被上报"
    assert 0 < audit["frames"] < audit["frames_claimed"], (
        "应当只读出一部分：实读 %d / 声称 %d" % (audit["frames"], audit["frames_claimed"]))
    got, _ = sf.read(dst, always_2d=True)
    assert got.shape[0] == audit["frames"], "写出的帧数应与实读一致"


# --------------------------------------------------- 5. 与测量内核互证
def test_measurement_crosscheck(tmp_path):
    """**用同一个测量内核再量一次**：证明增益确实只改了选中处。

    素材设计（关键是让**峰值**本身成为证据）：安静 0.1，选区内一声 0.9。
    对选区 −20 dB 后：突发变成 0.09 ⇒ **新的峰值必须是选区外的 0.1（≈ −20 dBFS），
    而不是 0.9×0.1（≈ −40.9 dBFS）**。
      * 若峰值 ≈ −20.0 dBFS ⇒ 突发被压了 20 dB，**且选区外没被压**（否则峰值会掉到 0.01）。
      * 若实现把整轨都压了，或选区挪位，这条会立刻红。
    """
    from audiocheck.core.engine import analyze

    src = _write(tmp_path / "src.wav", _burst())
    dst = str(tmp_path / "dst.wav")
    render_gain(src, dst, 0.5, 1.0, -20.0)

    p_src = analyze(src, deep=False)["peak_dbfs"]
    p_dst = analyze(dst, deep=False)["peak_dbfs"]
    assert abs(p_src - 20 * math.log10(0.9)) < 0.05, "素材峰值不是 0.9"
    assert abs(p_dst - 20 * math.log10(0.1)) < 0.05, (
        "导出后峰值 %.2f dBFS 不等于选区外的 0.1（%.2f）——增益没有只作用在选区内"
        % (p_dst, 20 * math.log10(0.1)))
