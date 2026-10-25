"""合成缺陷语料：给修复算法一套**有真值**的尺子（P3 判据地基）。

为什么必须有它（而不是拿真素材凑合）
------------------------------------
`docs/editor_roadmap.md §5` 规定：**每个修复算法落地前**必须先在**合成语料**上量化
"提升了多少"，再上真素材。真素材**没有真值** ⇒ 量不出"修好没修好"，只能靠耳朵，
那正是本项目不许的形态（"能算的绝不猜"）。

本模块只做三件事，**不掺任何算法**（算法在 `declip.py`，落地前必须先用本模块量原型）：

1. `harmonic_tone` / `band_limited_noise` —— **带宽已知**的干净信号（真值）。
   带宽已知是故意的：它让"带限重建"这类方法**可被判据**（`B` 是真相），
   也让 `metrics.sdr_db` 有个参照。两者都返回 `(n, 1)`。
2. `hard_clip` / `clip_stats` —— **硬削波**：`y = clip(s; c)`。按 `docs/theory.md §12.2`，
   饱和段的位置/个数与 `c` 都是**可测输入**，不是猜；本模块把真值一并给出。
   ⚠ 档位**不许偏温和**（Záviška/Rajmic et al. 2021 的明确教训：既有基准太软）——
   三档里最狠那档的**饱和占比实测 > 5%**（见 `probe_declip.py` 的实测表）。
3. `click_train` —— **脉冲噪声**：位置已知（为 `DeclickOp` 备料，本模块只备料不定算法）。
4. `white_noise` / `pink_noise` / `add_noise` —— **有真值的加性噪声**（SNR 是算出来的，
   不是"听着差不多"）：为去噪（`docs/editor_roadmap.md §4` 的 P3）备料。

判据在 `tests/test_edit_corpus.py`：非饱和段**逐位精确**、饱和集合与真值一致、
`c` 可从数据恢复、削波**近似能量中性**（只少了越过 `c` 的那点）、狠档占比够狠。
"""
from __future__ import annotations

import numpy as np

#: 默认采样率（与编辑内核其它部分的测试一致）。
FS_DEFAULT = 48000

#: 削波档位 = **相对干净信号峰值**的削波电平（越小削得越狠）。至少一档要"感知显著"。
CLIP_TIERS = {"light": 0.85, "moderate": 0.55, "severe": 0.28}


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


# ------------------------------------------------------------------ 干净信号（真值）
def harmonic_tone(seconds: float, fs: int = FS_DEFAULT, f0: float = 440.0,
                  n_partials: int = 40, amp: float = 0.95, seed: int = 0) -> np.ndarray:
    """`f0` 的 `n_partials` 次谐波之和（随机相位、`1/√k` 幅度），峰值归一化到 `amp`。

    **带宽 `B = n_partials·f0`**（默认 440·40 = 17.6 kHz < 奈奎斯特 24 kHz）是**真值**，
    带限重建类方法要用它做判据。返回 `(n, 1)`。
    """
    n = int(round(seconds * fs))
    t = np.arange(n) / fs
    rng = np.random.default_rng(seed)
    ph = rng.uniform(0.0, 2.0 * np.pi, n_partials)
    x = np.zeros(n)
    for k in range(1, n_partials + 1):
        x += (1.0 / np.sqrt(k)) * np.sin(2.0 * np.pi * f0 * k * t + ph[k - 1])
    peak = float(np.abs(x).max())
    x = x / peak * amp if peak > 0 else x
    return x[:, None]


def band_limited_noise(seconds: float, fs: int = FS_DEFAULT, bandwidth: float = 18000.0,
                       amp: float = 0.95, seed: int = 0) -> np.ndarray:
    """**严格带限**的白噪（真值带宽 = `bandwidth` Hz）：白噪 → rfft → 砍掉 `> bandwidth`
    → irfft → 归一化到 `amp`。返回 `(n, 1)`。

    ⚠ 砍频时**保留带内的所有 bin**（不做任何渐变），所以"带限"是**精确的**：
    测出的谱在 `bandwidth` 以上是数值零（判据之一）。
    """
    n = int(round(seconds * fs))
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n, 1.0 / fs)
    X[f > bandwidth] = 0.0
    y = np.fft.irfft(X, n=n)
    peak = float(np.abs(y).max())
    y = y / peak * amp if peak > 0 else y
    return y[:, None]


# ------------------------------------------------------------------ 硬削波（真值 c）
def hard_clip(s: np.ndarray, ceiling: float) -> np.ndarray:
    """硬削波 `y[n] = min(max(s[n], −c), c)`（`docs/theory.md §12.2` 的模型）。

    ⚠ 逐样本看：`|y| < c` ⇒ `s = y` **精确已知**；`|y| == c` ⇒ 只知道 `s` **越过** ±c
    （符号已知、幅度未知）。本模块据此把"真值"给全：`saturated` 掩码 = `|s| >= c`。
    """
    a = _as2d(s)
    if not ceiling > 0.0:
        raise ValueError("削波电平必须 > 0，得到 %r" % ceiling)
    return np.clip(a, -ceiling, ceiling)


