"""容器与电平统计（流式，内存与文件长度无关）。"""
from __future__ import annotations

import numpy as np
import soundfile as sf

from ..registry import Analyzer, register

BLOCK = 65536

# TT / Pleasurize Music Foundation 规定的 RMS 块长。
# 早先拿「读文件的块」当 RMS 块（65536 采样，48kHz 下 1.37 秒），
# 既不是 3 秒、也不与曲目结构对齐 —— 这是真 bug，不是口径差异。
DR_BLOCK_SECONDS = 3.0
DR_TOP_FRACTION = 0.20    # TT：只取最响的 20% 块参与计算


@register
class BasicAnalyzer(Analyzer):
    key = "basic"
    label = "容器与电平"
    order = 10

    def run(self, ctx) -> dict:
        ch = ctx.channels
        fs = ctx.fs
        peak = sumsq = 0.0
        n = clips = 0
        dr_win = max(1, int(DR_BLOCK_SECONDS * fs))
        dr_acc = np.zeros(ch)
        dr_n = 0
        block_rms: list[float] = []
        corr_num = corr_l = corr_r = 0.0
        mean_l = mean_r = 0.0
        frames_read = 0
        read_error: Exception | None = None

        with sf.SoundFile(ctx.path) as f:
            while True:
                # ⚠ 截断的文件**能打开**（容器头部的 STREAMINFO 还在、帧数照旧声称），
                # 读到一半才失败（实测 libsndfile 给 "flac decoder lost sync" 或
                # "Internal psf_fseek() failed."）。**真实曲库**里就有 3 个这样的文件。
                # 早先不接这个异常 ⇒ 只有一句 libsndfile 英文错误被引擎记进 errors，
                # 用户看不出"文件被截断了"，而结论是拿**部分内容**算出来的。
                try:
                    b = f.read(BLOCK, dtype="float32", always_2d=True)
                except Exception as exc:                          # noqa: BLE001
                    read_error = exc
                    break
                if not len(b):
                    break
                frames_read += b.shape[0]
                peak = max(peak, float(np.abs(b).max()))
                bf = b.astype(np.float64)
                sumsq += float((bf ** 2).sum())
                n += b.size
                clips += int((np.abs(b) >= 0.9999).sum())

                # 3 秒 RMS 块：**按样本精确消费**，把读块的超出部分留给下一个块。
                # 若写成「dr_n >= dr_win 就整块结算」，块长会随读块大小漂移
                # （BLOCK=65536 时第一个块实际是 4.1 秒）——那等于没修干净。
                pos = 0
                while pos < bf.shape[0]:
                    take = min(dr_win - dr_n, bf.shape[0] - pos)
                    dr_acc += (bf[pos:pos + take] ** 2).sum(axis=0)
                    dr_n += take
                    pos += take
                    if dr_n == dr_win:
                        block_rms.append(float(np.sqrt(dr_acc.sum() / (dr_n * ch))))
                        dr_acc[:] = 0.0
                        dr_n = 0

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

        # TT-DR：采样峰值 − 最响 20% 的 3 秒块中**第二响**那一块的 RMS。
        # 取第二响而非最响，是为了不被单个瞬态（一声镲片）主导。
        # ⚠ 选择规则是我对 TT 方法的解读；官方 spec sheet 未能直接核到（PyPI/GitHub 均被拦），
        #   故同时给出 mean20 变体供对照，两者差异通常 < 1 dB。见 docs/theory.md。
        if block_rms and out["peak_dbfs"] is not None:
            br = 20 * np.log10(np.array(block_rms) + 1e-12)
            k = max(1, int(len(br) * DR_TOP_FRACTION))
            top = np.sort(br)[-k:]
            ref = float(top[-2]) if len(top) >= 2 else float(top[-1])
            out["dr_db"] = round(out["peak_dbfs"] - ref, 1)
            out["dr_db_mean20"] = round(out["peak_dbfs"] - float(top.mean()), 1)

        if ch >= 2 and corr_l > 0 and corr_r > 0:
            out["lr_level_diff_db"] = round(
                abs(20 * np.log10((mean_l + 1e-9) / (mean_r + 1e-9))), 2)
            out["_corr"] = corr_num / float(np.sqrt(corr_l * corr_r))

        # 截断：**显式说出来**（E2.1 的规矩——"读不满"与"读完了"必须可区分）。
        # 为什么不直接判 level=error：可读部分（这里 ≥99.8%）的统计是**有效**的，
        # 判 error 会把整条结论丢掉；代价是用户可能没注意——所以文案必须显眼。
        if read_error is not None:
            claimed = int(ctx.frames or 0)
            pct = (100.0 * frames_read / claimed) if claimed else 0.0
            ctx.result.setdefault("errors", []).append(
                "basic: **文件疑似截断**——容器头部声称 %d 帧，实际只读到 %d 帧（%.1f%%）；"
                "以下结论只覆盖可读部分（libsndfile: %s）"
                % (claimed, frames_read, pct, read_error))
            out["read_incomplete"] = True
            out["read_frames"] = frames_read
            out["read_frames_claimed"] = claimed
        return out
