"""频率刻度的解析变换：线性 / 对数 / Bark / Mel / ERB。

## 为什么要自己写

项目铁律是**不增运行期依赖**，而这五个变换都是**教科书闭式公式**——
一行一个，出处逐条写在 docstring 里。装一个库只为了 `log` 与两个除加，
是拿 280 MB 的打包体积换三行代码。

## 为什么频谱图需要"换刻度"

对数频轴在低频挤、高频疏：20–200 Hz 只占轴长的 ~1/3，而人耳对**低频分辨率**
恰恰最敏感（临界带宽在 100 Hz 处约 100 Hz 宽，在 10 kHz 处约 2.5 kHz 宽）。
Bark / Mel / ERB 都是**按听觉临界带宽**铺开的刻度——同样一根轴，
低频拿到更多像素。本项目只把这三条**临界带线**画出来（"线"形态），
这里补的是**把轴本身换掉**（"刻度"形态）。

## 共同不变量（`tests/test_scales.py` 钉住）

1. 在 [20, 20000] Hz 上**严格单调递增**；
2. `forward`/`inverse` 互逆（相对误差 < 1e-9）；
3. 已知锚点：`bark(1000) ≈ 8.51`、`mel(1000) = 1000`（HTK 定义）、
   `erb(1000) ≈ 15.6`（ERB-rate，单位 ERB）。

## 出处

* **Bark**：Traunmüller, H. (1990). *Analytical expressions for the tonotopic
  sensory scale*. J. Acoust. Soc. Am. 88(1), 97–100. 式 `z = 26.81f/(1960+f) − 0.53`。
  （与 `headphone-lab` 的 `perceptual.py` 同一套；两仓库各自独立实现，口径必须一致。）
* **Mel**：O'Shaughnessy, D. (1987). *Speech Communication: Human and Machine*，
  HTK 采用的 `m = 2595·log10(1 + f/700)`。
* **ERB-rate**：Glasberg, B. R. & Moore, B. C. J. (1990). *Derivation of auditory
  filter shapes from notched-noise data*. Hearing Research 47, 103–138.
  `e = 21.4·log10(1 + 4.37·f/1000)`。

⚠ **本模块只做坐标变换**，不改任何判定阈值——判定仍然按 Hz 算。
"""
from __future__ import annotations

import numpy as np

#: 显示轴的下限（与各画布一致：20 Hz 以下没有内容，也不做判定）
F_MIN = 20.0


# ---------------------------------------------------------------- 各刻度（正向）
def linear(f):
    """线性（Hz）。"""
    return np.asarray(f, dtype=float)


def log10_scale(f):
    """常用对数（10 为底）。轴下限取 20 Hz，故不会碰到 log(0)。"""
    return np.log10(np.maximum(np.asarray(f, dtype=float), 1e-9))


def bark(f):
    """Bark（Traunmüller 1990）。单位 Bark，1 kHz ≈ 8.51。"""
    f = np.asarray(f, dtype=float)
    return 26.81 * f / (1960.0 + f) - 0.53


def mel(f):
    """Mel（HTK / O'Shaughnessy 1987）。1 kHz = 1000 Mel（定义使然）。"""
    f = np.asarray(f, dtype=float)
    return 2595.0 * np.log10(1.0 + f / 700.0)


def erb_rate(f):
    """ERB-rate（Glasberg & Moore 1990）。单位 ERB，1 kHz ≈ 15.6。"""
    f = np.asarray(f, dtype=float)
    return 21.4 * np.log10(1.0 + 4.37 * f / 1000.0)


# ---------------------------------------------------------------- 各刻度（逆向）
def _linear_inv(x):
    return np.asarray(x, dtype=float)


def _log10_inv(x):
    return 10.0 ** np.asarray(x, dtype=float)


def _bark_inv(z):
    z = np.asarray(z, dtype=float)
    # z = 26.81f/(1960+f) − 0.53  ⇒  f = 1960(z+0.53)/(26.81−(z+0.53))
    u = z + 0.53
    return 1960.0 * u / (26.81 - u)


def _mel_inv(m):
    m = np.asarray(m, dtype=float)
    return 700.0 * (10.0 ** (m / 2595.0) - 1.0)


def _erb_inv(e):
    e = np.asarray(e, dtype=float)
    return (10.0 ** (e / 21.4) - 1.0) * 1000.0 / 4.37


#: 刻度注册表：名字 → (正向, 逆向, 轴标签, 一句话说明)。
#: **加刻度只改这一张表**（画布与测试都从这里取，不许各写一份）。
SCALES: dict[str, tuple] = {
    "log": (log10_scale, _log10_inv, "对数", "默认：对数频轴，高频疏、低频挤"),
    "linear": (linear, _linear_inv, "线性", "线性 Hz：低频几乎看不见（对照用）"),
    "bark": (bark, _bark_inv, "Bark", "Traunmüller 1990：临界带宽刻度"),
    "mel": (mel, _mel_inv, "Mel", "HTK Mel：语音识别的常用刻度"),
    "erb": (erb_rate, _erb_inv, "ERB", "Glasberg-Moore 1990：ERB-rate"),
}

#: Bark 逆变换的**公开**别名：画布的临界带边界线（`charts.bark_to_hz`）要用它，
#: 而那张表的键是私有名。公开出去是为了让"一处数学"能被外部引用而不是抄一遍。
bark_inv = _bark_inv

DEFAULT = "log"


def get(name: str | None):
    """取一个刻度的 (正向, 逆向, 标签, 说明)。未知名字**回落默认并说明**，
    不抛异常——画布不该因为一个拼错的名字整块白掉。"""
    return SCALES.get(name or DEFAULT, SCALES[DEFAULT])


def is_known(name: str | None) -> bool:
    return (name or DEFAULT) in SCALES


def display_grid(f_max: float, rows: int, scale: str = DEFAULT) -> tuple[np.ndarray, float, float]:
    """给画布用的**均匀显示网格**：在该刻度上等距取 `rows` 个点。

    返回 `(目标频率 Hz 数组, 轴下限刻度值, 轴上限刻度值)`。
    中间那串频率是**非均匀的**（在 Hz 上），`_resample_rows` 本来就按
    searchsorted 取带内峰值，支持任意递增的 fl —— 所以这里不用改重采样。
    """
    fwd, inv, _label, _note = get(scale)
    lo, hi = float(fwd(F_MIN)), float(fwd(f_max))
    x = np.linspace(lo, hi, int(rows))
    return np.asarray(inv(x), dtype=float), lo, hi
