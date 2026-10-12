#!/usr/bin/env python3
"""内存基准探针 —— audiocheck GUI「越用越胖」到底胖在哪。

为什么需要它
------------
界面层最贵的资源不是 CPU，而是**被一直引用着的数组**：频谱图/瀑布图的 S 矩阵
（4096 点 STFT × 900 列 ≈ 15 MB/文件，float64）、matplotlib 图元、Qt 对象树。
它们不报错、不进日志，只表现为「开着它翻半小时文件，任务管理器里 1 GB」。

这个脚本给"胖了多少、胖在哪"一个可复现的数字，跑法：

    python packaging/mem_probe.py                  # 4 个文件 × 3 轮
    python packaging/mem_probe.py --files 8 --rounds 5
    python packaging/mem_probe.py --strict         # 超阈值退出码 1，给回归用

场景与判据
----------
S1 窗口基线      建 MainWindow 后的 RSS（后面的增长都相对它算）
S2 画布反复重画  同一份 S 矩阵喂 30 次：**图元不得累积**（figure.axes 必须恒定）
S3 悬停风暴      600 次 hover：只该动 3 个图元，不该长
S4 全流程多文件  分析 + 逐个点选 × N 轮：**缓存必须有界**
S5 换肤          30 次主题切换：QSS/图元重建不得留残余
S6 清空          点「清空」后应回落到接近 S1

--strict 阈值：
  S2 每次重画 RSS 增长 < 0.6 MB；S2 后 figure.axes 数量与首次一致
  S3 第 2 轮起每轮增长 < 5 MB（第 1 轮含一次性渲染预热，单列不判）
  S4/S5 图元数、弹窗数、动画数不增长
  S7 缓存 < 128 MB；结束 RSS < S1 + 100 MB（一次性预热的口径）

**注意**：S1 必须 `show()` 窗口。画布是"不可见就不画"的（惰性重画），
不显示窗口的话所有画布停在脏状态、一个图元都不建 —— S2 会变成
"什么都没画"的假通过（这一版探针真踩过）。
"""
from __future__ import annotations

import argparse
import ctypes
import gc
import os
import sys
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)


# ------------------------------------------------------------------ 进程内存

class _PMC(ctypes.Structure):
    _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
                ("PrivateUsage", ctypes.c_size_t)]


def _win_info() -> _PMC | None:
    """GetProcessMemoryInfo 住在 psapi.dll（kernel32 只有 K32 前缀那版）。

    第一版探针照 kernel32.GetProcessMemoryInfo 取，直接 AttributeError：
    Win10/11 上 kernel32 并不导出这个名字，必须走 psapi。
    """
    if sys.platform != "win32":
        return None
    fn = getattr(_win_info, "_fn", None)
    if fn is None:
        for dll, name in (("kernel32", "K32GetProcessMemoryInfo"),
                          ("psapi", "GetProcessMemoryInfo")):
            try:
                fn = getattr(ctypes.WinDLL(dll, use_last_error=True), name)
                break
            except (AttributeError, OSError):
                fn = None
        _win_info._fn = fn
    if fn is None:
        return None
    c = _PMC()
    c.cb = ctypes.sizeof(_PMC)
    if not fn(ctypes.c_void_p(-1), ctypes.byref(c), c.cb):
        return None
    return c


def rss_mb() -> float:
    """当前进程常驻内存（Windows 用 GetProcessMemoryInfo，其它平台读 /proc）。"""
    c = _win_info()
    if c is not None:
        return c.WorkingSetSize / 1048576.0
    if sys.platform == "win32":
        return float("nan")
    try:
        with open("/proc/self/statm", encoding="ascii") as fh:
            return int(fh.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1048576.0
    except OSError:
        return float("nan")


def peak_mb() -> float:
    """峰值常驻（泄漏的另一个视角：涨上去之后有没有降回来）。"""
    c = _win_info()
    if c is not None:
        return c.PeakWorkingSetSize / 1048576.0
    if sys.platform == "win32":
        return float("nan")
    try:
        import resource
        return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
    except ImportError:
        return float("nan")


# ------------------------------------------------------------------ 素材

def make_wav(path: str, seconds: float = 70.0, fs: int = 44100):
    """合成素材：白噪 + 砖墙 16k。时长必须够长，否则 STFT 列数到不了 max_cols=900，
    测出来的 S 就不是真实大小，整个探针也就失去意义（真实曲目 3–5 分钟必然到顶）。"""
    if os.path.exists(path):
        return path
    import numpy as np
    import soundfile as sf
    rng = np.random.default_rng(20261012)
    x = (rng.standard_normal(int(fs * seconds)) * 0.2).astype(np.float32)
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1.0 / fs) > 16000] = 0.0
    x = np.fft.irfft(spec, n=len(x)).astype(np.float32)
    sf.write(path, np.column_stack([x, x * 0.9]), fs, subtype="PCM_16")
    return path