def saturation_mask(y: np.ndarray, ceiling: float) -> np.ndarray:
    """饱和样本掩码：`|y| >= c`（与 `hard_clip` 的落点**逐位一致**：削过的样本恰好 == ±c）。

    返回与 `_as2d(y)` 同形状的 bool 数组。
    """
    return np.abs(_as2d(y)) >= ceiling


def saturation_runs(mask_1d: np.ndarray) -> list:
    """**连续饱和段** `[start, end)` 列表（`end` 为开区间 ⇒ 段长 = `end − start`）。"""
    m = np.asarray(mask_1d, dtype=bool)
    if m.size == 0:
        return []
    pad = np.concatenate([[False], m, [False]])
    d = np.diff(pad.astype(np.int8))
    starts = np.nonzero(d == 1)[0]
    ends = np.nonzero(d == -1)[0]
    return [(int(a), int(b)) for a, b in zip(starts, ends)]


def clip_stats(y: np.ndarray, ceiling: float) -> dict:
    """削波**如实记账**（逐声道扫一遍，聚合汇报）：

    * `saturated_samples` / `total_samples` / `fraction`：饱和样本**个数与占比**；
    * `frames_touched`：至少一个声道饱和的帧数；
    * `runs` / `longest_run`：连续饱和段个数与最长段长（段长决定了"锚点够不够" ⇒
      重建的**效力边界**，见 `declip.py`）。
    """
    a = _as2d(y)
    sat = saturation_mask(a, ceiling)
    ch_runs = [saturation_runs(sat[:, c]) for c in range(a.shape[1])]
    longest = max([e - s for rs in ch_runs for s, e in rs], default=0)
    n_runs = sum(len(rs) for rs in ch_runs)
    sat_samples = int(sat.sum())
    total = int(sat.size)
    return {
        "saturated_samples": sat_samples,
        "total_samples": total,
        "fraction": (sat_samples / total) if total else 0.0,
        "frames_touched": int(sat.any(axis=1).sum()),
        "runs": n_runs,
        "longest_run": int(longest),
    }


def ceiling_of(y: np.ndarray, k0: int | None = None, k1: int | None = None) -> float:
    """**从数据恢复**削波电平：区间内 `max|y|`。

    ⚠ 这**只在区间里真发生了削波**时才等于 `c`（因为削过的样本恰好 == ±c，而没削的
    样本必然 < c）——所以它是**可测输入**，不是猜。区间里没有削波时它退化成"最响那个
    样本的绝对值"，此时它**不代表 c**；调用方（`DeclipOp`）必须把来源如实报进审计。
    """
    a = _as2d(y)
    seg = a if k0 is None and k1 is None else a[k0:k1]
    if seg.size == 0:
        raise ValueError("区间为空，量不出峰值")
    return float(np.abs(seg).max())


def clipped(clean: np.ndarray, tier: str = "moderate", ceiling: float | None = None) -> dict:
    """把干净信号削一刀，返回**真值齐备**的一包（供修复算法的判据直接吃）。

    `ceiling` 给了就用它；否则用 `CLIP_TIERS[tier] × 干净信号峰值`。返回
    `{"clean", "clipped", "ceiling", "saturated", "stats", "tier"}`。
    """
    s = _as2d(clean)
    if ceiling is None:
        if tier not in CLIP_TIERS:
            raise ValueError("未知档位 %r，可选：%s" % (tier, sorted(CLIP_TIERS)))
        ceiling = CLIP_TIERS[tier] * float(np.abs(s).max())
    y = hard_clip(s, ceiling)
    return {"clean": s, "clipped": y, "ceiling": float(ceiling),
            "saturated": saturation_mask(y, ceiling), "stats": clip_stats(y, ceiling),
            "tier": tier}


def default_clean(seconds: float = 1.0, fs: int = FS_DEFAULT,
                  seed: int = 0) -> np.ndarray:
    """判据用的默认干净信号：**严格带限**到 0.9·奈奎斯特的白噪（带宽真值百分之九可算）。"""
    return band_limited_noise(seconds, fs=fs, bandwidth=0.9 * fs / 2.0, seed=seed)


