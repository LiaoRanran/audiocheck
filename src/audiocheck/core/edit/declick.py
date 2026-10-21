"""去咔哒算子（P3 第二个修复算子）：**两点预测残差 + 稳健尺子**检测，只改**检出样本**。

为什么是这两步（**本轮实测选的**，`packaging/declick_probe.py`）
--------------------------------------------------------------
素材 = `corpus.click_train`（**位置已知**的咔哒）+ 四种"背景"素材。先量后改：

| 残差口径 | 平滑滑音 | 谐波音 | 带限白噪 | 合法瞬态 |
|---|---|---|---|---|
| **邻域中值残差**（先试的） | α 扫到 12 仍 **15% 假阳性** | — | — | — |
| **两点预测残差**（落地） | ✅ | ✅ | ✅ | ✅ |

* **残差**：`r[n] = y[n] − (y[n−1] + y[n+1])/2`。对 `f` 远低于奈奎斯特的**平滑**内容，
  `r ≈ (2πf/fs)²·y`（极小）；咔哒样本上 `r ≈ 幅度` ⇒ **信噪比天生的**。
  ⚠ "邻域中值残差"被**实测否决**：正弦的局部中值本身就偏离样本值 ⇒ 残差被**波形**主导。
* **尺子**：`s = median(|r|)/0.6745`（全局 MAD ⇒ σ 的稳健估计）。**实测比过局部滑动尺子**：
  同一误报率下全局尺子的 SDR 更好（合法瞬态 α=5：全局 **+6.53** vs 局部 **−3.71**）⇒ 取全局。
* **阈值 `α`**：默认 **5.0**。**这个默认值是"代价不对称"量出来的**：
  * α=4：TPR 更高（谐波音 1.00、瞬态 0.80），但**合法瞬态素材上有 ~12 个误报**（FPR 1.3e-4）；
  * α=5：**谐波音 / 白噪 / 合法瞬态的误报都是 0**，ΔSDR 仍**全为正**（+40.6 / +3.6 / +2.0 / +6.5）；
  * **误报的代价不对称**：FPR 到 ~1e-3 时修复会把白噪**修坏 −13 dB**（α=3 实测）——
    而 TPR 掉一点只让收益从 +14.2 变 +3.6 ⇒ **宁可漏、不可错** ⇒ 取 5.0。
* **修复**：检出样本按**两侧最近的非检出样本线性插值**（连续段整体处理）。
  实测比"邻域中值替换"更好或持平（平滑素材 +40.60 vs +23.85）。

⚠ 效力边界（**全部实测**，写进审计）：
* **素材决定收益**：平滑 +40 dB、谐波音 +3.6、合法瞬态 +6.5、**带限白噪只 +2.0**（噪声本身不可预测
  ⇒ 预测残差里没有"咔哒"可分离的成分）；
* **误报会"修坏"**：见上面的不对称；**合法瞬态**素材上默认阈值的检出数是 **0**（判据钉住）。
* **多采样咔哒只修两个边**（`packaging/declick_verify_probe.py` 量出来的**真实缺口**）：
  `r[n] = y[n] − (y[n−1]+y[n+1])/2` 在**平台内部** ≈ 0 ⇒ 宽度 3/5/8 的矩形咔哒实测只被覆盖
  133%/80%/50%，ΔSDR 只有 **+1.5 / +0.7 / +0.5 dB**（宽度 1–2 是 **+39 / +37 dB**）。
  ⇒ **本算子修的是"脉冲型"咔哒，不是"台阶/平台型"**；要修后者需要**阶跃检测器**（另一个量具），
  本轮试过"按幅度沿两端扩展"的候选，**实测否决**（残差尺度不是幅度尺度，扩展会一路吃穿信号）。

### 宽度守卫（第二级复核，`max_run`）—— 用"代价表"定的 4
文献侧的动机见 `docs/editor_research.md §11.3`（Adobe 的 "Second Level Verification"：
小号/萨克斯/女声/军鼓的**正常高峰**会被误判）。实测（平滑音 + 5 ms 起音，α=5）：

| 宽度上限 `L` | 1 采样咔哒 | 2 采样咔哒 | 合法起音的破坏（离原素材 SDR） | 被改采样 |
|---|---|---|---|---|
| 无守卫 | +39.14 | +37.37 | **30.55 dB** | 300 |
| 3 | +39.14 | **0.00（丢了）** | 65.29 dB | 16 |
| **4（默认）** | **+39.14** | **+37.37** | **56.51 dB** | 40 |
| 5 | +39.14 | +37.37 | 51.37 dB | 60 |
| 8 | +39.14 | +37.37 | 32.89 dB | 240 |

⇒ **4 是"不丢掉任何我们真能修的咔哒（1–2 采样）"的最小值**，且仍把起音破坏降了 **26 dB**。
⚠ 这条守卫**挡不住亚毫秒起音**（0.2 ms 起音的检出段宽度与咔哒重叠，实测宽度都是 2）
——如实记账，别指望它万能。
"""
from __future__ import annotations

import numpy as np

#: 默认检测阈值（`|r| > α·s`）。**实测标定的"代价不对称"取值**（`packaging/declick_probe.py`）：
#: α=5 时**谐波音 / 带限白噪 / 合法瞬态三种素材的误报均为 0**，且四种素材 ΔSDR 全为正
#: （+40.6 / +3.6 / +2.0 / +6.5 dB）；而 α=4 会在合法瞬态上留下 ~12 个误报、α=3 直接**修坏**
#: （白噪 −13 dB）。⇒ **宁可漏、不可错**。
DEFAULT_THRESHOLD = 5.0

