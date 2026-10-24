"""编辑操作模型（**非破坏**）：项目只记录"对源做过什么"，源文件永不改。

三条设计约束（写在最前，免得后来者加花活）
------------------------------------------
1. **非破坏**：`Project` = 源路径 + 操作栈；导出是 `(源, 操作栈)` 的**纯函数**，
   同一输入必得同一输出。源文件在渲染过程中只读。
2. **可审计**：每个操作都能 `to_dict()`（机器可读）与 `describe()`（人可读），
   渲染再返回一份**审计记录**——这是本项目相对闭源工具的那条差别（见
   `docs/editor_roadmap.md §2`："可审计的修复"）。
3. **只做有判据的**：目前四个操作——时间选区增益 `GainOp`、时频选区增益
   `BandGainOp`（判据在 `tests/test_edit_render.py` / `tests/test_edit_stft.py`）、
   去削波 `DeclipOp`（判据在 `tests/test_edit_declip.py`）、去咔哒 `DeclickOp`
   （判据在 `tests/test_edit_declick.py`）、去嗡 `DehumOp`（判据在 `tests/test_edit_dehum.py`）。
   加任何新算法前，先按 `docs/editor_roadmap.md §5` 建判据（合成语料 + 客观指标 +
   效力边界），否则不加。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .declick import DEFAULT_MAX_RUN, DEFAULT_THRESHOLD
from .declip import DEFAULT_ITERATIONS
from .dehum import DEFAULT_HARMONICS, DEFAULT_Q


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

    ⚠ 两条**拒绝**规则（宁可不做，见 `render.render`）：① 与 `GainOp`/`BandGainOp` 同栈
    混用；② 多个 `DeclipOp` 的选区**互相重叠**——两种情况的顺序语义都还没立判据。
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

    ⚠ 两条**拒绝**规则（与 `DeclipOp` 同款，宁可不做）：① 与其它域的算子同栈混用；
    ② 多个 `DeclickOp` 的选区**互相重叠**。

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
                "threshold": self.threshold, "max_run": self.max_run}

    def describe(self) -> str:
        return "去咔哒 %.3f–%.3f s：检测阈值 %.2f×MAD，宽度守卫 %s" % (
            self.t0, self.t1, self.threshold,
            "关" if self.max_run is None else "≤%d 采样" % self.max_run)


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

    拒绝规则（与其它修复算子同款，宁可不做）：与其它域算子同栈混用；多个去嗡选区重叠。
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


@dataclass
class Project:
    """一个非破坏编辑项目：源文件 + 操作栈（按加入顺序**依次**作用）。

    ⚠ 操作栈目前是 `GainOp`（时间域）**或** `BandGainOp`（频域）**或** `DeclipOp`（去削波）
    **或** `DeclickOp`（去咔哒）**或** `DehumOp`（去嗡）**五选一**：同一栈里混用时，操作顺序的语义
    （先按频率改谱、
    还是先按时间改样点）还没立判据，渲染层直接拒绝（见 `render.render`），不猜。
    同类算子之间也不许重叠选区。
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
        if int(d.get("version", -1)) != PROJECT_VERSION:
            raise ValueError("不认识的项目版本 %r（只支持 %d）"
                             % (d.get("version"), PROJECT_VERSION))
        src = d.get("source")
        if not isinstance(src, str) or not src:
            raise ValueError("项目记录缺 source")
        ops = d.get("ops")
        if not isinstance(ops, list):
            raise ValueError("项目记录的 ops 必须是列表")
        return cls(src, [op_from_dict(x) for x in ops])


#: 项目记录的**版本号**。字段/语义一变就 +1，不许悄悄改（旧记录必须报错而不是被误解）。
PROJECT_VERSION = 1

#: `kind` → (类, 该类型 `to_dict` 的**精确**键集)：解析时**多一个少一个都拒绝**。
_OP_SCHEMA = {
    "gain": (GainOp, {"kind", "t0", "t1", "gain_db"}),
    "band_gain": (BandGainOp, {"kind", "t0", "t1", "f0", "f1", "gain_db"}),
    "declip": (DeclipOp, {"kind", "t0", "t1", "ceiling", "bandwidth", "bandwidth_source",
                          "iterations"}),
    "declick": (DeclickOp, {"kind", "t0", "t1", "threshold", "max_run"}),
    "dehum": (DehumOp, {"kind", "t0", "t1", "f0", "f0_source", "q", "harmonics"}),
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
    return cls(**kw)
