#!/usr/bin/env python3
"""交互帧时间基准 —— 把「感觉卡不卡」换成毫秒数。

为什么需要它
------------
悬停优化前前后后改了三版，理由一直是估计值（"单次约 30 ms"）。
这个脚本把三版放在一起测，谁省了多少一目了然：

    P1 悬停·现行        blit 局部重绘（快照 + draw_artist + blit）
    P2 悬停·上轮        "静态层/悬停层分离" + 整图 draw()   ← 只省了建图元
    P3 悬停·最初        每次整图重建（_static_layer 全量）

跑法：

    python packaging/perf_probe.py                 # 默认 offscreen，30 次取样
    python packaging/perf_probe.py --repeat 60
    python packaging/perf_probe.py --window        # 真开窗口测（更接近实际观感）
    python packaging/perf_probe.py --strict        # 超阈值退出码 1

其余场景：

    P4 点选文件（全套：卡片 + 表格 + 曲线 + 频段）
    P5 切页（视图数据缓存命中）
    P6 表格刷新 200 行
    P7 换肤 apply_theme
    P8 一次拖拽手势（20 次移动 + 松手，含 2 帧 3D 重绘）
    P9 分析过程中每回来一个文件的开销（合并刷新后应为 0）

## 一轮优化前后的实测（本机，同一套素材与场景）

| 动作 | 优化前 | 优化后 | 改了什么 |
| --- | --- | --- | --- |
| 悬停·最初（整图重建） | 177 ms | — | 基线，留作对照 |
| 悬停·上轮（分层，但仍整图 draw） | 131 ms | — | 只省下建图元那部分，大头没动 |
| 悬停·现行（blit） | 131 ms | **15.7 ms** | 背景快照 + `draw_artist` + `blit`；blit 本体仅 0.15 ms，剩下的是 Qt 拷 3.5 MB 像素 |
| 切页（缓存命中） | 438 ms | **3.8 ms** | 同一份数据不重复重画（`_load_view` 切页时会重复喂） |
| 表格刷新 200 行 | 214 ms | **38 ms** | 批次概览画布不可见时不重画 |
| 分析中每文件回调 | 37–214 ms | **0.00 ms** | 刷新合并到 120 ms 一次 |
| 换肤 apply_theme | 1589 ms | **788 ms** | 只重画可见画布（6 张里 5 张看不见） |
| 一次拖拽手势 | ~7.3 s | **0.85 s** | 拖拽限流到 ~8 fps + 松手补一帧 |

## 读数前必须知道的环境事实（否则会误判代码）

本机实测：`sys._is_gil_enabled()` 为 **False**（自由线程 CPython 3.14），
且单核性能约为常规桌面的 1/3 —— 同样一个 1e6 次空循环，本机 185 ms，
同版本 GIL 构建 110 ms，常规桌面约 30 ms。裸 matplotlib 画一张 600 点的小图
复绘一次要 29 ms（典型桌面 1–3 ms）。

所以本机 ms 数应看作**悲观上界**：判定"能不能跟手"要看**相对量**
（P1 与 P2/P3 差两个数量级），阈值也按这里的实测标定，
换到更快/更慢的机器上应重跑再定，不要照抄。
"""
from __future__ import annotations

import argparse
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))          # 复用 mem_probe

from mem_probe import make_wav, pump, wait  # noqa: E402

# 阈值 = 本机实测 p95 × ~1.5（留噪声余量），量级上仍能抓住"退化成整图重画"这类回归：
#   例如 P1 一旦退回 `redraw()`，p95 会从 17 ms 跳到 130 ms 以上；P5 退回两次重绘是 900 ms。
# 60 fps = 16.7 ms/帧。
THRESHOLDS = {"P1": 25.0, "P4": 400.0, "P5": 60.0, "P6": 120.0,
              "P7": 1500.0, "P8": 1500.0, "P9": 3.0}


