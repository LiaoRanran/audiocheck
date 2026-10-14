"""设计系统：令牌 → QSS。

为什么要有令牌：手写 QSS 时颜色/间距会散落各处，改一处要全局搜。
这里把所有视觉参数集中成一组令牌，QSS 由令牌生成，主题切换只需换一组值。

令牌命名遵循「语义 > 外观」：
  surface / border / text_primary 而不是 #1b202b，
  这样加新组件时不需要发明新的色号。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field, fields

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

from . import palette as _palette

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
    """字阶。caption 到 display 共 8 级，覆盖全部场景，不允许出现第 9 种字号。

    正文取 **14px**：Butterick 的网页正文字号原文是 15–25px，但那是西文
    （有升降部、字身密度低）。汉字是 1:1 字身、无升降部，同 px 下实观更大、
    行里能塞的字更多，所以取原文区间的**下限**——这是本项目标了"判断"的折算，
    不是 Butterick 的原值（见 docs/ 的排版依据账）。
    """
    micro: float = 10.0      # 角标 / 单位
    caption: float = 11.0    # 字段标签
    body_sm: float = 12.0    # 表格 / 次要正文
    body: float = 14.0       # 正文
    body_md: float = 15.0    # 小节标题
    title: float = 16.0      # 卡片标题
    heading: float = 20.0    # 页标题
    display: float = 27.0    # Hero 数字

    #: **第 9 档，只给图表刻度**（B7.2 的唯一新增档）。它不参与 QSS 文本：
    #: 图表走 matplotlib，没有字距/行高令牌那条路径，所以 `Tracking` / `Leading`
    #: 里**故意没有**这一项——硬加一个没人用的字段只会让人以为它有落点。
    #:
    #: 为什么需要它：图表刻度有**物理约束**（标签一大就挤占绘图区），
    #: 而现有最小档 `micro=10` 在 8px 级刻度上偏大。加一档比"把刻度提到 10px
    #: 挤掉绘图区"更合理——实测改动前 `charts.py` 里有 **8 种表外字号**
    #: （7.0/7.5/7.8/8.0/8.5/9.0/9.5/19.0），其中 7 种挤在 7–9.5 之间，间距 0.5px。
    #: 0.5px 的间距等于"逐个调出来的"，不是按刻度选的——**没有刻度就没有秩序**。
    chart_tick: float = 9.0  # 图表刻度 / 图例 / 轴标注

    #: **第 10 档，`hero` = 40px。这是对"≤2.5 倍 / ≤5 种"纪律的
    #: 一次有理由破例**（B9·R4），理由必须留在这里，免得未来某轮当违规"修好"：
    #:
    #: * 那条纪律防的是"—每层都比旁边大一点"（十几档字号的 AI 味）。
    #: * **hero 是数据，不是层级。** 它是"这一屏最重要的一读数"，
    #:   层级语义与"标题/正文/说明"不在同一根轴上，所以不受"层级差 ≤2.5 倍"约束。
    #: * 约束换成了更严的一条：**一屏只准一个**（`test_ui_b9_layout.py` 扫描钉死）。
    #:   十档字号里，前面九档管"层级"，这一档管"焦点"。
    #: * 来历：核心读数原先被压在 128px 环形仪表里只有 21px
    #:   （`type.heading - 4.5`），用户判"字体还是依然这么拥挤"。
    #:   同一份数据放大到 40px 之后，它才真的是"一屏的读数"。
    #: * 嫌大就改这一个数（34 也在小样里提过），不要动别的档去"配合"它。
    hero: float = 40.0       # 一屏唯一的核心读数


@dataclass(frozen=True)
class Tracking:
    """字距令牌（光学尺寸）：**字号越大，字距越紧**。

    这是经典光学尺寸规律，不是审美偏好：同一副字在大字号下笔画间隙相对变宽，
    需要收得更紧才保持同样的"灰度"；而 10–11px 的小字必须放宽，否则笔画糊在一起。

    Qt 的两条实现路径（**都实测过**，别改）：
      * QSS 的 `letter-spacing` **有效**（2px 让 12 个字符宽了 24px）；
      * `QFont.setLetterSpacing()` 也有效（用于代码里直接设字体的地方）。
    两者可以共存，但**值必须来自本表**——手写的 1.1px / 1.2px 就是上一轮的样子。
    """
    micro: float = 0.6
    caption: float = 0.5
    body_sm: float = 0.2
    body: float = 0.0
    body_md: float = 0.0
    title: float = -0.1
    heading: float = -0.2
    display: float = -0.3

    #: 西文**全大写**微标签的额外字距比例。Butterick：全大写可接受（少于一行），
    #: 但要配 5–12% 额外字距。取 8%。中文标签**不**转大写——转了对中文无意义。
    caps_pct: float = 0.08

    def caps(self, size: float) -> float:
        """全大写标签在该字号下的字距（px）。"""
        return round(size * self.caps_pct, 2)


@dataclass(frozen=True)
class Leading:
    """行高令牌（倍率）。

    **QSS 不支持 `line-height`**——实测往 QSS 里写 60px，控件高度一点不变，
    而且**不报错、不告警**（又是"看起来做了其实没生效"那一类）。
    所以本表的落点只有两处，都写在各自的调用点上：
      1. `theme.rich_text()`：多行文本走 Qt RichText，它的 CSS 子集支持
         `line-height`（实测 20px 让三行文本高 18px，200% 高 42px）；
      2. HTML 报告（`report.py`）：浏览器渲染，天然支持。
    单行标签仍用字体自然行距——那条路没有可用的令牌落点，登记为已知缺口，
    不要为了"看起来令牌被用到了"而把它塞进不生效的地方。
    """
    micro: float = 1.40
    caption: float = 1.45
    body_sm: float = 1.50
    body: float = 1.55
    body_md: float = 1.50
    title: float = 1.35
    heading: float = 1.25
    display: float = 1.15


@dataclass(frozen=True)
class Motion:
    """动效时长（毫秒）。**这是动画时长的唯一来源。**

    B3 定的规则，两句话、各自有测试：
      * **动效时长**（`motion.fade/pulse/stagger` 的 `ms`、`setDuration`）
        只许来自本表或 `motion.FAST|BASE|SLOW`（它们是本表的别名）。
        动画时长散落时，想调"整体手感"要全局搜——而 `Motion` 那三个令牌
        会退化成"声明了但没落点"。
      * **节流/去抖间隔**（`setInterval`、合并刷新的窗口）**不是动效时长**，
        不塞进本表：它们各自的值都跟一段具体性能事实绑在一起
        （28 ms 是"人眼看不出合并"、120 ms 是"跟文件到达同量级"），
        搬到表里就离家了。要求是**必须具名**并在原处写清为什么是这个值。

    Qt 的插值范围是 100–300 ms（超过 300 会觉得迟钝），本表在此区间内。
    """
    fast: int = 120          # 悬停
    base: int = 200          # 展开/切换/内容到达
    slow: int = 320          # 抽屉（大容器）


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
    # 次级强调色：**冷 counterpart**（色相 = 中性阶锚点 286°，同明度同彩度）。
    # 它与 primary 构成"一枚暖 + 一枚冷"的一对；第三枚及以后一律走 deco 色阶，
    # 不许再新增强调色（B 块配额）。只用于数据可视化与装饰，**不承担语义**。
    accent2: str = "#a06cf0"
    #: **装饰色阶**（6 档，六等分色环）：并列关系的多色——侧边栏图标、卡片强调线、
    #: 多文件对比的第 3..10 条曲线。它同样**不承担语义**：语义只有 success /
    #: warning / danger 三个固定色相（判定胶囊）。
    #: 为什么一个字段装 6 个值：它们是"一组等重的声音"，按 L 排序、按色相等分，
    #: 逐项命名（deco1..deco6）只会引诱别人单独挑某一档的颜色。
    deco: tuple = ()
    # 主按钮上的文字。**不是所有主色都配得起白字**：白字压在 #3d7dff 上只有
    # 3.77:1（低于 AA 4.5），压在悬停色 #5a92ff 上只有 3.00:1。
    # 两条路——压深主色（品牌色变闷）或改用墨字。选后者：种子色/品牌色的
    # 辨识度是这套设计系统的前提，文字颜色本来就该去适配底色。
    on_primary: str = "#0d0f13"
    success: str = "#35c46a"
    warning: str = "#e0a92e"
    danger: str = "#f0524a"
    info: str = "#38bdf8"
    #: `danger` 的**按下**色（比它深一档）。只给窗口关闭按钮用：那个按钮的
    #: 悬停已经是全红，按下若还是同一个红就等于没有反馈。见 `palette.build()`。
    danger_lo: str = "#c23b34"

    # 语义色的背景态（用于徽标/胶囊底）
    success_bg: str = "#0c2015"
    warning_bg: str = "#221a09"
    danger_bg: str = "#281110"
    info_bg: str = "#0b1e2a"
    success_bd: str = "#1d5a33"
    warning_bd: str = "#5e4818"
    danger_bd: str = "#6b2521"
    info_bd: str = "#12506a"

    #: **判定区**（B9.1 起两个角色，命名不许混）：
    #:   `*_tint` —— 大面积**淡判定底**，承载判词文字（占位积，低彩 C≈0.04）
    #:   `*_band` —— 4px **判定带**，视觉重量全在这里（高彩 C≈0.125）
    #: 拆开的原因（诊断文档 §三·根因 4）：B7.1 把"判定语义面"与"高彩面积"
    #: 绑在同一块 880×140 的色上——**面积百分比不等于视觉重量**，同样 3.5%，
    #: 横贯全宽的色带比一小块光斑重好几倍。拆开后两者各自可调。
    #: 对比度判据逐条在 `palette.CHECKS`（文字压 tint 4.5 / 带对 tint 3.0 /
    #: tint 对卡片底 1.5）。
    success_tint: str = "#1b2a20"
    warning_tint: str = "#2a2519"
    danger_tint: str = "#2b1d1e"
    success_band: str = "#2f9e4e"
    warning_band: str = "#9c7a12"
    danger_band: str = "#c8433c"
    tint_dim: str = "#aaaab2"

    # 选中 / 悬停叠加
    selection: str = "#1a2740"
    hover: str = "#1a1f29"
    # 按钮悬停底色。**单独一个令牌是必须的**：QSS 里原来写死 `#1c212b`，
    # 于是浅色主题下悬停按钮会把深色文字画到近黑底上（对比度 ≈1.1:1，字没了）。
    hover_hi: str = "#1c212b"

    # 图表
    grid: str = "#1e2430"
    plot_bg: str = "#0d1016"

    # 阴影。**Qt 的 QSS 没有 box-shadow**（实测写了不报错也不生效），
    # 所以这里是给自绘与效果对象用的参数令牌：双档（near 同层浮层 /
    # far 跨层浮层），颜色偏冷中性而非纯黑，理由见 palette.SHADOW_TIERS。
    #
    # B7.4：旧的单档 `shadow` 是 `rgba(0,0,0,.45)`——**纯黑**。
    # 依据：「pure black creates a cutout effect… like cardboard cutouts」
    # （硬纸板剪影）：纯黑阴影压在冷灰底上，交界处会显灰雾，物体像贴上去的。
    # 改用与中性阶同族的近黑冷调（slate-950 一族），保留旧名字以兼容
    # 已有调用点，但值已经换掉。
    shadow: str = "rgba(2,6,23,.45)"
    shadow_near: str = "rgba(2,6,23,.45)"
    shadow_far: str = "rgba(2,6,23,.22)"

    # 顶光高亮：浮层上缘那 1px（光从上方来的视觉线索）。见 palette.build()。
    top_light: str = "#39414f"

    def level_color(self, level: str) -> str:
        return {"ok": self.success, "warn": self.warning, "bad": self.danger}.get(
            level, self.text_dim)

    def level_bg(self, level: str) -> str:
        return {"ok": self.success_bg, "warn": self.warning_bg,
                "bad": self.danger_bg}.get(level, self.surface_hi)

    def level_bd(self, level: str) -> str:
        return {"ok": self.success_bd, "warn": self.warning_bd,
                "bad": self.danger_bd}.get(level, self.border_hi)

    def level_tint(self, level: str) -> str:
        """判定区**淡底**（大面积、低彩、承载判词文字）。"""
        return {"ok": self.success_tint, "warn": self.warning_tint,
                "bad": self.danger_tint}.get(level, self.surface_hi)

    def level_band(self, level: str) -> str:
        """判定区**细带**（4px、高彩、判定语义的视觉重量所在）。"""
        return {"ok": self.success_band, "warn": self.warning_band,
                "bad": self.danger_band}.get(level, self.border_hi)


def plot_palette(p: Palette, t: Theme | None = None) -> dict:
    """图表层（matplotlib）用的字典式调色适配。

    画布代码历史上按旧键名（surface2 / accent / ok / warn_border…）取值。
    与其改动画布，不如在这里做一次**显式**键名映射——映射表集中可见，
    将来统一画布命名后删掉本函数即可。

    B7.2 起**顺带带上字号**（`fs_*` 键）：matplotlib 拿不到 QSS，画布只持有
    这个字典。把字号塞进同一本字典，是让"图表字号来自 `Type` 表"这件事
    只需要一条通道，而不是在画布各处 import theme。
    `t` 省略时退回 `Type()` 的默认值——**仍然来自同一张表**，不是另一套数字。
    """
    ty = (t or Theme(name="default", p=p)).type
    return {
        "fs_tick": ty.chart_tick, "fs_micro": ty.micro,
        "fs_caption": ty.caption, "fs_body_sm": ty.body_sm,
        "fs_hero": ty.heading,
        "bg": p.bg, "surface": p.surface, "surface2": p.surface_hi, "surface3": p.surface_raised,
        "border": p.border, "border_hi": p.border_hi,
        "text": p.text, "text_dim": p.text_dim, "text_faint": p.text_faint,
        "accent": p.primary, "accent_hi": p.primary_hi, "accent_soft": p.primary_soft,
        "accent2": p.accent2, "deco": p.deco,
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


#: 发货字体目录（OFL，随包）。**这是"零字体教训"的真修**（B10/B10.5 → B13 §2A）：
#: 此前每一轮都在**降级字体**上验收——系统没装就落到 YaHei，而 YaHei 只有
#: 400/700 两档字重（B9 实测：写 `font-weight:600` 被吸附到 700，层级是假的）。
#: 捆绑 + 断言之后，"字体在场"从祈祷变成启动时的事实。
BUNDLED_FONT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

#: 目标族名（**实测值**，不是猜的：`out/_b13_fams.py` 逐个 file → families() 打出）。
#: 九个文件注册出三个族，Noto 是可变字体（VF）单文件覆盖全部字重。
BUNDLED_FAMILIES = ("IBM Plex Sans", "IBM Plex Mono", "Noto Sans SC")

_LOADED_BUNDLED: list = []
#: 发货字体是否装载成功。装载成功 ⇒ `qss()` **不再写 `font-family`**：
#: QSS 的单族名会**覆盖** app 级 `setFont(setFamilies([Plex, Noto]))` 的真回退链，
#: 于是中文被推给逐字形回退（本项目在等宽字体上踩过、B9 的注释也记着）。
#: 所以"QSS 不写多族假链"的正确落点是**不写**，让 app 字体的链说话。
_BUNDLED_ACTIVE: list = []


def load_bundled_fonts(assert_present: bool = True) -> list:
    """装载 `assets/fonts/` 下全部字体，并**断言三个族在手**（缺族 fail loudly）。

    为什么非断言不可：`addApplicationFont` 失败时**返回 -1 且不抛异常**，
    于是静默落回系统字体——本项目在字体上已踩三次（QSS 多族回退、
    无 QApplication 就查 QFontDatabase、离屏下 `families()` 列不出系统字体）。
    没有断言，"装载了"就只是希望。
    """
    from PySide6.QtGui import QFontDatabase

    if _LOADED_BUNDLED:
        return _LOADED_BUNDLED
    if not os.path.isdir(BUNDLED_FONT_DIR):
        if assert_present:
            raise RuntimeError("缺发货字体目录 %s（OFL 资产必须随包）" % BUNDLED_FONT_DIR)
        return []
    files = sorted(f for f in os.listdir(BUNDLED_FONT_DIR)
                   if f.lower().endswith((".ttf", ".otf")))
    if not files:
        raise RuntimeError("%s 里没有字体文件" % BUNDLED_FONT_DIR)
    for f in files:
        idx = QFontDatabase.addApplicationFont(os.path.join(BUNDLED_FONT_DIR, f))
        if idx < 0:
            raise RuntimeError("字体装载失败：%s（addApplicationFont 返回 -1）" % f)
    have = set(QFontDatabase.families())
    missing = [x for x in BUNDLED_FAMILIES if x not in have]
    if missing and assert_present:
        raise RuntimeError(
            "发货字体缺族 %s——`families()` 只有 %d 个。"
            "⚠ 若为0，说明未走（addApplicationFont）——离屏环境列不出系统字体，"
            "这正是本项目记过三次的坑）" % (missing, len(have)))
    _LOADED_BUNDLED.extend(files)
    _BUNDLED_ACTIVE.append(True)      # 供 qss() 判断：装载成功就不再写 font-family
    return _LOADED_BUNDLED


def init_fonts(app=None) -> str:
    """装载**发货字体**（OFL，带断言）→ 设成应用字体 → 写进主题令牌。
    **必须在建窗口之前调用。**

    与旧版的区别：先把 `assets/fonts/` 的 OFL 字体装进去（Plex 管西文/数字、
    思源管中文），**断言三个族都在**；然后才走原有的中文字体解析链作为兜底
    （其他机器上缺 Plex 时仍能出中文，只是会记录为降级）。
    """
    primary = ""
    try:
        load_bundled_fonts()
        # ⚠ 这里**不走 QSS**：QSS 的 `font-family` 多族回退链在 Qt 里不可靠
        # （本项目用过一次等宽字体踩过）。`setFamilies` 才是真回退链：
        # 拉丁走 Plex、中文缺字时落思源。
        primary = BUNDLED_FAMILIES[0]
    except RuntimeError as e:
        # 不静默：缺资产是配置错误，不是运行时小毛病
        print("⚠ 发货字体未装载：%s" % e)
    fams = load_cjk_fonts()
    fam = pick_family(fams)
    if app is not None:
        f = QFont(primary or fam, 10)
        if primary:
            f.setFamilies([BUNDLED_FAMILIES[0], BUNDLED_FAMILIES[2]])
        app.setFont(f)
    # **唯一真源**（B14 §4-①）：`ui_family()`、`init_fonts()` 的返回值、
    # app 的主族必须是同一个名字。发货字体在场 ⇒ 一律 "IBM Plex Sans"；
    # 没装载 ⇒ 落回系统解析出的中文字体（降级路径，会打印告警）。
    set_ui_family(primary or fam)
    return primary or fam


@dataclass(frozen=True)
class Theme:
    name: str
    p: Palette
    space: Space = field(default_factory=Space)
    radius: Radius = field(default_factory=Radius)
    type: Type = field(default_factory=Type)
    tracking: Tracking = field(default_factory=Tracking)
    leading: Leading = field(default_factory=Leading)
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

    @property
    def figure(self) -> str:
        """西文/数字标签用的 **DIN 系**族名（带引号，可直接进 QSS）。

        为什么单独一个族而不全用等宽：等宽（Consolas）解决"数字沿竖线对齐"，
        DIN 系（Bahnschrift）解决"西文标签的气质"——它是 1931 年德国标准协会
        为交通标识与工程绘图设计的字，血统就是仪器。两者是**不同的职责**，
        见 `figure_qfont()` 的 docstring：实测 Bahnschrift 的数字**不是**等宽，
        所以"要对齐的列"仍然走 mono，DIN 只用于不需要对齐的西文标签。

        本机没有 DIN 系时退回中文字体族——它照样能渲染西文，只是没有那味。
        **不能退回 `self.font`**：那个属性自带引号与 `,sans-serif` 后缀，
        再包一层会产出 `""Microsoft YaHei UI",sans-serif",sans-serif` 这种
         Qt 解析不了的怪串（第一次写就成了这样，QSS 会静默整条失效）。
        """
        fam = figure_family() or _UI_FAMILY or self.font_family
        return '"%s",sans-serif' % fam


#: 两个内置主题的种子色与风格档。
#: **它们是算出来的，不是手挑的色号**：下面两个 `Palette` 直接由
#: `palette.build(seed, mode, vibe)` 生成（OKLCH + WCAG 判据 +
#: 装饰色阶可见性，全部 strict 自审）。想换"内置主题长什么样"，
#: 改这两个常量再跑 `python packaging/palette_report.py` 核对，
#: 不要直接改下面的色值——手改的色号不会自己达标（这个坑踩过三次）。
#
# 🔻 **2026-10-13 换锚点（B 块，用户判决"仪器感/配额制配色"）**：
#    seed: #7c5cff（霓虹紫） → **#f97083（玫瑰）**
#    vibe: pop → **calm**
#
# 两处都是**判断**，不是实测结论，理由分开写：
#   · 种子色：B 块参考图的强调色实测在 OKLCH H≈14°/C≈0.168/L≈0.715，
#     换算成 sRGB 落在 #f97083 附近。玫瑰 + 冷灰是一组经典仪器配色
#     （暖色只用在"要你注意"的地方，其余全是冷灰）。
#   · vibe 档：pop 的表面彩度是 0.030，而参考图的中性簇实测 C≈0.010——
#     差 3 倍，不是"同一种气质的不同程度"。配额制（中性 ≤0.0125、
#     高彩面积 ≤5%）如果不管默认档，它就只是文档里的一句话。
#     **calm/pop 的取值一个都没改**（0.012 / 0.030 原样保留），
#     改的只是"默认用哪一档"；pop 仍在 Ctrl+M 与种子色对话框里可选。
BUILTIN_SEED = "#f97083"
BUILTIN_VIBE = "calm"

DARK_P = Palette(name="dark", **_palette.build(BUILTIN_SEED, "dark", BUILTIN_VIBE))
LIGHT_P = Palette(name="light", **_palette.build(BUILTIN_SEED, "light", BUILTIN_VIBE))

DARK = Theme("dark", DARK_P)
LIGHT = Theme("light", LIGHT_P)
THEMES = {"dark": DARK, "light": LIGHT}

LEVEL_CN = {"ok": "合格", "warn": "有疑", "bad": "不达标"}
LEVEL_COLOR_KEY = {"ok": "ok", "warn": "warn", "bad": "bad"}


# ------------------------------------------------------------------ QSS 生成

def qss(t: Theme) -> str:
    p, sp, ty = t.p, t.space, t.type
    tk = t.tracking
    return """
