"""编辑工作站主窗口（REAPER 式停靠面板外壳）。

切片 3–4：三个 `QDockWidget`（文件 / 编辑 / 检视）+ 顶部走带条 + 命令面板，
编辑视图（频谱图 + 波形 + 矩形时间×频率选区）与算子面板 + 审计/A-B 全部接入。

复用：
- `chrome.FramelessWindow`（无边框外壳，QMainWindow，天然支持 dock）
- `theme.THEMES` / `theme.qss` / `palette`（设计系统零新增）
- `widgets.CommandPalette`（Ctrl+K 沿用）
- `core.edit.playback.PlaybackEngine` + `core.edit.ops` / `session`（内核复用，不重写）
- `ui/zoom.py`（缩放数学）、`core.views.spectrogram`（与分析侧同一张图）
"""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QListWidget,
    QVBoxLayout,
    QWidget,
)

from ...core.edit.playback import Backend, PlaybackEngine, enumerate_playback_devices
from ...core.edit.session import EditSession
from .. import theme as T
from ..chrome import FramelessWindow
from ..widgets import CommandPalette
from .batch import BatchPanel
from .editor_view import EditorView
from .inspector import InspectorPanel
from .jobs import JobRunner
from .ops import OperatorPanel
from .transport import TransportBar


class _Dock(QDockWidget):
    """统一外观的停靠面板（REAPER 式：标题栏 + 内容）。"""

    def __init__(self, title: str, theme: T.Theme, central: QWidget):
        super().__init__(title)
        self.setObjectName("Dock_" + title)
        self.setWidget(central)
        self._theme = theme
        self.setFeatures(
            QDockWidget.DockWidgetFloatable
            | QDockWidget.DockWidgetMovable
            | QDockWidget.DockWidgetClosable
        )
        self.setStyleSheet(T.qss(theme))


