#!/usr/bin/env python3
"""visual_regression：关键视图的**视觉回归**（E1.3）——自实现，不引 pixelmatch。

## 为什么自实现

E1 提示词建议 `pixelmatch` / `pytest-image-snapshot`。两者都不在本机，
而项目铁律是**不增依赖**（dev 也尽量不加）。像素比对本身很简单：
两幅同尺寸图逐像素比、超阈值算"不同"、统计差异比例。**不需要框架。**

## 三条纪律（E1 提示词，逐条落实）

1. **固定 DPI / 固定主题**：全部基线用 `DPI=100` + 内置 `DARK` 主题渲染
   （`render_all`）。变的是被测代码，不是渲染条件。
2. **阈值显式**：`PIXEL_TOL`（单通道容差，容许抗锯齿差异）与
   `DIFF_FRAC_TOL`（允许不同的像素比例）都写死在文件里，可复算。
3. **基线图入库**：`tests/baselines/*.png` + `meta.json`（记录 matplotlib 版本、
   DPI、尺寸、主题与每张图的 sha256）。**版本不符就跳过并说明**——
   跨 matplotlib 版本渲染本来就会变，假装可比是不诚实。

    python packaging/visual_regression.py --render     # 生成/刷新基线
    python packaging/visual_regression.py              # 比对（--strict 判红）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SRC = os.path.join(_ROOT, "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

BASELINE_DIR = os.path.join(_ROOT, "tests", "baselines")
DPI = 100
W, H = 720, 430
THEME = "dark"

#: 单通道容差（0–255）。抗锯齿/字体栅格化的差异落在这一档内 ⇒ 不算不同。
PIXEL_TOL = 12
#: 允许"不同像素"占全图的比例（0.5%）。超了才判红。
DIFF_FRAC_TOL = 0.005


def _data():
    """确定性合成数据（不读任何外部文件，保证可复现）。"""
    import numpy as np

    f = np.geomspace(20.0, 20000.0, 400)
    db = -20.0 - 8.0 * np.log10(f / 20.0)
    db[f > 16000] = -95.0
    t = np.linspace(0.0, 30.0, 240)
    # ⚠ S 的行数必须 == len(f)（频谱图/瀑布图按 f 索引行；不匹配会取到空切片）。
    nf = len(f)
    S = (-30.0 - 40.0 * (np.arange(nf)[:, None] / nf)
         - 6.0 * np.sin(np.arange(240)[None, :] / 9.0))
    tl = {"timeline": [
        {"start": float(i * 5), "end": float(i * 5 + 10), "clip_pct": float(i % 3) * 0.4,
         "peak_dbfs": -1.0 - i * 0.3, "rms_dbfs": -9.0, "lufs": -12.0 - i * 0.4,
         "true_peak_dbfs": -1.0 - i * 0.3} for i in range(4)],
        "timeline_loudness_problems": [],
        "timeline_note": "时域逐段统计（削波/峰值/RMS）+ 逐段响度；逐段频谱未做"}
    return f, db, S, t, tl


def render_all(outdir: str) -> dict:
    """渲染基线（固定 DPI/主题/尺寸）。返回 meta。"""
    import matplotlib
    matplotlib.use("Agg")
    from PySide6.QtWidgets import QApplication

    from audiocheck.ui import charts as C
    from audiocheck.ui import theme as T

    QApplication.instance() or QApplication([])
    f, db, S, t, tl = _data()
    pal = T.plot_palette(T.DARK.p, T.DARK)
    os.makedirs(outdir, exist_ok=True)

    shots = {}

    # 1) 频谱曲线
    cv = C.SpectrumCanvas()
    cv.resize(W, H)
    cv.apply_theme(pal)
    cv.set_data({"file": "baseline.wav", "sr": 44100, "cutoff_khz": 16.0,
                 "curve": [[float(a), float(b)] for a, b in zip(f, db)]})
    cv.redraw()
    shots["spectrum"] = cv.fig

    # 2) 频谱图（色图 inferno）
    cv2 = C.SpectrogramCanvas()
    cv2.resize(W, H)
    cv2.apply_theme(pal)
    cv2.set_data({"file": "baseline.wav"}, (t, f, S))
    cv2.redraw()
    shots["spectrogram"] = cv2.fig

    # 3) 时间定位（双轴）
    cv3 = C.TimelineCanvas()
    cv3.resize(W, H)
    cv3.apply_theme(pal)
    cv3.set_data(tl)
    cv3.redraw()
    shots["timeline"] = cv3.fig

    meta = {"matplotlib": matplotlib.__version__, "platform": sys.platform,
            "dpi": DPI, "size": [W, H], "theme": THEME,
            "pixel_tol": PIXEL_TOL, "diff_frac_tol": DIFF_FRAC_TOL, "files": {}}
    for name, fig in shots.items():
        p = os.path.join(outdir, name + ".png")
        fig.savefig(p, dpi=DPI, facecolor=fig.get_facecolor())
        with open(p, "rb") as fh:
            meta["files"][name] = hashlib.sha256(fh.read()).hexdigest()
    with open(os.path.join(outdir, "meta.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=1)
    return meta


def _load_png(path: str):
    import numpy as np
    from PIL import Image

    with Image.open(path) as im:
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def compare(baseline_dir: str = BASELINE_DIR, verbose: bool = True):
    """逐图比对。返回 (是否全部通过, 说明行列表, 跳过原因或 None)。"""
    import matplotlib
    meta_path = os.path.join(baseline_dir, "meta.json")
    if not os.path.exists(meta_path):
        return False, [], "没有基线（先跑 --render 并入库）"
    with open(meta_path, encoding="utf-8") as f:
        meta = json.load(f)
    if meta.get("matplotlib") != matplotlib.__version__:
        return False, [], ("matplotlib 版本不符（基线 %s / 当前 %s）——"
                           "跨版本渲染本来就不同，**跳过**而不是假装可比"
                           % (meta.get("matplotlib"), matplotlib.__version__))
    if meta.get("platform") != sys.platform:
        return False, [], ("平台不符（基线 %s / 当前 %s）——字体栅格化栈不同，"
                           "**跳过**（基线按『版本+平台』钉住；跨平台需各留一套基线）"
                           % (meta.get("platform"), sys.platform))

    # 用同一套渲染参数重画到临时目录
    import tempfile
    tmp = tempfile.mkdtemp(prefix="ac_vis_")
    render_all(tmp)
    lines, ok = [], True
    for name in meta["files"]:
        a = _load_png(os.path.join(baseline_dir, name + ".png"))
        b = _load_png(os.path.join(tmp, name + ".png"))
        if a.shape != b.shape:
            lines.append("%-14s 尺寸不同 %s vs %s" % (name, a.shape, b.shape))
            ok = False
            continue
        diff = (abs(a - b).max(axis=2) > PIXEL_TOL)
        frac = float(diff.mean())
        good = frac <= DIFF_FRAC_TOL
        ok = ok and good
        lines.append("%-14s 不同像素 %.4f%%（阈值 %.2f%%）%s"
                     % (name, frac * 100, DIFF_FRAC_TOL * 100, "" if good else "  ← **超阈值**"))
    return ok, lines, None


def main() -> int:
    ap = argparse.ArgumentParser(description="视觉回归（自实现像素比对）")
    ap.add_argument("--render", action="store_true", help="生成/刷新基线到 tests/baselines")
    ap.add_argument("--strict", action="store_true", help="不通过返回 1")
    a = ap.parse_args()

    if a.render:
        meta = render_all(BASELINE_DIR)
        print("基线已生成：%s（matplotlib %s，%d×%d，主题 %s）"
              % (BASELINE_DIR, meta["matplotlib"], W, H, THEME))
        for n, h in meta["files"].items():
            print("  %-14s %s" % (n, h[:16]))
        return 0

    ok, lines, skip = compare()
    if skip:
        print("视觉回归：**跳过** —— %s" % skip)
        return 0                     # 缺基线/版本不符不算失败（如实说明）
    print("视觉回归（固定 %d DPI / %s 主题，单通道容差 %d，允许 %.2f%% 像素不同）"
          % (DPI, THEME, PIXEL_TOL, DIFF_FRAC_TOL * 100))
    for ln in lines:
        print("  " + ln)
    print("结论：%s" % ("通过" if ok else "**有图超阈值**（渲染真的变了）"))
    return 0 if (ok or not a.strict) else 1


if __name__ == "__main__":
    raise SystemExit(main())