/* ===== 基础 ===== */
* {{ outline: 0; }}
QWidget {{ background:{bg}; color:{text}; font-size:{body}px; {font_css} }}
QMainWindow, QDialog {{ background:{bg}; }}
QToolTip {{ background:{surface_raised}; color:{text}; border:1px solid {border_hi};
            border-top:1px solid {top_light};
            padding:{xs}px {sm}px; border-radius:{r_md}px; }}

/* ===== 标题栏（无边框窗口自绘） ===== */
/* 光的方向：窗口自上而下变亮一档，读起来像"光从上面来"。
   整块纯色会很呆；而渐变只用了两个已有令牌，没有新增任何色号。 */
#TitleBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
           stop:0 {surface_hi}, stop:1 {surface}); border-bottom:1px solid {border}; }}
#TitleText {{ font-size:{body_sm}px; color:{text_dim}; letter-spacing:{tr_body_sm}px; }}
#WinBtn {{ background:transparent; border:0; border-radius:{r_md}px; }}
#WinBtn:hover {{ background:{hover}; }}
/* 窗口按钮（最小化/最大化/关闭）是"按下即生效"的：按下态要比悬停更实，
   否则点下去的那几十毫秒里界面没有任何反馈。 */
#WinBtn:pressed {{ background:{hover_hi}; }}
#WinBtn#Close:hover {{ background:{danger}; }}
#WinBtn#Close:hover {{ color:#fff; }}
#WinBtn#Close:pressed {{ background:{danger_lo}; }}

