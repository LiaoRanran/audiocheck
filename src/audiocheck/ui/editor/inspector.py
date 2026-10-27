"""检视面板：操作栈（可复算记录）+ 审计 + A/B 切换。

这是"可审计的修复"的落地处——每一次修复都列出：改了什么 / 依据哪条测量 /
实测生效多少 / 置信度。A/B 把原曲与处理后并存，一键切换、读数差可见。

切片 8 新增**互操作栈**：操作栈从静态文本变成可交互列表——逐项开关（临时关掉某步看差异，
非破坏、不删栈）、上移/下移（重排；纯同域栈顺序无关 ⇒ 恒等，跨域走规范顺序 ⇒ 也恒等）、
删除、以及"载入项目记录"把一份 `Project.to_dict` JSON 直接灌进当前会话（复用内核
`Project.from_dict`，未知记录版本/缺字段会拒绝）。每次改动都触发 `stackChanged` ⇒ 外壳
重渲染并刷新 A/B 预览。
"""

from __future__ import annotations

import json
import os

from PySide6.QtCore import Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from ...core.edit.ops import Project
from ...core.edit.session import EditSession
from .. import theme as T

#: 列表项里存"操作下标"的 data 角色（自定整数，避开 Qt 枚举引用）。
_IDX_ROLE = 0x1000


