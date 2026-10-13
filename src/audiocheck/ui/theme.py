"""设计系统：令牌 → QSS。

为什么要有令牌：手写 QSS 时颜色/间距会散落各处，改一处要全局搜。
这里把所有视觉参数集中成一组令牌，QSS 由令牌生成，主题切换只需换一组值。

令牌命名遵循「语义 > 外观」：
  surface / border / text_primary 而不是 #1b202b，
  这样加新组件时不需要发明新的色号。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontDatabase,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QPolygonF,
)

# ------------------------------------------------------------------ 尺寸令牌（4px 栅格）


@dataclass(frozen=True)
class Space:
    """间距刻度。**两套刻度，用途不同，不要混用。**

    * 布局刻度（xs…xxl）：全部是 4 的倍数，用于**元素之间**的间距。
      栅格审计（tests/test_ui_grid.py）强制这一条。
    * 光学刻度（hair/thin）：**故意脱离栅格**，只用于**组件内部**的微调。
      例：StatCard 里字段名与数值之间、导航按钮的图标与文字之间。

    **为什么不把光学间距也凑到 4px**：4px 在 10–12px 的字号旁边是可见的
    空隙，硬凑会把"字段名 + 数值"这个整体拆散，观感反而更差。
    设计系统里"栅格"约束的是构图节奏，不是像素强迫症——
    但光学例外必须**显式登记**，否则它就退化成随手写的魔数。
    """
    xs: int = 4
    sm: int = 8
    md: int = 12
    lg: int = 16
    xl: int = 24
    xxl: int = 32
    # ---- 光学刻度：仅限组件内部，不参与布局 ----
    hair: int = 1
    thin: int = 2


@dataclass(frozen=True)
class Radius:
    sm: int = 4
    md: int = 6
    lg: int = 8
    xl: int = 12
    pill: int = 999


@dataclass(frozen=True)
class Type:
    """字阶。caption 到 display 共 8 级，覆盖全部场景，不允许出现第 9 种字号。"""
    micro: float = 10.0      # 角标 / 单位
    caption: float = 11.0    # 字段标签
    body_sm: float = 12.0    # 表格 / 次要正文
    body: float = 13.0       # 正文
    body_md: float = 14.0    # 小节标题
    title: float = 16.0      # 卡片标题
    heading: float = 20.0    # 页标题
    display: float = 27.0    # Hero 数字


@dataclass(frozen=True)
class Motion:
    fast: int = 120          # 悬停
    base: int = 200          # 展开/切换
    slow: int = 320          # 抽屉


@dataclass(frozen=True)
class Palette:
    """语义化配色。每一项都注明用途，新增组件时按用途选，不要自选色号。"""
    name: str = "dark"

    # 背景层级：bg 最低，surface_raised 最高（浮层）
    bg: str = "#0b0d12"
    surface: str = "#12151c"
    surface_hi: str = "#171b24"
    surface_raised: str = "#1d222d"

    # 描边：默认 / 悬停 / 聚焦
    border: str = "#222834"
    border_hi: str = "#2e3644"
    border_focus: str = "#3d7dff"

    # 文本：主 / 次 / 弱 / 禁用
    text: str = "#e8ecf3"
    text_dim: str = "#98a2b3"
    # 弱文本是 11px 的字段标签/说明，属于**小字正文**，WCAG AA 要 4.5:1。
    # 原值 #626c80 实测 3.46:1（不达标），沿 OKLCH 的 L 轴上推一格到 4.52:1；
    # 色相与 chroma 不变，所以看起来还是那个灰。
    text_faint: str = "#747f93"
    text_disabled: str = "#454e5e"

    # 语义色
    primary: str = "#3d7dff"
    # primary_hi 是**底色**角色（主按钮悬停）——要够暗，按钮文字才达标；
    # primary_text 是**文字**角色（激活导航）——要够亮，压在 primary_soft 上才达标。
    # 两者方向互斥，早先只有一个 primary_hi 时激活导航实测 3.2:1（低于 AA 4.5）。
    primary_hi: str = "#5a92ff"
    primary_text: str = "#5a92ff"
    primary_soft: str = "#16243d"
    # 次级强调色（分裂互补色相 +150°）：只用于数据可视化与装饰，**不承担语义**。
    # 有了它，多文件对比图的第一条/第二条曲线才是"同一套配色里的两个声音"，
    # 而不是从通用色板里各抓一个。
    accent2: str = "#a06cf0"
    # 主按钮上的文字。**不是所有主色都配得起白字**：白字压在 #3d7dff 上只有
    # 3.77:1（低于 AA 4.5），压在悬停色 #5a92ff 上只有 3.00:1。
    # 两条路——压深主色（品牌色变闷）或改用墨字。选后者：种子色/品牌色的
    # 辨识度是这套设计系统的前提，文字颜色本来就该去适配底色。
    on_primary: str = "#0d0f13"
    success: str = "#35c46a"
    warning: str = "#e0a92e"
    danger: str = "#f0524a"
    info: str = "#38bdf8"

    # 语义色的背景态（用于徽标/胶囊底）
    success_bg: str = "#0c2015"
    warning_bg: str = "#221a09"
    danger_bg: str = "#281110"
    info_bg: str = "#0b1e2a"
    success_bd: str = "#1d5a33"
    warning_bd: str = "#5e4818"
    danger_bd: str = "#6b2521"
    info_bd: str = "#12506a"

    # 选中 / 悬停叠加
    selection: str = "#1a2740"
    hover: str = "#1a1f29"
    # 按钮悬停底色。**单独一个令牌是必须的**：QSS 里原来写死 `#1c212b`，
    # 于是浅色主题下悬停按钮会把深色文字画到近黑底上（对比度 ≈1.1:1，字没了）。
    hover_hi: str = "#1c212b"

    # 图表
    grid: str = "#1e2430"
    plot_bg: str = "#0d1016"

    # 阴影（Qt 不直接支持 box-shadow，用于自绘与浮层描边模拟）
    shadow: str = "rgba(0,0,0,.45)"

    def level_color(self, level: str) -> str:
        return {"ok": self.success, "warn": self.warning, "bad": self.danger}.get(
            level, self.text_dim)

    def level_bg(self, level: str) -> str:
        return {"ok": self.success_bg, "warn": self.warning_bg,
                "bad": self.danger_bg}.get(level, self.surface_hi)

    def level_bd(self, level: str) -> str:
        return {"ok": self.success_bd, "warn": self.warning_bd,
                "bad": self.danger_bd}.get(level, self.border_hi)


def plot_palette(p: Palette) -> dict:
    """图表层（matplotlib）用的字典式调色适配。

    画布代码历史上按旧键名（surface2 / accent / ok / warn_border…）取值。
    与其改动画布，不如在这里做一次**显式**键名映射——映射表集中可见，
    将来统一画布命名后删掉本函数即可。
    """
    return {
        "bg": p.bg, "surface": p.surface, "surface2": p.surface_hi, "surface3": p.surface_raised,
        "border": p.border, "border_hi": p.border_hi,
        "text": p.text, "text_dim": p.text_dim, "text_faint": p.text_faint,
        "accent": p.primary, "accent_hi": p.primary_hi, "accent_soft": p.primary_soft,
        "accent2": p.accent2,
        "ok": p.success, "warn": p.warning, "bad": p.danger,
        "ok_bg": p.success_bg, "warn_bg": p.warning_bg, "bad_bg": p.danger_bg,
        "ok_border": p.success_bd, "warn_border": p.warning_bd, "bad_border": p.danger_bd,
        "grid": p.grid, "plot_bg": p.plot_bg, "selection": p.selection,
    }


#: **运行时解析出来的**中文字体族。空串 = 还没解析（用 Theme 自带的默认值）。
#: 为什么要有这个全局：QSS 的 `font-family` **不支持多族回退链**——`"A","B",sans-serif`
#: 里的逗号链在 Qt 里是无效/不可靠的（本项目在等宽字体上已经踩过一次：
#: 写死 "Cascadia Mono" 而本机只有 Consolas → 静默回退成比例字体）。
#: 而主题里写死 "Microsoft YaHei UI" 一旦与系统实际族名对不上，Qt 会回退到
#: **默认字体**——若那个默认字体没有汉字字形，**整屏中文都是方框**（用户报的乱码）。
#: 结论：族名必须在运行时从已注册的字体里解析出来，不能写死。
_UI_FAMILY = ""


def set_ui_family(fam: str) -> None:
    """把**实测存在**的中文字体族写进主题令牌（建窗口之前调用）。"""
    global _UI_FAMILY
    if fam:
        _UI_FAMILY = fam


def ui_family() -> str:
    return _UI_FAMILY


def init_fonts(app=None) -> str:
    """加载中文字体 → 设成应用字体 → 写进主题令牌。**必须在建窗口之前调用。**

    这三件事必须一起做：只注册字体文件（`load_cjk_fonts`）而 QSS 里仍写着
    对不上的族名，等于没做（中文照样方框）。
    """
    fams = load_cjk_fonts()
    fam = pick_family(fams)
    if app is not None:
        app.setFont(QFont(fam, 10))
    set_ui_family(fam)
    return fam


@dataclass(frozen=True)
class Theme:
    name: str
    p: Palette
    space: Space = field(default_factory=Space)
    radius: Radius = field(default_factory=Radius)
    type: Type = field(default_factory=Type)
    motion: Motion = field(default_factory=Motion)
    font_family: str = "Microsoft YaHei UI"

    @property
    def font(self) -> str:
        """QSS 用的字体族。**只写一族名**——写链是假回退（见 `_UI_FAMILY` 的说明）。

        优先用运行时解析出来的族（`init_fonts()` 写入），没有才退回实例默认值。
        """
        fam = _UI_FAMILY or self.font_family
        return '"%s",sans-serif' % fam

    @property
    def mono(self) -> str:
        """等宽字体族名（带引号，可直接进 QSS）。

        必须是**实测存在**的族。早先这里写死 "Cascadia Mono"，而本机只装了
        Consolas —— QSS 的 font-family 不支持多族回退，于是静默落回比例字体，
        "数字等宽"这件事看起来做了、实际没生效。
        """
        return '"%s",monospace' % (mono_family() or "Consolas")


DARK_P = Palette()
LIGHT_P = Palette(
    name="light",
    bg="#f3f5f9", surface="#ffffff", surface_hi="#f7f9fc", surface_raised="#ffffff",
    border="#e0e5ee", border_hi="#c8d0de", border_focus="#2563eb",
    # 下面这四色是按 OKLCH 的 L 轴**最小改动**拉到达标的（见 packaging/palette_report.py）：
    #   text_faint #8a94a8 → #6d778a（3.05 → 4.51）  小字正文要 4.5
    #   success    #15a34a → #008137（2.99 → 4.54）  「合格」胶囊
    #   warning    #b97a04 → #9b6500（3.29 → 4.52）  「有疑」胶囊
    #   danger     #dc2626 → #d61e21（4.23 → 4.52）  「不达标」胶囊
    # 后者是全应用最关键的一行信息（判定结论），而它在浅色主题下原本**低于 AA**。
    text="#141a26", text_dim="#5b6478", text_faint="#6d778a", text_disabled="#b2bac8",
    primary="#2563eb", primary_hi="#1d4fd8", primary_text="#1d4fd8", primary_soft="#e3ecff",
    on_primary="#ffffff", accent2="#7c3aed",
    success="#008137", warning="#9b6500", danger="#d61e21", info="#0284c7",
    success_bg="#e7f8ee", warning_bg="#fdf4e3", danger_bg="#fdeceb", info_bg="#e5f4fd",
    success_bd="#a9e3c2", warning_bd="#f0dcae", danger_bd="#f5c6c2", info_bd="#a8d8f0",
    selection="#dbe7ff", hover="#eef1f7", hover_hi="#e6ebf5",
    grid="#e8ecf3", plot_bg="#ffffff", shadow="rgba(15,23,42,.12)",
)

DARK = Theme("dark", DARK_P)
LIGHT = Theme("light", LIGHT_P)
THEMES = {"dark": DARK, "light": LIGHT}

LEVEL_CN = {"ok": "合格", "warn": "有疑", "bad": "不达标"}
LEVEL_COLOR_KEY = {"ok": "ok", "warn": "warn", "bad": "bad"}


# ------------------------------------------------------------------ QSS 生成

def qss(t: Theme) -> str:
    p, sp, ty = t.p, t.space, t.type
    return """
