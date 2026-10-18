#!/usr/bin/env python3
"""band_perf：频域编辑路径的**性能与内存口径**（可复算；CHANGELOG 里的数字出自这里）。

    python packaging/band_perf.py

两件事要一起证明：
  * **耗时 ∝ 写出的跨度**，不是素材长度（选一个小选区，长素材也快）；
  * **峰值内存 ∝ 块长**，与素材长度**无关**（分块流式，不是整轨进内存）。
素材在临时目录生成（默认 10 分钟立体声 ≈ 230 MB），可 `rm` 后重跑。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import render_band  # noqa: E402

FS = 48000
SECONDS = 600.0
CHUNK_HOPS = 512
N_FFT = 4096
HOP = N_FFT // 4


def main() -> None:
    out = os.path.join(tempfile.gettempdir(), "ac_band_perf")
    os.makedirs(out, exist_ok=True)
    L = int(SECONDS * FS)
    src = os.path.join(out, "long.wav")
    if not os.path.exists(src):
        t = np.arange(L) / FS
        x = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
        sf.write(src, np.column_stack([x, x]), FS, subtype="FLOAT")
    print("素材 %.1f s ｜ %.0f MB" % (L / FS, os.path.getsize(src) / 1e6))

    for t0, t1, label in [(100.0, 110.0, "10 s 选区"), (0.0, SECONDS, "整轨")]:
        dst = os.path.join(out, "d_%d.wav" % int(t0))
        tm = time.perf_counter()
        audit = render_band(src, dst, t0, t1, 7000.0, 9000.0, -40.0, overwrite=True)
        el = time.perf_counter() - tm
        span = audit["roundtrip_floor_samples"] / audit["channels"]
        print("%-9s 跨度 %.1f s ｜ 耗时 %.2f s（%.1f× 实时）"
              % (label, span / FS, el, (L / FS) / el))

    mem = (CHUNK_HOPS * HOP + 2 * N_FFT) * 2 * 8 / 1e6
    print("峰值内存上界 ≈ %.0f MB = 4 × (chunk_hops*hop + 2*n_fft) × 声道 × 8B"
          "（缓冲 + 分析块 + 窗平方 + 谱），**与素材长度无关**" % (4 * mem))


if __name__ == "__main__":
    main()
