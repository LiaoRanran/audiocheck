"""嗡的自动检测（`dehum` 的 `f0` 从哪来）—— **稳态**是判据，不是"有三条线"。

## 为什么不能只看谐波族

"50/100/150 Hz 上有三条线"不足以说明是嗡：一把 55 Hz 的贝斯也有 110/165。
实测（`packaging/hum_detect_probe.py`，同一把尺子）：

| 素材 | 三条线 excess（dB） | 帧间稳定性 `p95−p5`（dB） | 安静 20% 帧的 excess |
|---|---|---|---|
| 白噪（无嗡） | 3.5 / 4.1 / 4.1 | 9.9 / 9.1 / 10.1 | 0.8 / 0.5 / 1.9 |
| 白噪 + 50 Hz 嗡 −20 dB | 24.6 / 19.3 / 16.6 | 1.0 / 2.2 / 3.9 | 24.4 / 18.8 / 15.8 |
| 白噪 + 60 Hz 嗡 −30 dB | 15.1 / 8.1 / 7.3 | 5.5 / 7.0 / 9.3 | 14.0 / 6.1 / 4.6 |
| 谐波音 + 50 Hz 嗡 −30 dB | 51.2 / 45.2 / 41.8 | 0.0 / 0.0 / 0.0 | 51.2 / 45.2 / 41.8 |
| 贝斯 55 Hz（乐音） | **34.7 / 34.6 / 33.6** | **572 / 565 / 560** | **−445 / −438 / −434** |
| 贝斯 41.2 Hz（乐音） | 36.7 / 22.5 / 12.5 | 572 / 537 / 527 | −443 / −428 / −426 |
| 贝斯 82.4 Hz（乐音） | 1.7 / 1.2 / 17.7 | 505 / 516 / 540 | −413 / −425 / −431 |
| 贝斯 55 Hz + 50 Hz 嗡 −30 dB | 50.6 / 44.1 / 40.2 | 13.2 / 0.4 / 0.1 | 50.6 / 44.1 / 40.2 |

⇒ **excess 单独不够**（贝斯 55 Hz 的 34.7 dB 比 −30 dB 的嗡还高）；
**帧间稳定性**与**安静帧里还在不在**才是真正分开两类的东西（差距 20–500 dB）。
三个门限都落在**实测的空隙里**（不是拍出来的）：

* `MIN_EXCESS_DB = 4.0` —— 干净白噪最高 4.1，最弱的嗡（60 Hz −30 dB 的三次谐波）7.3；
* `MAX_STABILITY_DB = 30.0` —— 嗡最差 13.2，乐音最低 505；
* `MIN_QUIET_EXCESS_DB = 0.0` —— 乐音最高 1.9，嗡最低 4.6。

⚠ **本检测只回答"这段素材里有没有一条稳态工频线"**，不回答"该不该去嗡"。
稳态的低音持续音（管风琴、持续贝斯）与嗡在这套判据下**不可区分**——所以 CLI 把
检测到的 `f0` 与三项实测证据**打出来**，由用户确认（可审计，不偷偷动手）。

⚠ 素材太短（< 2 s）或谐波落不到奈奎斯特以下 ⇒ **拒绝**（不猜）。
"""
from __future__ import annotations

import numpy as np
from scipy import signal

from ...config import Thresholds

#: 谱图参数：16384 点 @48k ⇒ 频率格 2.93 Hz（50/55 Hz 分得开），帧移 4096。
NPERSEG = 16384
HOP = 4096

#: 检查的谐波次数（1/2/3 次）。少于 2 条落在奈奎斯特以下 ⇒ 拒绝。
K = 3

#: 三个判据门限在 `config.Thresholds`（`hum_min_excess_db / hum_max_stability_db /
#: hum_min_quiet_excess_db`）——它们落在实测空隙里，理由见模块 docstring。
#: **不在这里写死**：判据性阈值一律进 config（C0 卫生规则）。

#: 线水平的测量：取最近 bin；邻域底取 ±10 Hz（去掉线本身）。
HALF_HZ = 10.0

MIN_SECONDS = 2.0


