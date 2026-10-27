"""编辑操作模型（**非破坏**）：项目只记录"对源做过什么"，源文件永不改。

三条设计约束（写在最前，免得后来者加花活）
------------------------------------------
1. **非破坏**：`Project` = 源路径 + 操作栈；导出是 `(源, 操作栈)` 的**纯函数**，
   同一输入必得同一输出。源文件在渲染过程中只读。
2. **可审计**：每个操作都能 `to_dict()`（机器可读）与 `describe()`（人可读），
   渲染再返回一份**审计记录**——这是本项目相对闭源工具的那条差别（见
   `docs/editor_roadmap.md §2`："可审计的修复"）。
3. **只做有判据的**：目前六个操作——时间选区增益 `GainOp`、时频选区增益
   `BandGainOp`（判据在 `tests/test_edit_render.py` / `tests/test_edit_stft.py`）、
   去削波 `DeclipOp`（判据在 `tests/test_edit_declip.py`）、去咔哒 `DeclickOp`
   （判据在 `tests/test_edit_declick.py`）、去嗡 `DehumOp`（判据在 `tests/test_edit_dehum.py`）、
   去噪 `DenoiseOp`（判据在 `tests/test_edit_denoise.py`）。
   加任何新算法前，先按 `docs/editor_roadmap.md §5` 建判据（合成语料 + 客观指标 +
   效力边界），否则不加。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .declick import DEFAULT_MAX_RUN, DEFAULT_THRESHOLD
from .declip import DEFAULT_ITERATIONS
from .dehum import DEFAULT_HARMONICS, DEFAULT_Q
from .denoise import DEFAULT_N_FFT, DEFAULT_REDUCTION_DB


@dataclass(frozen=True)
class GainOp:
    """对时间选区 `[t0, t1)` 施加 `gain_db`（dB）。**不可变**。

    选区口径（与测试逐位对齐，别改）：样本 `k` **当且仅当** `t0 <= k/fs < t1` 时受影响。
    即 `t1` 处的样本**不**受影响（半开区间），故 `t1 - t0` 秒恰好影响 `(t1-t0)*fs` 个样本。

    `gain_db == 0.0` 视为 **no-op**：渲染时**整条跳过**，保证"没动过的地方逐位不变"。
    """

    t0: float
    t1: float
    gain_db: float

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))

    @property
    def is_noop(self) -> bool:
        return self.gain_db == 0.0

    def to_dict(self) -> dict:
        return {"kind": "gain", "t0": self.t0, "t1": self.t1, "gain_db": self.gain_db}

    def describe(self) -> str:
        return "时间选区 %.3f–%.3f s：%+.1f dB" % (self.t0, self.t1, self.gain_db)


@dataclass(frozen=True)
class BandGainOp:
    """对**时频选区**施加 `gain_db`：谱点 `(帧, bin)` 被改 ⟺

    * 该帧的**中心时刻** ∈ `[t0, t1)`，且
    * 该 bin 的**中心频率** ∈ `[f0, f1)`。

    两条都是**半开**、都用"中心"（与 `stft.frame_times` / `stft.bin_freqs` 的口径逐位一致）。
    帧中心时刻 = `(帧起点 + n_fft/2) / fs`；bin 中心频率 = `k·fs/n_fft`。

    ⚠ **时频编辑不是位精确的**（与时间域增益的根本差别，必须让用户看见）：
      * 一个谱点被改，其影响会摊到**整窗宽度**上 ⇒ **受影响的采样 = 选区 ± n_fft/2**；
      * 于是渲染时写出的范围要外扩一个窗宽：**选区 ± n_fft 之外逐位不变**，外扩的那部分里
        未受影响的采样只有 ~1e-15 的 STFT 往返残差（渲染层把**实测**值报进审计
        `roundtrip_floor_dbfs`）——不许说"无损"。

    `gain_db == 0.0` 视为 **no-op**（渲染时整条跳过 ⇒ 全轨逐位不变）。
    """

    t0: float
    t1: float
    f0: float
    f1: float
    gain_db: float

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("时间选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))
        if not (self.f1 > self.f0 >= 0.0):
            raise ValueError("频率选区非法：需要 0 <= f0 < f1，得到 [%r, %r)" % (self.f0, self.f1))

    @property
    def is_noop(self) -> bool:
        return self.gain_db == 0.0

    def to_dict(self) -> dict:
        return {"kind": "band_gain", "t0": self.t0, "t1": self.t1,
                "f0": self.f0, "f1": self.f1, "gain_db": self.gain_db}

    def describe(self) -> str:
        return "时频选区 %.3f–%.3f s × %.1f–%.1f Hz：%+.1f dB" % (
            self.t0, self.t1, self.f0, self.f1, self.gain_db)


@dataclass(frozen=True)
class PolyGainOp:
    """**多边形时频选区增益**（P2"矩形→套索"的内核件，2026-10-27）：对**顶点围成的
    时频多边形内部**施加 `gain_db`。

    * `points`：`[(t, f), …]` 顶点序列（**隐式闭合**：末点连回首点）。≥ 3 个顶点、
      t/f ≥ 0、有限值。**记录即选区**——顶点表就是可审计的选区本身。
    * 属内判据：**射线法**（ray casting）逐谱点（帧中心时刻, bin 中心频率）判定，
      与 `stft.frame_times` / `stft.bin_freqs` 的口径逐位一致。**边界上的谱点是否属内
      是实现定义**（半开区间在多边形上没有自然对应）——用偏离网格的坐标做选区即可避开，
      判据 `test_rect_polygon_matches_band_gain_bitwise` 用非网格坐标钉死"矩形多边形
      ≡ `BandGainOp` 逐位一致"。
    * **自相交**多边形（如蝴蝶结）不拒绝：射线法按**奇偶规则**给出确定性解释
      （两瓣属内、腰部不属内；判据 `test_self_intersecting_polygon_is_even_odd_deterministic`）。
      顶点表在审计里可见 ⇒ 用户意图可核对。
    * 和 `BandGainOp` 同一条 STFT/WOLA 渲染路径、同一个规范顺序相位（4）：**外扩窗宽、
      往返残差、时频点记账全部同款**（`render._render_band`）。

    `gain_db == 0.0` 视为 **no-op**（渲染时整条跳过 ⇒ 全轨逐位不变）。
    ⚠ 时频编辑不是位精确的——外扩/往返残差与 `BandGainOp` 相同，审计同款报
    `roundtrip_floor_dbfs`。
    """

    points: tuple
    gain_db: float

    def __post_init__(self) -> None:
        pts = tuple(self.points)
        if not isinstance(pts, tuple) or len(pts) < 3:
            raise ValueError("多边形至少 3 个顶点，得到 %r" % (self.points,))
        clean = []
        for pt in pts:
            try:
                t, f = float(pt[0]), float(pt[1])
            except (TypeError, ValueError, IndexError):
                raise ValueError("顶点必须是 (t, f) 数对，得到 %r" % (pt,)) from None
            if not (np_finite(t) and np_finite(f)) or t < 0.0 or f < 0.0:
                raise ValueError("顶点坐标必须有限且 ≥ 0，得到 (%r, %r)" % (pt[0], pt[1]))
            clean.append((t, f))
        object.__setattr__(self, "points", tuple(clean))

    @property
    def is_noop(self) -> bool:
        return self.gain_db == 0.0

    def to_dict(self) -> dict:
        return {"kind": "poly_gain",
                "points": [[t, f] for t, f in self.points],
                "gain_db": self.gain_db}

    def describe(self) -> str:
        ts = [t for t, _ in self.points]
        return "多边形选区（%d 顶点，t %.3f–%.3f s）：%+.1f dB" % (
            len(self.points), min(ts), max(ts), self.gain_db)


@dataclass(frozen=True)
class DeclipOp:
    """对时间选区 `[t0, t1)` 做**去削波**：只改饱和样本，其余逐位不变。

    两个输入都是**可测的**（`docs/theory.md §12.2`：削波对饱和样本只给"越过了 ±c"，
    位置/个数与 `c` 都可测 ⇒ 不是猜）：

    * `bandwidth` —— 内容**带宽上界** `B`（Hz）**必须给**。去削波靠"信号带限"这条先验
      （`declip.py` 实测：否认这条先验的方法——局部插值——会把 SDR **修坏**）。
      `B ≥ 奈奎斯特` 时没有约束可利用 ⇒ 渲染层**直接跳过**（逐位不变），如实记 `noop`。
    * `ceiling` —— 削波电平 `c`（线性幅度）。给了就用它（量它的入口 = `corpus.ceiling_of`）；
      `None` ⇒ 取**本段观测峰值** `max|y|`（审计记 `ceiling_source="auto_max"`）。
      ⚠ 自动取值不是偷懒：素材存成 float32/整型后样本值是**量化过**的，拿"写入前的理论
      电平"去比会把饱和集合判空（已在 `declip.py` 的 docstring 里实测记录）。代价是
      **区间本来没有削波**时会把最响的样本当成饱和去重建——给准确值更好。

    算法与实测依据见 `declip.py`（POCS 交替投影）。选区口径与 `GainOp` **逐位一致**
    （半开 `[t0, t1)`；样本 `k` 受影响 ⟺ `t0 ≤ k/fs < t1`）。

    ⚠ **与其它域算子混用**：按 `render.PHASE` 的**规范顺序**链式渲染（去削波在电平类之前；
    实测依据 `packaging/op_order_probe.py`）。**多个 `DeclipOp` 的选区互相重叠**仍**拒绝**
    （同域重叠的顺序语义未立判据，宁可不做）。
    """

    t0: float
    t1: float
    bandwidth: float
    ceiling: float | None = None
    iterations: int = DEFAULT_ITERATIONS
    #: **来源记账**（provenance，**不影响任何算式**）：这个 `B` 是谁给的。
    #: `"given"` = 调用方直接给；`"auto_cutoff"` / `"auto_nyquist"` = 由测量层的
    #: `spectrum.content_bandwidth` 读出（砖墙截止 / 量不到 ⇒ 奈奎斯特 ⇒ 逐位不变）。
    #: 进审计记录 —— "可审计的修复"要求写清**依据哪条测量**。
    bandwidth_source: str = "given"

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("时间选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))
        if self.ceiling is not None and not self.ceiling > 0.0:
            raise ValueError("削波电平必须 > 0 或为 None（自动取观测峰值），得到 %r" % self.ceiling)
        if not self.bandwidth > 0.0:
            raise ValueError("带宽必须 > 0，得到 %r" % self.bandwidth)
        if not self.iterations >= 1:
            raise ValueError("迭代次数必须 >= 1，得到 %r" % self.iterations)

    @property
    def is_noop(self) -> bool:
        """去削波**没有"设定值 0 ⇒ no-op"的形态**：改不改要等渲染时看有没有饱和样本、
        以及 `bandwidth` 是否 < 奈奎斯特 ⇒ 这里**恒返回 False**（不假装知道答案）。"""
        return False

    def to_dict(self) -> dict:
        return {"kind": "declip", "t0": self.t0, "t1": self.t1, "ceiling": self.ceiling,
                "bandwidth": self.bandwidth, "bandwidth_source": self.bandwidth_source,
                "iterations": self.iterations}

    def describe(self) -> str:
        c = "自动（观测峰值）" if self.ceiling is None else "%.4f" % self.ceiling
        return "去削波 %.3f–%.3f s：削波电平 %s ｜ 带宽 %.0f Hz（%s）｜ 迭代 %d" % (
            self.t0, self.t1, c, self.bandwidth, self.bandwidth_source, self.iterations)


@dataclass(frozen=True)
class DeclickOp:
    """对时间选区 `[t0, t1)` 做**去咔哒**：检出样本按两侧邻居线性插值，**只改检出样本**。

    * `threshold` —— 检测阈值 `α`（`|r| > α·s`，`r` = 两点预测残差、`s` = 全段 MAD 尺子）。
      **实测标定**过（`packaging/declick_probe.py`）：默认 `5.0` 时**谐波音 / 带限白噪 /
      合法瞬态三种素材的误报均为 0**，且四种素材 ΔSDR 全为正；**往下调会掉进"修坏"**
      （α=3 白噪 −13 dB）——**误报的代价是不对称的**。
    * 选区口径与 `GainOp` **逐位一致**（半开 `[t0, t1)`；样本 `k` 受影响 ⟺ `t0 ≤ k/fs < t1`）。

    ⚠ **与其它域算子混用**：按 `render.PHASE` 的**规范顺序**链式渲染（咔哒在去削波之前——
    带限投影会把它抹宽）。**多个 `DeclickOp` 的选区互相重叠**仍**拒绝**（同上）。

    效力边界（**实测**，见 `declick.py` 的 docstring）：**素材决定收益**——
    平滑 +40 dB、谐波音 +14、合法瞬态 +6.5、**带限白噪只 +1.0**（噪声本身不可预测）；
    而**误报会修坏**（FPR ~1e-3 ⇒ ΔSDR 翻负）⇒ 阈值取保守侧。
    """

    t0: float
    t1: float
    threshold: float = DEFAULT_THRESHOLD
    #: **宽度守卫**（第二级复核）：只接受宽度 ≤ `max_run` 的检出段。`None` = 不设守卫。
    #: 实测依据见 `declick.DEFAULT_MAX_RUN` 的代价表（4 = 保住 1–2 采样咔哒的最小值）。
    max_run: int | None = DEFAULT_MAX_RUN
    #: **台阶/平台咔哒第二检测臂**（2026-10-27 落地，`declick.detect_steps`，级联）：
    #: 长窗中值残差（L=41 + min_run 3）——两点残差修不了平台（内部残差 ≈ 0，§17/§20）。
    #: 实测 w=3/5/8 平台 ΔSDR +22.1/+3.4/+1.8、干净素材 0 误报 ⇒ 默认**开**；
    #: `False` 回到单臂行为（v2 逐位一致）。
    step_repair: bool = True

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("时间选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))
        if not self.threshold > 0.0:
            raise ValueError("检测阈值必须 > 0，得到 %r" % self.threshold)
        if self.max_run is not None and self.max_run < 1:
            raise ValueError("宽度守卫必须是 >= 1 的整数或 None，得到 %r" % self.max_run)

    @property
    def is_noop(self) -> bool:
        """去咔哒**改不改要等渲染时看检出结果** ⇒ 这里恒返回 `False`（不假装知道）。"""
        return False

    def to_dict(self) -> dict:
        return {"kind": "declick", "t0": self.t0, "t1": self.t1,
                "threshold": self.threshold, "max_run": self.max_run,
                "step_repair": self.step_repair}

    def describe(self) -> str:
        return "去咔哒 %.3f–%.3f s：检测阈值 %.2f×MAD，宽度守卫 %s%s" % (
            self.t0, self.t1, self.threshold,
            "关" if self.max_run is None else "≤%d 采样" % self.max_run,
            "" if self.step_repair else "｜台阶臂 关")


@dataclass(frozen=True)
class DehumOp:
    """对时间选区 `[t0, t1)` 做**去嗡**：零相位陷波级联（`dehum.py`）。

    * `f0` —— 嗡的基频（50 / 60 Hz）。**可测输入**（频谱上的嗡线），不在这里猜。
    * `q` —— 陷波 Q，默认 **10**（实测：±1 Hz 漂移下仍压 ≥17 dB 的最大 Q；
      Q=40 在 1 Hz 漂移下只剩 2.9 dB ⇒ 等于没去）。
    * `harmonics` —— 要清的谐波次数（默认 1/2/3）；`k·f0 ≥ 奈奎斯特` 的**跳过并记账**。

    ⚠ **与去削波/去咔哒的根本差别**：陷波是 **LTI** ⇒ **整段选区都被改动**，
      而不是"只改饱和/检出样本"。无嗡素材上它同样会改（实测干净带限白噪 Q=10：
      SDR 27.6 dB、能量 −0.017 dB）⇒ 收益由**嗡的能量占比**决定，审计报实测压制量。

    拒绝规则：**多个去嗡选区重叠**（同域重叠未立判据）；**与其它域算子混用**则按
    `render.PHASE` 的规范顺序链式渲染（去嗡在电平类之前）。
    """

    t0: float
    t1: float
    f0: float
    q: float = DEFAULT_Q
    harmonics: tuple = DEFAULT_HARMONICS
    #: **来源记账**（不影响算式）：`"given"` = 调用方直接给；`"auto"` = 由测量层
    #: `analyzers.hum.detect_hum` 从**稳态证据**读出来（CLI 的 `--hum-auto`）。
    f0_source: str = "given"

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("时间选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))
        if not self.f0 > 0.0:
            raise ValueError("基频必须 > 0，得到 %r" % self.f0)
        if not self.q > 0.0:
            raise ValueError("Q 必须 > 0，得到 %r" % self.q)
        if self.f0_source not in ("given", "auto"):
            raise ValueError("f0 来源只能是 given / auto，得到 %r" % self.f0_source)
        hs = tuple(self.harmonics)
        if not hs or any(int(k) < 1 or int(k) != k for k in hs):
            raise ValueError("谐波次数必须是 >= 1 的整数序列，得到 %r" % (self.harmonics,))
        object.__setattr__(self, "harmonics", hs)

    @property
    def is_noop(self) -> bool:
        """去嗡改不改要看渲染时的奈奎斯特（全部谐波越界才 no-op）⇒ 这里**恒 False**。"""
        return False

    def to_dict(self) -> dict:
        return {"kind": "dehum", "t0": self.t0, "t1": self.t1, "f0": self.f0,
                "f0_source": self.f0_source,
                "q": self.q, "harmonics": list(self.harmonics)}

    def describe(self) -> str:
        return "去嗡 %.3f–%.3f s：基频 %.2f Hz（%s ｜ Q=%.0f ｜ 谐波 %s）" % (
            self.t0, self.t1, self.f0, self.f0_source, self.q,
            "/".join(str(int(k)) for k in self.harmonics))


@dataclass(frozen=True)
class DenoiseOp:
    """对时间选区 `[t0, t1)` 做**去噪**（Wiener + 判决引导先验 SNR，`denoise.py`）。

    ⚠ **必须给噪声段** `[noise_t0, noise_t1)`：那段素材必须**只有噪声**，算子从它学谱。
    这不是"偷懒少一个参数"——`packaging/denoise_probe.py` 实测：不给噪声段、按"最低
    10% 帧"盲估，在 **+10 dB SNR 的持续音**上 ΔSDR **−8.7 dB**（把内容当噪声减掉）。
    所以盲模式**不进内核**，宁可不做。

    * `reduction_db` —— **压制上限**（dB）：增益不低于 `10^(−reduction_db/20)`。
      20 dB 的 musical-noise 代理只涨 +0.01 dB；30 dB 涨 +0.23 dB 却只多 0.64 dB ΔSDR
      ⇒ 默认取保守侧。**瞬态会被削软**：20 dB 时单采样脉冲峰值实测 −13.6 dB；
      降到 10 dB 则 −7.9 dB。这是本领对瞬态的**如实代价**，不是 bug。
    * `n_fft` / `hop` —— STFT 参数（默认 4096 / `n_fft/4`；窗长扫描见 `denoise.py`）。

    ⚠ 去噪**改整段选区**（增益逐帧逐 bin ⇒ 每个样本都可能变），不是"只改噪声样本"；
    选区之外逐位不变（渲染层保证）。**非稳态噪声**没做：`N` 是噪声段的平均谱。
    """

    t0: float
    t1: float
    noise_t0: float
    noise_t1: float
    reduction_db: float = DEFAULT_REDUCTION_DB
    n_fft: int = DEFAULT_N_FFT
    hop: int | None = None
    #: **逐帧噪声底跟踪**（2026-10-27 落地，`denoise._floor_track`）：噪声床在选区里
    #: 台阶变化时逐帧跟随（bin 中位数比值，内容 bin 被中位数天然剔除）。实测：台阶床
    #: 低参考总 ΔSDR +2.74 → **+9.40**；稳态代价 ≤0.66 dB（`denoise_nonstat_probe` /
    #: `docs/editor_research.md §19`）⇒ 默认**开**；`False` 回到 v1 的固定谱行为。
    track_floor: bool = True

    def __post_init__(self) -> None:
        if not (self.t1 > self.t0 >= 0.0):
            raise ValueError("时间选区非法：需要 0 <= t0 < t1，得到 [%r, %r)" % (self.t0, self.t1))
        if not (self.noise_t1 > self.noise_t0 >= 0.0):
            raise ValueError("噪声段非法：需要 0 <= noise_t0 < noise_t1，得到 [%r, %r)"
                             % (self.noise_t0, self.noise_t1))
        if not self.reduction_db > 0.0:
            raise ValueError("压制上限必须 > 0 dB，得到 %r" % self.reduction_db)
        if not self.n_fft >= 2:
            raise ValueError("n_fft 必须 >= 2，得到 %r" % self.n_fft)
        if self.hop is not None and self.hop < 1:
            raise ValueError("hop 必须 >= 1 或为 None（默认 n_fft/4），得到 %r" % self.hop)

    @property
    def is_noop(self) -> bool:
        """去噪至少改 STFT 往返残差 ⇒ **恒 False**（不假装"没动"）。"""
        return False

    def to_dict(self) -> dict:
        return {"kind": "denoise", "t0": self.t0, "t1": self.t1,
                "noise_t0": self.noise_t0, "noise_t1": self.noise_t1,
                "reduction_db": self.reduction_db, "n_fft": self.n_fft, "hop": self.hop,
                "track_floor": self.track_floor}

    def describe(self) -> str:
        return ("去噪 %.3f–%.3f s：噪声段 %.3f–%.3f s ｜ 压制上限 %.0f dB ｜ "
                "STFT %d/%s ｜ 底噪跟踪%s"
                % (self.t0, self.t1, self.noise_t0, self.noise_t1,
                   self.reduction_db, self.n_fft,
                   "自动" if self.hop is None else self.hop,
                   "开" if self.track_floor else "关"))


@dataclass
class Project:
    """一个非破坏编辑项目：源文件 + 操作栈（按**规范顺序**依次作用，见下）。

    ⚠ 操作栈可以**混用**不同域的算子（2026-10-24 起）：渲染层按**规范顺序**
    （`render.PHASE`：去咔哒 → 去削波 → 去嗡 → 去噪 → 时频增益 → 增益）逐算子链式渲染，
    顺序被规范化时审计记 `reordered_from`。规范顺序的依据是
    `packaging/op_order_probe.py` 的实测（见 `docs/theory.md §12.15`）。
    同类算子之间仍然**不许重叠选区**（同一段被处理两遍的语义未立判据）。
    """

    source: str
    ops: list = field(default_factory=list)

    def add(self, op) -> Project:
        """追加一个操作并返回自身（链式）。操作**按顺序**作用，后加的后作用。"""
        self.ops.append(op)
        return self

    def is_identity(self) -> bool:
        """操作栈是否等价于"什么都不做"（全部 no-op / 空栈）。"""
        return all(op.is_noop for op in self.ops)

    def audit(self) -> list[dict]:
        """操作栈的机器可读记录（还没渲染时用；渲染时会补上实际生效样本数）。"""
        return [op.to_dict() for op in self.ops]

    def to_dict(self) -> dict:
        """**可复算**的项目记录（"可审计的修复"要能存下来再跑一遍）。

        ⚠ `source` 按**给定路径**原样存（不做相对化）：换机器/换目录要由调用方负责。
        ⚠ 撤销历史**不落盘**——它是会话状态，不是作品状态。
        """
        return {"version": PROJECT_VERSION, "source": self.source, "ops": self.audit()}

    @classmethod
    def from_dict(cls, d: dict) -> Project:
        ver = int(d.get("version", -1))
        if ver not in SUPPORTED_VERSIONS:
            raise ValueError("不认识的项目版本 %r（支持：%s）"
                             % (d.get("version"), "/".join(str(v) for v in SUPPORTED_VERSIONS)))
        src = d.get("source")
        if not isinstance(src, str) or not src:
            raise ValueError("项目记录缺 source")
        ops = d.get("ops")
        if not isinstance(ops, list):
            raise ValueError("项目记录的 ops 必须是列表")
        return cls(src, [op_from_dict(_migrate(x, ver)) for x in ops])


def np_finite(v: float) -> bool:
    """有限数判定（不引 numpy 依赖进 ops 层：math.isfinite 的别名）。"""
    import math
    return math.isfinite(v)


#: 有序迁移链（Ardour 式契约，Agent D 调研）：`vN → vN+1` 的纯函数 dict→dict。
#: 每步只**补缺失键**并写清"为什么语义不变"；老记录**永远**能开、迁移逻辑可单测。
_MIGRATIONS: dict[int, callable] = {
    1: lambda d: d,                                 # 1→2 由 _migrate 的 ver 语义覆盖
}


def _migrate(op_dict: dict, ver: int) -> dict:
    """历史版本 → 当前 schema 的**显式迁移**（每一步都写清"补什么、为什么语义不变"）。

    只补**缺失**的键，且只对 **ver ≤ 2**（历史记录）；v3 起键是**必需**的
    （缺失 = 缺字段，按严格 schema 拒绝）——"补什么"由版本决定，不许含糊。
    """
    if not isinstance(op_dict, dict):
        return op_dict
    kind = op_dict.get("kind")
    d = dict(op_dict)
    if ver <= 1 and kind == "denoise" and "track_floor" not in d:
        d["track_floor"] = False            # v1 = 固定谱（当时的全部语义）
    if ver <= 2 and kind == "declick" and "step_repair" not in d:
        d["step_repair"] = False            # v1/v2 = 单臂（当时的全部语义）
    return d


#: 项目记录的**版本号**。字段/语义一变就 +1，不许悄悄改（旧记录必须报错而不是被误解）。
#: v2（2026-10-27）：denoise 增加 `track_floor`。**v1 记录显式迁移**而非报错：
#: v1 的 denoise 没有"底噪跟踪"概念 = 固定谱 ⇒ 迁移时补 `track_floor=False`
#: （渲染结果与 v1 **逐位相同**——这是"不被误解"的正确含义：旧产物可复现，
#: 新语义不会被静默套到旧记录上）。
#: v3（2026-10-27）：declick 增加 `step_repair`（台阶第二检测臂）。v1/v2 迁移补
#: `step_repair=False`（v1/v2 语义 = 单臂）。
PROJECT_VERSION = 3
#: 仍在支持读取（显式迁移）的历史版本。
SUPPORTED_VERSIONS = (1, 2, 3)

#: `kind` → (类, 该类型 `to_dict` 的**精确**键集)：解析时**多一个少一个都拒绝**。
_OP_SCHEMA = {
    "gain": (GainOp, {"kind", "t0", "t1", "gain_db"}),
    "band_gain": (BandGainOp, {"kind", "t0", "t1", "f0", "f1", "gain_db"}),
    "poly_gain": (PolyGainOp, {"kind", "points", "gain_db"}),
    "declip": (DeclipOp, {"kind", "t0", "t1", "ceiling", "bandwidth", "bandwidth_source",
                          "iterations"}),
    "declick": (DeclickOp, {"kind", "t0", "t1", "threshold", "max_run",
                            "step_repair"}),
    "dehum": (DehumOp, {"kind", "t0", "t1", "f0", "f0_source", "q", "harmonics"}),
    "denoise": (DenoiseOp, {"kind", "t0", "t1", "noise_t0", "noise_t1",
                            "reduction_db", "n_fft", "hop", "track_floor"}),
}


def op_from_dict(d: dict):
    """把一条操作记录还原成操作对象。**严格**：未知 kind / 缺键 / 多键都拒绝。

    为什么严格：「可复算」的前提是**同一条记录只能解出一种操作**。宽松解析
    （例如忽略多余键）会让"记录里写了 `q=10` 但代码悄悄丢掉"这种事故静默发生。
    """
    if not isinstance(d, dict):
        raise ValueError("操作记录必须是 dict，得到 %r" % type(d).__name__)
    kind = d.get("kind")
    if kind not in _OP_SCHEMA:
        raise ValueError("未知操作 kind=%r（可选：%s）" % (kind, " / ".join(sorted(_OP_SCHEMA))))
    cls, keys = _OP_SCHEMA[kind]
    got = set(d)
    if got != keys:
        extra, missing = sorted(got - keys), sorted(keys - got)
        raise ValueError("操作 %r 的字段不符：多 %s ／ 少 %s" % (kind, extra, missing))
    kw = {k: v for k, v in d.items() if k != "kind"}
    if kind == "dehum":
        kw["harmonics"] = tuple(kw["harmonics"])
    if kind == "poly_gain":
        kw["points"] = tuple((float(t), float(f)) for t, f in kw["points"])
    return cls(**kw)


def save_chain(path: str, ops: list) -> None:
    """把操作栈存成**修复链预设**（只存 ops，**不绑源**——链是"配方"，不是"作品"）。

    与 `Project.to_dict` 的区别：项目记录 = 源 + 栈（复算一个具体产物）；
    修复链 = 栈（可套到**任何**源上，例如"同一套修复"批量过整个曲库）。
    版本纪律与项目记录相同：字段/语义一变 `PROJECT_VERSION` +1，旧文件报错而不是被误读。
    """
    import json

    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"version": PROJECT_VERSION, "ops": [op.to_dict() for op in ops]},
                  fh, ensure_ascii=False, indent=2)


def load_chain(path: str) -> list:
    """读修复链预设，还原成操作对象列表。**严格**（与 `op_from_dict` 同口径）：

    * 顶层必须是 `{"version": N, "ops": [...]}`，版本不符**拒绝**（不许被误读）；
    * 每条操作走 `op_from_dict`（未知 kind / 缺键 / 多键都拒绝）；
    * 空链**拒绝**（"载入一条空链"九成是拿错了文件，宁可报错）。
    """
    import json

    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    if not isinstance(d, dict):
        raise ValueError("修复链文件必须是 JSON 对象，得到 %r" % type(d).__name__)
    ver = int(d.get("version", -1))
    if ver not in SUPPORTED_VERSIONS:
        raise ValueError("不认识的修复链版本 %r（支持：%s）"
                         % (d.get("version"), "/".join(str(v) for v in SUPPORTED_VERSIONS)))
    ops_in = d.get("ops")
    if not isinstance(ops_in, list) or not ops_in:
        raise ValueError("修复链的 ops 必须是非空列表")
    return [op_from_dict(_migrate(x, ver)) for x in ops_in]
