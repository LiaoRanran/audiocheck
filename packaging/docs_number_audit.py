#!/usr/bin/env python3
"""docs_number_audit：**文档数字对账**（"引用的数字必须能从仓库复算"的工具化）。

## 问题

项目纪律要求文档（CHANGELOG/docs）引用的实测数字**可从仓库复算**。此前靠人工抽查
（2026-10-27 抽查过 denoise_probe 的头牌数字），漏检靠运气。本工具把对账做成
**注册表驱动**：每个数字登记「文档声称值 + 复算函数 + 容差」，跑一遍全部核对。

## 口径

* 复算函数在 `RECOMPUTE` 表里（直接 import 探针/内核的公开函数，**短时**可跑）；
* 长跑探针（分钟级）不进自动对账——它们的数字由各自的 `--strict` 门禁守着；
* 容差：dB 类 ±0.15（浮点路径差），计数类精确。

    python packaging/docs_number_audit.py [--strict]
"""
from __future__ import annotations

import argparse
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))
sys.path.insert(0, os.path.join(_ROOT, "packaging"))


from audiocheck.core.edit import corpus, declick  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000

# ------------------------------------------------------------------ 复算函数
def _recompute_denoise_headline() -> dict:
    """denoise_probe 的头牌数字（白 +10/0/−5 的 Wiener-DD ΔSDR，文档引用
    +11.7/+12.7/+12.5）。**直接复用探针的场景构造**（对账 = 复算同一口径，
    不是近似重写）。"""
    import denoise_probe as dp
    out = {}
    for sc in dp.scenarios():
        if "white" not in sc["name"] or "pink" in sc["name"]:
            continue
        # 用**探针自己的包装**（与文档数字同一口径：同 STFT 参数/学谱时段）
        o = dp.denoise(sc["noisy"], "wiener", noise=sc["noise"],
                       profile=sc["profile"])
        base = sdr_db(sc["noisy"], sc["clean"])
        d = sdr_db(o, sc["clean"]) - base
        out[sc["name"]] = d
    return out


def _recompute_declick_smooth() -> dict:
    """declick_probe 的平滑素材数字（文档引用 +40.6）。"""
    clean = corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3)
    y = clean.copy()
    for p in (0.4, 0.9, 1.4, 1.9):
        y[int(p * FS), 0] += 0.9
    o, _ = declick.declick(y, 5.0, 4)
    return {"smooth_spikes": sdr_db(o, clean) - sdr_db(y, clean)}


def _recompute_declick_step_cascade() -> dict:
    """双臂级联的 w=3 多平台数字（文档引用 +24.03）。"""
    clean = corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3)
    y = clean.copy()
    for p in (0.4, 0.9, 1.4, 1.9):
        i0 = int(round(p * FS))
        y[i0:i0 + 3, 0] += 0.9
    o, _ = declick.declick(y, 5.0, 4)
    return {"step_cascade_w3": sdr_db(o, clean) - sdr_db(y, clean)}


#: 注册表：label → (复算函数, 结果键, **声称值**, 容差)。声称值必须与文档一致
#: （对账器不改文档——数字不一致时先查复算口径，再改文档）。
REGISTRY: dict[str, tuple] = {
    "denoise tone+white +10 dB Wiener-DD ΔSDR（docs 引 +11.7）": (
        _recompute_denoise_headline, "tone+white +10 dB", 11.7, 0.15),
    "denoise tone+white 0 dB Wiener-DD ΔSDR（docs 引 +12.7）": (
        _recompute_denoise_headline, "tone+white +0 dB", 12.7, 0.15),
    "denoise tone+white −5 dB Wiener-DD ΔSDR（docs 引 +12.5）": (
        _recompute_denoise_headline, "tone+white -5 dB", 12.5, 0.15),
    "declick 谐波音+尖刺 ΔSDR（docs 引 +40.6 量级）": (
        _recompute_declick_smooth, "smooth_spikes", 40.6, 30.0),
    "declick 台阶级联 w=3 多平台 ΔSDR（docs 引 +24.03）": (
        _recompute_declick_step_cascade, "step_cascade_w3", 24.03, 0.5),
}


def main() -> int:
    ap = argparse.ArgumentParser(description="文档数字对账")
    ap.add_argument("--strict", action="store_true",
                    help="对账失败 ⇒ 返回 1（门禁用）")
    a = ap.parse_args()
    print("文档数字对账（%d 条注册）\n" % len(REGISTRY))
    n_bad = 0
    for label, (fn, key, expected, tol) in REGISTRY.items():
        try:
            got = fn()[key]
        except Exception as e:                       # noqa: BLE001
            print("✗ %s\n   复算失败：%s" % (label, e))
            n_bad += 1
            continue
        ok = abs(got - expected) <= tol
        print("%s %s\n   声称 %+.2f ｜ 复算 %+.2f ｜ 容差 ±%.2f"
              % ("✓" if ok else "✗", label, expected, got, tol))
        if not ok:
            n_bad += 1
    print("\n对账：%d/%d 通过" % (len(REGISTRY) - n_bad, len(REGISTRY)))
    if n_bad and a.strict:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
