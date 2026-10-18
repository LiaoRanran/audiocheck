"""编辑操作模型（**非破坏**）：项目只记录"对源做过什么"，源文件永不改。

三条设计约束（写在最前，免得后来者加花活）
------------------------------------------
1. **非破坏**：`Project` = 源路径 + 操作栈；导出是 `(源, 操作栈)` 的**纯函数**，
   同一输入必得同一输出。源文件在渲染过程中只读。
2. **可审计**：每个操作都能 `to_dict()`（机器可读）与 `describe()`（人可读），
   渲染再返回一份**审计记录**——这是本项目相对闭源工具的那条差别（见
   `docs/editor_roadmap.md §2`："可审计的修复"）。
3. **只做有判据的**：目前**只有**时间选区增益 `GainOp`。加任何新算法前，先按
   `docs/editor_roadmap.md §5` 建判据（合成语料 + 客观指标 + 效力边界），否则不加。
"""
from __future__ import annotations

from dataclasses import dataclass, field


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


@dataclass
class Project:
    """一个非破坏编辑项目：源文件 + 操作栈（按加入顺序**依次**作用）。"""

    source: str
    ops: list[GainOp] = field(default_factory=list)

    def add(self, op: GainOp) -> Project:
        """追加一个操作并返回自身（链式）。操作**按顺序**作用，后加的后作用。"""
        self.ops.append(op)
        return self

    def is_identity(self) -> bool:
        """操作栈是否等价于"什么都不做"（全部 no-op / 空栈）。"""
        return all(op.is_noop for op in self.ops)

    def audit(self) -> list[dict]:
        """操作栈的机器可读记录（还没渲染时用；渲染时会补上实际生效样本数）。"""
        return [op.to_dict() for op in self.ops]
