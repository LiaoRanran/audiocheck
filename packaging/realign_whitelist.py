#!/usr/bin/env python3
"""realign_whitelist：**等价体白名单的行号自动对齐器**。

## 为什么需要它（这是本项目第 4 次遇到）

`packaging/mutation_probe.py` 的 `EQUIVALENT` 表用
`文件:行号:故障记号` 做键。源码一改（哪怕只是加几行注释），行号就漂移，
`--audit-registry` 立刻报一片"该行已无记号"。

手工改行号既慢又容易改错（第 3 次就改错过一次，把两条指向了同一行）。
所以做成工具：按**故障记号**在源码里模糊定位，单候选才自动改写，
多候选/零候选一律报人工——**宁可报错，不许猜**。

## 用法

    python packaging/realign_whitelist.py            # 只报告，不改
    python packaging/realign_whitelist.py --apply    # 真改

## 多候选时的人工流程（2026-10-28 实操）

`-`、`<=` 这类记号太常见，窗口内常有多个候选。此时按"内容映射"定位：

    git show HEAD:src/.../file.py > old.py

对每条过期键取**旧行内容**，数它是旧文件里第几次出现（含自身），
再到新文件里找同内容的第 N 次出现——纯插入不改相对顺序 ⇒ 序号一一对应。
2026-10-28 用这个方法一次对齐 9 条（全部 +7，与插入行数吻合）。
"""
from __future__ import annotations

import argparse
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PROBE = os.path.join(REPO, "packaging", "mutation_probe.py")

#: 故障记号 → 用来在源码里找"原位置"的正则（与 `_candidates` 的 sig 口径对应）
MARKER_RE = {
    "cmp:==->!=": r"==",
    "cmp:!=->==": r"!=",
    "cmp:<-><=": r"<",
    "cmp:<=-><": r"<=",
    "cmp:>->>=": r">",
    "cmp:>=->>": r">=",
    "bin:+->-": r"\+",
    "bin:-->+": r"-",
    "bin:*->/": r"\*",
    "bin:/->*": r"/",
    "bool:and->or": r"\band\b",
    "bool:or->and": r"\bor\b",
}


def _load_entries() -> list[tuple[str, str, int, str]]:
    """从 EQUIVALENT 里抽 (file, marker, line, raw_key)。"""
    src = open(PROBE, encoding="utf-8").read()
    m = re.search(r"^EQUIVALENT: dict\[str, str\] = \{(.*?)^\}", src, re.S | re.M)
    if not m:
        raise SystemExit("找不到 EQUIVALENT 字典")
    out = []
    for key in re.findall(r'"(src/[^"]+?):(\d+):([^"]+)"', m.group(1)):
        out.append((key[0], key[2], int(key[1]), None))
    return out


def _candidates_for(rel: str, marker: str, old_line: int, window: int = 80):
    """在 `old_line ± window` 内找所有"含该记号"的行。"""
    path = os.path.join(REPO, rel)
    lines = open(path, encoding="utf-8").read().splitlines()
    pat = MARKER_RE.get(marker)
    if pat is None:
        # 常量变异：`const:8->9` → 找那个常量值
        cm = re.match(r"const:(.+?)->", marker)
        if not cm:
            return []
        pat = re.escape(cm.group(1))
    rx = re.compile(pat)
    lo = max(1, old_line - window)
    hi = min(len(lines), old_line + window)
    return [n for n in range(lo, hi + 1) if rx.search(lines[n - 1])]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真改写白名单")
    a = ap.parse_args()

    entries = _load_entries()
    src = open(PROBE, encoding="utf-8").read()
    fixes: list[tuple[str, str]] = []
    manual: list[str] = []
    ok = 0

    for rel, marker, old_line, _ in entries:
        path = os.path.join(REPO, rel)
        if not os.path.isfile(path):
            manual.append("%s:%d:%s（文件不存在）" % (rel, old_line, marker))
            continue
        lines = open(path, encoding="utf-8").read().splitlines()
        pat = MARKER_RE.get(marker)
        if pat is None:
            cm = re.match(r"const:(.+?)->", marker)
            pat = re.escape(cm.group(1)) if cm else None
        if pat is None:
            manual.append("%s:%d:%s（不认识的记号）" % (rel, old_line, marker))
            continue
        rx = re.compile(pat)
        # 已在正确位置？
        if 1 <= old_line <= len(lines) and rx.search(lines[old_line - 1]):
            ok += 1
            continue
        cands = _candidates_for(rel, marker, old_line)
        if len(cands) == 1:
            new = cands[0]
            old_key = '"%s:%d:%s"' % (rel, old_line, marker)
            new_key = '"%s:%d:%s"' % (rel, new, marker)
            fixes.append((old_key, new_key))
        elif not cands:
            manual.append("%s:%d:%s（窗口内找不到该记号）" % (rel, old_line, marker))
        else:
            manual.append("%s:%d:%s（%d 个候选：%s —— 需人工）"
                          % (rel, old_line, marker, len(cands), cands[:6]))

    print("白名单：%d 条已对齐 ｜ %d 条可自动修 ｜ %d 条需人工"
          % (ok, len(fixes), len(manual)))
    for old, new in fixes:
        print("  可修 %s → %s" % (old, new.split(":")[1] + ":" + new.split(":")[2]))
    for m in manual:
        print("  人工 %s" % m)

    if a.apply and fixes:
        for old, new in fixes:
            assert src.count(old) == 1, "键不唯一：%s" % old
            src = src.replace(old, new)
        open(PROBE, "w", encoding="utf-8", newline="\n").write(src)
        print("\n已改写 %d 条。" % len(fixes))
    elif fixes:
        print("\n（未改写；加 --apply 生效）")
    return 0 if not manual else 1


if __name__ == "__main__":
    sys.exit(main())
