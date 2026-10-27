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
* **非稳态噪声**：`track_floor=True`（**默认**，2026-10-27 落地）逐帧跟随噪声床的
  **电平**变化（bin 中位数比值，内容 bin 被中位数剔除；实测台阶床低参考总 ΔSDR
  +2.74 → +9.40，稳态代价 ≤0.66 dB，见 `docs/editor_research.md §19`）。
  **谱形**仍取自噪声段（形状随时间变——如噪声类型切换——没有跟）；
  错配参考的实测边界：**不修坏**（最危险方向 +6.03 dB），欠跟随方向收益留白。
  `floor_track_db`（min/max/mean）进统计，偏差可见。
* **两次被否决的跟踪机制**（如实记账，别再走回头路）：
  ① 帧宽带能量 SPP（v1a）：持续音的帧能量永不落到噪声底 ⇒ 恒冻结，跟不动；
  ② 逐 bin 滑窗最小值（v1b，Martin 最小统计量判据形态）：无偏置补偿的硬最小值
     **棘轮式塌缩**（稳态 −19 dB 灾难）——最小统计量必须配 Martin 的偏置补偿，
     而中位数比值方案不需要它（内容剔除靠中位数、偏置 ln2 精确可校）。
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

#: --- 逐帧噪声底跟踪（`track_floor=True` 时启用；常数全部实测标定，见
#: `packaging/denoise_nonstat_probe.py` 与 `docs/editor_research.md §19`）---
#: 递归平滑系数：逐 bin `N ← λ·N + (1−λ)·P`（噪声主导帧）。λ=0.8（hop=n_fft/4 ⇒
#: 时间常数 ~85 ms）——跟随台阶要快、压帧间起伏要稳的折中（0.5/0.8/0.95 扫描见 §19）。
TRACK_LAMBDA = 0.8
#: 缩放边界（±12 dB）：噪声床真实变化可超过此界，但无界缩放会在"参考段取错"时
#: 把错误放大 4 倍以上——宁可欠跟随，不可放大错误（与"宁可漏、不可错"同纪律）。
TRACK_SCALE_MAX = 4.0


def _floor_track(P: np.ndarray, N: np.ndarray) -> np.ndarray:
    """逐帧噪声底跟踪 v2：**bin 中位数比值**（确定性、无随机、可复算）。

    机理：噪声床台阶变化时，**所有噪声 bin 的 P/N 按同一倍数抬升/下降**；内容
    （谐波/纯音）只占少数 bin ⇒ **逐帧取 `median_b(P/N)`** 天然剔除内容 bin，
    读出的就是"床的倍数"。两个已知偏置都**精确可校**：
    * 指数分布（噪声功率）的总体中位数 = ln2·均值 ⇒ 乘 `1/ln2` 校正；
    * 帧间抖动 ⇒ 递归平滑 `λ = TRACK_LAMBDA`。
    边界（如实）：**宽带突发内容**（鼓/合唱盖住多数 bin 的帧）会把中位数顶起来
    ⇒ 该帧 N 被抬高（最多 +12 dB，clip）⇒ 突发被多压一点——有界、可审计
    （`floor_track_db` 进统计）。**下行即时、上行即时**（无 SPP 冻结）：
    中位数本身已是内容剔除器，不再叠时间判据。
    """
    bins, frames, ch = P.shape
    ratio = P / np.maximum(N[:, None, :], 1e-30)          # (bins, frames, ch)
    r = np.median(ratio, axis=0) * (1.0 / float(np.log(2.0)))   # (frames, ch) 偏置校正
    lam = TRACK_LAMBDA
    L = r[0].copy()
    out = np.empty((frames, ch))
    for t in range(frames):
        out[t] = np.clip(L, 1.0 / TRACK_SCALE_MAX, TRACK_SCALE_MAX)
        L = lam * L + (1.0 - lam) * r[t]
    return N[:, None, :] * out[None, :, :]

