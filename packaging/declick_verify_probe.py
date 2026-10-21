#!/usr/bin/env python3
"""declick_verify_probe：**"第二级复核"到底能不能把合法瞬态与咔哒分开**——先量再改。

    python packaging/declick_verify_probe.py

背景：`docs/editor_research.md §11` 量出**误报的代价不对称**（FPR 到 ~1e-3 就把信号修坏），
所以默认阈值取在保守侧（α=5）。闭源标杆（Adobe Click/Pop Eliminator）为此专门有一级
**"Second Level Verification (Reject Clicks)"**：把"小号/萨克斯/女声/军鼓这类**正常高峰**
被误判成咔哒"的候选**再筛一遍**。

本探针就是量"再筛一遍"该用**哪个特征**、以及**在哪儿失效**。候选特征（都可复算）：

1. **脉冲宽度** `run`：检出的连续样本数。**真正的咔哒是 1–几个采样**；
   "合法起音"是**毫秒级**的爆发（本探针用 5 ms 起音 = 240 采样）⇒ 宽度差两个数量级。
2. **修复代价** `ΔSDR`：把该段按线性插值换掉，SDR 是**变好**还是**变坏**
   （合法瞬态被插值 = 把真实能量抹掉 ⇒ 变坏）。

⚠ 本探针**同时量"多宽的咔哒还算咔哒"**：`corpus.click_train` 只造**单采样**咔哒，
只测单采样等于**把量具的口径当成方法的边界**（本仓踩过这个坑）⇒ 这里额外造
**2/3/5/8 采样宽**的咔哒，看宽度判据在哪一档开始失效。
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import declick  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0
WIDTHS = (1, 2, 3, 5, 8)
ALPHA = 4.0            # **故意取 4**：它在合法瞬态上还有误报，正好用来考察"第二级复核"


def smooth_tone(seconds: float = SECONDS) -> np.ndarray:
    """平滑低波峰素材（慢滑音）——去咔哒设计上该管用的那类。"""
    t = np.arange(int(seconds * FS)) / FS
    f = 300.0 + 450.0 * t / seconds
    ph = 2 * np.pi * np.cumsum(f) / FS
    return ((0.7 * np.sin(ph) + 0.25 * np.sin(ph * 1.01)) / 0.95)[:, None]


def onsets(seconds: float = SECONDS, length_ms: float = 5.0,
           spacing_s: float = 0.25) -> tuple[np.ndarray, list[int]]:
    """**合法瞬态**素材：平滑音 + 陡起音（真实的"该有的"瞬态），返回 `(y, 起音位置)`。"""
    x = smooth_tone(seconds)[:, 0].copy()
    L = int(length_ms * FS / 1000.0)
    pos = []
    for i in np.arange(int(0.15 * FS), x.size - L - 10, int(spacing_s * FS)):
        env = np.exp(-np.arange(L) / (0.1 * L))
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
        pos.append(int(i))
    return (x / np.abs(x).max() * 0.95)[:, None], pos


def wide_clicks(clean: np.ndarray, positions_s, width: int, amp: float = 0.9) -> tuple[np.ndarray, list[int]]:
    """造**宽度 `width` 采样**的咔哒（矩形脉冲）——`corpus.click_train` 只有单采样。"""
    s = clean.copy()
    idx = []
    for t in positions_s:
        i = int(round(t * FS))
        s[i:i + width, 0] += amp
        idx.append(i)
    return s, idx


def runs_of(mask: np.ndarray):
    """连续检出段 `(start, end)` 列表（end 开区间）。"""
    out, i = [], 0
    while i < mask.size:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.size and mask[j]:
            j += 1
        out.append((i, j))
        i = j
    return out


def main() -> None:
    print("第二级复核探针 ｜ fs=%d ｜ %.0f s ｜ 检测 α=%.1f（**故意取 4**：让它还有误报）"
          % (FS, SECONDS, ALPHA))
    print("特征：① 检出段**宽度**（采样数）② 修掉该段的**ΔSDR**（变好=真咔哒，变坏=真内容）\n")

    # ---- A. 真咔哒：宽度扫（1/2/3/5/8 采样）
    print("A. 真咔哒（平滑音 + 已知位置的矩形脉冲）")
    print("   %-6s %8s %10s %10s %10s" % ("宽度", "检出数", "检出段宽度中位", "最大", "ΔSDR(修前→后)"))
    widths_seen = {}
    for w in WIDTHS:
        clean = smooth_tone()
        pos = list(np.arange(0.15, SECONDS - 0.05, 0.12))
        y, idx = wide_clicks(clean, pos, w)
        mask, _, _ = declick.detect(y[:, 0], ALPHA)
        rr = runs_of(mask)
        s0 = sdr_db(y, clean)
        rep = declick._repair_runs(y[:, 0], mask)[:, None]
        s1 = sdr_db(rep, clean)
        lens = [b - a for a, b in rr]
        widths_seen[w] = (min(lens) if lens else 0, max(lens) if lens else 0)
        print("   %-6d %8d %10s %10d   %+8.2f → %+8.2f"
              % (w, len(rr), ("%.0f" % np.median(lens)) if lens else "—",
                 max(lens) if lens else 0, s0, s1))

    # ---- B. 合法瞬态：宽度分布 + 修复代价
    print("\nB. 合法瞬态（平滑音 + 5 ms 陡起音，**不该被修**）")
    y_t, pos_t = onsets()
    mask_t, _, _ = declick.detect(y_t[:, 0], ALPHA)
    rr_t = runs_of(mask_t)
    lens_t = [b - a for a, b in rr_t]
    # "修复代价" = 修完的结果**离原始素材多远**（原始素材就是真值：起音是合法内容）
    rep_t = declick._repair_runs(y_t[:, 0], mask_t)[:, None]
    loss = sdr_db(rep_t, y_t)
    print("   检出段 %d 个 ｜ 宽度 中位 %.0f，范围 %d–%d ｜ 修复后「离原素材」%s dB"
          % (len(rr_t), np.median(lens_t) if lens_t else 0,
             min(lens_t) if lens_t else 0, max(lens_t) if lens_t else 0,
             "—" if loss is None else "%.2f" % loss))
    # 起音长度扫：起音越短，越像咔哒（这才是**真正的边界**）
    print("   起音长度扫（宽度判据的失效点）：")
    print("   %-10s %8s %10s %10s" % ("起音长度", "检出段", "宽度中位", "ΔSDR"))
    for ms in (5.0, 1.0, 0.5, 0.2):
        base = smooth_tone()
        L = max(1, int(ms * FS / 1000.0))
        x = base[:, 0].copy()
        for i in np.arange(int(0.15 * FS), x.size - L - 10, int(0.25 * FS)):
            env = np.exp(-np.arange(L) / (0.1 * L))
            x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
        x = x / np.abs(x).max() * 0.95
        m, _, _ = declick.detect(x, ALPHA)
        rr = runs_of(m)
        ln = [b - a for a, b in rr]
        # 修完离原素材多远（原素材 = 真值；起音越短，被抹掉的能量越少、但相对的破坏越集中）
        s1 = sdr_db(declick._repair_runs(x, m)[:, None], base)
        print("   %-10.1f %8d %10s %s"
              % (ms, len(rr), ("%.0f" % np.median(ln)) if ln else "—",
                 "—" if s1 is None else "%+.2f dB" % (s1 - sdr_db(x[:, None], base))))

    # ---- D. 缺口实验：多采样咔哒**只被检出两个边** ⇒ 平台段没修
    print("\nD. 多采样咔哒的**修复缺口**（为什么宽度 3+ 的 ΔSDR 只有 +0.5 dB）")
    print("   机制：`r[n] = y[n] − (y[n−1]+y[n+1])/2` 在**平台内部** ≈ 0，只在两个边上跳 ⇒")
    print("   线性插值只补两个边，平台原样留着。所以要看**扩展规则**能不能把整段圈出来：")
    print("   %-6s %10s %12s %12s" % ("宽度", "检出覆盖", "扩展后覆盖", "扩展后 ΔSDR"))

    def extend(y: np.ndarray, mask: np.ndarray, scale: float, alpha: float,
               max_gap: int = 0) -> np.ndarray:
        """把检出段**沿两端扩展**，直到样本与「段外基线」的差 <= alpha·scale。

        基线取段外**窗口内**的中位数（窗口 = 段长 + 32）；`max_gap` 为 0 表示不跨空洞。
        """
        out = mask.copy()
        runs = runs_of(mask)
        for a, b in runs:
            w = max(32, min(512, (b - a) * 4))
            lo, hi = max(0, a - w), min(y.size, b + w)
            outside = np.concatenate([y[lo:a], y[b:hi]])
            base = float(np.median(outside)) if outside.size else 0.0
            i = a - 1
            while i >= 0 and abs(y[i] - base) > alpha * scale:
                out[i] = True
                i -= 1
            j = b
            while j < y.size and abs(y[j] - base) > alpha * scale:
                out[j] = True
                j += 1
        return out

    for w in WIDTHS:
        clean = smooth_tone()
        pos = list(np.arange(0.15, SECONDS - 0.05, 0.12))
        y, idx = wide_clicks(clean, pos, w)
        mask, _, s = declick.detect(y[:, 0], 5.0)
        n_click = w * len(idx)
        cov = int(mask.sum()) / max(1, n_click)
        ext = extend(y[:, 0], mask, s, 5.0)
        # 扩展后再按宽度上限 2·w+8 截断（防它一路吃到内容里）
        keep = np.zeros_like(ext)
        for a, b in runs_of(ext):
            if b - a <= 2 * w + 8:
                keep[a:b] = True
        rep = declick._repair_runs(y[:, 0], keep)[:, None]
        print("   %-6d %10.2f %12.2f %+12.2f"
              % (w, cov, int(keep.sum()) / max(1, n_click), sdr_db(rep, clean) - sdr_db(y, clean)))

    print("\n读表要点（D）：")
    print("· 检出覆盖随宽度**下降**（3.00 → 0.50）：宽度 1 的咔哒被 3 个样本覆盖（边+邻），")
    print("  而宽度 8 的咔哒**只覆盖一半**（只有两个边），平台原样留着——这就是 ΔSDR 只有 +0.5 的原因。")
    print("· **扩展规则被实测否决**：本探针试过「沿两端扩展到与段外中位数之差 ≤ α·s」，")
    print("  结果**全部被宽度上限截掉**（覆盖归 0）——因为这里的 `s` 是**残差**尺度（很小），")
    print("  平滑波形本身几乎处处超过 `α·s` ⇒ 扩展会一路吃穿整条信号。")
    print("  ⇒ 要修多采样平台，需要**台阶/阶跃检测器**（另一个量具），不是把残差阈值放大就行。")
    print("· ⇒ 本轮的结论：**平台缺口如实记账、不硬修**；能落地的只有「宽度守卫」（见 E）。")

    # ---- E. 第二级复核规则：**只接受宽度 ≤ L_max 的检出段**（其余放行不修）
    print("\nE. 第二级复核规则：**只接受宽度 ≤ L_max 的检出段**（其余放行不修）")
    print("   %-8s %12s %12s %12s" % ("L_max", "咔哒留下(8宽)", "起音被挡", "起音误修数"))

    def apply_rule(y, mask, lmax):
        keep = np.zeros_like(mask)
        for a, b in runs_of(mask):
            if b - a <= lmax:
                keep[a:b] = True
        return keep

    y8, _ = wide_clicks(smooth_tone(), list(np.arange(0.15, SECONDS - 0.05, 0.12)), 8)
    m8, _, _ = declick.detect(y8[:, 0], ALPHA)
    for lmax in (1, 3, 8, 16, 64, 256):
        k8 = apply_rule(y8[:, 0], m8, lmax)
        kt = apply_rule(y_t[:, 0], mask_t, lmax)
        print("   %-8d %12.0f %12.0f %12d"
              % (lmax, 100.0 * k8.sum() / max(1, m8.sum()),
                 100.0 * (1 - kt.sum() / max(1, mask_t.sum())), int(kt.sum())))

    print("\n读表要点：")
    print("· **宽度**能把单采样/窄咔哒与 5 ms 起音分开（差两个数量级）；")
    print("· 但它**挡不住短起音**（0.2–0.5 ms 的起音宽度与宽咔哒重叠）⇒ 边界在起音长度那一列；")
    print("· **ΔSDR** 是另一把刀：真咔哒修完变好、真内容修完变坏——但它需要**音频**，")
    print("  在“不知道真值”的真实素材上不可得 ⇒ 只能作为**离线判据**，不能作为运行时复核。")


if __name__ == "__main__":
    main()
