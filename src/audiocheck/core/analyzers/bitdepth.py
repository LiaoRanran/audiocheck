"""有效位深与「升位」检测 —— 用低位统计判据，自己造的（无现成实现可用）。

两条不能省的前提（都踩过）：
  1. **不能按电平判断**。一段安静的 24bit 素材本来就用不满位数，
     按"用到几位"算会把安静误报成"由 16bit 升上来"。
     → 只统计**最响的那些样本**（|x| > 0.25 × 峰值），把电平因素隔离掉。
  2. **不能按固定掩码做**。libsndfile 把 24bit 整数 PCM 读成 int32 时
     是**左移对齐**的（24bit 值放在 32bit 的高 24 位，低 8 位补零）。
     不扣除这个固有位移，任何 24bit 文件都会被测出"低 8 位未使用"。

判据（可复算，见 tests/test_bitdepth.py）：
    unused(b) = 最响样本中，第 b 位为 0 的比例
    从 LSB 起，连续 ≥ DETECT_FRAC 为 0 的位数 = 未被使用的位数
    effective_bits = nominal − unused

抖动（dither）会让低位变成噪声 → unused(b) ≈ 0.5 而非 ≈1.0。
此时**不能**判定升位（抖动正是为了掩盖量化结构），如实报告"无法判定"。
"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from ..registry import Analyzer, register


@register
class BitDepthAnalyzer(Analyzer):
    key = "bitdepth"
    label = "有效位深 / 升位检测"
    order = 15
    requires = ("basic",)

    def run(self, ctx) -> dict:
        # 字段名是 **bits**（soundfile 的 info.subtype，如 'PCM_24'），不是 subtype。
        # 早先写成 get("subtype", ...) 会**永远落到默认值**，
        # 于是 24bit/32bit 文件全被按 16bit 判 —— 实测三个容器都输出 nominal=16。
        return analyze(ctx.path, ctx.result.get("bits", "PCM_16"), ctx.channels)

#: 判定"该位未被使用"的比例阈值
DETECT_FRAC = 0.999
#: 参与统计的样本幅度门限（相对峰值）
LOUD_FRAC = 0.25
#: 每个声道最多统计的样本数（控制内存；统计上远超所需）
MAX_SAMPLES = 2_000_000

#: libsndfile 读整数 PCM 时的对齐位移：容器位深 − 文件标称位深
_ALIGN = {"PCM_16": 16, "PCM_24": 24, "PCM_32": 32, "PCM_S8": 8, "PCM_U8": 8}


def _shift(subtype: str) -> int:
    return 32 - _ALIGN.get(subtype, 32)


def _unused_bits(values: np.ndarray) -> tuple[int, float]:
    """返回 (连续未使用的低位数, 抖动指示)。

    values: 已取绝对值、已右移对齐的整数样本（1 维）。
    """
    if values.size < 64:
        return 0, 0.0
    v = values.astype(np.int64)
    # 只保留非零样本：零样本的任何位都是 0，会把统计带偏
    v = v[v != 0]
    if v.size < 64:
        return 0, 0.0
    unused = 0
    dither_hint = 0.0
    for b in range(24):
        frac_zero = float(np.mean((v & (1 << b)) == 0))
        if b == 0:
            dither_hint = frac_zero
        if frac_zero >= DETECT_FRAC:
            unused += 1
        else:
            break
    return unused, dither_hint


def analyze(path: str, subtype: str, channels: int) -> dict:
    """主入口。subtype 来自 soundfile（如 'PCM_24'）。"""
    nominal = _ALIGN.get(subtype)
    if nominal is None or nominal >= 32:
        # 32bit 整数 / 浮点无法用这个方法（浮点没有量化栅格）
        return {"bit_effective": None, "bit_verdict": "不适用（浮点或 32bit 整数）"}
    shift = _shift(subtype)
    per_ch = []
    dither_flags = []
    with sf.SoundFile(path) as f:
        for c in range(channels):
            # 读 int32，取绝对值后右移对齐
            acc = []
            got = 0
            f.seek(0)
            while got < MAX_SAMPLES:
                want = min(1 << 20, MAX_SAMPLES - got)
                b = f.read(want, dtype="int32", always_2d=True)
                if not len(b):
                    break
                col = np.abs(b[:, c].astype(np.int64)) >> shift
                acc.append(col)
                got += len(b)
            if not acc:
                continue
            v = np.concatenate(acc)
            # 只统计最响的样本，隔离电平因素
            thr = LOUD_FRAC * float(v.max())
            loud = v[v >= thr]
            unused, dh = _unused_bits(loud)
            per_ch.append(nominal - unused)
            dither_flags.append(dh)
    if not per_ch:
        return {"bit_effective": None, "bit_verdict": "无法统计"}
    eff = int(round(float(np.mean(per_ch))))
    # 低位状态分类。**根本局限（不粉饰）**：均匀随机的低位与"抖动"在统计上
    # 不可区分 —— 抖动本来就是故意加成的均匀噪声。所以这种情况只能报告
    # "无法区分真实内容与抖动"，不能假装判出了升位。
    lsb_zero = float(np.mean(dither_flags))
    uniform_low = 0.4 < lsb_zero < 0.6
    if eff < nominal:
        verdict = "**疑似由 %dbit 升位而来**（低 %d 位恒为 0，无抖动）" % (eff, nominal - eff)
    elif uniform_low:
        # 注意主次：unused==0 意味着**没有升位的证据**，这是主要结论；
        # 低位近均匀只是"无法进一步区分真实内容与抖动"的次要注脚，
        # 不该让它把结论搅成"不适用"——那会把这个判据变得几乎永远不出结论。
        verdict = ("未检测到升位（低位有使用）。注：低位呈近均匀分布，"
                   "**无法进一步区分是真实内容还是施加的抖动**")
    else:
        verdict = "未检测到升位（低位有使用）"
    return {
        "bit_nominal": nominal,
        "bit_effective": eff,
        "bit_effective_min": int(min(per_ch)),
        "bit_effective_max": int(max(per_ch)),
        "bit_lsb_zero_frac": round(lsb_zero, 4),
        "bit_dither": uniform_low,
        "bit_verdict": verdict,
    }
