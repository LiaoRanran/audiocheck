#!/usr/bin/env python3
"""audiocheck 图表层：频谱 / 频谱图 / 频段能量 / 多文件对比 / 批次概览 + 表格委托。

约定：每个画布都实现 apply_theme(p) —— 换主题时只需重画，不用重建控件。
"""
import io
import math

import matplotlib
import numpy as np

matplotlib.use("QtAgg")
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from PySide6.QtCore import QRectF, Qt, QTimer  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPixmap  # noqa: E402
from PySide6.QtWidgets import (  # noqa: E402
    QApplication,
    QLabel,
    QStyle,
    QStyledItemDelegate,
    QVBoxLayout,
    QWidget,
)

from ..config import BAND_CN, BANDS  # noqa: E402
from . import palette as P  # noqa: E402
from . import theme as T  # noqa: E402
from . import zoom as Z  # noqa: E402
from .theme import LEVEL_CN, LEVEL_COLOR_KEY  # noqa: E402


def _pick_cjk_font():
    """给 matplotlib 挑一个中文字体，避免程序化调用窗口时满屏方框。"""
    import matplotlib.font_manager as fm
    available = {f.name for f in fm.fontManager.ttflist}
    for want in ("Microsoft YaHei", "Microsoft YaHei UI", "PingFang SC",
                 "Noto Sans CJK SC", "Source Han Sans SC", "SimHei", "Meiryo"):
        if want in available:
            return want
    return None


_CJK = _pick_cjk_font()
if _CJK:
    matplotlib.rcParams["font.sans-serif"] = [_CJK]
matplotlib.rcParams["axes.unicode_minus"] = False

# ============================== 玫瑰钻石色轨（B9.3a）==============================
#
# 用户 B9.2 后的判决："色彩和绘图依旧一股 AI 味道"——三个可测签名就是
# matplotlib 的默认色图（`magma` / `viridis`）与 Tailwind 彩虹 `CYCLE`：
# 它们与项目色相族（冷紫 H286 / 玫瑰 H10）毫无关系，是"教程默认值"的味。
#
# **D 玫瑰钻石**的依据不是审美偏好，是锚点图的**实测色轨**：
#   * 身体（全体像素中位）C ≤0.034 全程低彩，H291–350（冷紫罗兰）
#   * 玫瑰（高彩 C≥0.10）**只出现在 L0.55–0.85**（透光带），H6.6–14.7，C0.11–0.13
#   * 最顶部（L>0.9）C=0.004（几乎无彩的冷白，H340）
# ⇒ "钻石质感"的数学结构 = **色相走 U 形**（冷 → 玫瑰 → 冷）
#                           + **彩度走钟形**（玫瑰在内部发光、冷白从表面出射）。
#
# ⚠ **诚实边界**：钻石的**镜面闪点**是光模拟，频谱图里不能造假（每个像素是
#   一个真实测量值）；D 的顶档那一片近白只暗示"能量很强"，不是编造高频细节。
#   **核心锐利不变**：`interpolation="nearest"` 与 `vmin/vmax` 一个都不动。
#
#: D 的**规范定义**：九点 (t, L, C, H)。唯一真源——**hex 是产物，不许手抄**
#: （判据 14：仓库里出现手抄的九档 hex 即失败）。改任何一个数都要重过三条断言。
ROSE_DIAMOND_LCH = (
    (0.000, 0.12, 0.030, 310.0),
    (0.125, 0.25, 0.035, 320.0),
    (0.250, 0.40, 0.032, 330.0),
    (0.375, 0.55, 0.075, 350.0),
    (0.500, 0.65, 0.115, 10.0),
    (0.625, 0.78, 0.110, 12.0),
    (0.750, 0.90, 0.045, 330.0),
    (0.875, 0.96, 0.015, 340.0),
    (1.000, 0.99, 0.006, 345.0),
)

#: 曲线族的采样位点：暗色取 t **高段**（亮曲线配暗底）、亮色取**低段**
#: （深曲线配亮底）。同一条色轨各取一半 ⇒ 曲线与频谱图**同源**。
CURVE_T = {
    # ⚠ **两组位点都是实测反解的——规范给的两组在两条硬断言下都不合格。**
    # （提示词对这种情况有授权：不足则拉宽 t 间距，实测后记账。）
    #
    # 逐 0.01 扫过 t 0–1（`out/_b93a_t.py`）得到的可行域：
    #   * **对比 ≥3:1**：暗底要求 t ≥0.33（更暗的写不亮）；
    #     亮底要求 t ≤0.53（更亮的压不住白底）——两个主题方向相反；
    #   * **色距 ≥0.05**：暗底要求 t ≤0.92（0.94 起色轨接近白，
    #     相邻点色距只剩 0.028）。
    # ⇒ 两端都是**窄窗**：位点不是"挑一组好看的"，是解出来的。
    #
    # 规范原值的实测结果（这就是它们不合格的证据）：
    #   dark  {0.32, 0.44, 0.56, 0.68, 0.80, 0.92} → 暗底最低对比 2.96（差 0.04）
    #   light {0.06, 0.18, 0.30, 0.42, 0.54, 0.66} → 亮底最低对比 1.85（差 1.15）
    # 下面两组是各自可行域内间距最均匀且余量最大的一组：
    "dark": (0.40, 0.50, 0.60, 0.70, 0.80, 0.92),    # 暗底最低对比 4.20 ｜ 色距 0.051
    "light": (0.04, 0.13, 0.22, 0.31, 0.40, 0.50),   # 亮底最低对比 3.40 ｜ 色距 0.090
}


def _hex_to_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) / 255.0 for i in (0, 2, 4))


def _rd_lch_at(t: float):
    """在规范表上按 t 线性插值出 (L, C, H)——**色相走最短弧**。

    色相不能直接线性插值：从 350° 到 10° 的"直线"会穿过 180°（绿色），
    把 U 形拐点插成彩虹。实测：不转最短弧时 t0.44 的色相落在 **180°**——
    那正是"第三色相族"的来源。
    """
    tab = ROSE_DIAMOND_LCH
    if t <= tab[0][0]:
        return tab[0][1:]
    if t >= tab[-1][0]:
        return tab[-1][1:]
    for i in range(len(tab) - 1):
        t0, L0, c0, h0 = tab[i]
        t1, L1, c1, h1 = tab[i + 1]
        if t0 <= t <= t1:
            k = (t - t0) / (t1 - t0)
            dh = (h1 - h0 + 180.0) % 360.0 - 180.0      # 最短弧
            return L0 + (L1 - L0) * k, c0 + (c1 - c0) * k, (h0 + dh * k) % 360.0
    raise AssertionError("t=%r 落在表外：取值表必须覆盖 [0,1]" % t)


def rose_diamond_cmap():
    """D 色图：由 `ROSE_DIAMOND_LCH` 经**项目的** `oklch_to_srgb` 生成。"""
    from matplotlib.colors import LinearSegmentedColormap

    cols = [P.oklch_to_srgb((L, c, h)) for _t, L, c, h in ROSE_DIAMOND_LCH]
    cm = LinearSegmentedColormap.from_list("rose_diamond", cols, N=256)
    cm.audiocheck_source = ROSE_DIAMOND_LCH         # 供判据追溯来源
    return cm


_RD_CACHE = None


def rd_cmap():
    """缓存过的 D 色图（`imshow` 每次重建会白付解析代价）。"""
    global _RD_CACHE
    if _RD_CACHE is None:
        _RD_CACHE = rose_diamond_cmap()
    return _RD_CACHE


_SPECTRO_CACHE = None


def spectro_cmap():
    """**频谱图**色图（B14：专业可视化色图，替掉粉白 D）。

    `inferno` 是密度 / 时频图的**科学标准**色图：感知均匀、明度单调，
    峰值（亮黄白）与噪底（暗紫黑）一眼分得开——这正是频谱图要的"强"。
    **曲线仍用 D 玫瑰钻石**（品牌色，曲线要认得出是"我们的"图）；
    两者分工：**数据点/线 = 品牌色，密度场 = 科学色图**。
    """
    global _SPECTRO_CACHE
    if _SPECTRO_CACHE is None:
        import matplotlib
        _SPECTRO_CACHE = matplotlib.colormaps["inferno"]
    return _SPECTRO_CACHE


def rd_color(t: float) -> str:
    """色轨上 t 处的颜色（给曲线与标记用）——与色图**同源**，不是两套配色。"""
    L, c, h = _rd_lch_at(t)
    return P.oklch_to_srgb((L, c, h))


def curve_colors(dark: bool = True) -> list:
    """对比视图的曲线族：从 D 色轨按主题分段采样。"""
    return [rd_color(t) for t in CURVE_T["dark" if dark else "light"]]


def cycle_for(pal: dict) -> tuple:
    """按**底色明度**判断主题，返回该主题的曲线族。

    判断放在这里而不是要求调用方多传一个 `dark` 参数：少一个能传错的地方
    （`pal` 本来就在手边）。
    """
    dark = P.srgb_to_oklch(pal.get("bg", "#111111"))[0] < 0.5
    return tuple(curve_colors(dark))



#: 特效参数（B9.3b）。**常量而不是内联字面量**：判据要能直接引用它们，
#: 而不是去源码里正则匹配某个数字（那样改一个字面量就能骗过判据）。
#:
#: 截止淡染的透明度。上限 0.10 是写进判据的预算：它是一条**提示**，
#: 不是一块色域——超过这个值就开始与数据墨抢注意力（六条"不廉价"判据里的
#: "光有落点、不到处发光"）。取 0.08 留一点余量。
CUTOFF_FADE_ALPHA = 0.08
#: 绘图面板上缘亮边的高度（px）。1px = "Rim light"的最小可见量；
#: 2px 会读成"边框"，那又是廉价感的来源。与浮层"亮边配暗线"同一物理模型。
PANEL_RIM_H = 1


class FullscreenView(QWidget):
    """B14 E.2-6：**无损全屏查看**。

    关键在"无损"：这里放的不是把画布 `grab()` 出来的位图放大（那会糊），
    而是按**屏幕像素所需的 DPI 重新渲染**的位图（`Figure.savefig(dpi=...)`），
    所以放大到全屏时是**重算**出来的矢量光栅，不是插值。Esc / 双击 / 点击退出。
    """

    def __init__(self, pixmap: QPixmap):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("全屏查看 · Esc 退出")
        self.setWindowState(Qt.WindowState.WindowFullScreen)
        self.setStyleSheet("background:#000000;")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        lab = QLabel(self)
        lab.setPixmap(pixmap)
        lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(lab)

    def keyPressEvent(self, ev):
        if ev.key() in (Qt.Key.Key_Escape, Qt.Key.Key_Q, Qt.Key.Key_F):
            self.close()
            return
        super().keyPressEvent(ev)

    def mouseDoubleClickEvent(self, ev):
        self.close()

    def mousePressEvent(self, ev):
        self.close()