/* ===== 侧边栏 ===== */
#SideBar {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
           stop:0 {surface}, stop:1 {bg}); border-right:1px solid {border}; }}
#BrandName {{ font-size:{body_md}px; font-weight:700; color:{text}; letter-spacing:{tr_body_md}px; }}
#BrandSub {{ font-size:{micro}px; color:{text_faint}; letter-spacing:{tr_micro}px; }}
/* 品牌下的三色条：**只用装饰色阶**（deco[0]/[2]/[4]）。
   它不承担任何语义，也没有读数功能——存在的唯一理由是让"这是什么工具"
   在一屏灰里被认出来。三个色值全部来自令牌，没有新增色号。
   **刻意不是渐变**：渐变彩条已在本轮退场（用户判决"合成波陈词滥调"），
   改成三个平色段——仍然是"这一组是并列的几档"，但不带合成波那味。 */
#BrandBar {{ background:{deco0}; border-radius:2px; }}
#BrandBarSeg1 {{ background:{deco2}; border-radius:2px; }}
#BrandBarSeg2 {{ background:{deco4}; border-radius:2px; }}
#NavItem {{ background:transparent; border:0; border-radius:{r_lg}px;
            padding:{sm}px {md}px; color:{text_dim}; text-align:left; font-size:{body}px; }}
