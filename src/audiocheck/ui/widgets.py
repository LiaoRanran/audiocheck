"""界面控件库。

约定：所有控件都从 `theme` 取色与尺寸，不自带硬编码色值；
需要随主题变化的控件实现 `apply_theme(theme)`。
"""
from __future__ import annotations

from PySide6.QtCore import (
    QRectF,
    Qt,
    QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QPainter
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
    QTextBrowser,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import material, motion
from . import theme as T
from .palette import SLAB_BAND_H as P_SLAB_BAND_H
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
        # 三态着色：hover/press 用亮一档的同色，disabled 用弱色。
        # ⚠ 不能只给单色 pixmap——QSS 改不了图标色（见 `icon_states` 的说明）。
        hover = _visible_on(col, t.p.hover_hi) if col != t.p.text_dim else t.p.text
        self.setIcon(T.icon_states(self._icon_name, col, 17,
                                   active=hover, disabled=t.p.text_disabled))
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

    def paintEvent(self, ev):                           # noqa: N802
        """QSS 画完渐变背景后，再铺一层 ≤2/255 的噪点抗色带。

        侧边栏是一条贯穿整高的纵向渐变，是全窗口最平最长的渐变之一，
        6 bit 面板上最容易出色带。见 `material.paint_noise` 的说明：
        画在这里而不是做成半透明子控件，是为了不给整窗加一层 alpha 合成。
        """
        super().paintEvent(ev)
        material.paint_noise(self)

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

class Toast(material.ShadowFrame):
    """右下角浮层通知：**投影自绘**，卡片是内层 `#Toast`（走 app 级 QSS）。

    为什么要包一层：投影需要向外留出羽化边距，而 QSS 只能给整块矩形上背景色。
    外层负责"投影 + 留白"，内层负责"卡片"，两者的职责不混。

    为什么不用 `QGraphicsDropShadowEffect`：一个部件同时只能有一个 graphics
    effect，而这个 Toast 进出场要挂 `QGraphicsOpacityEffect`，两者会互相顶掉
    ——见 `material.py` 开头那段。
    """

    def __init__(self, text: str, kind: str, theme: T.Theme):
        super().__init__(theme, tier="near")
        self.setObjectName("ToastWrap")
        col = {"info": theme.p.info, "ok": theme.p.success,
               "warn": theme.p.warning, "bad": theme.p.danger}.get(kind, theme.p.info)
        outer = QVBoxLayout(self)
        # 四边留出投影的羽化边距——`ShadowFrame.pad` 就是 blur + 偏移。
        # 不按它留白的话投影会被自己的矩形裁掉，只剩一条硬边。
        outer.setContentsMargins(self.pad, self.pad, self.pad, self.pad)
        outer.setSpacing(0)
        self.card = QFrame()
        self.card.setObjectName("Toast")
        self.card.setMaximumWidth(340)
        h = QHBoxLayout(self.card)
        h.setContentsMargins(16, 8, 16, 8)
        dot = QLabel()
        pm = T.make_pixmap("shield", col, 16, 1.8)
        dot.setPixmap(pm)
        h.addWidget(dot)
        lab = QLabel(text)
        lab.setWordWrap(True)
        lab.setObjectName("ToastText")
        h.addWidget(lab, 1)
        outer.addWidget(self.card)
        # **不再在这里 setStyleSheet**：`#Toast` 的规则本来就在 app 级 QSS 里
        # （theme.py 的 `#Toast {{ … }}`），部件级那份是重复的——每次 notify 都
        # 新建一个 Toast、各带一份私有样式表，换肤时要逐个重新解析。
        # 这是欠账 #10（部件级样式表 21 处）里的一处，本轮收掉。

    def apply_theme(self, t: T.Theme) -> None:
        super().apply_theme(t)      # 投影颜色缓存作废并重画


class ToastHost(QWidget):
    """右下角浮层通知。鼠标穿透，不挡交互。"""

    #: 通知的存活时长。2800 不在任何时长令牌里，但它是**停留时长**而不是
    #: 动效时长（人读一句话要的时间），所以不进 Motion 表。
    LIFETIME = 2800

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 16, 16)
        self._lay.setSpacing(8)
        # **弹簧必须留在首位**，通知追加到它后面。反过来的话（弹簧在后、
        # 通知插在它前面）通知会被顶到窗口**顶部**——而它们该在右下角。
        # 这个错位从很早就在了：文档写着"右下角浮层"，画面在右上角，
        # 双方都没错、只是没人把两件事放在一起看过。出图时才发现。
        self._lay.addStretch(1)
        self._theme = T.DARK

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t

    def notify(self, text: str, kind: str = "info", ms: int = LIFETIME) -> None:
        t = Toast(text, kind, self._theme)
        # 右对齐**显式写出来**：不给对齐时，"最大宽度小于布局宽度"的控件
        # 落在哪个位置是 Qt 的内部行为（历史上靠"看起来在右边"蒙对）。
        self._lay.addWidget(t, 0, Qt.AlignRight)
        # 动效收编进 `motion`：这里原来是自建的 QPropertyAnimation，
        # 绕过了 `motion._run` 的统一清理路径（`isValid` + `deleteLater`）。
        # 收编后全仓只有 motion 一个入口，泄漏面只剩一处可守。
        motion.fade(t, self, ms=Motion.base, start=0.0)
        QTimer.singleShot(ms, lambda: self._dismiss(t))

    def _dismiss(self, t: Toast) -> None:
        """淡出并拆除。

        拆的动作挂 `cleanup_extra` 而不是自己再接一个 finished —— 收编到
        `motion` 的意义就是"清理只有一条路径"。这里踩过一次：B1 写成了
        `motion.fade(..., out=True)`，而 `fade` 的参数叫 `end`，
        于是**通知永远不会消失**（TypeError 被 Qt 的信号槽吞在槽里，
        界面照常，只是右下角一直堆着）。现在有 `test_ui_material` 钉住它。
        """
        motion.fade(
            t, self, ms=Motion.fast, start=1.0, end=0.0,
            cleanup_extra=lambda: (self._lay.removeWidget(t), t.deleteLater()))

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
                b.setStyleSheet("background:%s;color:%s;border:0;border-radius:%dpx"
                                % (hexv, "#0d0f13" if _luma(hexv) > 0.45 else "#ffffff",
                                   self._t.radius.md))
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
        self.cb_vibe.setAccessibleName("配色风格档")
        self.cb_vibe.addItems([cn for _, cn in self.VIBES])
        self.cb_vibe.setCurrentIndex(0 if vibe == "calm" else 1)
        self.cb_vibe.setFixedWidth(88)
        self.cb_vibe.currentIndexChanged.connect(self._preview)
        row.addWidget(self.cb_vibe)
        self.cb_mode = QComboBox()
        self.cb_mode.setAccessibleName("明暗模式")
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
            cell.setStyleSheet("background:%s;color:%s;border-radius:%dpx"
                               % (pal[role] if role != "border" else pal["surface_hi"],
                                  pal["text"] if role != "text" else bg,
                                  self._t.radius.instrument))
        d = pal["deco"]
        self.cell_deco.setStyleSheet(
            "background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 %s, stop:.5 %s,"
            " stop:1 %s); border-radius:%dpx; color:%s"
            % (d[0], d[2], d[4], self._t.radius.instrument, pal["text"]))
        # 只看**文字类**判据（要求 ≥4.5 的那些）：装饰性描边那条要求只有 1.0，
        # 混进"最弱一环"会让人以为配色不达标——报告口径要跟结论一致。
        text_findings = [f for f in audit(pal) if f.required >= 4.5]
        worst = min(text_findings, key=lambda f: f.ratio)
        self.lbl.setText("全部 %d 条 WCAG 判据通过 ✓ ｜ 文字类最弱一环 %.1f:1（%s，要求 %.1f）"
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

    顶端有一条 2px 的**中性顶光**（不再是装饰色阶，B7.1 做法 2）。
    它承担的还是同一件事——18 张卡并排时每张卡有自己的上缘，
    扫一眼能回到同一个格子——只是改用**明度**表达而不是**色相**：
    18 条彩色卡条会连成 6 条横贯全屏的彩带，实测吃掉全图高彩的 42.4%，
    却只把"一枚 accent"稀释成六个色相（主族占比掉到 64%）。
    颜色在 app 级 QSS 的 `QFrame#CardAccent` 里，部件侧不碰样式表。
    """

    def __init__(self, theme: T.Theme):
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

    def set(self, key: str, value: str, tooltip: str = "") -> None:
        self.k.setText(key)
        self.v.setText(value)
        # 值里可能是中文（厂牌 / 编码器串）→ 按内容选字体。
        # 让中文落在等宽族上，靠的就是"逐字形回退"，部分机器直接给方框。
        self.v.setFont(T.mono_qfont(self._t.type.body_md))   # B14 字排版：数值走等宽（列对齐）
        self.setToolTip(tooltip or value)

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
        # 顶条**不在这里重刷**：它的颜色来自 app 级 QSS 的一条规则，
        # 换肤时 QSS 一换它跟着换——部件侧无事可做。


class RatioBar(QWidget):
    """比例：**一条 1px 细线 + 一个标记**（替代 270° 环形仪表）。

    为什么不用环形（诊断文档 §一/§二 实测）：
      * `paintEvent` 里三行字的绘制带**矩形就交叠**——主数字 `h*0.30–0.60`、
        副行 `h*0.55–0.73`、占比 `h*0.71–0.91`，后两行重叠 **2.5px**；
        128px 的圆还要被 9px 环吃掉一圈（有效内径 ≈104px）。
      * "占比"用 270° 弧 + 锥形渐变 + 圆头 + 底部留口 = **车载转速表语汇**，
        与"仪器"不是一回事：仪器读数是**数字 + 刻度**，不是转盘。
      * 环色随判定变，于是"绿条上绿环"——同色相低对比，弧线几乎读不出。

    一条线 + 一个标记没有这些问题：读数精确、零歧义、不吃版面。
    颜色从主题令牌读（细线用 `border`、标记用判定带色），不新增色号。
    """

    #: 控件高：细线 1px + 上下各留 3px 给标记（2px 宽、9px 高）。
    H = 11
    MARK_W, MARK_H = 2, 9

    def __init__(self, theme: T.Theme | None = None):
        super().__init__()
        self.setFixedHeight(self.H)
        self.t = theme or T.DARK
        self.level = "warn"
        self.ratio: float | None = None

    def set_data(self, ratio, level: str) -> None:
        self.ratio = None if ratio is None else max(0.0, min(1.0, float(ratio)))
        self.level = level
        self.update()

    def apply_theme(self, t: T.Theme) -> None:
        self.t = t
        self.update()

    def paintEvent(self, _):                            # noqa: N802
        p = self.t.p
        pr = QPainter(self)
        y = self.H // 2
        pr.setPen(QColor(p.border))
        pr.drawLine(0, y, self.width() - 1, y)
        if self.ratio is not None:
            pr.setPen(QColor(p.level_band(self.level)))
            x = int(self.ratio * (self.width() - self.MARK_W))
            pr.fillRect(QRectF(x, y - self.MARK_H // 2, self.MARK_W, self.MARK_H),
                        QColor(p.level_band(self.level)))
        pr.end()


class VerdictPanel(QFrame):
    """判定区（B9.1）：**淡底 + 4px 判定带 + 判词 + hero 数字 + 比例标记**。

    它替代了 B7 的"通栏高彩块 + 270° 环形仪表"。结构照 B9 小样：

        ▌4px 判定带（判定色原彩度 · 高彩 · 视觉重量全在这里）
        ● 不达标   有损溯源                      真实截止
        ≈192–256 kbps 转码 · 内容止于 16.01 kHz…   16.01 kHz   ← hero 40px
        ────────────────│────────  占奈奎斯特 73%
        （整块 = 大面积淡判定底 · 低彩 · 承载判词文字）

    两个角色**各自有令牌、命名不许混**：`*_tint` 是面、`*_band` 是线。
    B7 把两者绑在同一块 880×140 的高彩底上——**面积百分比不等于视觉重量**，
    同样 3.5%，横贯全宽的色带比一小块光斑重好几倍。

    ⚠ **判词不用"深色字"**：那是提示词按亮色写的。这里按主题定方向——
    亮色下淡底比页面底深、用深字；暗色下淡底比页面底亮、用亮字。
    判据是"与 tint 的对比 ≥4.5"（`palette.CHECKS`），不是某个具体颜色。
    """

    #: hero 区固定宽度（小样的 1fr / 260px 结构）。
    HERO_W = 260

    def __init__(self, theme: T.Theme):
        super().__init__()
        self.setObjectName("VerdictBanner")
        self.t = theme
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        # —— 4px 判定带：贴在最上沿，横贯全宽 ——
        self.band = QFrame()
        self.band.setObjectName("VerdictBand")
        self.band.setFixedHeight(P_SLAB_BAND_H)
        v.addWidget(self.band)
        # —— 主体 ——
        body = QWidget()
        m = theme.space
        hb = QHBoxLayout(body)
        # 留白回来（R3）：外边距在 B9.1 的基础上**再升一档**
        # （`xl,lg` → `xxl,xl`）。判词区是顶部整幅大块，四周松一档是
        # "极度舒适"最直接的来源；仍守 4 的倍数。
        hb.setContentsMargins(m.xxl, m.xl, m.xxl, m.xl)
        hb.setSpacing(m.xxl)
        left = QVBoxLayout()
        left.setSpacing(m.sm)
        row1 = QHBoxLayout()
        row1.setSpacing(m.sm)
        self.dot = QFrame()
        self.dot.setObjectName("VerdictDot")
        self.dot.setFixedSize(8, 8)
        row1.addWidget(self.dot, 0, Qt.AlignVCenter)
        self.lbl_verdict = QLabel("—")
        self.lbl_verdict.setObjectName("VerdictText")
        row1.addWidget(self.lbl_verdict, 1)
        left.addLayout(row1)
        self.lbl_file = QLabel("")
        self.lbl_file.setObjectName("Caption")
        self.lbl_file.setWordWrap(True)
        left.addWidget(self.lbl_file)
        left.addStretch(1)
        hb.addLayout(left, 1)
        # —— hero 区 ——
        right = QVBoxLayout()
        right.setSpacing(m.xs)
        self.lbl_hero_label = QLabel("真实截止")
        self.lbl_hero_label.setObjectName("Caption")
        right.addWidget(self.lbl_hero_label)
        hero_row = QHBoxLayout()
        hero_row.setSpacing(m.xs)
        self.lbl_hero = QLabel("—")
        self.lbl_hero.setObjectName("HeroBig")
        hero_row.addWidget(self.lbl_hero, 0, Qt.AlignBaseline)
        self.lbl_hero_unit = QLabel("kHz")
        self.lbl_hero_unit.setObjectName("HeroUnit")
        hero_row.addWidget(self.lbl_hero_unit, 0, Qt.AlignBaseline)
        hero_row.addStretch(1)
        right.addLayout(hero_row)
        self.ratio_bar = RatioBar(theme)
        right.addWidget(self.ratio_bar)
        self.lbl_ratio = QLabel("—")
        self.lbl_ratio.setObjectName("Caption")
        right.addWidget(self.lbl_ratio)
        right.addStretch(1)
        right.setAlignment(Qt.AlignTop)
        # ⚠ **列宽约束必须设在"列"这个容器上，不是设在内层 layout 上。**
        # B9.1 写的是 `hb.addLayout(right, 0)`——一个 stretch=0 的**子布局**
        # 没有宽度语义，Qt 按它的 sizeHint 分配，而 hero 的 sizeHint 是在
        # `apply_theme` 设置 40px 字体**之前**算的，于是右列被左列挤窄、
        # `kHz` 单位被右边缘裁掉（B9.1 遗留，实测截图可见）。
        # 换成有确定宽度的容器之后，右列宽度与字体无关。
        hero_col = QWidget()
        hero_col.setFixedWidth(self.HERO_W)
        hero_col.setLayout(right)
        hb.addWidget(hero_col, 0)
        self._hero_box = body
        v.addWidget(body, 1)
        self._right = right
        self.apply_theme(theme)

    # ---- 数据 ----------------------------------------------------------
    def set_data(self, level: str, verdict: str, file_text: str = "",
                 hero: str = "—", unit: str = "kHz", hero_label: str = "真实截止",
                 ratio=None) -> None:
        self.level = level
        self.lbl_verdict.setText(verdict or "—")
        self.lbl_file.setText(file_text)
        self.lbl_hero.setText(hero)
        self.lbl_hero_unit.setText(unit)
        self.lbl_hero_label.setText(hero_label)
        self.lbl_ratio.setText("占奈奎斯特 %s" % _pct(ratio) if ratio is not None else "—")
        self.ratio_bar.set_data(ratio, level)
        for w in (self, self.band, self.dot):
            w.setProperty("level", level)
            w.style().unpolish(w)
            w.style().polish(w)

    def apply_theme(self, t: T.Theme) -> None:
        self.t = t
        self.band.setFixedHeight(P_SLAB_BAND_H)
        self.ratio_bar.apply_theme(t)
        # hero 是**数据不是层级**：40px 只此一处（`Type.hero`，有破例说明）。
        # 字体按内容选（ASCII → DIN 系/等宽），与指标卡同一条纪律。
        self.lbl_hero.setFont(T.pick_text_font(self.lbl_hero.text(), t.type.hero,
                                               t.font_family))
        self.lbl_verdict.setFont(T.pick_text_font(self.lbl_verdict.text(),
                                                  t.type.display, t.font_family))
        self._right.setAlignment(Qt.AlignTop)


def _pct(ratio) -> str:
    try:
        return "%.0f%%" % (float(ratio) * 100.0)
    except (TypeError, ValueError):
        return "—"


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


class HelpDialog(QDialog):
    """帮助面板：快捷键 / 视图 / 效力边界 / 阈值出处。

    ## 为什么内容**全部派生**、不许手写

    手写的快捷键表会在加功能后过期，而**过期的帮助比没有帮助更坏**——
    用户会照着做，然后发现不灵。所以这里只吃三份现成输出：
    `window._cmds`（命令表的快捷键列）、`causes.boundaries()`（效力边界）、
    `config.provenance_summary()`（阈值出处计数）。
    回归 `test_help_dialog_is_derived_not_handwritten` 扫源码禁止硬编码 `Ctrl+`。
    """

    TITLE = "帮助 · 快捷键与效力边界"

    def __init__(self, parent, shortcuts, views, boundaries, prov_text):
        super().__init__(parent)
        self.setWindowTitle(self.TITLE)
        self.resize(660, 640)
        lay = QVBoxLayout(self)
        self.body = QTextBrowser()
        self.body.setObjectName("HelpBody")
        self.body.setOpenExternalLinks(False)
        self.body.setHtml(self.build_html(shortcuts, views, boundaries, prov_text))
        lay.addWidget(self.body, 1)
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("关闭")
        btn.setObjectName("HelpClose")
        btn.clicked.connect(self.accept)
        row.addWidget(btn)
        lay.addLayout(row)

    @staticmethod
    def build_html(shortcuts, views, boundaries, prov_text) -> str:
        rows = "".join(
            "<tr><td style='padding:2px 14px 2px 0'>%s</td>"
            "<td><code>%s</code></td></tr>" % (lbl, sc) for lbl, sc in shortcuts)
        vs = "".join("<li>%s</li>" % v for v in views)
        bs = "".join("<li>%s</li>" % b for b in boundaries)
        return (
            "<h3>快捷键</h3><table>%s</table>"
            "<p>全部功能另有命令面板（<code>Ctrl+K</code>），可搜到任意视图与动作。</p>"
            "<h3>视图</h3><ul>%s</ul>"
            "<h3>效力边界（本工具<b>不能</b>告诉你的）</h3><ul>%s</ul>"
            "<h3>阈值出处</h3><p>%s</p>"
            % (rows, vs, bs, prov_text))