def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def _gains(P: np.ndarray, N: np.ndarray, *, g_min: float, alpha: float) -> np.ndarray:
    """逐帧逐 bin 的 Wiener-DD 增益。`P` = `(bins, frames, ch)`。

    `N` = `(bins, ch)`（固定谱）或 `(bins, frames, ch)`（逐帧谱——`track_floor` 时
    由 `_floor_track` 跟踪而来）。"""
    bins, frames, ch = P.shape
    G = np.empty_like(P)
    g_prev = np.ones((bins, ch))
    gamma_prev = np.ones((bins, ch))
    n = np.maximum(N, 1e-30)
    per_frame = (n.ndim == 3)
    for t in range(frames):
        nt = n[:, t, :] if per_frame else n
        gamma = np.maximum(P[:, t, :], 1e-30) / nt
        xi = (alpha * (g_prev ** 2) * gamma_prev
              + (1.0 - alpha) * np.maximum(gamma - 1.0, 0.0))
        g = np.maximum(xi / (1.0 + xi), g_min)
        G[:, t, :] = g
        g_prev, gamma_prev = g, gamma
    return G


def denoise(seg: np.ndarray, noise: np.ndarray, *, reduction_db: float = DEFAULT_REDUCTION_DB,
            n_fft: int = DEFAULT_N_FFT, hop: int | None = None,
            track_floor: bool = False) -> tuple[np.ndarray, dict]:
    """对 `seg` 去噪（噪声谱从 `noise` 学），返回 `(输出, 统计)`。

    `noise` 必须是**只有噪声**的素材（时长至少一个窗长）；`seg` 与 `noise` 的声道数必须一致。
    统计里 `noise_reduction_measured_db` 是**实测**：把同一套增益规则作用在噪声段上、
    合成后用 `metrics.energy_ratio_db` 量出的能量比（不是"设定值"的复述）。

    `track_floor=True`：在噪声段谱形 `N` 之上做**逐帧噪声底电平跟踪**（SPP 控制的
    递归平均，常数见 `TRACK_*`）——噪声床在选区里台阶变化时跟随电平、内容帧冻结。
    实测依据与"何时该开"见 `docs/editor_research.md §19`。
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
    N_track = None
    if track_floor:
        N_track = _floor_track(P, N)                      # (bins, frames, ch)
        G = _gains(P, N_track, g_min=g_min, alpha=ALPHA_DD)
    else:
        G = _gains(P, N, g_min=g_min, alpha=ALPHA_DD)
    out = stft.synthesize(S * G, meta)

    # 实测压制量：同一套规则作用在噪声段上（这是"噪声段被压了多少"的真音频测量）
    if track_floor:
        Gn = _gains(Pn, _floor_track(Pn, N), g_min=g_min, alpha=ALPHA_DD)
    else:
        Gn = _gains(Pn, N, g_min=g_min, alpha=ALPHA_DD)
    noise_out = stft.synthesize(Sn * Gn, meta_n)
    stats = {
        "reduction_db": float(reduction_db),
        "g_min_db": float(-reduction_db),
        "alpha_dd": ALPHA_DD,
        "n_fft": int(n_fft),
        "hop": int(hop),
        "track_floor": bool(track_floor),
        "noise_frames": int(Sn.shape[1]),
        "processed_frames": int(S.shape[1]),
        "processed_samples": int(a.shape[0] * a.shape[1]),
        "noise_reduction_measured_db": _round(energy_ratio_db(noise_out, nz)),
        "energy_ratio_measured_db": _round(energy_ratio_db(out, a)),
    }
    if N_track is not None:
        db = 10.0 * np.log10(np.maximum(N_track / np.maximum(N[:, None, :], 1e-30), 1e-30))
        stats["floor_track_db"] = {"min": _round(float(db.min())),
                                   "max": _round(float(db.max())),
                                   "mean": _round(float(db.mean()))}
    return out, stats


def _round(v: float | None) -> float | None:
    return None if v is None else round(float(v), 3)