# ------------------------------------------------------------------ 脉冲噪声（真值位置）
def click_train(clean: np.ndarray, positions_s, fs: int = FS_DEFAULT,
                amp: float = 0.9, sign: int = 1) -> dict:
    """在 `positions_s`（秒）各插一个**单采样脉冲**（`amp·sign`），返回真值齐备的一包。

    位置 → 采样落点用 `round(t·fs)`（这里是**合成**，不是编辑选区 ⇒ 用"最近采样"口径，
    与 `GainOp` 的半开 `ceil` 口径不同，别混）。返回
    `{"clean", "clicked", "positions_samples", "positions_s", "amp"}`。
    ⚠ 只备料；`DeclickOp` 尚未落地（`docs/editor_roadmap.md §4` 的 P3 顺序）。
    """
    s = _as2d(clean).copy()
    idx = [int(round(float(t) * fs)) for t in positions_s]
    for i in idx:
        if not 0 <= i < s.shape[0]:
            raise ValueError("脉冲位置越界：采样 %d 不在 [0, %d)" % (i, s.shape[0]))
        s[i] += amp * sign
    return {"clean": _as2d(clean), "clicked": s, "positions_samples": idx,
            "positions_s": [float(t) for t in positions_s], "amp": amp}


# ------------------------------------------------------------------ 加性噪声（真值 SNR）
def _normalize_rms(x: np.ndarray, rms: float) -> np.ndarray:
    """把 `x` 缩放到**实测 RMS = rms**（分母是算出来的，不是估的）。"""
    if not rms > 0.0:
        raise ValueError("RMS 必须 > 0，得到 %r" % rms)
    cur = float(np.sqrt(np.mean(_as2d(x) ** 2)))
    if cur <= 0.0:
        raise ValueError("全零信号归一化不出非零 RMS")
    return _as2d(x) * (rms / cur)


def _count(seconds: float, fs: int, min_n: int) -> int:
    """时长 → 采样数（四舍五入），并守住"至少 `min_n` 个采样"。"""
    n = int(round(seconds * fs))
    if n < min_n:
        raise ValueError("时长非法：%r s @ %r Hz ⇒ %d 采样（至少 %d）" % (seconds, fs, n, min_n))
    return n


def white_noise(seconds: float, fs: int = FS_DEFAULT, rms: float = 0.1,
                seed: int = 0, channels: int = 1) -> np.ndarray:
    """**白噪**：各样本独立同分布 `N(0,1)`，再缩放到实测 RMS = `rms`。返回 `(n, ch)`。"""
    return _noise_for(_count(seconds, fs, 1), channels, "white", rms, seed)


def pink_noise(seconds: float, fs: int = FS_DEFAULT, rms: float = 0.1,
               seed: int = 0, channels: int = 1) -> np.ndarray:
    """**粉噪**（`1/f` 功率谱 ⇒ 每倍频程等能量）：白噪 → rfft → 幅度乘 `1/√f` → irfft。

    口径写死：`|X(f)| ∝ f^(−1/2)`（功率 ∝ `1/f`），`f = 0` 的 bin 置 0（没有直流）。
    再缩放到实测 RMS = `rms`。返回 `(n, ch)`。
    """
    return _noise_for(_count(seconds, fs, 2), channels, "pink", rms, seed)


def add_noise(clean: np.ndarray, snr_db: float, kind: str = "white",
              seed: int = 0) -> dict:
    """给干净信号加**有真值**的噪声，SNR 按全段功率算：

        `SNR_dB = 10·log10( Σclean² / Σnoise² )`

    ⇒ 噪声 RMS = `sqrt(Σclean² / (N·10^(SNR/10)))`。噪声按**声道独立**生成
    （同一 `seed` 可复算）。返回 `{"clean", "noisy", "noise", "snr_db", "kind"}`。
    ⚠ `snr_db` 是**实数输入**（0 / −5 / 10 这类），没有"好/坏"门限。
    """
    s = _as2d(clean)
    if kind not in ("white", "pink"):
        raise ValueError("噪声类型只能是 white / pink，得到 %r" % kind)
    n, ch = s.shape
    p = float(np.sum(s ** 2))
    if p <= 0.0:
        raise ValueError("干净信号全零：SNR 无定义（要噪声床请直接用 white_noise/pink_noise）")
    rms = float(np.sqrt(p / (n * ch) / (10.0 ** (float(snr_db) / 10.0))))
    noise = _noise_for(n, ch, kind, rms, seed)
    return {"clean": s, "noisy": s + noise, "noise": noise,
            "snr_db": float(snr_db), "kind": kind}


def _noise_for(n: int, ch: int, kind: str, rms: float, seed: int) -> np.ndarray:
    """`add_noise` 的内部噪声发生器（按**样本**生成，不需要 fs：白噪与 1/f 形状都不依赖 fs）。"""
    if n < 1 or ch < 1:
        raise ValueError("采样数/声道数非法：%r / %r" % (n, ch))
    rng = np.random.default_rng(seed)
    if kind == "white":
        return _normalize_rms(rng.standard_normal((n, ch)), rms)
    f = np.fft.rfftfreq(n)
    shape = np.zeros_like(f)
    shape[1:] = 1.0 / np.sqrt(f[1:])
    y = np.zeros((n, ch))
    for c in range(ch):
        y[:, c] = np.fft.irfft(np.fft.rfft(rng.standard_normal(n)) * shape, n=n)
    return _normalize_rms(y, rms)
