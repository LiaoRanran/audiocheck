#!/usr/bin/env python3
"""bandwidth_probe：**"编辑要的 `B` 从哪来"**——把测量层现成的两种读数在真值已知的语料上量一遍。

    python packaging/bandwidth_probe.py

背景（上一轮留下的问题，见 `docs/editor_research.md §8.4`）：去削波的带限重建**必须**有
内容带宽上界 `B`，而上一轮把 `B` **白给**了（合成信号的带宽是真值）。本轮要回答的是
**"真实素材上 `B` 谁量、拿哪条读数"**。测量层现成有两条候选：

* **`detect_cutoff`**（`core/analyzers/spectrum.py`）—— **砖墙截止**：在原始谱上找"陡降 +
  阻带真的是个坑"的位置。**已校准**（v4.1，合成砖墙网格上最大相对偏差 ≤1.3%，
  `docs/theory.md §1.6`），没有砖墙时返回**奈奎斯特**。
* **`spectral_features().rolloff95_hz`**（`core/dsp.py`）—— **能量滚降点**：累计能量的
  95% 落在哪。**从未按真值校准过**（它是个描述量，不是"带限证据"）。

⚠ 本轮**不先写实现**：先量"这两条读数各自偏多少、拿它当 `B` 的去削波 SDR 差多少"，
再定口径（"先量后改"）。

素材全部**真值已知**（`core.edit.corpus`）：砖墙带限（B 已知）、削过一刀的同款、
谐波音（到 17.6 kHz）、满带宽、以及**自然滚降**（一阶低通，**没有**砖墙——用来验证
"滚降点不是带限证据"这条判据）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.analyzers.spectrum import detect_cutoff  # noqa: E402
from audiocheck.core.edit import corpus, declip  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0
NPERSEG = 16384          # 与 `context.AnalysisContext.spectrum()` 的 canonical 口径一致
ITER = 128               # 去削波的 POCS 迭代（与 `declip.DEFAULT_ITERATIONS` 同值）


def welch_db(x1d: np.ndarray, fs: int = FS):
    """与 `AnalysisContext.spectrum()` **同一口径**的 Welch 谱（去直流 + hann + 50% 重叠 → dB）。

    ⚠ 那条 canonical 口径要求素材 ≥ ~1.4 s（`n < nperseg*2` 的三分位取样要够长），
    所以本探针一律用 2 s 素材——短素材上"没有 canonical 谱"这条边界在 `b_short` 里单独量。
    """
    c = np.asarray(x1d, dtype=np.float64)
    c = c - c.mean()
    f, p = signal.welch(c, fs=fs, nperseg=NPERSEG, noverlap=NPERSEG // 2, window="hann")
    return f, 10 * np.log10(p + 1e-20)


def rolloff95(f, db) -> float:
    """`spectral_features` 的滚降点定义（累计能量 95% 处），这里**照抄口径**便于单独比较。"""
    p = np.maximum(10 ** (np.asarray(db) / 10.0), 0.0)
    tot = p.sum()
    if tot <= 0:
        return float("nan")
    cum = np.cumsum(p) / tot
    return float(f[min(np.searchsorted(cum, 0.95), len(f) - 1)])


def natural_rolloff(seconds: float = SECONDS, fs: int = FS, fc: float = 5000.0,
                    order: int = 1, seed: int = 3) -> np.ndarray:
    """**自然滚降**素材：白噪过一阶 Butterworth 低通（~6 dB/倍频程）——**没有砖墙**。"""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(int(seconds * fs))
    sos = signal.butter(order, fc / (fs / 2.0), btype="low", output="sos")
    y = signal.sosfilt(sos, x)
    return (y / np.abs(y).max() * 0.95)[:, None]


def trusted_frame_spectrum(y1d: np.ndarray, ceiling: float, fs: int = FS,
                           nperseg: int = NPERSEG):
    """只用**一个饱和样本都没有**的帧做平均谱（"从可信样本量 B"）——返回 `(f, db, used, total)`。

    动机：severge 档上整条谱都被削波谐波填起来，砖墙证据没了；那能不能只挑"没被削到"的帧来量？
    本函数就是量这个想法**到底行不行**（不是直接实现它）。
    """
    hop = nperseg // 2
    y1d = np.asarray(y1d, dtype=np.float64)
    sat = np.abs(y1d) >= ceiling
    w = np.hanning(nperseg)
    acc, used, total = None, 0, 0
    for s in range(0, max(0, y1d.size - nperseg + 1), hop):
        total += 1
        if sat[s:s + nperseg].any():
            continue
        seg = y1d[s:s + nperseg]
        seg = seg - seg.mean()
        p = np.abs(np.fft.rfft(seg * w)) ** 2
        acc = p if acc is None else acc + p
        used += 1
    if used == 0 or acc is None:
        return None, None, used, total
    f = np.fft.rfftfreq(nperseg, 1.0 / fs)
    return f, 10 * np.log10(acc / used + 1e-20), used, total


def report_row(name: str, y1d: np.ndarray, b_true: float, *, clean=None,
               ceiling: float | None = None):
    """一行：两条读数各自 vs 真值；若有真值干净信号，再报"拿各读数当 B"的 SDR。"""
    f, db = welch_db(y1d)
    cut, _ = detect_cutoff(f, db)
    r95 = rolloff95(f, db)
    nyq = FS / 2.0

    def err(v):
        return "—" if b_true is None or v is None else "%+7.1f%%" % (100 * (v / b_true - 1))

    line = "%-22s %8s %9s %8s %9s %8s" % (
        name, "%.0f" % b_true if b_true else "—",
        "%.0f" % cut if cut else "None", err(cut),
        "%.0f" % r95, err(r95))
    if clean is not None and ceiling is not None:
        s0 = sdr_db(y1d[:, None], clean)
        parts = []
        for tag, b in (("真值", b_true), ("cut", cut if cut else nyq), ("r95", r95), ("nyq", nyq)):
            out, _ = declip.reconstruct(y1d[:, None], ceiling, FS, b, iterations=ITER)
            s1 = sdr_db(out, clean)
            parts.append("%s %+.1f" % (tag, s1 - s0))
        line += "  ｜ SDR 提升: " + " ｜ ".join(parts)
    print(line)


def main() -> None:
    print("带宽读数探针 ｜ fs=%d ｜ 素材 %.0f s ｜ Welch nperseg=%d（canonical 口径）"
          % (FS, SECONDS, NPERSEG))
    print("尺子：`detect_cutoff`（砖墙截止，已校准）与 `rolloff95`（能量滚降，**未校准**）")
    print()
    print("%-22s %8s %9s %8s %9s %8s" % ("素材", "B真值", "cutoff", "误差", "rolloff95", "误差"))
    print("-" * 78)

    # ① 砖墙带限（干净）：这正是 `detect_cutoff` 的主场
    for b in (18000.0, 8000.0):
        y = corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=b, seed=1)
        report_row("砖墙白噪 B=%.0fk" % (b / 1000), y[:, 0], b)

    # ② 砖墙带限（削过一刀）：**削波会在带外造出谐波** —— 砖墙证据会被毁掉吗？
    for tier in ("moderate", "severe"):
        p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1),
                           tier)
        report_row("砖墙白噪18k·削波(%s)" % tier, p["clipped"][:, 0], 18000.0,
                   clean=p["clean"], ceiling=p["ceiling"])

    # ③ 谐波音（到 17.6 kHz，但谱是**离散谱线**，不是砖墙）
    tone = corpus.harmonic_tone(SECONDS, fs=FS, f0=440.0, n_partials=40, seed=2)
    report_row("谐波音(线状到17.6k)", tone[:, 0], 17600.0)

    # ④ 满带宽（没有任何带限证据）
    full = corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=FS / 2.0, seed=4)
    report_row("满带宽白噪", full[:, 0], FS / 2.0)

    # ⑤ 自然滚降（一阶低通）：**没有砖墙**，但能量滚降点会给出一个低值 ⇒ 不能当带限用
    nat = natural_rolloff()
    report_row("自然滚降(1阶LP@5k)", nat[:, 0], None)

    # ⑥ 端到端：拿不同 B 去做去削波，SDR 差多少（砖墙白噪 18k，moderate）
    print()
    print("端到端（砖墙白噪 B=18k，moderate 削波）：拿各读数当 `B` 的去削波 SDR 提升")
    p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1),
                       "moderate")
    report_row("端到端", p["clipped"][:, 0], 18000.0, clean=p["clean"], ceiling=p["ceiling"])

    # ⑦ canonical 谱对素材长度的门槛：**实测**（三分位取样 × `n < 2·nperseg` 的共同结果）
    print()
    print("canonical 谱的长度门槛（`AnalysisContext.spectrum()`，**实测**）")
    import tempfile

    import soundfile as sf

    from audiocheck.core.context import AnalysisContext, io_path
    with tempfile.TemporaryDirectory() as td:
        for sec in (0.5, 0.8, 0.9, 0.95, 1.0, 1.5):
            y = corpus.band_limited_noise(sec, fs=FS, bandwidth=18000.0, seed=1)
            p = os.path.join(td, "t%05.2f.wav" % sec)
            sf.write(p, y.astype(np.float32), FS, subtype="FLOAT")
            f, _db = AnalysisContext(path=io_path(p), deep=False, src_path=p).spectrum()
            print("  %4.2f s（%6d 帧）⇒ canonical 谱 %s"
                  % (sec, y.shape[0], "**有**" if f is not None else "**没有**（⇒ 自动定带宽必须拒绝）"))

    # ⑧ "可信帧"能不能救 severe 档（只用没有任何饱和样本的帧量谱）
    print()
    print("『可信帧』方案（只用无饱和样本的帧量谱）在 severe 档上救得回来吗")
    for tier in ("moderate", "severe"):
        p = corpus.clipped(corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1),
                           tier)
        f_t, db_t, used, total = trusted_frame_spectrum(p["clipped"][:, 0], p["ceiling"])
        if f_t is None:
            print("  %-8s 可用帧 **0/%d** ⇒ 无从量起" % (tier, total))
            continue
        cut_t, _ = detect_cutoff(f_t, db_t)
        print("  %-8s 可用帧 %d/%d ｜ 可信帧 cutoff %.0f（真值 18000，误差 %+.1f%%）"
              % (tier, used, total, cut_t if cut_t else -1,
                 100 * (cut_t / 18000.0 - 1) if cut_t else float("nan")))

    print()
    print("读表要点：")
    print("· 干净的**砖墙**素材：`cutoff` 误差是小量级（已校准），`rolloff95` 系统**偏低**"
          "（能量里总有一截在边缘以下）⇒ 拿它当 B 会**砍掉真实的高频内容**；")
    print("· **削过一刀**之后：砖墙证据还在不在（带外谐波会不会把阻带填起来）→ 看第 ② 组；")
    print("· **满带宽 / 自然滚降**：两条读数都给不出「真带限」⇒ 编辑侧必须退化成「不约束」"
          "（`B = 奈奎斯特` ⇒ 去削波逐位不变），而不是拿滚降点硬砍。")


if __name__ == "__main__":
    main()