# ------------------------------------------------------------------ 事件泵

def pump(app, seconds: float, events: bool = True):
    """转事件循环。

    **必须手动派发 DeferredDelete**：`deleteLater()` 投的是延迟删除事件，而
    `processEvents()` 不保证把它送出去（Qt 只在"事件循环层级匹配"时派发）。
    界面层大量用 deleteLater 做卡片/提示的回收（`_cards` 每次点选重建 18 张卡），
    漏派发就会让人看到"每点一次涨 0.6 MB"这种假泄漏，白查半天。
    真实运行时 QApplication.exec() 会自己派发，所以这里是探针保真度问题而非代码缺陷。
    """
    from PySide6.QtCore import QCoreApplication, QEvent
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if events:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.004)


def wait(cond, app, timeout=120.0):
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        pump(app, 0.004)
    return False


# ------------------------------------------------------------------ 报告

class Report:
    def __init__(self):
        self.rows: list[tuple[str, float, float, str]] = []

    def add(self, step: str, before: float, after: float, note: str = ""):
        self.rows.append((step, before, after, note))
        print("  %-26s %8.1f → %8.1f MB  %+7.1f  %s"
              % (step, before, after, after - before, note), flush=True)

    def table(self):
        print("\n== 汇总 ==")
        print("  %-26s %8s %8s %8s" % ("场景", "前", "后", "Δ"))
        for step, b, a, _ in self.rows:
            print("  %-26s %8.1f %8.1f %+8.1f" % (step, b, a, a - b))


# ------------------------------------------------------------------ 场景

def scene_canvas(app, w, cv, data, cycles=30):
    """S2：同一份数据反复重画。图元若累积，axes 数会一路涨。"""
    from PySide6.QtWidgets import QApplication  # noqa: F401
    w._navigate("spectro")
    cv.set_data(w.items[-1]["result"] if w.items else None, data)
    app.processEvents()
    axes0 = len(cv.fig.axes)
    b = rss_mb()
    for _ in range(cycles):
        cv.set_data(None, data)
        app.processEvents()
    a = rss_mb()
    axes1 = len(cv.fig.axes)
    return b, a, "axes %d→%d  %s" % (axes0, axes1, _axes_verdict(axes0, axes1))


def _axes_verdict(a0: int, a1: int) -> str:
    return "OK" if a1 == a0 else "**图元累积 %+d**" % (a1 - a0)


def scene_hover(app, w, cv, times=600):
    """S4：悬停风暴。只该删/建自己那 3 个图元，图元总数必须恒定。"""
    if not cv._has_data():
        return None
    n0 = len(cv.ax.lines) + len(cv.ax.texts) + len(cv.ax.collections)
    b = rss_mb()
    for i in range(times):
        f = 40.0 * (1.6 ** (i % 60))
        cv._set_hover(f if f < 20000 else None)
    cv._set_hover(None)                     # 移出鼠标，图元数应与风暴前一致
    app.processEvents()
    n1 = len(cv.ax.lines) + len(cv.ax.texts) + len(cv.ax.collections)
    return b, rss_mb(), "图元 %d→%d %s" % (n0, n1, "OK" if n1 == n0 else "**累积 %+d**" % (n1 - n0))


def scene_pipeline(app, w, files, rounds):
    """S4：分析 + 逐个点选 × rounds。缓存若无界，这里会一路涨。"""
    w.clear()
    w.enqueue(files)
    w.start()
    wait(lambda: w.worker is not None and not w.worker.isRunning(), app, 600)
    pump(app, 0.4)
    rows = w.table.rowCount()
    b = rss_mb()
    per_round, widgets, artists = [], [], []
    for _round in range(rounds):
        rb = rss_mb()
        for i in range(rows):
            w.table.setCurrentCell(i, 0)
            w._navigate("spectro")
            pump(app, 0.05)
            w._navigate("waterfall")
            pump(app, 0.05)
            wait(lambda: not any(x.isRunning() for x in w._view_workers.values()), app, 60)
        pump(app, 0.3)
        per_round.append(rss_mb() - rb)
        # 顺带盯住对象数：内存涨而对象数不涨 = 分配器/渲染缓存；两者一起涨 = 真泄漏
        from PySide6.QtWidgets import QWidget
        widgets.append(len(w.findChildren(QWidget)))
        # `ax` 可能是 None：画布现在**惰性重画**，瀑布图的 3D 轴是首次
        # 真正重画时才建的（没显示过就一直没轴）——探针自己也要跟上这条规则
        artists.append(sum(len(cv.ax.lines) + len(cv.ax.collections) + len(cv.ax.texts)
                           for cv in (w.cv_spec, w.cv_spectro, w.cv_wfall, w.cv_band)
                           if getattr(cv, "ax", None) is not None))
    a = rss_mb()
    note = "缓存 %s/%d 项 ｜ 各轮 Δ %s ｜ 控件 %s ｜ 图元 %s" % (
        human_bytes(cache_bytes(w)), len(w._cache),
        " ".join("%+.1f" % d for d in per_round),
        "/".join(str(x) for x in widgets), "/".join(str(x) for x in artists))
    return (b, a, note), per_round