def stats(ts: list[float]) -> tuple[float, float, float]:
    s = sorted(ts)
    return s[len(s) // 2], s[min(len(s) - 1, int(len(s) * 0.95))], s[-1]


def measure(fn, repeat: int, warmup: int = 3) -> tuple[float, float, float]:
    for _ in range(warmup):
        fn()
    ts = []
    for _ in range(repeat):
        t0 = time.perf_counter()
        fn()
        ts.append((time.perf_counter() - t0) * 1000.0)
    return stats(ts)


class Bench:
    def __init__(self):
        self.rows: list[tuple[str, str, float, float, float]] = []

    def add(self, no: str, what: str, ts: tuple[float, float, float], note: str = ""):
        self.rows.append((no, what, *ts))
        print("  %-4s %-24s p50 %7.2f  p95 %7.2f  max %8.2f ms  %s"
              % (no, what, ts[0], ts[1], ts[2], note), flush=True)

    def table(self):
        print("\n== 汇总（ms） ==")
        print("  %-4s %-24s %7s %7s %8s" % ("场景", "交互", "p50", "p95", "max"))
        for no, what, p50, p95, mx in self.rows:
            print("  %-4s %-24s %7.2f %7.2f %8.2f" % (no, what, p50, p95, mx))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repeat", type=int, default=30)
    ap.add_argument("--rows", type=int, default=200, help="P6 表格行数")
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--window", action="store_true", help="真开窗口（默认 offscreen）")
    args = ap.parse_args(argv)

    if not args.window:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtWidgets import QApplication

    from audiocheck.ui import theme as T
    from audiocheck.ui import window as W

    app = QApplication.instance() or QApplication([])
    bench = Bench()

    d = os.path.join(_ROOT, "out", "memprobe")
    os.makedirs(d, exist_ok=True)
    files = [make_wav(os.path.join(d, "probe_%02d.wav" % i)) for i in range(2)]

    print("== 准备：建窗口 + 分析 %d 个文件 ==" % len(files))
    w = W.MainWindow()
    w.resize(1460, 900)
    w.show()
    app.processEvents()
    w.enqueue(files)
    w.start()
    wait(lambda: w.worker is not None and not w.worker.isRunning(), app, 600)
    pump(app, 0.5)
    w.table.setCurrentCell(0, 0)          # 触 on_select：卡片 + 曲线 + 频段
    pump(app, 0.5)
    results = [i["result"] for i in w.items if i["result"]]
    if len(results) < 2:
        print("!! 分析结果不足，基准无法进行")
        return 2

    cv_spec, cv_band, cv_wfall = w.cv_spec, w.cv_band, w.cv_wfall
    w._navigate("spectrum")
    pump(app, 1.0)
    print("  频谱曲线画布 %s 轴上图元 line/text %d/%d ｜ 背景快照 %s"
          % (tuple(cv_spec.size().toTuple()), len(cv_spec.ax.lines),
             len(cv_spec.ax.texts), "已就绪" if cv_spec._bg is not None else "无"))

    freqs = [20.0 * (1.32 ** i) for i in range(60)]
    state = {"i": 0}

    def next_f():
        state["i"] += 1
        return freqs[state["i"] % len(freqs)]

    # ---- P1：悬停（现行 = blit）----
    def hover_now():
        cv_spec._set_hover(next_f())
        # 必须把 event loop 也计进去：走 blit 时 repaint() 是同步的，
        # 但一旦退回 draw_idle() 就是**异步**的——不计 processEvents，
        # 会把"其实整图重画了"测成 1 ms（第一版探针就这样骗过自己）。
        app.processEvents()

    bench.add("P1", "悬停·现行(blit)", measure(hover_now, args.repeat),
              "帧预算 16.7 ms ｜ 快照 %s"
              % ("就绪" if cv_spec._bg is not None else "无(走慢路)"))

    # ---- P2：悬停（上轮：分层但仍整图重画）----
    def hover_layered():
        cv_spec._set_hover(next_f())     # 只为更新 self._hover
        cv_spec._hover_layer()
        cv_spec.draw()
        app.processEvents()

    bench.add("P2", "悬停·上轮(分层+draw)", measure(hover_layered, args.repeat))

    # ---- P3：悬停（最初：整图重建）----
    def hover_original():
        cv_spec._hover = next_f()
        cv_spec._static_ok = False
        cv_spec.redraw()
        app.processEvents()

    bench.add("P3", "悬停·最初(全量重建)", measure(hover_original, args.repeat))
    cv_spec._static_ok = False
    cv_spec.redraw()

    # ---- P4：点选文件（全套）----
    def select_other():
        r = results[state["i"] % 2]
        state["i"] += 1
        w._banner_update(r)
        w._cards(r)
        cv_spec.set_data(r)
        cv_band.set_data(r)
        w._meta(r)
        pump(app, 0.0)                    # 让 deleteLater 的旧卡片真的被回收

    bench.add("P4", "点选文件(全套)", measure(select_other, max(6, args.repeat // 3)))

    # ---- P5：切页（缓存命中）----
    w.table.setCurrentCell(0, 0)
    pump(app, 0.3)
    for page in ("spectro", "waterfall", "spectrum"):
        w._navigate(page)
        wait(lambda: not any(x.isRunning() for x in w._view_workers.values()), app, 60)
    pump(app, 0.3)

    def switch_page():
        page = ("spectro", "waterfall", "spectrum")[state["i"] % 3]
        state["i"] += 1
        w._navigate(page)

    bench.add("P5", "切页(缓存命中)", measure(switch_page, args.repeat))

    # ---- P6：表格刷新 ----
    base_items, base_idx = w.items, w._idx
    w.items = [dict(base_items[0]) for _ in range(args.rows)]
    for i, it in enumerate(w.items):
        it["path"] = "C:/f%03d.wav" % i
        it["file"] = "track_%03d.flac" % i
    w._idx = {it["path"]: i for i, it in enumerate(w.items)}
    bench.add("P6", "表格刷新 %d 行" % args.rows,
              measure(w.refresh_table, max(5, args.repeat // 3)))
    w.items, w._idx = base_items, base_idx
    w.refresh_table()

    # ---- P7：换肤 ----
    def theme():
        w.apply_theme(T.LIGHT if state["i"] % 2 else T.DARK)
        state["i"] += 1

    bench.add("P7", "换肤 apply_theme", measure(theme, max(6, args.repeat // 3)))
    w.apply_theme(T.DARK)

    # ---- P8：瀑布图拖拽 —— 一次手势（20 次移动 + 松手）的总代价 ----
    # 单帧成本不是用户感受到的东西：**一次拖拽手势**才是。不节流时
    # 每个移动事件排一帧（20 × 367 ms ≈ 7.3 s，松手后还在补画），
    # 节流后同样只画 2 帧（按下 + 松手）。
    from types import SimpleNamespace

    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    w._navigate("waterfall")
    pump(app, 0.5)

    ev_in = lambda: SimpleNamespace(x=100, y=100, button=1,           # noqa: E731
                                   inaxes=cv_wfall.ax, xdata=None)
    ev_move = lambda i: SimpleNamespace(x=100 + i, y=100 + (i % 5),   # noqa: E731
                                        button=1, inaxes=cv_wfall.ax, xdata=None)
    release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(10, 10),
                          Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
                          Qt.KeyboardModifier.NoModifier)

    def drag_gesture():
        cv_wfall._on_press(ev_in())
        for i in range(20):
            cv_wfall._on_drag(ev_move(i))
        cv_wfall.mouseReleaseEvent(release)
        app.processEvents()

    if cv_wfall._has_data():
        bench.add("P8", "拖拽手势(20 移动+松手)", measure(drag_gesture, max(4, args.repeat // 6),
                                                      warmup=1), "含 2 帧 3D 重绘")
    else:
        print("  P8 跳过：瀑布图数据未就绪")

    # ---- P10/P11：频谱图整图重绘 —— 本轮优化的直接对象 ----
    # 旧写法在 (2049, ~900) 网格上用 pcolormesh + 对数轴，约 180 万个多边形，
    # 单帧 470 ms；现在改成"对数频域重采样 360 行 + imshow"。
    # **两种口径并排量出来**，免得"我觉得快了"变成口头结论。
    def spectro_now():
        w.cv_spectro.redraw()

    def spectro_old():
        import numpy as np

        ax = w.cv_spectro.ax
        t, f, S = w.cv_spectro.data
        db = S - np.nanmax(S)
        ax.clear()
        ax.pcolormesh(t, f, np.clip(db, -90, 0), shading="auto", cmap="magma",
                      vmin=-90, vmax=0, rasterized=True)
        ax.set_yscale("log")
        w.cv_spectro.draw()

    if w.cv_spectro._has_data():
        w._navigate("spectro")
        pump(app, 0.4)
        bench.add("P10", "频谱图重绘·现行(imshow)",
                  measure(spectro_now, max(6, args.repeat // 5)), "对数频域 360 行")
        bench.add("P11", "频谱图重绘·旧(pcolormesh+对数轴)",
                  measure(spectro_old, max(4, args.repeat // 8)), "**仅对照，不参与判定**")
        w._navigate("overview")
        pump(app, 0.3)
        w._navigate("spectro")
        pump(app, 0.4)                      # 恢复成正常画法，别把对照态留给后面
    else:
        print("  P10/P11 跳过：频谱图数据未就绪")

    # ---- P9：分析过程中每回来一个文件的开销 ----
    guard_items, guard_idx = w.items, w._idx
    w.items = [dict(base_items[0]) for _ in range(args.rows)]
    for i, it in enumerate(w.items):
        it["path"] = "C:/f%03d.wav" % i
    w._idx = {it["path"]: i for i, it in enumerate(w.items)}
    w._refresh_timer.start()          # 节流窗口已激活 = 连续分析时的常态
    bench.add("P9", "分析中每文件回调",
              measure(lambda: w._add_result({"path": "C:/f000.wav", "level": "ok"}),
                      args.repeat),
              "不刷新，只攒到下一次合并刷新")
    w._refresh_timer.stop()
    w.items, w._idx = guard_items, guard_idx

    bench.table()
    fails = ["%s %s p95 %.1f ms > %.1f ms" % (no, what, p95, THRESHOLDS[no])
             for no, what, _, p95, _ in bench.rows
             if no in THRESHOLDS and p95 > THRESHOLDS[no]]
    if fails:
        print("\n**未通过**")
        for f in fails:
            print("  - " + f)
        return 1 if args.strict else 0
    print("\n通过：交互都在帧预算内。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
