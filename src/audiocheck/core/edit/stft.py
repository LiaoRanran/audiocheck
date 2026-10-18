"""STFT / WOLA 内核 —— **频谱编辑的地基**（与 `docs/theory.md §12.1` 的推导一一对应）。

实现的是**加权重叠相加（WOLA）+ 逐样本归一化**：

* 分析：`S_m[k] = rfft( x[mH : mH+N] · w )`
* 合成：`y_m[n] = irfft(S_m)[n] · w`，再 OLA，最后**逐样本**除以 `Σ_m w²[n−mH]`

⚠ 为什么是"**逐样本除以真实窗能量和**"而不是"按常数缩放"：
`Σ_m w²[n−mH]` 只在**满足 COLA** 的 (N, H, w) 组合下才是常数；直接常数缩放会在
**边界**与**不满足 COLA 的组合**上留残差。逐样本归一化**恒精确**，只要分母 > 0。
⇒ 这也正是 `§12.1` 那条判据的落地形态：**改 hop/窗要先验这条**，不许"听起来没变"。
判据入口 = `window_sq_sum()`（`tests/test_edit_stft.py` 直接断言它恒 > 0）。

⚠ **两头各补 `N` 个零**（分析前，`pad=N`），否则首尾样本只被一个窗覆盖、`w[0]=0`（Hann）
⇒ 分母为 0。补零后每个中间样本都被 ≥ N/H 个窗覆盖，分母恒 > 0。
频域编辑（`render.py` 的 band 路径）**不用**这条默认补零：它自己在跨度外各取 `N` 个
**真实**样本做上下文，只输出中间那段——上下文必须是真的音频，否则窗口里的谱会被人工
零填污染。两条路径共用本模块的 `analyze/synthesize`。
⚠ `pad=0` 只对**自带上下文**的调用方成立（见 `synthesize` 的覆盖守卫）：帧网一旦被
截断，`Σ_m w²` 会在那一段塌到 ~1e-12，除下去把浮点噪声放大成波形——所以那里是**抛错**，
不是静默置 0。

⚠ 一维输入按单声道处理，`analyze/synthesize/roundtrip` 的输出统一是 `(samples, 1)`。

⚠ **不是逐位精确**：rfft→irfft 有浮点舍入，往返残差量级 ~1e-15 相对值。
渲染层把这个**实测**残差报进审计（`roundtrip_floor_dbfs`）——"频谱编辑永远不是位精确的"，
是必须让用户知道的事实（见 `docs/editor_research.md`）。
"""
from __future__ import annotations

import numpy as np

#: 重叠归一化的分母下限。分母 ≤ 该值 ⇒ 该样本**不重建**（留 0 并记账，不许静默）。
_WSS_MIN = 1e-12


def default_window(n_fft: int) -> np.ndarray:
    """默认分析/合成窗：**周期性 Hann**（`np.hanning(n+1)[:n]`，`w[0] = 0`）。

    ⚠ 用**周期** Hann 而不是 `np.hanning(n)`（对称版）：周期版才在 `hop | n_fft` 的重叠下
    给出**逐样本恒定**的窗平方和（内区恒为 `0.375·n_fft/hop`，Hann 的经典结论）；
    对称版两端会轻微起伏（逐样本归一化能吃掉起伏，但"窗平方和是不是常数"这条判据
    本身就成了模糊的，验收时不干净）。
    """
    return np.hanning(n_fft + 1)[:n_fft]


def check_params(n_fft: int, hop: int) -> None:
    """参数合法性（`docs/theory.md §12.1` 的 NOLA 条件）。

    `hop <= n_fft/2`：保证**每个中间样本至少被 2 个窗覆盖**。`hop > n_fft/2` 时相邻窗
    几乎不重叠，中间会出现"没有被任何窗覆盖"的样本 ⇒ 只能置 0（**静默丢样本**，正是
    本项目禁止的形态）⇒ 这里直接拒绝，而不是产出带洞的结果。
    """
    if n_fft < 2:
        raise ValueError("n_fft 太小：%r" % n_fft)
    if not 0 < hop <= n_fft // 2:
        raise ValueError("hop 必须落在 (0, n_fft//2]：得到 hop=%r, n_fft=%r" % (hop, n_fft))