def cache_bytes(w) -> int:
    """缓存里到底压着多少字节（按数组真实 nbytes 算，不按条数）。"""
    tot = 0
    for v in w._cache.values():
        for part in v:
            n = getattr(part, "nbytes", None)
            tot += int(n) if n is not None else 0
    return tot


def human_bytes(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit)
        n /= 1024.0
    return "%.1f GB" % n


def scene_toasts(app, w, times=40, ms=60):
    """S5：通知风暴。动画/弹窗必须用完即毁，不能挂在组件树上。"""
    from PySide6.QtCore import QAbstractAnimation
    lay = w.toasts._lay.count()
    b = rss_mb()
    for i in range(times):
        w.toasts.notify("探针通知 %d" % i, "info", ms)
    pump(app, ms / 1000.0 + 1.2)
    anims = len(w.toasts.findChildren(QAbstractAnimation))
    lay2 = w.toasts._lay.count()
    return b, rss_mb(), "残留弹窗 %d→%d ｜ 动画 %d" % (lay, lay2, anims)


def scene_theme(app, w, times=30):
    from audiocheck.ui import theme as T
    b = rss_mb()
    for i in range(times):
        w.apply_theme(T.LIGHT if i % 2 else T.DARK)
    pump(app, 0.2)
    w.apply_theme(T.DARK)
    return b, rss_mb(), "画布 axes/文本数不变即正常"


def scene_clear(app, w):
    b = rss_mb()
    w.clear()
    gc.collect()
    pump(app, 0.2)
    return b, rss_mb(), "缓存 %s" % human_bytes(cache_bytes(w))


# ------------------------------------------------------------------ 主流程

