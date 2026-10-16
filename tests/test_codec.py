"""编码器特征推断：截止点对照 + 位深升位检测。"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from audiocheck.core.analyzers.codec import bit_depth_origin, infer_codec


def test_infer_mp3_128_from_16khz():
    hits = infer_codec(16.0, 44100)
    assert any("128kbps" in h for h in hits), hits


def test_infer_mp3_320_from_20khz():
    hits = infer_codec(20.2, 44100)
    assert any("320" in h for h in hits), hits


def test_no_inference_for_full_bandwidth():
    """满带宽（≈22kHz）不该被报成有损编码器特征。"""
    assert infer_codec(22.0, 44100) == []


def test_no_inference_without_cutoff():
    assert infer_codec(None, 44100) == []


def test_inference_scales_with_sample_rate():
    """表是按 44.1k 整理的；96k 容器应按奈奎斯特比例缩放。"""
    hits = infer_codec(16.0, 44100)
    scaled = infer_codec(16.0 * (48000 / 2) / 22050.0, 48000)
    assert hits and scaled


def test_bit_depth_padding_detected(tmp_path):
    """构造一个"16bit 内容装进 24bit 容器"的文件，应检出升位。"""
    fs = 44100
    rng = np.random.default_rng(7)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()                            # 归一化到满刻度
    # 16→24 升位的写法：24bit 字段里存 v16<<8，即 int32 里要 <<16
    # （sf.write 写 PCM_24 取 int32 的高 24 位，所以再右移 8 才是真实存储值）
    ints = (v * 32760).astype(np.int32) << 16
    p = str(tmp_path / "padded.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out
    assert "升位" in out.get("bit_note", "")


def test_quiet_material_is_inconclusive(tmp_path):
    """电平太低的 24bit 文件：有效位深无法可靠估计 → 如实不判，而不是误报升位。"""
    fs = 44100
    rng = np.random.default_rng(9)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max() * 0.02                     # 归一化后再压到约 -34dBFS
    ints = (v * 8380000).astype(np.int32) << 8
    p = str(tmp_path / "quiet.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False, out
    assert out["bit_origin"] == 24, out      # 安静但仍是真 24bit，不能降级报成升位


def test_real_24bit_is_not_flagged(tmp_path):
    """真正用满 24 位的内容不该被误判。

    注意：sf.write 写 PCM_24 时取 int32 的**高 24 位**（即先 >>8），
    所以要写入的 24bit 值 X 必须以 X<<8 的形式传入。
    """
    fs = 44100
    rng = np.random.default_rng(11)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    ints = (v * 8380000).astype(np.int32) << 8          # 低位有内容
    p = str(tmp_path / "real24.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False, out
    assert out["bit_origin"] == 24, out


def test_16bit_file_is_skipped(tmp_path):
    """16bit 文件不做升位检测（没有升位可言）。"""
    fs = 44100
    rng = np.random.default_rng(3)
    p = str(tmp_path / "x.wav")
    sf.write(p, (rng.standard_normal(fs) * 0.3).astype(np.float32), fs, subtype="PCM_16")
    assert bit_depth_origin(p, "PCM_16")["bit_padded"] is False


def test_missing_file_is_graceful():
    out = bit_depth_origin("/no/such/file.flac", "PCM_24")
    assert out["bit_padded"] is False


# ------------------------------------------------- 2026-10-16：真实曲库暴露的缺陷
def test_bit_depth_padding_detected_in_stereo(tmp_path):
    """★ 回归：24bit **立体声** 文件上的升位检测曾**整个失效**。

    根因：`np.bitwise_or.reduce(x)` 默认**只沿 axis 0** 归约，立体声读成 [n, 2]
    时返回**每声道一个值**（1-D 数组）⇒ `int()` 抛
    `TypeError: only 0-dimensional arrays can be converted to Python scalars`，
    被引擎吞成"分析器告警"，于是**位深升位检测在 24bit 立体声上永远跑不出来**——
    而那正是伪 Hi-Res 的主要形态。

    为什么此前没抓到：本文件所有位深用例写的都是**单声道**（1-D ⇒ 默认 axis=0
    恰好等于全归约）。是**真实曲库**（88.2k/24bit 立体声）把它顶出来的。
    """
    fs = 44100
    rng = np.random.default_rng(7)
    vl = rng.standard_normal(fs)
    vr = rng.standard_normal(fs)
    vl, vr = vl / np.abs(vl).max(), vr / np.abs(vr).max()
    ints = np.column_stack([(vl * 32760).astype(np.int32) << 16,
                            (vr * 32760).astype(np.int32) << 16])       # 16bit 内容装 24bit
    p = str(tmp_path / "padded_stereo.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out


def test_bit_depth_origin_reduces_across_channels(tmp_path):
    """归约必须**跨所有声道**：右声道是数字静音时也要能判左声道的升位。"""
    fs = 44100
    rng = np.random.default_rng(8)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    ints = np.column_stack([(v * 32760).astype(np.int32) << 16, np.zeros(fs, np.int32)])
    p = str(tmp_path / "one_silent.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True and out["bit_origin"] == 16, out


def test_real_24bit_stereo_is_not_flagged(tmp_path):
    """真 24bit 立体声（低位有内容）不许被判升位——立体声路径的另一半。"""
    fs = 44100
    rng = np.random.default_rng(11)
    vl = rng.standard_normal(fs)
    vr = rng.standard_normal(fs)
    vl, vr = vl / np.abs(vl).max(), vr / np.abs(vr).max()
    ints = np.column_stack([(vl * 8380000).astype(np.int32) << 8,
                            (vr * 8380000).astype(np.int32) << 8])
    p = str(tmp_path / "real24_stereo.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False and out["bit_origin"] == 24, out
