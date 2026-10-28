#!/usr/bin/env python3
"""step_L_sweep：**台阶臂的窗长 L 到底取多少？** （21 / 41 / 81 直接对拍）

## 为什么要单独量

调研 Agent 的结论说 "L=41 是甜点，L=21 时宽度 8 全灭、L=81 会开始跟踪平台"，
而本仓 `declick.py` 里 `STEP_L = 21`、docstring 写的是"21 留余量"。
**两方都没有给出同一口径下的 L 扫描**——一个说 21、一个说 41，必须实测裁决。

理论预期：平台宽 W 在窗 L 内占比 = W/L。中值把平台整体剔除的条件是
`W/L < 0.5`（平台占比不过半），即 **L > 2W**。宽度 8 ⇒ L > 16，所以 21
在理论上"够用"；但中值滤波的**过渡带**需要额外余量（窗边缘的样本会把
中值拉回平台），实践上通常要 L ≥ 3W 才稳 ⇒ 8 宽需要 ~25，取 41 更保险。

## 量什么

* 三档宽度（3 / 5 / 8）× 三档 L（21 / 41 / 81）的 **ΔSDR**（主判据）；
* **TPR / precision**（真值位置已知）；
* **误报账**：干净谐波音 / 带限白噪 / 合法瞬态（代价不对称，必须三项全零）；
* 检出 run 的**宽度分布**（解释为什么某档漏检）。

    python packaging/step_L_sweep.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy.ndimage import median_filter

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.core.edit.declick import _runs, apply_max_run  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SEC = 2.0
WIDTHS = (3, 5, 8)
LS = (21, 41, 81)
K_MED = 5.0
MIN_RUN = 3
MAX_RUN = 8
POSITIONS = (0.30, 0.55, 0.80)      # 三个孤立平台（这是"孤立单平台"的最难格）


def make_click(clean, width, amp=0.9, positions=POSITIONS):
    s = np.asarray(clean, dtype=np.float64).copy()
    if s.ndim == 1:
        s = s[:, None]
    idx = []
    for pos in positions:
        i = int(round(pos * FS))
        s[i:i + width, 0] += amp
        idx.append(i)
    return s, idx


def detect(y, L, k=K_MED, min_run=MIN_RUN, max_run=MAX_RUN):
    res = y - median_filter(y, size=L, mode="mirror")
    s = float(np.median(np.abs(res)) / 0.6745)
    if s <= 0.0:
        return np.zeros(res.shape, dtype=bool)
    m = apply_max_run(np.abs(res) > k * s, max_run)
    # min_run：把短于 min_run 的检出段整体清掉
    out = np.zeros_like(m)
    for a, b in _runs(m[:, 0]):
        if b - a >= min_run:
            out[a:b, 0] = True
    return out


def repair(y, mask):
    """两侧最近非检出样本线性插值（与内核 `_repair_runs` 同语义，此处单声道）。"""
    out = y.copy()
    x = out[:, 0]
    m = mask[:, 0]
    i = 0
    n = len(x)
    while i < n:
        if not m[i]:
            i += 1
            continue
        j = i
        while j < n and m[j]:
            j += 1
        a, b = i - 1, j
        if a >= 0 and b < n:
            x[i:j] = np.linspace(x[a], x[b], j - i + 2)[1:-1]
        elif a >= 0:
            x[i:j] = x[a]
        elif b < n:
            x[i:j] = x[b]
        i = j
    return out


def measure_click(clean, width, L):
    y, idx = make_click(clean, width)
    mask = detect(y, L)
    out = repair(y, mask)
    pos = np.array([i for i in idx])
    detected = mask[:, 0]
    tpr = float(detected[pos].mean()) if pos.size else 0.0
    precision = float(detected[pos].sum() / max(1, detected.sum()))
    widths = [b - a for a, b in _runs(mask[:, 0])]
    return {
        "dsdr": sdr_db(out, clean) - sdr_db(y, clean),
        "tpr": tpr, "precision": precision,
        "runs": len(widths), "wmed": int(np.median(widths)) if widths else 0,
    }


def _onset_signal(seconds=SEC, fs=FS, seed=11):
    """合法瞬态：一串短促起音（指数衰减包络的窄脉冲群，宽度 2 ms）。

    这是"**不该被修**"的内容——真实的钢琴/鼓起音就是毫秒级机宽。
    中值臂的 min_run 设计正是为了把它们和平台分开（前者的检出 run 在宽 1–2）。
    """
    rng = np.random.default_rng(seed)
    n = int(round(seconds * fs))
    x = np.zeros(n)
    for _ in range(12):
        i = int(rng.integers(0, n - int(0.05 * fs)))
        w = int(0.002 * fs)                     # 2 ms 起音宽度
        env = np.exp(-np.arange(w) / (0.35 * w))
        x[i:i + w] += 0.9 * env
    return x[:, None]


def false_positives(L):
    """误报账：干净谐波 / 带限白噪 / 合法瞬态。返回三项检出样本数。"""
    out = {}
    harm = corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3)
    noise = corpus.band_limited_noise(SEC, FS, seed=7)
    transient = _onset_signal()
    for name, sig in (("谐波", harm), ("带限白噪", noise), ("瞬态", transient)):
        s = np.asarray(sig, dtype=np.float64)
        if s.ndim == 1:
            s = s[:, None]
        out[name] = int(detect(s, L).sum())
    return out


def main() -> int:
    print("台阶臂窗长扫描：孤立单平台（最难格）在三档宽度 × 三档 L 下的表现\n")
    clean = corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3)
    clean = np.asarray(clean, dtype=np.float64)
    if clean.ndim == 1:
        clean = clean[:, None]

    print("%-6s %-5s %10s %8s %10s %6s %6s" %
          ("宽度", "L", "ΔSDR(dB)", "TPR", "precision", "段数", "中位宽"))
    print("-" * 62)
    best = {}
    for w in WIDTHS:
        for L in LS:
            m = measure_click(clean, w, L)
            print("%-6d %-5d %+10.2f %8.3f %10.3f %6d %6d"
                  % (w, L, m["dsdr"], m["tpr"], m["precision"], m["runs"], m["wmed"]))
            best.setdefault(w, []).append((L, m["dsdr"]))
        print("-" * 62)

    print("\n误报账（干净素材上的检出样本数，必须全 0）：")
    print("%-10s %8s %8s %8s" % ("L", "谐波", "带限白噪", "瞬态"))
    for L in LS:
        fp = false_positives(L)
        print("%-10d %8d %8d %8d" % (L, fp["谐波"], fp["带限白噪"], fp["瞬态"]))

    print("\n各宽度最优 L：")
    for w in WIDTHS:
        rows = sorted(best[w], key=lambda t: -t[1])
        print("  w=%d → L=%d（ΔSDR %+.2f dB）" % (w, rows[0][0], rows[0][1]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