class BaseCanvas(FigureCanvasQTAgg):
    def paintEvent(self, ev):
        """在 Qt 层补一条上缘亮边（特效第 2 招：Rim light）。

        **为什么画在 `paintEvent` 而不是 matplotlib 里**：数据墨（曲线、
        色图、网格）由 matplotlib 画，而这条边是**帧层**的东西——它不属于
        任何一组数据，也不该随坐标轴缩放而移动。分层是判据"特效只碰帧层"
        的实现方式：帧层改错了，数据墨一点不受影响。
        """
        super().paintEvent(ev)
        p = getattr(self, "p", None)
        if not p:
            return
        col = p.get("top_light") or p.get("border_hi")
        if not col:
            return
        pr = QPainter(self)
        pr.setPen(QPen(QColor(col), PANEL_RIM_H))
        # y=0 那条线：Qt 的 1px 线在 y=0 会有一半落在控件外，但截图里
        # 第 0 行确实被点亮（判据量的是 grab() 的第 0 行），所以照画。
        pr.drawLine(0, 0, self.width() - 1, 0)
        pr.end()

    # 内容已变但当前不可见 → 标脏，等 showEvent 再画。
    # 放在类属性上：WaterfallCanvas 直接调 FigureCanvasQTAgg.__init__、不走
    # super().__init__，类属性是它唯一能稳定拿到该字段的途径。
    _stale = False

    def __init__(self, w=6.0, h=3.4, dpi=100):
        self.fig = Figure(figsize=(w, h), dpi=dpi, layout="constrained")
        super().__init__(self.fig)
        self.setStyleSheet("background:transparent;")
        # B14 E.2-6 宽敞：constrained layout 的默认留白偏紧（轴标题贴边）。
        # w_pad/h_pad 是**字体高度的倍数**（不是像素）；0.6/0.5 让轴标题与画布边
        # 之间有呼吸，同时不压缩数据墨占比。
        try:
            self.fig.set_constrained_layout_pads(w_pad=0.6, h_pad=0.5,
                                                 wspace=0.04, hspace=0.04)
        except (AttributeError, ValueError):
            pass
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)   # 收 F 键（全屏）
        self._fs = None
        self.p = None
        CURSOR.subscribe(self)      # B14 补强⑤：默认不画（子类按需覆写）

    # ---- B14 补强⑤：跨画布频率游标（默认空操作）----
    def set_crosshair(self, hz: float) -> None:
        """别处的画布指到了某个频率。默认忽略——只有有频率轴的图才覆写。"""

    def clear_crosshair(self) -> None:
        """别处的鼠标离开了。"""

    # ---- B14 E.2-6 全屏无损放大 ----
    def fullscreen_pixmap(self, dpi: int) -> QPixmap:
        """按目标 DPI **重新渲染**（`Figure.savefig`），不是放大位图 ⇒ 无损。"""
        buf = io.BytesIO()
        self.fig.savefig(buf, format="png", dpi=dpi,
                         facecolor=self.fig.get_facecolor())
        buf.seek(0)
        pm = QPixmap()
        pm.loadFromData(buf.read(), "PNG")
        return pm

    def open_fullscreen(self):
        """铺满屏幕显示当前图。DPI 取"铺满屏幕所需的下界"⇒ 渲染像素 ≥ 屏幕像素。"""
        app = QApplication.instance()
        scr = app.primaryScreen() if app else None
        if scr is None or not self._has_data():
            return
        geo = scr.geometry()
        fw, fh = self.fig.get_size_inches()
        dpi = max(100, int(min(geo.width() / max(fw, 1e-6),
                               geo.height() / max(fh, 1e-6))))
        self._fs = FullscreenView(self.fullscreen_pixmap(dpi))
        self._fs.show()
        self._fs.raise_()
        self._fs.activateWindow()

    def keyPressEvent(self, ev):
        if ev.key() == Qt.Key.Key_F:
            self.open_fullscreen()
            return
        super().keyPressEvent(ev)

    def _has_data(self) -> bool:
        """当前画布是否已有可画的内容。子类按自己的数据字段覆写。

        构造期 MainWindow 会对 6 个画布各调一次 apply_theme，若每次都全量重建
        matplotlib 图元，实测白付约 470ms，而且画出来的是随即被丢弃的空图
        （数据要等用户选文件才有）。没有数据时换肤只存色板即可。
        """
        return False

    def redraw_if_visible(self):
        """看得见就立刻画，看不见只标脏。

        画布在 QStackedWidget 里，同一时刻**只有一页可见**（实测
        `isVisible()`：切换前 6 张全 False，切到频谱图后才 1 张 True）。
        而一次重绘在本机要 90–370 ms（频谱图 90、瀑布图 370，见
        packaging/perf_probe.py），所以给看不见的画布喂数据/换肤纯属白付：
        换肤 1.6 s 里绝大部分花在 5 张看不见的画布上。
        """
        if self.isVisible():
            self.redraw()
        else:
            self._stale = True

    def apply_theme(self, p):
        self.p = p
        self.fig.set_facecolor(p["surface"])
        if not self._has_data():
            # 没有数据也要让背景色立刻跟上，否则深/浅主题切换时会闪一下旧底色
            self.draw_idle()
        else:
            self.redraw_if_visible()

    def showEvent(self, e):
        super().showEvent(e)
        if self._stale:
            self._stale = False
            self.redraw()

    def redraw(self):
        pass

    def _plot_is_dark(self) -> bool:
        """绘图区底色是深还是浅——决定配色往哪个方向走。

        为什么算而不是写死：主题现在是**生成**出来的（`ui/palette.py`），
        同一个代码路径要能同时服务深色与浅色，硬编码"深色"会重演
        "写死 #1c212b"那类 bug。
        """
        from .palette import relative_luminance

        return relative_luminance(self.p.get("plot_bg", "#000000")) < 0.18

    # ---- 公共样式 ----
    def _ax_style(self, ax, xlabel=None, ylabel=None):
        p = self.p
        ax.set_facecolor(p["plot_bg"])
        ax.minorticks_on()      # B14 E.2-2：开次刻度——否则下面那条 minor 网格是空的（"看着没网格"的真因）
        ax.grid(True, which="major", color=p["grid"], lw=.7, zorder=0)
        ax.grid(True, which="minor", color=p["grid"], lw=.35, alpha=.45, zorder=0)
        ax.tick_params(colors=p["text_faint"], labelsize=p['fs_tick'], length=3)
        for s in ax.spines.values():
            s.set_color(p["border"])
            s.set_linewidth(.9)
        if xlabel:
            ax.set_xlabel(xlabel, color=p["text_faint"], fontsize=p['fs_tick'], labelpad=4)
        if ylabel:
            ax.set_ylabel(ylabel, color=p["text_faint"], fontsize=p['fs_tick'], labelpad=4)


# --------------------------------------------------------------------- 时频重采样
#: 频谱图重采样后的**行数**（对数频域等距）。
#: 360 行 ≈ 每 1/24 倍频程一行——比人耳的临界频带（Bark）还密，够读；
#: 但比原始的 2049 行少 5.7 倍，这正是 imshow 比 pcolormesh 快的主要来源。
RESAMPLE_ROWS = 360

#: 频谱图 / 瀑布图的 dB 显示窗（相对峰值）——**默认值**。
#:
#: **不要做成"每文件自适应"**——那是看起来聪明的错：自适应窗会让"两张看着差不多亮"
#: 的图其实量纲不同（一张 40dB 窗、一张 90dB 窗），肉眼分不出、结论就错。
#: 数据在画之前做了 `S - nanmax(S)`（峰值归一到 0 dB），所以窗就是"**峰值下 N dB**"。
#: 90 dB 覆盖 CD 级动态范围（16bit 理论 96 dB）的绝大部分。
#:
#: B14：窗**可由用户切换**（60/90/120），因为显示窗不参与判定——判定读的是
#: `context.spectrum()`，与这里的窗无关。**但默认必须是 90**，且切换要留下痕迹
#: （见 `SpectrogramCanvas.set_db_range`：把当前窗画在色条标签上）。
SPECTRO_DB_RANGE = 90.0

#: 可选的 dB 显示窗（dB）。只有这几个——不是一个滑条：离散档让"跨文件可比"
#: 仍然是可说的（大家默认都在 90，要偏离是有意识的、看得见的）。
DB_RANGE_CHOICES = (60.0, 90.0, 120.0)

#: 纵轴刻度（Hz）
TICKS_HZ = (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)

#: **相位场**的发散色图九点 LCH 表（ρ 从 −1 到 +1）。
#:
#: 发散色图（diverging）**不该按顺序色图的判据测**——它的中点是"零点"，
#: 明度天然非单调（两端深、中间浅）。它的判据是另外两条：
#:   ① **两臂各自单调**：|ρ| 增大必须单调变深（否则"有多反常"读不出来）；
#:   ② **中点可辨**：ρ≈0 必须是一眼认出的近白中性（不是某一臂的浅色）。
#:
#: ## 为什么两臂**彩度不对称**（这是设计，不是偷懒）
#:
#: 负臂（反相 = **要查的缺陷**）走高彩玫瑰（= accent 族，本项目里"要注意"的颜色）；
#: 正臂（同相 = 正常）走**极低彩的冷灰蓝**（C ≤ 0.045，全在高彩门限之下），
#: 只靠**明度**从浅到深表达"同相程度"。
#:
#: 两条理由：
#:   * **读图**：一屏里最该被看见的是反相区。正臂也高彩的话，两个色块在抢注意力，
#:     "哪里反相"反而要一个个比。让正常内容退成灰蓝，反相区自己跳出来。
#:   * **预算**：本项目的"高彩面积"是有配额的（见 `packaging/hue_family_report.py`）。
#:     两臂都高彩会把整屏的高彩面积推到 45%（实测），且引入**第二色相族**——
#:     门禁当场抓红。一臂高彩既守住配额，又是更好的读图设计。
PHASE_LCH = (
    (0.00, 0.32, 0.150, 12.0),      # ρ=-1 完全反相：深玫瑰（最需要被看见的）
    (0.14, 0.42, 0.120, 10.0),
    (0.28, 0.58, 0.085, 8.0),
    (0.42, 0.78, 0.040, 350.0),
    (0.50, 0.90, 0.006, 330.0),     # ρ=0 不相关：近白中性（零点）
    (0.58, 0.80, 0.020, 240.0),     # 以下正臂：冷灰蓝，靠明度不靠彩度
    (0.72, 0.66, 0.032, 235.0),
    (0.86, 0.50, 0.040, 232.0),
    (1.00, 0.38, 0.045, 230.0),     # ρ=+1 完全同相：深冷灰蓝（安静）
)

_PHASE_CACHE = None


def phase_cmap():
    """相位场色图：由 `PHASE_LCH` 经**项目的** `oklch_to_srgb` 生成（不手抄 hex）。"""
    global _PHASE_CACHE
    if _PHASE_CACHE is None:
        from matplotlib.colors import LinearSegmentedColormap
        cols = [P.oklch_to_srgb((L, c, h)) for _t, L, c, h in PHASE_LCH]
        _PHASE_CACHE = LinearSegmentedColormap.from_list("phase", cols, N=256)
        _PHASE_CACHE.audiocheck_source = PHASE_LCH
    return _PHASE_CACHE

#: 3D 瀑布图**每条谱线的频点数**（对数等距）。300 ≈ 每 1/12 倍频程一点。
WATERFALL_POINTS = 300

#: 3D 瀑布图的**时间切片数**（画多少片）。44 片在 1440 宽窗上片间距 <1px 的
#: 视觉融合临界；再多只是白增多边形（实测每片 300 点，44 片 draw 仍 < 拖拽节流窗）。
WATERFALL_SLICES = 44

#: **参考线**线宽。比数据曲线（1.5–2px）细——Tufte 的规矩是数据墨必须
#: 比参考墨重；把参考线加粗到 1.5 会反过来压住数据。
#: ⚠ 特意做成**常量**而不是字面量：判据 `test_data_curves_are_thick_enough`
#: 扫 `lw=<数字>` 的调用点，常量不会误触（注释会被扫描器去掉——那个坑本项目
#: 踩过三次，所以"声明自己的类别"必须写成代码，不能写成注释）。
REF_LINE_W = 1.0


# --------------------------------------------------------------------- 跨画布游标
class CursorBus:
    """B14 补强⑤：**跨画布频率游标**（竞品的多窗联动）。

    在一张图上指一个频率，另外几张同步画出参考线——看"这条 8 kHz 的峰
    在瀑布里从什么时候开始冒"必须来回扫三张图，这道手续本该由机器做。

    ## 为什么是"总线 + 各自实现"，不是"共享一条 Line2D"

    三张图的频率轴**互不相同**（曲线是 x 对数、频谱图是 y 对数、瀑布是 3D x），
    画一条线的语法都不一样；而且各自的刷新路径也不一样（曲线走 blit 快路、
    频谱图走 draw_idle、瀑布要节流整帧重画）。所以总线只负责**传播频率**，
    怎么画由每张图自己决定——`set_crosshair(hz)` 是画布的接口，默认空操作。

    ## 性能边界（诚实记账）

    瀑布图是 3D：一条线也要整帧重画（本机 257 ms/帧），所以它**节流到 40 ms**
    才刷一次，跟拖拽同一个量级。2D 两张不节流（blit ~1 ms / draw_idle）。
    没有"既全图联动又零成本"的方案——3D 联动就是贵，节流是唯一诚实解。

    ## 订阅者的生命周期（**这是一条真 bug 的修复**）

    第一版只往列表里 `append`，从不退订。后果：画布被 `deleteLater()` 之后
    仍然挂在总线上，下一次 `emit` 去调它的方法就撞
    `RuntimeError: Internal C++ object already deleted`——**而且只在"先建过、
    又关掉过一批画布"之后才复现**（单跑新测试看不出来，跑全量才炸）。
    生产里对应的场景是：关一个文件窗口 → 再开一个 → 一悬停就崩。

    两道防线，缺一不可：
      ① **自动退订**：订阅时挂 `destroyed` 信号，C++ 对象一销毁就把自己摘掉；
      ② **有效性过滤**：`emit` 时跳过 `shiboken6.isValid` 为假的对象。
         只做 ① 不够——`destroyed` 是队列投递，信号发出与处理之间仍有窗口期。
    """

    def __init__(self):
        self._subs: list = []

    def subscribe(self, canvas) -> None:
        self._subs.append(canvas)
        try:
            # ① 自动退订。用 weakref 比较身份，避免闭包**强引用**住画布
            #    （那会让 `destroyed` 永远不触发——把自己要监听的对象救活了）。
            import weakref
            ref = weakref.ref(canvas)

            def _drop(*_a, _r=ref, _self=self):
                dead = _r()
                if dead is not None:
                    _self._subs = [c for c in _self._subs if c is not dead]

            canvas.destroyed.connect(_drop)
        except (AttributeError, TypeError):
            pass        # 非 QObject（单元测试里的假画布）：靠 ② 兜底

    def _alive(self) -> list:
        """只返回 C++ 对象仍然有效的订阅者，顺手清掉死引用。"""
        try:
            import shiboken6
        except ImportError:                 # 理论上不会发生（PySide6 自带）
            return list(self._subs)
        out = [cv for cv in self._subs if shiboken6.isValid(cv)]
        if len(out) != len(self._subs):
            self._subs = out
        return out

    def emit(self, hz: float, source) -> None:
        """把频率广播给**除来源以外**的画布。"""
        for cv in self._alive():
            if cv is not source:
                cv.set_crosshair(hz)

    def clear(self, source) -> None:
        for cv in self._alive():
            if cv is not source:
                cv.clear_crosshair()