#NavItem:hover {{ background:{hover}; color:{text}; }}
#NavItem:pressed {{ background:{hover_hi}; }}
#NavItem[active="true"] {{ background:{primary_soft}; color:{primary_text}; font-weight:700; }}
/* NavSection 是西文全大写微标签 → 走 caps 字距（Butterick：全大写配 5–12% 额外
   字距，取 8%）。原来这里手写 1.1px，现在由 `Tracking.caps()` 算出来。 */
#NavSection {{ color:{text_faint}; font-size:{micro}px; font-weight:700;
              letter-spacing:{tr_caps_micro}px; padding:{md}px {md}px {xs}px; }}

/* ===== 卡片 / 分区 ===== */
QFrame#Card, QWidget#Card {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {surface_hi}, stop:1 {surface}); border:1px solid {border};
                             border-radius:{r_xl}px; }}
/* 指标卡顶端的 2px 色条：**从装饰色阶撤出，改中性顶光**（B7.1 做法 2）。
   来历：18 张卡的色条连成 6 条横贯全屏的彩色长条，实测贡献了**全图高彩的
   42.4%** 却只引入噪声（勘误 §5.2 逐行扫描量到的）。它们很细，面积判据抓不到，
   但把"一枚 accent"稀释成了六个色相（主族占比只有 64.1%）。
   现在它承担的还是同一件事——"18 张卡并排时，每张卡有自己的上缘"——
   只是用**明度**表达而不是**色相**：这是"顶光"的语言，与 B7.4 的"亮边"同源，
   不再新增一个色相。
   **为什么保留这块而不是删掉**：删了就退回"一整片同色块"，
   而"扫一眼回到同一格"是这张网格的实用功能，不是装饰。 */
