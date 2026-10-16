#!/usr/bin/env python3
"""kernel_bench：**内核逐分析器耗时基准**（"先量后改"那把尺）。

## 为什么入库

CHANGELOG 里那些"35.08 → 22.27 s"式的数字必须**能从仓库复算**，否则它只是一句
"我测过"（本项目最忌讳的形式）。这个基准自己合成语料（**不依赖任何外部曲库**），
逐分析器给时间；也可以直接喂真实文件。

## 口径（先读，免得比错）

* **绝对值随机器与解释器变**：本机是**自由线程 3.14**（`sys._is_gil_enabled()` False，
  纯 Python 比同版本 GIL 构建慢约 70%）⇒ **跨机比较没有意义，只有前后对比有意义**。
  因此这里**没有 `--strict`**：它是尺，不是门禁。
* 语料是白噪 + 瞬态（**不是音乐**）：目的是让各分析器都有活干、可复算，
  不是"代表真实内容"。真实曲库上的分布见 `docs/real_library.md`。
* 计时口径：**逐分析器**（`registry.schedule()` 顺序），不含进程启动与导入。

    python packaging/kernel_bench.py                      # 合成 44.1k/16 与 96k/24
    python packaging/kernel_bench.py --seconds 180 --json out/bench.json
    python packaging/kernel_bench.py <某个音频文件> …      # 直接量真实文件
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core import context as C  # noqa: E402
from audiocheck.core.registry import schedule  # noqa: E402


def _synth(sr: int, seconds: float, bits: str, path: str) -> str:
    """白噪 + 三个瞬态：让 basic/spectrum/loudness/timeline/stereo 都有活干。"""
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(20261017)
    x = (rng.standard_normal(int(sr * seconds)) * 0.25).astype(np.float32)
    for i in range(1, 4):
        a = int(sr * seconds * i / 4)
        x[a:a + 64] = (0.95 * np.sin(2 * np.pi * (sr / 6) * np.arange(64) / sr)).astype(np.float32)
    sf.write(path, np.column_stack([x, x]), sr, subtype=bits)
    return path


def run_one(path: str) -> dict:
    """逐分析器计时（顺序与真实运行一致）。"""
    ctx = C.AnalysisContext(path=path, thresholds=Thresholds(), deep=True)
    ctx.seed_metrics()
    ctx.result["errors"] = []
    rows, total = [], 0.0
    for a in schedule():
        t0 = time.perf_counter()
        try:
            out = a.run(ctx)
            if out:
                ctx.result.update(out)
        except Exception as exc:                            # noqa: BLE001
            ctx.result["errors"].append("%s: %s" % (a.key, exc))
        dt = time.perf_counter() - t0
        total += dt
        rows.append((a.key, dt))
    return {"file": os.path.basename(path), "seconds": round(ctx.seconds, 1),
            "total": round(total, 3), "rows": rows,
            "errors": list(ctx.result["errors"])}


def main() -> int:
    ap = argparse.ArgumentParser(description="内核逐分析器耗时基准")
    ap.add_argument("files", nargs="*", help="要量的音频文件（缺省则合成语料）")
    ap.add_argument("--seconds", type=float, default=120.0, help="合成语料时长")
    ap.add_argument("--json", help="把结果写到 JSON（供前后对比）")
    a = ap.parse_args()

    cases = []
    if a.files:
        cases = [("真实文件", f) for f in a.files]
    else:
        d = tempfile.mkdtemp(prefix="ac_bench_")
        cases = [("合成 44.1k/16", _synth(44100, a.seconds, "PCM_16", os.path.join(d, "44k.wav"))),
                 ("合成 96k/24", _synth(96000, a.seconds, "PCM_24", os.path.join(d, "96k.wav")))]

    out = []
    print("内核基准（本机 %s 核；绝对值随机器/解释器变，**只有前后对比有意义**）"
          % (os.cpu_count() or 1))
    for label, path in cases:
        r = run_one(path)
        out.append({"label": label, **r})
        print("\n== %s  %s（%.1f s）==" % (label, r["file"], r["seconds"]))
        for key, dt in r["rows"]:
            print("   %-14s %8.3f s" % (key, dt))
        print("   %-14s %8.3f s   %s" % ("**合计**", r["total"],
                                         ("（告警：%s）" % r["errors"]) if r["errors"] else ""))
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        print("\n已写入 %s" % a.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
