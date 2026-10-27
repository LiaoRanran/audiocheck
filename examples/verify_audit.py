#!/usr/bin/env python3
"""verify_audit：**30 秒可复算演示**——证明"同一份修复 ⇒ 同一份审计摘要"。

用途：README / 发布页 / 给委托方的第一眼验证。跑一次就能看清：

1. 合成一段劣化素材（种子固定 ⇒ 可复算）；
2. 跑一遍修复链，取审计摘要与输出哈希；
3. **再跑一遍**（换个目标路径、换个时刻）；
4. 断言两个摘要**完全相同**——`audit_digest` 不随路径/时刻漂移。

    python examples/verify_audit.py

退出码 0 = 可复算成立；1 = 摘要不一致（实现坏了）。
"""
from __future__ import annotations

import os
import sys
import tempfile

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import numpy as np  # noqa: E402
import soundfile as sf  # noqa: E402

from audiocheck.core.edit import DeclickOp, DeclipOp, DehumOp, Project, render  # noqa: E402
from audiocheck.core.edit.report import write_report  # noqa: E402

FS = 48000


def synth(path: str) -> None:
    """劣化素材：50 Hz 嗡 + 削波 + 3 个咔哒（与 examples/restore_pipeline 同源）。"""
    t = np.arange(int(2.0 * FS)) / FS
    x = 0.4 * np.sin(2 * np.pi * 440.0 * t) + 0.15 * np.sin(2 * np.pi * 50.0 * t)
    x = np.clip(x, -0.7, 0.7)
    for pos in (0.5, 1.0, 1.5):
        x[int(pos * FS)] += 0.5
    sf.write(path, x.astype(np.float32), FS, subtype="FLOAT")


def main() -> int:
    work = tempfile.mkdtemp(prefix="ac_verify_")
    src = os.path.join(work, "degraded.wav")
    synth(src)
    chain = [DehumOp(0.2, 2.0, 50.0), DeclipOp(0.2, 2.0, 21000.0),
             DeclickOp(0.2, 2.0)]

    print("audiocheck 可复算演示\n" + "=" * 52)
    audits = []
    for run, dest in ((1, "a.wav"), (2, "b.wav")):
        out = os.path.join(work, dest)
        audit = render(Project(src, list(chain)), out, overwrite=True)
        audits.append(audit)
        print("第 %d 次：目标 %s" % (run, dest))
        print("  审计摘要 audit_digest : %s" % audit["audit_digest"])
        print("  输出哈希 dest_sha256  : %s" % audit["dest_sha256"])
        print("  实现自证 kernel_digest: %s" % audit["kernel_digest"][:32] + "…")

    same_digest = audits[0]["audit_digest"] == audits[1]["audit_digest"]
    same_kernel = audits[0]["kernel_digest"] == audits[1]["kernel_digest"]
    same_bytes = audits[0]["dest_sha256"] == audits[1]["dest_sha256"]

    print("\n" + "=" * 52)
    print("审计摘要一致（不随路径/时刻漂移）：%s" % ("是" if same_digest else "**否**"))
    print("渲染实现一致（同一份代码）：      %s" % ("是" if same_kernel else "**否**"))
    print("输出字节一致（除 PEAK 时间戳）：  %s" % ("是" if same_bytes else "否（允许）"))

    report = os.path.join(work, "report.md")
    write_report(audits[0], report)
    print("\n人读报告已生成：%s" % report)
    print("（每个数值标〔实测〕〔推导〕〔设定〕——量具可靠性一眼可见）")

    ok = same_digest and same_kernel
    print("\n结论：%s" % ("✅ 可复算成立" if ok else "❌ 摘要不一致——实现有问题"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