def frame_count(n_samples: int, n_fft: int, hop: int, pad: int | None = None) -> int:
    """补零后，落在 hop 网格上的**合法窗起点**个数（与 `analyze` 实际产出的帧数一致）。"""
    pad = n_fft if pad is None else pad
    return int(-(-(n_samples + 2 * pad - n_fft + 1) // hop))


def analyze(x: np.ndarray, n_fft: int = 4096, hop: int | None = None,
            win: np.ndarray | None = None, pad: int | None = None):
    """WOLA 分析。`x` 为 `(samples, channels)`（一维会被当成单声道）。

    `pad`：分析前两端各补多少个零（默认 `n_fft`；频域编辑的分块路径传 `0`，
    因为它已经自带真实上下文）。

    返回 `(S, meta)`：`S` 形状 `(bins, frames, channels)`；`meta` 是**合成所需的全部参数**
    （`win` 与 `pad` 都在里面 ⇒ 换窗/换补零不会和不匹配的合成静默配错）。
    """
    x, ch = _as2d(x)
    hop = n_fft // 4 if hop is None else hop
    check_params(n_fft, hop)
    win = default_window(n_fft) if win is None else win
    pad = n_fft if pad is None else pad
    if pad:
        xp = np.concatenate([np.zeros((pad, ch)), x, np.zeros((pad, ch))], axis=0)
    else:
        xp = x
    frames = frame_count(x.shape[0], n_fft, hop, pad)
    if frames < 1:
        raise ValueError("信号太短，放不下一个窗：%d 采样 / n_fft=%d" % (x.shape[0], n_fft))
    sw = np.lib.stride_tricks.sliding_window_view(xp, n_fft, axis=0)   # (ns, ch, n)
    blk = sw[np.arange(frames) * hop] * win                           # (M, ch, n)
    S = np.fft.rfft(blk, axis=-1)                                     # (M, ch, bins)
    meta = {"n_fft": n_fft, "hop": hop, "win": win, "pad": pad,
            "length": x.shape[0], "channels": ch, "frames": frames}
    return np.transpose(S, (2, 0, 1)), meta


def synthesize(S: np.ndarray, meta: dict, *, keep: slice | None = None) -> np.ndarray:
    """WOLA 合成（逐样本归一化）。默认返回 `[pad, pad+length)`（长度与源一致）。

    `keep`：要取回的**输出区间**（在输入信号的坐标系里）——频域编辑的分块路径用
    `keep=slice(n_fft, n_fft + 段长)` 显式声明"只要中间那段"。

    ⚠ **覆盖守卫**（`pad=0` 的调用方必须看懂这条）：重叠归一化的分母在"帧网被截断"的
    位置会塌到很小的值，除下去会把浮点噪声**放大**成真实波形（实测：`pad=0` 直接喂整段
    信号时，末端 ~一个窗宽内的样本会被放大 1e10 倍以上）。这里**不静默**：只要
    `keep` 里有一个样本的分母 ≤ `_WSS_MIN`（或帧数根本铺不满这段长度），直接抛错——
    正确的用法是"自带 `n_fft` 个**真实**上下文，且只取中间那段"（见模块 docstring ⚠）。
    """
    n_fft, hop, win, pad = meta["n_fft"], meta["hop"], meta["win"], meta["pad"]
    length, ch = meta["length"], meta["channels"]
    seg = slice(pad, pad + length) if keep is None else keep
    n0 = 0 if seg.start is None else seg.start
    n1 = length + pad if seg.stop is None else seg.stop
    blk = np.fft.irfft(np.transpose(S, (1, 2, 0)), n=n_fft, axis=-1) * win
    ola, wss = _overlap_add(blk, win, hop)
    if not 0 <= n0 < n1 <= ola.shape[0] or not np.all(wss[n0:n1] > _WSS_MIN):
        raise ValueError(
            "帧数/补零组合铺不满 [%d, %d)：`pad=0` 时调用方必须自带 %d 个**真实**样本的"
            "上下文，并且只取中间那段（帧数=%d，可重建长度=%d）"
            % (n0, n1, n_fft, S.shape[1], ola.shape[0]))
    out = np.zeros((n1 - n0, ch))
    out[:] = ola[n0:n1] / wss[n0:n1, None]
    return out


def roundtrip(x: np.ndarray, n_fft: int = 4096, hop: int | None = None) -> np.ndarray:
    """分析→合成一遍（**验证用**：恒等往返的残差应当只有浮点舍入，~1e-15 相对）。"""
    S, meta = analyze(x, n_fft=n_fft, hop=hop)
    return synthesize(S, meta)


def window_sq_sum(n_fft: int, hop: int, frames: int) -> np.ndarray:
    """`Σ_m w²[n − m·hop]` —— 重叠归一化的分母（**COLA 判据的量化入口**）。

    判据：所有会被重建的样本上它必须**恒 > 0**（`_WSS_MIN`），且在内区应当与位置无关。
    这是"改 hop/窗必须先验"的那条；`docs/theory.md §12.1`。
    实现上直接复用 `_overlap_add`（零块），保证**判据与合成用同一份算法**，不搞两份会分叉的实现。
    """
    win = default_window(n_fft)
    return _overlap_add(np.zeros((frames, 1, n_fft)), win, hop)[1]


def bin_freqs(fs: int, n_fft: int) -> np.ndarray:
    """每个 bin 的**中心频率**（Hz），与 `analyze` 的 `bins` 轴一一对应。"""
    return np.fft.rfftfreq(n_fft, 1.0 / fs)


def frame_times(fs: int, n_fft: int, hop: int, frames: int,
                start_sample: int = 0) -> np.ndarray:
    """每帧的**中心时刻**（秒）。

    `start_sample` = 第 0 帧**第一个样本**在源信号坐标系里的下标（默认补零路径为 `-n_fft`，
    分块路径传块起点减 `n_fft`）⇒ 返回值可直接与源信号的秒数比较。
    """
    return (start_sample + np.arange(frames) * hop + n_fft / 2.0) / fs


def _overlap_add(blk: np.ndarray, win: np.ndarray, hop: int):
    """加权重叠相加 + 窗平方和。`blk` = "irfft 后已乘窗"的块，形状 `(M, ch, n)`。

    返回 `(ola (lp, ch), wss (lp,))`，`lp = (M−1)·hop + n`，两者按**同一条**索引铺开
    （`p = q·hop + j`）⇒ 逐样本相除恒成立。

    `hop | n_fft` 时走快路径：把帧拆成 `(R=n/hop, hop)` 再对 `R` 条对角线做向量加
    （`R` 次 numpy 调用，而不是 `M` 次 Python 逐帧循环——长素材上这是主项）。
    """
    M, ch, n = blk.shape
    R = n // hop
    if R * hop == n:
        A = blk.reshape(M, ch, R, hop).transpose(0, 2, 3, 1)      # (M, R, hop, ch)
        w2 = (win ** 2).reshape(R, hop)
        ola = np.zeros((M + R - 1, hop, ch), dtype=np.float64)
        wss = np.zeros((M + R - 1, hop), dtype=np.float64)
        for d in range(R):
            ola[d:M + d] += A[:, d]
            wss[d:M + d] += w2[d]
        return ola.reshape(-1, ch), wss.reshape(-1)
    ola = np.zeros(((M - 1) * hop + n, ch), dtype=np.float64)      # 通用路径
    wss = np.zeros((M - 1) * hop + n, dtype=np.float64)
    w2 = win ** 2
    for m in range(M):
        sl = slice(m * hop, m * hop + n)
        ola[sl] += blk[m].T
        wss[sl] += w2
    return ola, wss


def _as2d(x: np.ndarray) -> tuple[np.ndarray, int]:
    a = np.asarray(x, dtype=np.float64)
    if a.ndim == 1:
        a = a[:, None]
    return a, a.shape[1]
