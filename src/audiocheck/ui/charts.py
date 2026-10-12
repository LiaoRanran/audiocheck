#!/usr/bin/env python3
"""audiocheck 图表层：频谱 / 频谱图 / 频段能量 / 多文件对比 / 批次概览 + 表格委托。

约定：每个画布都实现 apply_theme(p) —— 换主题时只需重画，不用重建控件。
"""
import matplotlib
import numpy as np

matplotlib.use("QtAgg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg  # noqa: E402
from matplotlib.figure import Figure  # noqa: E402
from matplotlib.gridspec import GridSpec  # noqa: E402
from PySide6.QtCore import QRectF, Qt  # noqa: E402
from PySide6.QtGui import QColor, QFont, QPainter, QPen  # noqa: E402
from PySide6.QtWidgets import QStyle, QStyledItemDelegate  # noqa: E402

from ..config import BAND_CN, BANDS  # noqa: E402
from . import theme as T  # noqa: E402
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

# 对比视图的配色（刻意避开状态色，防止与「合格/不达标」混淆）
CYCLE = ["#3d7dff", "#8b5cf6", "#06b6d4", "#22c55e", "#eab308", "#ec4899",
         "#f97316", "#14b8a6", "#a855f7", "#0ea5e9"]


class BaseCanvas(FigureCanvasQTAgg):
    def __init__(self, w=6.0, h=3.4, dpi=100):
        self.fig = Figure(figsize=(w, h), dpi=dpi, layout="constrained")
        super().__init__(self.fig)
        self.setStyleSheet("background:transparent;")
        self.p = None

    def _has_data(self) -> bool:
        """当前画布是否已有可画的内容。子类按自己的数据字段覆写。

        构造期 MainWindow 会对 6 个画布各调一次 apply_theme，若每次都全量重建
        matplotlib 图元，实测白付约 470ms，而且画出来的是随即被丢弃的空图
        （数据要等用户选文件才有）。没有数据时换肤只存色板即可。
        """
        return False

    def apply_theme(self, p):
        self.p = p
        self.fig.set_facecolor(p["surface"])
        if self._has_data():
            self.redraw()
        else:
            # 没有数据也要让背景色立刻跟上，否则深/浅主题切换时会闪一下旧底色
            self.draw_idle()

    def redraw(self):
        pass

    # ---- 公共样式 ----
    def _ax_style(self, ax, xlabel=None, ylabel=None):
        p = self.p
        ax.set_facecolor(p["plot_bg"])
        ax.grid(True, which="major", color=p["grid"], lw=.7, zorder=0)
        ax.grid(True, which="minor", color=p["grid"], lw=.35, alpha=.5, zorder=0)
        ax.tick_params(colors=p["text_faint"], labelsize=8, length=3)
        for s in ax.spines.values():
            s.set_color(p["border"])
            s.set_linewidth(.9)
        if xlabel:
            ax.set_xlabel(xlabel, color=p["text_faint"], fontsize=9, labelpad=4)
        if ylabel:
            ax.set_ylabel(ylabel, color=p["text_faint"], fontsize=9, labelpad=4)


# --------------------------------------------------------------------- 频谱曲线
class SpectrumCanvas(BaseCanvas):
    """真实截止点 + 频段刻度 + 悬停读数（本工具的核心证据图）。"""

    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.r = None
        self._hover = None
        self._static_ok = False        # 静态内容是否已按当前数据画好
        self._hline = self._hdot = self._hann = None   # 常驻悬停图元
        self.mpl_connect("motion_notify_event", self._on_move)
        self.mpl_connect("axes_leave_event", lambda e: self._set_hover(None))

    def _on_move(self, ev):
        if ev.inaxes is not self.ax or not self._has_data():
            return
        self._set_hover(ev.xdata)

    def set_data(self, r):
        if r is not self.r:
            # 数据换了，静态内容（曲线/频段线/截止标注）必须重画
            self._static_ok = False
        self.r = r
        self.redraw()

    def _has_data(self):
        return bool(self.r and self.r.get("curve"))

    def _set_hover(self, f):
        if f == self._hover:
            return
        self._hover = f
        if self._has_data():
            # **只重画悬停图元，不动静态内容**。早先每次鼠标移动都
            # ax.clear() 全量重建（曲线是几百个点 + fill_between + 6 条频段线
            # + 截止标注），单次约 30ms，鼠标划过时图表明显发涩。
            self._hover_layer()
            self.draw_idle()

    def redraw(self):
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        if not self._has_data():
            self._static_ok = False
            ax.clear()
            self._ax_style(ax, "频率 Hz", "相对电平 dB")
            ax.text(.5, .5, "选择左侧条目以查看频谱", ha="center", va="center",
                    color=p["text_faint"], transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        if not self._static_ok:
            self._static_layer()
            self._static_ok = True
        self._hover_layer()
        self.draw()

    def _static_layer(self):
        """曲线 + 频段分界 + 截止标注。**只有数据变化才重画。**"""
        p, ax = self.p, self.ax
        arr = np.asarray(self.r["curve"], dtype=float)
        f, db = arr[:, 0], arr[:, 1]
        nyq = (self.r.get("sr") or 48000) / 2
        top = float(np.percentile(db, 99.8))
        lo = max(top - 95, float(db.min()) - 3)
        ax.clear()
        self._ax_style(ax, "频率 Hz", "相对电平 dB")

        # ---- 频段分界（与推荐引擎 band_def 一致）----
        # 原来用 grid 色画，与背景几乎同色 —— 宣称的功能实际看不见。
        # 改用 border_hi 并在顶部标出边界值，让"分了几段、界在哪"可直接读。
        for edge in (60, 250, 500, 2000, 6000, 16000):
            if edge < nyq:
                ax.axvline(edge, color=p["border_hi"], lw=.9, zorder=1)
                ax.text(edge, top + (top - lo) * .012,
                        ("%gk" % (edge / 1000) if edge >= 1000 else "%g" % edge),
                        color=p["text_faint"], fontsize=7, ha="center", va="bottom",
                        clip_on=False)

        ax.axvline(nyq, color=p["text_faint"], lw=1.0, ls=":", zorder=2)
        ax.text(nyq, lo + (top - lo) * .02, " 奈奎斯特 %.1fk" % (nyq / 1000),
                color=p["text_faint"], fontsize=7.5, va="bottom", ha="right", rotation=90)

        # 填充 .16→.10：原来低频段糊成一块，把曲线本身盖住了
        ax.fill_between(f, db, lo, color=p["accent"], alpha=.10, lw=0, zorder=2)
        ax.semilogx(f, db, lw=1.15, color=p["accent"], zorder=3)

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
                        color=col, fontsize=9, fontweight="bold", va="top",
                        bbox=dict(boxstyle="round,pad=0.32", fc=p["surface2"], ec=col, lw=.9))
        # ---- x 轴刻度：**这张图的全部意义是「内容停在哪」** ----
        # 默认对数轴只标注 10²/10³/10⁴ 三个点，关键区（10k–22k）一个刻度都没有，
        # 读者无法判断截止点是 18k 还是 21k —— 只能信标注框里那个数字，
        # 而"读数能自证"正是这张图该提供的东西。
        #
        # **位置必须在这里、不能提前**：semilogx() 内部会 set_xscale('log')，
        # 顺手重置定位器与格式器 —— 早先把刻度配置写在绘图之前，全部被冲掉，
        # 底轴照样只显示 10²/10³/10⁴（一跑图就看出来了）。
        ticks = [t for t in (20, 50, 100, 200, 500, 1000, 2000, 5000, 10000, 20000)
                 if t <= nyq]
        ax.set_xticks(ticks)
        ax.set_xticklabels([("%gk" % (t / 1000) if t >= 1000 else "%g" % t) for t in ticks])
        ax.minorticks_off()                 # 关掉默认密集次刻度，避免与上面打架
        ax.set_xlim(20, nyq)
        # 顶部多留 14% 给频段标签，否则标签与截止标注挤在一起
        ax.set_ylim(lo, top + (top - lo) * 0.14)

    def _hover_layer(self):
        """竖线 + 读数点 + 数值气泡。鼠标移动只走这一层。"""
        p, ax = self.p, self.ax
        arr = np.asarray(self.r["curve"], dtype=float)
        f = arr[:, 0]
        nyq = (self.r.get("sr") or 48000) / 2
        # 只删自己加的三个图元，不碰静态内容
        for art in (self._hline, self._hdot, self._hann):
            if art is not None:
                try:
                    art.remove()
                except (ValueError, NotImplementedError):
                    pass
        self._hline = self._hdot = self._hann = None
        if not (self._hover and 20 <= self._hover <= nyq):
            return
        i = int(np.argmin(np.abs(f - self._hover)))
        self._hline = ax.axvline(f[i], color=p["text_dim"], lw=.8, zorder=5)
        self._hdot, = ax.plot([f[i]], [arr[i, 1]], "o", ms=4, color=p["text"], zorder=6)
        self._hann = ax.annotate("%.0f Hz    %.1f dB" % (f[i], arr[i, 1]),
                                 xy=(f[i], arr[i, 1]), xytext=(9, 9),
                                 textcoords="offset points", fontsize=8.5,
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

    def set_data(self, r=None, data=None, msg=None):
        self.r, self.data = r, data
        if msg:
            self._msg = msg
        self.redraw()

    def _has_data(self):
        return self.data is not None   # data=None 表示还没生成

    def redraw(self):
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        ax.clear()
        self._ax_style(ax, "时间 s", "频率 Hz")
        if not self.data:
            if self._cbar is not None:
                self._cbar.ax.set_visible(False)   # 空数据不该留着一条没有含义的色条
            ax.text(.5, .5, self._msg, ha="center", va="center", color=p["text_faint"],
                    transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        t, f, S = self.data
        db = S - np.nanmax(S)
        mesh = ax.pcolormesh(t, f, np.clip(db, -90, 0), shading="auto", cmap="magma",
                             vmin=-90, vmax=0, rasterized=True)
        ax.set_yscale("log")
        ax.set_ylim(20, f[-1])
        ax.grid(False)
        for lev in (100, 1000, 10000):
            if lev < f[-1]:
                ax.axhline(lev, color=p["grid"], lw=.6, alpha=.55, zorder=2)
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
        cb.ax.tick_params(colors=p["text_faint"], labelsize=7.5)
        cb.outline.set_edgecolor(p["border"])
        cb.set_label("dB", color=p["text_faint"], fontsize=8)


# --------------------------------------------------------------------- 频段能量
class BandCanvas(BaseCanvas):
    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.r = None

    def set_data(self, r):
        self.r = r
        self.redraw()

    def _has_data(self):
        return bool(self.r)   # r 为 None 表示未选中

    def redraw(self):
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
                    color=p["text_faint"], transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        names = [BAND_CN[k] for k, _, _ in BANDS]
        keys = [k for k, _, _ in BANDS]
        vals = [b.get(k, 0) for k in keys]
        cmap = plt.get_cmap("viridis")
        colors = [cmap(0.10 + 0.80 * i / (len(keys) - 1)) for i in range(len(keys))]
        bars = ax.bar(range(len(keys)), vals, color=colors, width=.68, zorder=3)
        for rect, v in zip(bars, vals):
            ax.text(rect.get_x() + rect.get_width() / 2, v + max(vals + [1]) * .025,
                    "%.0f%%" % v, ha="center", color=p["text"], fontsize=9.5, fontweight="bold")
        for i, (_k, lo, hi) in enumerate(BANDS):
            ax.text(i, -max(vals + [1]) * .085, "%s–%s" % (
                lo if lo < 1000 else "%dk" % (lo // 1000),
                hi if hi < 1000 else "%dk" % (hi // 1000)),
                ha="center", color=p["text_faint"], fontsize=7.5)
        ax.set_xticks(range(len(keys)))
        ax.set_xticklabels(names, color=p["text_dim"], fontsize=9.5)
        ax.set_ylim(0, max(vals + [1]) * 1.20)
        ax.tick_params(axis="x", pad=14)
        self.draw()


# --------------------------------------------------------------------- 3D 瀑布
class WaterfallCanvas(BaseCanvas):
    """3D 瀑布频谱：把时间轴立起来，低频堆积与高频骤停一目了然。"""

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
        self._drag = None
        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_drag)

    def set_data(self, r=None, data=None, msg=None):
        self.r, self.data = r, data
        if msg:
            self._msg = msg
        self.redraw()

    def _on_press(self, ev):
        if ev.inaxes is self.ax and ev.button == 1:
            self._drag = (ev.x, ev.y, self._elev, self._azim)

    def _on_drag(self, ev):
        if not self._drag or ev.x is None:
            return
        x0, y0, e0, a0 = self._drag
        self._elev = max(4, min(88, e0 + (ev.y - y0) * 0.35))
        self._azim = a0 - (ev.x - x0) * 0.35
        self.redraw()

    def _release(self, ev):
        self._drag = None

    def mouseReleaseEvent(self, ev):
        self._drag = None
        super().mouseReleaseEvent(ev)

    def _has_data(self):
        return self.data is not None   # data=None 表示还没生成

    def redraw(self):
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
                      color=p["text_faint"], fontsize=11)
            self.draw()
            return
        t, f, S = self.data
        m = (f >= 20)
        lf = np.log10(np.maximum(f[m], 20))
        db = S[m] - np.nanmax(S)
        n = db.shape[1]
        cmap = plt.get_cmap("viridis")
        # 只画有限条谱线，太多会糊成一团
        step = max(1, n // 30)
        idxs = list(range(0, n, step))
        for i in idxs:
            col = cmap(0.06 + 0.88 * i / max(1, n - 1))
            ax.plot(lf, db[:, i], zs=i, zdir="y", color=col, lw=1.25, alpha=.98)
        ax.set_zlim(-90, 0)
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
                           fontsize=8)
        ax.set_yticks([0, n // 2, n])
        ax.set_yticklabels(["0", "%.0fs" % (t[len(t) // 2] if len(t) else 0),
                            "%.0fs" % (t[-1] if len(t) else 0)], fontsize=7.5)
        ax.set_zticks([-80, -60, -40, -20, 0])
        ax.set_zticklabels(["-80", "-60", "-40", "-20", "0"], fontsize=7.5)
        ax.set_xlabel("Hz", color=p["text_dim"], fontsize=8.5, labelpad=-2)
        ax.set_ylabel("时间", color=p["text_dim"], fontsize=8.5, labelpad=-2)
        ax.set_zlabel("dB", color=p["text_dim"], fontsize=8.5, labelpad=-4)
        ax.tick_params(colors=p["text_faint"], pad=-2)
        for pane in (ax.xaxis, ax.yaxis, ax.zaxis):
            pane.set_pane_color((0, 0, 0, 0))
            pane.line.set_color(p["border"])
        ax.grid(True, color=p["grid"], lw=.5, alpha=.55)
        cut = (self.r or {}).get("cutoff_khz")
        ax.text2D(.015, .95, ("真实截止 %.2f kHz" % cut) if cut else "", transform=ax.transAxes,
                  color=p["text_dim"], fontsize=8.5)
        ax.text2D(.86, .95, "拖拽旋转", transform=ax.transAxes, color=p["text_faint"], fontsize=8)
        self.draw()


# --------------------------------------------------------------------- 多文件对比
class CompareCanvas(BaseCanvas):
    """把多个文件的频谱叠在一张图上——判断「同专辑/同批源」的最快方式。"""

    def __init__(self):
        super().__init__(6.2, 3.6)
        self.ax = self.fig.add_subplot(111)
        self.items = []

    def set_data(self, items):
        self.items = items or []
        self.redraw()

    def _has_data(self):
        return bool(self.items)   # items 空表示没有多选

    def redraw(self):
        p, ax = self.p, self.ax
        if p is None:
            return
        self.fig.set_facecolor(p["surface"])
        ax.clear()
        self._ax_style(ax, "频率 Hz", "相对电平 dB")
        if not self.items:
            ax.text(.5, .5, "在左侧按住 Ctrl 选中多个条目即可叠加对比",
                    ha="center", va="center", color=p["text_faint"],
                    transform=ax.transAxes, fontsize=11)
            ax.set_xticks([])
            ax.set_yticks([])
            self.draw()
            return
        nyq = 48000
        for i, (r, cur) in enumerate(self.items[:10]):
            arr = np.asarray(cur, dtype=float)
            col = CYCLE[i % len(CYCLE)]
            nyq = max(nyq, r.get("sr", 48000) / 2)
            top = float(np.percentile(arr[:, 1], 99.5))
            ax.semilogx(arr[:, 0], arr[:, 1] - top, lw=1.15, color=col, alpha=.95,
                        label="%s  ｜ %.1fk" % (r["file"][:30], (r.get("cutoff_khz") or 0)),
                        zorder=3)
            cut = r.get("cutoff_hz")
            if cut:
                ax.axvline(cut, color=col, lw=1.0, ls="--", alpha=.55, zorder=2)
        ax.axhline(0, color=p["text_faint"], lw=.7, ls=":", zorder=1)
        ax.set_xlim(20, nyq)
        ax.set_ylim(-80, 8)
        leg = ax.legend(loc="lower left", fontsize=7.8, framealpha=.92, ncol=1,
                        facecolor=p["surface2"], edgecolor=p["border_hi"], labelcolor=p["text"])
        leg.get_frame().set_linewidth(.8)
        self.draw()


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
        self.redraw()

    def _has_data(self):
        return bool(self.rows)   # rows 空表示没有已分析文件

    def redraw(self):
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
                          color=p["text_faint"], transform=self.axh.transAxes, fontsize=11)
            self.axd.axis("off")
            self.draw()
            return

        cuts = np.array([r["cutoff_khz"] for r in rows])
        levels = [r.get("level", "warn") for r in rows]
        bins = np.arange(0, max(48, cuts.max() + 2), 2.0)
        self._ax_style(self.axh, "真实截止 kHz", "文件数")
        self.axh.set_title("真实带宽分布", color=p["text_dim"], fontsize=10, pad=8)
        for lv in ("ok", "warn", "bad"):
            sel = cuts[[i for i, lvl in enumerate(levels) if lvl == lv]]
            if len(sel):
                self.axh.hist(sel, bins=bins, color=p[lv], alpha=.85, zorder=3,
                              label="%s (%d)" % (LEVEL_CN[lv], len(sel)), rwidth=.92)
        for x, lab in ((22.05, "44.1k 上限"), (24.0, "48k 上限")):
            self.axh.axvline(x, color=p["text_faint"], lw=.9, ls=":", zorder=2)
            self.axh.text(x, self.axh.get_ylim()[1] * .97, " " + lab, color=p["text_faint"],
                          fontsize=7.5, va="top")
        leg = self.axh.legend(fontsize=8, framealpha=.9, facecolor=p["surface2"],
                              edgecolor=p["border_hi"], labelcolor=p["text"])
        leg.get_frame().set_linewidth(.8)

        cnt = {lv: sum(1 for lvl in levels if lvl == lv) for lv in ("ok", "warn", "bad")}
        order = [lv for lv in ("ok", "warn", "bad") if cnt[lv]]
        vals = [cnt[lv] for lv in order]
        cols = [p[lv] for lv in order]
        self.axd.set_facecolor(p["surface"])
        self.axd.set_title("结论构成", color=p["text_dim"], fontsize=10, pad=8)
        wedges, _ = self.axd.pie(vals, colors=cols, startangle=90,
                                 wedgeprops=dict(width=.42, edgecolor=p["surface"], linewidth=2))
        tot = sum(vals)
        self.axd.text(0, .10, str(tot), ha="center", va="center", color=p["text"],
                      fontsize=19, fontweight="700")
        self.axd.text(0, -.20, "个文件", ha="center", va="center", color=p["text_faint"], fontsize=9)
        self.axd.text(0, -0.52, "   ".join("%s %d" % (LEVEL_CN[lv], cnt[lv]) for lv in order),
                      color=p["text_dim"], fontsize=8.5, transform=self.axd.transAxes,
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