class InspectorPanel(QFrame):
    """检视面板：交互式操作栈 + 审计 + A/B 开关。"""

    abToggled = Signal(bool)   # True=看处理后 / False=看原曲
    exportRequested = Signal()
    stackChanged = Signal()     # 操作栈被（就地）改动 ⇒ 外壳重渲染 + 刷 A/B

    def __init__(self, theme: T.Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("InspectorPanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(T.Space.sm, T.Space.sm, T.Space.sm, T.Space.sm)
        v.setSpacing(T.Space.sm)

        # A/B 切换 + 导出
        ab = QHBoxLayout()
        ab.setSpacing(T.Space.sm)
        self.btn_ab = QPushButton("A/B：原曲")
        self.btn_ab.setCheckable(True)
        self.btn_ab.toggled.connect(lambda on: self.abToggled.emit(on))
        self._ab_state = False
        #: 顶点级编辑：当前选中的顶点下标（0-based）；`_show_poly_points` 负责夹紧到合法区间。
        self._poly_vertex = 0
        ab.addWidget(self.btn_ab)
        self.btn_export = QPushButton("导出项目记录")
        self.btn_export.clicked.connect(self.exportRequested)
        ab.addWidget(self.btn_export)
        v.addLayout(ab)

        # 操作栈（可交互）
        v.addWidget(QLabel("操作栈（可复算，可交互）"))
        self.stack_view = QListWidget()
        self.stack_view.setObjectName("InspectorStack")
        self.stack_view.setSelectionMode(QListWidget.SingleSelection)
        # 选中 poly_gain 条目 ⇒ 审计区显示**顶点明细**（"记录即选区"的可读性落地）
        self.stack_view.itemSelectionChanged.connect(self._show_poly_points)
        v.addWidget(self.stack_view, 2)

        # 操作栈的工具行：开关 / 上移 / 下移 / 删除 / 载入记录
        tools = QHBoxLayout()
        tools.setSpacing(T.Space.xs)
        self.btn_toggle = self._tbtn("开关", "启用/停用选中步骤", self._on_toggle)
        self.btn_up = self._tbtn("↑", "上移选中步骤", self._on_move_up)
        self.btn_down = self._tbtn("↓", "下移选中步骤", self._on_move_down)
        self.btn_del = self._tbtn("删除", "删除选中步骤", self._on_remove)
        self.btn_load = self._tbtn("载入记录", "导入 Project JSON 灌入会话", self._on_load_project)
        for b in (self.btn_toggle, self.btn_up, self.btn_down, self.btn_del, self.btn_load):
            tools.addWidget(b)
        v.addLayout(tools)

        # 审计
        v.addWidget(QLabel("最近一次渲染审计"))
        self.audit_view = QTextBrowser()
        self.audit_view.setObjectName("InspectorAudit")
        v.addWidget(self.audit_view, 2)

        # 当前会话（由外壳喂入；互操作栈的就地改动作用在这里）
        self._session: EditSession | None = None
        self._fs: int = 44100

    # ---- 内部 ----
    def _tbtn(self, text: str, tip: str, slot) -> QPushButton:
        from PySide6.QtCore import Qt as _Qt

        b = QPushButton(text)
        b.setToolTip(tip)
        b.setFocusPolicy(_Qt.StrongFocus)
        b.clicked.connect(slot)
        return b

    # ---- 外部（外壳调）----
    def attach_session(self, session: EditSession) -> None:
        """绑定当前编辑会话（就地互操作栈的作用对象）。"""
        self._session = session
        self.set_stack(list(session.ops))

    def set_stack(self, ops: list) -> None:
        """用当前会话的操作刷新列表（每次应用/撤销/载入后由外壳调）。"""
        if self._session is not None:
            ops = list(self._session.ops)
        self.stack_view.blockSignals(True)
        self.stack_view.clear()
        disabled = frozenset(self._session.disabled) if self._session else frozenset()
        for i, op in enumerate(ops):
            item = QListWidgetItem("%d. %s" % (i + 1, op.describe()))
            item.setData(_IDX_ROLE, i)
            item.setFlags(item.flags())  # 允许选中
            if i in disabled:
                item.setText("[关] %d. %s" % (i + 1, op.describe()))
                item.setForeground(QColor(self._theme.p.text_disabled))
            self.stack_view.addItem(item)
        self._refresh_tool_enabled()
        self.stack_view.blockSignals(False)

    def _poly_context(self):
        """当前选中的 `PolyGainOp` 与其栈下标 → `(idx, op)`；不是 poly 则返回 `None`。

        「记录即选区」的可编辑面：顶点表就是选区本身，所以这里把它当**数据**取出来，
        展示与顶点级微调用**同一个入口**（避免两处逻辑漂移）。
        """
        item = self.stack_view.currentItem()
        if item is None or self._session is None:
            return None
        idx = item.data(_IDX_ROLE)
        ops = self._session.ops
        if idx is None or not (0 <= idx < len(ops)):
            return None
        op = ops[idx]
        if op.to_dict().get("kind") != "poly_gain":
            return None
        return idx, op

    def _show_poly_points(self) -> None:
        """选中 `PolyGainOp` 条目 ⇒ 顶点表进审计区（逐行 t/f，**可读 + 可微调**）。

        "记录即选区"要**能被读到**、且**能被改到**：
        * 只读：逐行列出（人工核对它是审计的一部分）；
        * **编辑（v1.1.1）**：↑/↓ 选顶点，←/→ 平移，Shift 缩放，Alt+↑↓ 改频率
          （与画布套索同一套键盘口径，只是粒度降到**单个顶点**）；改动经
          `session.replace_at` 落回栈（构造新 op ⇒ 非破坏语义不变）并刷新视图。
        其它 kind 不动审计区（点击不打断现有审计内容）。
        """
        ctx = self._poly_context()
        if ctx is None:
            return
        _idx, op = ctx
        self._poly_vertex = max(0, min(self._poly_vertex, len(op.points) - 1))
        lines = ["多边形顶点（%d 个，隐式闭合）——↑/↓ 选顶点，←/→ 平移，Shift 缩放，Alt+↑↓ 改频率："
                 % len(op.points)]
        for i, (t, f) in enumerate(op.points, 1):
            mark = "▶" if (i - 1) == self._poly_vertex else " "
            lines.append(" %s %d. t=%.4f s  f=%.1f Hz" % (mark, i, t, f))
        self.audit_view.setPlainText("\n".join(lines))

    def keyPressEvent(self, ev):  # noqa: N802
        """**顶点级微调**（选中 `PolyGainOp` 条目时接管方向键）：

        * ↑/↓  ← 换顶点；←/→ ← 时间平移；Shift+方向键 ← 缩放该顶点坐标；Alt+↑/↓ ← 改频率。
        与画布套索（`editor_view._move_poly`）同一套语义，只是作用粒度从"整条多边形"
        降到"单个顶点"——**套索能力的上限形态**：画完不仅能整体挪，还能逐点修。
        """
        from PySide6.QtCore import Qt

        ctx = self._poly_context()
        if ctx is None:
            super().keyPressEvent(ev)
            return
        idx, op = ctx
        k = ev.key()
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        alt = bool(ev.modifiers() & Qt.KeyboardModifier.AltModifier)
        pts = list(op.points)
        vi = max(0, min(self._poly_vertex, len(pts) - 1))
        t, f = pts[vi]
        if not shift and not alt and k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            self._poly_vertex = (vi + (-1 if k == Qt.Key.Key_Up else 1)) % len(pts)
            self._show_poly_points()
            return
        dt = max(float(t) * 0.002, 1e-4)
        df = max(float(f) * 0.02, 1.0)
        if shift:
            st = 1.05 if k in (Qt.Key.Key_Right, Qt.Key.Key_Up) else 1.0 / 1.05
            if k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                t = float(t) * st
            elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                f = float(f) * st
            else:
                super().keyPressEvent(ev)
                return
        elif alt and k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            f = float(f) + (df if k == Qt.Key.Key_Up else -df)
        elif k in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            t = float(t) + (-dt if k == Qt.Key.Key_Left else dt)
        elif k in (Qt.Key.Key_Up, Qt.Key.Key_Down):
            f = float(f) + (df if k == Qt.Key.Key_Up else -df)
        else:
            super().keyPressEvent(ev)
            return
        pts[vi] = (max(0.0, float(t)), max(20.0, float(f)))
        from audiocheck.core.edit.ops import PolyGainOp
        self._session.replace_at(idx, PolyGainOp(points=pts, gain_db=op.gain_db))
        self._show_poly_points()
        self.stackChanged.emit()

    def _refresh_tool_enabled(self) -> None:
        has = self.stack_view.count() > 0
        for b in (self.btn_toggle, self.btn_up, self.btn_down, self.btn_del):
            b.setEnabled(has)
        self.btn_load.setEnabled(self._session is not None)

    def _selected_index(self) -> int | None:
        it = self.stack_view.currentItem()
        if it is None:
            return None
        return it.data(_IDX_ROLE)

    # ---- 互操作栈动作 ----
    def _on_toggle(self) -> None:
        if self._session is None:
            return
        idx = self._selected_index()
        if idx is None:
            return
        self._session.toggle_enabled(idx)
        self.set_stack(list(self._session.ops))
        self.stackChanged.emit()

    def _on_move_up(self) -> None:
        if self._session is None:
            return
        idx = self._selected_index()
        if idx is None or idx == 0:
            return
        self._session.move(idx, idx - 1)
        self.set_stack(list(self._session.ops))
        self.stack_view.setCurrentRow(idx - 1)
        self.stackChanged.emit()

    def _on_move_down(self) -> None:
        if self._session is None:
            return
        idx = self._selected_index()
        if idx is None or idx >= len(self._session.ops) - 1:
            return
        self._session.move(idx, idx + 1)
        self.set_stack(list(self._session.ops))
        self.stack_view.setCurrentRow(idx + 1)
        self.stackChanged.emit()

    def _on_remove(self) -> None:
        if self._session is None:
            return
        idx = self._selected_index()
        if idx is None:
            return
        self._session.remove_at(idx)
        self.set_stack(list(self._session.ops))
        self.stackChanged.emit()

    def _on_load_project(self) -> None:
        """把一份 `Project.to_dict` JSON 灌入当前会话（未知版本/缺字段 ⇒ 拒绝，不静默）。"""
        if self._session is None:
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "载入项目记录", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                record = json.load(fh)
            proj = Project.from_dict(record)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            self.set_audit({"status": "载入失败：%s" % e})
            return
        # 不静默替换源：记录里的 source 必须与当前会话的源一致（与 CLI --project 同纪律）
        if os.path.normcase(os.path.abspath(proj.source)) != \
                os.path.normcase(os.path.abspath(self._session.source)):
            self.set_audit({"status": "载入失败：记录源 %s 与当前源不一致（不静默换源）"
                            % proj.source})
            return
        # 重建会话的操作栈（保留当前源），redo 清空
        self._session._ops = list(proj.ops)
        self._session._redo.clear()
        self._session._disabled.clear()
        self.set_stack(list(self._session.ops))
        self.stackChanged.emit()

    def set_audit(self, audit: dict) -> None:
        if not audit:
            self.audit_view.setPlainText("（无）")
            return
        # 只取可人工读的字段，不 dump 整本字典
        keys = ("stages", "reordered_from", "changed_samples", "suppression_db",
                "ambiguity", "residual_scale", "roundtrip_floor_dbfs", "energy_ratio_db")
        out = []
        for k in keys:
            if k in audit:
                out.append("%s: %s" % (k, audit[k]))
        # stages 是列表，逐条展开
        if "stages" in audit and isinstance(audit["stages"], list):
            out.append("stages:")
            for s in audit["stages"]:
                out.append("  - %s" % s)
        # 关掉的步骤也要在审计区显示，避免"关了但看不见"
        if self._session is not None and self._session.disabled:
            off = [i + 1 for i in sorted(self._session.disabled)]
            out.append("（已停用步骤：%s）" % ", ".join(str(i) for i in off))
        self.audit_view.setPlainText("\n".join(out) if out else str(audit))

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.setStyleSheet(T.qss(t))
