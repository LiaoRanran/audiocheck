"""去噪内核：**从噪声段学谱** + 判决引导先验 SNR 的 Wiener 增益（P3 第四个修复算子）。

判据先行的结论（`packaging/denoise_probe.py` 实测，见 `docs/editor_research.md §16`）
--------------------------------------------------------------------------------
* **盲估计被否决**：不给噪声段、按"最低 10% 帧"估噪声，在 **+10 dB SNR 的持续音**上
  ΔSDR **−8.7 dB**（`tone+white +10 dB`，n_fft=2048 首轮）——因为持续音在**每一帧**
  都很响，"安静帧"里也含着音，逐 bin 噪声谱被音污染 ⇒ 把内容当噪声减掉。
  ⇒ **本算子必须给噪声段**（`noise_t0/noise_t1`），不给就拒绝。
* **噪声段的估计已经够好**：同一素材上 Wiener-DD 的 ΔSDR 与"逐帧真值噪声谱（oracle）"
  只差 **0.4–0.9 dB**（`packaging/denoise_probe.py` 的 oracle 行）——瓶颈不在估计。
* **Wiener-DD 选型**（n_fft=4096 / α_dd=0.98 / 压制上限 20 dB）：
  ΔSDR **+11.7 / +12.7 / +12.5 / +14.0 dB**（白噪 +10 / 0 / −5、粉噪 0），
  纯噪声段压制 **−19.9 dB**，musical-noise 代理 **+0.01 dB**；MMSE-LSA 在低 SNR 上
  只多 0.07 dB、瞬态损伤少 0.9 dB，但纯噪声压制少 **1.4 dB**、音乐噪声略高 ⇒
  **不为 0.07 dB 再开一个分支**（实测表见 `denoise_probe.py`）。
* **瞬态守卫两种候选都实测否决**：
  ① 按帧总能量（>2/4× 中位数）判瞬态：单采样脉冲只占帧能量千分之几 ⇒ **永不触发**；
  ② 按"有多少 bin 的信噪比 >4"判瞬态：阈值 5%/10% 在持续音上把**大量正常帧**误判
     （谐波本身就让 ~4% 的 bin 超额）⇒ 去噪几乎停摆（ΔSDR 掉到 +1.2 dB）；20% 又
     抓不到脉冲（脉冲 −13.6 dB 不变）。⇒ **不设瞬态守卫**，把"瞬态会被削软"
     （实测 −13.6 dB 峰值）如实写进审计与文档。

算法（全部是可复算的算式，没有"猜"的输入）
--------------------------------------------
1. 分析：`S = STFT(段)`（WOLA，周期性 Hann，hop = n_fft/4）。
2. 噪声谱 `N[k]` = **噪声段的逐帧平均功率**（逐声道）。这就是"可测输入"。
3. 判决引导先验 SNR（Ephraim–Malah 1984 的 DD 递归；⚠ 一手原文未取到，
   见 `docs/editor_research.md §5`，这里按二手标准式实现）：

       γ_t = |Y_t|² / N
       ξ_t = α·G_{t−1}²·γ_{t−1} + (1−α)·max(γ_t − 1, 0)
       G_t = ξ_t / (1 + ξ_t)   （Wiener）

4. 压制上限：`G_t ← max(G_t, 10^(−reduction_db/20))`（默认 20 dB）。
5. 合成：`S·G` → WOLA 合成。

**边界（必须让用户看见）**
* 陷波/去削波那套"只改缺陷样本"在这里不存在：增益是**逐帧逐 bin**的 ⇒ **整段选区都被改**；
  选区之外逐位不变（由渲染层保证）。
* 去噪**不保护瞬态**：宽带瞬态被当成噪声压掉一部分（实测 −13.6 dB 峰值）。
  要保住瞬态，只能降低 `reduction_db`（实测 10 dB 时脉冲 −7.9 dB），或把去噪放在
  修复链**最后**（`render.PHASE` 已如此排）。
* **非稳态噪声**（音量/频谱随时间变）没做：`N` 是噪声段的**平均**谱。噪声段若不代表
  整段，去噪会偏深或偏浅——审计把 `noise_reduction_measured_db` 报出来，让偏差可见。
"""
from __future__ import annotations

import numpy as np

from . import stft
from .metrics import energy_ratio_db

#: 窗长/跳步：由 `packaging/denoise_probe.py` 的窗长扫描定（1024/2048/4096 的
#: ΔSDR 为 +8.2/+10.8/+12.6 dB，4096 最好且压制量不变）⇒ 取 4096、hop = n_fft/4。
DEFAULT_N_FFT = 4096

