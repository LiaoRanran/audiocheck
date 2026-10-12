"""编码器特征推断：把「内容止于 16.0kHz」翻译成「像是 128kbps 的 MP3/AAC」。

两条线索
--------
1. **截止频率对照表**：有损编码器普遍带低通，典型截止频率随码率变化。
2. **位深升位检测**：24/32bit 容器若低位恒为零，说明是从低位深**升位**而来
   （容器写 24bit，信息量仍是 16bit）。

诚实声明
--------
编码器版本、预设、是否显式指定 --lowpass 都会改变实际截止点，
因此第 1 条只能作为**疑似**线索，不能当证据；第 2 条是确定的，可当证据。
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from ..registry import Analyzer, register

# 常见有损编码器在 44.1kHz 容器下的典型截止区间（kHz）
# 取自社区实测汇总（hydrogenaud 等）；仅作线索，不作为判定依据。
CODEC_SIGNATURES = [
    ("MP3 320kbps (LAME)", 19.9, 20.6),
    ("MP3 256kbps", 19.2, 19.9),
    ("MP3 192kbps", 18.5, 19.2),
    ("MP3 160kbps", 17.0, 17.9),
    ("MP3 128kbps", 15.4, 16.6),
    ("MP3 96kbps", 14.3, 15.4),
    ("MP3 64kbps", 10.5, 11.6),
    ("AAC 256kbps", 19.4, 20.6),
    ("AAC 128kbps", 14.8, 16.6),
    ("AAC 96kbps", 13.2, 14.8),
    ("Vorbis q6 (~192kbps)", 19.4, 20.6),
    ("Opus ≥96kbps", 19.4, 20.6),
    ("WMA 128kbps", 15.4, 16.6),
]


def infer_codec(cutoff_khz: float | None, fs: int) -> list[str]:
    """按实测截止点给出可能的编码器/码率（按区间中心距离排序）。"""
    if not cutoff_khz or cutoff_khz <= 0:
        return []
    # 表是按 44.1k 容器整理的；其它采样率下按奈奎斯特比例缩放
    scale = (fs / 2.0) / 22050.0
    hits = []
    for name, lo, hi in CODEC_SIGNATURES:
        lo2, hi2 = lo * scale, hi * scale
        if lo2 <= cutoff_khz <= hi2:
            hits.append((abs(cutoff_khz - (lo2 + hi2) / 2), name))
        elif abs(cutoff_khz - (lo2 + hi2) / 2) <= 0.35:     # 允许 0.35kHz 的容差
            hits.append((abs(cutoff_khz - (lo2 + hi2) / 2) + 10, name))
    hits.sort()
    return [n for _, n in hits[:3]]


def bit_depth_origin(path: str, bits: str) -> dict:
    """检测位深升位：从"实际使用了多少量化级数"反推有效位深。

    为什么不能按固定掩码判断：libsndfile 读 int32 时会把低分辨率内容**左移**对齐
    （16bit 内容 → 左移 16 位），所以"低位全零"的位数取决于读取时的移位量，
    不是固定的 8/16。正确做法是算 `log2(峰值 / 2^最低置位) + 1`（+1 是符号位）。

    返回 {'bit_origin', 'bit_padded', 'bit_note'}；
    电平过低时无法判定，如实返回未检出而不猜。
    """
    out: dict = {"bit_origin": None, "bit_padded": False}
    bu = (bits or "").upper()
    claimed = 24 if "24" in bu else (32 if ("32" in bu and "FLOAT" not in bu) else None)
    if claimed is None:
        return out
    try:
        with sf.SoundFile(path, "r") as f:
            n = min(f.frames, int(f.samplerate * 8))
            if n <= 0:
                return out
            x = f.read(n, dtype="int32", always_2d=False)
    except Exception:                                        # noqa: BLE001
        return out
    x = np.abs(np.asarray(x, dtype=np.int64))
    if x.size == 0:
        return out
    acc = int(np.bitwise_or.reduce(x))
    if acc == 0:                                            # 数字静音，无从判断
        return out
    # libsndfile 把整数 PCM 读成 int32 时会**左移对齐**：24bit 内容读到 int32 是
    # `存储值 << 8`，因此"低位全零"里有 8 位是格式固有的，不是升位造成的。
    # 必须先把固有移位扣掉，剩下的零位才是真正的填充——
    # 否则安静的 24bit 素材（本来就用不满位数）会被误判成升位。
    inherent = 32 - claimed
    low = (acc & -acc).bit_length() - 1                     # 所有样本的最低置位
    extra = low - inherent
    if extra > 0:
        origin = claimed - extra
        out["bit_origin"] = origin
        out["bit_padded"] = True
        out["bit_note"] = ("容器标称 %s，但样本最低 %d 位恒为零（扣除格式固有的 %d 位移位后"
                           "仍有 %d 位填充）→ 实为 %dbit 升位"
                           % (bits, low, inherent, extra, origin))
    else:
        out["bit_origin"] = claimed
    return out


@register
class CodecAnalyzer(Analyzer):
    key = "codec"
    label = "编码器特征推断"
    order = 60
    requires = ("spectrum",)

    def run(self, ctx) -> dict:
        r = ctx.result
        cut = r.get("cutoff_khz")
        cands = infer_codec(cut, ctx.fs)

        out: dict = {"codec_candidates": cands}
        if cands:
            # 只在确实是"内容被截断"时才提示：满带宽文件不该被报成有损
            if cut and cut < (ctx.fs / 2000.0) * 0.97:
                out["codec_note"] = (
                    "内容止于 %.2fkHz，与 %s 等编码器的典型截止一致（**线索，非证据**）"
                    % (cut, " / ".join(cands)))
            else:
                out["codec_candidates"] = []

        out.update(bit_depth_origin(ctx.path, ctx.result.get("bits") or ""))
        if out.get("bit_padded"):
            ctx.metric("bit_origin", "实际位深", group="容器", primary=True)
        return out
