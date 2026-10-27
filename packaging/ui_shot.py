#!/usr/bin/env python3
"""离屏出图：把真实窗口渲染成 PNG（**不走 `PrintWindow`**）。

## 为什么另开一条出图路径

`packaging/shot.ps1` 是"启动打包好的 exe + Win32 `PrintWindow` 抓屏"，它验证的是
**端到端**（打包产物能不能起来、起来长什么样），代价是每次要先花约 4 分钟打包。
它还有两个已知坑（见接手文档 §9）：硬件加速窗口常返回全黑；视图切换后要等很久。

本脚本走另一条路：**在进程内用 `QWidget.grab()` 渲染部件树**。
  * 它是 Qt 自己的绘制路径，不经过窗口管理器，不会返回全黑；
  * 秒级出图，适合"改一处看一眼"的迭代；
  * 代价：**不含**系统级装饰、DPI 缩放与真实字形栅格化结果，
    最终验收仍应跑一次 `shot.ps1`。

出图同时供 `palette_report.py --shot` 量面积配额（那一条必须量渲染结果）。

跑法：

    python packaging/ui_shot.py --out shots/b2 --set dark,light,pop
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
for p in (_SRC, os.path.join(_ROOT, "packaging")):
    if p not in sys.path:
        sys.path.insert(0, p)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import perf_probe  # noqa: E402  复用它的 make_wav / pump / wait（同一套等待纪律）


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(_ROOT, "shots", "b2"))
    ap.add_argument("--set", default="dark,light,pop",
                    help="要出的几张：dark / light / pop / meta（逗号分隔）")
    ap.add_argument("--size", default="1460x900")
    args = ap.parse_args(argv)

    from PySide6.QtWidgets import QApplication

    from audiocheck.ui import window as W

    app = QApplication.instance() or QApplication([])
    W, T = _load(W)
    # **必须解析字体族**：真实入口 `window.main()` 里做了这一步，离屏脚本不做
    # 就会得到"满屏方框"的截图——而那种图会被误读成"界面渲染坏了"。
    # （本项目在字体上踩过两次：QSS 多族回退、无 QApplication 调 QFontDatabase。）
    T.init_fonts(app)

    w, h = (int(v) for v in args.size.split("x"))
    d = os.path.join(_ROOT, "out", "memprobe")
    os.makedirs(d, exist_ok=True)
    files = [perf_probe.make_wav(os.path.join(d, "shot_%02d.wav" % i)) for i in range(2)]

    win = W.MainWindow()
    win.resize(w, h)
    win.show()
    perf_probe.pump(app, 0.4)

    for f in files:
        win.items.append({"path": f, "file": os.path.basename(f), "result": None})
    win._idx = {i["path"]: n for n, i in enumerate(win.items)}
    win.queue = [i["path"] for i in win.items]
    win.start()
    perf_probe.wait(lambda: not (win.worker and win.worker.isRunning()), app, 120)
    perf_probe.pump(app, 1.0)
    if win.items:
        win.table.setCurrentCell(0, 0)
        win.table.selectRow(0)
    perf_probe.pump(app, 1.2)

    os.makedirs(args.out, exist_ok=True)
    made = []
    for name in [s.strip() for s in args.set.split(",") if s.strip()]:
        if name == "editor":
            # 编辑工作站：打开一段削波素材、框选区、应用去削波，抓默认（暗）三栏布局。
            _make_editor_shot(app, T, args, made)
            continue
        if name == "editor_ab":
            _make_editor_shot(app, T, args, made, ab=True)
            continue
        if name == "editor_sel":
            # 选区态：框选区并拉出 t/f 测层，抓"选区 + 读数"那一帧。
            _make_editor_shot(app, T, args, made, measure=True)
            continue
        if name == "editor_light":
            # 暗/亮切换：同一工作站换浅色主题。
            _make_editor_shot(app, T, args, made, light=True)
            continue
        if name == "editor_lasso":
            # 套索态：自由多边形选区的叠加与工具互斥（P2"矩形→套索"的视觉证据）。
            _make_editor_shot(app, T, args, made, lasso=True)
            continue
        if name == "editor_stack":
            # 多算子栈：连续应用 3 个算子，让 Inspector 的**可交互栈**与底部批处理
            # 坞都填满，验证"栈可重排/移除/开关"这一轮的 UI。
            _make_editor_shot(app, T, args, made, multi=True)
            continue
        if name == "dark":
            win.apply_theme(T.DARK)
        elif name == "light":
            win.apply_theme(T.LIGHT)
        elif name == "pop":
            from audiocheck.ui.palette import theme_from_seed
            win.apply_theme(theme_from_seed("pop", T.BUILTIN_SEED, "dark", "pop"))
        elif name == "meta":
            win._navigate("meta")
            perf_probe.wait(lambda: not any(x.isRunning() for x in win._view_workers.values()),
                            app, 60)
        elif name in ("toast", "toast_light"):
            # 浮层（投影 + 顶光）只在**真有浮层时**才看得见。存活时长给足，
            # 抓的是"稳定显示"的那一帧；淡入 200 ms 已走完。
            if name == "toast_light":
                win.apply_theme(T.LIGHT)
            kind = ("ok", "bad", "warn")
            for i, k in enumerate(kind):
                win.toasts.notify("材质自检：%s 通知 %d" % (k, i), k, ms=60000)
            perf_probe.pump(app, 0.6)
        perf_probe.pump(app, 1.0)
        path = os.path.join(args.out, "%s.png" % name)
        win.grab().save(path)
        made.append(path)
        print("已出图 %s（%d KB）" % (path, os.path.getsize(path) // 1024))

    print("\n量面积配额：")
    print("  python packaging/palette_report.py --shot %s" % " --shot ".join(made))
    return 0


def _load(W):
    """把 theme 也取出来（少写一行 import，且保证与 window 同一份）。"""
    from audiocheck.ui import theme as T
    return W, T


def _make_editor_shot(app, T, args, made, ab: bool = False,
                     measure: bool = False, light: bool = False,
                     multi: bool = False, lasso: bool = False) -> None:
    """离屏出编辑工作站截图：开削波素材→框选区→应用算子，抓三栏布局。

    ab     : 同时切到 A/B 选源视图。
    measure: 额外拉出 t/f 测层浮标（验证选区读数）。
    light  : 用浅色主题（验证暗/亮切换）。
    multi  : 连续应用多个算子，并把一个作业塞进底部批处理坞，验证可交互栈这一轮 UI。
    """
    import os
    import tempfile

    import numpy as np
    import soundfile as sf

    from audiocheck.ui.editor import EditorWindow

    theme = T.THEMES["light"] if light else T.THEMES["dark"]
    win = EditorWindow(theme)
    win.resize(*(int(v) for v in args.size.split("x")))
    win.show()
    perf_probe.pump(app, 0.4)

    # 合成一段削波音频（答案已知），不依赖外部素材
    fs = 44100
    rng = np.random.default_rng(7)
    x = np.clip(rng.standard_normal(int(fs * 4)) * 6.0, -1.0, 1.0).astype(np.float32)
    path = os.path.join(tempfile.gettempdir(), "_shot_clip.wav")
    sf.write(path, np.column_stack([x, x]), fs, subtype="PCM_16")
    win.open_file(path)
    win.view._sel = (0.5, 2.0, 100.0, 4000.0)
    win.view.selectionChanged.emit(0.5, 2.0, 100.0, 4000.0)
    win.ops.kind.setCurrentIndex(win.ops.kind.findData("declip"))
    win.ops._apply()
    perf_probe.pump(app, 0.5)
    if ab:
        win.inspector_panel.btn_ab.setChecked(True)
        win.inspector_panel.abToggled.emit(True)
        perf_probe.pump(app, 0.3)
    if measure:
        # 拉出测层：与真实拖动一致，调用内部绘制（不依赖鼠标事件）。
        win.view._draw_measure()
        perf_probe.pump(app, 0.2)
    if multi:
        # 再堆两个算子（增益 / 降噗声），让 Inspector 的可交互栈出现 3 项；
        # 同时塞一个作业进底部批处理坞，验证本轮新增的两块 UI 都画得出来。
        win.ops.kind.setCurrentIndex(win.ops.kind.findData("gain"))
        win.ops.gain_db.setValue(-3.0)
        win.ops._apply()
        win.ops.kind.setCurrentIndex(win.ops.kind.findData("declick"))
        win.ops._apply()
        perf_probe.pump(app, 0.4)
        if hasattr(win, "batch_panel"):
            win.batch_panel.add_job({"source": path, "dest": path + ".out.wav",
                                     "ops": win.ops._session.ops[-1].to_dict()})
            perf_probe.pump(app, 0.2)

    if lasso:
        # 套索态：开套索工具 + 一段进行中的多边形轨迹叠加（P2 套索的视觉证据）。
        win.editor.raise_()                      # 编辑坞与检视坞 tab 化 ⇒ 先抬编辑坞
        win.view.set_lasso_tool(True)
        win.view._lasso_pts = [(0.4, 1200.0), (0.7, 1100.0), (1.0, 1500.0),
                               (1.2, 2600.0), (1.0, 3900.0), (0.6, 4100.0),
                               (0.42, 3000.0)]
        win.view._draw_lasso()
        perf_probe.pump(app, 0.3)
    name = "editor_lasso" if lasso else ("editor_ab" if ab else ("editor_sel" if measure else ("editor_light" if light else ("editor_stack" if multi else "editor"))))
    out = os.path.join(args.out, "%s.png" % name)
    win.grab().save(out)
    made.append(out)
    print("已出图 %s（%d KB）" % (out, os.path.getsize(out) // 1024))
    win.deleteLater()
    perf_probe.pump(app, 0.2)


if __name__ == "__main__":
    sys.exit(main())
