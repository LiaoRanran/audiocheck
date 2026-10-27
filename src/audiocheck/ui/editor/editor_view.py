"""编辑视图画布：频谱图 + 波形叠加 + 播放头 + 矩形时间×频率选区。

设计（往极致做，但不重造轮子）：
- 频谱图复用 `core.views.spectrogram`（双分辨率、功率合并、峰值保持——与分析侧同一张图）；
- 频率轴**对数**（音频界常识），刻度按当前视野重挑（复用 `ui/zoom.pick_ticks`），
  放大到 100–200 Hz 也保证有 ≥3 个刻度（"读数能自证"）；
- 缩放/平移走 `ui/zoom.py` 的**纯函数**（锚点 / 钳制 / 自适应刻度），与既有图表同一套数学；
- 波形用**min/max 包络**（每显示列取该列样本极值），放大不糊、不假插值；
- 矩形选区：选区工具开启时拖拽生成 `[t0,t1]×[f0,f1]`，坐标夹紧到数据范围；
- 套索选区：自由拖拽多边形（RDP 简化后发 `polySelected`）——**顶点是连续 (t,f) 数据
  坐标，与显示网格无关**；渲染端掩膜的粒度 = 编辑 STFT 网格（n_fft=4096），显示端
  频谱图用 nperseg=16384（`views.DEFAULT_VIEW_NPERSEG`）——"看到的格比刷到的细"是
  **有意**的（显示要平滑统计、编辑要局部性，见 `docs/theory.md §12.1`）；套索顶点
  不落在格点上 ⇒ 看/刷口径在多边形语义下天然解耦，无需对齐；
- 播放头：一条竖线，由 `PlaybackEngine` 的 `on_position` 驱动（blit 快路更新）。

信号：
- selectionChanged(t0, t1, f0, f1)
- viewChanged()  （缩放/平移后，供其他面板同步）
"""

from __future__ import annotations

import numpy as np
import soundfile as sf
from PySide6.QtCore import Qt, Signal

from ...core import views
from .. import theme as T
from ..charts import BaseCanvas
from ..zoom import clamp_span, pick_ticks, zoom_step

F_MIN, F_MAX = 20.0, 22000.0  # 频率轴默认范围（显示用，数据可超出则扩展）

#: 套索顶点 RDP 简化的容差（占视野时间跨度的比例）——顶点表要**人可读**（可审计：
#: 记录即选区），不是逐采样的鼠标轨迹。
_LASSO_EPS_FRAC = 0.01


def _rdp_simplify(points: list[tuple[float, float]], eps: float) -> list[tuple[float, float]]:
    """Douglas–Peucker（迭代实现，确定性）：把套索轨迹简化成少量**有意义的顶点**。

    自由拖拽的原始轨迹是几百个采点；顶点表进 `PolyGainOp.points`（审计记录）——
    500 个噪声顶点没法人工核对，简化后的一二十个可以。eps 取视野跨度的 1%。
    """
    if len(points) < 3:
        return list(points)
    pts = list(points)

    def perp_dist(p, a, b) -> float:
        ax, ay = a
        bx, by = b
        dx, dy = bx - ax, by - ay
        seg = dx * dx + dy * dy
        if seg <= 0.0:
            return float(np.hypot(p[0] - ax, p[1] - ay))
        t = ((p[0] - ax) * dx + (p[1] - ay) * dy) / seg
        t = min(max(t, 0.0), 1.0)
        return float(np.hypot(p[0] - (ax + t * dx), p[1] - (ay + t * dy)))

    keep = [False] * len(pts)
    keep[0] = keep[-1] = True
    stack = [(0, len(pts) - 1)]
    while stack:
        lo, hi = stack.pop()
        if hi <= lo + 1:
            continue
        dmax, imax = -1.0, -1
        for i in range(lo + 1, hi):
            d = perp_dist(pts[i], pts[lo], pts[hi])
            if d > dmax:
                dmax, imax = d, i
        if dmax > eps and imax > 0:
            keep[imax] = True
            stack.append((lo, imax))
            stack.append((imax, hi))
    return [p for p, k in zip(pts, keep) if k]


