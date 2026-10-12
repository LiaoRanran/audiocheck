"""CLI 全路径冒烟：逐个开关跑一遍，把报错、非零退出、空输出都揪出来。

跑得慢的（--plot / --jobs）也包含，因为"没人跑过的路径"最容易坏。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile

FILE = r"C:\Temp\acdemo\sayonara_no_natsu.flac"
os.makedirs("out", exist_ok=True)
TMP = tempfile.mkdtemp(prefix="acsmoke_")

CASES = [
    ("版本", ["--version"], False),
    ("列出分析器", ["--list-analyzers"], False),
    ("环境自检", ["--doctor"], False),
    ("单文件分析", [FILE], False),
    ("紧凑输出", [FILE, "--quiet"], False),
    ("浅模式", [FILE, "--shallow"], False),
    ("导出 JSON", [FILE, "--json", os.path.join(TMP, "r.json")], True),
    ("导出 CSV", [FILE, "--csv", os.path.join(TMP, "r.csv")], True),
    ("只跑指定分析器", [FILE, "--only", "basic,spectrum"], False),
    ("并行（自动）", [FILE, "--jobs", "0"], False),
    ("缓存往返", [FILE, "--cache", os.path.join(TMP, "c.json")], True),
    ("出图", [FILE, "--plot", os.path.join(TMP, "plots")], True),
    ("目录分析", [os.path.dirname(FILE)], False),
    ("清单文件", None, True),          # 动态生成
    ("坏路径（应优雅报错）", [os.path.join(TMP, "不存在.flac")], False),
    ("坏配置（应优雅报错）", [FILE, "--config", os.path.join(TMP, "bad.json")], True),
]

# 生成清单文件
lst = os.path.join(TMP, "list.txt")
with open(lst, "w", encoding="utf-8") as f:
    f.write("# 注释行\n%s\n" % FILE)
CASES[13] = ("清单文件", ["--list", lst], False)

# 生成坏配置
bad = os.path.join(TMP, "bad.json")
with open(bad, "w", encoding="utf-8") as f:
    f.write('{"cd_full": "不是数字"}')
CASES[15] = ("坏配置（应优雅报错）", [FILE, "--config", bad], False)

fails = []
for name, args, _needs_out in CASES:
    p = subprocess.run([sys.executable, "-m", "audiocheck", *args],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (p.stdout or "") + (p.stderr or "")
    tb = "Traceback" in out
    # 坏路径/坏配置这类**应当**报错的用例，只看有没有未捕获回溯
    expect_err = "坏" in name
    ok = (not tb) and (p.returncode == 0 or expect_err)
    mark = "OK  " if ok else "FAIL"
    print("%s %-22s rc=%-3s 输出 %4d 字符%s"
          % (mark, name, p.returncode, len(out), "  << 有回溯" if tb else ""))
    if not ok:
        fails.append((name, args, p.returncode, out))

print()
print("=== 失败明细 ===")
for name, args, rc, out in fails:
    print("\n--- %s  (rc=%s) ---" % (name, rc))
    print("  argv: %s" % " ".join(args))
    for line in out.splitlines()[-14:]:
        print("  |", line)

# 检查产物
print()
print("=== 产物检查 ===")
for f in ("r.json", "r.csv", "c.json"):
    p = os.path.join(TMP, f)
    print("  %-10s %s" % (f, ("%d 字节" % os.path.getsize(p)) if os.path.exists(p) else "**缺失**"))
pd = os.path.join(TMP, "plots")
if os.path.isdir(pd):
    print("  plots/     %d 个文件" % len(os.listdir(pd)))
else:
    print("  plots/     **缺失**")

shutil.rmtree(TMP, ignore_errors=True)
print()
print("失败 %d / %d" % (len(fails), len(CASES)))
