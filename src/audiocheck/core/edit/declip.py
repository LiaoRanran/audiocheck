"""去削波算子：**带限重建 + 削波不等式投影**（POCS 交替投影），只改饱和样本。

为什么是这个方法（**本轮实测选出来的，不是文献照搬**）
----------------------------------------------------
`packaging/declip_probe.py` 在合成语料（真值已知）上把四种候选量了一遍，结论很硬：

| 方法 | 机制 | 实测（带限白噪，SDR，越高越好） |
|---|---|---|
| `poly1/poly3`（局部插值） | 每段用两侧锚点拟合多项式 | **变差**：light 43.5→21.3、severe 10.1→1.6 |
| `spline`（三次样条） | 同上，更光滑 | 同样**变差**（22.6 / 1.2） |
| `pg`（纯 Papoulis–Gerchberg） | 迭代"砍带外 + 恢复已知样本" | 真值带宽下很好（312/39.7/11.2），但**带宽取窄就反伤**（severe 0.5 倍带宽 ⇒ 0.47 dB） |
| **`pocs`（本项目落地）** | P-G + **削波真给过的不等式** `|x| ≥ c` 且同号 | **全面最优**（312.8/42.8/**14.1**），且**带宽取窄只退化到"不改"、不反伤** |

**为什么局部插值会变差**（这条反直觉，但可解释）：削波对饱和样本只留下 `±c` 这一个
观测，而它的误差**被"越出 `c` 多少"限住**（`docs/theory.md §12.2`）⇒ light 档的
`SDR_before` 本来就高到 43 dB；插值拿**不相关的锚点**去猜，估计方差远大于那点越出量
⇒ 越修越远。**"修"必须用全局结构（带限），不能用邻居瞎猜**。
⇒ 所以本模块**不提供**插值路径（它已被实测否决），只提供 POCS。

铁律落地
--------
* **只改饱和样本**：已知样本每一轮都被写回**原值** ⇒ 逐位不变（判据在
  `tests/test_edit_declip.py`）。
* **`B` 是输入不是猜**：带宽 `bandwidth` 必须由调用方给出（测量层/用户），本模块**不估**
  ——"能算的绝不猜"，且实测 B 取窄会退化（上表）。`bandwidth ≥ 奈奎斯特` ⇒ 无约束可利用
  ⇒ **早退为逐位不变**（有守卫，不是静默）。
* **整段饱和 ⇒ 拒绝**：一个已知样本都没有时重建纯属编造，直接抛错（宁可不做）。
"""
from __future__ import annotations

import numpy as np

#: 默认迭代次数。**实测据**（`packaging/declip_probe.py` 的收敛表，moderate 档 SDR）：
#: 4→26.4、8→29.2、16→33.2、32→36.3、64→38.0、128→39.5、400→42.8。
#: 交替投影收敛**慢**，取 128（约为 400 次的 93%）——是**速度/质量**的取舍，
#: 不是"够用了"的手感；调用方可显式调大（`DeclipOp.iterations`）。
DEFAULT_ITERATIONS = 128


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def _saturation(y: np.ndarray, ceiling: float) -> np.ndarray:
    """饱和掩码 `|y| >= c`（与 `corpus.saturation_mask` 同一口径）。"""
    return np.abs(y) >= ceiling


def _runs(mask_1d: np.ndarray) -> list:
    m = np.asarray(mask_1d, dtype=bool)
    if m.size == 0:
        return []
    pad = np.concatenate([[False], m, [False]])
    d = np.diff(pad.astype(np.int8))
    return [(int(a), int(b)) for a, b in zip(np.nonzero(d == 1)[0], np.nonzero(d == -1)[0])]