#: 压制上限（dB）：20 dB 的 musical-noise 代理只涨 **+0.01 dB**；放到 30 dB 涨 +0.23 dB，
#: 只换 0.64 dB 的 ΔSDR ⇒ 取保守侧（误报/伪影的代价不对称，沿用去咔哒的规矩）。
DEFAULT_REDUCTION_DB = 20.0

#: DD 平滑系数。扫描 0.90/0.95/0.98/0.99 的 ΔSDR：+11.1/+12.2/**+12.6**/+12.4，
#: 纯噪声压制 −15.6/−18.6/**−19.9**/−20.0 ⇒ **0.98 在 ΔSDR 上最好**，取它。
ALPHA_DD = 0.98


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def _gains(P: np.ndarray, N: np.ndarray, *, g_min: float, alpha: float) -> np.ndarray:
    """逐帧逐 bin 的 Wiener-DD 增益。`P` = `(bins, frames, ch)`，`N` = `(bins, ch)`。"""
    bins, frames, ch = P.shape
    G = np.empty_like(P)
    g_prev = np.ones((bins, ch))
    gamma_prev = np.ones((bins, ch))
    n = np.maximum(N, 1e-30)
    for t in range(frames):
        gamma = np.maximum(P[:, t, :], 1e-30) / n
        xi = (alpha * (g_prev ** 2) * gamma_prev
              + (1.0 - alpha) * np.maximum(gamma - 1.0, 0.0))
        g = np.maximum(xi / (1.0 + xi), g_min)
        G[:, t, :] = g
        g_prev, gamma_prev = g, gamma
    return G


def denoise(seg: np.ndarray, noise: np.ndarray, *, reduction_db: float = DEFAULT_REDUCTION_DB,
            n_fft: int = DEFAULT_N_FFT, hop: int | None = None) -> tuple[np.ndarray, dict]:
    """对 `seg` 去噪（噪声谱从 `noise` 学），返回 `(输出, 统计)`。

    `noise` 必须是**只有噪声**的素材（时长至少一个窗长）；`seg` 与 `noise` 的声道数必须一致。
    统计里 `noise_reduction_measured_db` 是**实测**：把同一套增益规则作用在噪声段上、
    合成后用 `metrics.energy_ratio_db` 量出的能量比（不是"设定值"的复述）。
    """
    a = _as2d(seg)
    nz = _as2d(noise)
    if a.shape[1] != nz.shape[1]:
        raise ValueError("声道数不一致：段 %d / 噪声段 %d" % (a.shape[1], nz.shape[1]))
    if not reduction_db > 0.0:
        raise ValueError("压制上限必须 > 0 dB，得到 %r" % reduction_db)
    hop = n_fft // 4 if hop is None else hop
    stft.check_params(n_fft, hop)
    if nz.shape[0] < n_fft:
        raise ValueError("噪声段太短：至少 %d 个采样（一个窗长），得到 %d"
                         % (n_fft, nz.shape[0]))

    S, meta = stft.analyze(a, n_fft=n_fft, hop=hop)
    P = np.abs(S) ** 2
    Sn, meta_n = stft.analyze(nz, n_fft=n_fft, hop=hop)
    Pn = np.abs(Sn) ** 2
    N = Pn.mean(axis=1)                                   # (bins, ch)
    g_min = 10.0 ** (-float(reduction_db) / 20.0)
    G = _gains(P, N, g_min=g_min, alpha=ALPHA_DD)
    out = stft.synthesize(S * G, meta)

    # 实测压制量：同一套规则作用在噪声段上（这是"噪声段被压了多少"的真音频测量）
    Gn = _gains(Pn, N, g_min=g_min, alpha=ALPHA_DD)
    noise_out = stft.synthesize(Sn * Gn, meta_n)
    stats = {
        "reduction_db": float(reduction_db),
        "g_min_db": float(-reduction_db),
        "alpha_dd": ALPHA_DD,
        "n_fft": int(n_fft),
        "hop": int(hop),
        "noise_frames": int(Sn.shape[1]),
        "processed_frames": int(S.shape[1]),
        "processed_samples": int(a.shape[0] * a.shape[1]),
        "noise_reduction_measured_db": _round(energy_ratio_db(noise_out, nz)),
        "energy_ratio_measured_db": _round(energy_ratio_db(out, a)),
    }
    return out, stats


def _round(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)