/* ===== 基础 ===== */
* {{ outline: 0; }}
QWidget {{ background:{bg}; color:{text}; font-size:{body}px; font-family:{font}; }}
QMainWindow, QDialog {{ background:{bg}; }}
QToolTip {{ background:{surface_raised}; color:{text}; border:1px solid {border_hi};
            padding:{xs}px {sm}px; border-radius:{md}px; }}

/* ===== 标题栏（无边框窗口自绘） ===== */
/* 光的方向：窗口自上而下变亮一档，读起来像"光从上面来"。
   整块纯色会很呆；而渐变只用了两个已有令牌，没有新增任何色号。 */
#TitleBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
           stop:0 {surface_hi}, stop:1 {surface}); border-bottom:1px solid {border}; }}
#TitleText {{ font-size:{body_sm}px; color:{text_dim}; }}
#WinBtn {{ background:transparent; border:0; border-radius:{md}px; }}
#WinBtn:hover {{ background:{hover}; }}
#WinBtn#Close:hover {{ background:{danger}; }}
#WinBtn#Close:hover {{ color:#fff; }}

/* ===== 侧边栏 ===== */
#SideBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
           stop:0 {surface}, stop:1 {bg}); border-right:1px solid {border}; }}
#BrandName {{ font-size:{body_md}px; font-weight:700; color:{text}; }}
#BrandSub {{ font-size:{micro}px; color:{text_faint}; }}
#NavItem {{ background:transparent; border:0; border-radius:{lg}px;
            padding:{sm}px {md}px; color:{text_dim}; text-align:left; font-size:{body}px; }}
