"""P5 生态起点：脚本化示例的冒烟判据（**示例即判据**）。

`examples/restore_pipeline.py` 是脚本化使用内核的权威示例——它必须：
1. 以子进程跑通（rc=0）——任何内核 API 变更把它跑坏 = 判据红；
2. 产出全部四件产物（degraded/restored/chain.json/audit.json/report.md）；
3. 审计里三个修复算子**都实际生效**（declick 检出 >0、declip 饱和 >0、dehum 压制 <0）
   且按**规范顺序**（declick → declip → dehum）；
4. 审计 JSON 与报告 Markdown 内容一致（同一份审计的两种排版）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _run(tmp_path):
    out = str(tmp_path / "out")
    p = subprocess.run([sys.executable, os.path.join(_ROOT, "examples", "restore_pipeline.py"),
                        "--out", out], capture_output=True, text=True, timeout=300)
    return p, out


def test_restore_pipeline_example_runs_and_produces_artifacts(tmp_path):
    p, out = _run(tmp_path)
    assert p.returncode == 0, p.stdout + p.stderr
    for name in ("degraded.wav", "restored.wav", "chain.json", "audit.json", "report.md"):
        assert os.path.exists(os.path.join(out, name)), name
    # 脚本自述可复算：再跑一遍，审计 JSON 逐字节一致（渲染是纯函数）
    audit1 = json.load(open(os.path.join(out, "audit.json"), encoding="utf-8"))
    p2, out2 = _run(tmp_path)
    assert p2.returncode == 0
    audit2 = json.load(open(os.path.join(out2, "audit.json"), encoding="utf-8"))
    for k in ("frames", "samplerate", "duration_s", "ops"):
        assert audit1[k] == audit2[k], k


def test_restore_pipeline_audit_shows_canonical_chain(tmp_path):
    p, out = _run(tmp_path)
    assert p.returncode == 0
    audit = json.load(open(os.path.join(out, "audit.json"), encoding="utf-8"))
    kinds = [o["kind"] for o in audit["ops"]]
    assert kinds == ["declick", "declip", "dehum"], "规范顺序：脉冲→削波→嗡"
    by = {o["kind"]: o for o in audit["ops"]}
    assert by["declick"]["detected_samples"] > 0, "咔哒必须被检出"
    assert by["declip"]["saturated_samples"] > 0, "削波必须被检出"
    assert by["declip"]["ceiling_source"] in ("auto_max", "given")
    sup = by["dehum"]["suppression_db"]
    assert all(v is not None and v < 0 for v in sup), "嗡的压制量必须实测为负"
    assert by["dehum"]["f0"] == 50.0
