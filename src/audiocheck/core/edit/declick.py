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
"""
from __future__ import annotations

import numpy as np

#: 默认检测阈值（`|r| > α·s`）。**实测标定的"代价不对称"取值**（`packaging/declick_probe.py`）：
#: α=5 时**谐波音 / 带限白噪 / 合法瞬态三种素材的误报均为 0**，且四种素材 ΔSDR 全为正
#: （+40.6 / +3.6 / +2.0 / +6.5 dB）；而 α=4 会在合法瞬态上留下 ~12 个误报、α=3 直接**修坏**
#: （白噪 −13 dB）。⇒ **宁可漏、不可错**。
DEFAULT_THRESHOLD = 5.0


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


def detect(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD):
    """检出掩码。尺子 = **全段** MAD（`median(|r|)/0.6745`）——它是**全局**统计量，

    ⇒ 这也是本算子**不分块**的原因（分块会让每块各有一套尺子，接缝处判据不一致）。
    返回 `(mask, r, s)`；`s` 是尺子值（如实报出去）。
    """
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


def declick(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD):
    """对 `(samples, channels)` 去咔哒，返回 `(out, stats)`。**只改检出样本**（逐声道独立检测）。

    ⚠ `threshold` 是**标定过的输入**（见 `DEFAULT_THRESHOLD`）：本模块**不替调用方猜**它，
    但会给一个实测过的默认值；改小它会把误报引进来（实测：FPR ~1e-3 时 ΔSDR 翻负）。
    """
    a = _as2d(y)
    if not threshold > 0.0:
        raise ValueError("检测阈值必须 > 0，得到 %r" % threshold)
    n, ch = a.shape
    out = a.copy()
    detected = np.zeros(n, dtype=bool)
    scales = []
    for c in range(ch):
        mask, _r, s = detect(a[:, c], threshold)
        scales.append(s)
        if mask.any():
            out[:, c] = _repair_runs(a[:, c], mask)
            detected |= mask
    stats = {
        "threshold": float(threshold),
        "detected_samples": int(detected.sum()),
        "fraction": int(detected.sum()) / (n * ch) if n * ch else 0.0,
        #: 各声道的残差尺子（**全段** MAD）。0 = 该声道数字静音（没有可检测的内容）。
        "residual_scale": [round(s, 8) for s in scales],
        "changed_samples": int((out != a).any(axis=1).sum()),
    }
    return out, stats
