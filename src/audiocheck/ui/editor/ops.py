"""算子面板 + 编辑会话控制器：把"选区 + 算子种类"变成可审计的修复。

往极致做，但不绕开内核纪律：
- 去削波要 `bandwidth` —— 由测量层 `spectrum.content_bandwidth` 读出（砖墙截止；
  量不到 ⇒ 奈奎斯特 ⇒ 退化成逐位不变），`bandwidth_source` 记账；
- 去嗡要 `f0` —— 由 `analyzers.hum.detect_hum` 从稳态证据读出；证据不足就拒绝（不猜）；
- 去噪要噪声段 —— GUI 上用**第二段选区**（"噪声段"模式）采集，必须给；
- 时频选区增益 `BandGainOp` 直接吃矩形选区的 `[t0,t1]×[f0,f1]`（频谱图上刷的典型操作）。

每一步都落到 `EditSession`（非破坏栈）+ `render`（返回审计记录）。
A/B：渲染结果存内存，与原曲并存，检视面板一键切换。
"""

from __future__ import annotations

import os
import tempfile

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...core import edit  # noqa: F401  (确保包可见)
from ...core.analyzers.hum import detect_hum
from ...core.analyzers.spectrum import content_bandwidth
from ...core.context import io_path
from ...core.edit.ops import (
    BandGainOp,
    DeclickOp,
    DeclipOp,
    DehumOp,
    DenoiseOp,
    PolyGainOp,
)
from ...core.edit.session import EditSession
from .. import theme as T


