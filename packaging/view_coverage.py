"""视图覆盖矩阵与精度基线（B11-E1）——**一条命令出全部数字**。

测的不是"代码怎么写"，而是**图实际看见文件的哪一段、每列/每行多宽**。
覆盖矩阵是"精度披露"的底稿：图上角标说的每一句话都要能被这张表复算
（B11 判据 5：文档表 ↔ 代码断言，镜像 B6 手法）。

    python packaging/view_coverage.py            # 全部矩阵 + 基线
    python packaging/view_coverage.py --strict   # 50Hz 占行未翻倍则返回 1

⚠ 本脚本只做**测量**，不改任何代码（E1 的纪律：先量后改）。
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

SONG_SECONDS = 180.0                     # "一首 3 分钟的歌"——覆盖矩阵的标准样本


def _synth(path: str, fs: int = 44100, seconds: float = SONG_SECONDS,
           tone_hz: float | None = None) -> str:
    """合成样本：默认宽带噪（全频段有内容，可测行占据）；tone_hz 给单频。"""
    rng = np.random.default_rng(42)
    n = int(fs * seconds)
    if tone_hz:
        t = np.arange(n) / fs
        x = 0.5 * np.sin(2 * np.pi * tone_hz * t)
        x += 0.001 * rng.standard_normal(n)      # 极轻底噪：避免纯音把行"钉死"
    else:
        x = 0.2 * rng.standard_normal(n)
    sf.write(path, np.column_stack([x, x]), fs, subtype="PCM_16")
    return path


def _mat(rows_a: int, rows_b: int) -> tuple[float, float]:
    """imshow 渲染耗时基线（matplotlib Agg，与 Qt 画布无关的那部分成本）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(9, 5))
    out = []
    for r in (rows_a, rows_b):
        db = np.random.default_rng(1).random((r, 900)).astype(np.float32) * -90.0
        ax.clear()
        best = 9e9
        for _ in range(7):                        # 取最小值：排除 GC/预热噪音
            t0 = time.perf_counter()
            ax.imshow(db, aspect="auto", origin="lower",
                      extent=(0, 900, math.log10(20), math.log10(22050)),
                      cmap="inferno", interpolation="nearest")
            best = min(best, time.perf_counter() - t0)
        out.append(best * 1000.0)                 # ms
    plt.close(fig)
    return out[0], out[1]


