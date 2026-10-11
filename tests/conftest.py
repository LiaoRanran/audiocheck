"""测试夹具：用合成音频做离线回归，不依赖任何外部素材。

为什么用**白噪声 + 砖墙低通**而不是扫频：
对数扫频在高端只停留极短时间，Welch 平均后高频会被压下去，
真实截止点就无法验证。白噪声频谱平坦，加一道刀切低通后截止点即"标准答案"。
"""
from __future__ import annotations

import numpy as np
import pytest
import soundfile as sf
from scipy import signal

RNG_SEED = 20261011


def noise(fs: int, seconds: float = 8.0, amp: float = 0.2, seed: int = RNG_SEED):
    rng = np.random.default_rng(seed)
    return (rng.standard_normal(int(fs * seconds)) * amp).astype(np.float32)


def lowpass(x: np.ndarray, fs: int, hz: float, order: int = 10):
    """巴特沃斯低通——用于"自然滚降"一类素材。"""
    sos = signal.butter(order, hz / (fs / 2), btype="low", output="sos")
    return signal.sosfilt(sos, x).astype(np.float32)


def brickwall(x: np.ndarray, fs: int, hz: float):
    """频域硬切——模拟有损编码器 / 升采样滤波器留下的"砖墙"。

    为什么必须用它而不能全用巴特沃斯：巴特沃斯 10 阶在 1.1×截止频率处才衰减
    十几个 dB，边缘贴近奈奎斯特时（如 48k 容器切 21.4k）谱上根本形不成"洞"，
    判定算法合理地认为"内容直达奈奎斯特"。要测砖墙就得给真的砖墙。
    """
    spec = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1.0 / fs)
    spec[freqs > hz] = 0.0
    return np.fft.irfft(spec, n=len(x)).astype(np.float32)


def stereo(x: np.ndarray, corr: float = 0.9):
    """生成两声道；corr 为右声道相对左声道的增益（None 表示完全相同）。"""
    right = x if corr is None else (x * corr).astype(np.float32)
    return np.column_stack([x, right])


def write(path, data, fs):
    sf.write(str(path), data, fs, subtype="PCM_16")
    return str(path)


@pytest.fixture(scope="session")
def audio(tmp_path_factory):
    """一批"答案已知"的测试音频。"""
    d = tmp_path_factory.mktemp("audio")
    out = {}

    # 1) 44.1k 全带宽 → 期望「CD 级满带宽」
    fs = 44100
    out["cd_full"] = write(d / "cd_full.wav", stereo(noise(fs)), fs)

    # 2) 44.1k 内容止于 16kHz（砖墙）→ 期望「有损溯源」
    out["lossy_16k"] = write(d / "lossy_16k.wav", stereo(brickwall(noise(fs), fs, 16000)), fs)

    # 3) 44.1k 内容止于 19.5kHz（砖墙）→ 期望「轻微带限」
    out["band_19k"] = write(d / "band_19k.wav", stereo(brickwall(noise(fs), fs, 19500)), fs)

    # 3b) 44.1k 自然滚降（2 阶巴特沃斯 ≈ 12dB/oct，真实音乐高频衰减量级）
    #     → 必须**不**被当成"截止"。注意别用高阶：10 阶巴特沃斯是 60dB/oct，
    #       那已经和有损编码器的砖墙一样陡，判成有损反而是对的。
    out["natural_rolloff"] = write(d / "natural_rolloff.wav",
                                   stereo(lowpass(noise(fs), fs, 18000, order=2)), fs)

    # 4) 96k 容器、内容止于 19kHz → 期望「伪 Hi-Res」
    fs = 96000
    out["fake_hires"] = write(d / "fake_hires.wav", stereo(brickwall(noise(fs), fs, 19000)), fs)

    # 5) 96k 真 Hi-Res（内容到 40kHz）→ 期望「真 Hi-Res」
    out["true_hires"] = write(d / "true_hires.wav", stereo(noise(fs), None), fs)

    # 6) 48k 容器、内容止于 21.4kHz（砖墙）→ 期望「疑似 44.1kHz 升频」
    fs = 48000
    out["up_44"] = write(d / "up_44.wav", stereo(brickwall(noise(fs), fs, 21400)), fs)

    # 7) 1kHz 正弦（幅度 0.5）→ 响度/真峰值确定性检验
    t = np.arange(int(fs * 6)) / fs
    tone = (0.5 * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)
    out["tone_50"] = write(d / "tone_50.wav", np.column_stack([tone, tone]), fs)
    # 同一正弦，幅度减半（-6.02 dB）
    out["tone_25"] = write(d / "tone_25.wav",
                           np.column_stack([tone * 0.5, tone * 0.5]).astype(np.float32), fs)

    # 8) 削波素材
    xc = np.clip(noise(44100) * 6.0, -1.0, 1.0).astype(np.float32)
    out["clipped"] = write(d / "clipped.wav", np.column_stack([xc, xc]), 44100)

    return out


@pytest.fixture(scope="session")
def tagged(tmp_path_factory):
    """带完整标签的 FLAC（验证元数据层）。"""
    d = tmp_path_factory.mktemp("tagged")
    p = d / "tagged.flac"
    fs = 44100
    sf.write(str(p), stereo(noise(fs, seconds=2.0)), fs, subtype="PCM_16")
    try:
        from mutagen.flac import FLAC
        f = FLAC(str(p))
        f["title"] = "测试标题"
        f["artist"] = "测试艺术家"
        f["album"] = "测试专辑"
        f["organization"] = "测试唱片公司"
        f["isrc"] = "TEST12345678"
        f["date"] = "2026"
        f.save()
    except Exception:                                          # noqa: BLE001
        pytest.skip("mutagen 不可用，跳过标签测试")
    return str(p)
