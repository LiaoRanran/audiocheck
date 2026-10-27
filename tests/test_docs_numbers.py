"""文档数字对账（`packaging/docs_number_audit.py`）的门禁化。

项目纪律："引用的数字必须能从仓库复算"——此前靠人工抽查，现在注册表驱动自动核对。
本测试以子进程跑对账器（隔离 sys.path/缓冲），rc=0 即全过。
"""
from __future__ import annotations

import os
import subprocess
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_docs_numbers_recompute_clean():
    p = subprocess.run([sys.executable, os.path.join(_ROOT, "packaging",
                                                      "docs_number_audit.py"),
                        "--strict"],
                       capture_output=True, text=True, timeout=600, cwd=_ROOT)
    assert p.returncode == 0, p.stdout + p.stderr
