#!/usr/bin/env python3
"""restore_pipeline：**脚本化使用 audiocheck 编辑内核**的完整示例（P5 生态起点）。

端到端演示非破坏修复的全流程——全部走公开 API，没有一个内部快捷方式：

1. 合成一段"劣化"素材（50 Hz 嗡 + 削波 + 咔哒；种子固定 ⇒ 全程可复算）；
2. 建**修复链**（去咔哒 → 去削波 → 去嗡——顺序由 `render.PHASE` 规范，不必手工排）；
3. 存成修复链预设（JSON，可复算、可套到别的文件）；
4. 渲染到 WAV，落**审计 JSON** 与**人读 Markdown 报告**；
5. 打印每步实测（改了什么 / 依据什么 / 结果如何）。

用法::

    python examples/restore_pipeline.py [--out DIR]

产物（默认 `examples/out/`）：`degraded.wav` / `restored.wav` / `chain.json` /
`audit.json` / `report.md`。本脚本由 `tests/test_scripting_example.py` 以子进程
运行——**示例即判据**：脚本坏了 = 判据红。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from audiocheck.core.edit import (  # noqa: E402
    DeclickOp,
    DeclipOp,
    DehumOp,
    Project,
    render,
    save_chain,
)
from audiocheck.core.edit.report import write_report  # noqa: E402

FS = 48000


def synth_degraded(path: str) -> None:
    """合成劣化素材：0.5 s 静音（去嗡学谱用）+ 3.5 s 内容（嗡/削波/咔哒三害俱全）。"""
    t = np.arange(int(4.0 * FS)) / FS
    content = 0.4 * np.sin(2 * np.pi * 440.0 * t) + 0.2 * np.sin(2 * np.pi * 3000.0 * t)
    hum = 0.15 * np.sin(2 * np.pi * 50.0 * t) + 0.07 * np.sin(2 * np.pi * 100.0 * t)
    x = content + hum
    x[: int(0.5 * FS)] = 0.0                       # 开头留一段"纯嗡"（学谱输入）
    # 削波：电平超过 ±0.7 的样本钳到 ±0.7（真实录音的过载形态）
    x = np.clip(x, -0.7, 0.7)
    # 咔哒：位置/幅度已知的单采样脉冲
    for pos in (1.2345, 2.0001, 2.8888):
        x[int(pos * FS)] += 0.5
    sf.write(path, x.astype(np.float32), FS, subtype="FLOAT")


def main() -> int:
    ap = argparse.ArgumentParser(description="audiocheck 修复链示例")
    ap.add_argument("--out", default=os.path.join(_ROOT, "examples", "out"))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)

    degraded = os.path.join(a.out, "degraded.wav")
    restored = os.path.join(a.out, "restored.wav")
    chain_path = os.path.join(a.out, "chain.json")
    audit_path = os.path.join(a.out, "audit.json")
    report_path = os.path.join(a.out, "report.md")
    synth_degraded(degraded)
    print("① 劣化素材已合成：%s（嗡 50/100 Hz + 削波 ±0.7 + 3 个咔哒）" % degraded)

    # 修复链：声明顺序随意——渲染按 render.PHASE 的规范顺序执行（咔哒→削波→嗡）。
    chain = [
        DehumOp(0.0, 4.0, 50.0),                   # 嗡：基频 50，谐波 1/2/3 默认
        DeclipOp(0.5, 4.0, 21000.0),               # 削波：带宽先验给足（内容 3 kHz）
        DeclickOp(0.5, 4.0),                       # 咔哒：阈值/守卫用标定默认
    ]
    save_chain(chain_path, chain)
    print("② 修复链已存：%s（%d 个操作，不绑源——可套到任何文件）"
          % (chain_path, len(chain)))

    audit = render(Project(degraded, list(chain)), restored, overwrite=True)
    print("③ 渲染完成：%s" % restored)
    for i, rec in enumerate(audit["ops"], 1):
        changed = rec.get("changed_samples") or rec.get("replaced_samples")
        extra = ""
        if rec["kind"] == "dehum":
            sup = "/".join("—" if v is None else "%.1f" % v for v in rec["suppression_db"])
            extra = "｜各谐波压制 %s dB" % sup
        elif rec["kind"] == "declip":
            extra = "｜饱和 %d 样本（电平 %s）" % (
                rec["saturated_samples"], rec["ceiling_source"])
        elif rec["kind"] == "declick":
            extra = "｜检出 %d / 被守卫挡下 %d 段" % (
                rec["detected_samples"], rec["rejected_runs"])
        print("   %d. %s ｜ 实改 %s%s" % (i, rec.get("describe", rec["kind"]),
                                          changed, extra))

    with open(audit_path, "w", encoding="utf-8") as fh:
        json.dump(audit, fh, ensure_ascii=False, indent=2)
    write_report(audit, report_path)
    print("④ 审计：%s ｜ 报告：%s" % (audit_path, report_path))
    print("   （人读报告含每个操作的算法口径与**实测**数值；'未测'= 该量具在此不可用）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