def _lines(mag: np.ndarray, f: np.ndarray, f0: float) -> list[tuple]:
    """一条候选 f0 的三项证据：[(excess, stability, quiet_excess), ...]。"""
    df = float(f[1] - f[0])
    half = max(2, int(round(HALF_HZ / df)))
    out = []
    for k in range(1, K + 1):
        j = int(np.argmin(np.abs(f - k * f0)))
        if j < half + 1 or j > len(f) - half - 2:
            out.append((-999.0, 999.0, -999.0))
            continue
        line = mag[j, :]                     # **只取最近 bin**：位置要可分辨（±1 bin 会糊掉 50/55）
        nb = np.r_[mag[j - half:j - 2, :].ravel(), mag[j + 3:j + half + 1, :].ravel()]
        floor = float(np.median(nb))
        out.append((float(np.median(line)) - floor,
                    float(np.percentile(line, 95) - np.percentile(line, 5)),
                    float(np.percentile(line, 20)) - floor))
    return out


def _accept(ev: list[tuple], th: Thresholds) -> bool:
    return all(e >= th.hum_min_excess_db and s <= th.hum_max_stability_db
               and q >= th.hum_min_quiet_excess_db for e, s, q in ev)


def _peak_hz(f: np.ndarray, M: np.ndarray, lo: float = 44.0, hi: float = 66.0) -> float | None:
    """在**帧间中位数谱**的 `[lo, hi]` 里找最强的一条线，并做抛物线插值。

    抛物线插值（三个 bin 的 dB 值拟合）给的是**亚 bin** 频率——不插值的话
    50/60 Hz 只能报到最近的 2.93 Hz 格点上（实测会把 60 Hz 报成 61）。
    """
    df = float(f[1] - f[0])
    band = np.nonzero((f >= lo) & (f <= hi))[0]
    if band.size < 3:
        return None
    j = int(band[np.argmax(M[band])])
    if j <= 0 or j >= len(M) - 1:
        return None
    y0, y1, y2 = float(M[j - 1]), float(M[j]), float(M[j + 1])
    den = y0 - 2.0 * y1 + y2
    delta = 0.5 * (y0 - y2) / den if den != 0.0 else 0.0
    delta = float(np.clip(delta, -0.5, 0.5))
    return (j + delta) * df


def detect_hum(x: np.ndarray, fs: int, th: Thresholds | None = None) -> dict:
    """在时域素材上找**稳态工频线**。返回：

    `{"accepted", "f0_hz", "excess_db", "stability_db", "quiet_excess_db", "reason"}`。

    `accepted=False` 时 `f0_hz` 是**最好的候选**（一并给出证据），但调用方**不许**拿它去去嗡。
    """
    a = np.asarray(x, dtype=np.float64)
    if th is None:
        th = Thresholds()
    if a.ndim > 1:
        a = a.mean(axis=1)
    if a.shape[0] < MIN_SECONDS * fs:
        return {"accepted": False, "f0_hz": None, "reason":
                "素材太短（< %.0f s），帧间稳态量不出来" % MIN_SECONDS}
    if K * 66.0 >= fs / 2.0:
        return {"accepted": False, "f0_hz": None, "reason":
                "采样率 %d Hz 放不下 3 次谐波（要求 3×66 < fs/2）" % fs}

    # 窗长随采样率缩放 ⇒ **频率格恒为 2.93 Hz**（与 48k 的 16384 点等价）
    nperseg = max(256, int(round(NPERSEG * fs / 48000.0)))
    hop = max(1, nperseg // 4)
    f, _t, Z = signal.stft(a, fs=fs, nperseg=nperseg, noverlap=nperseg - hop,
                           window="hann", boundary=None, padded=False)
    if Z.shape[1] < 8:
        return {"accepted": False, "f0_hz": None, "reason": "帧数太少（< 8），量不出稳态"}
    mag = 20.0 * np.log10(np.abs(Z) + 1e-30)
    M = np.median(mag, axis=1)                 # 帧间中位数：**稳态**内容才留得下
    f0 = _peak_hz(f, M)
    if f0 is None:
        return {"accepted": False, "f0_hz": None, "reason": "低频段没有可检的线"}

    ev = _lines(mag, f, f0)
    accepted = _accept(ev, th)
    return {
        "accepted": bool(accepted),
        "f0_hz": round(float(f0), 2),
        "excess_db": [round(e, 2) for e, _s, _q in ev],
        "stability_db": [round(s, 2) for _e, s, _q in ev],
        "quiet_excess_db": [round(q, 2) for _e, _s, q in ev],
        "reason": ("三条线都在且稳态" if accepted else
                   "证据不足：需要 excess ≥ %.0f dB、帧间稳定 ≤ %.0f dB、安静帧也 ≥ %.0f dB"
                   % (th.hum_min_excess_db, th.hum_max_stability_db,
                      th.hum_min_quiet_excess_db)),
        "df_hz": round(float(f[1] - f[0]), 3),
    }
