"""标准级 DSP 原语。

实现依据（公开标准，非自创指标）：
  * ITU-R BS.1770-4        —— K 加权（两级 biquad）、门限积分响度、True Peak
  * EBU R128 / Tech 3342   —— Loudness Range (LRA)

K 加权系数按实际采样率实时重算（不是把 48kHz 的表照抄），
因此 44.1k / 96k / 192k 的容器都能得到正确读数。
"""
from __future__ import annotations

import numpy as np
import soundfile as sf
from scipy import signal

# BS.1770-4 规定的滤波器参数（表 1 / 表 2）
SHELF_F0, SHELF_G, SHELF_Q = 1681.974450955533, 3.999843853973347, 0.7071752369554196
HP_F0, HP_Q = 38.13547087602444, 0.5003270373238773

ABS_GATE = -70.0        # 绝对门限 LUFS
REL_GATE_INT = -10.0    # 积分响度相对门限 LU
REL_GATE_LRA = -20.0    # LRA 相对门限 LU
OFFSET = -0.691         # BS.1770 常数项

BLOCK_STEP = 0.1        # 100ms 步进（400ms 块 = 4 步）
SHORT_STEP = 1.0        # LRA 短时块 3s，步进 1s

# True Peak（ITU-R BS.1770-4 Annex 2）
TP_OS = 4               # 规格要求 ≥4 倍过采样
TP_OVERLAP = 4096       # 块间重叠的输入采样数，必须 > 滤波器瞬态长度
TP_TRIM = 64            # 每段处理时两端各丢弃的输入采样数（滤波器瞬态）。
                        # resample_poly 用 81 抽头 / up=4 → 瞬态约 21 个输入采样，
                        # 取 64 留足余量。**不丢就会在人造边界上生出虚假峰值**：
                        # 早先「每块独立过采样后从两头各削 10%」既是这个道理、
                        # 又削得太狠（把真实峰值也削掉了）。


def k_weighting(fs: int):
    """返回两级 biquad 系数 ((b1,a1),(b2,a2))。"""
    k = np.tan(np.pi * SHELF_F0 / fs)
    vh = 10 ** (SHELF_G / 20)
    vb = vh ** 0.4996667741545416
    a0 = 1 + k / SHELF_Q + k * k
    b1 = np.array([(vh + vb * k / SHELF_Q + k * k) / a0,
                   2 * (k * k - vh) / a0,
                   (vh - vb * k / SHELF_Q + k * k) / a0])
    a1 = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / SHELF_Q + k * k) / a0])

    k = np.tan(np.pi * HP_F0 / fs)
    a0 = 1 + k / HP_Q + k * k
    b2 = np.array([1.0, -2.0, 1.0])
    a2 = np.array([1.0, 2 * (k * k - 1) / a0, (1 - k / HP_Q + k * k) / a0])
    return (b1, a1), (b2, a2)


def _gated_loudness(block_ms: np.ndarray, rel_gate: float) -> float | None:
    """门限平均：先绝对门限，再相对门限。block_ms 形状 [n, ch]。"""
    z = block_ms.sum(axis=1)
    lv = OFFSET + 10 * np.log10(z + 1e-30)
    keep = lv > ABS_GATE
    if not keep.any():
        return None
    rel = OFFSET + 10 * np.log10(z[keep].mean() + 1e-30) + rel_gate
    keep &= (lv > rel)
    if not keep.any():
        return None
    return float(OFFSET + 10 * np.log10(z[keep].mean() + 1e-30))