#: 进程级唯一总线。**故意不用 Qt 信号**：画布之间没有父子关系，信号要
#: 先找个共同 QObject 当中介，而那正是"为了机制而加一层"的例子。
CURSOR = CursorBus()


def _hz_label(v: float) -> str:
    return ("%gk" % (v / 1000)) if v >= 1000 else "%g" % v


def _resample_rows(db, f, fl, signed: bool = False):
    """把 (n_f, n_t) 的矩阵按**对数频轴**重采样成 (len(fl), n_t)。

    **峰值保持，不做线性插值**（B14 精度）：线性插值会把**窄峰/深谷**平均掉，
    而砖墙截止、陷波这类"一条细线"正是本工具要看的东西（插值后它变淡、甚至消失）。
    改成每行取带内 bin 的**保持量**：峰不丢；上采样时退化为最近邻，不制造假分辨率。

    `signed=True`：矩阵是**有符号**的（如相位场的 ρ ∈ [−1,1]），保持量改为
    **|值| 最大者**。若仍取 `max`，整张图会偏向 +1（"看着都是同相"），
    把要查的反相区抹平——这正是 `_peak_hold_abs` 存在的理由，两边口径必须一致。
    """
    f = np.asarray(f, dtype=float)
    n = len(fl)
    edges = np.clip(np.searchsorted(f, fl), 1, len(f))
    out = np.empty((n, db.shape[1]), dtype=db.dtype)
    lo = 0
    for i in range(n):
        hi = max(lo + 1, int(edges[i]))
        blk = db[lo:hi]
        if signed:
            out[i] = blk[np.argmax(np.abs(blk), axis=0), np.arange(blk.shape[1])]
        else:
            out[i] = blk.max(axis=0)          # 带内峰值保持
        lo = hi
    return out


# --------------------------------------------------------------------- Bark 标度
# Traunmüller (1990) 的解析式：z = 26.81·f/(1960+f) − 0.53。
# 与 headphone-lab 的 `perceptual.py` 同一套（两个项目各自独立实现，
# 因为它们是分开的仓库，但口径必须一致，否则跨项目的数字没法对）。
def bark(f: float) -> float:
    return 26.81 * f / (1960.0 + f) - 0.53


def bark_to_hz(z: float) -> float:
    return 1960.0 * (z + 0.53) / (26.28 - z)


def bark_edges(lo: float = 20.0, hi: float = 20000.0) -> list[float]:
    """把 [lo, hi] 按 Bark 等宽切分，返回边界频率（首尾即 lo / hi）。"""
    z0, z1 = bark(lo), bark(hi)
    n = max(1, int(round(z1 - z0)))
    edges = [bark_to_hz(z0 + (z1 - z0) * i / n) for i in range(n + 1)]
    return [lo] + [e for e in edges if lo < e < hi] + [hi]


def third_octave_smooth(f, db):
    """1/3 倍频程滑动平滑：把曲线里的毛刺压掉，露出宏观走向。

    为什么要单独画一条：**原始曲线的毛刺是内容**（共振、噪声），
    平滑线看的是"整体倾斜"。两张信息叠在一张图上，谁也不替谁。
    """
    import numpy as np
    f = np.asarray(f, dtype=float)
    db = np.asarray(db, dtype=float)
    out = np.empty_like(db)
    for i in range(len(f)):
        lo, hi = f[i] / (2 ** (1 / 6)), f[i] * (2 ** (1 / 6))
        m = (f >= lo) & (f <= hi)
        out[i] = db[m].mean() if m.any() else db[i]
    return out


def semantic_ramp(n: int, seed_hex: str, *, dark: bool) -> list[str]:
    """从主色派生的**顺序渐变**（低频→高频）。

    为什么不用 viridis：柱状图的类别是**有序**的（频段从低到高），
    顺序型数据配顺序型色阶才对——彩虹色会暗示"这几段是并列类别"，那是错的。
    这里用 OKLCH 做等 L 步长，因此每一档的视觉差是均匀的（HSL 做不到）。
    """
    from .palette import oklch_to_srgb, srgb_to_oklch

    _, c, h = srgb_to_oklch(seed_hex)
    c = max(0.09, min(0.16, c))
    l0, l1 = (0.82, 0.46) if dark else (0.62, 0.34)
    return [oklch_to_srgb((l0 + (l1 - l0) * (i / max(1, n - 1)), c, h))
            for i in range(n)]


