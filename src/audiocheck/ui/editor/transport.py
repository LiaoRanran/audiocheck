"""走带条（TransportBar）：REAPER 式全局播放控制。

复用设计系统：语义色走 `palette` 生成、间距走 `Space` 令牌、图标走既有字体解析。
本控件不持有播放引擎——它只暴露信号，由 `EditorWindow` 把信号接到
`core/edit/playback.PlaybackEngine`。这样走带条可 offscreen 构造、可 headless 测。
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QWidget,
)

from .. import theme as T


class TransportBar(QFrame):
    """全局走带控制条。

    信号（EditorWindow 接引擎）：
    - playClicked / pauseClicked / stopClicked
    - deviceChanged(name)
    - selectionToolToggled(on)
    - operatorChosen(kind)        # 修复算子下拉：declip/declick/dehum/denoise/gain
    - zoomIn / zoomOut / zoomReset
    """

    playClicked = Signal()
    pauseClicked = Signal()
    stopClicked = Signal()
    deviceChanged = Signal(str)
    selectionToolToggled = Signal(bool)
    lassoToggled = Signal(bool)
    operatorChosen = Signal(str)
    zoomIn = Signal()
    zoomOut = Signal()
    zoomReset = Signal()
    loopAuditionClicked = Signal()

    def __init__(self, theme: T.Theme, parent: QWidget | None = None):
        super().__init__(parent)
        self._theme = theme
        self.setObjectName("TransportBar")
        self.setFrameShape(QFrame.NoFrame)
        self._devices: list[str] = []

        row = QHBoxLayout(self)
        row.setContentsMargins(T.Space.md, T.Space.xs, T.Space.md, T.Space.xs)
        row.setSpacing(T.Space.sm)

        # 走带按钮（StrongFocus，沿用旧 a11y 纪律）
        self.btn_play = self._btn("▶", self.tr("播放"), "play")
        self.btn_pause = self._btn("⏸", self.tr("暂停"), "pause")
        self.btn_stop = self._btn("⏹", self.tr("停止"), "stop")
        self.btn_play.clicked.connect(self.playClicked)
        self.btn_pause.clicked.connect(self.pauseClicked)
        self.btn_stop.clicked.connect(self.stopClicked)

        # 时间读数
        self.lbl_time = QLabel("00:00.000 / 00:00.000")
        self.lbl_time.setObjectName("TransportTime")
        row.addWidget(self.lbl_time)

        row.addStretch(1)

        # 缩放（编辑视图）
        self.btn_zin = self._btn("＋", self.tr("放大"), "zin")
        self.btn_zout = self._btn("－", self.tr("缩小"), "zout")
        self.btn_zreset = self._btn("⤢", self.tr("复位缩放"), "zreset")
        self.btn_zin.clicked.connect(self.zoomIn)
        self.btn_zout.clicked.connect(self.zoomOut)
        self.btn_zreset.clicked.connect(self.zoomReset)

        # 选区工具
        self.btn_sel = self._btn("▭", self.tr("选区工具"), "sel")
        self.btn_sel.setCheckable(True)
        self.btn_sel.toggled.connect(self.selectionToolToggled)
        self.btn_lasso = self._btn("◌", self.tr("套索工具（自由多边形选区）"), "lasso")
        self.btn_lasso.setCheckable(True)
        self.btn_lasso.toggled.connect(self.lassoToggled)
        # 互斥：开套索自动关矩形选区，反之亦然（一次只有一个活动选区工具）
        self.btn_sel.toggled.connect(lambda on: on and self.btn_lasso.setChecked(False))
        self.btn_lasso.toggled.connect(lambda on: on and self.btn_sel.setChecked(False))

        # 修复算子下拉
        self.op_combo = QComboBox()
        self.op_combo.setObjectName("OpCombo")
        self.op_combo.setAccessibleName("修复算子")
        for kind, label in (
            ("declip", "去削波"),
            ("declick", "去咔哒"),
            ("dehum", "去嗡"),
            ("denoise", "去噪"),
            ("gain", "增益"),
        ):
            self.op_combo.addItem(label, kind)
        self.op_combo.currentIndexChanged.connect(
            lambda _i: self.operatorChosen.emit(self.op_combo.currentData() or "declip")
        )
        row.addWidget(self.op_combo)

        # 设备选择
        self.dev_combo = QComboBox()
        self.dev_combo.setObjectName("DeviceCombo")
        self.dev_combo.setAccessibleName("播放设备")
        self.dev_combo.setMinimumWidth(200)
        self.dev_combo.currentIndexChanged.connect(
            lambda _i: self.deviceChanged.emit(self.dev_combo.currentText())
        )
        row.addWidget(self.dev_combo)

        # 选区段试听（REAPER 式：框一段→只听这段）
        self.btn_loop = self._btn("↻", "试听选区", "loop")
        self.btn_loop.clicked.connect(self.loopAuditionClicked)
        row.addWidget(self.btn_loop)

        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self._apply_theme(theme)

    # ---- 对外 ----
    def set_devices(self, names: list[str], current: str | None = None) -> None:
        self._devices = list(names)
        self.dev_combo.blockSignals(True)
        self.dev_combo.clear()
        self.dev_combo.addItems(names)
        if current and current in names:
            self.dev_combo.setCurrentText(current)
        self.dev_combo.blockSignals(False)

    def set_time(self, pos: float, dur: float) -> None:
        self.lbl_time.setText("%s / %s" % (self._fmt(pos), self._fmt(dur)))

    def set_playing(self, playing: bool) -> None:
        self.btn_play.setChecked(playing)

    # ---- 内部 ----
    def _btn(self, text: str, tip: str, name: str) -> QPushButton:
        b = QPushButton(text)
        b.setObjectName("Transport" + name.capitalize())
        b.setToolTip(tip)
        b.setFocusPolicy(Qt.StrongFocus)
        b.setFixedSize(34, 34)
        return b

    @staticmethod
    def _fmt(sec: float) -> str:
        if sec < 0:
            sec = 0.0
        m = int(sec // 60)
        s = sec - m * 60
        return "%02d:%06.3f" % (m, s)

    def _apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.setStyleSheet(T.qss(t))
