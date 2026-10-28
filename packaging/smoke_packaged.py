#!/usr/bin/env python3
"""打包产物冒烟测试：**构建成功 ≠ 能跑**（exclude 列表必须逐条验证）。

## 为什么必须有这个脚本

`packaging/audiocheck.spec` 的 `excludes` 里每一条都是"砍掉体积"的赌注。
砍错的后果不是构建失败——构建永远成功——而是 **EXE 一启动就
`ModuleNotFoundError`**，而且只有真实用户会踩到。

本仓有过一次先例（spec 里的注释记着）：早先想砍 `numpy.f2py` 减体积，
结果 scipy 的 `array_api_compat` 在**模块加载时**就要 import 它，
EXE 一启动就崩。

## 测什么（三层，由浅入深）

1. **CLI 通路**：`--version` / `--doctor`。这两条覆盖"解释器起来 + 核心依赖
   全部可导入 + numpy/scipy/soundfile/mutagen 可用"。冷启动耗时也在这里量。
2. **真实分析**：拿一个合成的 2 秒 WAV 跑一遍完整分析，断言退出码 0、
   报告里出现判定结论。这覆盖分析器动态发现（PyInstaller 静态分析看不到
   `pkgutil.iter_modules`，必须靠 spec 的 hiddenimports）。
3. **GUI 通路**：`QT_QPA_PLATFORM=offscreen` 下启动编辑工作站，
   1.5 秒后自动退出，断言退出码 0。这覆盖 PySide6 插件是否打全。

用法：`python packaging/smoke_packaged.py [--exe PATH] [--repeat N]`
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_EXE = os.path.join(REPO, "dist", "audiocheck", "audiocheck.exe")


def _run(exe, args, timeout=180, env=None):
    e = dict(os.environ)
    e["QT_QPA_PLATFORM"] = "offscreen"
    if env:
        e.update(env)
    t0 = time.perf_counter()
    try:
        p = subprocess.run([exe, *args], capture_output=True, text=True,
                           timeout=timeout, env=e)
    except subprocess.TimeoutExpired:
        # ⚠ **超时必须当作明确失败报出来，不能让它抛异常终止冒烟**。
        # 这正是本脚本存在的理由之一：2026-10-28 在旧产物上跑 `--version`
        # 卡了 180 s——因为那时的入口硬接在 `ui.app.main` 上，任何参数都会
        # 开窗口进主循环（发布包因此完全没有 CLI）。如果这里直接抛
        # `TimeoutExpired`，冒烟脚本会以 traceback 结束，看不出"是卡死"。
        class _Timeout:
            returncode = -9
            stdout = ""
            stderr = ("超时 %ss：进程没有退出（很可能是入口分派错误，"
                      "参数被当成'开 GUI'处理了）" % timeout)
        return _Timeout(), time.perf_counter() - t0
    return p, time.perf_counter() - t0


def _make_wav(path):
    import numpy as np
    import soundfile as sf
    fs = 44100
    t = np.arange(int(fs * 2)) / fs
    x = (0.4 * np.sin(2 * np.pi * 440 * t)
         + 0.2 * np.sin(2 * np.pi * 1320 * t)).astype(np.float32)
    sf.write(path, np.c_[x, x], fs, subtype="PCM_16")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--exe", default=DEFAULT_EXE)
    ap.add_argument("--repeat", type=int, default=1,
                    help="重复次数（量冷/热启动差异用）")
    a = ap.parse_args()
    exe = a.exe
    if not os.path.isfile(exe):
        print("[X] 找不到可执行文件：%s" % exe, file=sys.stderr)
        return 2
    print("冒烟：%s（%.1f MB）\n" % (exe, os.path.getsize(exe) / 1048576))

    fails = []

    # ---- 层 1：CLI 通路 + 冷启动 ----
    for i in range(a.repeat):
        p, dt = _run(exe, ["--version"])
        ok = p.returncode == 0 and "audiocheck" in p.stdout
        print("[%s] --version        %.2fs rc=%d" % ("OK" if ok else "FAIL", dt,
                                                    p.returncode))
        if not ok:
            fails.append("--version rc=%d stderr=%s" % (p.returncode,
                                                       p.stderr[:300]))
        if i == 0:
            first = dt

    p, dt = _run(exe, ["--doctor"])
    ok = p.returncode == 0 and "numpy" in p.stdout and "OK" in p.stdout
    print("[%s] --doctor         %.2fs rc=%d" % ("OK" if ok else "FAIL", dt,
                                                p.returncode))
    if not ok:
        fails.append("--doctor rc=%d out=%s err=%s"
                     % (p.returncode, p.stdout[:400], p.stderr[:300]))
    else:
        for must in ("scipy", "soundfile", "mutagen", "PySide6", "matplotlib"):
            if must not in p.stdout:
                fails.append("--doctor 输出缺 %s（可能被 exclude 砍掉了）" % must)

    # ---- 层 2：真实分析（覆盖分析器动态发现）----
    with tempfile.TemporaryDirectory(prefix="ac_smoke_") as d:
        wav = os.path.join(d, "tone.wav")
        js = os.path.join(d, "out.json")
        _make_wav(wav)
        p, dt = _run(exe, [wav, "--json", js, "--quiet"])
        ok = p.returncode == 0 and os.path.exists(js)
        print("[%s] 完整分析         %.2fs rc=%d" % ("OK" if ok else "FAIL", dt,
                                                    p.returncode))
        if not ok:
            fails.append("分析 rc=%d err=%s" % (p.returncode, p.stderr[:400]))
        else:
            import json
            with open(js, encoding="utf-8") as f:
                data = json.load(f)
            res = data.get("results", data if isinstance(data, list) else [data])
            if not res:
                fails.append("JSON 里没有 results")
            print("     → JSON %d 条结果，字段 %d 个"
                  % (len(res), len(res[0]) if res else 0))

    # ---- 层 3：GUI 通路（覆盖 PySide6 插件）----
    # ⚠ **不能用 `-c`**：发布包是 PyInstaller 的 exe，不是 Python 解释器——
    # `-c` 会被 argparse 当未知参数拒绝（实测 rc=2）。
    # 正确做法是走产品自己的 GUI 入口：`--edit`（编辑工作站）。
    # 它会阻塞在 `app.exec()`，所以**活到超时就是成功**：说明 Qt 插件、
    # 字体、主题、窗口构造全部走通（崩溃的话会立刻退出，rc≠-9）。
    p, dt = _run(exe, ["--edit"], timeout=12)
    ok = p.returncode == -9                     # `_run` 用 -9 标记超时
    note = "活到超时（窗口正常）" if ok else "rc=%d err=%s" % (
        p.returncode, (p.stderr or "")[:400])
    print("[%s] GUI 通路         %.2fs  %s" % ("OK" if ok else "FAIL", dt, note))
    if not ok:
        fails.append("GUI %s" % note)

    print()
    if fails:
        print("失败项（%d）：" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1
    print("全部通过。首次启动 %.2fs。" % first)
    return 0


if __name__ == "__main__":
    sys.exit(main())
