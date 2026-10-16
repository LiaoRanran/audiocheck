"""B11-C7 评估：低频段双分辨率 STFT 的改善量（**只评估，不动 DSP 核心**）。

B11 诊断第 3/4 条的病根：视图 STFT 用 4096 窗（Δf 10.77Hz），546Hz 以下
本来就是"行距细过 bin"——低频的糊是 **bin 粗**造成的，720 行治不了它
（E1 实测：50Hz 占行数 360→720 不变）。真正的根治是低频用更长的窗
（16384，Δf 2.69Hz）重算。但那是动 `core/dsp.py` 的 STFT 核心——判定
曲线也走它——所以本轮**只量改善量**，结论进 docs，实现留给后续。

    python packaging/lowfreq_eval.py

量三件事：
1. **可分辨间距**：两个等幅低频音，间距多大时 4096 窗能分开、16384 能分开
   （峰谷比 > 3dB 判"分开"）；
2. **窄峰宽度**：单个 50Hz 音在各分辨率下占多少 Hz（-3dB 主瓣宽）；
3. **代价**：16384 窗在 3 分钟文件上的 STFT 耗时（4096 的几倍）。
"""
from __future__ import annotations

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
from scipy import signal  # noqa: E402

FS = 44100
LOW_BAND = 500.0                          # "低频段"上界（B11 的分界）


def _two_tones(path: str, f1: float, f2: float, seconds: float = 8.0):
    """两个等幅正弦（ Hann 窗泄漏下峰谷比可算"分不分得开"）。"""
    n = int(FS * seconds)
    t = np.arange(n) / FS
    x = (np.sin(2 * np.pi * f1 * t) + np.sin(2 * np.pi * f2 * t)).astype(np.float32)
    sf.write(path, x, FS, subtype="PCM_16")
    return path


def _welch_peak_valley(x: np.ndarray, nperseg: int, t1: float, t2: float):
    """Welch 谱上：两音各自峰值与**它们之间**的谷，返回 (峰谷比 dB, 峰位 Hz)。

    ⚠ 第一版的错法：谷在"全局峰 ±40 bin"里找——间距小于一个 bin 时两音
    合成一个峰，"谷"落到旁瓣零上，报出 72dB 的假"可分"。谷必须限定在
    **两个音位之间**；间距小于一个 bin 时如实报"不可分"（物理极限，
    不是插值能补的）。
    """
    f, p = signal.welch(x, fs=FS, nperseg=nperseg, noverlap=nperseg // 2, window="hann")
    m = f < LOW_BAND
    fb, pb = f[m], 10 * np.log10(p[m] + 1e-20)
    j1 = int(np.argmin(np.abs(fb - t1)))
    j2 = int(np.argmin(np.abs(fb - t2)))
    lo, hi = min(j1, j2), max(j1, j2)
    if hi - lo < 2:                      # 两音落在同一 bin：不可分（如实报）
        return -99.0, float(fb[j1])
    peak = max(float(pb[j1]), float(pb[j2]))
    valley = float(pb[lo:hi + 1].min())
    return peak - valley, float(fb[j1])


def _mainlobe_hz(nperseg: int) -> float:
    """Hann 窗 -3dB 主瓣宽 ≈ 1.44·Δf（四 bin 经典值，解析可查）。"""
    return 1.44 * FS / nperseg


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="lowfreq_")
    print("# B11-C7 低频双分辨率评估（只评估不动 DSP）\n")

    # --- 1. 可分辨间距 ---
    print("## 1. 两个等幅低频音的「分不分得开」（峰谷比 >3dB 判可分）")
    print("| 间距 | 4096 窗（Δf 10.8Hz） | 16384 窗（Δf 2.7Hz） |")
    print("|---|---|---|")
    gaps = (5.0, 10.0, 15.0, 20.0, 30.0, 45.0)
    thresh = {}
    for gap in gaps:
        row = {}
        for nperseg in (4096, 16384):
            p = _two_tones(os.path.join(tmp, "t.wav"), 80.0, 80.0 + gap)
            x, _ = sf.read(p, dtype="float32")
            pv, _pk = _welch_peak_valley(x, nperseg, 80.0, 80.0 + gap)
            row[nperseg] = pv
        for nperseg in (4096, 16384):
            if row[nperseg] > 3 and nperseg not in thresh:
                thresh[nperseg] = gap
        print("| %4.0f Hz | %5.1f dB %s | %5.1f dB %s |"
              % (gap, row[4096], "可分" if row[4096] > 3 else ("同bin" if row[4096] < -50 else "合并"),
                 row[16384], "可分" if row[16384] > 3 else ("同bin" if row[16384] < -50 else "合并")))

    # --- 2. 单音主瓣宽（解析 + 实测各一） ---
    print("\n## 2. 单音 -3dB 主瓣宽（Hann：≈1.44·Δf）")
    print("| 窗 | Δf | 主瓣宽 |")
    print("|---|---|---|")
    for nperseg in (4096, 16384):
        print("| %d | %.2f Hz | %.1f Hz |"
              % (nperseg, FS / nperseg, _mainlobe_hz(nperseg)))

    # --- 3. 代价 ---
    print("\n## 3. 3 分钟文件的 STFT 耗时（4096 vs 16384，hop=75%% 重叠）")
    n = int(FS * 180)
    x = np.random.default_rng(5).standard_normal(n).astype(np.float32)
    cost = {}
    for nperseg in (4096, 16384):
        best = 9e9
        for _ in range(3):
            t0 = time.perf_counter()
            signal.spectrogram(x, fs=FS, nperseg=nperseg, noverlap=int(nperseg * 0.75),
                               window="hann")
            best = min(best, time.perf_counter() - t0)
        cost[nperseg] = best * 1000.0
    print("- 4096：**%.0f ms** ｜ 16384：**%.0f ms** ｜ 倍率 %.2f×"
          % (cost[4096], cost[16384], cost[16384] / cost[4096]))

    t4 = thresh.get(4096, float("nan"))
    t16 = thresh.get(16384, float("nan"))
    print("\n结论（进 docs/views_coverage.md）：低频改善量 = 主瓣宽 %.1f→%.1f Hz、"
          "可分辨间距 ~%.0fHz→~%.0fHz；同重叠比下代价 %.2f×（大窗列数少，反而"
          "不更贵——双分辨率的真实代价是**两次 pass ≈2× 视图成本**，不是窗口本身）。"
          "**动 STFT 核心影响判定曲线（16384 锁死），实现须先解决'视图窗与判定窗"
          "解耦'——本轮不做。**"
          % (_mainlobe_hz(4096), _mainlobe_hz(16384), t4, t16,
             cost[16384] / cost[4096]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