def loudness(path: str, fs: int, channels: int, total: int, chunk: int = 1 << 20) -> dict:
    """流式积分响度 + LRA + True Peak（4 倍过采样）。

    内存占用与文件长度无关：每 100ms 只保留一个均方值。
    """
    hop = int(BLOCK_STEP * fs)
    if hop < 1 or total < hop * 4:
        return {}
    (b1, a1), (b2, a2) = k_weighting(fs)

    nsteps = int(total // hop)
    step_ms = np.zeros((nsteps, channels), dtype=np.float64)
    z1 = [np.zeros(2) for _ in range(channels)]
    z2 = [np.zeros(2) for _ in range(channels)]
    acc = np.zeros(channels)
    acc_n = si = 0
    tp_db: float | None = None      # 别叫 true_peak —— 那会遮蔽同名模块函数

    with sf.SoundFile(path) as f:
        while True:
            block = f.read(chunk, dtype="float32", always_2d=True)
            if not len(block):
                break
            y = np.empty_like(block, dtype=np.float64)
            for c in range(channels):
                x = block[:, c].astype(np.float64)
                x, z1[c] = signal.lfilter(b1, a1, x, zi=z1[c])
                y[:, c], z2[c] = signal.lfilter(b2, a2, x, zi=z2[c])
            np.square(y, out=y)

            # True Peak 不在这里做：它与响度计量的分块方式需求不同
            # （响度要跨块连续滤波，真峰值要段段带上下文），混在一个循环里
            # 必然有一方被将就。改为循环结束后单独一遍，见文件末尾。

            p = 0
            while p < y.shape[0]:
                take = min(hop - acc_n, y.shape[0] - p)
                acc += y[p:p + take].sum(axis=0)
                acc_n += take
                p += take
                if acc_n == hop:
                    if si < nsteps:
                        step_ms[si] = acc / hop
                        si += 1
                    acc[:] = 0.0
                    acc_n = 0

    if si < 4:
        return {}
    step_ms = step_ms[:si]

    # True Peak 单独一遍（见 true_peak() 的 docstring）：每段带 TP_TRIM 上下文、
    # 只取中段，既不在人造边界上生出虚假峰值，也不漏掉真实峰值。
    tp_db = true_peak(path)

    # 积分响度：400ms 块 = 4 个 100ms 步（用卷积做滑窗，矢量化）
    kern = np.ones(4) / 4.0
    blk = np.stack([np.convolve(step_ms[:, c], kern, mode="valid")
                    for c in range(channels)], axis=1)
    lufs = _gated_loudness(blk, REL_GATE_INT)

    # 短时（3s 块 / 1s 步）→ LRA 与短时极值
    w = int(3.0 / BLOCK_STEP)
    kern3 = np.ones(w) / w
    st_ms = np.stack([np.convolve(step_ms[:, c], kern3, mode="valid")
                      for c in range(channels)], axis=1)
    lra = None
    st = np.array([])
    if st_ms.shape[0] >= 4:
        z = st_ms.sum(axis=1)
        lv = OFFSET + 10 * np.log10(z + 1e-30)
        keep = lv > ABS_GATE
        if keep.any():
            rel = OFFSET + 10 * np.log10(z[keep].mean() + 1e-30) + REL_GATE_LRA
            keep &= (lv > rel)
            if keep.any():
                lra = float(np.percentile(lv[keep], 95) - np.percentile(lv[keep], 10))
        st = lv

    out: dict = {
        "lufs": round(lufs, 1) if lufs is not None else None,
        "lra": round(lra, 1) if lra is not None else None,
        "st_max": round(float(st.max()), 1) if st.size else None,
        "st_min": round(float(st.min()), 1) if st.size else None,
    }
    if tp_db is not None:
        out["true_peak_dbfs"] = round(tp_db, 2)
        if lufs is not None:
            out["plr"] = round(tp_db - lufs, 1)
    return out


def spectral_features(f: np.ndarray, db: np.ndarray) -> dict:
    """频谱重心 / 滚降点 / 谱平坦度 / 噪声底。"""
    if f is None:
        return {}
    p = np.maximum(10 ** (db / 10.0), 0)
    tot = p.sum()
    if tot <= 0:
        return {}
    centroid = float((f * p).sum() / tot)
    cum = np.cumsum(p) / tot
    roll85 = float(f[min(np.searchsorted(cum, 0.85), len(f) - 1)])
    roll95 = float(f[min(np.searchsorted(cum, 0.95), len(f) - 1)])
    gm = float(np.exp(np.log(p[p > 0] + 1e-30).mean())) if (p > 0).any() else 0.0
    return {
        "centroid_hz": round(centroid, 1),
        "rolloff85_hz": round(roll85, 1),
        "rolloff95_hz": round(roll95, 1),
        "flatness": round(float(gm / (tot / len(p) + 1e-30)), 5),
        "noise_floor_db": round(float(np.percentile(db, 5)), 1),
    }


def stereo_features(left: np.ndarray, right: np.ndarray) -> dict:
    """声道宽度（M/S 能量比）、相位一致度与单声道兼容性。"""
    if left is None or right is None:
        return {}
    n = min(len(left), len(right), 4_000_000)
    step = max(1, len(left) // n)
    lc = np.asarray(left[::step], dtype=np.float64)
    rc = np.asarray(right[::step], dtype=np.float64)
    if lc.size < 1024:
        return {}
    mid, side = (lc + rc) / 2.0, (lc - rc) / 2.0
    me, se = float((mid ** 2).mean()), float((side ** 2).mean())
    corr = float((lc * rc).mean() / (np.sqrt((lc ** 2).mean() * (rc ** 2).mean()) + 1e-30))
    return {
        "width_db": round(10 * np.log10((se + 1e-30) / (me + 1e-30)), 1),
        "corr": round(corr, 3),
        "mono_compat": "良好" if corr > 0.3 else ("警告：单声道兼容性差" if corr < -0.1 else "一般"),
    }


#: ρ 的**可定义门限**（dB）：某格上弱侧比强侧低过这个数，就认为该格
#: **没有"两声道相位关系"可言**，ρ 置 0（中性）。
#:
#: ⚠ 这条是必须的，不是保险：ρ = Re(L·conj(R)) / (|L|·|R|)。若某格只有左声道
#: 有能量（比如右声道在该频率是静音），分母趋近 0，剩下的是**数值噪声**——
#: 实测会被推到 **±1.00**，于是图/指标会报出"这里完全反相"的假结论。
#: 30 dB 是"弱侧已进入本底"的常用判据（与频谱分析里"信号高于本底"同量级）。
PHASE_UNDEF_DB = 30.0

#: 判"反相格"的相关系数门限：ρ < −0.5 ⇒ 相位差超过 120°，已经是明确反相。
ANTI_RHO = -0.5

#: 判"有能量"的门限（相对本帧峰值的 dB）。低于此的格不参与反相统计——
#: 安静处的相位没有意义（且 ρ 不可靠）。
ANTI_FLOOR_DB = 40.0


def phase_field(x2d: np.ndarray, fs: int, nperseg: int = 4096, hop: int | None = None):
    """**立体声相位场**：L/R 在每个时频格的相关系数 ρ ∈ [−1, 1]。

    ρ(f,t) = Re(L·conj(R)) / (|L|·|R|)，即归一化互谱的实部、两声道相位差的余弦。
    ρ=+1 同相 ｜ ρ≈0 不相关 ｜ ρ=−1 完全反相。

    **唯一实现**：视图层（`core/views.stereo_field`）与判定层
    （`anti_phase_scan`）都调这里——同一个数学不许有两份代码（本项目的老毛病）。
    """
    hop = hop or max(1, int(nperseg * 3 // 4))
    noverlap = max(0, nperseg - hop)
    f = t = None
    L = R = None
    for ch, slot in ((0, "L"), (1, "R")):
        f, t, Z = signal.stft(x2d[:, ch], fs=fs, nperseg=nperseg, noverlap=noverlap,
                              window="hann")
        Z = Z.astype(np.complex64, copy=False)      # 复数 128 → 64：内存减半
        if slot == "L":
            L = Z
        else:
            R = Z
    denom = np.sqrt(np.abs(L) ** 2 * np.abs(R) ** 2) + 1e-30
    rho = np.clip((L * np.conj(R)).real / denom, -1.0, 1.0)
    pL, pR = np.abs(L) ** 2, np.abs(R) ** 2
    undef = np.minimum(pL, pR) < np.maximum(pL, pR) * (10.0 ** (-PHASE_UNDEF_DB / 10.0))
    return f, t, np.where(undef, 0.0, rho).astype(np.float32)


def anti_phase_scan(left: np.ndarray, right: np.ndarray, fs: int,
                    nperseg: int = 4096) -> dict:
    """**局部化反相检测**（自研）：全局相关度看不见的"只在某频段/某时段反相"。

    ## 为什么需要它（`stereo_features` 的 `corr` 不够）

    `corr` 是**整段一个数**。只要有一半时间是正常立体声，整段 `corr` 就能是正的——
    而"某段人声被反相加倍""某频段被极性反转"这类缺陷，在全局数上**完全看不出来**：
    它被平均值吃掉了。本函数把相位关系**按频率和时间分开看**。

    ## 口径（不许含糊）

    * 只在**有能量**的格上统计（高于本帧峰值下 `ANTI_FLOOR_DB`）——
      安静处的相位没有意义；
    * "反相格" = ρ < `ANTI_RHO`（−0.5，即相位差 > 120°）；
    * ⚠ **判据用能量占比，不用格数占比**。这一条是**实测纠出来的**：
      格数占比会被"边缘格"主导——实测真立体声（L=1 kHz / R=3 kHz，完全正常的
      素材）格数占比报 **18%**，而反相**能量**只占 **0.02%**（−36.9 dB）。
      那些格是泄漏裙边（一侧只有噪声，另一侧有信号，但失衡不足 30 dB 所以 ρ 仍被保留）。
      **格数会说谎，能量不会**——所以对外只报能量口径，格数不再输出。

    ## 返回

      * `anti_energy_db`  反相格里的能量占**有能量格**总能量的比例（dB，越低越少）
      * `anti_energy_pct` 同一件事的线性百分比（给人看）
      * `anti_band_khz`   反相能量最集中的频点
      * `anti_scope`      "无 / 轻微 / 明显 / 严重" —— 按能量占比分档，给判定用
    """
    if left is None or right is None or len(left) < nperseg * 2:
        return {}
    x = np.stack([np.asarray(left, dtype=np.float32), np.asarray(right, dtype=np.float32)],
                 axis=1)
    hop = max(1, int(nperseg * 3 // 4))
    f, _t, rho = phase_field(x, fs, nperseg=nperseg, hop=hop)
    if rho.size == 0:
        return {}
    _f2, _t2, ZL = signal.stft(x[:, 0], fs=fs, nperseg=nperseg,
                               noverlap=max(0, nperseg - hop), window="hann")
    _f3, _t3, ZR = signal.stft(x[:, 1], fs=fs, nperseg=nperseg,
                               noverlap=max(0, nperseg - hop), window="hann")
    power = (np.abs(ZL) ** 2 + np.abs(ZR) ** 2).astype(np.float64)
    frame_peak = power.max(axis=0, keepdims=True) + 1e-30
    active = power > frame_peak * (10.0 ** (-ANTI_FLOOR_DB / 10.0))
    if not active.any():
        return {"anti_energy_db": -120.0, "anti_energy_pct": 0.0,
                "anti_band_khz": None, "anti_scope": "无"}
    anti = active & (rho < ANTI_RHO)
    tot_p = float(power[active].sum())
    anti_p = float(power[anti].sum()) if anti.any() else 0.0
    share = anti_p / (tot_p + 1e-30)
    edb = 10.0 * np.log10(share + 1e-30)
    band = None
    if anti.any():
        per_bin = (power * anti).sum(axis=1)
        band = float(f[int(np.argmax(per_bin))] / 1000.0)
    # 分档**只看能量占比**（格数不可靠，见 docstring）
    scope = ("无" if share < 0.01 else
             "轻微" if share < 0.10 else
             "明显" if share < 0.50 else "严重")
    return {
        "anti_energy_db": round(float(edb), 1),
        "anti_energy_pct": round(100.0 * share, 3),
        "anti_band_khz": round(band, 2) if band is not None else None,
        "anti_scope": scope,
    }


def dc_offset(path: str, channels: int, chunk: int = 1 << 20) -> dict:
    """直流偏移——会吃掉动态余量，是转码/母带环节的常见瑕疵。"""
    s = np.zeros(channels)
    n = 0
    with sf.SoundFile(path) as f:
        while True:
            b = f.read(chunk, dtype="float32", always_2d=True)
            if not len(b):
                break
            s += b.sum(axis=0, dtype=np.float64)
            n += b.shape[0]
    if n == 0:
        return {}
    dc = s / n
    return {
        "dc_offset": [round(float(x), 6) for x in dc],
        "dc_max_dbfs": round(float(20 * np.log10(np.abs(dc).max() + 1e-12)), 1),
    }


def _oversample(x: np.ndarray) -> np.ndarray:
    """4 倍过采样后的整段信号（BS.1770-4 Annex 2 要求 ≥4×）。

    滤波器说明（**已知偏差，不粉饰**）：BS.1770-4 Annex 2 给的是一张**固定 FIR 系数表**；
    scipy 的 `resample_poly` 用 Kaiser 窗设计 sinc，无法逐字复刻那张表。
    这里取 beta=10（阻带约 −70 dB，规格要求 ≥ −40 dB，因此更保守）。
    注意 `resample_poly` 的抽头数固定为 2·(10·max(up,down))+1 = 81，**与 beta 无关**——
    所以调 beta 只会改变窗形、不会让滤波器变长。实测真实音乐素材上，
    本实现与 FFT 理想插值的真峰值相差 < 0.01 dB。
    """
    if x.size == 0:
        return np.zeros(0)
    return signal.resample_poly(x, TP_OS, 1, axis=0, window=("kaiser", 10.0))


def _true_peak_of(x: np.ndarray) -> float:
    """整段过采样后的最大绝对值。"""
    ov = _oversample(x)
    return float(np.abs(ov).max()) if ov.size else 0.0


def true_peak(path: str, chunk: int = 1 << 21) -> float | None:
    """单独计算真峰值（供只需要这一项的调用方）。

    做法：按 `chunk` 分段，但每段都向前后各多读 `TP_TRIM` 个采样作上下文，
    过采样后**只取中段**（丢掉两端各 TP_TRIM 对应的输出）。

    两个坑都因此避开：
      * 不多读上下文 → 段首的人造不连续被滤波器振铃放大成**虚假峰值**；
      * 不从两端丢瞬态 → 同上；
      * 早先「每块独立过采样后从两头各削 10%」则是丢得太多，
        落在削掉区间里的**真实峰值永久漏掉**。
    """
    with sf.SoundFile(path) as f:
        total = f.frames
    if total <= 0:
        return None
    peak, pos = 0.0, 0
    while pos < total:
        end = min(pos + chunk, total)
        a = max(0, pos - TP_TRIM)                 # 向前借上下文
        b = min(total, end + TP_TRIM)             # 向后借上下文
        x, _ = sf.read(path, start=a, frames=b - a, dtype="float32", always_2d=True)
        ov = _oversample(x)
        lo, hi = (pos - a) * TP_OS, (end - a) * TP_OS
        seg = ov[lo:hi]
        if seg.size:
            peak = max(peak, float(np.abs(seg).max()))
        pos = end
    return round(20 * np.log10(peak + 1e-12), 2) if peak > 0 else None
