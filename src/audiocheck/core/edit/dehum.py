"""去嗡（dehum）：**零相位陷波级联**。Q 与谐波集都是**量出来的**，不是手感。

判据先行（`packaging/dehum_probe.py`，带限白噪 / 谐波音 × 50/60 Hz × 嗡 −20/−30 dB，
三族候选）。SDR 一律相对**真值**（合成时叠上去的嗡是已知的，拿掉后应回到 `clean`）：

| 素材（f0=50，嗡 −20 dB） | 因果 Q=10 | 零相位 Q=10 | 零相位 Q=40 | STFT 置零 ±24 Hz |
|---|---|---|---|---|
| 带限白噪 | 24.7 | **26.8** | 29.6 | 21.0 |
| 谐波音 | 27.4 | 33.3 | 36.1 | 46.9 |

两条结论：

1. **零相位 > 因果**（同 Q 高 2–3.6 dB）：我们是**离线**修复，"不引入相位失真"不用付
   实时性代价 ⇒ 用 `filtfilt`（正反各滤一遍，相位相互抵消）。
2. **STFT 置零不进内核**：它只在"嗡所在的 bin 本来没什么内容"的素材上占优（谐波音 46.9），
   在**宽带素材上崩到 21.0**——置零 ±24 Hz 等于在 50/100/150 Hz 各挖掉 4 个 bin 的内容。
   这正是 `docs/editor_research.md §7` 记过的"软掩码不进内核"的同一类现象（素材相关）。

## Q = 10 的依据（频率漂移扫描，带限白噪、嗡 −20 dB、1 次谐波压制 dB）

陷波的 −3 dB 宽 = `f0/Q`。电网与转档都**不是理想 50.000 Hz**，所以窄陷波会漏嗡：

| Q | ±0 Hz | ±0.25 | ±0.5 | ±1.0 |
|---|---|---|---|---|
| **10** | −27.1 | −26.9 | −24.7 | **−17.1** |
| 20 | −23.7 | −22.3 | −16.4 | −8.3 |
| 40 | −22.0 | −15.9 | −8.1 | **−2.9** |

⇒ **Q=10**：只有它在 ±1 Hz 漂移下仍压 ≥ 17 dB；Q=40 在 1 Hz 漂移下基本没去（2.9 dB）。
SDR 的代价是 ~2.8 dB（Q=10 vs Q=40，见上表）——**小于"漏掉嗡"的后果**（漏掉的嗡是持续的）。
⚠ ±1 Hz 是**工程假设（无外部出处）**：老录音走带偏速、电网频率偏离标称都落在这个量级。

## 效力边界（实测，必须让用户看见）

* **整段选区都被滤波**（LTI + 零相位）——不像去削波"只改饱和样本"。所以：
  **无嗡素材上它也会改动信号**（干净带限白噪、Q=10：SDR **27.6 dB**、能量 −0.017 dB）。
  ⇒ **收益由嗡的能量占比决定**，审计必须报**每个谐波压制了多少 dB**（实测值），
  而不是只报"跑过了"。
* 谐波 `k·f0 ≥ fs/2` 的**跳过并记账**（`skipped_harmonics`），不猜着折回。
* 选区太短（< 10 采样，`filtfilt` 的 `padlen=9` 之下）**明确拒绝**，不做"悄悄原样返回"。
"""
from __future__ import annotations

import numpy as np
from scipy import signal

from .metrics import band_gain_db

#: 陷波 Q。实测依据见模块 docstring 的漂移表（10 = 在 ±1 Hz 漂移下仍压 ≥17 dB 的最大 Q）。
DEFAULT_Q = 10.0

#: 默认清 1/2/3 次谐波（50/100/150 Hz 或 60/120/180 Hz）。更高次通常已低于本底。
DEFAULT_HARMONICS = (1, 2, 3)

#: 压制量的测量带宽（每个谐波 ±5 Hz）：覆盖 ±1 Hz 漂移 + 陷波过渡带。
MEASURE_HALF_HZ = 5.0

#: `filtfilt` 的默认 `padlen = 3·max(len(b), len(a)) = 9` ⇒ 至少 10 个采样才可定义。
MIN_SAMPLES = 10


def used_harmonics(f0: float, fs: int, harmonics=DEFAULT_HARMONICS) -> tuple:
    """实际会做陷波的谐波（`0 < k·f0 < fs/2`），升序返回。"""
    return tuple(sorted(int(k) for k in harmonics if 0 < k * f0 < fs / 2.0))


def dehum(x: np.ndarray, f0: float, fs: int, q: float = DEFAULT_Q,
          harmonics=DEFAULT_HARMONICS) -> tuple[np.ndarray, dict]:
    """零相位陷波级联。返回 `(out, stats)`；`stats` 里的压制量是**实测**（带内增益）。

    `f0` 必须由调用方给（它是**可测输入**：频谱上的嗡线；本函数不猜）。
    返回形状与输入一致（一维进一维出）。
    """
    if not f0 > 0.0:
        raise ValueError("基频必须 > 0，得到 %r" % f0)
    if not q > 0.0:
        raise ValueError("Q 必须 > 0，得到 %r" % q)
    a = np.asarray(x, dtype=np.float64)
    one_d = a.ndim == 1
    if one_d:
        a = a[:, None]
    ks = used_harmonics(f0, fs, harmonics)
    skipped = sorted(int(k) for k in harmonics if int(k) not in ks)
    if not ks:
        raise ValueError("所有谐波都 ≥ 奈奎斯特（%.1f Hz）：没有可滤的成分" % (fs / 2.0))
    if a.shape[0] < MIN_SAMPLES:
        raise ValueError("选区太短（%d 采样 < %d）：零相位滤波的边界填充无定义"
                         % (a.shape[0], MIN_SAMPLES))

    out = a.copy()
    for k in ks:
        b, aa = signal.iirnotch(k * f0, q, fs)
        out = signal.filtfilt(b, aa, out, axis=0)

    sup = [band_gain_db(a, out, fs, k * f0 - MEASURE_HALF_HZ, k * f0 + MEASURE_HALF_HZ)
           for k in ks]
    changed = int(np.any(out != a, axis=1).sum())
    stats = {
        "f0": float(f0),
        "q": float(q),
        "harmonics_used": list(ks),
        "skipped_harmonics": skipped,
        #: 每个谐波处的**实测**压制量（dB，负 = 压下去了）；`None` = 该带本来没能量
        "suppression_db": [None if s is None else round(float(s), 3) for s in sup],
        #: 被改动的采样数（LTI 滤波 ⇒ 恒等于选区采样数；如实给出，别让读者以为"只改了嗡"）
        "changed_samples": changed,
        "total_samples": int(a.shape[0]),
    }
    return (out[:, 0] if one_d else out), stats