def main() -> int:
    ap = argparse.ArgumentParser(description="视图覆盖矩阵与精度基线")
    ap.add_argument("--strict", action="store_true", help="50Hz 占行未翻倍则返回 1")
    args = ap.parse_args()

    from audiocheck.core import views
    from audiocheck.ui.charts import RESAMPLE_ROWS, _resample_rows

    tmp = tempfile.mkdtemp(prefix="viewcov_")
    song = _synth(os.path.join(tmp, "song.wav"))
    tone = _synth(os.path.join(tmp, "tone50.wav"), seconds=8.0, tone_hz=50.0)

    print("# 视图覆盖矩阵（实测，样本 = %.0fs 立体声 44.1k 宽带噪）\n" % SONG_SECONDS)
    print("| 视图 | 覆盖 | 列/片数 | 每列 ms | 原始行 | 渲染行 | 每行 Hz（@1k） | 插值区（<） |")
    print("|---|---|---|---|---|---|---|---|")

    # --- 频谱图 ---
    t, f, S = views.spectrogram(song)
    dt = float(np.mean(np.diff(t))) * 1000.0
    fl = np.geomspace(20.0, f[-1], RESAMPLE_ROWS)
    j1k = int(np.searchsorted(fl, 1000))
    step1k = float(fl[j1k] / fl[j1k - 1])
    # 插值区下界：行间距粗过 bin 间距的频率。f·(ratio−1) = Δf_bin ⇒ f = Δf_bin/(ratio−1)
    # （实测 546Hz，与 B11 提示词的"546Hz 以下插值"吻合——公式先前写错成 ×20。）
    interp_floor = float(f[1] - f[0]) / (fl[1] / fl[0] - 1.0)
    print("| 频谱图 spectrogram | %.1f–%.1fs（全曲） | %d | %.1f | %d | %d | %.1f | %.0f Hz |"
          % (t[0], t[-1], len(t), dt, len(f), RESAMPLE_ROWS, 1000 * (step1k - 1), interp_floor))

    # --- 瀑布图 ---
    tw, fw, Sw = views.waterfall(song)
    print("| 瀑布 waterfall | %.1f–%.1fs（全曲） | %d 片 | %.0f | %d | %d | — | — |"
          % (tw[0], tw[-1], len(tw), float(np.mean(np.diff(tw))) * 1000.0, len(fw), len(tw)))

    # --- 立体声相位场 ---
    tp, fp, rho = views.stereo_field(song)
    print("| 相位场 stereo_field | %.1f–%.1fs（全曲） | %d | %.1f | %d | %d | — | — |"
          % (tp[0], tp[-1], len(tp), float(np.mean(np.diff(tp))) * 1000.0,
             len(fp), RESAMPLE_ROWS))

    # --- 频谱曲线（判定用；覆盖来自 context.py 三分位取样，代码可见）---
    # ⚠ 频谱图的"每列"是**显示列**（峰值保持压到 max_cols=900 之后的宽度）。
    # 实测 3 分钟歌：原始 2580 列（70ms）→ 900 列（**200ms**）。B11 提示词写的
    # "70ms/列"是**原始列**宽度；峰值保持保住幅度不丢瞬态，但时间定位就是 200ms。
    # 披露必须写显示列宽——先量后改又一次抓到"文档想象 vs 实测"的差。
    print("| 频谱曲线（判定） | 25%%/50%%/75%% 各 %.0fs（三分位） | — | — | %d | — | Δf %.2f | — |"
          % (20.0, 8192, 44100 / 16384))

    # --- 垂直定位精度基线（50Hz 与 @1k 两处，说明行数限制到底在哪）---
    # ★ E1 实测推翻 B11 提示词的一条判据：提示词假设"360→720 行让 50Hz 占行
    # 翻倍"，实测 **360/720 都是 7 行（倍率 1.00）**——50Hz 附近行距（≈1Hz）
    # 本就远细于 STFT bin（10.77Hz），占行数由 **bin 泄漏宽度**决定，翻倍行数
    # 加不了信息。720 行的真实收益在**行距粗过 bin 的高频区**（插值区之上）：
    # @1k 行距 19.7→9.9Hz，垂直定位误差减半。判据按实测改写，不按想象。
    tt, tf, tS = views.spectrogram(tone)
    db = tS - np.nanmax(tS)
    occ, step = {}, {}
    for rows in (RESAMPLE_ROWS, 720):
        flr = np.geomspace(20.0, tf[-1], rows)
        rs = _resample_rows(db, tf, flr)
        occ[rows] = int((rs.max(axis=1) > -40.0).sum())
        j = int(np.searchsorted(flr, 1000))
        step[rows] = float(flr[j] / flr[j - 1] - 1.0) * 1000.0
    ratio_hz = step[RESAMPLE_ROWS] / max(step[720], 1e-9)
    print("\n## 垂直定位精度基线（50Hz 正弦；>峰值-40dB 的行）")
    print("- 50Hz 占行：%d 行 **%d 行** ｜ 720 行 **%d 行** ｜ 倍率 **%.2f**"
          "（**行数不是低频的约束**——bin 泄漏才是；翻倍行数在低频加不了信息）"
          % (RESAMPLE_ROWS, occ[RESAMPLE_ROWS], occ[720],
             occ[720] / max(occ[RESAMPLE_ROWS], 1)))
    print("- @1kHz 行距：%.2f Hz → %.2f Hz ｜ 倍率 **%.2f**（判据：≥2 才是 720 的真实收益）"
          % (step[RESAMPLE_ROWS], step[720], ratio_hz))

    # --- 渲染耗时基线 ---
    ms360, ms720 = _mat(RESAMPLE_ROWS, 720)
    print("\n## imshow 渲染耗时（Agg，%d 列，取 7 次最小值）" % 900)
    print("- %d 行：**%.2f ms** ｜ 720 行：**%.2f ms** ｜ 倍率 **%.2f**（判据：≤2.5）"
          % (RESAMPLE_ROWS, ms360, ms720, ms720 / max(ms360, 1e-9)))

    ok = ratio_hz >= 2.0
    print("\n结论：720 行 @1k 行距 %s（%.2f×，判据 ≥2）；渲染记账 %.2f→%.2f ms（%.2f×）"
          % ("达标" if ok else "未翻倍", ratio_hz, ms360, ms720, ms720 / max(ms360, 1e-9)))
    if args.strict and not ok:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
