#!/usr/bin/env python3
"""op_order_probe：**复合操作的顺序到底要不要立判据**（先量，再决定要不要加机制）。

## 问题

现在 `render()` 对"同栈混用不同算子"是**一律拒绝**的（"顺序语义未立判据，宁可不做"）。
但拒绝也是一种机制：用户没法一条命令跑"先去削波、再去咔哒、再去嗡"。
要不要放开？先看**顺序是否真的改变结果**——在**有真值**的合成文件上量。

## 素材

`clean` → 叠三种真值已知的缺陷（削波 / 稀疏咔哒 / 50 Hz 嗡），得到 `faulty`。
三种修复算子各跑一次：`DeclipOp(0, T, bw=18k)` / `DeclickOp(0, T)` / `DehumOp(0, T, 50)`。
**六种排列**全部渲染一遍，同一把尺子：

* `sdr_db(out, clean)` —— 离真值多远（主判据）；
* 50 Hz 带内增益（嗡压了多少）、咔哒位置上的残差（尖峰漏没漏）、
  饱和样本数（削波复原了多少）。

    python packaging/op_order_probe.py
"""
from __future__ import annotations

import itertools
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, "src")
from audiocheck.core.edit import (  # noqa: E402
    DeclickOp,
    DeclipOp,
    DehumOp,
    Project,
    corpus,  # noqa: E402
    render,
)
from audiocheck.core.edit.declick import residual  # noqa: E402
from audiocheck.core.edit.metrics import band_gain_db, sdr_db  # noqa: E402

FS = 48000
T = 2.0
N = int(T * FS)


def build(seed: int = 11):
    clean = corpus.band_limited_noise(T, FS, 18000.0, amp=0.5, seed=seed)
    # 削波（相对峰值 0.3）：真值 = clean
    ceiling = 0.3 * float(np.abs(clean).max())
    y = np.clip(clean, -ceiling, ceiling)
    # 稀疏咔哒：位置已知
    rng = np.random.default_rng(seed)
    pos = np.sort(rng.uniform(0.05, T - 0.05, 60))
    idx = [int(round(p * FS)) for p in pos]
    for i in idx:
        y[i, 0] += 0.6
    # 50 Hz 嗡（−30 dB）
    t = np.arange(N) / FS
    h = sum(np.sin(2 * np.pi * 50 * k * t + 0.7 * k) / k for k in (1, 2, 3))
    h *= (10 ** (-30 / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    y = y + h[:, None]
    return clean, y, ceiling, idx


def chain(ops, faulty_path, tag):
    """依次渲染（每步新文件），返回最终输出路径与审计列表。"""
    tmp = tempfile.mkdtemp(prefix="order_")
    cur = faulty_path
    audits = []
    for i, op in enumerate(ops):
        dst = os.path.join(tmp, "%s_%d.wav" % (tag, i))
        audits.append(render(Project(cur, [op]), dst, subtype="FLOAT"))
        cur = dst
    return cur, audits


clean, faulty, ceiling, click_idx = build()
tmp = tempfile.mkdtemp(prefix="order_src_")
src = os.path.join(tmp, "faulty.wav")
sf.write(src, faulty, FS, subtype="FLOAT")

OPS = {
    "declip": lambda: DeclipOp(0.0, T, bandwidth=18000.0, ceiling=ceiling),
    "declick": lambda: DeclickOp(0.0, T),
    "dehum": lambda: DehumOp(0.0, T, 50.0),
}
faulty_ref = faulty

print("素材：带限白噪 + 削波(%.3f) + 60 个咔哒 + 50Hz 嗡(−30 dB)" % ceiling)
print("（SDR 越高越好；嗡 = 50Hz 带内增益 dB；咔哒残差 = 已知位置上的 |r| 最大值）")
print(" %-22s %8s %8s %8s" % ("顺序", "SDR dB", "嗡 dB", "咔哒残差"))
rows = []
for perm in itertools.permutations(OPS):
    ops = [OPS[k]() for k in perm]
    out_path, audits = chain(ops, src, "_".join(perm))
    out, _ = sf.read(out_path, dtype="float64", always_2d=True)
    sdr = sdr_db(out, clean)
    hum = band_gain_db(faulty_ref, out, FS, 45.0, 55.0)
    r = residual(out)
    click_res = float(np.max(np.abs(r[click_idx]))) if click_idx else 0.0
    rows.append((perm, sdr, hum, click_res))
    print(" %-22s %8.2f %8.2f %8.4f" % ("→".join(perm), sdr, hum, click_res))

sdr_vals = [r[1] for r in rows]
print("\n SDR 极差 = %.2f dB（最好 %s ｜ 最差 %s）"
      % (max(sdr_vals) - min(sdr_vals),
         "→".join(min(rows, key=lambda r: -r[1])[0]),
         "→".join(max(rows, key=lambda r: -r[1])[0])))