QFrame#CardAccent {{ border:0; border-top-left-radius:{r}px;
    border-top-right-radius:{r}px; background:{top_light}; }}

/* ===== 判定区（淡底 + 细带 + 判词 + hero） ===== */
/* B9.1 的结构性改动：判定语义从"通栏高彩块"降维为
   **大面积淡底（tint）+ 4px 高彩带（band）**。
   来历（诊断 §三·根因 4）：B7.1 把"判定语义面"与"高彩面积"绑在同一块色上，
   而**面积百分比不等于视觉重量**——同样 3.5%，横贯全宽的色带比一小块光斑重
   好几倍。拆开后：tint 承担"判定语义有面积"，band 承担"高彩在说哪一档"。
   ⚠ 判据也随之修订：高彩面积目标 3.0–4.0% → **0.6–1.5%**（载体从块变线，
   面积下降是设计意图）。**不许为了凑面积把带子加粗回块**——那是走回头路。 */
QFrame#VerdictBanner {{ border:1px solid {border_hi}; border-radius:{r_xl}px; }}
QFrame#VerdictBanner[level="ok"] {{ background:{ok_tint}; }}
QFrame#VerdictBanner[level="warn"] {{ background:{warn_tint}; }}
QFrame#VerdictBanner[level="bad"] {{ background:{bad_tint}; }}
/* 4px 判定带：**视觉重量全在这里**。它按 `level` 取判定色原彩度，
   算进高彩配额；高度由代码固定为 4（判据钉 3–5px）。 */
QFrame#VerdictBand {{ border:0; }}
QFrame#VerdictBand[level="ok"] {{ background:{ok_band}; }}
QFrame#VerdictBand[level="warn"] {{ background:{warn_band}; }}
QFrame#VerdictBand[level="bad"] {{ background:{bad_band}; }}
/* 判词前的方点（小样的「● 不达标」）：6px 见方，同判定色。 */
QFrame#VerdictDot {{ border:0; border-radius:3px; }}
QFrame#VerdictDot[level="ok"] {{ background:{ok_band}; }}
QFrame#VerdictDot[level="warn"] {{ background:{warn_band}; }}
QFrame#VerdictDot[level="bad"] {{ background:{bad_band}; }}
/* 比例（1px 细线 + 一个标记）**不走 QSS**：它要在任意比例位置放一个标记，
   而那需要按比例算坐标——QSS 没有这个能力。由 `widgets.RatioBar` 自绘，
   颜色从主题令牌读（不新增色号）。**替代的是 270° 环形仪表**：
   128px 圆内三行字的排版带实测重叠 2.5px，且"占比用转盘"是车载仪表语汇。 */
/* 判定区上的次要文字（文件名 · 编码 · 路径）与正文分开取色：
   淡底不是页面底，`text_faint` 在它上面达不到 4.5。 */
#VerdictBanner QLabel#Caption {{ color:{tint_dim}; }}
#VerdictBanner QLabel#Hero, #VerdictBanner QLabel#HeroUnit {{ color:{text}; }}
/* SectionTitle 用字距而不是字重做层级：本机中文字体只有 400/700 两档，
   原来写 font-weight:600 会被 Qt 吸附到 700（还会打 findfont 告警），
   于是"600"和"700"渲染出来一模一样——那个层级是假的。 */
QLabel#SectionTitle {{ color:{text_dim}; font-size:{caption}px; font-weight:700;
                       letter-spacing:{tr_caps_caption}px; }}
/* 判定区的判词（B9.1）：从"白字压高彩底"改为"**文字色本身就是判定色来源**"
   的深/浅色正文，压在**淡底**上。字号用 display 档（27px）——与小样的
   26px 同量级；再大就与 hero 抢焦点（hero 唯一，见 `Type.hero`）。 */
QLabel#VerdictText {{ font-size:{display}px; font-weight:700; color:{text};
                      letter-spacing:{tr_display}px; }}
/* hero：一屏唯一的核心读数（`Type.hero` = 40px，有破例说明）。
   **它不做入场动画**——跟手路径零动画的既有纪律，读数也不该"飞进来"。 */
QLabel#HeroBig {{ font-size:{hero}px; font-weight:700; color:{text};
                  letter-spacing:{tr_display}px; }}
/* #Hero 的等宽**由代码按值内容指定**（`theme.pick_text_font`），QSS 不设族名：
   值里可能有中文（厂牌、编码器串），而 QSS 的族名是"一律套用"、没有回退链，
   中文一旦落上去就得靠逐字形回退——那是不可控的。 */
QLabel#Hero {{ font-size:{display}px; font-weight:700; color:{text}; letter-spacing:{tr_display}px; }}
QLabel#HeroUnit {{ color:{text_faint}; font-size:{caption}px; letter-spacing:{tr_caption}px; }}
QLabel#Caption {{ color:{text_faint}; font-size:{caption}px; letter-spacing:{tr_caption}px; }}

/* ===== 按钮 ===== */
QPushButton {{ background:{surface_hi}; border:1px solid {border_hi};
               border-radius:{r_lg}px; padding:{sm}px {lg}px; color:{text}; font-size:{body}px; }}
