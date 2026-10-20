#!/usr/bin/env python3
"""declick_probe：**去咔哒**——先量"检测能不能做"再谈"修"。

    python packaging/declick_probe.py

为什么这一步是**检测**而不是滤镜：`corpus.py` 早就把**位置已知**的咔哒备好了
（`click_train`），所以这一轮能像去削波那样用**真值**把两条线量清楚：

1. **可检测性**（这是去咔哒的**真边界**）：用**局部中值残差 + 稳健尺子（MAD）**做检测器，
   扫阈值 `α` ⇒ 报**检出率（TPR）/ 误报率（FPR）**。
   ⚠ 关键对照素材是**合法的瞬态**（"该有的"起音）：如果检测器分不开"咔哒"与"起音"，
   那它不是"不够灵敏"，而是**这类信号上根本没有可用的判据**——必须量出来、不许含糊。
2. **修得回多少**：检出后用两种**只用邻居**的法子修（跑内**线性插值** / 邻域**中值**），
   用同一把尺子（`sdr_db` + `energy_ratio_db`）量 ⇒ 与"不修"比。
3. **效力边界**：咔哒**幅度**扫（相对本底 σ）、**密度**扫（相邻咔哒间距）——
   量出"到哪个幅度/密度就不行了"。

⚠ 检测阈值 `α` **不许凭空定**：本探针把它扫出来，再按**代价**选值——实测发现代价**不对称**
（误报到 ~1e-3 就把信号**修坏** −13 dB；而 TPR 掉一点只让收益从 +14 掉到 +3.6）
⇒ 取值规则是"**先压误报、再看收益**"，落到 **α = 5.0**（见文末的决策表）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from scipy import ndimage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0
K = 8                     # 局部窗半宽（中值预测用 [n−k, n+k] 去掉 n 自己）
ALPHAS = (3.0, 4.0, 5.0, 6.0, 8.0, 12.0)
CLICK_AMP = 0.9


# ------------------------------------------------------------------ 三种素材（含"合法瞬态"）
def _tone() -> np.ndarray:
    return corpus.harmonic_tone(SECONDS, fs=FS, f0=440.0, n_partials=40, seed=2)[:, 0]


def _noise() -> np.ndarray:
    return corpus.band_limited_noise(SECONDS, fs=FS, bandwidth=18000.0, seed=1)[:, 0]


def _transients() -> np.ndarray:
    """**合法瞬态**素材：带限噪声 + 若干**起音很陡**的爆发（"该有的"瞬态，不是咔哒）。

    用途 = 量**误报率**：检测器若把起音也当咔哒，那就是它在这类信号上**判不开**。
    """
    x = _noise().copy()
    n = x.size
    for i in np.arange(int(0.15 * FS), n - int(0.05 * FS), int(0.25 * FS)):
        L = int(0.005 * FS)                      # 5 ms 起音：快，但是**合法内容**
        env = np.exp(-np.arange(L) / (0.1 * L))
        t = np.arange(L) / FS
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * t)
    return x / np.abs(x).max() * 0.95


def _smooth() -> np.ndarray:
    """**平滑低波峰**素材（慢速滑音 + 两个近邻谐波）——去咔哒**设计上**就该管用的那类信号。

    为什么必须有它：上面的谐波音**波峰因数很高**（几十条等幅谐波叠加 ⇒ 波形像脉冲），
    局部中值**预测不了**它 ⇒ 残差被信号本身主导，检测器**先天吃亏**。
    拿"素材本身就不可预测"去否定检测器，等于**把量具的错算成方法的错**（本仓教训）。
    """
    t = np.arange(int(SECONDS * FS)) / FS
    f = 300.0 + 450.0 * t / SECONDS                     # 300 → 750 Hz 慢滑音
    ph = 2 * np.pi * np.cumsum(f) / FS
    return (0.7 * np.sin(ph) + 0.25 * np.sin(ph * 1.01)) / 0.95


MATERIALS = (("平滑低波峰（滑音）", _smooth), ("谐波音（高波峰）", _tone),
             ("带限白噪（粗糙）", _noise), ("合法瞬态（起音）", _transients))


# ------------------------------------------------------------------ 检测 + 修复
def detect_clicks(y: np.ndarray, alpha: float, k: int = K, w: int = 257, local: bool = True):
    """**两点预测残差 + 稳健尺子**：`r[n] = y[n] − (y[n−1] + y[n+1])/2`。

    ⚠ 残差的选法是**量出来的**：先试过"邻域中值残差"，它**在平滑素材上就废了**
    （正弦的局部中值本身偏离样本值 ⇒ 残差被波形主导，α 扫到 12 还有 **15% 假阳性**）。
    两点残差才是经典口径：对 `f=750 Hz` 的滑音，`ω = 2πf/fs ≈ 0.098` ⇒ 残差量级 `≈ ω²·y ≈ 0.007`
    （比 0.9 的咔哒小两个数量级）；而咔哒样本上 `r ≈ A`。

    尺子两种（**用数据比**）：
    * `local=False`：**全局** `s = median(|r|)/0.6745`（MAD ⇒ σ 的稳健估计）；
    * `local=True`：**局部** `s[n] = median_{|i−n|<w/2} |r[i]|`（滑动中值）——
      让阈值跟着**局部**活跃度走。

    判据 `|r[n]| > alpha·s[n]`。返回 `(mask, r, s)`。
    """
    y = np.asarray(y, dtype=np.float64)
    r = np.zeros_like(y)
    r[1:-1] = y[1:-1] - 0.5 * (y[:-2] + y[2:])       # 首尾样本无两点预测 ⇒ 残差 0（不检测）
    if local:
        s = ndimage.median_filter(np.abs(r), size=w, mode="nearest")
    else:
        s = np.full_like(r, float(np.median(np.abs(r)) / 0.6745) or 1e-12)
    return np.abs(r) > alpha * s, r, s


def repair_linear(y: np.ndarray, idx: np.ndarray) -> np.ndarray:
    """检出样本用**两侧最近的非检出样本线性插值**（连续段整体处理）。"""
    out = y.copy()
    if idx.size == 0:
        return out
    mask = np.zeros(y.size, dtype=bool)
    mask[idx] = True
    i = 0
    while i < y.size:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < y.size and mask[j]:
            j += 1
        a, b = i - 1, j
        left = y[a] if a >= 0 else None
        right = y[b] if b < y.size else None
        if left is None and right is None:
            pass
        elif left is None:
            out[i:j] = right
        elif right is None:
            out[i:j] = left
        else:
            out[i:j] = np.linspace(left, right, j - i + 2)[1:-1]
        i = j
    return out


def repair_median(y: np.ndarray, idx: np.ndarray, k: int = K) -> np.ndarray:
    """检出样本用**去掉自己的邻域中值**替换（经典中值去咔哒）。"""
    out = y.copy()
    if idx.size == 0:
        return out
    pad = np.concatenate([np.full(k, y[0]), y, np.full(k, y[-1])])
    win = np.lib.stride_tricks.sliding_window_view(pad, 2 * k + 1)
    side = np.concatenate([win[:, :k], win[:, k + 1:]], axis=1)
    out[idx] = np.median(side, axis=1)[idx]
    return out


def _metrics(est, ref):
    s = sdr_db(est[:, None], ref[:, None])
    e = energy_ratio_db(est[:, None], ref[:, None])
    return (-999.0 if s is None else s), (0.0 if e is None else e)


def rates(det: np.ndarray, truth: np.ndarray, n: int):
    """检出率 / **误报率**（分母 = 非咔哒样本数）。"""
    tpr = float(det[truth].sum()) / max(1, truth.sum())
    fp = int(det.sum()) - int(det[truth].sum())
    fpr = fp / max(1, n - truth.sum())
    return tpr, fpr, fp


def main() -> None:
    n = int(SECONDS * FS)
    pos = list(np.arange(0.15, 1.85, 0.12))
    print("去咔哒探针 ｜ fs=%d ｜ %.0f s ｜ 局部窗 2k+1=%d ｜ 咔哒幅度 %.2f"
          % (FS, SECONDS, 2 * K + 1, CLICK_AMP))
    print("尺子：`metrics.sdr_db` + `metrics.energy_ratio_db`；检测器 = 局部中值残差 + MAD 稳健尺子\n")
    for name, make in MATERIALS:
        clean = make()
        clicked = corpus.click_train(clean[:, None], pos, fs=FS, amp=CLICK_AMP)
        y = clicked["clicked"][:, 0]
        truth = np.zeros(n, dtype=bool)
        truth[clicked["positions_samples"]] = True
        base_s, _ = _metrics(y, clean)
        print("%s（无咔哒信号 SDR %.1f dB）" % (name, base_s))
        print("  %-6s ｜ %-30s ｜ %-30s" % ("α", "**局部**尺子（TPR/FPR ｜ ΔSDR 线性/中值）",
                                            "**全局**尺子（TPR/FPR ｜ ΔSDR 线性/中值）"))
        for al in ALPHAS:
            cells = []
            for local in (True, False):
                det, _, _ = detect_clicks(y, al, local=local)
                tpr, fpr, _ = rates(det, truth, n)
                idx = np.nonzero(det)[0]
                s_lin, _ = _metrics(repair_linear(y, idx), clean)
                s_med, _ = _metrics(repair_median(y, idx), clean)
                cells.append("%6.2f /%9.1e ｜ %+5.2f /%+5.2f"
                             % (tpr, fpr, s_lin - base_s, s_med - base_s))
            print("  %-6.1f ｜ %-30s ｜ %-30s" % (al, cells[0], cells[1]))
        print()
    print("决策（据此定 `declick.DEFAULT_THRESHOLD`）：")
    print("· **残差口径**：邻域中值残差**被否决**（平滑素材上 α 扫到 12 仍有 15% 假阳性）；")
    print("  两点预测残差才是经典口径（平滑素材残差 ≈ (2πf/fs)²·|y|，比咔哒小两个数量级）。")
    print("· **尺子**：同一误报率下**全局 MAD** 优于局部滑动尺子（合法瞬态 α=5：+6.53 vs −3.71）。")
    print("· **阈值 = 5.0**：α=5 时谐波音/白噪/合法瞬态的误报均为 **0**，四种素材 ΔSDR 全为正")
    print("  （+40.6 / +3.6 / +2.0 / +6.5 dB）；α=4 会在合法瞬态上留 ~12 个误报、α=3 直接**修坏**。")
    print("· **效力边界**：收益由**素材的可预测性**决定——平滑 +40 dB，**带限白噪只 +2 dB**")
    print("  （噪声本身不可预测 ⇒ 预测残差里没有可分离的「咔哒」成分）。")


if __name__ == "__main__":
    main()
