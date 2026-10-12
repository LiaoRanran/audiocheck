"""界面控件库。

约定：所有控件都从 `theme` 取色与尺寸，不自带硬编码色值；
需要随主题变化的控件实现 `apply_theme(theme)`。
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
    QAbstractAnimation,
    QEasingCurve,
    QPropertyAnimation,
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QConicalGradient, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import theme as T

# ------------------------------------------------------------------ 数值格式化

def fmt_num(v, unit: str = "", digits: int = 1, signed: bool = False) -> str:
    if v is None:
        return "—"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, (int, float)):
        spec = "%+.*f" if signed else "%.*f"
        return (spec % (digits, float(v))) + unit
    return "%s%s" % (v, unit)


def human_khz(hz) -> str:
    if hz is None:
        return "—"
    hz = float(hz)
    return "%.1f Hz" % hz if hz < 1000 else "%.2f kHz" % (hz / 1000)


def ratio_pct(x) -> str:
    try:
        return "%.0f%%" % (float(x) * 100)
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------ 导航

class NavButton(QToolButton):
    """侧边栏导航项：图标 + 文字，选中时高亮。"""

    def __init__(self, key: str, label: str, icon_name: str, theme: T.Theme):
        super().__init__()
        self.key = key
        self._label = label
        self._icon_name = icon_name
        self.setObjectName("NavItem")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.apply_theme(theme)

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        active = self.property("active") is True
        col = t.p.primary_hi if active else t.p.text_dim
        self.setIcon(T.icon(self._icon_name, col, 17))
        self.setText(self._label)
        self.setProperty("active", active)
        self.style().unpolish(self)
        self.style().polish(self)

    def set_active(self, on: bool) -> None:
        self.setProperty("active", bool(on))
        self.apply_theme(self._theme)


class SideBar(QFrame):
    """左侧导航：品牌区 + 导航列表 + 底部操作。"""

    navigate = Signal(str)

    def __init__(self, theme: T.Theme):
        super().__init__()
        self.setObjectName("SideBar")
        self.setFixedWidth(196)
        self._theme = theme
        self._items: dict[str, NavButton] = {}

        v = QVBoxLayout(self)
        v.setContentsMargins(12, 16, 12, 12)
        v.setSpacing(4)

        brand = QHBoxLayout()
        brand.setSpacing(8)
        self.brand_icon = QLabel()
        # 24 = 4×6，与品牌文字的行高对齐（图标需略大于字高，22→24 仍在视觉等高处）。
        self.brand_icon.setFixedSize(24, 24)
        brand.addWidget(self.brand_icon)
        box = QVBoxLayout()
        box.setSpacing(0)
        self.brand_name = QLabel("audiocheck")
        self.brand_name.setObjectName("BrandName")
        self.brand_sub = QLabel("音源检测")
        self.brand_sub.setObjectName("BrandSub")
        box.addWidget(self.brand_name)
        box.addWidget(self.brand_sub)
        brand.addLayout(box, 1)
        v.addLayout(brand)
        v.addSpacing(14)

        self.nav_host = QVBoxLayout()
        # 光学间距（theme.Space.thin）：导航按钮之间贴紧成一组，
        # 用 4px 会把「一列导航项」的整组感拆掉。不参与布局栅格。
        self.nav_host.setSpacing(2)
        v.addLayout(self.nav_host)
        v.addStretch(1)

        self.footer = QVBoxLayout()
        # 光学间距（theme.Space.thin）：底部两项同属一组，理由同上。
        self.footer.setSpacing(2)
        v.addLayout(self.footer)

    def add_section(self, text: str) -> None:
        lab = QLabel(text.upper())
        lab.setObjectName("NavSection")
        self.nav_host.addWidget(lab)

    def add_item(self, key: str, label: str, icon_name: str) -> NavButton:
        b = NavButton(key, label, icon_name, self._theme)
        b.clicked.connect(lambda: self.navigate.emit(key))
        self._items[key] = b
        self.nav_host.addWidget(b)
        return b

    def add_footer_item(self, key: str, label: str, icon_name: str) -> NavButton:
        b = NavButton(key, label, icon_name, self._theme)
        b.clicked.connect(lambda: self.navigate.emit(key))
        self._items[key] = b
        self.footer.addWidget(b)
        return b

    def set_active(self, key: str) -> None:
        for k, b in self._items.items():
            b.set_active(k == key)

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        self.brand_icon.setPixmap(T.make_pixmap("wave", t.p.primary, 22, 2.0))
        for b in self._items.values():
            b.apply_theme(t)


# ------------------------------------------------------------------ 命令面板

class CommandPalette(QDialog):
    """Ctrl+K 命令面板：模糊匹配，键盘直达任意操作。"""

    def __init__(self, parent: QWidget, commands, theme: T.Theme):
        super().__init__(parent, Qt.FramelessWindowHint | Qt.Popup)
        self.setObjectName("Palette")
        self._commands = list(commands)
        self._theme = theme
        self.setFixedWidth(520)
        self.setMaximumHeight(420)

        v = QVBoxLayout(self)
        v.setContentsMargins(8, 8, 8, 8)
        v.setSpacing(8)
        self.input = QLineEdit()
        self.input.setPlaceholderText("输入命令…（↑↓ 选择，Enter 执行，Esc 关闭）")
        self.input.textChanged.connect(self._filter)
        v.addWidget(self.input)
        self.list = QListWidget()
        self.list.setObjectName("PaletteList")
        self.list.itemActivated.connect(self._run)
        v.addWidget(self.list, 1)
        self._filter("")
        self.input.setFocus()

    def _filter(self, text: str) -> None:
        q = text.strip().lower()
        self.list.clear()
        for label, hint, fn in self._commands:
            hay = (label + " " + (hint or "") + " ").lower()
            if not q or self._fuzzy(q, hay):
                it = QListWidgetItem("%s    %s" % (label, hint or ""))
                it.setData(Qt.UserRole, fn)
                self.list.addItem(it)
        if self.list.count():
            self.list.setCurrentRow(0)

    @staticmethod
    def _fuzzy(q: str, hay: str) -> bool:
        """子序列匹配：'exht' 能命中 'Export HTML'。"""
        i = 0
        for ch in hay:
            if i < len(q) and ch == q[i]:
                i += 1
        return i == len(q)

    def _run(self, item: QListWidgetItem) -> None:
        fn = item.data(Qt.UserRole)
        self.accept()
        if callable(fn):
            fn()

    def keyPressEvent(self, e):                         # noqa: N802
        if e.key() in (Qt.Key_Down,):
            self.list.setCurrentRow(min(self.list.currentRow() + 1, self.list.count() - 1))
        elif e.key() in (Qt.Key_Up,):
            self.list.setCurrentRow(max(self.list.currentRow() - 1, 0))
        elif e.key() in (Qt.Key_Return, Qt.Key_Enter):
            if self.list.currentItem():
                self._run(self.list.currentItem())
        elif e.key() == Qt.Key_Escape:
            self.reject()
        else:
            super().keyPressEvent(e)


# ------------------------------------------------------------------ Toast

class Toast(QFrame):
    def __init__(self, text: str, kind: str, theme: T.Theme):
        super().__init__()
        self.setObjectName("Toast")
        col = {"info": theme.p.info, "ok": theme.p.success,
               "warn": theme.p.warning, "bad": theme.p.danger}.get(kind, theme.p.info)
        h = QHBoxLayout(self)
        h.setContentsMargins(16, 8, 16, 8)
        dot = QLabel()
        pm = T.make_pixmap("shield", col, 16, 1.8)
        dot.setPixmap(pm)
        h.addWidget(dot)
        lab = QLabel(text)
        lab.setWordWrap(True)
        h.addWidget(lab, 1)
        self.setStyleSheet(
            "#Toast{background:%s;border:1px solid %s;border-radius:%dpx}"
            % (theme.p.surface_raised, theme.p.border_hi, theme.radius.lg))


class ToastHost(QWidget):
    """右下角浮层通知。鼠标穿透，不挡交互。"""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 16, 16)
        self._lay.setSpacing(8)
        self._lay.addStretch(1)
        self._theme = T.DARK

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t

    def notify(self, text: str, kind: str = "info", ms: int = 2800) -> None:
        t = Toast(text, kind, self._theme)
        t.setMaximumWidth(340)
        self._lay.insertWidget(self._lay.count() - 1, t)
        anim = QPropertyAnimation(t, b"windowOpacity", self)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setDuration(160)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        # 动画必须**用完即毁**：它以 ToastHost 为父对象，若只 start() 就会被挂在
        # 组件树上永不回收，而它还握着 target（弹窗控件）→ 每次通知泄漏
        # 两个动画 + 一个弹窗。开着界面跑一晚上分析，光提示就能攒出上千个对象。
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)
        QTimer.singleShot(ms, lambda: self._dismiss(t))

    def _dismiss(self, t: Toast) -> None:
        anim = QPropertyAnimation(t, b"windowOpacity", self)
        anim.setStartValue(1.0)
        anim.setEndValue(0.0)
        anim.setDuration(180)
        anim.finished.connect(lambda: (self._lay.removeWidget(t), t.deleteLater()))
        anim.start(QAbstractAnimation.DeletionPolicy.DeleteWhenStopped)

    def sync_geometry(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())


class SeedDialog(QDialog):
    """从种子色生成整套主题：预设 / 手输 / 深浅切换，**并实时预览生成结果**。

    为什么要能预览：这套配色是**算出来的**，不是挑出来的。让用户先看到
    "这个种子色生成的正文对比度是多少"，比让他挑一个好看的色块更有用——
    也顺手把"配色要达标"这件事变成看得见的界面。
    """

    PRESETS = (("#3d7dff", "蓝"), ("#00b4d8", "青"), ("#35c46a", "绿"),
               ("#e0a92e", "琥珀"), ("#f0524a", "砖红"), ("#8b5cf6", "紫"),
               ("#e879a6", "粉"), ("#5b6478", "石墨"))
    SWATCHES = ("bg", "surface", "border", "text", "primary", "success", "warning", "danger")

    def __init__(self, parent, theme: T.Theme, seed: str = "#3d7dff", mode: str = "dark"):
        super().__init__(parent)
        self.setWindowTitle("从种子色生成主题")
        self.setMinimumWidth(460)
        self._mode = mode
        v = QVBoxLayout(self)
        v.setSpacing(12)

        g = QGridLayout()
        g.setSpacing(8)
        self.btns = []
        for i, (hexv, name) in enumerate(self.PRESETS):
            b = QPushButton(name)
            b.setFixedHeight(32)        # 4 的倍数：`tests/test_ui_grid.py` 会查
            b.setCursor(Qt.PointingHandCursor)
            b.setStyleSheet("background:%s;color:%s;border:0;border-radius:6px"
                            % (hexv, "#0d0f13" if _luma(hexv) > 0.45 else "#ffffff"))
            b.clicked.connect(lambda _=False, h=hexv: (self.ed.setText(h), self._preview()))
            self.btns.append(b)
            g.addWidget(b, i // 4, i % 4)
        v.addLayout(g)

        row = QHBoxLayout()
        row.addWidget(QLabel("种子色"))
        self.ed = QLineEdit(seed)
        self.ed.setFixedWidth(112)
        self.ed.textChanged.connect(self._preview)
        row.addWidget(self.ed)
        self.cb_mode = QComboBox()
        self.cb_mode.addItems(["深色", "浅色"])
        self.cb_mode.setCurrentIndex(0 if mode == "dark" else 1)
        self.cb_mode.setFixedWidth(88)
        self.cb_mode.currentIndexChanged.connect(self._preview)
        row.addWidget(self.cb_mode)
        row.addStretch(1)
        v.addLayout(row)

        self.preview = QWidget()
        pv = QHBoxLayout(self.preview)
        pv.setSpacing(4)
        pv.setContentsMargins(0, 0, 0, 0)
        self.cells = []
        for role in self.SWATCHES:
            c = QLabel(role)
            c.setAlignment(Qt.AlignCenter)
            c.setFixedHeight(36)
            self.cells.append(c)
            pv.addWidget(c)
        v.addWidget(self.preview)

        self.lbl = QLabel("")
        self.lbl.setObjectName("Caption")
        self.lbl.setWordWrap(True)
        v.addWidget(self.lbl)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        v.addWidget(bb)
        self._preview()

    @property
    def mode(self) -> str:
        return "dark" if self.cb_mode.currentIndex() == 0 else "light"

    @property
    def seed(self) -> str:
        return self.ed.text().strip()

    def _preview(self) -> None:
        from .palette import audit, build

        try:
            pal = build(self.seed, self.mode)
        except Exception:                                      # noqa: BLE001
            self.lbl.setText("这个种子色生成不达标（或格式不对），换一个试试")
            return
        for role, cell in zip(self.SWATCHES, self.cells):
            bg = pal["surface"] if role in ("text", "border") else pal["bg"]
            cell.setStyleSheet("background:%s;color:%s;border-radius:5px"
                               % (pal[role] if role != "border" else pal["surface_hi"],
                                  pal["text"] if role != "text" else bg))
        # 只看**文字类**判据（要求 ≥4.5 的那些）：装饰性描边那条要求只有 1.0，
        # 混进"最弱一环"会让人以为配色不达标——报告口径要跟结论一致。
        text_findings = [f for f in audit(pal) if f.required >= 4.5]
        worst = min(text_findings, key=lambda f: f.ratio)
        self.lbl.setText("全部 %d 条 WCAG 判据通过 ✓ ｜ 文字类最弱一环 %.1f:1（%s，要求 %.1f）"
                         % (len(audit(pal)), worst.ratio, worst.pair.split(" / ")[0],
                            worst.required))


def _luma(hexv: str) -> float:
    """sRGB 相对亮度粗算（只用于决定按钮上该用白字还是墨字）。"""
    r, g, b = (int(hexv[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4  # noqa: E731
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


# ------------------------------------------------------------------ 空状态

class EmptyState(QWidget):
    """没有数据时的占位：图标 + 说明 + 主操作（不给用户一个"死界面"）。"""

    def __init__(self, theme: T.Theme, icon_name: str = "folder", title: str = "",
                 hint: str = "", action_text: str = "", on_action=None):
        super().__init__()
        v = QVBoxLayout(self)
        v.setSpacing(8)
        v.setAlignment(Qt.AlignCenter)
        self.icon = QLabel()
        self.icon.setAlignment(Qt.AlignCenter)
        v.addWidget(self.icon)
        self.title = QLabel(title)
        self.title.setAlignment(Qt.AlignCenter)
        self.title.setStyleSheet("font-size:%dpx;font-weight:700" % int(theme.type.body_md))
        v.addWidget(self.title)
        self.hint = QLabel(hint)
        self.hint.setAlignment(Qt.AlignCenter)
        self.hint.setWordWrap(True)
        self.hint.setObjectName("Caption")
        v.addWidget(self.hint)
        if action_text:
            btn = QPushButton(action_text)
            btn.setObjectName("Primary")
            btn.setFixedWidth(180)
            if on_action:
                btn.clicked.connect(on_action)
            v.addWidget(btn, 0, Qt.AlignCenter)
        self.apply_theme(theme, icon_name)

    def apply_theme(self, t: T.Theme, icon_name: str = "folder") -> None:
        self.icon.setPixmap(T.make_pixmap(icon_name, t.p.text_faint, 48, 1.4))
        self.title.setStyleSheet("font-size:%dpx;font-weight:700;color:%s"
                                 % (int(t.type.body_md), t.p.text))
        self.hint.setStyleSheet("color:%s;font-size:%dpx" % (t.p.text_faint, int(t.type.caption)))


# ------------------------------------------------------------------ 指标卡 / 仪表

class StatCard(QFrame):
    """紧凑指标卡：字段名 + 数值 + 单位。数值过长时省略，完整值给 tooltip。"""

    def __init__(self, theme: T.Theme):
        super().__init__()
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        m = theme.space
        v.setContentsMargins(m.md, m.sm, m.md, m.sm)
        # 光学间距（theme.Space.hair）：字段名与数值是一个整体，
        # 间距调到 4px 会把它们拆成两行信息，读者要多花一次眼动。
        v.setSpacing(m.hair)
        self.k = QLabel("—")
        self.v = QLabel("—")
        self.k.setObjectName("Caption")
        self.v.setObjectName("Hero")
        v.addWidget(self.k)
        v.addWidget(self.v)
        self.apply_theme(theme)

    def set(self, key: str, value: str, tooltip: str = "") -> None:
        self.k.setText(key)
        self.v.setText(value)
        self.setToolTip(tooltip or value)

    def apply_theme(self, t: T.Theme) -> None:
        self.k.setStyleSheet("color:%s;font-size:%dpx;letter-spacing:.6px"
                             % (t.p.text_faint, int(t.type.caption)))
        # 数值用等宽 QFont，**不靠 QSS 的 font-family**：
        # 部件自带 setStyleSheet 与 app 级 `QLabel#Hero` 规则并存时，
        # Qt 对各属性的合并行为并不明确，实测出现过等宽没生效的情况。
        # 另外这里原来写 font-weight:700 —— 中文字体只有 400/700 两档，
        # 600 会被吸附到 700 并打 findfont 告警，等于写了个假层级。
        self.v.setFont(T.mono_qfont(t.type.body_md))
        self.v.setStyleSheet("color:%s" % t.p.text)


class Gauge(QWidget):
    """270° 环形仪表：显示「真实截止 / 奈奎斯特」占比，颜色随结论变化。"""

    def __init__(self, size: int = 128, theme: T.Theme | None = None):
        super().__init__()
        self.setFixedSize(size, size)
        self.t = theme or T.DARK
        self.value: float | None = None
        self.level: str | None = None
        self.main = "—"
        self.sub = "真实截止"
        self.caption = ""
        # 环形进度的**缓动值**：数字跳变看不出"变了多少"，指针扫过去才看得出。
        # 走 Qt 属性动画（不碰 matplotlib）——画布重绘是按百毫秒计的，
        # 而这只小部件的动画每帧只是几个 drawArc，代价可以忽略。
        self._anim = 0.0
        self._ease = QPropertyAnimation(self, b"anim", self)
        self._ease.setDuration(430)
        self._ease.setEasingCurve(QEasingCurve.OutCubic)

    def _get_anim(self) -> float:
        return self._anim

    def _set_anim(self, v: float) -> None:
        self._anim = float(v)
        self.update()

    anim = Property(float, _get_anim, _set_anim)

    def set_data(self, ratio, level: str | None, main: str, sub: str, caption: str = "") -> None:
        self.value, self.level = ratio, level
        self.main, self.sub, self.caption = main, sub, caption
        target = float(ratio) if ratio else 0.0
        self._ease.stop()
        self._ease.setStartValue(self._anim)
        self._ease.setEndValue(target)
        self._ease.start()
        self.update()

    def apply_theme(self, t: T.Theme) -> None:
        self.t = t
        self.update()

    def paintEvent(self, _):                           # noqa: N802
        p, pr = self.t.p, QPainter(self)
        pr.setRenderHint(QPainter.Antialiasing, True)
        w, h = self.width(), self.height()
        thick = 9.0
        pad = thick / 2 + 3
        rect = QRectF(pad, pad, w - 2 * pad, h - 2 * pad)
        start, span = 225.0, -270.0

        pr.setPen(QPen(QColor(p.surface_hi), thick, Qt.SolidLine, Qt.RoundCap))
        pr.drawArc(rect, int(start * 16), int(span * 16))

        if self.value is not None:
            col = QColor(p.level_color(self.level or "info"))
            grad = QConicalGradient(rect.center(), 225)
            grad.setColorAt(0.0, col.lighter(125))
            grad.setColorAt(0.5, col)
            grad.setColorAt(1.0, col.darker(115))
            pr.setPen(QPen(grad, thick, Qt.SolidLine, Qt.RoundCap))
            # 用缓动值画弧（`_anim`），文字仍用真实值——读数不能跟着动画变
            frac = max(0.0, min(1.0, self._anim))
            pr.drawArc(rect, int(start * 16), int(span * frac * 16))

        f = QFont(self.font())
        f.setPointSizeF(self.t.type.heading - 4.5)
        f.setBold(True)
        pr.setFont(f)
        pr.setPen(QColor(p.text))
        pr.drawText(QRectF(0, h * 0.30, w, h * 0.30), Qt.AlignCenter, self.main)
        f.setPointSizeF(self.t.type.caption)
        f.setBold(False)
        pr.setFont(f)
        pr.setPen(QColor(p.text_faint))
        pr.drawText(QRectF(0, h * 0.55, w, h * 0.18), Qt.AlignCenter, self.sub)
        if self.caption:
            pr.drawText(QRectF(0, h * 0.71, w, h * 0.20), Qt.AlignCenter, self.caption)
        pr.end()


# ------------------------------------------------------------------ 键值表

class KVTable(QTableWidget):
    """两列键值表：只读、可选中复制、斑马纹、悬浮提示。"""

    def __init__(self, theme: T.Theme, headers=("字段", "值")):
        super().__init__(0, 2)
        self.setHorizontalHeaderLabels(list(headers))
        self.verticalHeader().setVisible(False)
        self.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setWordWrap(False)
        hh = self.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        hh.setHighlightSections(False)
        self.verticalHeader().setDefaultSectionSize(27)
        self._t = theme

    def apply_theme(self, t: T.Theme) -> None:
        self._t = t

    def set_rows(self, rows, mono_keys=()) -> None:
        self.setRowCount(0)
        for k, v in rows:
            i = self.rowCount()
            self.insertRow(i)
            a = QTableWidgetItem(str(k))
            b = QTableWidgetItem(str(v))
            b.setToolTip(str(v))
            if str(k) in mono_keys:
                f = QFont("Consolas")
                f.setPointSizeF(9.0)
                b.setFont(f)
            self.setItem(i, 0, a)
            self.setItem(i, 1, b)


# ------------------------------------------------------------------ 徽标

class Badge(QLabel):
    """结论徽标：胶囊底 + 语义色文字。"""

    def __init__(self, text: str, level: str, theme: T.Theme):
        super().__init__(text)
        self._level = level
        self.apply_theme(theme)

    def apply_theme(self, t: T.Theme) -> None:
        p = t.p
        self.setStyleSheet(
            "background:%s;color:%s;border:1px solid %s;border-radius:%dpx;"
            "padding:3px 10px;font-weight:700;font-size:%dpx"
            % (p.level_bg(self._level), p.level_color(self._level),
               p.level_bd(self._level), t.radius.pill, int(t.type.caption)))

    def set_level(self, level: str, theme: T.Theme) -> None:
        self._level = level
        self.apply_theme(theme)