#NavItem:hover {{ background:{hover}; color:{text}; }}
#NavItem[active="true"] {{ background:{primary_soft}; color:{primary_text}; font-weight:700; }}
#NavSection {{ color:{text_faint}; font-size:{micro}px; font-weight:700;
              letter-spacing:1.1px; padding:{md}px {md}px {xs}px; }}

/* ===== 卡片 / 分区 ===== */
QFrame#Card, QWidget#Card {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {surface_hi}, stop:1 {surface}); border:1px solid {border};
                             border-radius:{xl}px; }}
/* SectionTitle 用字距而不是字重做层级：本机中文字体只有 400/700 两档，
   原来写 font-weight:600 会被 Qt 吸附到 700（还会打 findfont 告警），
   于是"600"和"700"渲染出来一模一样——那个层级是假的。 */
QLabel#SectionTitle {{ color:{text_dim}; font-size:{caption}px; font-weight:700;
                       letter-spacing:1.2px; }}
/* #Hero 的等宽**由代码按值内容指定**（`theme.pick_text_font`），QSS 不设族名：
   值里可能有中文（厂牌、编码器串），而 QSS 的族名是"一律套用"、没有回退链，
   中文一旦落上去就得靠逐字形回退——那是不可控的。 */
QLabel#Hero {{ font-size:{display}px; font-weight:700; color:{text}; }}
QLabel#HeroUnit {{ color:{text_faint}; font-size:{caption}px; }}
QLabel#Caption {{ color:{text_faint}; font-size:{caption}px; }}

