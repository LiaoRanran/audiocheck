"""可复算演示脚本的门禁（`examples/verify_audit.py`）——示例即判据。

演示脚本是 README/发布页的第一眼验证：它必须能跑通，且**摘要一致性成立**。
"""
from __future__ import annotations

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_verify_audit_demo_reports_reproducible():
    p = subprocess.run([sys.executable, os.path.join(_ROOT, "examples", "verify_audit.py")],
                       capture_output=True, text=True, timeout=600, cwd=_ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
    assert "可复算成立" in p.stdout
