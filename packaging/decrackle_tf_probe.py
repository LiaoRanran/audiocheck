#!/usr/bin/env python3
"""decrackle_tf_probe：**新检测量具**能不能把"破音"修得比"更松的 DeclickOp"更好？（先量，再决定）

## 问题（接 `docs/editor_research.md §17` 的挂账）

§17 量清了机制：全段 MAD 尺子被**合法高频内容**主导——小脉冲（幅度 0.02–0.2）的两点
残差 γ = 0.23–2.5，连 α=3 都过不了；降 α 换 TPR 的代价是误报（"代价不对称"）。
LPC(64) 也被否决（−14…−0.2 dB）。要落地 `decrackle` 需要**另一个量具**：时频域离群点 /
稳健局部尺度。本轮把两个候选量出来：

* **C2 局部尺度**：检出判据仍是两点残差，但尺子从"全段 MAD"换成**滑动窗口的局部 MAD**
  （`s[n] = 1.4826·median(|r[n±w]|)`）——机理：合法高频的残差是**持续**的（每处都大），
  脉冲的残差是**孤峰**（邻域里只有它大）⇒ 局部尺子天然把两者分开。
  ⚠ 咔哒会污染自己的窗口 ⇒ 窗要够宽（默认 ±100 采样）。
* **C3 时频两级**：STFT 里脉冲是**跨频段的竖条**（宽带上同时离群），谐波是**横条**
  （每 bin 持续）⇒ 第一级在幅度谱上按"逐 bin 时间中值"找竖条帧（粗定位），
  第二级只在被标记的帧**附近**用局部 MAD 两点残差精检（不污染全局尺子）。

对照：C1 = 现行 `DeclickOp`（全段 MAD，α=5 / 宽度守卫 4）、C1L = 更松设置（α=3，
§17 已量过 ΔSDR +3.1 dB 但误报代价大）。

## 量什么

`corpus.crackle_train`（位置/幅度真值）：ΔSDR / TPR / precision / **误报**
（干净谐波音、带限白噪、合法瞬态上被改的样本数——"代价不对称"的教训）。

    python packaging/decrackle_tf_probe.py
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, declick, stft  # noqa: E402
from audiocheck.core.edit.declick import _repair_runs, residual  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
T = 2.0
RATES = (10.0, 100.0, 500.0)
AMP_RANGES = ((0.02, 0.2), (0.05, 0.5))
LOCAL_WIN = 100          # C2 的半窗宽（采样）
N_FFT = 1024
HOP = 256
MED_FRAMES = 5           # C3 第一级：时间中值窗（帧）
BIN_OUTLIER_K = 8.0      # C3 第一级：逐 bin 离群倍数
FRAME_MIN_BINS = 12      # C3 第一级：多少个 bin 同时离群才算"竖条"帧
FRAME_PAD = 2            # C3 第二级：标记帧两侧各扩几帧再精检
ALPHA_LOCAL = 5.0        # C3 第二级：局部 MAD 的阈值


def build(rate: float, amp_min: float, amp_max: float, seed: int = 5) -> dict:
    clean = corpus.harmonic_tone(T, FS, amp=0.5, seed=3)
    return corpus.crackle_train(clean, rate, FS, amp_min=amp_min, amp_max=amp_max,
                                seed=seed)


# ---------------------------------------------------------------- C2 局部 MAD
def local_scale(r: np.ndarray, half: int = LOCAL_WIN) -> np.ndarray:
    """滑动局部 MAD（逐点）：`s[n] = 1.4826·median(|r[n±half]|)`（端点用截断窗）。"""
    n = r.shape[0]
    ar = np.abs(r)
    s = np.empty(n)
    for i in range(n):
        lo, hi = max(0, i - half), min(n, i + half + 1)
        s[i] = np.median(ar[lo:hi]) / 0.6745
    return s


def detect_local(y: np.ndarray, alpha: float, half: int = LOCAL_WIN) -> np.ndarray:
    """C2：两点残差 > α·局部 MAD。返回检出掩码（单声道）。"""
    r = residual(y)
    s = local_scale(r, half)
    if s.max() <= 0:
        return np.zeros_like(r, dtype=bool)
    return np.abs(r) > alpha * s


# ---------------------------------------------------------------- C3 时频两级
def detect_tf(y: np.ndarray, mono: np.ndarray) -> np.ndarray:
    """C3：STFT 竖条帧粗定位 + 帧邻域局部 MAD 两点残差精检。返回检出掩码（单声道）。"""
    n = mono.shape[0]
    S, _meta = stft.analyze(mono[:, None], n_fft=N_FFT, hop=HOP, pad=0)
    mag = np.abs(S[:, 0, :])                       # (bins, frames)
    k = MED_FRAMES // 2
    med = np.empty_like(mag)
    for t in range(mag.shape[1]):
        lo, hi = max(0, t - k), min(mag.shape[1], t + k + 1)
        med[:, t] = np.median(mag[:, lo:hi], axis=1)
    d = mag - med
    scale = 1.4826 * np.median(np.abs(d), axis=1, keepdims=True) / 0.6745
    outlier = d > BIN_OUTLIER_K * np.maximum(scale, 1e-12)
    frame_hit = outlier.sum(axis=0) >= FRAME_MIN_BINS
    # 粗定位 → 采样域邻域（标记帧 ± PAD 覆盖的样本段）内做局部 MAD 精检
    region = np.zeros(n, dtype=bool)
    for _t in np.nonzero(frame_hit)[0]:
        lo = max(0, t * HOP - FRAME_PAD * HOP)
        hi = min(n, t * HOP + MED_FRAMES * HOP + FRAME_PAD * HOP)
        region[lo:hi] = True
    if not region.any():
        return np.zeros(n, dtype=bool)
    mask = detect_local(mono, ALPHA_LOCAL)
    return mask & region


# ---------------------------------------------------------------- 修复与指标
def repair(y: np.ndarray, mask: np.ndarray):
    """检出样本按两侧非检出样本线性插值（与 `DeclickOp` 同一修复）。"""
    out = y.copy()
    out[:, 0] = _repair_runs(y[:, 0], mask)
    return out, mask


def measure(pack, out, mask) -> dict:
    clean, y = pack["clean"], pack["crackled"]
    pos = np.array(pack["positions_samples"], dtype=int)
    changed = out[:, 0] != y[:, 0]
    tpr = float(changed[pos].mean()) if pos.size else 0.0
    precision = float(changed[pos].sum() / max(1, changed.sum()))
    return {"dsdr": sdr_db(out, clean) - sdr_db(y, clean),
            "er": energy_ratio_db(out, clean), "tpr": tpr, "precision": precision,
            "changed": int(changed.sum())}


def main() -> int:
    print("破音新量具对照（2 s 谐波音，位置/幅度已知；修复=同款线性插值）\n")
    hdr = "%-8s %-7s %-22s %8s %8s %7s %9s %8s" % (
        "幅度", "密度/s", "量具", "ΔSDR", "能量比", "TPR", "precision", "改动面")
    print(hdr)
    print("-" * len(hdr.expandtabs()))
    for amp_min, amp_max in AMP_RANGES:
        for rate in RATES:
            pack = build(rate, amp_min, amp_max)
            cands = [
                ("C1 现行(α5,全段MAD)", declick.declick(pack["crackled"], 5.0, 4)[0], None),
                ("C1L 更松(α3,无守卫)", declick.declick(pack["crackled"], 3.0, None)[0], None),
            ]
            mono = pack["crackled"][:, 0]
            m2 = detect_local(mono, 5.0)
            cands.append(("C2 局部MAD(α5)", *repair(pack["crackled"], m2)))
            m3 = detect_tf(pack["crackled"], mono)
            cands.append(("C3 时频两级", *repair(pack["crackled"], m3)))
            for name, out, _m in cands:
                r = measure(pack, out, None)
                print("%-8s %-7g %-22s %+8.2f %+8.2f %7.3f %9.3f %8d"
                      % ("%.2f–%.2f" % (amp_min, amp_max), rate, name,
                         r["dsdr"], r["er"], r["tpr"], r["precision"], r["changed"]))
            print("-" * 78)
    # 误报账（代价不对称的教训）：干净素材上不许改
    print("\n误报账（干净素材被改样本数；越大越糟）")
    clean = corpus.harmonic_tone(T, FS, amp=0.5, seed=3)
    noise = corpus.band_limited_noise(T, fs=FS, bandwidth=18000.0, seed=1)
    x = noise[:, 0].copy()
    for i in np.arange(int(0.15 * FS), x.size - int(0.05 * FS), int(0.25 * FS)):
        L = int(0.005 * FS)
        env = np.exp(-np.arange(L) / (0.1 * L))
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
    xt = (x / np.abs(x).max() * 0.95)[:, None]
    mats = [("谐波音", clean), ("带限白噪", noise), ("合法瞬态", xt)]
    for label, mat in mats:
        mono = mat[:, 0]
        row = []
        row.append("C1=%d" % declick.declick(mat, 5.0, 4)[1]["detected_samples"])
        row.append("C1L=%d" % declick.declick(mat, 3.0, None)[1]["detected_samples"])
        row.append("C2=%d" % int(repair(mat, detect_local(mono, 5.0))[1].sum()))
        row.append("C3=%d" % int(repair(mat, detect_tf(mat, mono))[1].sum()))
        print("  %-10s %s" % (label, "  ".join(row)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