class OperatorPanel(QFrame):
    """修复算子面板：选种类 → 填参数（部分自动）→ 应用到选区。"""

    applied = Signal(object)          # EditSession（渲染后的最新状态）
    auditReady = Signal(dict)         # 最后一次渲染审计
    status = Signal(str)              # 状态/错误文案

    def __init__(self, theme: T.Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("OperatorPanel")
        self._session: EditSession | None = None
        self._source: str | None = None
        self._fs: int = 44100
        self._noise_sel: tuple[float, float] | None = None  # (t0,t1) 噪声段

        v = QVBoxLayout(self)
        v.setContentsMargins(T.Space.sm, T.Space.sm, T.Space.sm, T.Space.sm)
        v.setSpacing(T.Space.sm)

        # 算子种类
        row = QHBoxLayout()
        row.setSpacing(T.Space.sm)
        row.addWidget(QLabel("算子"))
        self.kind = QComboBox()
        for k, lbl in (("band_gain", "时频增益"), ("poly_gain", "多边形增益"),
                       ("declip", "去削波"), ("declick", "去咔哒"), ("dehum", "去嗡"),
                       ("denoise", "去噪")):
            self.kind.addItem(lbl, k)
        self.kind.currentTextChanged.connect(self._on_kind)
        row.addWidget(self.kind, 1)
        v.addLayout(row)

        # 增益（band_gain）
        self.gain_row = QWidget()
        gr = QHBoxLayout(self.gain_row)
        gr.setContentsMargins(0, 0, 0, 0)
        gr.setSpacing(T.Space.sm)
        gr.addWidget(QLabel("增益 dB"))
        self.gain_db = QDoubleSpinBox()
        self.gain_db.setRange(-24.0, 24.0)
        self.gain_db.setValue(-3.0)
        self.gain_db.setSuffix(" dB")
        gr.addWidget(self.gain_db)
        v.addWidget(self.gain_row)

        # 去削波带宽来源提示
        self.bw_hint = QLabel("去削波：带宽自动从内容截止点读取")
        v.addWidget(self.bw_hint)

        # 去嗡 f0
        self.hum_row = QWidget()
        hr = QHBoxLayout(self.hum_row)
        hr.setContentsMargins(0, 0, 0, 0)
        hr.setSpacing(T.Space.sm)
        hr.addWidget(QLabel("嗡 f0 Hz"))
        self.f0 = QDoubleSpinBox()
        self.f0.setRange(20.0, 200.0)
        self.f0.setValue(50.0)
        hr.addWidget(self.f0)
        self.btn_detect_hum = QPushButton("自动检测")
        self.btn_detect_hum.clicked.connect(self._detect_hum)
        hr.addWidget(self.btn_detect_hum)
        v.addWidget(self.hum_row)

        # 去噪噪声段
        self.noise_row = QWidget()
        nr = QHBoxLayout(self.noise_row)
        nr.setContentsMargins(0, 0, 0, 0)
        nr.setSpacing(T.Space.sm)
        nr.addWidget(QLabel("噪声段"))
        self.noise_lbl = QLabel("未采集（用选区工具选一段纯噪声）")
        self.track_floor = QCheckBox("底噪跟踪（噪声床台阶时逐帧跟随；稳态代价 ≤0.7 dB）")
        self.track_floor.setChecked(True)
        self.track_floor.setObjectName("TrackFloor")
        nr.addWidget(self.noise_lbl, 1)
        nr.addWidget(self.track_floor)
        self.btn_grab_noise = QPushButton("采集当前选区为噪声段")
        self.btn_grab_noise.clicked.connect(self._grab_noise)
        nr.addWidget(self.btn_grab_noise)
        v.addWidget(self.noise_row)

        # 动作
        act = QHBoxLayout()
        act.setSpacing(T.Space.sm)
        self.btn_apply = QPushButton("应用到选区")
        self.btn_apply.clicked.connect(self._apply)
        self.btn_undo = QPushButton("撤销")
        self.btn_undo.clicked.connect(self._undo)
        self.btn_redo = QPushButton("重做")
        self.btn_redo.clicked.connect(self._redo)
        act.addWidget(self.btn_apply)
        act.addWidget(self.btn_undo)
        act.addWidget(self.btn_redo)
        v.addLayout(act)

        # 预设：命名算子配置（落盘 JSON，可复算）
        pres = QHBoxLayout()
        pres.setSpacing(T.Space.sm)
        self.btn_save_preset = QPushButton("存预设")
        self.btn_save_preset.clicked.connect(self._save_preset)
        self.preset_name = QLineEdit("我的预设")
        self.preset_name.setObjectName("PresetName")
        self.btn_load_presets = QPushButton("载预设")
        self.btn_load_presets.clicked.connect(self._load_presets)
        pres.addWidget(self.preset_name)
        pres.addWidget(self.btn_save_preset)
        pres.addWidget(self.btn_load_presets)
        v.addLayout(pres)

        self._on_kind()

    # ---- 外部 ----
    def load_source(self, path: str, fs: int) -> None:
        self._source = path
        self._fs = fs
        self._session = EditSession(path)
        self._noise_sel = None
        self.noise_lbl.setText("未采集（用选区工具选一段纯噪声）")
        self.applied.emit(self._session)

    def set_selection(self, t0: float, t1: float, f0: float, f1: float) -> None:
        self._sel = (t0, t1, f0, f1)
        kind = self.kind.currentData()
        if kind == "denoise":
            self._noise_sel = (t0, t1)
            self.noise_lbl.setText("噪声段 %.2f–%.2f s（去噪用此段学谱）" % (t0, t1))

    def set_poly_selection(self, points: list) -> None:
        """套索顶点（view 的 polySelected）：存起来给"多边形增益"用，标签即时可读。"""
        self._poly = [(float(t), float(f)) for t, f in points]
        self.noise_lbl.setText("多边形 %d 顶点（'多边形增益'将作用其内部）" % len(self._poly))

    def _on_kind(self) -> None:
        kind = self.kind.currentData()
        self.gain_row.setVisible(kind in ("band_gain", "poly_gain"))
        self.bw_hint.setVisible(kind == "declip")
        self.hum_row.setVisible(kind == "dehum")
        self.noise_row.setVisible(kind == "denoise")

    # ---- 测量驱动 ----
    def _detect_hum(self) -> None:
        if not self._source:
            return
        from ...core.context import AnalysisContext, io_path
        ctx = AnalysisContext(path=io_path(self._source), deep=False, src_path=self._source)
        x, _fs = ctx.segment(seconds=6.0)
        if x is None:
            self.status.emit("素材太短，取不到 6 s 检测段")
            return
        ev = detect_hum(x, _fs, ctx.thresholds)
        if ev.get("accepted"):
            self.f0.setValue(float(ev["f0_hz"]))
            self.status.emit("嗡自动检测：%s" % ev.get("reason", ""))
        else:
            self.status.emit("嗡证据不足，未自动设置：%s" % ev.get("reason", ""))

    def _grab_noise(self) -> None:
        if getattr(self, "_sel", None) is None:
            self.status.emit("先用选区工具框一段纯噪声")
            return
        t0, t1 = self._sel[0], self._sel[1]
        self._noise_sel = (t0, t1)
        self.noise_lbl.setText("噪声段 %.2f–%.2f s" % (t0, t1))

    def _measure_bandwidth(self, t0: float, t1: float) -> tuple[float, str]:
        """读内容带宽 B（Hz）+ 来源。量不到 ⇒ 奈奎斯特 ⇒ 逐位不变。

        走**边界层**唯一出口 `content_bandwidth`（口径写在测量层，不在此抄），
        与 `edit_cli._auto_bandwidth` 完全一致：用 `AnalysisContext.spectrum()` 的
        三分位谱，太短就明拒（不猜 B）。
        """
        if not self._source:
            return self._fs / 2.0, "auto_nyquist"
        try:
            from ...core.context import AnalysisContext
            ctx = AnalysisContext(path=io_path(self._source), deep=False, src_path=self._source)
            f, db = ctx.spectrum()
            if f is None:
                return self._fs / 2.0, "auto_nyquist"
            info = content_bandwidth(f, db, ctx.fs, ctx.thresholds)
            return float(info["bandwidth_hz"]), "auto_" + info["source"]
        except ValueError:
            # 素材太短量不出 canonical 谱：边界层明确拒绝，GUI 这里落到奈奎斯特
            return self._fs / 2.0, "auto_nyquist"

    # ---- 应用 ----
    def _apply(self) -> None:
        kind = self.kind.currentData()
        has_poly = kind == "poly_gain" and bool(getattr(self, "_poly", None))
        if self._session is None or (getattr(self, "_sel", None) is None and not has_poly):
            self.status.emit("先载入文件并框选区（多边形增益用套索工具圈选）")
            return
        t0, t1, f0, f1 = self._sel if getattr(self, "_sel", None) is not None \
            else (0.0, 0.0, 0.0, 0.0)      # poly 分支不用矩形坐标；占位保持解包形状
        try:
            if kind == "band_gain":
                op = BandGainOp(t0=t0, t1=t1, f0=f0, f1=f1, gain_db=self.gain_db.value())
            elif kind == "poly_gain":
                poly = getattr(self, "_poly", None)
                if not poly or len(poly) < 3:
                    self.status.emit("多边形增益需要先用套索工具圈一片（拖一圈松手）")
                    return
                op = PolyGainOp(points=poly, gain_db=self.gain_db.value())
            elif kind == "declip":
                B, src = self._measure_bandwidth(t0, t1)
                op = DeclipOp(t0=t0, t1=t1, bandwidth=B, bandwidth_source=src)
            elif kind == "declick":
                op = DeclickOp(t0=t0, t1=t1)
            elif kind == "dehum":
                op = DehumOp(t0=t0, t1=t1, f0=self.f0.value(), f0_source="given")
            elif kind == "denoise":
                if self._noise_sel is None:
                    self.status.emit("去噪必须先采集噪声段")
                    return
                op = DenoiseOp(t0=t0, t1=t1,
                               noise_t0=self._noise_sel[0], noise_t1=self._noise_sel[1],
                               track_floor=self.track_floor.isChecked())
            else:
                return
            self._session.apply(op)
            self._render_and_emit()
        except ValueError as e:
            self.status.emit("拒绝：%s" % e)

    def _render_and_emit(self) -> None:
        assert self._session is not None and self._source is not None
        dest = os.path.join(tempfile.gettempdir(),
                            "_ac_edit_preview_%d.wav" % id(self._session))
        audit = self._session.render(dest, overwrite=True)
        self._last_audit = audit
        self.auditReady.emit(audit)
        self.applied.emit(self._session)
        self.status.emit("已应用：%s（%d 个操作）" % (
            self._session.ops[-1].describe() if self._session.ops else "",
            len(self._session.ops)))

    def _undo(self) -> None:
        if self._session and self._session.undo():
            self._render_and_emit()
        else:
            self.status.emit("无可撤销")

    def _redo(self) -> None:
        if self._session and self._session.redo():
            self._render_and_emit()
        else:
            self.status.emit("无可重做")

    def last_audit(self):
        return getattr(self, "_last_audit", None)

    # ---- 算子预设（命名配置，落盘 JSON，可复算）----
    def current_preset(self) -> dict:
        """把当前算子种类 + 面板参数抽成一份预设 dict（与 `Project` 算子字段对齐的口径）。"""
        kind = self.kind.currentData() or "band_gain"
        cfg = {"kind": kind}
        if kind == "band_gain":
            cfg["gain_db"] = self.gain_db.value()
        elif kind == "dehum":
            cfg["f0"] = self.f0.value()
        # declip 的带宽靠测量层自动读、declick/denoise 走默认 ⇒ 不写死
        return cfg

    def apply_preset(self, cfg: dict) -> None:
        """把一个预设灌进面板（只认面板能表达的字段；其余留给自动/默认）。"""
        kind = cfg.get("kind", "band_gain")
        idx = self.kind.findData(kind)
        if idx < 0:
            return
        self.kind.setCurrentIndex(idx)
        if kind == "band_gain" and "gain_db" in cfg:
            self.gain_db.setValue(float(cfg["gain_db"]))
        elif kind == "dehum" and "f0" in cfg:
            self.f0.setValue(float(cfg["f0"]))

    def save_preset(self, name: str, path: str) -> None:
        """把当前面板参数存成命名预设（追加到 JSON 文件，覆盖同名）。"""
        import json

        presets: dict = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as fh:
                    presets = json.load(fh)
            except (OSError, ValueError, json.JSONDecodeError):
                presets = {}
        if not isinstance(presets, dict):
            presets = {}
        presets[name] = self.current_preset()
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(presets, fh, ensure_ascii=False, indent=2)

    def load_presets(self, path: str) -> dict:
        """读一个预设文件，返回 {name: cfg}（读不了返回空 dict，不抛）。"""
        import json

        if not os.path.exists(path):
            return {}
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_preset(self) -> None:
        if not self._source:
            self.status.emit("先载入文件")
            return
        # 预设存到临时目录下的 presets.json（用户可读可改，跨会话）
        path = os.path.join(tempfile.gettempdir(), "audiocheck_presets.json")
        self.save_preset(self.preset_name.text().strip() or "未命名", path)
        self.status.emit("已存预设：%s" % (self.preset_name.text().strip() or "未命名"))

    def _load_presets(self) -> None:
        path = os.path.join(tempfile.gettempdir(), "audiocheck_presets.json")
        presets = self.load_presets(path)
        if not presets:
            self.status.emit("没有已存预设")
            return
        # 载入命名框里写的优先；否则取第一个
        name = self.preset_name.text().strip()
        if name in presets:
            self.apply_preset(presets[name])
            self.status.emit("已载入预设：%s" % name)
        else:
            first = next(iter(presets))
            self.apply_preset(presets[first])
            self.status.emit("已载入预设：%s" % first)

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.setStyleSheet(T.qss(t))