def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--files", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=3)
    ap.add_argument("--seconds", type=float, default=70.0)
    ap.add_argument("--cycles", type=int, default=30)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--trace", action="store_true", help="结束时打印 tracemalloc 增长归因")
    ap.add_argument("--window", action="store_true", help="真的显示窗口（默认 offscreen）")
    args = ap.parse_args(argv)

    if not args.window:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    import tracemalloc

    from PySide6.QtCore import QAbstractAnimation
    from PySide6.QtWidgets import QApplication

    from audiocheck.core.views import spectrogram
    from audiocheck.ui import window as W

    if args.trace:
        tracemalloc.start(12)

    t0 = time.perf_counter()
    app = QApplication.instance() or QApplication([])
    rep = Report()

    print("== S0 导入 ==")
    base = rss_mb()
    print("  python+Qt+matplotlib 载入完成  %.1f MB  (%.1f s)" % (base, time.perf_counter() - t0))

    d = os.path.join(_ROOT, "out", "memprobe")
    os.makedirs(d, exist_ok=True)
    files = [make_wav(os.path.join(d, "probe_%02d.wav" % i), args.seconds)
             for i in range(args.files)]

    print("== S1 建窗口 ==")
    b = rss_mb()
    w = W.MainWindow()
    # **必须 show()**：画布现在是"不可见就不画"的（惰性重画），
    # 不显示窗口的话所有画布停在脏状态、一个图元都不建，
    # S2 之后的"图元/orientation 不累积"就变成了"什么都没画"的假通过。
    w.resize(1460, 900)
    w.show()
    app.processEvents()
    rep.add("MainWindow()", b, rss_mb(), "")
    baseline = rss_mb()
    snap0 = tracemalloc.take_snapshot() if args.trace else None

    print("== S2 画布反复重画 ==")
    _, f, S = spectrogram(files[0])
    data = (_, f, S)
    print("  素材 S 矩阵 %s  %s  %.2f MB" % (S.shape, S.dtype, S.nbytes / 1048576.0))
    b2, a2, note2 = scene_canvas(app, w, w.cv_spectro, data, args.cycles)
    rep.add("spectro.set_data ×%d" % args.cycles, b2, a2, note2)
    axes_after = len(w.cv_spectro.fig.axes)
    per_redraw = (a2 - b2) / max(1, args.cycles)

    print("== S3 分析 + 点选 ×%d 轮 ==" % args.rounds)
    out, rounds_delta = scene_pipeline(app, w, files, args.rounds)
    rep.add("pipeline %d 文件 ×%d" % (len(files), args.rounds), out[0], out[1], out[2])
    cache_mb, cache_n = cache_bytes(w) / 1048576.0, len(w._cache)
    wfall_axes = None

    print("== S4 悬停风暴 ==")
    w._navigate("spectrum")
    out = scene_hover(app, w, w.cv_spec)
    if out:
        rep.add("spectrum hover ×600", out[0], out[1], out[2])
    w._navigate("waterfall")
    pump(app, 0.4)
    wfall_axes = len(w.cv_wfall.fig.axes)

    print("== S5 通知风暴 ==")
    out = scene_toasts(app, w)
    rep.add("notify ×40", out[0], out[1], out[2])
    toast_anim = len(w.toasts.findChildren(QAbstractAnimation))
    toast_left = w.toasts._lay.count()

    print("== S6 换肤 ×%d ==" % 30)
    out = scene_theme(app, w)
    rep.add("apply_theme ×30", out[0], out[1], out[2])
    axes_final = len(w.cv_spectro.fig.axes)

    print("== S7 清空 ==")
    out = scene_clear(app, w)
    rep.add("clear()", out[0], out[1], out[2])
    final = rss_mb()

    rep.table()
    print("\n空闲基线 %.1f MB ｜ 结束 %.1f MB ｜ 净增 %+.1f MB ｜ 缓存 %.1f MB（%d 项）"
          % (baseline, final, final - baseline, cache_mb, cache_n))

    if snap0 is not None:
        print("\n== tracemalloc 归因：相对 Window 基线的增长 Top 10 ==")
        for st in tracemalloc.take_snapshot().compare_to(snap0, "lineno")[:10]:
            fr = st.traceback[0]
            print("  %+9.1f KB  %s:%d" % (st.size_diff / 1024.0, fr.filename, fr.lineno))

    fails = []
    if per_redraw > 0.6:
        fails.append("每次重画 +%.2f MB（>0.6）→ 图元在累积" % per_redraw)
    # **判"泄漏"要看"随使用增长"，不是看绝对增量。**
    # 第 1 轮必然偏大：Qt 的 backing store、matplotlib 的字体/文本布局缓存、
    # AGG 的像素缓冲都是首次真正渲染时才分配的一次性开销（本机约 30 MB）。
    # 真正的泄漏表现为**每一轮都涨**——旧版色条 bug 是 +17.9 MB/次重画，
    # 第一轮就能看出量级不对。
    tail = rounds_delta[1:] if len(rounds_delta) > 1 else []
    if tail and max(tail) > 5.0:
        fails.append("后续轮次仍在涨（%s）→ 疑似随使用增长"
                     % " ".join("%+.1f" % d for d in tail))
    if axes_after != 2 or axes_final != 2:
        fails.append("频谱图 figure.axes = %d→%d（应为 2：主图 + 1 个 colorbar）"
                     % (axes_after, axes_final))
    if wfall_axes is not None and wfall_axes != 1:
        fails.append("瀑布图 figure.axes = %d（应为 1）" % wfall_axes)
    if toast_anim > 4 or toast_left > 3:
        fails.append("通知残留：动画 %d 个、弹窗 %d 个 → 用完没销毁" % (toast_anim, toast_left))
    if cache_mb > 128:
        fails.append("缓存持有 %.0f MB（>128）" % cache_mb)
    # 100 MB 是"一次性预热"的容差，不是泄漏容差：真正渲染过之后，
    # Qt backing store（4 张 1149×758 的位图）+ matplotlib 字体缓存 +
    # 各画布的 Agg 像素缓冲合计约 30–70 MB，且**此后不再涨**。
    if final - baseline > 100:
        fails.append("清理后净增 %.0f MB（>100，已扣掉一次性预热的口径）"
                     % (final - baseline))

    if fails:
        print("\n**未通过**")
        for f_ in fails:
            print("  - " + f_)
        print("  提示：--strict 才会返回非零退出码")
        return 1 if args.strict else 0
    print("\n通过：图元不累积、缓存有界、清理后回落。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