QPushButton:hover {{ background:{hover_hi}; border-color:{border_focus}; }}
QPushButton:pressed {{ background:{surface}; }}
QPushButton:disabled {{ color:{text_disabled}; border-color:{border}; background:{surface}; }}
QPushButton#Primary {{ background:{primary}; border-color:{primary}; color:{on_primary};
                       font-weight:700; }}
QPushButton#Primary:hover {{ background:{primary_hi}; border-color:{primary_hi}; }}
QPushButton#Primary:pressed {{ background:{primary_hi}; }}
QPushButton#Ghost {{ background:transparent; border-color:transparent; color:{text_dim}; }}
QPushButton#Ghost:hover {{ background:{hover}; color:{text}; }}
QPushButton#Ghost:pressed {{ background:{hover_hi}; }}
QToolButton {{ background:transparent; border:1px solid transparent;
               border-radius:{r_lg}px; padding:{xs}px {md}px; color:{text}; font-size:{body}px; }}
QToolButton:hover {{ background:{hover}; border-color:{border_hi}; }}
QToolButton:pressed {{ background:{hover_hi}; }}
QToolButton:disabled {{ color:{text_disabled}; border-color:{border}; }}
QToolButton#Primary {{ background:{primary}; border-color:{primary}; color:{on_primary}; font-weight:700; }}
QToolButton#Primary:pressed {{ background:{primary_hi}; }}

/* ===== 输入 ===== */
QLineEdit, QComboBox, QSpinBox {{ background:{surface_hi}; border:1px solid {border};
    border-radius:{r_lg}px; padding:{xs}px {md}px; color:{text};
    selection-background-color:{primary}; font-size:{body}px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color:{border_focus}; }}
/* 禁用态：**必须比"看起来一样"多做一步**。少了这条，一个不可编辑的输入框
   与可编辑的长得一模一样——用户会去点它，然后以为界面卡了。
   （WCAG 1.4.3 把禁用控件排除在对比度要求外，所以这里只求"看得出不同"。） */
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    background:{surface}; color:{text_disabled}; border-color:{border}; }}
QLineEdit[invalid="true"] {{ border-color:{danger}; }}
QComboBox::drop-down {{ border:0; width:22px; }}
QComboBox QAbstractItemView {{ background:{surface_raised}; border:1px solid {border_hi};
    selection-background-color:{primary}; color:{text}; padding:{xs}px; border-radius:{r_md}px; }}
QCheckBox {{ spacing:{sm}px; color:{text}; font-size:{body}px; }}
QCheckBox::indicator {{ width:16px; height:16px; border-radius:{r_sm}px;
    border:1px solid {border_hi}; background:{surface_hi}; }}
QCheckBox::indicator:checked {{ background:{primary}; border-color:{primary}; }}
QCheckBox::indicator:disabled {{ border-color:{border}; background:{surface}; }}

/* ===== 表格 ===== */
/* 表格整体走等宽：规格/截止/DR/削波全是数字，比例数字会让列宽抖动、
   读数时眼睛没法沿竖线扫。中文列（文件名/判定）仍用比例字体，
   由 ItemDelegate 按列切换。 */
/* 表格**不设整体等宽**：等宽是给数字列的（那几列由委托显式指定字体），
   而这张表里有中文文件名、有整页中文的元数据表——整体等宽等于把中文
   全部推给"逐字形回退"，落到哪个字体不可控，部分机器上直接是方框。 */
QTableWidget {{ background:{surface}; alternate-background-color:{surface_hi};
    gridline-color:transparent; border:1px solid {border}; border-radius:{r_xl}px;
    selection-background-color:{selection}; selection-color:{text};
    font-size:{body_sm}px; }}
QTableWidget::item {{ padding:{xs}px {sm}px; border:0; }}
QTableWidget::item:selected {{ background:{selection}; color:{text}; }}
QHeaderView::section {{ background:{surface_hi}; color:{text_dim}; border:0;
    border-bottom:1px solid {border}; padding:{sm}px {md}px;
    font-weight:700; font-size:{caption}px; letter-spacing:{tr_caps_caption}px; }}
QHeaderView::section:hover {{ color:{text}; }}
QTableCornerButton::section {{ background:{surface_hi}; border:0; }}

/* ===== 标签页（内容区内的次级切换） ===== */
QTabWidget::pane {{ border:1px solid {border}; border-radius:{r_xl}px; background:{surface}; }}
QTabBar::tab {{ background:transparent; color:{text_dim}; padding:{xs}px {lg}px;
    border:0; border-bottom:2px solid transparent; font-size:{body}px; }}
QTabBar::tab:hover {{ color:{text}; }}
QTabBar::tab:selected {{ color:{text}; border-bottom-color:{primary}; font-weight:700; }}
QTabBar::tab:disabled {{ color:{text_disabled}; }}

/* ===== 滚动条（细、圆角、悬停才明显） ===== */
QScrollBar:vertical {{ background:transparent; width:10px; margin:0; }}
QScrollBar::handle:vertical {{ background:{border_hi}; border-radius:5px; min-height:36px;
    margin:2px; }}
QScrollBar::handle:vertical:hover {{ background:{text_faint}; }}
/* 按住滑块拖动时要与"只是悬停"区分开：滚动条是**拖拽类**控件，
   悬停=准备好，按下=正在拖。少了 :pressed，拖动过程中手感和悬停一样。 */
QScrollBar::handle:vertical:pressed {{ background:{text_dim}; }}
QScrollBar:horizontal {{ background:transparent; height:10px; }}
QScrollBar::handle:horizontal {{ background:{border_hi}; border-radius:5px; min-width:36px;
    margin:2px; }}
QScrollBar::handle:horizontal:hover {{ background:{text_faint}; }}
QScrollBar::handle:horizontal:pressed {{ background:{text_dim}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width:0; height:0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background:transparent; }}

/* ===== 状态栏 ===== */
QStatusBar {{ background:{surface}; border-top:1px solid {border};
              color:{text_dim}; font-size:{caption}px; }}
QStatusBar::item {{ border:0; }}
QProgressBar {{ background:{surface_hi}; border:0; border-radius:3px; height:4px;
                color:transparent; }}
/* 进度条**用单色**：原来主色→次级强调色的横向渐变，在本轮"渐变彩条退场"
   （用户判决"合成波陈词滥调"）下撤掉。它看起来仍在动——靠的是 chunk 本身
   在前进，不靠颜色流动。 */
QProgressBar::chunk {{ background:{primary}; border-radius:3px; }}