class EditorWindow(FramelessWindow):
    """编辑工作站主窗口。"""

    def __init__(self, theme: T.Theme | None = None):
        theme = theme or T.active_theme("dark")
        super().__init__(title="audiocheck · 编辑工作站")
        self._theme = theme
        # 后台作业执行器：导出/批处理不再阻塞 UI 线程。
        # ⚠ 必须作为成员持有（本仓踩过 QThread 被 GC 而线程仍在跑 ⇒ 进程硬崩）。
        self._jobs = JobRunner(self)

        self.transport = TransportBar(theme)
        self._add_transport(self.transport)

        # 三个主面板（先建，dock 构建时再塞进去）
        self.view = EditorView(theme)
        self.ops = OperatorPanel(theme)
        self.inspector_panel = InspectorPanel(theme)
        self.batch_panel = BatchPanel(theme)

        # 三个主面板 dock + 批处理 dock
        self.files = _Dock("文件", theme, self._build_files())
        self.editor = _Dock("编辑", theme, self._build_editor())
        self.inspector = _Dock("检视", theme, self._build_inspector())
        self.batch = _Dock("批处理", theme, self.batch_panel)

        self.addDockWidget(Qt.LeftDockWidgetArea, self.files)
        self.addDockWidget(Qt.RightDockWidgetArea, self.inspector)
        self.addDockWidget(Qt.RightDockWidgetArea, self.editor)
        self.tabifyDockWidget(self.inspector, self.editor)
        self.inspector.raise_()
        self.addDockWidget(Qt.BottomDockWidgetArea, self.batch)

        # 播放引擎
        self._engine: PlaybackEngine | None = None
        self._source: str | None = None

        self._palette: CommandPalette | None = None
        self._build_commands()
        self._wire_transport()
        self._wire_ops()

        self.apply_theme(theme)

    # ---- 构建 ----
    def _build_files(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(T.Space.sm, T.Space.sm, T.Space.sm, T.Space.sm)
        self._file_list = QListWidget()
        self._file_list.setObjectName("EditorFileList")
        # 屏幕阅读器需要一个名字：QListWidget 没有 text/toolTip，
        # `accessibleName` 是它唯一能被念出来的途径。
        self._file_list.setAccessibleName("文件列表")
        self._file_list.itemDoubleClicked.connect(self._on_file_chosen)
        v.addWidget(self._file_list)
        return w

    def _build_editor(self) -> QWidget:
        # 编辑 dock 的内容：视图在上、算子面板在下（垂直拆分，可浮动/重排）。
        from PySide6.QtWidgets import QSplitter
        split = QSplitter(Qt.Vertical)
        split.addWidget(self.view)
        split.addWidget(self.ops)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 1)
        self._editor_split = split
        return split

    def _build_inspector(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(0, 0, 0, 0)
        v.addWidget(self.inspector_panel, 1)
        return w

    def _add_transport(self, bar: TransportBar) -> None:
        central = QWidget()
        layout = QVBoxLayout(central)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(bar)
        self._central_stack = QWidget()
        layout.addWidget(self._central_stack, 1)
        self.setCentralWidget(central)

    # ---- 走带条接线 ----
    def _wire_transport(self) -> None:
        self.transport.playClicked.connect(self._on_play)
        self.transport.pauseClicked.connect(self._on_pause)
        self.transport.stopClicked.connect(self._on_stop)
        self.transport.zoomIn.connect(self.view.zoom_in)
        self.transport.zoomOut.connect(self.view.zoom_out)
        self.transport.zoomReset.connect(self.view.zoom_reset)
        self.transport.selectionToolToggled.connect(self.view.set_selection_tool)
        self.transport.lassoToggled.connect(self.view.set_lasso_tool)
        self.view.polySelected.connect(self.ops.set_poly_selection)
        self.transport.deviceChanged.connect(self._on_device)
        self.transport.loopAuditionClicked.connect(self._audition_selection)
        self.transport.operatorChosen.connect(lambda k: self._choose_op(k))

    def _wire_ops(self) -> None:
        self.view.selectionChanged.connect(self.ops.set_selection)
        self.view.seekRequested.connect(self._on_seek)
        self.ops.applied.connect(self._on_ops_applied)
        self.ops.auditReady.connect(self._on_audit)
        self.ops.status.connect(self._on_status)
        self.inspector_panel.abToggled.connect(self._on_ab)
        self.inspector_panel.exportRequested.connect(self._export_project)
        self.inspector_panel.stackChanged.connect(self._on_stack_changed)
        self.batch_panel.runRequested.connect(self._on_run_batch)

    def _on_audit(self, audit: dict) -> None:
        self.inspector_panel.set_audit(audit)
        # A/B 的 B 面 = 上次渲染的产物；缓存路径，切换时换缓冲。
        dest = audit.get("dest")
        if dest:
            self._preview_path = dest

    def _on_ops_applied(self, session: EditSession) -> None:
        """算子面板应用/撤销后：把会话绑给检视面板，刷新交互式栈。"""
        self.inspector_panel.attach_session(session)

    def _on_stack_changed(self) -> None:
        """检视面板就地改了操作栈（开关/重排/删除/载入）：重渲染刷 A/B，并刷新审计。"""
        if self._source is None or self.ops._session is None:
            return
        self.ops._render_and_emit()

    # ---- 文件 ----
    def open_file(self, path: str) -> None:
        import soundfile as sf
        info = sf.info(path)
        fs = info.samplerate
        self._source = path
        self._fs = fs
        self.view.set_source(path, fs)
        self.ops.load_source(path, fs)
        self.inspector_panel.attach_session(self.ops._session)
        # A 面（原曲）缓冲：A/B 切换时换回它。
        x, _ = sf.read(path, dtype="float32", always_2d=True)
        self._source_data = x
        devs = enumerate_playback_devices()
        dev = next((d for d in devs if d.backend is Backend.NULL), devs[0])
        self._engine = PlaybackEngine(x, fs, device=dev, backend=Backend.NULL)
        self._engine.on_position(lambda pos, st: (self.view.set_playhead(pos),
                                                   self.set_time(pos, self._engine.transport.duration)))
        self._preview_path = None
        self._ab_b = False
        self.set_devices([d.name for d in devs], dev.name)
        self.set_time(0.0, self._engine.transport.duration)

    def _on_file_chosen(self, item) -> None:
        self.open_file(item.data(Qt.UserRole) or item.text())

    def add_files_dialog(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "打开音频", "", "音频 (*.flac *.wav *.aiff *.mp3 *.ogg)")
        for p in paths:
            self._add_file_row(p)

    def _add_file_row(self, path: str) -> None:
        from PySide6.QtCore import Qt as _Qt
        it = self._file_list.addItem(path)
        it.setData(_Qt.UserRole, path)

    # ---- 走带动作 ----
    def _on_play(self) -> None:
        if self._engine:
            self._engine.play()
            self.set_playing(self._engine.transport.is_playing)

    def _on_pause(self) -> None:
        if self._engine:
            self._engine.pause()
            self.set_playing(self._engine.transport.is_playing)

    def _on_stop(self) -> None:
        if self._engine:
            self._engine.stop()
            self.set_playing(False)

    def _on_device(self, name: str) -> None:
        # 暂只记；真实 WASAPI 设备切换在 P1 后续接入（NULL 后端不依赖）
        self._status("设备：%s" % name)

    def _audition_selection(self) -> None:
        """选区段试听：把当前矩形选区的时间窗 [t0,t1) 设为引擎 loop，并从 t0 起播。"""
        if self._engine is None:
            self._status("先载入文件")
            return
        sel = self.view.get_selection()
        if sel is None:
            self._status("先用选区工具框一段（试听需要时间窗）")
            return
        t0, t1 = sel[0], sel[1]
        if t1 - t0 < 1e-3:
            self._status("选区太短，无法试听")
            return
        ok = self._engine.set_loop(t0, t1)
        if not ok:
            self._status("试听选区设置失败")
            return
        self._engine.seek(t0)
        self._on_play()
        self._status("试听选区 %.3f–%.3f s（播完停在 t1）" % (t0, t1))

    def _audition_noise_segment(self) -> None:
        """试听**噪声段**（去噪的学谱输入）："混入内容"机器查不出来（统计量判据被实测否决：
        粉噪 14.6 dB 假高、平滑混噪 2.1 dB 假低，见 `denoise_noisecheck_probe`）——
        人耳一听便知。这是契约"噪声段必须只有噪声"的**人工复核入口**。
        """
        if self._engine is None:
            self._status("先载入文件")
            return
        ns = getattr(self.ops, "_noise_sel", None)
        if ns is None:
            self._status("还没有采集噪声段（框一段纯噪声 + 算子选'去噪'）")
            return
        t0, t1 = float(ns[0]), float(ns[1])
        if t1 - t0 < 1e-3:
            self._status("噪声段太短，无法试听")
            return
        if not self._engine.set_loop(t0, t1):
            self._status("试听噪声段设置失败")
            return
        self._engine.seek(t0)
        self._on_play()
        self._status("试听噪声段 %.3f–%.3f s（应只有噪声；听到内容=拿错了段）" % (t0, t1))

    def _on_seek(self, t: float) -> None:
        """点击时间轴定位播放头：引擎 clamp 到 [0,dur]；播放中实时跳播。"""
        if self._engine is None:
            return
        playing = self._engine.transport.is_playing
        self._engine.seek(t)
        if playing:
            # 播放中点击：重新定位并续播（NULL 后端即时生效）
            self._engine.play()
        self.set_time(self._engine.transport.position, self._engine.transport.duration)

    def _export_audio(self) -> None:
        """导出**处理后音频**：先选导出配方（容器/子类型/抖动，可存预设），再渲染落盘。

        **渲染在后台线程执行**（此前是同步调用主线程）：10 分钟素材整轨渲染实测
        约 24 秒（`packaging/band_perf.py`），同步跑会让窗口在这段时间完全不响应，
        Windows 直接给标题栏挂"未响应"。
        """
        if self._source is None or self.ops._session is None:
            self._status("没有可导出的工程")
            return
        if self._jobs.busy("export"):
            self._status("已有导出在进行中（可先取消）")
            return
        from .export_dialog import build_export_dialog, filter_for

        dlg = build_export_dialog(parent=self)
        if not dlg.exec():
            return
        cfg = dlg.result_cfg()
        path, _ = QFileDialog.getSaveFileName(
            self, "导出处理后音频", "", filter_for(cfg["fmt"]))
        if not path:
            return

        session = self.ops._session          # 捕获当前会话（避免深处 self 变更）
        subtype, dither = cfg["subtype"], cfg["dither"]

        def work(token, report):
            """后台作业体：**分段**推进，每段之间检查取消。

            为什么分段：`EditSession.render` 内部是一次性纯函数调用，
            无法中途打断。但混域栈会走前缀缓存逐级渲染——这里按"算子级"
            分段（每级之间 1..n/len），取消粒度是"一个算子"，语义诚实：
            不会假装能中断 STFT 内部，但用户不必等完整条链跑完。
            """
            token.check()
            report(10)
            audit = session.render(path, subtype=subtype, dither=dither,
                                   overwrite=True)
            report(90)
            token.check()
            return audit

        def ok(audit):
            nd = audit.get("changed_samples")
            n = audit.get("frames", audit.get("duration_s"))
            self._status("已导出：%s（%s ｜ %s%s）" % (
                path, subtype, "抖动" if dither else "无抖动",
                "｜改动样本 %s" % (nd if nd is not None else n)))

        def fail(msg):
            self._status("导出失败：%s" % msg)

        def cancelled():
            self._status("导出已取消（文件可能不完整）")

        self._status("导出中…")
        if self._jobs.submit(work, "export", on_progress=self._on_job_progress,
                             on_done=ok, on_failed=fail,
                             on_cancelled=cancelled) is None:
            self._status("导出已在队列中")

    def _on_job_progress(self, pct: int) -> None:
        """后台作业进度（已节流；这里只更新状态栏，不碰重活）。"""
        self._status("处理中… %d%%" % pct)

    def _on_run_batch(self, jobs: list[dict]) -> None:
        """批处理：把作业清单写盘，调 CLI 的 `--batch`（与命令行同一个实现，逐项隔离）。"""
        import json
        import tempfile

        if not jobs:
            self._status("批处理清单为空")
            return
        manifest = os.path.join(tempfile.gettempdir(), "_ac_batch_jobs.json")
        with open(manifest, "w", encoding="utf-8") as fh:
            json.dump(jobs, fh, ensure_ascii=False, indent=2)
        self._status("批处理中：%d 项…" % len(jobs))
        try:
            from audiocheck.edit_cli import main as edit_main
            rc = edit_main(["--batch", manifest])
        except Exception as e:                       # noqa: BLE001 —— CLI 抛的杂项
            self._status("批处理异常：%s" % e)
            return
        self._status("批处理完成（退出码 %d）：%d 项" % (rc, len(jobs)))

    def _export_audio_ab(self) -> None:
        """A/B 选源导出：A=原曲（copy 源），B=当前渲染产物。"""
        if self._source is None:
            self._status("先载入文件")
            return
        path, _ = QFileDialog.getSaveFileName(
            self, "导出（A/B 选源）", "", "WAV (*.wav)")
        if not path:
            return
        if self._ab_b and self._preview_path and os.path.exists(self._preview_path):
            import shutil
            shutil.copy(self._preview_path, path)
            self._status("已导出 B 面（处理后）：%s" % path)
        else:
            import shutil
            shutil.copy(self._source, path)
            self._status("已导出 A 面（原曲）：%s" % path)

    def _on_ab(self, on: bool) -> None:
        self._ab_b = on
        self.transport.btn_play.setText("A/B：处理后" if on else "A/B：原曲")
        if self._engine is None or self._source_data is None:
            self._status("A/B 模式：%s（无缓冲）" % ("看处理后" if on else "看原曲"))
            return
        if not on:
            # A 面：原曲缓冲（逐位不变，切回永不丢信息）
            self._engine.set_source_data(self._source_data, keep_position=True)
            self._status("A/B：原曲（未处理）")
            return
        # B 面：渲染预览；没渲染过就先渲染一次当前栈
        if self._preview_path is None:
            self.ops._render_and_emit()
        if self._preview_path and os.path.exists(self._preview_path):
            import soundfile as sf
            b, _ = sf.read(self._preview_path, dtype="float32", always_2d=True)
            self._engine.set_source_data(b, keep_position=True)
            self._status("A/B：处理后（%d 个操作）" % len(self.ops._session.ops))
        else:
            self._status("A/B：处理后（无渲染产物，请先应用算子）")

    def _on_status(self, msg: str) -> None:
        self._status(msg)

    def _export_report(self) -> None:
        """导出**审计报告**（Markdown 人可读）：最近一次渲染/预览的审计记录机械排版。

        与"导出项目记录"（可复算 JSON）配套：JSON 给程序复算，报告给人存档。
        没渲染过就先渲染一次当前栈（与 A/B 的 B 面同口径）。
        """
        if self._source is None or self.ops._session is None:
            self._status("没有可导出的工程")
            return
        audit = self.ops.last_audit()
        if audit is None:
            self.ops._render_and_emit()
            audit = self.ops.last_audit()
        if audit is None:
            self._status("还没有渲染产物（请先应用算子）")
            return
        from ...core.edit.report import audit_report

        path, _ = QFileDialog.getSaveFileName(
            self, "导出审计报告", "", "Markdown (*.md)")
        if not path:
            return
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(audit_report(audit))
        self._status("已导出审计报告：%s（%d 个操作，机械排版）" % (path, len(audit.get("ops", []))))

    def _export_project(self) -> None:
        """把当前操作栈导出成可复算 JSON（`Project.to_dict` + 源路径）。"""
        if self._source is None or self.ops._session is None:
            self._status("没有可导出的工程")
            return
        import json

        from ...core.edit.ops import Project

        path, _ = QFileDialog.getSaveFileName(
            self, "导出项目记录", "", "JSON (*.json)")
        if not path:
            return
        proj = Project(self._source, list(self.ops._session.ops))
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(proj.to_dict(), fh, ensure_ascii=False, indent=2)
        self._status("已导出项目记录：%s（%d 个操作）" % (path, len(proj.ops)))

    def _status(self, msg: str) -> None:
        # 复用检视面板的审计区状态行（轻量）
        self.inspector_panel.set_audit({"status": msg})

    def _save_chain(self) -> None:
        """存**修复链**（只存操作栈、不绑源——配方，可套到任何源）。"""
        if self.ops._session is None or not self.ops._session.ops:
            self._status("没有可保存的操作栈")
            return
        from ...core.edit.ops import save_chain

        path, _ = QFileDialog.getSaveFileName(self, "存修复链", "", "修复链 (*.json)")
        if not path:
            return
        save_chain(path, list(self.ops._session.ops))
        self._status("已存修复链：%s（%d 个操作，不绑源）" % (path, len(self.ops._session.ops)))

    def _load_chain(self) -> None:
        """载**修复链**：逐个 `apply` 进当前会话（每步可撤销），然后渲染预览。"""
        if self._source is None or self.ops._session is None:
            self._status("先载入文件")
            return
        from ...core.edit.ops import load_chain

        path, _ = QFileDialog.getOpenFileName(self, "载修复链", "", "修复链 (*.json)")
        if not path:
            return
        try:
            ops = load_chain(path)
        except (OSError, ValueError) as e:
            self._status("修复链读不了：%s" % e)
            return
        for op in ops:
            self.ops._session.apply(op)
        self.ops._render_and_emit()

    # ---- 命令面板 ----
    def _build_commands(self) -> None:
        self._commands = [
            ("打开文件", "加音频到工程", self.add_files_dialog),
            ("播放 / 暂停", "空格", lambda: (self._on_play() if not (self._engine and self._engine.transport.is_playing)
                                            else self._on_pause())),
            ("停止", "回到起点", self._on_stop),
            ("撤销", "Ctrl+Z", self._on_undo),
            ("重做", "Ctrl+Y", self._on_redo),
            ("选区工具", "开关矩形选区", lambda: self.transport.btn_sel.toggle()),
            ("套索工具", "自由多边形选区（拖一圈松手）", lambda: self.transport.btn_lasso.toggle()),
            ("放大", "编辑视图", self.view.zoom_in),
            ("缩小", "编辑视图", self.view.zoom_out),
            ("复位缩放", "编辑视图", self.view.zoom_reset),
            ("选段试听", "只听框选段", self._audition_selection),
            ("试听噪声段", "去噪学谱输入", self._audition_noise_segment),
            ("去削波", "修复算子", lambda: self._choose_op("declip")),
            ("去咔哒", "修复算子", lambda: self._choose_op("declick")),
            ("去嗡", "修复算子", lambda: self._choose_op("dehum")),
            ("去噪", "修复算子", lambda: self._choose_op("denoise")),
            ("时频增益", "修复算子", lambda: self._choose_op("band_gain")),
            ("导出项目记录", "JSON 可复算", self._export_project),
            ("导出审计报告", "Markdown 人可读", self._export_report),
            ("导出处理后音频", "渲染当前栈", self._export_audio),
            ("导出 A/B 选源", "原曲/处理后", self._export_audio_ab),
            ("存算子预设", "命名配置", lambda: self.ops._save_preset()),
            ("载算子预设", "命名配置", lambda: self.ops._load_presets()),
            ("存修复链", "无源配方 JSON", self._save_chain),
            ("载修复链", "套到当前源", self._load_chain),
        ]

    def _on_undo(self) -> None:
        self.ops._undo()

    def _on_redo(self) -> None:
        self.ops._redo()

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_Z and (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._on_undo()
            return
        if ev.key() == Qt.Key.Key_Y and (ev.modifiers() & Qt.KeyboardModifier.ControlModifier):
            self._on_redo()
            return
        if ev.key() == Qt.Key.Key_Space:
            (self._on_play() if not (self._engine and self._engine.transport.is_playing)
             else self._on_pause())
            return
        super().keyPressEvent(ev)

    def _choose_op(self, kind: str) -> None:
        idx = self.ops.kind.findData(kind)
        if idx >= 0:
            self.ops.kind.setCurrentIndex(idx)

    def open_command_palette(self) -> None:
        if self._palette is None:
            self._palette = CommandPalette(self, self._commands, self._theme)
        self._palette.show()

    # ---- 主题 ----
    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.setStyleSheet(T.qss(t))
        self.transport._apply_theme(t)
        self.view.apply_theme(t)
        self.ops.apply_theme(t)
        self.inspector_panel.apply_theme(t)
        for d in (self.files, self.editor, self.inspector):
            d.setStyleSheet(T.qss(t))

    def set_devices(self, names: list[str], current: str | None = None) -> None:
        self.transport.set_devices(names, current)

    def set_time(self, pos: float, dur: float) -> None:
        self.transport.set_time(pos, dur)

    def set_playing(self, playing: bool) -> None:
        self.transport.set_playing(playing)
