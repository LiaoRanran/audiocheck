"""去咔哒算子（P3 第二个修复算子）：**两点预测残差 + 双尺度稳健尺子**检测，只改**检出样本**。

为什么是这两步（**本轮实测选的**，`packaging/declick_probe.py`）
--------------------------------------------------------------
素材 = `corpus.click_train`（**位置已知**的咔哒）+ 四种"背景"素材。先量后改：

| 残差口径 | 平滑滑音 | 谐波音 | 带限白噪 | 合法瞬态 |
|---|---|---|---|---|
| **邻域中值残差**（先试的） | α 扫到 12 仍 **15% 假阳性** | — | — | — |
| **两点预测残差**（落地） | ✅ | ✅ | ✅ | ✅ |

* **残差**：`r[n] = y[n] − (y[n−1] + y[n+1])/2`。对 `f` 远低于奈奎斯特的**平滑**内容，
  `r ≈ (2πf/fs)²·y`（极小）；咔哒样本上 `r ≈ 幅度` ⇒ **信噪比天生的**。
  ⚠ "邻域中值残差"被**实测否决**：正弦的局部中值本身就偏离样本值 ⇒ 残差被**波形**主导。
* **尺子**：`s = median(|r|)/0.6745`（全局 MAD ⇒ σ 的稳健估计）+ **局部 MAD**
  （见下节）——**两把尺子取并集**。
* **阈值 `α`**：默认 **5.0**。**这个默认值是"代价不对称"量出来的**：
  * α=4：TPR 更高（谐波音 1.00、瞬态 0.80），但**合法瞬态素材上有 ~12 个误报**（FPR 1.3e-4）；
  * α=5：**谐波音 / 白噪 / 合法瞬态的误报都是 0**，ΔSDR 仍**全为正**（+40.6 / +3.6 / +2.0 / +6.5）；
  * **误报的代价不对称**：FPR 到 ~1e-3 时修复会把白噪**修坏 −13 dB**（α=3 实测）——
    而 TPR 掉一点只让收益从 +14.2 变 +3.6 ⇒ **宁可漏、不可错** ⇒ 取 5.0。
* **修复**：检出样本按**两侧最近的非检出样本线性插值**（连续段整体处理）。
  实测比"邻域中值替换"更好或持平（平滑素材 +40.60 vs +23.85）。

### 双尺度尺子（2026-10-27 并入，`packaging/decrackle_tf_probe.py` 实测）
全段 MAD 有一类**结构性漏检**：合法高频内容把尺子抬高后，**稀疏**的中等幅度咔哒
（残差倍数 γ < α）一个都进不来。解法不是降 α（"代价不对称"：α=3 在白噪/瞬态上
285/286 个误报），而是加第二把尺子——**滑动局部 MAD**
（`s_loc[n] = median_filter(|r|, ±100)/0.6745`）：合法高频的残差是**持续**的
（邻域里到处都大 ⇒ 局部尺子同样高），脉冲的残差是**孤峰**（邻域里只有它大 ⇒
局部尺子低 ⇒ 峰过线）。**两臂各过宽度守卫后取并集**，阈值/守卫语义都不变
（**没有新旋钮**）。实测（谐波音 + 已知脉冲，对照全段 MAD 单尺子）：

| 素材 | 全局单尺 | **并集（现行）** |
|---|---|---|
| 稀疏 10/s（0.05–0.5） | +1.94 dB | **+2.65 dB**（precision 1.000） |
| 中密 100/s | +1.38 | **+1.44** |
| 密集 500/s | +1.09 | **+1.21** |
| 误报（谐波/白噪/瞬态） | 0/0/0 | **0/0/0** |

⇒ **每一格都不降、稀疏格大升、干净素材零误报** ⇒ 并集直接当默认，不设开关。
（更密的破音/小脉冲 0.02–0.2 仍检不出：γ = 0.23–2.5 是**两点残差**的物理极限，
换量具的调研见 `docs/editor_research.md §17`；纯局部尺子在密集下被咔哒自污染
（−0.2 dB），故只作并集的一臂、不单独当默认。）

⚠ 效力边界（**全部实测**，写进审计）：
* **素材决定收益**：平滑 +40 dB、谐波音 +3.6、合法瞬态 +6.5、**带限白噪只 +2.0**（噪声本身不可预测
  ⇒ 预测残差里没有"咔哒"可分离的成分）；
* **误报会"修坏"**：见上面的不对称；**合法瞬态**素材上默认阈值的检出数是 **0**（判据钉住）。
* **多采样咔哒只修两个边**（`packaging/declick_verify_probe.py` 量出来的**真实缺口**）：
  `r[n] = y[n] − (y[n−1]+y[n+1])/2` 在**平台内部** ≈ 0 ⇒ 宽度 3/5/8 的矩形咔哒实测只被覆盖
  133%/80%/50%，ΔSDR 只有 **+1.5 / +0.7 / +0.5 dB**（宽度 1–2 是 **+39 / +37 dB**）。
  ⇒ **本算子修的是"脉冲型"咔哒，不是"台阶/平台型"**；要修后者需要**阶跃检测器**（另一个量具），
  本轮试过"按幅度沿两端扩展"的候选，**实测否决**（残差尺度不是幅度尺度，扩展会一路吃穿信号）。

### 宽度守卫（第二级复核，`max_run`）—— 用"代价表"定的 4
文献侧的动机见 `docs/editor_research.md §11.3`（Adobe 的 "Second Level Verification"：
小号/萨克斯/女声/军鼓的**正常高峰**会被误判）。实测（平滑音 + 5 ms 起音，α=5）：

| 宽度上限 `L` | 1 采样咔哒 | 2 采样咔哒 | 合法起音的破坏（离原素材 SDR） | 被改采样 |
|---|---|---|---|---|
| 无守卫 | +39.14 | +37.37 | **30.55 dB** | 300 |
| 3 | +39.14 | **0.00（丢了）** | 65.29 dB | 16 |
| **4（默认）** | **+39.14** | **+37.37** | **56.51 dB** | 40 |
| 5 | +39.14 | +37.37 | 51.37 dB | 60 |
| 8 | +39.14 | +37.37 | 32.89 dB | 240 |

⇒ **4 是"不丢掉任何我们真能修的咔哒（1–2 采样）"的最小值**，且仍把起音破坏降了 **26 dB**。
⚠ 这条守卫**挡不住亚毫秒起音**（0.2 ms 起音的检出段宽度与咔哒重叠，实测宽度都是 2）
——如实记账，别指望它万能。
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import median_filter

#: 默认检测阈值（`|r| > α·s`）。**实测标定的"代价不对称"取值**（`packaging/declick_probe.py`）：
#: α=5 时**谐波音 / 带限白噪 / 合法瞬态三种素材的误报均为 0**，且四种素材 ΔSDR 全为正
#: （+40.6 / +3.6 / +2.0 / +6.5 dB）；而 α=4 会在合法瞬态上留下 ~12 个误报、α=3 直接**修坏**
#: （白噪 −13 dB）。⇒ **宁可漏、不可错**。
DEFAULT_THRESHOLD = 5.0

#: **宽度守卫**：只接受宽度 ≤ 这个值的检出段（"第二级复核"的第一版）。
#: 取值来自上面那张表：**4 是保住 1–2 采样咔哒的最小值**（3 会把 2 采样咔哒整段丢掉），
#: 同时把合法 5ms 起音的破坏从 30.55 dB 抬到 56.51 dB。
DEFAULT_MAX_RUN = 4

#: --- 台阶/平台咔哒的第二检测臂（2026-10-27 落地，`declick_step_probe.py` 实测）---
#: 长窗中值残差的窗长。**L=21 是实测甜点**：σ 与检出率的最优折中（4×w5 平台命中
#: 12 采样、三素材零误报）；L=41 的全局 σ 反涨（0.152→0.178）⇒ 5σ 阈值更高、
#: 平台检出反而全灭（阈值追踪 σ 的反相关盲区，实测记录）。0.44 ms @48k。
STEP_L = 21
#: 平台检测的**最小 run 宽**：中值臂的瞬态误报实测全在宽 1–2（噪声台阶/瞬态边），
#: 平台目标在 4–5 ⇒ min_run=3 清零误报；1–2 采样尖刺归两点残差臂管（领域天然分离）。
STEP_MIN_RUN = 3
#: 台阶臂的 run 宽度上界（平台最宽到 8；更宽属于"静音丢失"，不是咔哒）。
STEP_MAX_RUN = 8

#: **局部尺子的半窗宽**（采样）。窗宽扫描实测（`packaging/decrackle_tf_probe.py`）：
#: 小窗（50–200）在**稀疏**咔哒上最好、密集下被别的咔哒**自污染**（±100 窗在 500/s 时
#: 平均含 ~2 个脉冲）；大窗（≥1000）趋近全局尺子、增益消失。**100** 在并集里取到
#: "稀疏 +0.7 dB、密集不降"的平衡点（±100 @48k ≈ ±2 ms，远短于合法起音的 ms 级宽度，
#: 不会把起音当成"孤峰"放进来——起音仍由宽度守卫挡）。
LOCAL_HALF = 100


def local_scale(r: np.ndarray, half: int = LOCAL_HALF) -> np.ndarray:
    """滑动**局部 MAD**（逐点）：`s[n] = median(|r[n±half]|)/0.6745`（端点**镜像**填充）。

    向量化实现 = `scipy.ndimage.median_filter`（C 速度；显式循环版 96000 采样要跑秒级，
    滤波器毫秒级）。与全局 MAD 的关系见模块 docstring 的实测表：它是检测并集的
    **另一臂**，不是替代。
    ⚠ 端点必须用 **`mirror`**（镜像不重复边缘样本）：`residual` 把首尾置 0 作"不检测"
    哨兵，`nearest` 模式会把这 100 个哨兵零**复制进**端部窗口，把端部尺子压塌
    （实测：干净谐波音的 1–40 采样被误检，s_loc 0.0013 vs 全局 0.085）——
    `mirror` 下端部尺子 = **真实邻域**的统计，与探针的截断窗语义一致。
    """
    a = np.asarray(r)
    if a.size == 0:
        return np.zeros_like(a)
    return median_filter(np.abs(a), size=2 * int(half) + 1, mode="mirror") / 0.6745


def _as2d(x: np.ndarray) -> np.ndarray:
    a = np.asarray(x, dtype=np.float64)
    return a[:, None] if a.ndim == 1 else a


def residual(y: np.ndarray) -> np.ndarray:
    """两点预测残差 `r[n] = y[n] − (y[n−1] + y[n+1])/2`（首尾样本置 0 ⇒ 不检测）。

    ⚠ 逐**声道**调用（一维）——本模块的检测/尺子都在**单声道**上定义，
    立体声由 `declick()` 逐声道跑（每声道一套判据）。
    """
    a = np.asarray(y, dtype=np.float64)
    r = np.zeros_like(a)
    if a.shape[0] >= 3:
        r[1:-1] = a[1:-1] - 0.5 * (a[:-2] + a[2:])
    return r


def detect(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
           max_run: int = DEFAULT_MAX_RUN):
    """检出掩码（**已过宽度守卫**）。尺子 = **双尺度**：全段 MAD + 滑动局部 MAD 的**并集**

    （两者都是**全段**统计量——局部尺子也是对整段逐点算的 ⇒ 这也是本算子**不分块**的原因：
    分块会让每块各有一套尺子，接缝处判据不一致）。
    返回 `(mask, r, s)`；`s` 是全局尺子值（如实报出去）。
    """
    raw, r, s = _detect_raw(y, threshold)
    return apply_max_run(raw, max_run), r, s


def apply_max_run(mask: np.ndarray, max_run: int = DEFAULT_MAX_RUN) -> np.ndarray:
    """**宽度守卫（第二级复核）**：只保留宽度 ≤ `max_run` 的检出段。

    "合法瞬态"（起音）在残差域里是**毫秒级**的连续偏离，而咔哒是**采样级**的脉冲。
    实测（`packaging/declick_verify_probe.py`）：`max_run=4` 时 1–2 采样咔哒一个不丢，
    同时把合法 5ms 起音的破坏从 **30.55 dB 抬到 56.51 dB**（被改采样 300 → 40）。
    ⚠ 挡不住**亚毫秒**起音（0.2 ms 的检出段宽度也是 2，与咔哒重叠）——这是方法边界。
    """
    if max_run is None or max_run <= 0:
        return mask
    out = np.zeros_like(mask)
    for start, end in _runs(mask):
        if end - start <= max_run:
            out[start:end] = True
    return out


def _runs(mask: np.ndarray):
    """连续 True 段 `(start, end)`（end 开区间）。"""
    i = 0
    while i < mask.size:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.size and mask[j]:
            j += 1
        yield i, j
        i = j


def _detect_raw(y: np.ndarray, threshold: float):
    """**未过宽度守卫**的检出（双尺度并集 + 阈值判据）；`detect` 与统计共用它。

    两把尺子各自过阈值再**取并集**：全局 MAD（`s`）守密集、局部 MAD（`local_scale`）
    守稀疏——实测表见模块 docstring。返回 `(mask, r, s)`，`s` 仍是**全局**尺子
    （审计口径保持连续；`local_scale` 的值随检出掩码一起进审计）。
    """
    r = residual(y)
    s = float(np.median(np.abs(r)) / 0.6745)
    if s <= 0.0:
        # 数字静音（或整段恒值）：全局尺子为 0。局部尺子此时同样是 0（|r| 恒 0），
        # `|r| > 0` 为 False ⇒ 不会把静音里的任何东西检出来。
        return np.zeros_like(r, dtype=bool), r, 0.0
    raw = np.abs(r) > threshold * s
    s_loc = local_scale(r)
    if s_loc.size and float(s_loc.max()) > 0.0:
        raw |= np.abs(r) > threshold * s_loc
    return raw, r, s


def _detect_steps_L(y: np.ndarray, L: int, k: float, max_run: int,
                    min_run: int) -> np.ndarray:
    """单窗长窗中值检测（`detect_steps` 的构件）。"""
    from scipy.ndimage import median_filter
    res = y - median_filter(y, size=L, mode="mirror")
    sc = float(np.median(np.abs(res)) / 0.6745)
    if sc <= 0.0:
        return np.zeros_like(res, dtype=bool)
    raw = apply_max_run(np.abs(res) > k * sc, max_run)
    out = np.zeros_like(raw)
    i = 0
    n = raw.shape[0]
    while i < n:
        if not raw[i]:
            i += 1
            continue
        j = i
        while j < n and raw[j]:
            j += 1
        if j - i >= min_run:
            out[i:j] = True
        i = j
    return out


def detect_steps(y: np.ndarray, L: int = STEP_L, k: float = DEFAULT_THRESHOLD,
                 max_run: int = STEP_MAX_RUN,
                 min_run: int = STEP_MIN_RUN) -> np.ndarray:
    """**台阶/平台咔哒检测**（第二检测臂）：长窗中值残差（L=21）+ min_run=3（单声道）。

    `res = y − median_filter(y, L)`：平台在窗内占比低于半窗 ⇒ 被中值整体剔除 ⇒
    平台内部残差 ≈ 平台幅度（与单点咔哒同构）；两点残差在平台内部 ≈ 0（§17/§20 挂账
    的根因）。**min_run=3** 清零瞬态误报（实测：中值臂误报 run 全在宽 1–2、平台
    多个平台并存的检出 run 在 3–4）。**效力边界（实测，如实）**：多平台（≥2 个并存）
    w=3/5 全检出（级联 ΔSDR +24.0/+2.3）；**孤立单平台**的中值残差 run 只有尾缘
    1–2 宽（前缘被内容上升形态掩没）⇒ 被 min_run 清掉 ⇒ 检不出——边缘配对与
    平坦性签名两个候选都实测无分离力（`declick_step_probe.py`），这是本臂的
    已知边界，不是 bug。
    """
    return _detect_steps_L(y, L, k, max_run, min_run)


def _repair_runs(y: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """检出段用两侧最近的非检出样本**线性插值**（单样本就是两点中点）。"""
    out = y.copy()
    i = 0
    while i < mask.shape[0]:
        if not mask[i]:
            i += 1
            continue
        j = i
        while j < mask.shape[0] and mask[j]:
            j += 1
        a, b = i - 1, j
        left = out[a] if a >= 0 else None
        right = out[b] if b < out.shape[0] else None
        if left is not None and right is not None:
            out[i:j] = np.linspace(left, right, j - i + 2)[1:-1]
        elif left is not None:
            out[i:j] = left
        elif right is not None:
            out[i:j] = right
        i = j
    return out


def declick(y: np.ndarray, threshold: float = DEFAULT_THRESHOLD,
            max_run: int = DEFAULT_MAX_RUN, step_repair: bool = True):
    """对 `(samples, channels)` 去咔哒，返回 `(out, stats)`。**只改检出样本**（逐声道独立检测）。

    两检测臂**级联**（2026-10-27 起）：① 两点残差 + 双尺度 MAD（尖刺 1–2 采样）→
    ② 在①的**结果**上跑 `detect_steps`（长窗中值，平台 3–8 采样）。⚠ 为什么是级联
    不是掩膜并集：实测（`declick_step_probe.py`）并集会把①的端点样本拉进同一次插值、
    跨度变长，w=3 平台从 +22 塌回 +0.60——**级联**让平台检测看到的是已修净尖刺的
    信号，插值跨度各自正确。`step_repair=False` 回到单臂行为（v1 逐位一致）。

    ⚠ `threshold` 是**标定过的输入**（见 `DEFAULT_THRESHOLD`）：本模块**不替调用方猜**它，
    但会给一个实测过的默认值；改小它会把误报引进来（实测：FPR ~1e-3 时 ΔSDR 翻负）。
    ⚠ `max_run` 是**宽度守卫**（见 `DEFAULT_MAX_RUN` 与 `apply_max_run`）：`None` = 不设守卫
    （如实报出被拒的段数，让用户知道"有东西被挡在门外"）。
    """
    a = _as2d(y)
    if not threshold > 0.0:
        raise ValueError("检测阈值必须 > 0，得到 %r" % threshold)
    n, ch = a.shape
    out = a.copy()
    detected = np.zeros(n, dtype=bool)
    scales = []
    rejected = 0
    step_samples = 0
    step_runs = 0
    for c in range(ch):
        # **两臂都在原始信号上检测**（平台臂若看尖刺修复后的信号，端点已被插值、
        # 平台形态被破坏——实测 w=3 从 +24 塌回 +0.60）；**修复按平台先、尖刺后**
        # （平台 run 覆盖完整跨度先插值，尖刺端点在其上收尾）。
        raw, _r, s = _detect_raw(a[:, c], threshold)
        mask = apply_max_run(raw, max_run)
        scales.append(s)
        rejected += sum(1 for st, en in _runs(raw) if not mask[st:en].any())
        smask = detect_steps(a[:, c]) if step_repair else np.zeros(n, dtype=bool)
        # 平台先修（宽 run），再在结果上修尖刺
        if smask.any():
            out[:, c] = _repair_runs(out[:, c], smask)
            step_samples += int(smask.sum())
            step_runs += sum(1 for _st, _en in _runs(smask))
            detected |= smask
        if mask.any():
            out[:, c] = _repair_runs(out[:, c], mask)
            detected |= mask
    stats = {
        "threshold": float(threshold),
        "max_run": None if max_run is None else int(max_run),
        "detected_samples": int(detected.sum()),
        "fraction": int(detected.sum()) / (n * ch) if n * ch else 0.0,
        #: 各声道的残差尺子（**全段** MAD）。0 = 该声道数字静音（没有可检测的内容）。
        "residual_scale": [round(s, 8) for s in scales],
        "changed_samples": int((out != a).any(axis=1).sum()),
        #: **被宽度守卫挡下**的检出段数（"第二级复核"挡掉了多少）——如实记账，不许静默。
        "rejected_runs": int(rejected),
        #: 第二检测臂（台阶/平台）的记账：检出样本与段数。0 = 无平台检出或臂关闭。
        "step_samples": int(step_samples),
        "step_runs": int(step_runs),
        "step_repair": bool(step_repair),
    }
    return out, stats