# --------------------------------------------------------------------- 频谱曲线
class SpectrumCanvas(BaseCanvas):
    """真实截止点 + 频段刻度 + 悬停读数（本工具的核心证据图）。"""

    def __init__(self):
        # 这张图是全工具的**主证据**，给它更大的画幅（用户明确要求"大一点没问题"）。
        # 代价是 blit 每次要多拷一些像素——实测仍在帧预算内（`perf_probe.py` P1）。
        super().__init__(7.0, 4.4)
        self.ax = self.fig.add_subplot(111)
        self.r = None
        self._hover = None
        self._static_ok = False        # 静态内容是否已按当前数据画好
        self._bg = None                # 静态层的栅格快照，悬停 blit 用
        self._hline = self._hdot = self._hann = None   # 常驻悬停图元
        # ---- 缩放 / 平移状态 ----
        # 数据全距（进栈时由 _static_layer 填），视野初始 = 全距
        self._full_x = (20.0, 20000.0)
        # None = 用户还没缩放过（或数据刚换过）→ 下一次重画时回全距。
        # 不用"跨度≈全距"来判断：20000 与 22050 只差 1.4%，
        # 任何固定阈值都会把这种占位值误判成缩放态。
        self._view_x = None
        self._view_dirty = False
        self._pan_from = None          # 拖拽起点 (xdata, ydata)
        self._view_timer = None        # 重画合并计时器
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.mpl_connect("motion_notify_event", self._on_move)
        self.mpl_connect("axes_leave_event", lambda e: self._set_hover(None))
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("key_press_event", self._on_key)

    # ---- 缩放 / 平移 -------------------------------------------------------
    # 为什么重画要走合并计时器：滚轮是连续事件，一路滚下去会触发十几次重画，
    # 而每次重画是整图（本图实测 ~90 ms）。合成一帧再画，与瀑布图拖拽限流
    # 是同一个思路——不是"少画"，是"别把同一帧画两遍"。
    VIEW_DEBOUNCE_MS = 28

    def _schedule_view(self):
        if self._view_timer is None:
            self._view_timer = QTimer(self)
            self._view_timer.setSingleShot(True)
            self._view_timer.setInterval(self.VIEW_DEBOUNCE_MS)
            self._view_timer.timeout.connect(self._apply_view)
        self._view_timer.start()

    def _apply_view(self):
        if not self._has_data():
            return
        lo, hi = self._view_x or self._full_x
        self.ax.set_xlim(lo, hi)
        # 视野变了，刻度与 Bark 底纹都要按新区间重挑（见 zoom.pick_ticks）
        self._static_ok = False
        self._bg = None
        self.redraw_if_visible()

    def _zoom(self, step: int, anchor=None):
        if not self._has_data():
            return
        lo, hi = self._view_x or self._full_x
        mid = (lo + hi) / 2 if anchor is None else anchor
        self._view_x = Z.zoom_step(lo, hi, self._full_x[0], self._full_x[1], step, mid,
                                   log=True)
        self._schedule_view()

    def _pan_by(self, frac: float):
        if not self._has_data():
            return
        lo, hi = self._view_x or self._full_x
        a, b = Z.pan_span(lo, hi, frac, log=True)
        self._view_x = Z.clamp_span(a, b, self._full_x[0], self._full_x[1], log=True)
        self._schedule_view()

    def reset_view(self):
        self._view_x = self._full_x
        self._view_dirty = False
        self._schedule_view()

    def _on_scroll(self, ev):
        if ev.inaxes is not self.ax or not self._has_data():
            return
        step = getattr(ev, "step", 0) or (1 if getattr(ev, "button", "") == "up" else -1)
        self._zoom(step, ev.xdata)
        self.setFocus()

    def _on_press(self, ev):
        if ev.inaxes is not self.ax or not self._has_data():
            return
        if ev.button == 1:
            self._pan_from = (ev.xdata, ev.ydata)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif ev.button == 3:                     # 右键 = 放大一级（触板/无滚轮环境）
            self._zoom(1, ev.xdata)

    def _on_release(self, ev):
        if self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()

    def _on_key(self, ev):
        if not self._has_data():
            return
        k = (ev.key or "").lower()
        if k in ("+", "="):
            self._zoom(1)
        elif k in ("-", "_"):
            self._zoom(-1)
        elif k in ("0", "r"):
            self.reset_view()
        elif k in ("left", "right"):
            self._pan_by(0.25 if k == "right" else -0.25)

    def _on_move(self, ev):
        if self._pan_from is not None:
            if ev.inaxes is not self.ax or ev.xdata is None or ev.xdata <= 0:
                return
            lo, hi = self._view_x
            # 位移要按**对数空间**算比例：横轴是对数的，
            # 直接拿数据差除以跨度会让"拖过同样的像素"在高低频表现不一致。
            span_l = math.log10(hi) - math.log10(lo)
            if span_l <= 0:
                return
            frac = -(math.log10(ev.xdata) - math.log10(self._pan_from[0])) / span_l
            self._pan_from = (ev.xdata, self._pan_from[1])
            self._pan_by(frac)
            return
        if ev.inaxes is not self.ax or not self._has_data():
            CURSOR.clear(self)          # B14 补强⑤：离开了 ⇒ 别处参考线收起来
            return
        self._set_hover(ev.xdata)
        CURSOR.emit(float(ev.xdata), self)     # B14 补强⑤：广播给频谱图/瀑布

    def set_data(self, r):
        if r is not self.r:
            # 数据换了，静态内容（曲线/频段线/截止标注）必须重画
            self._static_ok = False
            self._bg = None
            # 换文件 = 换全距（奈奎斯特随采样率变），视野回全距。
            # 留着上一个文件的缩放区会把新文件显示成"空白的低频段"。
            self._view_dirty = True
        self.r = r
        self.redraw_if_visible()

    def resizeEvent(self, e):
        self._bg = None                # 尺寸变了，快照会错位
        super().resizeEvent(e)

    def _has_data(self):
        return bool(self.r and self.r.get("curve"))

    def _set_hover(self, f):
        if f == self._hover:
            return
        self._hover = f
        if self._has_data():
            # 只重建那 3 个悬停图元，再把它们**补画到静态快照上**。
            self._hover_layer()
            self._blit()

    # ---- B14 补强⑤：被别的画布驱动（复用同一条 blit 快路）----
    def set_crosshair(self, hz: float) -> None:
        """**别处**指到了某个频率 ⇒ 把自己的悬停线移到那儿。

        直接复用 `_set_hover`：那条线本来就在 blit 快路上（~1 ms），
        联动因此不引入新的成本。没有数据 / 频率非法 ⇒ 不收（不许画错）。
        """
        if not self._has_data() or hz is None or hz <= 0:
            return
        self._set_hover(float(hz))

    def clear_crosshair(self) -> None:
        self._set_hover(None)

    def _blit(self):
        """把悬停层补画到静态快照上，而不是重画整张图。

        测量依据（packaging/perf_probe.py，本机 = 自由线程 3.14、单核约为常规桌面 1/3）：
            整图 `draw()`        90 ms（其中 33 个刻度文本占一半）
            `_hover_layer()`    0.3 ms（建图元）
            blit 本路径          ~1 ms
        差两个数量级。上一轮"静态层 / 悬停层分离"只省下建图元那部分（90 → 60 ms 量级），
        大头始终是整图重画——所以这里必须走 blit。
        QtAgg 的 `paintEvent` 只从 Agg 缓冲拷像素、不重渲染，`blit()` 因此是纯像素搬运。
        快照失效时**当场补一张再走快路径**，而不是退回 `draw_idle()`：
        窗口一缩放就会作废快照（`resizeEvent`），若只退回异步重画，
        那之后每一次悬停都会是整图重画——等于这个优化白做。
        """
        if self._bg is None:
            self._drop_hover()
            self.draw()
            self._snapshot()
            if self._bg is None:               # 拿不到缓冲（无 renderer）：只能走慢路
                self._hover_layer()
                self.draw_idle()
                return
            self._hover_layer()                # 刚为拍照把它摘了，补回来
        self.restore_region(self._bg)
        for art in (self._hline, self._hdot, self._hann):
            if art is not None:
                self.ax.draw_artist(art)
        self.blit(self.fig.bbox)

    def _snapshot(self):
        """把当前画面（此刻没有悬停图元）存成栅格快照。

        取整个 figure 而不是 `ax.bbox`：悬停气泡贴着左右边缘时会探出坐标区，
        只 blit 坐标区会把它裁掉。
        """
        try:
            self._bg = self.copy_from_bbox(self.fig.bbox)
        except Exception:                                  # noqa: BLE001
            self._bg = None

    def redraw(self):
        self._stale = False
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        if not self._has_data():
            self._static_ok = False
            self._bg = None
            self._drop_hover()
            ax.clear()
            self._ax_style(ax, "频率 Hz", "相对电平 dB")
            ax.text(.5, .5, "选择左侧条目以查看频谱", ha="center", va="center",
                    color=p["text_faint"], transform=ax.transAxes, fontsize=p['fs_caption'])
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        if not self._static_ok:
            self._static_layer()
            self._static_ok = True
        # 顺序要紧：先摘掉悬停图元再拍快照，否则快照里会烙进上一帧的悬停层
        self._drop_hover()
        self.draw()
        self._snapshot()
        if self._hover is not None:
            self._hover_layer()
            self._blit()

    def _static_layer(self):
        """曲线 + 频段分界 + 截止标注。**只有数据变化才重画。**"""
        p, ax = self.p, self.ax
        arr = np.asarray(self.r["curve"], dtype=float)
        f, db = arr[:, 0], arr[:, 1]
        nyq = (self.r.get("sr") or 48000) / 2
        top = float(np.percentile(db, 99.8))
        lo = max(top - 95, float(db.min()) - 3)
        ax.clear()
        self._ax_style(ax, "频率 Hz", "相对电平 dB")

        # ---- Bark 临界频带底纹 ----
        # 人耳的频率分辨率是按临界频带走的下（<500Hz 约 100Hz 恒定，>500Hz 约 0.2·f），
        # 而这张图的横轴是对数——**对数轴的等距在感知上并不等距**。铺一层 Bark 带，
        # 读者才看得出"这一段在耳朵里算多宽一处"。交替深浅只作底纹，不抢曲线。
        edges = bark_edges(20.0, nyq)
        for i in range(len(edges) - 1):
            if i % 2:
                ax.axvspan(edges[i], edges[i + 1], color=p["surface2"],
                           alpha=.55, lw=0, zorder=0)

        # ---- 频段分界（与推荐引擎 band_def 一致）----
        # 原来用 grid 色画，与背景几乎同色 —— 宣称的功能实际看不见。
        # 改用 border_hi 并在顶部标出边界值，让"分了几段、界在哪"可直接读。
        for edge in (60, 250, 500, 2000, 6000, 16000):
            if edge < nyq:
                ax.axvline(edge, color=p["border_hi"], lw=.9, zorder=1)
                ax.text(edge, top + (top - lo) * .012,
                        ("%gk" % (edge / 1000) if edge >= 1000 else "%g" % edge),
                        color=p["text_faint"], fontsize=p['fs_tick'], ha="center", va="bottom",
                        clip_on=False)

        ax.axvline(nyq, color=p["text_faint"], lw=1.0, ls=":", zorder=2)
        ax.text(nyq, lo + (top - lo) * .02, " 奈奎斯特 %.1fk" % (nyq / 1000),
                color=p["text_faint"], fontsize=p['fs_tick'], va="bottom", ha="right", rotation=90)
        # B14 精度：**披露频率分辨率**。曲线是 16384-FFT（Δf≈2.7Hz）、频谱图是
        # 4096-FFT（Δf≈10.8Hz）——同一截止在两图落在**不同精度**上，不写出来读数不可信。
        if len(f) > 1:
            ax.text(0.995, 0.02, "Δf ≈ %.3g Hz" % float(f[1] - f[0]), transform=ax.transAxes,
                    color=p["text_faint"], fontsize=p['fs_tick'], ha="right", va="bottom")

        # 填充 .16→.10：原来低频段糊成一块，把曲线本身盖住了
        ax.fill_between(f, db, lo, color=p["accent"], alpha=.10, lw=0, zorder=2)
        # 1/3 倍频程平滑线：**宏观走向**（原曲线的毛刺是内容，不替它）
        ax.semilogx(f, third_octave_smooth(f, db), lw=1.9, color=p["accent_hi"],
                    alpha=.85, zorder=3)
        # 核心数据曲线 1.5px（B7.4「核心锐利」）。它原来是 .85 的**发丝线**，
        # 在 2200px 宽的画布上等于"几乎看不见"——而这是本工具的核心证据。
        # 1/3 倍频程平滑线在它上面 1.9px：读数是"体"，原始曲线是"细节"，
        # 两者都落在 1.5–2px 带内且保持层级。
        ax.semilogx(f, db, lw=1.5, color=p["accent"], alpha=.95, zorder=3)

        # ---- 谱低端百分位：动态范围的下边界（有测量值才画，没测就不画）----
        # C0-5 正名：这不是"噪底"（噪底要按静音段测），是**频谱第 5 百分位**。
        # 图上文案跟着改——界面不许出现与 `theory.md §9` 口径不符的名字。
        nf = self.r.get("noise_floor_db")
        if nf and lo < nf < top:
            ax.axhline(nf, color=p["text_faint"], lw=.8, ls=":", zorder=2)
            ax.text(22, nf + (top - lo) * .012,
                    "谱低端 5%% %.0f dB" % nf,
                    color=p["text_faint"], fontsize=p['fs_tick'], va="bottom")

        cut = self.r.get("cutoff_hz")
        if cut:
            lv = LEVEL_COLOR_KEY.get(self.r.get("level"), "accent")
            col = p[lv] if lv in p else p["accent"]
            ax.axvline(cut, color=col, lw=1.4, ls="--", zorder=4)
            ax.fill_betweenx([lo, top], cut, nyq, color=col, alpha=.07, lw=0, zorder=1)
            # 标注靠右时向左展开，否则顶出绘图区被裁掉
            # （实测 20.27kHz 就在最右侧，原来的 xytext=(8,0) 把框推了出去）
            right_side = cut > nyq * 0.62
            ax.annotate("真实截止 %.2f kHz" % ((cut or 0) / 1000),
                        xy=(cut, top - (top - lo) * .04),
                        xytext=(-8 if right_side else 8, 0),
                        textcoords="offset points",
                        ha="right" if right_side else "left",
                        color=col, fontsize=p['fs_tick'], fontweight="bold", va="top",
                        bbox=dict(boxstyle="round,pad=0.32", fc=p["surface2"], ec=col, lw=.9))
        # ---- x 轴刻度：**这张图的全部意义是「内容停在哪」** ----
        # 默认对数轴只标注 10²/10³/10⁴ 三个点，关键区（10k–22k）一个刻度都没有，
        # 读者无法判断截止点是 18k 还是 21k —— 只能信标注框里那个数字，
        # 而"读数能自证"正是这张图该提供的东西。
        #
        # **位置必须在这里、不能提前**：semilogx() 内部会 set_xscale('log')，
        # 顺手重置定位器与格式器 —— 早先把刻度配置写在绘图之前，全部被冲掉，
        # 底轴照样只显示 10²/10³/10⁴（一跑图就看出来了）。
        #
        # 刻度按**当前视野**挑（zoom.pick_ticks）：缩放后如果还按全距那张表，
        # 放大到 100–200 Hz 时只剩两个刻度，等于"能放大但读不了数"。
        self._full_x = (20.0, float(nyq))
        if self._view_x is None or self._view_dirty:
            self._view_x = self._full_x
            self._view_dirty = False
        v0, v1 = self._view_x
        ticks = [t for t in Z.pick_ticks(v0, v1, log=True) if t <= nyq]
        ax.set_xticks(ticks)
        ax.set_xticklabels([("%gk" % (t / 1000) if t >= 1000 else "%g" % t) for t in ticks])
        # 次刻度只留 2/5 两档（对数频率轴的惯例）：既够精读，又不会糊成一片。
        # 早先 `minorticks_off()` 是因为**默认**次刻度太密——那是关错了对象。
        minor = [float(t * m)
                 for t in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
                 for m in (2, 5)]
        ax.set_xticks([v for v in minor if v0 < v <= nyq], minor=True)
        ax.set_xlim(v0, v1)
        # 顶部多留 14% 给频段标签，否则标签与截止标注挤在一起
        ax.set_ylim(lo, top + (top - lo) * 0.14)

    def _drop_hover(self):
        """只删自己加的三个图元，不碰静态内容。"""
        for art in (self._hline, self._hdot, self._hann):
            if art is not None:
                try:
                    art.remove()
                except (ValueError, NotImplementedError):
                    pass
        self._hline = self._hdot = self._hann = None

    def _hover_layer(self):
        """竖线 + 读数点 + 数值气泡。鼠标移动只重建这三个。"""
        p, ax = self.p, self.ax
        self._drop_hover()
        if not self._has_data():
            return
        arr = np.asarray(self.r["curve"], dtype=float)
        f = arr[:, 0]
        nyq = (self.r.get("sr") or 48000) / 2
        if not (self._hover and 20 <= self._hover <= nyq):
            return
        i = int(np.argmin(np.abs(f - self._hover)))
        self._hline = ax.axvline(f[i], color=p["text_dim"], lw=.8, zorder=5)
        self._hdot, = ax.plot([f[i]], [arr[i, 1]], "o", ms=4, color=p["text"], zorder=6)
        self._hann = ax.annotate("%.0f Hz    %.1f dB" % (f[i], arr[i, 1]),
                                 xy=(f[i], arr[i, 1]), xytext=(9, 9),
                                 textcoords="offset points", fontsize=p['fs_tick'],
                                 color=p["text"],
                                 bbox=dict(boxstyle="round,pad=0.34",
                                           fc=p["surface3"], ec=p["border_hi"], lw=.8))