/* ===== 菜单 / 命令面板 ===== */
QMenu {{ background:{surface_raised}; border:1px solid {border_hi};
         border-radius:{r_lg}px; padding:{xs}px; }}
QMenu::item {{ padding:{xs}px {xl}px {xs}px {md}px; border-radius:{r_md}px; font-size:{body}px; }}
QMenu::item:selected {{ background:{primary}; color:{on_primary}; }}
QMenu::item:disabled {{ color:{text_disabled}; }}
QMenu::separator {{ height:1px; background:{border}; margin:{xs}px {sm}px; }}
#Palette {{ background:{surface_raised}; border:1px solid {border_hi}; border-radius:{r_xl}px; }}
#PaletteList {{ background:transparent; border:0; font-size:{body}px; }}
#PaletteList::item {{ padding:{sm}px {md}px; border-radius:{r_lg}px; }}
#PaletteList::item:selected {{ background:{primary}; color:{on_primary}; }}
#Toast {{ background:{surface_raised}; border:1px solid {border_hi}; border-radius:{r_lg}px; }}
/* 浮层的**顶光**：上缘 1px 内高光。
   它模拟的是"光从上方来，浮层的上边缘被照亮"——人眼靠这个判断
   "这块东西浮在上面"，比加粗描边便宜也比加粗描边准。

   ⚠ **必须写在所有 `border` 简写之后**：QSS 与 CSS 一样，同特异度下后写的
   赢，而 `border` 简写会把 `border-top` 一起重置。第一次写时放在了
   `#Toast {{ border:… }}` 之前，于是那条顶光被静默吃掉了——
   界面看起来"少了点什么"，但没有任何报错。 */
/* **亮边必须配暗线**（B7.4 的「金属四段」）：光从上方来 → 上缘被照亮、
   下缘落进阴影。两件事要成对出现：只有亮边的话，物体像「贴了一片高光」。
   ⚠ Qt 的 QSS **不能在同一个边上叠两条线**（`border-top` 只有一条），
   所以配对实现为**上缘亮线 + 下缘暗线**——同一个物理模型，不是省略。
   这条平台限制登记在此，免得后来者以为漏了「上缘亮线下面那 1px」。 */
QMenu, #Palette, #Toast {{ border-top:1px solid {top_light};
                          border-bottom:1px solid {bg}; }}
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
        top_light=p.top_light,
        # 装饰色阶按序号展开成独立占位符：QSS 模板里写 `{deco[0]}` 会与
        # Python 的格式串解析纠缠（中括号不是合法占位符），显式摊开最直白。
        # QSS 里只剩品牌条的**三个小段**用 deco（B7.1：deco 保留但只做小色块，
        # 不做大面积强调线）。卡片的色条已改中性顶光，另外三档 deco 只在
        # Python 侧用（侧边栏导航图标按顺序取满六档），所以不再进 QSS 占位符。
        deco0=(p.deco[0] if len(p.deco) > 0 else p.accent2),
        deco2=(p.deco[2] if len(p.deco) > 2 else p.accent2),
        deco4=(p.deco[4] if len(p.deco) > 4 else p.accent2),
        danger=p.danger, danger_lo=p.danger_lo, hover=p.hover, selection=p.selection,
        # 判定结论条（判定语义唯一的大面积着色）与它自己的次要文字色
        ok_tint=p.success_tint, warn_tint=p.warning_tint, bad_tint=p.danger_tint,
        ok_band=p.success_band, warn_band=p.warning_band, bad_band=p.danger_band,
        # 结论条描边用的判定色（二次反光），不新增色号
        tint_dim=p.tint_dim,
        # 发货字体在位**且仍是主族** ⇒ QSS 不写 font-family（写单族名会覆盖
        # app 的 `setFamilies([Plex, Noto])` 真回退链，把中文推给逐字形回退）。
        # 一旦有人显式 `set_ui_family(非 bundle 族)` 覆盖，就恢复写它——
        # 覆盖必须**看得见**（否则运行时改族名会静默失效，那又是一次假绿）。
        font_css=("" if (_BUNDLED_ACTIVE and _UI_FAMILY in BUNDLED_FAMILIES)
                  else "font-family:%s;" % t.font),
        body=int(ty.body), body_sm=int(ty.body_sm), body_md=int(ty.body_md),
        caption=int(ty.caption), micro=int(ty.micro), display=int(ty.display),
        hero=int(ty.hero),
        # 字距令牌：QSS 里只许出现这些值，不许手写 px
        # （`tests/test_ui_typography.py` 逐条扫，手写即失败）
        tr_micro=tk.micro, tr_caption=tk.caption, tr_body_sm=tk.body_sm,
        tr_body_md=tk.body_md, tr_display=tk.display,
        # 全大写微标签的字距按 **Butterick 的 8%** 从当前字号算出来，
        # 不是又一个手写常数（原先这里是 1.1px / 1.2px / .6px 三个散点）。
        tr_caps_micro=tk.caps(ty.micro), tr_caps_caption=tk.caps(ty.caption),
        xs=sp.xs, sm=sp.sm, md=sp.md, lg=sp.lg, xl=sp.xl,
        # 圆角走**独立**占位符：`{lg}` 是 Space 的值（16px），
        # 而 Radius.lg 是 8px。早先两者共用一个名字，format() 只喂了 Space，
        # 于是"圆角令牌"从来没被用过——卡片圆角实际是 24px（space.xl），
        # 与卡片强调线按 `radius.xl - 1` 算出来的 11px 对不上。
        r_sm=t.radius.sm, r_md=t.radius.md, r_lg=t.radius.lg, r_xl=t.radius.xl,
        # 卡片圆角减 1（描边宽度）：色条要贴住卡片的圆角内侧
        r=max(0, t.radius.xl - 1),
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


#: **DIN 系**西文字体候选（按优先级）。Bahnschrift 是 Windows 自带的 DIN 1451
#: 数字化版本（本机实测 `bahnschrift.ttf` 在 `C:\Windows\Fonts`，用 .NET 的
#: 已安装字体集合核对过）。**离屏/无 DirectWrite 的场景里 `families()` 列不出
#: 系统字体**（实测 Qt 离屏下连 "Consolas" 都列不出来），所以必须像
#: `_MONO_FILES` 那样显式注册字体文件再查——只查 families() 会得到"没装"的
#: 错误结论。
FIGURE_STACK = ("Bahnschrift", "Bahnschrift SemiCondensed", "Bahnschrift Condensed",
                "DIN Alternate", "DIN 1451", "OSRD")