def reconstruct(y: np.ndarray, ceiling: float | None, fs: int, bandwidth: float,
                iterations: int = DEFAULT_ITERATIONS):
    """对 `(samples, channels)` 做去削波，返回 `(out, stats)`。**只改饱和样本**。

    三相交替投影（每轮）：
    ① **带限集**：`rfft → 砍掉 > bandwidth 的 bin → irfft`；
    ② **一致集**：把**非饱和**样本写回原值（⇒ 这些样本逐位不变）；
    ③ **削波集**：饱和样本钳到 `|x| ≥ ceiling` 且与观测同号（削波唯一给过的信息）。

    `ceiling`：
      * 给了就用它（**可测输入**，量它的入口是 `corpus.ceiling_of`）；
      * `None` ⇒ 取**本段观测峰值** `max|y|`（`stats["ceiling_source"]="auto_max"`）。
        ⚠ 这样取值是**必要的**：素材存成 float32/整型后再读回来，样本值是**量化过**的，
        拿"写入前的理论电平"去比会把饱和集合判空（实测过：`|y| >= 0.5225` 在 float32
        存回后一个都匹配不到）。取**它自己的峰值**则永远精确匹配"到达极值"的那些样本。
        代价：区间里**本来没有削波**时，会把最响的样本当成饱和去重建（审计会报
        `ceiling_source` 与 `peak_in`，用户看得见）——所以给准确值仍然更好。

    ⚠ `bandwidth >= fs/2` ⇒ ① 是恒等映射 ⇒ 没有任何可利用的约束 ⇒ **早退为原样返回**
    （`stats["noop"]=True`），不许假装修过。
    """
    a = _as2d(y)
    n, ch = a.shape
    if not 0.0 < bandwidth <= fs / 2.0:
        raise ValueError("带宽必须落在 (0, fs/2]：得到 %r（fs=%d）" % (bandwidth, fs))
    if not iterations >= 1:
        raise ValueError("迭代次数必须 ≥ 1，得到 %r" % iterations)
    peak_in = float(np.abs(a).max())
    if ceiling is None:
        ceiling, ceiling_source = peak_in, "auto_max"
    else:
        if not ceiling > 0.0:
            raise ValueError("削波电平必须 > 0，得到 %r" % ceiling)
        ceiling_source = "given"

    stats = {
        "ceiling": float(ceiling),
        "ceiling_source": ceiling_source,
        "peak_in": peak_in,
        "saturated_samples": 0,
        "total_samples": int(a.size),
        "fraction": 0.0,
        "runs": 0,
        "longest_run": 0,
        "iterations": int(iterations),
        "band_limited": False,
        "noop": False,
        "replaced_samples": 0,
    }

    if peak_in <= 0.0:                               # 全零 / 纯静音：没有削波可言
        stats["noop"] = True
        return a.copy(), stats

    sat = _saturation(a, ceiling)
    known = ~sat
    runs = [r for c in range(ch) for r in _runs(sat[:, c])]
    stats["saturated_samples"] = int(sat.sum())
    stats["fraction"] = int(sat.sum()) / a.size if a.size else 0.0
    stats["runs"] = len(runs)
    stats["longest_run"] = max([e - s for s, e in runs], default=0)

    if not sat.any():
        return a.copy(), stats                       # 没有削波 ⇒ 逐位不变
    if not known.any():
        raise ValueError("整段都在饱和：一个已知样本都没有，重建无从谈起（宁可不做）")

    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = f <= bandwidth
    if bool(keep.all()):                             # 无带限约束可利用 ⇒ 早退
        stats["noop"] = True
        return a.copy(), stats
    stats["band_limited"] = True

    out = a.copy()
    sign = np.where(a >= 0.0, 1.0, -1.0)
    for c in range(ch):
        x = a[:, c].copy()
        k = known[:, c]
        s = sat[:, c]
        sg = sign[:, c]
        for _ in range(int(iterations)):
            X = np.fft.rfft(x)
            X[~keep] = 0.0
            x = np.fft.irfft(X, n=n)
            x[k] = a[k, c]                           # 已知样本逐位写回
            x[s] = sg[s] * np.maximum(np.abs(x[s]), ceiling)   # 削波不等式投影
        out[:, c] = x
    stats["replaced_samples"] = int((out != a).any(axis=1).sum())
    return out, stats
