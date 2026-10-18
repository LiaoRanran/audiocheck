"""编辑侧**测量层**：把"改完到底怎么样"变成一个可复算的数。

为什么要有这一层（而不是在测试里各写一遍）
------------------------------------------
`docs/editor_roadmap.md §2` 把本项目的定位写成"**可审计的修复**"：竞品是黑盒，
我们每次修复都要给出"改了什么、依据哪条测量、结果如何"。那"结果如何"就必须是**数**，
而且是**同一把尺子**量出来的数——测试、CLI、决策实验三处各写一遍，迟早分叉。

⚠ 本模块**只做相对测量**（比值、跨度、误差比），**不设任何判定门限**。
本仓铁律："加阈值前先跑区分力检验、门限不许凭空升格"——所以这里的输出是
"**多少 dB / 多少 ms**"，不是"好/坏"。要不要判"好/坏"，属于上层（判定/验收）的事。

四个函数（都有"答案已知"的判据，见 `tests/test_edit_metrics.py`）：

* `band_gain_db` —— **实测带内衰减**：调用方给"设了 −60 dB"，还得给"实测 −59.8 dB"。
* `envelope_width_ms` —— **脉冲包络宽度**：量化时变增益对瞬态的**涂改**（展宽/振铃）。
* `sdr_db` —— 源分离口径的**信噪比**：为去削波/去咔哒这类"有真值的修复"铺路。
* `energy_ratio_db` —— 幅度**缩水/膨胀**：PHAIN 的警告（"变安静"被误当"修好了"）就靠它防。
"""
from __future__ import annotations

import math

import numpy as np
from scipy import signal


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def _seg(x: np.ndarray, k0: int | None, k1: int | None) -> np.ndarray:
    a = _as2d(x)
    if k0 is None and k1 is None:
        return a
    if k0 is None or k1 is None:
        raise ValueError("k0/k1 要么都给，要么都不给（半开区间 [k0, k1)）")
    return a[k0:k1]


def band_energy(x: np.ndarray, fs: int, f0: float, f1: float,
                k0: int | None = None, k1: int | None = None) -> float:
    """`[k0,k1)` 段内、`[f0,f1)` 频带里的**功率**（Hann 窗 DFT 的 bin 功率和）。

    ⚠ 段长决定了格点间距（`fs/段长`），带边会落进某一个 bin ⇒ 带边做窄带测量时
    要把 `f0/f1` 放在**格点之间**（调用方负责；`tests` 里的带都是宽于 1 kHz 的）。
    """
    seg = _seg(x, k0, k1)
    if seg.shape[0] < 8:
        raise ValueError("段太短，量不出带功率：%d 采样" % seg.shape[0])
    w = np.hanning(seg.shape[0])[:, None]
    X = np.fft.rfft(seg * w, axis=0)
    bins = np.fft.rfftfreq(seg.shape[0], 1.0 / fs)
    m = (bins >= f0) & (bins < f1)
    return float(np.sum(np.abs(X[m]) ** 2))


def band_gain_db(before: np.ndarray, after: np.ndarray, fs: int, f0: float, f1: float,
                 k0: int | None = None, k1: int | None = None) -> float | None:
    """**实测**的带内增益（dB）：`10·log10(带功率_后 / 带功率_前)`。

    `None` = 段内本来就没能量（分母为 0）⇒ 比值无定义，绝不返回 0 冒充"没变"。
    """
    e0 = band_energy(before, fs, f0, f1, k0, k1)
    if e0 <= 0.0:
        return None
    return 10.0 * math.log10(band_energy(after, fs, f0, f1, k0, k1) / e0)


def envelope_width_ms(x: np.ndarray, fs: int, k0: int | None = None, k1: int | None = None,
                      rel_db: float = -40.0) -> float | None:
    """**脉冲包络宽度**（ms）：Hilbert 包络在峰值的 `rel_db` 高度上的跨越宽度。

    用途 = 量化"时变增益对瞬态做了什么"：掩码边界把能量摊开（涂改/pre-echo）时，
    同一个脉冲的包络会**变宽**；纯幅度缩放**不改**宽度（那是判据，见测试）。
    `None` = 段内包络全在阈值以下（没内容）。
    """
    seg = _seg(x, k0, k1)[:, 0]
    if seg.shape[0] < 8:
        raise ValueError("段太短，量不出包络：%d 采样" % seg.shape[0])
    env = np.abs(signal.hilbert(seg))
    peak = float(env.max())
    if peak <= 0.0:
        return None
    idx = np.nonzero(env >= peak * 10.0 ** (rel_db / 20.0))[0]
    if idx.size == 0:
        return None
    return float((idx[-1] - idx[0] + 1) / fs * 1000.0)


def sdr_db(est: np.ndarray, ref: np.ndarray) -> float | None:
    """**全局 SDR**（dB）：`10·log10(Σref² / Σ(est−ref)²)`。

    ⚠ 这是**工程口径**的全局式，不是 BSS_EVAL 的投影式（那套要算目标/干扰/伪影的分解，
    是另一件事；等真上修复算法、需要分项时再单独落，别混用一个名字）。
    `None` = 完全一致（误差为 0，没有 dB 可报）；也不是"很高分"，是"没法用对数说"。
    """
    a, b = _as2d(est), _as2d(ref)
    if a.shape != b.shape:
        raise ValueError("形状不一致：%r vs %r" % (a.shape, b.shape))
    e = float(np.sum((a - b) ** 2))
    if e <= 0.0:
        return None
    p = float(np.sum(b ** 2))
    if p <= 0.0:
        raise ValueError("参考信号全零：SDR 无定义")
    return 10.0 * math.log10(p / e)


def energy_ratio_db(est: np.ndarray, ref: np.ndarray) -> float | None:
    """**能量比**（dB）：`10·log10(Σest² / Σref²)` —— 幅度缩水/膨胀的守卫。

    PHAIN（IEEE 2024）指出纯稀疏推广会**把幅度压小**；只报 SDR 会把"变安静"算成改进，
    所以"修复"的验收必须**两条一起报**（`docs/theory.md §12.2/§12.3`）。
    `None` = 参考全零。
    """
    a, b = _as2d(est), _as2d(ref)
    if a.shape != b.shape:
        raise ValueError("形状不一致：%r vs %r" % (a.shape, b.shape))
    p = float(np.sum(b ** 2))
    if p <= 0.0:
        return None
    return 10.0 * math.log10(float(np.sum(a ** 2)) / p)
