#!/usr/bin/env python3
"""declick_step_probe：**长窗中值残差**能不能补上"台阶/平台型咔哒"的检测缺口？（先量，再定）

## 问题（`docs/editor_research.md §17` / declick 模块 docstring 的挂账）

两点预测残差在**平台内部 ≈ 0** ⇒ 宽度 3/5/8 采样的矩形咔哒实测只覆盖 133%/80%/50%
（覆盖 >100% = 检出了端点、修复把它们插没了）。"按幅度扩展"候选被实测否决。
本轮量调研（Agent 结论，Vaseghi & Rayner 1990 标准法）推荐的候选：

* **S1 长窗中值残差**：`res = x − median_filter(x, L)`，L ≥ 2·W_max+1——平台在窗内
  占比 < 半窗 ⇒ 被中值整体剔除 ⇒ 平台内部残差 ≈ 平台幅度（与单点咔哒同构）。
  一个 L 覆盖全部宽度。**与现行两点残差做"或"融合**（两点专管 1–2 采样尖刺）。
* 对照：C1 = 现行（双尺度并集），S1 单独，S1∪C1 融合。

量什么：宽度 3/5/8 矩形咔哒（真值位置已知）的 ΔSDR/TPR/precision + **误报账**
（干净谐波/带限白噪/合法瞬态——"代价不对称"教训）。

    python packaging/declick_step_probe.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy.ndimage import median_filter

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.core.edit.declick import _detect_raw, _repair_runs, apply_max_run  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SEC = 2.0
L_WINDOW = 21          # 长窗（≥ 2×8+1 = 17，取 21 留余量；0.44 ms @48k）
K_MED = 5.0            # 中值残差的 MAD 阈值（与去咔哒 α 同一"代价不对称"口径）
MAX_RUN = 8            # 平台咔哒宽度到 8 ⇒ 守卫放宽到 8（宽度表：8 仍在"可修"档）


def wide_clicks(clean: np.ndarray, positions_s, width: int, amp: float = 0.9):
    """宽度 `width` 采样的矩形咔哒（与 declick_verify_probe 同构造）。"""
    s = np.asarray(clean, dtype=np.float64).copy()
    if s.ndim == 1:
        s = s[:, None]
    idx = []
    for pos in positions_s:
        i = int(round(pos * FS))
        s[i:i + width, 0] += amp
        idx.append(i)
    return s, idx


def detect_median(y: np.ndarray, L: int = L_WINDOW, k: float = K_MED,
                  max_run: int = MAX_RUN) -> np.ndarray:
    """长窗中值残差检测：`|x − median_filter(x, L)| > k·MAD(res)`（过宽度守卫）。"""
    res = y - median_filter(y, size=L, mode="mirror")
    s = float(np.median(np.abs(res)) / 0.6745)
    if s <= 0.0:
        return np.zeros_like(res, dtype=bool)
    return apply_max_run(np.abs(res) > k * s, max_run)


def detect_median_min3(y: np.ndarray, L: int = L_WINDOW, k: float = K_MED,
                       max_run: int = MAX_RUN, min_run: int = 3) -> np.ndarray:
    """S1 + **最小宽度守卫**：中值检测器的误报实测全在宽度 1–2（噪声台阶/瞬态边）、
    平台目标在 4–5 ⇒ min_run=3 只留"真平台宽"的 run。1–2 采样尖刺归两点检测器管。"""
    res = y - median_filter(y, size=L, mode="mirror")
    sc = float(np.median(np.abs(res)) / 0.6745)
    if sc <= 0.0:
        return np.zeros_like(res, dtype=bool)
    raw = apply_max_run(np.abs(res) > k * sc, max_run)
    out = np.zeros_like(raw)
    i = 0
    n = len(raw)
    while i < n:
        if not raw[i]:
            i += 1
            continue
        j = i
        while j < n and raw[j]:
            j += 1
        if j - i >= min_run:
            out[i:j] = True
        i = j
    return out


def detect_median_paired(y: np.ndarray, L: int = L_WINDOW, k: float = K_MED,
                         w_max: int = 8) -> np.ndarray:
    """S1 + **边缘配对**：中值残差只标记平台的**两端边缘**（每端 1–3 采样）——

    上升沿（residual 为正的 run）与下降沿（为负的 run）在 `w_max` 内配对 ⇒ 掩膜
    覆盖**整个平台跨度**（两端之间）。幅度对称要求 |down| ≥ 0.5·|up|（平台两端的
    阶跃幅度同量级；孤立尖刺/未配对边缘不落）。误报控制靠 5σ 阈值 + 配对条件。
    """
    res = y - median_filter(y, size=L, mode="mirror")
    sc = float(np.median(np.abs(res)) / 0.6745)
    n = len(y)
    if sc <= 0.0:
        return np.zeros(n, dtype=bool)
    raw = np.abs(res) > k * sc
    # 边沿 run 分类（符号 = 平台方向）
    runs = []
    i = 0
    while i < n:
        if not raw[i]:
            i += 1
            continue
        j = i
        while j < n and raw[j]:
            j += 1
        sign = 1.0 if res[i:j].mean() > 0 else -1.0
        runs.append((i, j, sign, float(np.abs(res[i:j]).max())))
        i = j
    out = np.zeros(n, dtype=bool)
    used = [False] * len(runs)
    for a, (i1, j1, s1, m1) in enumerate(runs):
        if s1 < 0 or used[a]:
            continue
        for b in range(a + 1, len(runs)):
            i2, j2, s2, m2 = runs[b]
            if s2 > 0 or used[b]:
                continue
            span = i2 - j1
            if 1 <= span <= w_max and m2 >= 0.5 * m1:
                out[j1:i2] = True          # 平台内部（两端已各被覆盖一段）
                out[i1:j1] = True
                out[i2:j2] = True
                used[a] = used[b] = True
                break
    return out


def detect_median_flat(y: np.ndarray, L: int = L_WINDOW, k: float = K_MED,
                       max_run: int = MAX_RUN, flat_k: float = 0.35) -> np.ndarray:
    """S1 + **平坦性签名**：平台咔哒的 run 内部近似常数（阶跃平台），瞬态边缘陡变。

    对中值残差检出的每个 run：step = median(y[run]) − median(y[run 前 L 个])；
    平坦度 = std(diff(y[run]))；接受条件 flatness ≤ flat_k·|step|。
    （矩形咔哒：run 内 y ≈ 内容 + 常量 amp ⇒ diff ≈ 内容自己的差分，远小于 amp；
    瞬态上升沿：diff 里含陡坡 ⇒ 远超 0.35·step。标定见输出表。）
    """
    res = y - median_filter(y, size=L, mode="mirror")
    sc = float(np.median(np.abs(res)) / 0.6745)
    if sc <= 0.0:
        return np.zeros_like(res, dtype=bool)
    mask = apply_max_run(np.abs(res) > k * sc, max_run)
    out = np.zeros_like(mask)
    i = 0
    n = len(y)
    while i < n:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < n and mask[j]:
            j += 1
        run = y[i:j]
        pre = y[max(0, i - L):i]
        step = float(np.median(run) - (np.median(pre) if pre.size else 0.0))
        flat = float(np.std(np.diff(run))) if j - i >= 2 else 0.0
        if abs(step) > 0 and flat <= flat_k * abs(step):
            out[i:j] = True
        i = j
    return out


def detect_current_union(y: np.ndarray) -> np.ndarray:
    """现行检测（两点残差 + 双尺度 MAD 并集，α=5/守卫 4）。"""
    raw, _r, _s = _detect_raw(y, 5.0)
    return apply_max_run(raw, 4)


def measure(clean, y, out, positions) -> dict:
    changed = out[:, 0] != y[:, 0]
    pos = np.array(positions, dtype=int)
    covered = sum(1 for p in pos if changed[max(0, p - 1):p + 10].any())
    return {"dsdr": sdr_db(out, clean) - sdr_db(y, clean),
            "tpr": covered / max(1, len(pos)),
            "changed": int(changed.sum())}


def main() -> int:
    clean = corpus.harmonic_tone(SEC, FS, amp=0.5, seed=3)
    positions = [0.4 + 0.4 * i for i in range(4)]        # 4 个咔哒，间距 0.4 s
    print("台阶咔哒检测对照（谐波音 %g s，4 个已知位置矩形咔哒，amp=0.9）\n" % SEC)
    print("%-6s %-22s %8s %7s %8s" % ("宽度", "量具", "ΔSDR", "TPR", "改动面"))
    print("-" * 58)
    for width in (3, 5, 8):
        y, pos_samples = wide_clicks(clean, positions, width)
        cands = [
            ("C1 现行(α5/守卫4)", detect_current_union(y[:, 0])),
            ("S1 长窗中值(L=%d)" % L_WINDOW, detect_median(y[:, 0])),
        ]
        u = detect_current_union(y[:, 0]) | detect_median(y[:, 0])
        cands.append(("S1∪C1 融合", u))
        cands.append(("S1f 平坦签名", detect_median_flat(y[:, 0])))
        cands.append(("S1m3 最小宽3", detect_median_min3(y[:, 0])))
        cands.append(("C1∪S1m3", detect_current_union(y[:, 0]) | detect_median_min3(y[:, 0])))
        cands.append(("S1p 边缘配对", detect_median_paired(y[:, 0])))
        for name, mask in cands:
            out = y.copy()
            out[:, 0] = _repair_runs(y[:, 0], mask)
            r = measure(clean, y, out, positions)      # positions 仍是秒（真值）
            print("%-6d %-22s %+8.2f %7.2f %8d"
                  % (width, name, r["dsdr"], r["tpr"], r["changed"]))
        print("-" * 58)
    # 误报账
    print("\n误报账（干净素材被改样本数）")
    noise = corpus.band_limited_noise(SEC, fs=FS, bandwidth=18000.0, seed=1)
    xn = noise[:, 0].copy()
    for i in np.arange(int(0.15 * FS), xn.size - int(0.05 * FS), int(0.25 * FS)):
        L = int(0.005 * FS)
        env = np.exp(-np.arange(L) / (0.1 * L))
        xn[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
    xt = (xn / np.abs(xn).max() * 0.95)[:, None]
    for label, mat in (("谐波音", clean), ("带限白噪", noise), ("合法瞬态", xt)):
        mono = mat[:, 0]
        cur = detect_current_union(mono)
        med = detect_median(mono)
        pr = detect_median_paired(mono)
        print("  %-10s C1=%d  S1=%d  S1p配对=%d"
              % (label, int(cur.sum()), int(med.sum()), int(pr.sum())))
    print("""
读法：
* C1 对宽度 3/5/8 的 TPR 应复现挂账（~50–100% 但修复后 ΔSDR 小）；
* S1 的 TPR 应显著更高（平台整体检出）；
* S1∪C1 若在全部宽度上 ΔSDR ≥ max(C1, S1) 且误报保持 0 ⇒ 才有融合落地的话题。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