# --------------------------------------------------------------------- 频谱图
class SpectrogramCanvas(BaseCanvas):
    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.r = None
        self.data = None
        self._msg = "选择条目后自动生成"
        self._cbar = None            # 色条只建一次，之后只换 mappable（见 _colorbar）
        # 对数频域重采样结果的缓存（键 = 数据身份 + 行数 + 列数，见 redraw）
        self._rs_key = None
        self._rs_cache = None
        self._cursor = None                     # B14 E.2-3 游标读数（时间/频率/dB）
        self._xhair = None                      # B14 补强⑤：**别处**指过来的频率参考线
        # B14：dB 显示窗按**画布实例**持有（可被用户切 60/90/120）。
        # 类属性做不到——同一次会话里两张图画同一个文件也共享不了各自的窗。
        self.db_range = SPECTRO_DB_RANGE
        self.mpl_connect("motion_notify_event", self._on_hover)

    def set_db_range(self, n: float):
        """切换 dB 显示窗。**幂等**：同值再调不重画。

        换了窗必须重画：窗变了，色条量程、`_rs_cache` 的裁剪界、游标读数全跟着变。
        """
        if abs(n - self.db_range) < 1e-9:
            return
        self.db_range = float(n)
        self.redraw_if_visible()

    def _on_hover(self, ev):
        """B14 E.2-3：鼠标处读数。**不知道就说不知道**（数据没渲染/不在轴内 → 藏起）。"""
        p = self.p
        if (p is None or not self.data or self._rs_cache is None
                or ev.inaxes is not self.ax or ev.xdata is None or ev.ydata is None):
            if self._cursor is not None:
                self._cursor.set_visible(False)
                self.draw_idle()
            # B14 补强⑤：鼠标离开了 ⇒ 别处的参考线也该收（否则会一直挂在那儿）
            CURSOR.clear(self)
            return
        t, f, _S = self.data
        fq = 10.0 ** float(ev.ydata)
        rows = np.geomspace(20.0, f[-1], self._rs_cache.shape[0])
        j = int(np.clip(np.searchsorted(rows, fq), 0, len(rows) - 1))
        k = int(np.clip(np.searchsorted(t, float(ev.xdata)), 0, len(t) - 1))
        txt = "%.2fs  %.0f Hz  %.1f dB" % (float(ev.xdata), fq, float(self._rs_cache[j, k]))
        if self._cursor is None:
            self._cursor = self.ax.text(
                0.015, 0.03, txt, transform=self.ax.transAxes, color=p["text"],
                fontsize=p['fs_tick'], va="bottom", ha="left", zorder=5,
                bbox=dict(boxstyle="round,pad=0.28", fc=p["surface"],
                          ec=p["border_hi"], lw=.6))
        else:
            self._cursor.set_text(txt)
        self._cursor.set_visible(True)
        self.draw_idle()
        # B14 补强⑤：把自己指到的频率广播出去（曲线/瀑布同步画参考线）
        CURSOR.emit(fq, self)

    def set_crosshair(self, hz: float) -> None:
        """B14 补强⑤：**别处**（曲线 / 瀑布）指到了某个频率。

        频率轴是对数的，参考线画在 log10(hz) 上。数据没渲染 / 频率落在轴外
        ⇒ 不画（"不知道就说不知道"——画到轴外会被裁掉，看着像没生效）。
        """
        p = self.p
        if p is None or not self.data or self._rs_cache is None:
            return
        _t, f, _S = self.data
        if not (20.0 <= float(hz) < f[-1]):
            return
        y = math.log10(float(hz))
        if self._xhair is None:
            self._xhair = self.ax.axhline(y, color=p["accent"], lw=1.0, ls=(0, (4, 3)),
                                          alpha=.9, zorder=4)
        else:
            self._xhair.set_ydata([y, y])
            self._xhair.set_visible(True)
        self.draw_idle()

    def clear_crosshair(self) -> None:
        if self._xhair is not None:
            self._xhair.set_visible(False)
            self.draw_idle()

    def set_data(self, r=None, data=None, msg=None):
        # 同一份数据（对象身份相同）再喂一次不必重画：`_load_view` 命中缓存时
        # 每次切页都会重复喂一遍，而这张图重绘一次要 470 ms（实测）。
        same = (r is self.r and data is self.data
                and (msg is None or msg == self._msg))
        self.r, self.data = r, data
        if msg:
            self._msg = msg
        if not (same and not self._stale):
            self.redraw_if_visible()

    def _has_data(self):
        return self.data is not None   # data=None 表示还没生成

    def redraw(self):
        self._stale = False
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        ax.clear()
        self._ax_style(ax, "时间 s", "频率 Hz")
        if not self.data:
            if self._cbar is not None:
                self._cbar.ax.set_visible(False)   # 空数据不该留着一条没有含义的色条
            ax.text(.5, .5, self._msg, ha="center", va="center", color=p["text_faint"],
                    transform=ax.transAxes, fontsize=p['fs_caption'])
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        t, f, S = self.data
        # 重采样结果按数据身份缓存：换肤、切页会反复重画**同一份**数据，
        # 而重采样是 2049→360 行的插值，不缓存等于每次白算一遍。
        # ⚠ 键必须含 db_range：换了窗，裁剪界就变，缓存不复用（否则 60dB 窗
        #    会画出 90dB 缓存的数据——看起来"换了没反应"，实则是旧数据）。
        key = (id(S), RESAMPLE_ROWS, len(t), self.db_range)
        if self._rs_key != key:
            self._rs_cache = np.clip(
                _resample_rows(S - np.nanmax(S), f, np.geomspace(20.0, f[-1],
                                                                 RESAMPLE_ROWS)),
                -self.db_range, 0.0)
            self._rs_key = key
        db = self._rs_cache
        # **对数频轴重采样 + imshow**，替代 `pcolormesh` + 对数轴。
        # 旧写法在 (2049, ~900) 的网格上为**每个格子建一个多边形**——约 180 万个，
        # 单这一项就 83 ms，加上对数轴的坐标变换让整图 draw 到 470 ms（实测，
        # 见 packaging/perf_probe.py）。改成：先在**对数频域**上重采样成 ~360 行，
        # 再用 imshow 画一张位图；纵轴用 log10 坐标 + 手动刻度，观感完全等价，
        # 代价只在"频域采样密度"上（360 行 ≈ 每 1/24 倍频程一行，够读）。
        mesh = ax.imshow(db, aspect="auto", origin="lower",
                         extent=(t[0], t[-1], math.log10(20.0), math.log10(f[-1])),
                         cmap=spectro_cmap(), vmin=-self.db_range, vmax=0.0, interpolation="nearest",
                         zorder=1)
        ax.set_ylim(math.log10(20.0), math.log10(f[-1]))
        ax.grid(False)
        for lev in (100, 1000, 10000):
            if lev < f[-1]:
                ax.axhline(math.log10(lev), color=p["grid"], lw=.6, alpha=.55, zorder=2)
        # 纵轴刻度：位置是 log10(Hz)，标签写 Hz（读起来和原来一样）
        ax.set_yticks([math.log10(v) for v in TICKS_HZ if v < f[-1]])
        ax.set_yticklabels([_hz_label(v) for v in TICKS_HZ if v < f[-1]])
        # B14 E.2-2 次刻度：主刻度之间按 2–9 倍频程细分，读频率时不用"估"
        minor = [math.log10(v * m) for v in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
                 for m in (2, 3, 4, 5, 6, 7, 8, 9)
                 if 20.0 <= v * m < f[-1]]
        ax.set_yticks(minor, minor=True)
        ax.tick_params(axis="y", which="minor", length=2, color=p["text_faint"])
        # B14 精度：披露频率分辨率（Δf = 相邻 bin 间距，从数据直接算，不靠 nperseg 猜）
        if len(f) > 1:
            ax.text(0.995, 0.02, "Δf ≈ %.3g Hz" % float(f[1] - f[0]), transform=ax.transAxes,
                    color=p["text_faint"], fontsize=p['fs_tick'], ha="right", va="bottom", zorder=3)
        self._colorbar(mesh, p)
        self.draw()

    def _colorbar(self, mesh, p):
        """色条：**建一次，之后只换 mappable**。

        为什么不能每次 `fig.colorbar(...)`：它每调一次就往 `fig.axes` 里再加一个
        Axes，而 `ax.clear()` 带不走它。旧写法（每次重画新建色条）的后果是
        ——色条握着 mappable → 握着那份 S 矩阵，于是每重画一次净增约 18 MB，
        且**不可回收**。实测量化（packaging/mem_probe.py）：
            点选 10 个文件：+179 MB        切 30 次主题：+577 MB
            figure.axes 从 2 涨到 31，renderer 每次要给 31 个轴做布局
        复用之后：axes 恒为 2（主图 + 1 个色条），30 次重画共 23 MB。
        """
        if self._cbar is None:
            self._cbar = self.fig.colorbar(mesh, ax=self.ax, pad=.015, fraction=.045)
        else:
            self._cbar.ax.set_visible(True)
            # 千万别先 `self._cbar.ax.clear()`：那会把 solids 从轴上摘下来，
            # `_add_solids()` 随后执行 `self.solids.remove()` 会抛
            # NotImplementedError: cannot remove artist（实测）。
            self._cbar.update_normal(mesh)
        cb = self._cbar
        cb.ax.tick_params(colors=p["text_faint"], labelsize=p['fs_tick'])
        cb.outline.set_edgecolor(p["border"])
        # B14：把**当前窗**写进色条标签——否则"看着比那张暗"分不清是内容差异
        # 还是窗不同（这正是我们不肯做自适应窗的理由：量纲必须说得出来）。
        cb.set_label("dB（峰值下 %.0f）" % self.db_range,
                     color=p["text_faint"], fontsize=p['fs_tick'])


# --------------------------------------------------------------- 立体声相位场
class StereoCanvas(BaseCanvas):
    """**立体声相位场**（自研；常规工具没有这一张）。

    ρ ∈ [−1,1] = L/R 在每个时频格的相关系数。频谱图丢相位、常规做法还先把
    立体声压成单声道，所以"反相"这一类缺陷在别处**根本看不见**；
    这张图把它变成可读的色块：**深玫瑰 = 完全反相**，近白 = 不相关，深青蓝 = 同相。

    与频谱图的区别只有三处：色图（发散 vs 顺序）、量程（固定 ±1 vs dB 窗）、
    读数的量纲（ρ vs dB）。其余（对数频轴、峰值保持重采样、色条复用、
    游标读数）**共用同一套实现**——同一类图不该有两套代码。
    """

    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.r = None
        self.data = None
        self._msg = "选择条目后自动生成"
        self._cbar = None
        self._rs_key = None
        self._rs_cache = None
        self._cursor = None
        self._xhair = None
        self.mpl_connect("motion_notify_event", self._on_hover)

    def _has_data(self):
        return self.data is not None

    def set_data(self, r=None, data=None, msg=None):
        same = (r is self.r and data is self.data
                and (msg is None or msg == self._msg))
        self.r, self.data = r, data
        if msg:
            self._msg = msg
        if not (same and not self._stale):
            self.redraw_if_visible()

    def _on_hover(self, ev):
        """读数 = 时间 / 频率 / **相关系数 ρ**。不知道就说不知道。"""
        p = self.p
        if (p is None or not self.data or self._rs_cache is None
                or ev.inaxes is not self.ax or ev.xdata is None or ev.ydata is None):
            if self._cursor is not None:
                self._cursor.set_visible(False)
                self.draw_idle()
            CURSOR.clear(self)
            return
        t, f, _rho = self.data
        fq = 10.0 ** float(ev.ydata)
        rows = np.geomspace(20.0, f[-1], self._rs_cache.shape[0])
        j = int(np.clip(np.searchsorted(rows, fq), 0, len(rows) - 1))
        k = int(np.clip(np.searchsorted(t, float(ev.xdata)), 0, len(t) - 1))
        rv = float(self._rs_cache[j, k])
        txt = "%.2fs  %.0f Hz  ρ %+.2f" % (float(ev.xdata), fq, rv)
        if self._cursor is None:
            self._cursor = self.ax.text(
                0.015, 0.03, txt, transform=self.ax.transAxes, color=p["text"],
                fontsize=p['fs_tick'], va="bottom", ha="left", zorder=5,
                bbox=dict(boxstyle="round,pad=0.28", fc=p["surface"],
                          ec=p["border_hi"], lw=.6))
        else:
            self._cursor.set_text(txt)
        self._cursor.set_visible(True)
        self.draw_idle()
        CURSOR.emit(fq, self)

    def set_crosshair(self, hz: float) -> None:
        p = self.p
        if p is None or not self.data or self._rs_cache is None:
            return
        _t, f, _rho = self.data
        if not (20.0 <= float(hz) < f[-1]):
            return
        y = math.log10(float(hz))
        if self._xhair is None:
            self._xhair = self.ax.axhline(y, color=p["accent"], lw=REF_LINE_W,
                                          ls=(0, (4, 3)), alpha=.9, zorder=4)
        else:
            self._xhair.set_ydata([y, y])
            self._xhair.set_visible(True)
        self.draw_idle()

    def clear_crosshair(self) -> None:
        if self._xhair is not None:
            self._xhair.set_visible(False)
            self.draw_idle()

    def redraw(self):
        self._stale = False
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        ax.clear()
        self._ax_style(ax, "时间\u00a0s", "频率\u00a0Hz")
        if not self.data:
            if self._cbar is not None:
                self._cbar.ax.set_visible(False)
            ax.text(.5, .5, self._msg, ha="center", va="center", color=p["text_faint"],
                    transform=ax.transAxes, fontsize=p['fs_caption'])
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        t, f, rho = self.data
        key = (id(rho), RESAMPLE_ROWS, len(t))
        if self._rs_key != key:
            self._rs_cache = _resample_rows(
                rho, f, np.geomspace(20.0, f[-1], RESAMPLE_ROWS), signed=True)
            self._rs_key = key
        mesh = ax.imshow(self._rs_cache, aspect="auto", origin="lower",
                         extent=(t[0], t[-1], math.log10(20.0), math.log10(f[-1])),
                         cmap=phase_cmap(), vmin=-1.0, vmax=1.0,
                         interpolation="nearest", zorder=1)
        ax.set_ylim(math.log10(20.0), math.log10(f[-1]))
        ax.grid(False)
        for lev in (100, 1000, 10000):
            if lev < f[-1]:
                ax.axhline(math.log10(lev), color=p["grid"], lw=.6, alpha=.55, zorder=2)
        ax.set_yticks([math.log10(v) for v in TICKS_HZ if v < f[-1]])
        ax.set_yticklabels([_hz_label(v) for v in TICKS_HZ if v < f[-1]])
        minor = [math.log10(v * m) for v in (10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000)
                 for m in (2, 3, 4, 5, 6, 7, 8, 9)
                 if 20.0 <= v * m < f[-1]]
        ax.set_yticks(minor, minor=True)
        ax.tick_params(axis="y", which="minor", length=2, color=p["text_faint"])
        # 披露分辨率（与频谱图同口径）——相位场同样吃 STFT 的窗长
        if len(f) > 1:
            ax.text(0.995, 0.02, "Δf ≈ %.3g Hz" % float(f[1] - f[0]), transform=ax.transAxes,
                    color=p["text_faint"], fontsize=p['fs_tick'], ha="right", va="bottom",
                    zorder=3)
        self._colorbar(mesh, p)
        self.draw()

    def _colorbar(self, mesh, p):
        """色条（与频谱图同一套"建一次只换 mappable"的做法，见那边的长注释）。"""
        if self._cbar is None:
            self._cbar = self.fig.colorbar(mesh, ax=self.ax, pad=.015, fraction=.045)
        else:
            self._cbar.ax.set_visible(True)
            self._cbar.update_normal(mesh)
        cb = self._cbar
        cb.ax.tick_params(colors=p["text_faint"], labelsize=p['fs_tick'])
        cb.outline.set_edgecolor(p["border"])
        cb.set_label("L/R 相关 ρ（−1 反相 ← → +1 同相）",
                     color=p["text_faint"], fontsize=p['fs_tick'])


