"""**撤销/重做**的会话模型：非破坏编辑栈的"可回退、可复算"。

## 为什么撤销在这个架构里几乎是免费的

`docs/editor_roadmap.md` 的 P0 把文档模型定成 `(源, 操作栈)` 纯函数渲染——
**没有"改过的采样"可以回滚**：撤销就是把栈**弹一格**，重做就是**压回去**。
所以本模块的判据不是"看起来像撤销了"，而是三条**可逐位验证**的事实：

1. **撤销到空栈的渲染结果与"从未编辑"的渲染结果逐位相同**（不是"接近"）；
2. **重做后的输出字节与撤销前完全相同**（同一栈 ⇒ 同一输出，确定性）；
3. **记录（`to_dict`）能还原出同一个栈**，并且**渲染同样的字节**（可复算）。

## 边界（写清楚，别让人误解）

* **撤销历史不落盘**（`to_dict` 只存当前栈）：它是会话状态，不是作品状态；
* **不存"改过的音频"**：栈里只有操作参数 ⇒ 撤销的代价是**重渲染**，不是回滚字节；
  因此撤销的"速度"取决于最近的选区大小（这也是为什么 `DeclipOp` 只把选区进内存）。
* **`redo` 栈会被新操作清空**（标准语义；不这么做就等于允许分叉，而分叉需要树形模型）。
"""
from __future__ import annotations

from .ops import Project, op_from_dict
from .render import render as _render


class EditSession:
    """源文件 + 当前操作栈 + 撤销/重做两个栈。所有操作都**不修改源**。"""

    def __init__(self, source: str, ops=None):
        self.source = source
        self._ops = list(ops or [])
        self._redo = []

    # ------------------------------------------------------------ 状态
    @property
    def ops(self) -> tuple:
        return tuple(self._ops)

    @property
    def undo_depth(self) -> int:
        return len(self._ops)

    @property
    def redo_depth(self) -> int:
        return len(self._redo)

    @property
    def can_undo(self) -> bool:
        return bool(self._ops)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def project(self) -> Project:
        """当前状态对应的渲染项目（**拷贝**栈，避免外部改动穿透到会话）。"""
        return Project(self.source, list(self._ops))

    # ------------------------------------------------------------ 编辑
    def apply(self, op) -> None:
        """压入一个操作。**新操作清空 redo 栈**（标准语义：不做分叉）。"""
        self._ops.append(op)
        self._redo.clear()

    def undo(self) -> bool:
        """弹出一格。空栈返回 `False`（**不抛异常**：UI 的按钮可以无脑点）。"""
        if not self._ops:
            return False
        self._redo.append(self._ops.pop())
        return True

    def redo(self) -> bool:
        """把最近撤销的操作压回去。没有可重做的返回 `False`。"""
        if not self._redo:
            return False
        self._ops.append(self._redo.pop())
        return True

    # ------------------------------------------------------------ 渲染
    def render(self, dest: str, **kw) -> dict:
        """渲染当前状态（**不渲染撤销历史**：撤销掉的操作不影响输出）。"""
        return _render(self.project(), dest, **kw)

    # ------------------------------------------------------------ 记录
    def to_dict(self) -> dict:
        """存**作品状态**（当前栈）。撤销历史是会话状态，不落盘。"""
        return self.project().to_dict()

    @classmethod
    def from_dict(cls, d: dict) -> EditSession:
        """从记录还原会话（redo 栈为空：载入后不能"重做"任何东西）。"""
        p = Project.from_dict(d)
        return cls(p.source, p.ops)


__all__ = ["EditSession", "op_from_dict"]