_FIGURE_FILES = [
    r"C:\Windows\Fonts\bahnschrift.ttf",
    "/System/Library/Fonts/Supplemental/DIN Alternate.ttf",
    "/usr/share/fonts/truetype/din/DINAlternate-Bold.ttf",
]

_FIGURE_CACHE: str | None = None


def figure_family() -> str:
    """返回本机真实可用的 DIN 系字体族名。找不到返回空串（调用方退回默认族）。

    与 `mono_family()` 同一套纪律：显式注册字体文件 → 查 families() → 缓存。
    """
    global _FIGURE_CACHE
    if _FIGURE_CACHE is not None:
        return _FIGURE_CACHE
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QApplication
    if QApplication.instance() is None:
        return ""                       # 不缓存：有了 app 之后还要重新探测
    for path in _FIGURE_FILES:
        if os.path.exists(path):
            idx = QFontDatabase.addApplicationFont(path)
            if idx >= 0:
                QFontDatabase.applicationFontFamilies(idx)
    have = set(QFontDatabase.families())
    for f in FIGURE_STACK:
        if f in have:
            _FIGURE_CACHE = f
            return f
    _FIGURE_CACHE = ""
    return ""


def figure_qfont(size_pt: float, family: str = ""):
    """西文/数字标签用的 DIN 系 QFont。族名缺失时落到 styleHint。

    ⚠ **它不保证数字等宽**——实测 Bahnschrift 的数字是**比例**的
    （24pt 下 `111`=33px 而 `888`=54px）。所以：
      * 要"沿竖线对齐"的列（表格数字列、刻度值）→ 用 `mono_qfont()`；
      * 要"仪器的字气质"的西文标签与单位（LUFS / dBTP / kHz）→ 用本函数。
    这条分流是**量出来的**，不是猜的：排版依据文档里"tabular 等宽、上下对齐
    成列"那句话在本机不成立（Bahnschrift 数字比例宽度），已按实测改正。
    """
    from PySide6.QtGui import QFont

    f = QFont()
    f.setStyleHint(QFont.SansSerif)          # 族名缺失时落到系统无衬线
    f.setFamilies([figure_family()] if figure_family()
                  else list(FIGURE_STACK) + ["Segoe UI", "Microsoft YaHei"])
    f.setPointSizeF(size_pt)
    return f


#: 中西文之间的间隙字符。clreq 的要求是 **≤1/4 汉字宽**。
#:
#: ⚠ **B14 §4-② 按新发货字体重标定**（改的是机制，不是阈值——阈值没动）：
#: 逐字符 × 逐族实测（`out/_b14_spaces.py`，14pt，汉字宽 19px）：
#:
#: | 字符 | IBM Plex Sans | Noto Sans SC |
#: |---|---|---|
#: | U+0020 普通空格 | 0.211 em ✓ | 0.211 em ✓ |
#: | **U+00A0 不断行空格** | **0.211 em ✓** | **0.211 em ✓** |
#: | U+2009 thin space | 0.105 em ✓ | **0.579 em ✗** |
#: | U+3000 全角空格 | 1.000 em ✗ | 1.000 em ✗ |
#:
#: ⇒ **U+2009 在捆绑字体下不可用**：Noto Sans SC 把 thin space 映射到接近汉字的
#: 宽度（0.579 em，超上限 2.3 倍），而混排处的字形正是由 CJK 族（Noto）提供的。
#: B1 选它是因为在 YaHei 下它约 1/5 em——**换字体后机制失效**，这正是"按字体
#: 标定"的意义：**阈值不变，字符换**。
#: ⇒ 改用 **U+00A0**：两个族里都 0.211 em（≤1/4 汉字），而且**不断行**——
#: "44.1 kHz" 不会被折行拆开，比 U+0020 多一条排版收益。
#:
#: 为什么非要一个字符而不是直接打空格：手打的空格在源码里看不见——
#: 普通空格与 U+00A0 长得一模一样，宽度与折行行为却不同。
#: 于是提供 `mix()`，并在测试里扫"手打全角空格 / 连续两个半角空格"。
GAP_SPACE = "\u00a0"
#: 兼容名（旧代码/旧测试可能仍引用 THIN_SPACE）。**指向新机制**，
#: 不留"两个名字两种机制"的岔路——那正是本轮修掉的病。
THIN_SPACE = GAP_SPACE


def mix(*parts: str) -> str:
    """中西文混排连接：相邻的中西文之间插一个 thin space。

    只在**中西文交界**处插（"真峰值" + "ITU-R" → "真峰值\\u2009ITU-R"）；
    两段都是中文、或都是西文时不插——那会用标点或普通空格解决，
    不该在这里偷偷改。

    纯装饰性使用（如 `"a · b"` 的分隔符）不走本函数：那个 `·` 是内容，
    不是混排间隙。
    """
    out: list[str] = []
    for i, s in enumerate(parts):
        if i and out and _need_gap(out[-1], s):
            out.append(GAP_SPACE)
        out.append(s)
    return "".join(out)


def _need_gap(a: str, b: str) -> bool:
    """前一段的末字符与后一段的首字符是否一中一西。"""
    if not a or not b:
        return False
    return _is_cjk(a[-1]) != _is_cjk(b[0])


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (0x4E00 <= o <= 0x9FFF or 0x3400 <= o <= 0x4DBF      # CJK 统一表意
            or 0xF900 <= o <= 0xFAFF or 0x3000 <= o <= 0x303F    # 兼容表意 + 中文标点
            or 0xFF00 <= o <= 0xFF60)                            # 全角形式


def rich_text(text: str, level: str = "body") -> str:
    """把多行文本包成带 `line-height` 的富文本（**QSS 走不到这里**）。

    QSS 的 `line-height` 被 Qt 静默忽略（实测 60px 无效果），而 Qt RichText
    走 QTextDocument，它的 CSS 子集支持 `line-height`（实测 200% 让三行
    文本高 42px）。这是本机唯一能给多行文本设行高的路径。

    只用于**多行**文本；单行标签用了没有意义（自然行距已经是单行高度）。
    """
    lv = _LEADING[level]        # 等级名写错 → KeyError，不许静默用默认值
    return '<div style="line-height:%d%%">%s</div>' % (round(lv * 100), text)


#: `rich_text` 用的等级名 → 行高倍率。单独一张表而不是吃 `Leading` 的字段反射：
#: 等级名写错时**立刻**在这里暴露（KeyError），而不是静默用默认值。
_LEADING = {f.name: getattr(Leading(), f.name) for f in fields(Leading)}


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