class EditorView(BaseCanvas):
    """频谱 + 波形 + 选区 + 播放头的编辑画布。"""

    selectionChanged = Signal(float, float, float, float)  # t0,t1,f0,f1
    polySelected = Signal(list)  # [(t, f), ...] 套索顶点（RDP 简化后，隐式闭合）
    viewChanged = Signal()
    seekRequested = Signal(float)  # 点击时间轴定位播放头（秒）

    def __init__(self, theme: T.Theme):
        super().__init__(w=8.0, h=4.2, dpi=100)
        self._theme = theme
        self._t: np.ndarray | None = None
        self._f: np.ndarray | None = None
        self._S: np.ndarray | None = None
        self._wave: np.ndarray | None = None
        self._fs: int = 44100
        self._dur: float = 0.0

        # 视野（时间 / 频率），初始 = 全曲
        self._t0, self._t1 = 0.0, 1.0
        self._f0v, self._f1v = F_MIN, F_MAX

        # 选区
        self._sel: tuple[float, float, float, float] | None = None
        self._selecting = False
        self._sel_anchor: tuple[float, float] | None = None

        # 播放头
        self._play_pos: float = 0.0
        self._play_line = None
        self._sel_rect = None
        self._measure_txt = None

        # 交互模式
        self._select_tool = False
        self._lasso_tool = False
        self._lasso_pts: list[tuple[float, float]] = []
        self._lasso_line = None
        self._lasso_txt = None
        self._drag_pan = False
        self._pan_anchor = None
        self._press_pos = None

        self._build_axes()

    # ---- 数据 ----
    def set_source(self, path: str, fs: int, nperseg: int = views.DEFAULT_VIEW_NPERSEG) -> None:
        """载入源：算频谱图 + 波形包络。"""
        t, f, Sdb = views.spectrogram(path, nperseg=nperseg)
        x, _ = sf.read(path, dtype="float32", always_2d=True)
        self._t, self._f, self._S = t, f, Sdb
        self._fs = fs
        self._dur = float(len(x)) / fs
        # 波形包络（min/max），按 ~显示列数（取 2000 列足够）
        ncol = 2000
        n = x.shape[0]
        idx = np.linspace(0, n, ncol + 1).astype(int)
        env = np.empty((ncol, 2), dtype=np.float32)
        for i in range(ncol):
            seg = x[idx[i]:idx[i + 1]]
            if seg.size:
                env[i] = (seg.min(axis=0).min(), seg.max(axis=0).max())
            else:
                env[i] = (0.0, 0.0)
        self._wave = env
        self._t0, self._t1 = 0.0, self._dur
        self._f0v, self._f1v = F_MIN, min(F_MAX, float(f.max()) * 1.05)
        self._sel = None
        self._stale = True
        self._redraw_all()

    def _build_axes(self) -> None:
        from matplotlib.axes import Axes
        self.ax: Axes = self.fig.add_axes([0.06, 0.08, 0.90, 0.84])
        self.ax.set_yscale("log")
        self.ax.set_ylim(self._f0v, self._f1v)
        self.ax.set_xlim(self._t0, self._t1)
        self.ax.set_xlabel("时间 (s)")
        self.ax.set_ylabel("频率 (Hz)")

    def _has_data(self) -> bool:
        return self._S is not None

    # ---- 绘制 ----
    def _redraw_view(self) -> None:
        """缩放/平移的 **ROI 快速路径**：切片数据 + `set_data/set_extent`，不 ax.clear()。

        Agent E 调研的第一步（`docs/editor_research.md`）：整幅重绘 518 ms 的主要成本是
        全曲 imshow 重栅格化 + 全 artist 重建；ROI 路径只栅格化可视窗口。所有叠加
        artist（选区/套索/播放头）跨重绘存活，无需重建。
        """
        if not self._has_data() or self._img is None:
            return
        i0 = int(np.searchsorted(self._t, self._t0))
        i1 = int(np.searchsorted(self._t, self._t1))
        j0 = int(np.searchsorted(self._f, self._f0v))
        j1 = int(np.searchsorted(self._f, self._f1v))
        S = self._S[max(0, j0):max(j1, j0 + 1), max(0, i0):max(i1, i0 + 1)]
        if S.size == 0:
            return
        self._img.set_data(S)
        flo = max(float(self._f[max(0, j0)]), 1.0)
        fhi = float(self._f[min(j1, len(self._f) - 1)])
        self._img.set_extent([float(self._t[max(0, i0)]),
                              float(self._t[min(i1, len(self._t) - 1)]), flo, fhi])
        self._draw_waveform()
        self._apply_view_limits()
        self._draw_selection()
        self._draw_lasso()
        self._draw_playhead()
        self.draw_idle()

    def _redraw_all(self) -> None:
        if not self._has_data():
            return
        self.ax.clear()
        self.ax.set_yscale("log")
        # 频谱图（imshow，对数频率轴用 pcolormesh 等价：用 imshow + extent）
        S = self._S
        vmin = float(np.nanmin(S))
        vmax = float(np.nanmax(S))
        # 频率轴是 log 刻度，0 Hz 显示不了——把 imshow 下沿抬到 1 Hz，
        # 避免 matplotlib 在 log 轴上设非正 ylim 的告警（子 1 Hz 的真实内容
        # 本就被 `_f0v=20` 的显示范围裁掉，这里只影响色图绘制下沿）。
        fb = max(float(self._f[0]), 1.0)
        self._img = self.ax.imshow(
            S, origin="lower", aspect="auto",
            extent=[self._t[0], self._t[-1], fb, float(self._f[-1])],
            cmap="inferno", vmin=vmin, vmax=vmax,
        )
        # 波形包络（叠加在底部线性坐标不合适，改用右轴归一化叠加）
        self._draw_waveform()
        self._apply_view_limits()
        self._draw_selection()
        self._draw_lasso()          # zoom/pan 重绘不吞掉进行中的套索叠加
        self._draw_playhead()
        self.draw_idle()

    def _draw_waveform(self) -> None:
        if self._wave is None:
            return
        # 波形包络（min/max）以半透明填充带画在频率轴底部的"电平条"，
        # 不干扰频谱判读；两根极值线给出真正的包络而非单根峰值线。
        # ⚠ 列按**真时间**对齐（全曲 2000 列 ⇒ 视野内的列才画）——此前把全曲包络
        # linspace 压进视野，缩放时波形时间轴错位（实测抓到并修复）。
        n = self._wave.shape[0]
        env_t = np.linspace(0.0, self._dur, n)
        lo_m = (env_t >= self._t0) & (env_t <= self._t1)
        if not lo_m.any():
            return
        tn = env_t[lo_m]
        lo = self._wave[lo_m, 0]
        hi = self._wave[lo_m, 1]
        fhi = self._f0v * 1.6
        ylo = np.clip(np.abs(lo), 0, 1.0) * (fhi - self._f0v) + self._f0v
        yhi = np.clip(np.abs(hi), 0, 1.0) * (fhi - self._f0v) + self._f0v
        self.ax.fill_between(tn, ylo, yhi, color="white", alpha=0.12,
                             lw=0, zorder=3)
        self.ax.plot(tn, yhi, color="white", alpha=0.22, lw=0.5, zorder=3)
        self.ax.plot(tn, ylo, color="white", alpha=0.14, lw=0.5, zorder=3)

    def _apply_view_limits(self) -> None:
        self.ax.set_xlim(self._t0, self._t1)
        self.ax.set_ylim(self._f0v, self._f1v)
        # 频率刻度按视野重挑（对数）
        self.ax.set_yticks(pick_ticks(self._f0v, self._f1v, log=True))
        self.ax.set_xticks(self._time_ticks())

    def _time_ticks(self):
        # 时间轴线性 1/2/5 步长
        from ..zoom import pick_ticks as pt
        return pt(self._t0, self._t1, log=False)

    def _draw_selection(self) -> None:
        if self._sel_rect is not None:
            try:
                self._sel_rect.remove()
            except Exception:
                pass
            self._sel_rect = None
        if self._sel is None:
            return
        t0, t1, f0, f1 = self._sel
        from matplotlib.patches import Rectangle
        rect = Rectangle((t0, f0), t1 - t0, f1 - f0,
                         fill=True, facecolor="#39d0ff", alpha=0.18,
                         edgecolor="#39d0ff", lw=1.2, zorder=5)
        self._sel_rect = self.ax.add_patch(rect)

    def _draw_measure(self) -> None:
        """拖动选区时实时显示 t/f 窗与跨度的**实测读数**（可审计：不靠肉眼估）。"""
        if self._measure_txt is not None:
            try:
                self._measure_txt.remove()
            except Exception:
                pass
            self._measure_txt = None
        if self._sel is None:
            return
        t0, t1, f0, f1 = self._sel
        txt = ("Δt %.3f–%.3f s (Δ=%.3f)\n"
               "Δf %.0f–%.0f Hz (×%.2f)" % (t0, t1, t1 - t0, f0, f1, f1 / max(f0, 1e-9)))
        self._measure_txt = self.ax.text(
            t0, f1 * 1.05, txt, color="#39d0ff", fontsize=8,
            va="bottom", ha="left", zorder=7,
            bbox=dict(boxstyle="round,pad=0.2", fc="#0d0e13", ec="#39d0ff", alpha=0.75))

    def _draw_playhead(self) -> None:
        if self._play_line is not None:
            try:
                self._play_line.remove()
            except Exception:
                pass
        if not (0 <= self._play_pos <= self._dur):
            self._play_line = None
            return
        self._play_line = self.ax.axvline(self._play_pos, color="#ffd23f", lw=1.0, zorder=6)

    # ---- 外部驱动 ----
    def set_playhead(self, pos: float) -> None:
        self._play_pos = pos
        if self._has_data():
            self._draw_playhead()
            self.draw_idle()

    def set_selection_tool(self, on: bool) -> None:
        self._select_tool = on
        if on:
            self._lasso_tool = False
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    def set_lasso_tool(self, on: bool) -> None:
        """套索工具：按住拖一圈，松手闭合为多边形选区（顶点经 RDP 简化后上报）。"""
        self._lasso_tool = on
        if on:
            self._select_tool = False
            self._sel = None
            self._draw_selection()
        self.setCursor(Qt.CursorShape.CrossCursor if on else Qt.CursorShape.ArrowCursor)

    # ---- 套索（自由多边形选区）----
    def _lasso_append(self, t: float, f: float) -> None:
        """追加套索点：与上一 recorded 点的距离 < 视野对角的 0.4% 时丢弃（采点去密）。

        空列表时自动播种（调用方无需区分"起点/后续"）。"""
        if not self._lasso_pts:
            self._lasso_pts = [(t, f)]
            self._draw_lasso()
            return
        lt, lf = self._lasso_pts[-1]
        dt = (self._t1 - self._t0) * 0.004
        df = (self._f1v - self._f0v) * 0.004
        if abs(t - lt) < dt and abs(f - lf) < df:
            return
        self._lasso_pts.append((t, f))
        self._draw_lasso()
        self.draw_idle()

    def _lasso_finish(self) -> None:
        pts = _rdp_simplify(self._lasso_pts, eps=_LASSO_EPS_FRAC *
                            max(self._t1 - self._t0, 1e-9))
        self._lasso_pts = []
        self._clear_lasso_line()
        if len(pts) >= 3:
            self.polySelected.emit(pts)
        self.draw_idle()

    def _lasso_cancel(self) -> None:
        self._lasso_pts = []
        self._clear_lasso_line()
        self.draw_idle()

    def _clear_lasso_line(self) -> None:
        if self._lasso_line is not None:
            try:
                self._lasso_line.remove()
            except Exception:
                pass
            self._lasso_line = None
        if self._lasso_txt is not None:
            try:
                self._lasso_txt.remove()
            except Exception:
                pass
            self._lasso_txt = None

    def _draw_lasso(self) -> None:
        self._clear_lasso_line()
        if not self._lasso_pts:
            return
        ts = [t for t, _ in self._lasso_pts]
        fs_ = [f for _, f in self._lasso_pts]
        self._lasso_line, = self.ax.plot(
            ts, fs_, color="#39d0ff", lw=1.2, marker=".", ms=3, zorder=6, label="_lasso")
        # 进行中读数（可审计：顶点数与包围盒实测，不靠肉眼估）
        txt = "套索 %d 点\nΔt %.3f–%.3f s\nΔf %.0f–%.0f Hz" % (
            len(self._lasso_pts), min(ts), max(ts), min(fs_), max(fs_))
        self._lasso_txt = self.ax.text(
            min(ts), max(fs_) * 1.05, txt, color="#39d0ff", fontsize=8,
            va="bottom", ha="left", zorder=7,
            bbox=dict(boxstyle="round,pad=0.2", fc="#0d0e13",
                      ec="#39d0ff", alpha=0.75))

    def seekTo(self, t: float) -> None:
        """点击时间轴定位播放头：把秒数回给上层（由引擎 clamp 到 [0,dur]）。"""
        if not self._has_data():
            return
        self.seekRequested.emit(max(0.0, min(float(t), self._dur)))

    def get_selection(self):
        return self._sel

    # ---- 坐标换算 ----
    def _data_coords(self, event) -> tuple[float, float]:
        # 把鼠标像素位置换成 (时间, 频率)
        x, y = event.x(), event.y()
        try:
            inv = self.ax.transData.inverted()
            fx, fy = inv.transform((x, self.height() - y))
            return float(fx), float(fy)
        except Exception:
            return 0.0, 1000.0

    # ---- 交互 ----
    def wheelEvent(self, ev):  # noqa: N802
        if not self._has_data():
            return
        if ev.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return
        # 以鼠标处为锚点缩放频率（纵向滚轮）或时间（横向）——这里按 Shift 切换轴
        pos_t, pos_f = self._data_coords(ev)
        step = 1 if ev.angleDelta().y() > 0 else -1
        if ev.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            self._f0v, self._f1v = zoom_step(self._f0v, self._f1v, F_MIN, F_MAX,
                                            step, max(pos_f, 1.0), log=True)
        else:
            self._t0, self._t1 = zoom_step(self._t0, self._t1, 0.0, self._dur,
                                          step, pos_t, log=False)
        self._apply_view_limits()
        self._redraw_view()
        self.viewChanged.emit()

    def mousePressEvent(self, ev):  # noqa: N802
        if not self._has_data():
            return
        self._press_pos = (ev.x(), ev.y())
        t, f = self._data_coords(ev)
        if self._lasso_tool:
            self._lasso_pts = [(t, f)]
            self._draw_lasso()
        elif self._select_tool:
            self._selecting = True
            self._sel_anchor = (t, f)
        else:
            self._drag_pan = True
            self._pan_anchor = (ev.x(), ev.y(), self._t0, self._t1, self._f0v, self._f1v)

    def mouseMoveEvent(self, ev):  # noqa: N802
        if not self._has_data():
            return
        t, f = self._data_coords(ev)
        if self._lasso_tool and self._lasso_pts:
            self._lasso_append(t, f)
        elif self._selecting and self._sel_anchor is not None:
            a0, a1 = self._sel_anchor
            self._sel = (min(a0, t), max(a0, t),
                         min(a1, f), max(a1, f))
            self._draw_selection()
            self._draw_measure()
            self.draw_idle()
        elif self._drag_pan and self._pan_anchor is not None:
            x0, y0, t0, t1, f0, f1 = self._pan_anchor
            dx = (ev.x() - x0) / max(self.width(), 1)
            dy = (ev.y() - y0) / max(self.height(), 1)
            dt = (t1 - t0) * dx
            dfr = (f1 / f0) ** dy
            self._t0, self._t1 = clamp_span(t0 - dt, t1 - dt, 0.0, self._dur, log=False)
            self._f0v, self._f1v = clamp_span(f0 / dfr, f1 / dfr, F_MIN, F_MAX, log=True)
            self._apply_view_limits()
            self._redraw_view()

    def mouseReleaseEvent(self, ev):  # noqa: N802
        # 区分"点击"与"拖拽"：位移极小视为点击；非选区工具下点击 = 定位播放头。
        moved = self._press_pos is not None and (
            abs(ev.x() - self._press_pos[0]) > 3 or abs(ev.y() - self._press_pos[1]) > 3)
        if self._lasso_tool and self._lasso_pts:
            self._lasso_finish()
        elif self._selecting and self._sel is not None:
            self._selecting = False
            self._sel_anchor = None
            t0, t1, f0, f1 = self._sel
            # 夹紧 + 最小尺寸
            if t1 - t0 > 1e-3 and f1 - f0 > 1.0:
                self.selectionChanged.emit(t0, t1, f0, f1)
        elif not self._select_tool and not moved:
            t, _f = self._data_coords(ev)
            self.seekTo(t)
        self._selecting = False
        self._drag_pan = False
        self._pan_anchor = None
        self._press_pos = None

    def keyPressEvent(self, ev):  # noqa: N802
        if ev.key() == Qt.Key.Key_F:
            self.open_fullscreen()
            return
        if ev.key() == Qt.Key.Key_Escape and self._lasso_pts:
            self._lasso_cancel()
            return
        # 键盘选区：方向键微调 / Shift+方向扩展（验收 §"键盘可建/可调选区"）。
        # 步长取当前视野的 2%（可见即所调），不依赖绝对栅格。
        if self._has_data() and self._move_selection(ev):
            return
        super().keyPressEvent(ev)

    def _move_selection(self, ev) -> bool:
        sel = self._sel
        if sel is None:
            # 还没选区：方向键先建一个默认选区（全程中点一段）
            if ev.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right,
                             Qt.Key.Key_Up, Qt.Key.Key_Down):
                mid = self._dur / 2.0
                span = max(self._dur * 0.1, 1e-3)
                sel = (mid - span / 2, mid + span / 2, F_MIN * 2, F_MAX / 2)
                self._sel = sel
                self._emit_selection()
                self._draw_selection()
                self.draw_idle()
                return True
            return False
        t0, t1, f0, f1 = sel
        dt = max((self._t1 - self._t0) * 0.02, self._dur * 1e-4)
        shift = bool(ev.modifiers() & Qt.KeyboardModifier.ShiftModifier)
        k = ev.key()
        if k == Qt.Key.Key_Left:
            if shift:
                t1 = max(t0 + 1e-3, t1 - dt)
            else:
                t0, t1 = max(0.0, t0 - dt), max(t0 - dt + 1e-3, t1 - dt)
        elif k == Qt.Key.Key_Right:
            if shift:
                t1 = min(self._dur, t1 + dt)
            else:
                t0, t1 = min(self._dur - 1e-3, t0 + dt), min(self._dur, t1 + dt)
        elif k == Qt.Key.Key_Down:
            if shift:
                f1 = max(f0 + 1.0, f1 / 1.05)
            else:
                f0, f1 = max(F_MIN, f0 / 1.05), max(f0 / 1.05 + 1.0, f1 / 1.05)
        elif k == Qt.Key.Key_Up:
            if shift:
                f1 = min(F_MAX, f1 * 1.05)
            else:
                f0, f1 = min(F_MAX / 1.05, f0 * 1.05), min(F_MAX, f1 * 1.05)
        else:
            return False
        t0, t1 = min(t0, t1), max(t0, t1)
        f0, f1 = min(f0, f1), max(f0, f1)
        self._sel = (t0, t1, f0, f1)
        self._emit_selection()
        self._draw_selection()
        self.draw_idle()
        return True

    def _emit_selection(self) -> None:
        t0, t1, f0, f1 = self._sel  # type: ignore[union-attr]
        self.selectionChanged.emit(t0, t1, f0, f1)

    def zoom_in(self) -> None:
        if not self._has_data():
            return
        self._f0v, self._f1v = zoom_step(self._f0v, self._f1v, F_MIN, F_MAX, 1,
                                        (self._f0v * self._f1v) ** 0.5, log=True)
        self._apply_view_limits()
        self._redraw_view()
        self.viewChanged.emit()

    def zoom_out(self) -> None:
        if not self._has_data():
            return
        self._f0v, self._f1v = zoom_step(self._f0v, self._f1v, F_MIN, F_MAX, -1,
                                        (self._f0v * self._f1v) ** 0.5, log=True)
        self._apply_view_limits()
        self._redraw_view()
        self.viewChanged.emit()

    def zoom_reset(self) -> None:
        if not self._has_data():
            return
        self._t0, self._t1 = 0.0, self._dur
        self._f0v, self._f1v = F_MIN, min(F_MAX, float(self._f.max()) * 1.05)
        self._apply_view_limits()
        self._redraw_all()
        self.viewChanged.emit()

    def apply_theme(self, t: T.Theme) -> None:
        self._theme = t
        if self._has_data():
            self._redraw_all()
