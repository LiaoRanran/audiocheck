"""导出抖动的判据（`render(dither=True)`）。

抖动不是"音质玄学"，它有可算的口径：
* **TPDF**（两个独立均匀相减）+ **0.5 LSB 偏置**——后者是实测出来的：libsndfile 的
  float→PCM 是**截断**（0.0001 → 3 LSB，不是 3.28 的四舍五入），不加偏置会留 −0.5 LSB 直流；
* **量化误差无偏、有界**：均值 |bias| < 0.02 LSB、`max|err| ≤ 2 LSB`、RMS ≈ 0.5 LSB；
* **确定性**：同一输入两次渲染**逐字节相同**（RNG 固定种子 ⇒ `render` 仍是纯函数）；
* **代价如实**：全文件重量化 ⇒ 未编辑处的"逐位不变"承诺**不再成立**（判据钉住）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import DeclickOp, GainOp, Project, render  # noqa: E402
from audiocheck.core.edit.render import (  # noqa: E402
    _DITHER_SEED,
    _PCM_BITS,
    _DitherWriter,
)
from audiocheck.edit_cli import main  # noqa: E402

FS = 48000
LSB16 = 1.0 / 32768.0


def _tone(seconds: float = 1.0, amp: float = 0.4) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return np.column_stack([amp * np.sin(2 * np.pi * 440 * t),
                            amp * np.sin(2 * np.pi * 660 * t)])


def _write(path, x, subtype="FLOAT") -> str:
    sf.write(str(path), x, FS, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


def test_dither_noise_is_the_declared_tpdf_with_a_fixed_seed():
    """逐位钉住：TPDF = `(U1−U2+0.5)·LSB`，种子固定为 0（渲染是纯函数）。"""
    assert _DITHER_SEED == 0

    class _Sink:
        def __init__(self):
            self.data = None

        def write(self, b):
            self.data = np.asarray(b)

    sink = _Sink()
    w = _DitherWriter(sink, LSB16, seed=_DITHER_SEED)
    w.write(np.zeros((4, 2)))
    rng = np.random.default_rng(0)
    expected = ((rng.random((4, 2)) - rng.random((4, 2))) + 0.5) * LSB16
    assert np.allclose(sink.data, expected, rtol=0.0, atol=0.0)


def test_dither_off_is_bit_exact_on_pcm16(tmp_path):
    """不开抖动：PCM16 源的恒等渲染**逐采样不变**（与源解码值完全相同）。"""
    x = _tone()
    src = _write(tmp_path / "a.wav", x, subtype="PCM_16")
    dst = str(tmp_path / "b.wav")
    audit = render(Project(src, []), dst, subtype="PCM_16")
    assert audit["dither"] is False and audit["dither_lsb"] is None
    assert np.array_equal(_read(src), _read(dst))


def test_dither_makes_the_error_unbiased_and_bounded(tmp_path):
    """抖动后：每个采样都动，误差均值 ≈0、RMS ≈0.5 LSB、最大 ≤2 LSB。"""
    x = np.full((FS, 1), 0.0001)            # 直流小信号：量化误差最容易暴露偏置
    src = _write(tmp_path / "a.wav", x)
    dst = str(tmp_path / "b.wav")
    audit = render(Project(src, []), dst, subtype="PCM_16", dither=True)
    assert audit["dither"] is True and audit["dither_lsb"] == LSB16
    y = _read(dst)
    e = (y - x) / LSB16
    assert np.count_nonzero(e) == e.size
    assert abs(float(e.mean())) < 0.02                # 实测 0.0013 LSB
    assert float(np.max(np.abs(e))) <= 2.0 + 1e-9     # 实测 1.277 LSB
    assert 0.45 < float(np.sqrt(np.mean(e ** 2))) < 0.55   # 实测 0.501 LSB


def test_dither_is_deterministic(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    a = str(tmp_path / "a16.wav")
    b = str(tmp_path / "b16.wav")
    render(Project(src, []), a, subtype="PCM_16", dither=True)
    render(Project(src, []), b, subtype="PCM_16", dither=True)
    assert open(a, "rb").read() == open(b, "rb").read()


def test_dither_on_pcm24_uses_the_24bit_lsb(tmp_path):
    src = _write(tmp_path / "a.wav", np.full((FS, 1), 0.0001))
    dst = str(tmp_path / "b.wav")
    audit = render(Project(src, []), dst, subtype="PCM_24", dither=True)
    assert audit["dither_lsb"] == 1.0 / (2 ** 23)
    e = (_read(dst) - 0.0001) / audit["dither_lsb"]
    assert abs(float(e.mean())) < 0.02
    assert float(np.max(np.abs(e))) <= 2.0 + 1e-9


def test_dither_rejects_float_targets_before_writing(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    with pytest.raises(ValueError, match="整型"):
        render(Project(src, []), dst, subtype="FLOAT", dither=True)
    assert not os.path.exists(dst)


def test_dither_in_a_chained_render_still_deterministic(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    proj = Project(src, [GainOp(0.0, 1.0, -3.0), DeclickOp(0.0, 1.0)])
    a, b = str(tmp_path / "a16.wav"), str(tmp_path / "b16.wav")
    audit = render(proj, a, subtype="PCM_16", dither=True)
    render(proj, b, subtype="PCM_16", dither=True)
    assert audit["chained"] is True and audit["dither"] is True
    assert open(a, "rb").read() == open(b, "rb").read()


def test_pcm_bit_table_is_exact():
    """位深表是抖动 LSB 的唯一出处：每个 subtype 都对（PCM8/32 也要钉住）。"""
    assert _PCM_BITS == {"PCM_S8": 8, "PCM_U8": 8, "PCM_16": 16,
                         "PCM_24": 24, "PCM_32": 32}


def test_audit_rounding_duration_and_peak_are_pinned(tmp_path):
    """审计里的 `duration_s`（6 位）、`peak_out_dbfs`（3 位）与时域字段集都逐位钉住。"""
    x = np.full((1000, 1), 0.25)
    src = _write(tmp_path / "a.wav", x)
    audit = render(Project(src, []), str(tmp_path / "b.wav"))
    assert audit["frames"] == 1000
    assert audit["duration_s"] == round(1000 / FS, 6) == 0.020833
    assert audit["peak_out_dbfs"] == round(20.0 * np.log10(0.25), 3) == -12.041
    assert "n_fft" not in audit and "hop" not in audit      # 时域审计不许多出频域字段


def test_silence_peak_is_none_and_does_not_crash(tmp_path):
    src = _write(tmp_path / "a.wav", np.zeros((1000, 1)))
    audit = render(Project(src, []), str(tmp_path / "b.wav"))
    assert audit["peak_out_dbfs"] is None


def test_cli_dither_requires_integer_subtype(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--gain", "0", "--at", "0.0-1.0", "--dither"]) == 2
    assert "整型" in capsys.readouterr().out
    assert not os.path.exists(dst)
    assert main([src, dst, "--gain", "0", "--at", "0.0-1.0",
                 "--subtype", "PCM_16", "--dither"]) == 0
    assert "抖动" in capsys.readouterr().out
    assert os.path.exists(dst)
