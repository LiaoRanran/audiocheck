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

import glob
import json
import os
import tempfile

from .ops import Project, op_from_dict
from .render import render as _render


class EditSession:
    """源文件 + 当前操作栈 + 撤销/重做两个栈。所有操作都**不修改源**。"""

    def __init__(self, source: str, ops=None):
        self.source = source
        self._ops = list(ops or [])
        self._redo = []
        # **启用掩码**：被禁用的操作不参与渲染（视图侧"临时关掉某步"而不删栈）。
        # 用操作在 `_ops` 里的下标做键；顺序/增删时由调用方维护。
        self._disabled: set[int] = set()

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

    # ------------------------------------------------------------ 互操作栈编辑
    def move(self, src: int, dst: int) -> bool:
        """把第 `src` 个操作移到 `dst` 个位置（0-based；夹紧到合法区间）。

        这是"重排操作顺序"——**只在不改变渲染结果的范围内才有意义**：
        - 纯同域栈（如多个 `band_gain`）渲染是逐操作按选区叠加，**顺序无关** ⇒ 重排恒等；
        - 跨域栈走 `render.canonical_order` 的**规范顺序**，用户给的顺序会被重排，
          所以这里的 `move` 也不改变最终输出（只是改变了"用户意图顺序"的展示）。
        因此 `move` 是安全的**纯展示态**操作，不会让渲染结果不可预测。
        新操作清空 redo 栈（与 `apply` 同语义：动了栈就不可逆分叉）。
        """
        if not (0 <= src < len(self._ops)):
            return False
        dst = max(0, min(dst, len(self._ops) - 1))
        if src == dst:
            return False
        op = self._ops.pop(src)
        self._ops.insert(dst, op)
        # 掩码下标跟随移动：把 dst 位置的旧键映射成 src 的新键
        self._disabled = {dst if k == src else k for k in self._disabled}
        self._redo.clear()
        return True

    def replace_at(self, idx: int, op) -> bool:
        """**就地替换**第 `idx` 个操作（顶点编辑 / 参数微调的底座）。

        为什么需要它（而不是"undo + apply"）：
        * 顶点编辑要**保持栈位置**（替换后仍在原处，不影响其它算子的相对顺序）；
        * 不进 undo/redo —— 它是**同一个作品状态的一次精修**，不是一次新的编辑
          （与 `remove_at` 同语义：动了栈就清空 redo，避免分叉）。
        * 非破坏语义不变：只是构造新 op 对象，源文件只读。
        越界返回 `False`（UI 可无脑调）。
        """
        if not (0 <= idx < len(self._ops)):
            return False
        self._ops[idx] = op
        self._redo.clear()
        return True

    def remove_at(self, idx: int) -> bool:
        """删除第 `idx` 个操作（**不进 undo 栈**：删除是破坏性编辑，与"撤销到空栈可复算"
        的判据冲突——撤销应回到"上一版作品"，而不是"少了一项的半截栈"）。
        返回是否真的删了。"""
        if not (0 <= idx < len(self._ops)):
            return False
        del self._ops[idx]
        # 掩码下标收缩：删掉 idx 后，>idx 的键整体前移一位
        self._disabled = {k - 1 if k > idx else k for k in self._disabled if k != idx}
        self._redo.clear()
        return True

    def toggle_enabled(self, idx: int) -> bool:
        """切换第 `idx` 个操作的"是否参与渲染"。返回切换后的状态（True=启用）。
        这是一个**非破坏的临时开关**：不删栈、不进 undo，用于"先关掉某步看差异"。
        """
        if not (0 <= idx < len(self._ops)):
            return False
        if idx in self._disabled:
            self._disabled.discard(idx)
            return True
        self._disabled.add(idx)
        return False

    @property
    def disabled(self) -> frozenset:
        """当前被禁用的操作下标（只读视图）。"""
        return frozenset(self._disabled)

    def live_ops(self) -> tuple:
        """**实际参与渲染**的操作（剔除被禁用的）。撤销历史不进入渲染。"""
        return tuple(o for i, o in enumerate(self._ops) if i not in self._disabled)

    # ------------------------------------------------------------ 渲染
    #: 前缀缓存的**键**：源 + 启用栈里每个操作的 `to_dict()`（不可变 dataclass ⇒
    #: 同参数同键）。中间文件在 tempdir，按键命名；命中即跳过该前缀的渲染。
    #: （Agent D 调研的 Audacity 块共享思路的 JSON 版：算子不可变 ⇒ 前缀可 memo，
    #: undo/redo 退化为缓存索引切换。上限 8 个中间文件，超限整体失效——有界。）
    _CACHE_LIMIT = 8

    #: **正在被某个渲染持有**的前缀文件（进程内引用计数）。清理时必须跳过它们：
    #: 并发渲染（UI 后台线程 / 批量作业 / 并行测试）会读到同一批内容寻址的文件，
    #: 删掉对方正在读的文件 ⇒ `LibsndfileError: ... System error`。
    #: 用进程内字典而非文件锁：本项目所有渲染都在同一进程内（多进程各有一份
    #: 字典，且 mtime 截断本就跨进程近似），加文件锁反而会引入新的死锁面。
    _IN_USE: dict[str, int] = {}

    @classmethod
    def _acquire(cls, files) -> None:
        for f in files:
            cls._IN_USE[f] = cls._IN_USE.get(f, 0) + 1

    @classmethod
    def _release(cls, files) -> None:
        for f in files:
            n = cls._IN_USE.get(f, 0) - 1
            if n > 0:
                cls._IN_USE[f] = n
            else:
                cls._IN_USE.pop(f, None)

    def _cache_dir(self) -> str:
        """前缀缓存的**目录**。

        默认 `tempfile.gettempdir()`；可用环境变量 `AUDIOCHECK_PREFIX_CACHE_DIR`
        覆盖——**并行渲染/并行测试时必须各自指定**：多个进程（或同一进程里跑
        不同代码版本的工作区）共享一个 tempdir 时，"内容寻址 + 全局 mtime 截断"
        会让它们互相删对方的中间产物。实测：8 路并发跑同一个变异体得到
        `killed×7 / survived×1` 的不稳定结果——**量具被自己的缓存竞态污染**了。
        每人一个目录之后，隔离是文件系统级别的，不需要任何锁。
        """
        return os.environ.get("AUDIOCHECK_PREFIX_CACHE_DIR") or tempfile.gettempdir()

    def _prefix_key(self) -> str:
        """本会话的前缀缓存**命名空间**（16 位十六进制）。

        ⚠ **必须只由源路径决定，不能掺会话 token**：前缀缓存的价值在于
        "同一源 + 同一前缀栈 ⇒ 同一文件"，跨会话/跨实例都要能命中
        （重新打开同一个文件不该从零重算）。`test_prefix_files_keys_are_content_addressed`
        就是钉这一点的判据：两个 `EditSession` 实例、同源同栈 ⇒ 键必须相同。

        并发隔离靠 `_cache_dir()`（目录级）+ `_IN_USE`（进程内引用计数）。
        """
        import hashlib

        return hashlib.sha256(os.path.abspath(self.source).encode("utf-8")
                              ).hexdigest()[:16]

    def _prefix_files(self, live: list) -> dict:
        """启用栈的**前缀中间文件**映射：`{i: path}`（i = 前缀长度，`i` 个操作后的
        产物）。只有全部 `0..i` 键都存在时前缀 `i` 才算命中。"""
        import hashlib

        cache: dict = {}
        key = self._prefix_key()
        for i in range(1, len(live) + 1):
            h = hashlib.sha256()
            h.update(key.encode("utf-8"))
            for op in live[:i]:
                h.update(json.dumps(op.to_dict(), sort_keys=True).encode("utf-8"))
            cache[i] = os.path.join(self._cache_dir(),
                                    "_ac_prefix_%s_%d_%s.wav"
                                    % (key, i, h.hexdigest()[:16]))
        return cache

    def render(self, dest: str, **kw) -> dict:
        """渲染当前状态（**不渲染撤销历史**：撤销掉的操作不影响输出）。

        若部分操作被 `toggle_enabled` 关掉，则只渲染仍启用的操作
        （被关掉的步骤是**非破坏临时开关**，不删栈、不进 undo）。

        **前缀缓存**（2026-10-27）：算子不可变 ⇒ 启用栈的每个前缀的渲染产物可复用。
        找到最长已缓存前缀 `i`，从中间文件续渲染 `live[i:]`（每级联一段落一个
        FLOAT 中间文件，与 `_render_chained` 同构）；应用/撤销/重做通常只动栈尾
        ⇒ 长链的重复渲染从 O(全链) 降到 O(尾部)。缓存命中数进审计
        （`prefix_cache_hits`）。
        """

        live = list(self.live_ops())
        if len(live) <= 1:
            proj = Project(self.source, live)
            return _render(proj, dest, **kw)
        # ⚠ 前缀缓存**只对混域（chained）栈启用**：中间文件是 FLOAT（float32），
        # 对同域单路径栈（time/band 全 float64）会把 −144 dBFS 的舍入带进"未编辑
        # 处"，破坏 undo/redo **逐位相同**的硬判据（实测抓到）。混域栈走
        # `_render_chained` 本来就是 FLOAT 中间层 ⇒ 缓存不改变其精度口径。
        from .render import _kind_of
        kinds = {_kind_of(o) for o in live}
        if len(kinds) == 1:
            proj = Project(self.source, live)
            return _render(proj, dest, **kw)
        cache = self._prefix_files(live)
        best = 0
        for i in sorted(cache, reverse=True):
            if all(os.path.exists(cache[j]) for j in range(1, i + 1)):
                best = i
                break
        # ⚠ **不在这里删任何文件**。前缀文件名是**内容寻址**的（同源同栈 ⇒ 同路径），
        # 所以「本会话不再需要的文件」可能正是**另一个正在并发渲染的会话**要读的
        # 中间产物——删了它，对方随后 `_render` 读到就拿到
        # `LibsndfileError: ... System error`（实测：并发两路渲染互相删对方前缀 ⇒
        # 等价变异体被误判成"杀死"，量具被竞态污染）。
        #
        # 空间有界靠 `finally` 里的**按 mtime 截断**（保留最近 _CACHE_LIMIT 个），
        # 且只在**渲染结束之后**执行——那时本会话已不再读取这批文件。并发场景下
        # 最坏是多个会话争夺同一批内容相同的文件，谁的都还是同一份数据（内容寻址
        # 保证同路径 = 同内容），因此截断只影响"下次要不要重算"，不影响正确性。
        cur = self.source if best == 0 else cache[best]
        audit = None
        # 登记本会话接下来可能读写的全部前缀文件（含复用的 best 前缀）：
        # 清理逻辑必须跳过 `_IN_USE` 里的文件，否则并发渲染互相删对方的中间产物。
        held = list(cache.values())
        self._acquire(held)
        try:
            for i in range(best + 1, len(live) + 1):
                proj = Project(cur, [live[i - 1]])
                is_last = i == len(live)
                audit = _render(proj, dest if is_last else cache[i],
                                subtype=None if is_last else "FLOAT",
                                overwrite=True)
                cur = cache[i]
                if is_last:
                    audit["prefix_cache_hits"] = best        # 复用的前缀段数
                    audit["prefix_cache_used"] = best
                    return audit
            # 空栈/单操作兜底（不可达，len(live) >= 2）——但**口径一致**：
            # 走了缓存路径就必须报 prefix 状态（变异实验抓到：兜底缺字段 ⇒ 判据漏杀）
            proj = Project(self.source, live)
            audit = _render(proj, dest, **kw)
            audit["prefix_cache_hits"] = best
            audit["prefix_cache_used"] = best
            return audit
        finally:
            self._release(held)
            # **有界**：只保留最近的中间文件（按 mtime 截断到 _CACHE_LIMIT）。
            #
            # ⚠ 并发安全靠两件事（实测出来的，别简化）：
            # 1. **跳过 `_IN_USE`**：别的会话可能正在它的 for 循环里读某个前缀文件；
            #    删掉正在被读的文件会让它拿到 `LibsndfileError: ... System error`。
            #    （实测症状：两个等价变异体单独跑都通过、并发跑就"失败"——
            #    量具被自己的竞态污染。）
            # 2. **不按会话命名空间隔离**：文件名是内容寻址的（同源同栈同路径），
            #    多会话共享同一批文件是**特性**（跨实例复用），不是冲突。
            in_use = self._IN_USE
            files = sorted(glob.glob(os.path.join(self._cache_dir(),
                                                  "_ac_prefix_*.wav")),
                           key=os.path.getmtime, reverse=True)
            for f in files[self._CACHE_LIMIT:]:
                if f in in_use:
                    continue
                try:
                    os.remove(f)
                except OSError:
                    pass

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