/* ===== 按钮 ===== */
QPushButton {{ background:{surface_hi}; border:1px solid {border_hi};
               border-radius:{lg}px; padding:{sm}px {lg}px; color:{text}; font-size:{body}px; }}
QPushButton:hover {{ background:{hover_hi}; border-color:{border_focus}; }}
QPushButton:pressed {{ background:{surface}; }}
QPushButton:disabled {{ color:{text_disabled}; border-color:{border}; background:{surface}; }}
QPushButton#Primary {{ background:{primary}; border-color:{primary}; color:{on_primary};
                       font-weight:700; }}
QPushButton#Primary:hover {{ background:{primary_hi}; border-color:{primary_hi}; }}
QPushButton#Ghost {{ background:transparent; border-color:transparent; color:{text_dim}; }}
QPushButton#Ghost:hover {{ background:{hover}; color:{text}; }}
QToolButton {{ background:transparent; border:1px solid transparent;
               border-radius:{lg}px; padding:{xs}px {md}px; color:{text}; font-size:{body}px; }}
QToolButton:hover {{ background:{hover}; border-color:{border_hi}; }}
QToolButton#Primary {{ background:{primary}; border-color:{primary}; color:{on_primary}; font-weight:700; }}
QToolButton:disabled {{ color:{text_disabled}; }}

/* ===== 输入 ===== */
QLineEdit, QComboBox, QSpinBox {{ background:{surface_hi}; border:1px solid {border};
    border-radius:{lg}px; padding:{xs}px {md}px; color:{text};
    selection-background-color:{primary}; font-size:{body}px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color:{border_focus}; }}
QLineEdit[invalid="true"] {{ border-color:{danger}; }}
QComboBox::drop-down {{ border:0; width:22px; }}
QComboBox QAbstractItemView {{ background:{surface_raised}; border:1px solid {border_hi};
    selection-background-color:{primary}; color:{text}; padding:{xs}px; border-radius:{md}px; }}
QCheckBox {{ spacing:{sm}px; color:{text}; font-size:{body}px; }}
QCheckBox::indicator {{ width:16px; height:16px; border-radius:{sm}px;
    border:1px solid {border_hi}; background:{surface_hi}; }}
QCheckBox::indicator:checked {{ background:{primary}; border-color:{primary}; }}

/* ===== 表格 ===== */
/* 表格整体走等宽：规格/截止/DR/削波全是数字，比例数字会让列宽抖动、
   读数时眼睛没法沿竖线扫。中文列（文件名/判定）仍用比例字体，
   由 ItemDelegate 按列切换。 */
/* 表格**不设整体等宽**：等宽是给数字列的（那几列由委托显式指定字体），
   而这张表里有中文文件名、有整页中文的元数据表——整体等宽等于把中文
   全部推给"逐字形回退"，落到哪个字体不可控，部分机器上直接是方框。 */
QTableWidget {{ background:{surface}; alternate-background-color:{surface_hi};
    gridline-color:transparent; border:1px solid {border}; border-radius:{xl}px;
    selection-background-color:{selection}; selection-color:{text};
    font-size:{body_sm}px; }}
QTableWidget::item {{ padding:{xs}px {sm}px; border:0; }}
QHeaderView::section {{ background:{surface_hi}; color:{text_dim}; border:0;
    border-bottom:1px solid {border}; padding:{sm}px {md}px;
    font-weight:700; font-size:{caption}px; letter-spacing:.6px; }}
QHeaderView::section:hover {{ color:{text}; }}
QTableCornerButton::section {{ background:{surface_hi}; border:0; }}

