"""界面控件库。

约定：所有控件都从 `theme` 取色与尺寸，不自带硬编码色值；
需要随主题变化的控件实现 `apply_theme(theme)`。
"""
from __future__ import annotations

from PySide6.QtCore import (
    Property,
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

from . import motion
from . import theme as T
from .motion import SLOW
from .theme import Motion

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


def _visible_on(color: str, bg: str) -> str:
    """把颜色推到在 `bg` 上**可见**（非文本 3:1，WCAG 1.4.11）。

    为什么代码里还要兜这一层：装饰色阶生成时就是按这条判据挑的，
    但（a）手写主题可能给不出合格的 deco；（b）同一个色阶会被压在
    `surface` / `plot_bg` / `primary_soft` 三种底色上，其中激活导航的
    胶囊底是最难的一个。生成管生成，落笔前再核一次——配色问题不会报错，
    只会"看不清"，而这类失败事后极难归因。
    """
    from .palette import contrast, fit_contrast, srgb_to_oklch
    if not color or not bg or contrast(color, bg) >= 3.0:
        return color
    lighter = srgb_to_oklch(color)[0] > srgb_to_oklch(bg)[0]
    return fit_contrast(color, bg, 3.0, lighter=lighter)


def ratio_pct(x) -> str:
    try:
        return "%.0f%%" % (float(x) * 100)
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------ 导航

class NavButton(QToolButton):
    """侧边栏导航项：图标 + 文字，选中时高亮。

    图标颜色取**装饰色阶**里属于自己的那一档（`SideBar` 按添加顺序分配）：
    导航项之间是并列关系，各自一个色相；同一档颜色在多文件对比曲线里也会
    出现，于是"第 2 个视图"和"第 2 条曲线"在视觉上是同一件事。
    """

    def __init__(self, key: str, label: str, icon_name: str, theme: T.Theme,
                 accent: str = ""):
        super().__init__()
        self.key = key
        self._label = label
        self._icon_name = icon_name
        self._accent = accent
        self.setObjectName("NavItem")
        self.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(36)
        self.setCursor(Qt.PointingHandCursor)
        self.apply_theme(theme)

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        active = self.property("active") is True
        col = self._accent or t.p.text_dim
        if active:
            # 激活态压在 primary_soft 胶囊上：图标仍要能看见（非文本 3:1）。
            # 装饰色阶生成时就是按这条判据挑的，这里再兜一层——
            # 手写主题可能给不出合格的 deco。
            col = _visible_on(col, t.p.primary_soft)
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
        # 品牌下的三色条：**只用装饰色阶**（deco[0]/[2]/[4]），三个**平色段**。
        # 纯装饰、无读数功能，存在的唯一理由是让"这是什么工具"在一屏灰里被认出来。
        # 原先是一条横向渐变，本轮"渐变彩条退场"（用户判决"合成波陈词滥调"）后
        # 改成三段平色——仍然是"这一组是并列的几档"，但不带合成波那味。
        # 颜色全在 app 级 QSS 里（按 objectName 取令牌），这里不碰样式表。
        # 三段之间用 2px 空隙分隔：贴在一起会糊成一条花俏的带子，
        # 而"三段"才是想表达的东西（并列的几档，不是一个渐变）。
        bar = QHBoxLayout()
        # 光学间距（theme.Space.thin）：三段色条之间的 2px 是"让它们读作三段"
        # 的最小值，凑到 4px 会把 24px 宽的侧边栏里的三段压得太散。
        # 不参与布局栅格——它分割的是装饰元素，不是内容块。
        bar.setSpacing(2)
        bar.setContentsMargins(0, 0, 0, 0)
        self.brand_segs = []
        for name in ("BrandBar", "BrandBarSeg1", "BrandBarSeg2"):
            seg = QFrame()
            seg.setObjectName(name)
            # 4 = 4×1：与品牌块的行高成组。
            seg.setFixedHeight(4)
            bar.addWidget(seg)
            self.brand_segs.append(seg)
        v.addLayout(bar)
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

    def _next_accent(self) -> str:
        """按添加顺序从装饰色阶里取色；用完了绕回。

        并列关系不预设"谁更重要"——真要分主次，那是 `primary` 的活
        （激活态已经用了它），装饰色阶里每个位置都是平等的。
        """
        ramp = self._theme.p.deco or ()
        return ramp[len(self._items) % len(ramp)] if ramp else ""

    def add_section(self, text: str) -> None:
        lab = QLabel(text.upper())
        lab.setObjectName("NavSection")
        self.nav_host.addWidget(lab)

    def add_item(self, key: str, label: str, icon_name: str) -> NavButton:
        b = NavButton(key, label, icon_name, self._theme, self._next_accent())
        b.clicked.connect(lambda: self.navigate.emit(key))
        self._items[key] = b
        self.nav_host.addWidget(b)
        return b

    def add_footer_item(self, key: str, label: str, icon_name: str) -> NavButton:
        b = NavButton(key, label, icon_name, self._theme, self._next_accent())
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
        lab.setObjectName("ToastText")
        h.addWidget(lab, 1)
        # **不再在这里 setStyleSheet**：`#Toast` 的规则本来就在 app 级 QSS 里
        # （theme.py 的 `#Toast {{ … }}`），部件级那份是重复的——每次 notify 都
        # 新建一个 Toast、各带一份私有样式表，换肤时要逐个重新解析。
        # 这是欠账 #10（部件级样式表 21 处）里的一处，本轮收掉。


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
        # 动效收编进 `motion`：这里原来是自建的 QPropertyAnimation，
        # 绕过了 `motion._run` 的统一清理路径（`isValid` + `deleteLater`）。
        # 收编后全仓只有 motion 一个入口，泄漏面只剩一处可守。
        motion.fade(t, self, ms=Motion.base, start=0.0)
        QTimer.singleShot(ms, lambda: self._dismiss(t))

    def _dismiss(self, t: Toast) -> None:
        motion.fade(t, self, ms=Motion.fast, start=1.0, out=True)

    def sync_geometry(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())


class SeedDialog(QDialog):
    """从种子色生成整套主题：预设 / 手输 / 深浅切换 / 风格档切换，
    **并实时预览生成结果**。

    为什么要能预览：这套配色是**算出来的**，不是挑出来的。让用户先看到
    "这个种子色生成的正文对比度是多少"，比让他挑一个好看的色块更有用——
    也顺手把"配色要达标"这件事变成看得见的界面。
    """

    #: 经典预设：日常、克制的几个种子。
    PRESETS = (("#3d7dff", "蓝"), ("#00b4d8", "青"), ("#35c46a", "绿"),
               ("#e0a92e", "琥珀"), ("#f0524a", "砖红"), ("#8b5cf6", "紫"),
               ("#e879a6", "粉"), ("#5b6478", "石墨"))
    #: ACG 预设：色相更跳、彩度更高的一档，配合「缤纷」风格档使用。
    #: 命名用中文昵称而不是色名——种子色在这里是"氛围"而不是"颜色"。
    ACG_PRESETS = (("#e879a6", "樱"), ("#39c5bb", "初音"), ("#7c5cff", "霓虹"),
                   ("#4f8cff", "星野"), ("#ff9f45", "蜜柑"), ("#5fd6a0", "薄荷"))
    SWATCHES = ("bg", "surface", "border", "text", "primary", "success", "warning", "danger")
    VIBES = (("calm", "克制"), ("pop", "缤纷"))

    def __init__(self, parent, theme: T.Theme, seed: str = "#3d7dff", mode: str = "dark",
                 vibe: str = "calm"):
        super().__init__(parent)
        self.setWindowTitle("从种子色生成主题")
        self.setMinimumWidth(460)
        self._mode = mode
        v = QVBoxLayout(self)
        v.setSpacing(12)

        for row in (self.PRESETS, self.ACG_PRESETS):
            g = QGridLayout()
            g.setSpacing(8)
            for i, (hexv, name) in enumerate(row):
                b = QPushButton(name)
                b.setFixedHeight(32)        # 4 的倍数：`tests/test_ui_grid.py` 会查
                b.setCursor(Qt.PointingHandCursor)
                b.setStyleSheet("background:%s;color:%s;border:0;border-radius:6px"
                                % (hexv, "#0d0f13" if _luma(hexv) > 0.45 else "#ffffff"))
                b.clicked.connect(lambda _=False, h=hexv: (self.ed.setText(h), self._preview()))
                g.addWidget(b, i // 4, i % 4)
            v.addLayout(g)

        row = QHBoxLayout()
        row.addWidget(QLabel("种子色"))
        self.ed = QLineEdit(seed)
        self.ed.setFixedWidth(112)
        self.ed.textChanged.connect(self._preview)
        row.addWidget(self.ed)
        self.cb_vibe = QComboBox()
        self.cb_vibe.addItems([cn for _, cn in self.VIBES])
        self.cb_vibe.setCurrentIndex(0 if vibe == "calm" else 1)
        self.cb_vibe.setFixedWidth(88)
        self.cb_vibe.currentIndexChanged.connect(self._preview)
        row.addWidget(self.cb_vibe)
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
        # 装饰色阶的预览：一条三色渐变。**这一步不能省**——它是「五颜六色」
        # 那个档位唯一能被提前看见的东西，色块看不出"这一档会变成 6 个颜色"。
        self.cell_deco = QLabel("装饰阶")
        self.cell_deco.setAlignment(Qt.AlignCenter)
        self.cell_deco.setFixedHeight(36)
        pv.addWidget(self.cell_deco)

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
    def vibe(self) -> str:
        return self.VIBES[self.cb_vibe.currentIndex()][0]

    @property
    def seed(self) -> str:
        return self.ed.text().strip()

    def _preview(self) -> None:
        from .palette import audit, build

        try:
            pal = build(self.seed, self.mode, self.vibe)
        except Exception:                                      # noqa: BLE001
            self.lbl.setText("这个种子色生成不达标（或格式不对），换一个试试")
            return
        for role, cell in zip(self.SWATCHES, self.cells):
            bg = pal["surface"] if role in ("text", "border") else pal["bg"]
            cell.setStyleSheet("background:%s;color:%s;border-radius:5px"
                               % (pal[role] if role != "border" else pal["surface_hi"],
                                  pal["text"] if role != "text" else bg))
        d = pal["deco"]
        self.cell_deco.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:.5 %s,"
            " stop:1 %s); border-radius:5px; color:%s"
            % (d[0], d[2], d[4], pal["text"]))
        # 只看**文字类**判据（要求 ≥4.5 的那些）：装饰性描边那条要求只有 1.0，
        # 混进"最弱一环"会让人以为配色不达标——报告口径要跟结论一致。
        text_findings = [f for f in audit(pal) if f.required >= 4.5]
        worst = min(text_findings, key=lambda f: f.ratio)
        self.lbl.setText("全部 %d 条 WCAG 判据通过 ✓ ｜ 文字类最弱一环 %.1f:1（%s，要求 %.1f）"
                         "｜ 装饰阶 %d 档均已 ≥3:1"
                         % (len(audit(pal)), worst.ratio, worst.pair.split(" / ")[0],
                            worst.required, len(d)))


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
    """紧凑指标卡：字段名 + 数值 + 单位。数值过长时省略，完整值给 tooltip。

    顶端有一条 2px 的**装饰色条**（取装饰色阶里属于这一格的那一档）。
    它是纯装饰：不承担语义（语义色只用于判定胶囊），也不读数——
    但 18 张卡并排时，没有它就是一整片同色块，而有了它，
    每张卡有自己的位置标识，扫一眼能回到同一个格子。
    """

    def __init__(self, theme: T.Theme, slot: int = 0):
        super().__init__()
        self.setObjectName("Card")
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        self.accent = QFrame()
        self.accent.setObjectName("CardAccent")
        # 2px 属光学尺度（<4px 栅格步进，且不参与布局分割）。
        self.accent.setFixedHeight(2)
        v.addWidget(self.accent)
        body = QWidget()
        inner = QVBoxLayout(body)
        m = theme.space
        inner.setContentsMargins(m.md, m.sm, m.md, m.sm)
        # 光学间距（theme.Space.hair）：字段名与数值是一个整体，
        # 间距调到 4px 会把它们拆成两行信息，读者要多花一次眼动。
        inner.setSpacing(m.hair)
        self.k = QLabel("—")
        self.v = QLabel("—")
        self.k.setObjectName("Caption")
        self.v.setObjectName("Hero")
        self._t = theme
        inner.addWidget(self.k)
        inner.addWidget(self.v)
        v.addWidget(body, 1)
        self.apply_theme(theme)
        self.set_slot(slot)

    def set(self, key: str, value: str, tooltip: str = "") -> None:
        self.k.setText(key)
        self.v.setText(value)
        # 值里可能是中文（厂牌 / 编码器串）→ 按内容选字体。
        # 让中文落在等宽族上，靠的就是"逐字形回退"，部分机器直接给方框。
        self.v.setFont(T.pick_text_font(value, self._t.type.body_md, self._t.font_family))
        self.setToolTip(tooltip or value)

    def set_slot(self, slot: int) -> None:
        """色条取装饰色阶的第几档。

        颜色走 **app 级 QSS 的属性选择器**（`QFrame#CardAccent[slot="3"]`），
        不给每张卡单独 `setStyleSheet`。这不是风格选择，是量出来的：
        18 张卡各带一份样式表时，换肤的 `setStyleSheet` 要多花 ~90 ms
        （每份私有样式表都要重新解析一遍）。规则集中在主题里，换肤只算一次。
        """
        self.accent.setProperty("slot", str(int(slot)))
        self.accent.style().unpolish(self.accent)
        self.accent.style().polish(self.accent)

    def apply_theme(self, t: T.Theme) -> None:
        self._t = t
        # 字距来自令牌表（`theme.Tracking`），不是手写的 .6px：
        # caption 级 0.5px，全大写西文标签按 8% 另算（见 `Tracking.caps`）。
        self.k.setStyleSheet("color:%s;font-size:%dpx;letter-spacing:%gpx"
                             % (t.p.text_faint, int(t.type.caption), t.tracking.caption))
        # 数值字体**按内容选**（ASCII → 等宽 / 含中文 → 中文比例字体），
        # 且不靠 QSS 的 font-family：部件自带 setStyleSheet 与 app 级
        # `QLabel#Hero` 规则并存时，Qt 对各属性的合并行为并不明确。
        self.v.setFont(T.pick_text_font(self.v.text(), t.type.body_md, t.font_family))
        self.v.setStyleSheet("color:%s" % t.p.text)
        # 色条**不在这里重刷**：它的颜色来自 app 级 QSS 的属性选择器，
        # 换肤时 QSS 一换，6 条规则全部跟着换——部件侧无事可做。


class Gauge(QWidget):
    """270° 环形仪表：显示「真实截止 / 奈奎斯特」占比，颜色随结论变化。"""

    def __init__(self, size: int = 128, theme: T.Theme | None = None):
        super().__init__()
        self.setFixedSize(size, size)
        self.t = theme or T.DARK
        self.value: float | None = None
        self.level: str | None = None
        self.main = "—"
        self.sub = " 真实截止"
        self.caption = ""
        # 环形进度的**缓动值**：数字跳变看不出"变了多少"，指针扫过去才看得出。
        # 走 `motion.tween`（不碰 matplotlib）——画布重绘是按百毫秒计的，
        # 而这只小部件的动画每帧只是几个 drawArc，代价可以忽略。
        # 时长取 `Motion.slow`：原先写死 430ms，那个数字不在任何一张时长表里。
        self._anim = 0.0
        self._ease = None

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
        # 连续触发时先停掉上一个：两个补间同时写 `anim` 会互相打架
        # （表现是指针抖一下）。`isValid` 是必须的——上一个补间可能已经
        # 被 `_run` 的清理路径 deleteLater 掉了。
        if self._ease is not None:
            from shiboken6 import isValid
            if isValid(self._ease):
                self._ease.stop()
        self._ease = motion.tween(self, b"anim", self, start=self._anim, end=target,
                                  ms=SLOW)
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

    def set_rows(self, rows) -> None:
        self.setRowCount(0)
        for k, v in rows:
            i = self.rowCount()
            self.insertRow(i)
            a = QTableWidgetItem(str(k))
            b = QTableWidgetItem(str(v))
            b.setToolTip(str(v))
            # **按值的实际内容**选字体：数值给等宽（好沿竖线扫），含中文的
            # （厂牌、编码器串、标题）给中文比例字体。早先按**键名**判断，
            # 等于给中文值套了没有汉字字形的等宽族 → 方框。
            b.setFont(T.pick_text_font(str(v), 9.0, self._t.font_family))
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
