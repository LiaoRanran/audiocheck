"""容器与电平统计（流式，内存与文件长度无关）。"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from ..registry import Analyzer, register

BLOCK = 65536


@register
class BasicAnalyzer(Analyzer):
    key = "basic"
    label = "容器与电平"
    order = 10

    def run(self, ctx) -> dict:
        ch = ctx.channels
        peak = sumsq = 0.0
        n = clips = 0
        block_rms: list[float] = []
        corr_num = corr_l = corr_r = 0.0
        mean_l = mean_r = 0.0

        with sf.SoundFile(ctx.path) as f:
            while True:
                b = f.read(BLOCK, dtype="float32", always_2d=True)
                if not len(b):
                    break
                peak = max(peak, float(np.abs(b).max()))
                bf = b.astype(np.float64)
                sumsq += float((bf ** 2).sum())
                n += b.size
                clips += int((np.abs(b) >= 0.9999).sum())
                block_rms.append(float(np.sqrt((bf ** 2).mean())))
                if ch >= 2:
                    lc, rc = bf[:, 0], bf[:, 1]
                    corr_num += float((lc * rc).sum())
                    corr_l += float((lc * lc).sum())
                    corr_r += float((rc * rc).sum())
                    mean_l += float(np.abs(lc).sum())
                    mean_r += float(np.abs(rc).sum())

        rms = np.sqrt(sumsq / max(n, 1))
        out = {
            "peak_dbfs": round(20 * np.log10(peak + 1e-9), 2) if peak > 0 else None,
            "rms_dbfs": round(20 * np.log10(rms + 1e-9), 2),
            "clip_pct": round(clips / max(n, 1) * 100, 4),
        }
        if out["peak_dbfs"] is not None:
            out["crest_db"] = round(out["peak_dbfs"] - out["rms_dbfs"], 1)

        # 近似 TT-DR：采样峰值 − 最响 20% 块的均方根
        # 注意：不能用「块 RMS 的最大值」，那对恒定响度曲目会算出 ≈0 的假结果
        if block_rms:
            br = 20 * np.log10(np.array(block_rms) + 1e-9)
            top = br[np.argsort(br)[-max(1, len(br) // 5):]]
            out["dr_db"] = round(out["peak_dbfs"] - float(top.mean()), 1)

        if ch >= 2 and corr_l > 0 and corr_r > 0:
            out["lr_level_diff_db"] = round(
                abs(20 * np.log10((mean_l + 1e-9) / (mean_r + 1e-9))), 2)
            out["_corr"] = corr_num / float(np.sqrt(corr_l * corr_r))
        return out