/* ===== 标签页（内容区内的次级切换） ===== */
QTabWidget::pane {{ border:1px solid {border}; border-radius:{xl}px; background:{surface}; }}
QTabBar::tab {{ background:transparent; color:{text_dim}; padding:{xs}px {lg}px;
    border:0; border-bottom:2px solid transparent; font-size:{body}px; }}
QTabBar::tab:hover {{ color:{text}; }}
QTabBar::tab:selected {{ color:{text}; border-bottom-color:{primary}; font-weight:700; }}

/* ===== 滚动条（细、圆角、悬停才明显） ===== */
QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
QScrollBar::handle:vertical {{ background:{border_hi}; border-radius:5px; min-height:36px;
    margin:2px; }}
QScrollBar::handle:vertical:hover {{ background:{text_faint}; }}
QScrollBar:horizontal {{ background:transparent; height:10px; }}
QScrollBar::handle:horizontal {{ background:{border_hi}; border-radius:5px; min-width:36px;
    margin:2px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* ===== 状态栏 ===== */
QStatusBar {{ background:{surface}; border-top:1px solid {border};
              color:{text_dim}; font-size:{caption}px; }}
QStatusBar::item {{ border:0; }}
QProgressBar {{ background:{surface_hi}; border:0; border-radius:3px; height:4px;
                color:transparent; }}
/* 进度条走到哪一目了然还不够——它得**看起来在动**：
   主色 → 次级强调色（分裂互补）的横向渐变给出一段"流向"。 */
QProgressBar::chunk {{ background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
    stop:0 {primary}, stop:1 {accent2}); border-radius:3px; }}

/* ===== 菜单 / 命令面板 ===== */
QMenu {{ background:{surface_raised}; border:1px solid {border_hi};
         border-radius:{lg}px; padding:{xs}px; }}
QMenu::item {{ padding:{xs}px {xl}px {xs}px {md}px; border-radius:{md}px; font-size:{body}px; }}
QMenu::item:selected {{ background:{primary}; color:{on_primary}; }}
QMenu::separator {{ height:1px; background:{border}; margin:{xs}px {sm}px; }}
#Palette {{ background:{surface_raised}; border:1px solid {border_hi}; border-radius:{xl}px; }}
#PaletteList {{ background:transparent; border:0; font-size:{body}px; }}
#PaletteList::item {{ padding:{sm}px {md}px; border-radius:{lg}px; }}
#PaletteList::item:selected {{ background:{primary}; color:{on_primary}; }}
#Toast {{ background:{surface_raised}; border:1px solid {border_hi}; border-radius:{lg}px; }}
QFrame#Sep {{ background:{border}; }}
QSplitter::handle {{ background:transparent; }}
QSplitter::handle:horizontal {{ width:6px; }}
QSplitter::handle:hover {{ background:{primary_soft}; }}
""".format(
        bg=p.bg, surface=p.surface, surface_hi=p.surface_hi, surface_raised=p.surface_raised,
        border=p.border, border_hi=p.border_hi, border_focus=p.border_focus,
        text=p.text, text_dim=p.text_dim, text_faint=p.text_faint, text_disabled=p.text_disabled,
        primary=p.primary, primary_hi=p.primary_hi, primary_soft=p.primary_soft,
        primary_text=p.primary_text, on_primary=p.on_primary, hover_hi=p.hover_hi,
        accent2=p.accent2,
        danger=p.danger, hover=p.hover, selection=p.selection,
        font=t.font, body=int(ty.body), body_sm=int(ty.body_sm), body_md=int(ty.body_md),
        caption=int(ty.caption), micro=int(ty.micro), display=int(ty.display),
        xs=sp.xs, sm=sp.sm, md=sp.md, lg=sp.lg, xl=sp.xl,
        )


# ------------------------------------------------------------------ 字体

#: 等宽字体栈。**工业仪表的底线**：数字必须等宽，否则列宽会随读数抖动
#: （"1" 和 "8" 宽度不同），而且眼睛没法沿竖线扫一列数。
#: QSS 的 font-family 不支持多族回退链，所以这里只取栈首，配合
#: mono_qfont() 的 styleHint 做双保险（族名找不到时 Qt 自动落系统等宽）。
MONO_STACK = ("Cascadia Mono", "Cascadia Code", "Consolas", "SF Mono", "Menlo",
              "DejaVu Sans Mono", "Liberation Mono", "Courier New")


_MONO_CACHE: str | None = None


def mono_family() -> str:
    """返回本机真实可用的等宽字体族名。找不到返回空串。

    **没有 QApplication 实例时必须返回空串，不能继续往下走。**
    `QFontDatabase` 是依赖 QGuiApplication 存在的，在它之前调用会
    **让进程直接 abort**（不是抛 Python 异常，是 C 层终止，连 traceback
    都打不全）。实测：在 pytest 里跑一个只读 qss() 的测试就会崩在
    `test_qss_uses_type_scale_only`。

    调用方对此的约定：空串时退回 "monospace" 通用名，QSS 照样合法。

    与 load_cjk_fonts() 同理：**先显式注册字体文件再查**。只查
    families() 不够——直接写死族名（如 "Cascadia Mono"）在本机没装时
    会静默回退到比例字体，等宽等于没做。
    结果缓存，避免每次 qss()/mono_qfont() 都去扫字体库。
    """
    global _MONO_CACHE
    if _MONO_CACHE is not None:
        return _MONO_CACHE
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        return ""                       # 不缓存：有了 app 之后还要重新探测
    for path in _MONO_FILES:
        if os.path.exists(path):
            idx = QFontDatabase.addApplicationFont(path)
            if idx >= 0:
                QFontDatabase.applicationFontFamilies(idx)
    have = set(QFontDatabase.families())
    for f in MONO_STACK:
        if f in have:
            _MONO_CACHE = f
            return f
    _MONO_CACHE = ""
    return ""


#: 常见等宽字体文件（Windows / macOS / Linux 各给一个）
_MONO_FILES = [
    r"C:\Windows\Fonts\consola.ttf",
    r"C:\Windows\Fonts\Consolas.ttf",
    r"C:\Windows\Fonts\lucon.TTF",
    r"C:\Windows\Fonts\cour.ttf",
    "/System/Library/Fonts/Menlo.ttc",
    "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
]


def pick_text_font(text: str, size_pt: float, family: str = ""):
    """按**内容**选字体：纯 ASCII 才给等宽，含中文就走中文比例字体。

    为什么按内容而不是按列、更不是按键名：等宽是为了"数字沿竖线对齐"，
    中文没有对齐需求；而等宽族（Consolas / DejaVu Sans Mono）**没有汉字字形**，
    中文落在上面只能靠 Qt 的逐字形回退——**落到哪个字体不可控**，实测在部分
    机器上直接是方框（用户报的"字乱码"就是这个）。

    早先两处踩了同一个坑：`KVTable` 按**键名**决定等宽（给中文值套了等宽），
    QSS 又把整张表设成等宽（元数据页成片中文全走回退路径）。
    """
    from PySide6.QtGui import QFont

    if text.isascii():
        return mono_qfont(size_pt)
    f = QFont(family or "Microsoft YaHei")
    f.setPointSizeF(size_pt)
    return f


def mono_qfont(size_pt: float):
    """构造一个等宽 QFont。用 styleHint 兜底，不依赖族名一定存在。"""
    from PySide6.QtGui import QFont
    f = QFont()
    f.setStyleHint(QFont.Monospace)          # 族名缺失时落到系统等宽
    f.setFamilies(list(MONO_STACK))
    f.setFixedPitch(True)
    f.setPointSizeF(size_pt)
    return f


# ------------------------------------------------------------------ 字体

def load_cjk_fonts() -> list[str]:
    """Qt6 不再自带字体；缺中文字体会整屏方框，这里主动注册系统字体。"""
    cands = [
        r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyhl.ttc",
        r"C:\Windows\Fonts\msjh.ttc", r"C:\Windows\Fonts\simhei.ttf", r"C:\Windows\Fonts\Deng.ttf",
        r"C:\Windows\Fonts\YuGothM.ttc", r"C:\Windows\Fonts\meiryo.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    ]
    fams: list[str] = []
    for path in cands:
        if os.path.exists(path):
            idx = QFontDatabase.addApplicationFont(path)
            if idx >= 0:
                fams += QFontDatabase.applicationFontFamilies(idx)
    return fams


def pick_family(fams: list[str],
                prefer=("Microsoft YaHei UI", "Microsoft YaHei", "PingFang SC",
                        "Noto Sans CJK SC", "Meiryo")) -> str:
    for want in prefer:
        for f in fams:
            if f.lower().startswith(want.lower()):
                return f
    return fams[0] if fams else "Sans Serif"


def mpl_cjk_font() -> str | None:
    """只经 matplotlib 自己的字体表挑中文字体，**完全不碰 Qt**。

    为什么要单独一个函数：`load_cjk_fonts()` 走 `QFontDatabase`，
    而没有 `QApplication` 时它会直接在 C 层 abort（进程终止、traceback 打不全，
    接手文档 §4 第 14 条记过同样的坑）。纯脚本场景（出图、报告）需要一条
    不依赖 Qt 事件循环的路。本机实测这两条路给出同一个族名。
    """
    import matplotlib.font_manager as fm  # 3.14 起不再随 matplotlib 自动导入
    names = {f.name for f in fm.fontManager.ttflist}
    for want in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "SimHei", "Meiryo"):
        if want in names:
            return want
    return None


def mpl_font(fams: list[str]) -> str | None:
    """给 matplotlib 也挑一个能显示中文的字体族。"""
    import matplotlib.font_manager as fm  # 3.14 起不再随 matplotlib 自动导入
    for want in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "Meiryo", "SimHei"):
        for f in fams + [x.name for x in fm.fontManager.ttflist]:
            if want.lower() in f.lower():
                return want
    return None


# ------------------------------------------------------------------ 矢量图标

def make_pixmap(name: str, color: str, size: int = 18, weight: float = 1.6) -> QPixmap:
    """手绘矢量图标：统一圆头线帽、圆角连接，保证整套图标风格一致。"""
    pm = QPixmap(size, size)
    pm.fill(Qt.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing, True)
    s = float(size)
    pen = QPen(QColor(color))
    pen.setWidthF(weight)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    pr.setPen(pen)
    pr.setBrush(Qt.NoBrush)
    m = s * 0.22

    if name == "add":
        pr.drawLine(QPointF(s / 2, m), QPointF(s / 2, s - m))
        pr.drawLine(QPointF(m, s / 2), QPointF(s - m, s / 2))
    elif name == "folder":
        path = QPainterPath()
        path.moveTo(m, s - m)
        path.lineTo(m, m + s * 0.14)
        path.lineTo(s * 0.42, m + s * 0.14)
        path.lineTo(s * 0.52, m + s * 0.30)
        path.lineTo(s - m, m + s * 0.30)
        path.lineTo(s - m, s - m)
        path.closeSubpath()
        pr.drawPath(path)
    elif name == "play":
        path = QPainterPath()
        path.moveTo(m + s * 0.06, m)
        path.lineTo(s - m, s / 2)
        path.lineTo(m + s * 0.06, s - m)
        path.closeSubpath()
        pr.setBrush(QColor(color))
        pr.drawPath(path)
    elif name == "stop":
        r = s * 0.26
        pr.setBrush(QColor(color))
        pr.drawRoundedRect(QRectF(r, r, s - 2 * r, s - 2 * r), 2.2, 2.2)
    elif name == "export":
        pr.drawLine(QPointF(s / 2, m), QPointF(s / 2, s * 0.64))
        pr.drawPolyline(QPolygonF([QPointF(s * 0.31, s * 0.46), QPointF(s / 2, s * 0.66),
                                   QPointF(s * 0.69, s * 0.46)]))
        pr.drawLine(QPointF(m, s - m), QPointF(s - m, s - m))
    elif name == "trash":
        pr.drawLine(QPointF(m, m + s * 0.16), QPointF(s - m, m + s * 0.16))
        pr.drawLine(QPointF(s * 0.40, m + s * 0.16), QPointF(s * 0.40, m + s * 0.05))
        pr.drawLine(QPointF(s * 0.60, m + s * 0.16), QPointF(s * 0.60, m + s * 0.05))
        path = QPainterPath()
        path.moveTo(m + s * 0.07, m + s * 0.16)
        path.lineTo(m + s * 0.12, s - m)
        path.lineTo(s - m - s * 0.12, s - m)
        path.lineTo(s - m - s * 0.07, m + s * 0.16)
        pr.drawPath(path)
    elif name == "search":
        pr.drawEllipse(QPointF(s * 0.44, s * 0.44), s * 0.24, s * 0.24)
        pr.drawLine(QPointF(s * 0.62, s * 0.62), QPointF(s * 0.83, s * 0.83))
    elif name == "gear":
        import math
        pr.drawEllipse(QPointF(s / 2, s / 2), s * 0.17, s * 0.17)
        for i in range(8):
            a = i * math.pi / 4
            p1 = QPointF(s / 2 + math.cos(a) * s * 0.26, s / 2 + math.sin(a) * s * 0.26)
            p2 = QPointF(s / 2 + math.cos(a) * s * 0.36, s / 2 + math.sin(a) * s * 0.36)
            pr.drawLine(p1, p2)
    elif name == "sun":
        import math
        pr.drawEllipse(QPointF(s / 2, s / 2), s * 0.16, s * 0.16)
        for i in range(8):
            a = i * math.pi / 4
            p1 = QPointF(s / 2 + math.cos(a) * s * 0.25, s / 2 + math.sin(a) * s * 0.25)
            p2 = QPointF(s / 2 + math.cos(a) * s * 0.36, s / 2 + math.sin(a) * s * 0.36)
            pr.drawLine(p1, p2)
    elif name == "moon":
        path = QPainterPath()
        path.moveTo(s * 0.66, m)
        path.arcTo(QRectF(m, m, s - 2 * m, s - 2 * m), 70, 220)
        path.closeSubpath()
        pr.setBrush(QColor(color))
        pr.setPen(Qt.NoPen)
        pr.drawPath(path)
    elif name == "chart":
        for i, hh in enumerate((0.30, 0.55, 0.42, 0.72)):
            x = m + i * (s - 2 * m) / 4
            w = (s - 2 * m) / 7
            pr.setBrush(QColor(color))
            pr.setPen(Qt.NoPen)
            pr.drawRoundedRect(QRectF(x, s - m - (s - 2 * m) * hh, w, (s - 2 * m) * hh), 1.6, 1.6)
    elif name == "layers":
        pr.drawLine(QPointF(m, s * 0.62), QPointF(s - m, s * 0.62))
        path = QPainterPath()
        path.moveTo(m, s * 0.62)
        path.cubicTo(s * 0.36, s * 0.20, s * 0.62, s * 0.86, s - m, s * 0.38)
        pr.drawPath(path)
    elif name == "wave":
        path = QPainterPath()
        path.moveTo(m, s / 2)
        for k in range(10):
            x = m + (s - 2 * m) * k / 9
            y = s / 2 + (s * 0.30 if k % 2 else -s * 0.30)
            path.lineTo(x, y)
        pr.drawPath(path)
    elif name == "grid":
        pr.setBrush(QColor(color))
        pr.setPen(Qt.NoPen)
        for i in range(3):
            for j in range(3):
                pr.drawRect(QRectF(m + i * (s - 2 * m) / 3 + 1, m + j * (s - 2 * m) / 3 + 1,
                                   (s - 2 * m) / 3 - 1.6, (s - 2 * m) / 3 - 1.6))
    elif name == "reload":
        pr.drawArc(QRectF(m, m, s - 2 * m, s - 2 * m), 40 * 16, 280 * 16)
        pr.drawPolyline(QPolygonF([QPointF(s - m - s * 0.16, m + s * 0.02),
                                   QPointF(s - m, m + s * 0.16),
                                   QPointF(s - m - s * 0.20, m + s * 0.26)]))
    elif name == "copy":
        pr.drawRoundedRect(QRectF(m, m + s * 0.12, s * 0.5, s * 0.58), 2.4, 2.4)
        pr.drawRoundedRect(QRectF(m + s * 0.20, m, s * 0.5, s * 0.58), 2.4, 2.4)
    elif name == "doc":
        pr.drawRoundedRect(QRectF(m + s * 0.06, m, s * 0.62, s - 2 * m), 2.4, 2.4)
        for i in range(3):
            y = m + s * (0.22 + i * 0.19)
            pr.drawLine(QPointF(m + s * 0.20, y), QPointF(m + s * 0.56, y))
    elif name == "shield":
        path = QPainterPath()
        path.moveTo(s / 2, m)
        path.lineTo(s - m, m + s * 0.16)
        path.lineTo(s - m, s * 0.55)
        path.quadTo(s - m, s - m, s / 2, s - m)
        path.quadTo(m, s - m, m, s * 0.55)
        path.lineTo(m, m + s * 0.16)
        path.closeSubpath()
        pr.drawPath(path)
    elif name == "sliders":
        for i, yy in enumerate((0.32, 0.52, 0.72)):
            pr.drawLine(QPointF(m, s * yy), QPointF(s - m, s * yy))
            pr.setBrush(QColor(color))
            pr.setPen(Qt.NoPen)
            pr.drawEllipse(QPointF(s * (0.35 + i * 0.15), s * yy), s * 0.07, s * 0.07)
            pen2 = QPen(QColor(color))
            pen2.setWidthF(weight)
            pr.setPen(pen2)
            pr.setBrush(Qt.NoBrush)
    pr.end()
    return pm


def icon(name: str, color: str = "#e8ecf3", size: int = 18) -> QIcon:
    """2x 渲染后再缩放，任意 DPI 都锐利。"""
    ic = QIcon()
    px = make_pixmap(name, color, size * 2, 1.9)
    ic.addPixmap(px, QIcon.Normal, QIcon.Off)
    ic.addPixmap(px, QIcon.Disabled, QIcon.Off)
    return ic


def app_icon(p: Palette = DARK_P) -> QIcon:
    """程序图标：频谱砖墙——低区密、高区骤停（本工具的立意为"看内容"）。"""
    pm = QPixmap(128, 128)
    pm.fill(Qt.transparent)
    pr = QPainter(pm)
    pr.setRenderHint(QPainter.Antialiasing, True)
    pr.setPen(Qt.NoPen)
    pr.setBrush(QColor(p.surface_raised))
    pr.drawRoundedRect(QRectF(4, 4, 120, 120), 26, 26)
    heights = [30, 52, 74, 62, 84, 70, 88, 66, 78, 40, 16, 6]
    for i, hh in enumerate(heights):
        x = 18 + i * 7.8
        pr.setBrush(QColor(p.primary if i < 9 else p.danger))
        pr.drawRoundedRect(QRectF(x, 112 - hh, 5.4, hh), 2.4, 2.4)
    pr.end()
    return QIcon(pm)