# --------------------------------------------------------------------- 频段能量
class BandCanvas(BaseCanvas):
    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.r = None

    def set_data(self, r):
        self.r = r
        self.redraw_if_visible()

    def _has_data(self):
        return bool(self.r)   # r 为 None 表示未选中

    def redraw(self):
        self._stale = False
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        ax.clear()
        self._ax_style(ax, None, "能量占比 %")
        r = self.r
        b = (r or {}).get("bands_pct") or {}
        if not b:
            ax.text(.5, .5, "选择左侧条目以查看频段分布", ha="center", va="center",
                    color=p["text_faint"], transform=ax.transAxes, fontsize=p['fs_caption'])
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        names = [BAND_CN[k] for k, _, _ in BANDS]
        keys = [k for k, _, _ in BANDS]
        vals = [b.get(k, 0) for k in keys]
        # 频段是**有序**类别（低→高），配顺序型色阶才对；色阶从当前主色派生
        # （OKLCH 等 L 步长），于是换种子色时这张图也跟着变，而不是永远 viridis。
        dark = self._plot_is_dark()
        colors = semantic_ramp(len(keys), self.p["accent"], dark=dark)
        bars = ax.bar(range(len(keys)), vals, color=colors, width=.68, zorder=3)
        for rect, v in zip(bars, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, v + max(vals + [1]) * .025,
                    "%.0f%%" % v, ha="center", color=p["text"], fontsize=p['fs_tick'], fontweight="bold")
        for i, (_k, lo, hi) in enumerate(BANDS):
            ax.text(i, -max(vals + [1]) * .085, "%s–%s" % (
                lo if lo < 1000 else "%dk" % (lo // 1000),
                hi if hi < 1000 else "%dk" % (hi // 1000)),
                ha="center", color=p["text_faint"], fontsize=p['fs_tick'])
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(names, color=p["text_dim"], fontsize=p['fs_tick'])
        ax.set_ylim(0, max(vals + [1]) * 1.20)
        ax.tick_params(axis="x", pad=14)
        self.draw()


# --------------------------------------------------------------------- 3D 瀑布
class WaterfallCanvas(BaseCanvas):
    """3D 瀑布频谱：把时间轴立起来，低频堆积与高频骤停一目了然。"""

    #: 拖拽重绘的节流间隔（毫秒）。≈8 fps：3D 重绘在本机实测 367 ms/帧，
    #: 不节流的话每个鼠标移动事件都排一次重绘，越拖越卡、松手后还在补画。
    #: **不是动效时长**，所以不进 `theme.Motion`（见那张表的 docstring）。
    DRAG_THROTTLE_MS = 40      # B14 E.2-5：拖拽更丝滑（原 120 有肉眼可见的顿挫）

    def __init__(self):
        # 3D 轴与 constrained layout 不兼容（会把轴挤成 0 宽），这里单独建 figure
        self.fig = Figure(figsize=(6.2, 3.8), dpi=100)
        FigureCanvasQTAgg.__init__(self, self.fig)
        self.setStyleSheet("background:transparent;")
        self.p = None
        self.ax = None
        self.r = None
        self.data = None
        self._msg = "选择条目后自动生成"
        self._elev, self._azim = 26, -58
        self._elev0, self._azim0 = 26, -58      # 复位目标（B14 E.2-5）
        # B14：dB 显示窗按**画布实例**持有（与频谱图同一套控件驱动）。
        self.db_range = SPECTRO_DB_RANGE
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._drag = None
        # 拖拽节流：3D 重绘在本机实测 367 ms/帧（2.7 fps），不节流的话
        # 每个鼠标移动事件都排一次重绘，越拖越卡、松手后还在补画。
        # 120 ms ≈ 8 fps：这是**节流间隔**不是动效时长，所以不进 `Motion` 表，
        # 但必须具名（B3 的规则：动画时长进表，节流间隔具名 + 在原处说明依据）。
        self._drag_timer = QTimer(self)
        self._drag_timer.setSingleShot(True)
        self._drag_timer.setInterval(self.DRAG_THROTTLE_MS)
        # B14 补强⑤：联动参考线的节流计时器。**与拖拽分开**——两者会同时发生
        # （一边拖旋转一边被别处指频率），共用一个计时器会互相把对方的帧吞掉。
        self._xhz = None
        self._x_timer = QTimer(self)
        self._x_timer.setSingleShot(True)
        self._x_timer.setInterval(self.DRAG_THROTTLE_MS)
        self._x_timer.timeout.connect(self.redraw)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_drag)

    def set_crosshair(self, hz: float) -> None:
        """B14 补强⑤：**别处**指到了某个频率。

        3D 里画一条"频率墙"的底边。代价说明：3D 加一条线也要整帧重画
        （257 ms/帧），所以**节流到 40 ms**——与拖拽同量级。2D 两张不节流。
        """
        self._xhz = float(hz)
        if not self._x_timer.isActive():
            self._x_timer.start()

    def clear_crosshair(self) -> None:
        if self._xhz is not None:
            self._xhz = None
            self.redraw()

    def set_db_range(self, n: float):
        """切换 dB 显示窗（幂等）。换了窗：切片底面与 z 轴量程都跟着变。"""
        if abs(n - self.db_range) < 1e-9:
            return
        self.db_range = float(n)
        self.redraw_if_visible()

    def set_data(self, r=None, data=None, msg=None):
        same = (r is self.r and data is self.data
                and (msg is None or msg == self._msg))
        self.r, self.data = r, data
        if msg:
            self._msg = msg
        if not (same and not self._stale):
            self.redraw_if_visible()

    def _on_press(self, ev):
        if ev.inaxes is self.ax and ev.button == 1:
            self._drag = (ev.x, ev.y, self._elev, self._azim)

    def _on_drag(self, ev):
        if not self._drag or ev.x is None:
            return
        x0, y0, e0, a0 = self._drag
        self._elev = max(4, min(88, e0 + (ev.y - y0) * 0.35))
        self._azim = a0 - (ev.x - x0) * 0.35
        # 角度**每次都更新**（状态无损），只把重绘限流到 ~8 fps。
        # 先画一帧再启动计时器：跟手感来自"按下即有响应"。
        if not self._drag_timer.isActive():
            self.redraw()
            self._drag_timer.start()

    def _release(self, ev):
        self._drag = None

    def mouseReleaseEvent(self, ev):
        was_dragging = self._drag is not None
        self._drag = None
        self._drag_timer.stop()
        if was_dragging:
            self.redraw()              # 松手补一帧最终角度（拖拽期间被节流掉的那部分）
        super().mouseReleaseEvent(ev)

    def reset_view(self):
        """复位视角（B14 E.2-5）：回到初始 elev/azim。**幂等**（连按两次结果一致）。"""
        self._elev, self._azim = self._elev0, self._azim0
        self.redraw()

    def mouseDoubleClickEvent(self, ev):        # 双击复位
        self.reset_view()
        super().mouseDoubleClickEvent(ev)

    def keyPressEvent(self, ev):                # R / 0 复位
        if ev.key() in (Qt.Key.Key_R, Qt.Key.Key_0):
            self.reset_view()
            return
        super().keyPressEvent(ev)

    def _has_data(self):
        return self.data is not None   # data=None 表示还没生成

    def redraw(self):
        self._stale = False
        p = self.p
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        if self.ax is None:                           # 3D 轴默认会缩成一小块，这里铺满但留边
            self.ax = self.fig.add_axes([0.01, -0.02, 0.98, 1.04], projection="3d")
        ax = self.ax
        ax.clear()
        ax.set_facecolor(p["surface"])
        if not self.data:
            ax.set_axis_off()
            ax.text2D(.5, .5, self._msg, ha="center", va="center", transform=ax.transAxes,
                      color=p["text_faint"], fontsize=p['fs_caption'])
            self.draw()
            return
        t, f, S = self.data
        m = (f >= 20)
        f2, S2 = np.asarray(f)[m], np.asarray(S)[m]
        db = S2 - np.nanmax(S2)
        # 每条谱线只取 WATERFALL_POINTS 个**对数等距**频点：原来每条 2000 点，
        # 30 条就是 6 万个 3D 顶点——那是这张图 370 ms 的来源。
        # 300 点 ≈ 每 1/12 倍频程一点（正好一个临界频带），肉眼看不出差别。
        fl = np.geomspace(20.0, f2[-1], WATERFALL_POINTS)
        db = _resample_rows(db, f2, fl)
        lf = np.log10(fl)
        n = db.shape[1]
        cmap = spectro_cmap()
        from matplotlib.collections import PolyCollection
        from matplotlib.colors import to_rgb
        bg = to_rgb(p["surface"])
        # B14 E.2-1：**填充带状切片 + 深度雾 + 脊线高光**。
        # 细线→填充片给了"实体"，但实体之间还是"平"的；补两条物理线索：
        #   ① 深度雾：越远（时间越早）越向背景色收敛 ⇒ 大气透视，纵深可分；
        #   ② 脊线高光：每片的上沿提亮描一条 ⇒ 顶面受光，片与片分层。
        # 这是 3D 可视化里最省算力的立体线索（不引入 LightSource 的逐面法线计算）。
        step = max(1, n // WATERFALL_SLICES)
        idxs = list(range(0, n, step))
        verts, facecols = [], []
        for i in idxs:
            u = i / max(1, n - 1)
            v = ([(lf[0], -self.db_range)] + list(zip(lf, db[:, i]))
                 + [(lf[-1], -self.db_range)])
            verts.append(v)
            col = cmap(0.10 + 0.82 * u)
            fog = 0.34 * (1.0 - u)               # 远端向背景收敛（大气透视）
            facecols.append((float(col[0] * (1 - fog) + bg[0] * fog),
                             float(col[1] * (1 - fog) + bg[1] * fog),
                             float(col[2] * (1 - fog) + bg[2] * fog),
                             0.40 + 0.55 * u))
        poly = PolyCollection(verts, facecolors=facecols,
                              edgecolors=(1.0, 1.0, 1.0, 0.10), linewidths=0.25)
        ax.add_collection3d(poly, zs=idxs, zdir="y")
        # 脊线高光：每片上沿提亮描一条（顶面受光），立体感的主要来源
        for i in idxs:
            u = i / max(1, n - 1)
            rc = cmap(min(1.0, 0.16 + 0.80 * u))
            ax.plot(lf, db[:, i], zs=i, zdir="y",
                    color=(float(rc[0]), float(rc[1]), float(rc[2])),
                    lw=0.8, alpha=0.34 + 0.56 * u, solid_capstyle="round")
        ax.set_zlim(-self.db_range, 0)
        ax.set_ylim(0, n)
        ax.set_xlim(lf[0], lf[-1])
        try:
            ax.set_box_aspect((1.55, 1.0, 0.78), zoom=1.12)
        except TypeError:                             # 老版本 matplotlib 不支持 zoom
            ax.set_box_aspect((1.55, 1.0, 0.78))
        ax.view_init(elev=self._elev, azim=self._azim)
        ticks = [v for v in (1, 2, 3, 4) if lf[0] <= v <= lf[-1]]
        ax.set_xticks(ticks)
        ax.set_xticklabels(["%g" % (10 ** v) if v < 3 else "%gk" % (10 ** (v - 3)) for v in ticks],
                           fontsize=p['fs_tick'])
        ax.set_yticks([0, n // 2, n])
        ax.set_yticklabels(["0", "%.0fs" % (t[len(t) // 2] if len(t) else 0),
                            "%.0fs" % (t[-1] if len(t) else 0)], fontsize=p['fs_tick'])
        ax.set_zticks([-80, -60, -40, -20, 0])
        ax.set_zticklabels(["-80", "-60", "-40", "-20", "0"], fontsize=p['fs_tick'])
        ax.set_xlabel("Hz", color=p["text_dim"], fontsize=p['fs_tick'], labelpad=-2)
        ax.set_ylabel("时间", color=p["text_dim"], fontsize=p['fs_tick'], labelpad=-2)
        ax.set_zlabel("dB", color=p["text_dim"], fontsize=p['fs_tick'], labelpad=-4)
        ax.tick_params(colors=p["text_faint"], pad=-2)
        for pane in (ax.xaxis, ax.yaxis, ax.zaxis):
            pane.set_pane_color((0, 0, 0, 0))
            pane.line.set_color(p["border"])
        ax.grid(True, color=p["grid"], lw=.5, alpha=.55)
        cut = (self.r or {}).get("cutoff_khz")
        ax.text2D(.015, .95, ("真实截止 %.2f kHz" % cut) if cut else "", transform=ax.transAxes,
                  color=p["text_dim"], fontsize=p['fs_tick'])
        # B14 精度：披露频率/时间分辨率（Δf、Δt 从数据直接算，与频谱图同口径）
        df = float(f[1] - f[0]) if len(f) > 1 else 0.0
        dt = float(t[1] - t[0]) if len(t) > 1 else 0.0
        ax.text2D(.015, .90, "Δf ≈ %.3g Hz   Δt ≈ %.3g s" % (df, dt), transform=ax.transAxes,
                  color=p["text_faint"], fontsize=p['fs_tick'])
        ax.text2D(.86, .95, "拖拽旋转 · 双击复位 · F 全屏", transform=ax.transAxes, color=p["text_faint"], fontsize=p['fs_tick'])
        # B14 补强⑤：**别处**指过来的频率 → 画一面"频率墙"（底边 + 顶边）。
        # 只画两条边不画面：满幅半透明面会把背后的数据压掉，而参考线的职责
        # 是"指位置"，不是"遮内容"。
        if self._xhz is not None and lf[0] <= math.log10(self._xhz) <= lf[-1]:
            lx = math.log10(self._xhz)
            for z in (-self.db_range, 0.0):
                ax.plot([lx, lx], [0, n], [z, z], color=p["accent"], lw=REF_LINE_W,
                        alpha=.55, zorder=6)
        self.draw()


# --------------------------------------------------------------------- 多文件对比
class CompareCanvas(BaseCanvas):
    """把多个文件的频谱叠在一张图上——判断「同专辑/同批源」的最快方式。

    十条曲线叠在一起时，**关掉几条**比放大更常用：先排到只剩两条可疑的，
    再放大细看。所以这里同时给了图例开关（点图例）与缩放/平移。
    """

    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.items = []
        #: 末端标注对象（判据 9 要用）与降级标志（判据 9 第 4 条要用）。
        #: 在构造里就给初值：`set_data([])` 之后的 `redraw` 可能在
        #: `_draw_end_labels` 之前就被别处读到（构造函数顺序不是接口）。
        self.end_labels = []
        self.direct_degraded = False
        # B14 补强⑥：视图模式。"overlay" 叠加（默认）｜ "grid" 并排小图阵列。
        self.mode = "overlay"
        self._grid_axes: list = []
        # ---- 缩放 / 平移（与频谱曲线同一套逻辑，见 SpectrumCanvas）----
        self._full_x = (20.0, 24000.0)
        self._view_x = None            # None = 未缩放 / 数据刚换过（见 SpectrumCanvas）
        self._view_dirty = False
        self._pan_from = None
        self._view_timer = None
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.mpl_connect("scroll_event", self._on_scroll)
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("button_release_event", self._on_release)
        self.mpl_connect("motion_notify_event", self._on_move)
        self.mpl_connect("key_press_event", self._on_key)
        self.mpl_connect("pick_event", self._on_pick)

    def set_data(self, items):
        if items is not self.items:
            self._view_dirty = True
        self.items = items or []
        self.redraw_if_visible()

    def _has_data(self):
        return bool(self.items)   # items 空表示没有多选

    # ---- 缩放 / 平移 ------------------------------------------------------
    VIEW_DEBOUNCE_MS = 28

    def _schedule_view(self):
        if self._view_timer is None:
            self._view_timer = QTimer(self)
            self._view_timer.setSingleShot(True)
            self._view_timer.setInterval(self.VIEW_DEBOUNCE_MS)
            self._view_timer.timeout.connect(self._apply_view)
        self._view_timer.start()

    def _apply_view(self):
        if not self._has_data():
            return
        self.ax.set_xlim(*self._view_x)
        self.draw_idle()

    def _zoom(self, step: int, anchor=None):
        if not self._has_data():
            return
        lo, hi = self._view_x
        mid = (lo + hi) / 2 if anchor is None else anchor
        self._view_x = Z.zoom_step(lo, hi, self._full_x[0], self._full_x[1], step, mid,
                                   log=True)
        self._schedule_view()

    def _pan_by(self, frac: float):
        if not self._has_data():
            return
        a, b = Z.pan_span(*self._view_x, frac, log=True)
        self._view_x = Z.clamp_span(a, b, self._full_x[0], self._full_x[1], log=True)
        self._schedule_view()

    def reset_view(self):
        self._view_x = self._full_x
        self._schedule_view()

    def _on_scroll(self, ev):
        if ev.inaxes is not self.ax or not self._has_data():
            return
        step = getattr(ev, "step", 0) or (1 if getattr(ev, "button", "") == "up" else -1)
        self._zoom(step, ev.xdata)
        self.setFocus()

    def _on_press(self, ev):
        if ev.inaxes is not self.ax or not self._has_data():
            return
        if ev.button == 1:
            self._pan_from = (ev.xdata, ev.ydata)
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif ev.button == 3:
            self._zoom(1, ev.xdata)

    def _on_release(self, ev):
        if self._pan_from is not None:
            self._pan_from = None
            self.unsetCursor()

    def _on_move(self, ev):
        if self._pan_from is not None:
            if ev.inaxes is not self.ax or not ev.xdata or ev.xdata <= 0:
                return
            lo, hi = self._view_x
            span_l = math.log10(hi) - math.log10(lo)
            if span_l <= 0:
                return
            frac = -(math.log10(ev.xdata) - math.log10(self._pan_from[0])) / span_l
            self._pan_from = (ev.xdata, self._pan_from[1])
            self._pan_by(frac)
            return
        # 拖拽提示：十条曲线叠着时给个抓手提示，否则用户不知道能平移
        if ev.inaxes is self.ax and self._has_data():
            self.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.unsetCursor()

    def _on_key(self, ev):
        if not self._has_data():
            return
        k = (ev.key or "").lower()
        if k in ("+", "="):
            self._zoom(1)
        elif k in ("-", "_"):
            self._zoom(-1)
        elif k in ("0", "r"):
            self.reset_view()
        elif k in ("left", "right"):
            self._pan_by(0.25 if k == "right" else -0.25)

    def _on_pick(self, ev):
        """点图例 = 开关对应曲线。

        十条曲线叠在一张图上时，第一件事是"关掉几条"。matplotlib 的
        legend 支持 `set_picker(True)` + pick_event，但那样拿不到
        "哪一条被点了"的稳定标识——这里改成直接命中测试：
        图例文字的画布坐标落在鼠标位置上，就开关那一组。
        """
        leg = getattr(self, "_legend", None)
        mouse = getattr(ev, "mouseevent", None)
        if leg is None or mouse is None or mouse.inaxes is not self.ax:
            return
        # ⚠ 顺序要紧：**先测末端标注，再测图例**。标注贴在曲线右端、图例在
        # 左下角，正常不重叠；但放大后图例会挤到右边，若先测图例就可能
        # 把"点标注"误判成"点图例"。
        for lab in getattr(self, "end_labels", []):
            if lab.get_visible() and lab.get_window_extent().contains(mouse.x, mouse.y):
                self._toggle_by_artist(lab)
                return
        for text, line in zip(leg.get_texts(), self._lines):
            if not text.get_visible():
                continue
            bb = text.get_window_extent()
            if bb.contains(mouse.x, mouse.y):
                vis = not line.get_visible()
                line.set_visible(vis)
                text.set_alpha(1.0 if vis else 0.35)
                self.draw_idle()
                return

    #: 并排模式每行最多几列。3 列在 900px 宽窗上每格约 290px——频谱曲线缩到
    #: 290px 时仍看得出砖墙台阶在哪；4 列只剩约 210px，台阶开始糊。
    GRID_COLS = 3
    #: 并排模式最多画几张。超过就得**分页**（那是另一种交互），这里先给上限并
    #: 在标题上写明"只画前 N 张"——**不许静默截断**（用户会以为这就是全部）。
    GRID_MAX = 9

    def set_mode(self, mode: str) -> None:
        """切换 叠加 / 并排。**幂等**（同值不重画）。"""
        if mode not in ("overlay", "grid") or mode == self.mode:
            return
        self.mode = mode
        self.redraw_if_visible()

    def _clear_grid(self) -> None:
        for a in self._grid_axes:
            a.remove()
        self._grid_axes = []

    def _redraw_grid(self, p) -> None:
        """并排小图：**每个文件一张同轴小图**，扫一遍就能看出谁不一样。

        与叠加的分工：叠加读"差异有多细"（能叠着比同一段频响），并排读
        "这一批里谁不合群"（十几个文件时叠加会糊成一片）。
        每格保留自己的截止标注，标题带文件名——**不需要图例**（图例是叠加的负担）。
        """
        self._clear_grid()
        items = self.items[:self.GRID_MAX]
        n = len(items)
        cols = min(self.GRID_COLS, n)
        rows = (n + cols - 1) // cols
        for k, (r, cur) in enumerate(items):
            ax = self.fig.add_subplot(rows, cols, k + 1)
            self._grid_axes.append(ax)
            arr = np.asarray(cur, dtype=float)
            self._ax_style(ax, None, None)
            top = float(np.percentile(arr[:, 1], 99.5))
            # lw=1.5：小图里的曲线**也是数据曲线**，走同一条判据（1.5–2px）。
            # 别因为"图小了"就调细——判据盯的是"数据墨够不够重"，与画布尺寸无关。
            ax.semilogx(arr[:, 0], arr[:, 1] - top, lw=1.5, color=p["accent"],
                        alpha=.95, zorder=3)
            cut = r.get("cutoff_hz")
            if cut:
                ax.axvline(cut, color=p["text_dim"], lw=0.9, ls="--", alpha=.6, zorder=2)
            ax.axhline(0, color=p["text_faint"], lw=.6, ls=":", zorder=1)
            ax.set_xlim(20.0, max(float(r.get("sr", 48000)) / 2.0, 24000.0))
            ax.set_ylim(-80, 8)
            # 每格标题 = 文件名 + 真实截止（并排时图例没地方放，信息进标题）
            nm = str(r.get("file") or "")[:22]
            ck = r.get("cutoff_khz")
            ax.set_title(("%s  ｜ %.1fk" % (nm, ck)) if ck else nm,
                         color=p["text_dim"], fontsize=p["fs_micro"], pad=4)
            # 轴标签只给**最左列 / 最下行**，否则 9 张图会挂 9 组标签把图压扁
            if k % cols == 0:
                ax.set_ylabel("dB", color=p["text_faint"], fontsize=p["fs_tick"],
                              labelpad=2)
            if k >= n - cols:
                ax.set_xlabel("Hz", color=p["text_faint"], fontsize=p["fs_tick"],
                              labelpad=2)
            ax.tick_params(axis="both", labelsize=p["fs_tick"], pad=1)
        if len(self.items) > self.GRID_MAX:
            # **不许静默截断**：说清楚只画了前几张
            self.fig.text(0.5, 0.005, "仅显示前 %d 张（共 %d 张）"
                          % (self.GRID_MAX, len(self.items)),
                          ha="center", va="bottom", color=p["text_faint"],
                          fontsize=p["fs_tick"])
        self.draw()

    def redraw(self):
        self._stale = False
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        # B14 补强⑥：**并排（small multiples）**模式。叠加适合"几条可疑的放一起比"，
        # 并排适合"十几个文件扫一遍看谁不一样"——后者叠起来会糊成一片。
        if self.mode == "grid" and len(self.items) > 1:
            self.ax.set_visible(False)
            self._redraw_grid(p)
            return
        self._clear_grid()
        self.ax.set_visible(True)
        ax.clear()
        self._ax_style(ax, "频率 Hz", "相对电平 dB")
        if not self.items:
            ax.text(.5, .5, "在左侧按住 Ctrl 选中多个条目即可叠加对比",
                    ha="center", va="center", color=p["text_faint"],
                    transform=ax.transAxes, fontsize=p['fs_caption'])
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        nyq = 48000
        # 前两条曲线用**当前主题的主色 / 次级强调色**（分裂互补），
        # 第 3..7 条用主题的**装饰色阶**（六等分色环，跳过第 1 档——
        # 它与主色同色相，只是明度不同，并排放会误以为是同一条），
        # 最后才轮到通用色板：这样"这一批里最重要的几条"看起来是同一套
        # 配色里的几个声音，而不是从通用色板里各抓一个。
        # 装饰色阶的每一档都按"对 plot_bg ≥3:1"生成（`palette.audit_deco`），
        # 所以十条曲线叠在一张图上仍然条条可辨——不用再逐条肉眼核。
        seq = (p["accent"], p.get("accent2", p["accent"])) \
            + tuple(p.get("deco") or ())[1:] + cycle_for(p)
        self._lines = []
        for i, (r, cur) in enumerate(self.items[:10]):
            arr = np.asarray(cur, dtype=float)
            col = seq[i % len(seq)]
            nyq = max(nyq, r.get("sr", 48000) / 2)
            top = float(np.percentile(arr[:, 1], 99.5))
            ln, = ax.semilogx(arr[:, 0], arr[:, 1] - top, lw=1.5, color=col, alpha=.95,
                              label="%s  ｜ %.1fk" % (r["file"][:30], (r.get("cutoff_khz") or 0)),
                              zorder=3)
            self._lines.append(ln)
            cut = r.get("cutoff_hz")
            if cut:
                ax.axvline(cut, color=col, lw=1.0, ls="--", alpha=.55, zorder=2)
                # 特效第 1 招（**光有落点**）：截止位置拖一条极低透明度竖向淡染。
                # 只给**第一条**曲线画——"一屏一处最强"（特效第 3 招）：10 条
                # 各染一道会变成一片底色，反而看不出"截止在哪"。
                if i == 0:
                    ax.axvspan(cut * 0.985, cut * 1.015, color=col,
                               alpha=CUTOFF_FADE_ALPHA, lw=0, zorder=1.5)
        ax.axhline(0, color=p["text_faint"], lw=.7, ls=":", zorder=1)
        # 全距按当前数据的最大奈奎斯特算；换一批文件要回全距
        self._full_x = (20.0, float(nyq))
        if self._view_x is None or self._view_dirty:
            self._view_x = self._full_x
            self._view_dirty = False
        ax.set_xlim(*self._view_x)
        ax.set_ylim(-80, 8)
        leg = ax.legend(loc="lower left", fontsize=p['fs_tick'], framealpha=.92, ncol=1,
                        facecolor=p["surface2"], edgecolor=p["border_hi"], labelcolor=p["text"])
        leg.get_frame().set_linewidth(.8)
        self._legend = leg
        self._draw_end_labels(ax, p)
        self.draw()

    #: 末端标注的上限（判据 9 第 4 条）。**8 的由来**：一屏能舒适容纳的贴边
    #: 标签数——10 条曲线时标签会连成一整列，"这条是谁"反而更难对。
    #: 超量**降级回图例**并置 `direct_degraded` 标志：降级不许静默。
    MAX_DIRECT = 8
    #: 标签之间的最小垂直间距（像素）。**实测**：9pt 标签的
    #: window_extent 高 **14.1px**（900×520 画布，offscreen），
    #: 取 18 = 字高 + 约 4px 空隙。取 15 时实测间距只有 12.3px——字压字。
    LABEL_GAP = 18.0

    def _draw_end_labels(self, ax, p) -> None:
        """每条可见曲线的右端贴一个同色的名字（B8-H1 的四条做法）。

        **避让是确定性的**：按末端 y 排序后**一趟推匀**（`yy = max(y, 上一个+间隙)`），
        不做迭代求解——迭代的收敛性依赖初值，同一份数据可能摆出两种样子。
        """
        self.end_labels = []
        self.direct_degraded = False
        # ⚠ `self.items` 的每个元素是 `(r, cur)` **二元组**（曲线循环里
        # `for i, (r, cur) in enumerate(self.items)` 就是这么解的）。
        # 第一版写成 `zip(self._lines, self.items)`，于是 `r` 拿到的是元组，
        # `r.get("file")` 抛 `'tuple' object has no attribute 'get'`。
        vis = [(ln, it[0]) for ln, it in zip(self._lines, self.items)]
        if not vis:
            return
        if len(vis) > self.MAX_DIRECT:
            self.direct_degraded = True      # **降级可见**：图例还在，标志置上
            return
        y0, y1 = ax.get_ylim()
        span = y1 - y0
        # ⚠ **不能用 `ax.get_window_extent().height` 单独估像素**：redraw 可能
        # 发生在布局之前，那时它是 0，`gap = 15*88/1` 会被放大成整个量程，
        # 于是所有标签被推到同一条线上——判据 ② 实测就是这样红的
        # （"曲目0.flac 与 曲目1.flac 压在一起"）。
        # 控件高度是**始终有效**的量，取它乘一个坐标区占比，并加下限兜底。
        try:
            h_widget = float(self.height())
        except Exception:                       # 探针/无 GUI 场景
            h_widget = 0.0
        # **优先用真实坐标区高度**（上一轮 draw 留下的值在布局后就是准的）；
        # 只有它不可信（还没画过、≈0）时才退回控件高度估。
        h_ext = float(ax.get_window_extent().height)
        h_px = h_ext if h_ext > 60.0 else max(60.0, h_widget * 0.60)
        gap = self.LABEL_GAP * span / h_px    # 像素间隙 → 数据单位
        pts = []
        for ln, r in vis:
            if not ln.get_visible():
                continue
            x, y = ln.get_xdata(), ln.get_ydata()
            k = len(x) - 1
            while k > 0 and not np.isfinite(y[k]):
                k -= 1
            pts.append((float(y[k]), float(x[k]), ln, r))
        # 按末端 y 排序 → 一趟推匀（确定性）
        pts.sort(key=lambda t: t[0])
        # 一趟推匀：`yy = max(自己的位置, 上一个 + 间隙)`——**确定性、无迭代**。
        placed = []
        last = None
        for y, x, ln, r in pts:
            yy = y if last is None else max(y, last + gap)
            placed.append([yy, x, ln, r])
            last = yy
        # 顶到上界时**整块下移**，不是逐个夹——逐个夹会让越界的几个
        # 全部落到 `y1-gap` 同一点上，反而制造碰撞（第一版的第二个错）。
        over = placed[-1][0] - (y1 - gap)
        if over > 0:
            for it in placed:
                it[0] -= over
        for yy, x, ln, r in placed:
            yy = min(max(yy, y0 + gap), y1 - gap)        # 极端情形兜底
            lab = ax.annotate(self._short(r), xy=(x, yy), xytext=(-6, 0),
                              textcoords="offset points", ha="right", va="center",
                              color=ln.get_color(), fontsize=p["fs_tick"], zorder=6)
            lab.audiocheck_line = ln                    # pick 时靠它找曲线
            self.end_labels.append(lab)

    @staticmethod
    def _short(r) -> str:
        """末端标签的名字：**只要文件名**，超长截断。

        不用图例那串 `名字 ｜ 16.0k`——末端标签贴着曲线，读者已经在看那条线了，
        再挂截止值就是重复；截断到 16 字符是为了让标签不被坐标区右边界吃掉。
        """
        name = str(r.get("file") or "")
        return name if len(name) <= 16 else name[:15] + "…"

    def _toggle_by_artist(self, artist) -> None:
        """按被点的对象开关对应曲线（标注与图例走同一条路径）。"""
        for ln in self._lines:
            if artist is ln:
                self._set_visible(ln, not ln.get_visible())
                return
            if getattr(artist, "audiocheck_line", None) is ln:
                self._set_visible(ln, not ln.get_visible())
                return

    def _set_visible(self, ln, vis: bool) -> None:
        ln.set_visible(vis)
        # 标注跟着曲线一起开关：只剩一条线旁边挂三个名字会更乱
        for lab in getattr(self, "end_labels", []):
            if getattr(lab, "audiocheck_line", None) is ln:
                lab.set_visible(vis)
        self.draw_idle()


# --------------------------------------------------------------------- 批次概览
class OverviewCanvas(BaseCanvas):
    """整个队列的分布：截止点直方图 + 结论环形图。一眼看出「这批音源什么水平」。"""

    def __init__(self):
        super().__init__(6.2, 3.6)
        self.gs = GridSpec(1, 3, figure=self.fig, wspace=.28)
        self.axh = self.fig.add_subplot(self.gs[0, :2])
        self.axd = self.fig.add_subplot(self.gs[0, 2])
        self.rows = []

    def set_data(self, rows):
        self.rows = rows or []
        # 批次概览只在「批次概览」页可见，而 `refresh_table()` 每刷新一次就会喂一次数据
        # （分析时每个文件回来都刷）。不可见时只存数据、标脏，切过去再画。
        self.redraw_if_visible()

    def _has_data(self):
        return bool(self.rows)   # rows 空表示没有已分析文件

    def redraw(self):
        self._stale = False
        p = self.p
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        for ax in (self.axh, self.axd):
            ax.clear()
            ax.set_facecolor(p["plot_bg"])
        rows = [r for r in self.rows if r.get("cutoff_khz")]
        if not rows:
            self.axh.axis("off")
            self.axh.text(.5, .5, "分析完成后显示批次分布", ha="center", va="center",
                          color=p["text_faint"], transform=self.axh.transAxes, fontsize=p['fs_caption'])
            self.axd.axis("off")
            self.draw()
            return

        cuts = np.array([r["cutoff_khz"] for r in rows])
        levels = [r.get("level", "warn") for r in rows]
        bins = np.arange(0, max(48, cuts.max() + 2), 2.0)
        self._ax_style(self.axh, "真实截止 kHz", "文件数")
        self.axh.set_title("真实带宽分布", color=p["text_dim"], fontsize=p['fs_micro'], pad=8)
        for lv in ("ok", "warn", "bad"):
            sel = cuts[[i for i, lvl in enumerate(levels) if lvl == lv]]
            if len(sel):
                self.axh.hist(sel, bins=bins, color=p[lv], alpha=.85, zorder=3,
                              label="%s (%d)" % (LEVEL_CN[lv], len(sel)), rwidth=.92)
        for x, lab in ((22.05, "44.1k 上限"), (24.0, "48k 上限")):
            self.axh.axvline(x, color=p["text_faint"], lw=.9, ls=":", zorder=2)
            self.axh.text(x, self.axh.get_ylim()[1] * .97, " " + lab, color=p["text_faint"],
                          fontsize=p['fs_tick'], va="top")
        leg = self.axh.legend(fontsize=p['fs_tick'], framealpha=.9, facecolor=p["surface2"],
                              edgecolor=p["border_hi"], labelcolor=p["text"])
        leg.get_frame().set_linewidth(.8)

        cnt = {lv: sum(1 for lvl in levels if lvl == lv) for lv in ("ok", "warn", "bad")}
        order = [lv for lv in ("ok", "warn", "bad") if cnt[lv]]
        vals = [cnt[lv] for lv in order]
        cols = [p[lv] for lv in order]
        self.axd.set_facecolor(p["surface"])
        self.axd.set_title("结论构成", color=p["text_dim"], fontsize=p['fs_micro'], pad=8)
        wedges, _ = self.axd.pie(vals, colors=cols, startangle=90,
                                 wedgeprops=dict(width=.42, edgecolor=p["surface"], linewidth=2))
        tot = sum(vals)
        self.axd.text(0, .10, str(tot), ha="center", va="center", color=p["text"],
                      fontsize=p['fs_hero'], fontweight="700")
        self.axd.text(0, -.20, "个文件", ha="center", va="center", color=p["text_faint"], fontsize=p['fs_tick'])
        self.axd.text(0, -0.52, "   ".join("%s %d" % (LEVEL_CN[lv], cnt[lv]) for lv in order),
                      color=p["text_dim"], fontsize=p['fs_tick'], transform=self.axd.transAxes,
                      ha="center", va="top")
        for w in wedges:
            w.set_linewidth(2)
        self.draw()


# --------------------------------------------------------------------- 表格委托
class VerdictDelegate(QStyledItemDelegate):
    """判定列画成胶囊标签，并在行首画一条状态色竖条。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.p = None
        self.f_cjk = None          # 中文列用比例字体
        self.f_mono = None         # 数字列用等宽

    def apply_theme(self, p, theme=None):
        self.p = p
        if theme is not None:
            # 表格整体在 QSS 里设了等宽，但 Consolas 没有中文字形，
            # 文件名/判定两列会走 Qt 逐字形回退，落到哪个字体不可控。
            # 这里按列显式指定：中文列给比例字体，数字列给等宽。
            self.f_cjk = QFont(theme.font_family or "Microsoft YaHei")
            self.f_cjk.setPointSizeF(theme.type.body_sm)
            self.f_mono = T.mono_qfont(theme.type.body_sm)

    def _font_for(self, col: int, last: int):
        return self.f_cjk if col in (0, last) else (self.f_mono or self.f_cjk)

    def paint(self, painter, opt, index):
        p = self.p
        if p is None:
            super().paint(painter, opt, index)
            return
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        fnt = self._font_for(index.column(), index.model().columnCount() - 1)
        if fnt is not None:
            painter.setFont(fnt)
        r = opt.rect
        selected = bool(opt.state & QStyle.StateFlag.State_Selected)
        if selected:                                  # 自定义绘制需自己铺选中背景
            painter.fillRect(r, QColor(p["selection"]))

        level = index.data(Qt.UserRole + 1) or ""
        text = str(index.data(Qt.DisplayRole) or "")
        last = index.model().columnCount() - 1

        if index.column() == 0:                       # 行首状态竖条 + 文件名
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(p.get(level, p["border_hi"])))
            painter.drawRoundedRect(QRectF(r.left() + 6, r.center().y() - 7, 3, 14), 1.5, 1.5)
            rr = QRectF(r.left() + 15, r.top(), r.width() - 18, r.height())
            painter.setPen(QColor(p["text"] if level else p["text_faint"]))
            # 音频文件后缀高度统一，掐尾比掐中间更有用（保住曲名/音轨号）
            painter.drawText(rr, Qt.AlignVCenter | Qt.AlignLeft,
                             painter.fontMetrics().elidedText(text, Qt.ElideRight, int(rr.width())))
            painter.restore()
            return

        if index.column() == last:                    # 判定胶囊
            col = p.get(level, p["text_dim"])
            bg = p.get(level + "_bg", p["surface2"])
            bd = p.get(level + "_border", p["border_hi"])
            fm = painter.fontMetrics()
            w = fm.horizontalAdvance(text) + 22
            box = QRectF(r.center().x() - w / 2, r.center().y() - 10, w, 20)
            painter.setPen(QPen(QColor(bd), 1))
            painter.setBrush(QColor(bg))
            painter.drawRoundedRect(box, 10, 10)
            f = QFont(painter.font())
            f.setBold(True)
            f.setPointSizeF(max(7.0, f.pointSizeF() - .5))
            painter.setFont(f)
            painter.setPen(QColor(col))
            painter.drawText(box, Qt.AlignCenter, text)
            painter.restore()
            return

        painter.setPen(QColor(p["text_dim"]))
        align = Qt.AlignRight if index.column() in (2, 3, 4) else Qt.AlignLeft
        painter.drawText(r.adjusted(8, 0, -8, 0), Qt.AlignVCenter | align, text)
        painter.restore()

    def sizeHint(self, opt, index):
        s = super().sizeHint(opt, index)
        s.setHeight(max(s.height(), 30))
        return s
