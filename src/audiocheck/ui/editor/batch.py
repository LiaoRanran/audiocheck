"""批处理作业面板（P4 工作流的可视化入口）。

把"一堆音轨各跑一套操作"做成可视化：添加作业（源 + 目标 + 操作栈）、从项目记录汇入、
逐项删除、一键"导出批处理清单 JSON"并**直接调 CLI 的 `--batch` 跑**（复用已验证的
`Project.from_dict` + `render` 链路，逐项隔离）。失败的作业不拖垮其余，汇总列在下方。

设计：面板只管"作业清单的数据 + 渲染成清单"，**真正跑**交给 `edit_cli.main`（与命令行同一个
实现，不重写）；这样 GUI 与 CLI 共用一条批处理路径，互证。
"""

from __future__ import annotations

import json

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from ...core.edit.ops import Project, op_from_dict
from .. import theme as T


class BatchPanel(QWidget):
    """批处理作业清单（纯数据 + 渲染成 `--batch` 清单）。

    作业项：`{"source": ..., "dest": ..., "ops": [...], "subtype"?, "overwrite"?, "dither"?}`。
    `ops` 是 `Project.to_dict["ops"]` 的**同一个格式**（kind + 精确字段集）。
    """

    runRequested = Signal(list)   # 把清单（list[dict]）交给外壳去调 CLI

    def __init__(self, theme: T.Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("BatchPanel")
        v = QVBoxLayout(self)
        v.setContentsMargins(T.Space.sm, T.Space.sm, T.Space.sm, T.Space.sm)
        v.setSpacing(T.Space.sm)

        from PySide6.QtWidgets import QLabel
        v.addWidget(QLabel("批处理作业（每项一个独立小工程）"))

        self.list = QTextEdit()
        self.list.setObjectName("BatchList")
        self.list.setReadOnly(True)
        self.list.setPlainText("（空）")
        v.addWidget(self.list, 1)

        add = QHBoxLayout()
        add.setSpacing(T.Space.sm)
        self.btn_add = QPushButton("加作业")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_from_project = QPushButton("从记录汇入")
        self.btn_from_project.clicked.connect(self._on_import_project)
        self.btn_del = QPushButton("删选中")
        self.btn_del.clicked.connect(self._on_clear)
        for b in (self.btn_add, self.btn_from_project, self.btn_del):
            add.addWidget(b)
        v.addLayout(add)

        run = QHBoxLayout()
        run.setSpacing(T.Space.sm)
        self.btn_export = QPushButton("导出清单")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_run = QPushButton("运行批处理")
        self.btn_run.clicked.connect(lambda: self.runRequested.emit(self.jobs()))
        run.addWidget(self.btn_export)
        run.addWidget(self.btn_run)
        v.addLayout(run)

        self._jobs: list[dict] = []

    # ---- 数据 ----
    def jobs(self) -> list[dict]:
        return list(self._jobs)

    def add_job(self, job: dict) -> bool:
        """加一条作业；缺 source/dest 拒绝。返回是否真的加了。"""
        if not isinstance(job, dict) or "source" not in job or "dest" not in job:
            return False
        # ops 须在加入前就能解析（复用 CLI 的严格解析，未知 kind/缺字段当场拒绝）
        try:
            for o in job.get("ops", []):
                op_from_dict(o)
        except (ValueError, json.JSONDecodeError):
            return False
        self._jobs.append(job)
        self._refresh()
        return True

    def clear(self) -> None:
        self._jobs.clear()
        self._refresh()

    def _refresh(self) -> None:
        if not self._jobs:
            self.list.setPlainText("（空）")
            return
        lines = []
        for i, j in enumerate(self._jobs, 1):
            ops = j.get("ops", [])
            lines.append("%d. %s → %s ｜ %d 个操作" % (
                i, j["source"], j["dest"], len(ops)))
        self.list.setPlainText("\n".join(lines))

    def to_manifest(self) -> list[dict]:
        """渲染成 `--batch` 清单（与 CLI 同口径；只保留 CLI 认识的键）。"""
        out = []
        for j in self._jobs:
            item = {"source": j["source"], "dest": j["dest"],
                    "ops": j.get("ops", [])}
            if "subtype" in j:
                item["subtype"] = j["subtype"]
            if j.get("overwrite"):
                item["overwrite"] = True
            if j.get("dither"):
                item["dither"] = True
            out.append(item)
        return out

    # ---- 动作 ----
    def _on_add(self) -> None:
        src, _ = QFileDialog.getOpenFileName(
            self, "源音频", "", "音频 (*.flac *.wav *.aiff *.mp3 *.ogg)")
        if not src:
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "输出文件", "", "WAV (*.wav)")
        if not dst:
            return
        self.add_job({"source": src, "dest": dst, "ops": []})

    def _on_import_project(self) -> None:
        """从一份 `Project.to_dict` 汇入成一条作业（不静默换源，沿用 CLI 纪律）。"""
        path, _ = QFileDialog.getOpenFileName(
            self, "载入项目记录", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                rec = json.load(fh)
            proj = Project.from_dict(rec)
        except (OSError, ValueError, json.JSONDecodeError) as e:
            self._status("汇入失败：%s" % e)
            return
        dst, _ = QFileDialog.getSaveFileName(
            self, "输出文件", "", "WAV (*.wav)")
        if not dst:
            return
        self.add_job({"source": proj.source, "dest": dst,
                      "ops": proj.audit(), "overwrite": False})

    def _on_clear(self) -> None:
        self.clear()

    def _on_export(self) -> None:
        if not self._jobs:
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出批处理清单", "", "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_manifest(), fh, ensure_ascii=False, indent=2)

    def _status(self, msg: str) -> None:
        # 轻量：直接把状态写进清单区上方（与检视面板同款极简）
        self.list.setPlainText(msg)

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.setStyleSheet(T.qss(t))