#: **宽度守卫**：只接受宽度 ≤ 这个值的检出段（"第二级复核"的第一版）。
#: 取值来自上面那张表：**4 是保住 1–2 采样咔哒的最小值**（3 会把 2 采样咔哒整段丢掉），
#: 同时把合法 5ms 起音的破坏从 30.55 dB 抬到 56.51 dB。
DEFAULT_MAX_RUN = 4


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def residual(y: np.ndarray) -> np.ndarray:
    """两点预测残差 `r[n] = y[n] − (y[n−1] + y[n+1])/2`（首尾样本置 0 ⇒ 不检测）。

    ⚠ 逐**声道**调用（一维）——本模块的检测/尺子都在**单声道**上定义，
    立体声由 `declick()` 逐声道跑（每声道一套判据）。
    """
    a = np.asarray(y, dtype=np.float64)
    r = np.zeros_like(a)
    if a.shape[0] >= 3:
        r[1:-1] = a[1:-1] - 0.5 * (a[:-2] + a[2:])
    return r


def detect(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
           max_run: int = DEFAULT_MAX_RUN):
    """检出掩码（**已过宽度守卫**）。尺子 = **全段** MAD（`median(|r|)/0.6745`）——它是**全局**统计量，

    ⇒ 这也是本算子**不分块**的原因（分块会让每块各有一套尺子，接缝处判据不一致）。
    返回 `(mask, r, s)`；`s` 是尺子值（如实报出去）。
    """
    raw, r, s = _detect_raw(y, threshold)
    return apply_max_run(raw, max_run), r, s


def apply_max_run(mask: np.ndarray, max_run: int = DEFAULT_MAX_RUN) -> np.ndarray:
    """**宽度守卫（第二级复核）**：只保留宽度 ≤ `max_run` 的检出段。

    "合法瞬态"（起音）在残差域里是**毫秒级**的连续偏离，而咔哒是**采样级**的脉冲。
    实测（`packaging/declick_verify_probe.py`）：`max_run=4` 时 1–2 采样咔哒一个不丢，
    同时把合法 5ms 起音的破坏从 **30.55 dB 抬到 56.51 dB**（被改采样 300 → 40）。
    ⚠ 挡不住**亚毫秒**起音（0.2 ms 的检出段宽度也是 2，与咔哒重叠）——这是方法边界。
    """
    if max_run is None or max_run <= 0:
        return mask
    out = np.zeros_like(mask)
    for start, end in _runs(mask):
        if end - start <= max_run:
            out[start:end] = True
    return out


def _runs(mask: np.ndarray):
    """连续 True 段 `(start, end)`（end 开区间）。"""
    i = 0
    while i < mask.size:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.size and mask[j]:
            j += 1
        yield i, j
        i = j


def _detect_raw(y: np.ndarray, threshold: float):
    """**未过宽度守卫**的检出（尺子 + 阈值判据）；`detect` 与统计共用它。"""
    r = residual(y)
    s = float(np.median(np.abs(r)) / 0.6745)
    if s <= 0.0:
        return np.zeros_like(r, dtype=bool), r, 0.0
    return np.abs(r) > threshold * s, r, s


def _repair_runs(y: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """检出段用两侧最近的非检出样本**线性插值**（单样本就是两点中点）。"""
    out = y.copy()
    i = 0
    while i < mask.shape[0]:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.shape[0] and mask[j]:
            j += 1
        a, b = i - 1, j
        left = out[a] if a >= 0 else None
        right = out[b] if b < out.shape[0] else None
        if left is not None and right is not None:
            out[i:j] = np.linspace(left, right, j - i + 2)[1:-1]
        elif left is not None:
            out[i:j] = left
        elif right is not None:
            out[i:j] = right
        i = j
    return out


def declick(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
            max_run: int = DEFAULT_MAX_RUN):
    """对 `(samples, channels)` 去咔哒，返回 `(out, stats)`。**只改检出样本**（逐声道独立检测）。

    ⚠ `threshold` 是**标定过的输入**（见 `DEFAULT_THRESHOLD`）：本模块**不替调用方猜**它，
    但会给一个实测过的默认值；改小它会把误报引进来（实测：FPR ~1e-3 时 ΔSDR 翻负）。
    ⚠ `max_run` 是**宽度守卫**（见 `DEFAULT_MAX_RUN` 与 `apply_max_run`）：`None` = 不设守卫
    （如实报出被拒的段数，让用户知道"有东西被挡在门外"）。
    """
    a = _as2d(y)
    if not threshold > 0.0:
        raise ValueError("检测阈值必须 > 0，得到 %r" % threshold)
    n, ch = a.shape
    out = a.copy()
    detected = np.zeros(n, dtype=bool)
    scales = []
    rejected = 0
    for c in range(ch):
        raw, _r, s = _detect_raw(a[:, c], threshold)
        mask = apply_max_run(raw, max_run)
        scales.append(s)
        rejected += sum(1 for st, en in _runs(raw) if not mask[st:en].any())
        if mask.any():
            out[:, c] = _repair_runs(a[:, c], mask)
            detected |= mask
    stats = {
        "threshold": float(threshold),
        "max_run": None if max_run is None else int(max_run),
        "detected_samples": int(detected.sum()),
        "fraction": int(detected.sum()) / (n * ch) if n * ch else 0.0,
        #: 各声道的残差尺子（**全段** MAD）。0 = 该声道数字静音（没有可检测的内容）。
        "residual_scale": [round(s, 8) for s in scales],
        "changed_samples": int((out != a).any(axis=1).sum()),
        #: **被宽度守卫挡下**的检出段数（"第二级复核"挡掉了多少）——如实记账，不许静默。
        "rejected_runs": int(rejected),
    }
    return out, stats
