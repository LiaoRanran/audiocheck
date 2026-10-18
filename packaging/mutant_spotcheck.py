#!/usr/bin/env python3
"""mutant_spotcheck：对**已知清单**里的变异体逐条手工核对（比整轮探针快得多）。

    python packaging/mutant_spotcheck.py

什么时候用它
------------
`mutation_probe.py` 是**通用**工具，但它的默认上限（`--max-seconds 300 --max-mutants 40`）
让"改完测试立刻复核"变得不现实；而**当你手里已经有一张"存活清单"**（比如上一轮探针报的
12 条判定层边界翻转）时，只需要"把每条改回去、看新判据抓不抓得住"——就是这个脚本。

它做的事与探针**完全一样**（复制 `src/` + `tests/` 到临时工作区、按**行号**改一处、
`PYTHONPATH` 指向工作区以免 import 到真实仓库的代码、跑定向测试子集），只是候选表是**手写**的：
  * 按**行号**定位 → 同一段文本在文件里出现两次也不会改错（`verdict.py` 里
    `if k >= th.lossy:` 就出现两次，`and` 版 `conf < th.conf_low` 也是两处）；
  * 改完立刻还原，工作区用完即删，**不碰真实仓库**。

⚠ 表里的行号是**快照**：源文件一改动就漂（探针的 `EQUIVALENT` 白名单同病）。复用时先
`git diff` 核一遍；行号对不上会**直接报错退出**（不会静默改错地方）。
"""
from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent

#: 定向测试子集：改哪层就跑哪层的判据（越窄越快，但必须覆盖被改代码的**消费者**）
TEST = "tests/test_judgment_boundaries.py"

#: (文件, 行号（1 起）, 原文, 变异后)。当前这张表 = **2026-10-18 判定层那 12 条**，
#: 全部来自上一轮完整探针（`packaging/mutation_probe.py`）的存活清单。
CASES = [
    ("verdict.py", 101, "if k >= th.lossy:", "if k > th.lossy:"),
    ("verdict.py", 114, "if k >= th.up_44:", "if k > th.up_44:"),
    ("verdict.py", 116, "if k >= th.ltd:", "if k > th.ltd:"),
    ("verdict.py", 118, "if k >= th.lossy:", "if k > th.lossy:"),
    ("verdict.py", 124, "if conf is not None and conf < th.conf_low:",
     "if conf is not None or conf < th.conf_low:"),
    ("causes.py", 49, "slope >= th.brick_db", "slope > th.brick_db"),
    ("causes.py", 50, "slope < th.brick_flat_db_per_oct", "slope <= th.brick_flat_db_per_oct"),
    ("causes.py", 64, "flat >= th.noise_like_flatness", "flat > th.noise_like_flatness"),
    ("causes.py", 65, "low_pct >= th.vinyl_rumble_pct", "low_pct > th.vinyl_rumble_pct"),
    ("causes.py", 67, "and slope < th.brick_flat_db_per_oct",
     "and slope <= th.brick_flat_db_per_oct"),
    ("causes.py", 92, "if fs > th.upsample_fs", "if fs >= th.upsample_fs"),
    ("causes.py", 132, "if clip > th.clip_pct", "if clip >= th.clip_pct"),
]


def main() -> int:
    ws = pathlib.Path(tempfile.mkdtemp(prefix="ac_spotcheck_"))
    shutil.copytree(ROOT / "src", ws / "src")
    shutil.copytree(ROOT / "tests", ws / "tests")
    (ws / "pyproject.toml").write_text("[tool.pytest.ini_options]\naddopts=''\n", encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(ws / "src")}
    killed = 0
    try:
        for name, lineno, old, new in CASES:
            rel = "src/audiocheck/core/analyzers/" + name
            path = ws / rel
            orig = path.read_text(encoding="utf-8")
            lines = orig.splitlines(keepends=True)
            if old not in lines[lineno - 1]:
                print("!! 行号对不上（%s:%d 不是 %r）：%r" % (rel, lineno, old, lines[lineno - 1]))
                return 2
            lines[lineno - 1] = lines[lineno - 1].replace(old, new)
            path.write_text("".join(lines), encoding="utf-8")
            p = subprocess.run([sys.executable, "-m", "pytest", "-o", "addopts=", "-q", "-x", TEST],
                               cwd=str(ws), env=env, capture_output=True, text=True)
            path.write_text(orig, encoding="utf-8")
            caught = p.returncode != 0
            killed += caught
            print("%-11s:%-4d %-40s → %s"
                  % (name, lineno, old.strip(), "**杀死**" if caught else "存活！！"))
        print("\n结果：%d/%d 被杀死" % (killed, len(CASES)))
        if killed != len(CASES):
            print("存活的那些 = **测试缺失**（不是等价变异体），回去补判据。")
    finally:
        shutil.rmtree(ws, ignore_errors=True)
    return 0 if killed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
