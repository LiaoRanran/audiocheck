#!/usr/bin/env python3
"""cutoff_impact：**隔离** `detect_cutoff` 口径变更对判定的影响（两份 CLI `--json` 对比）。

## 为什么入库

`docs/theory.md §1.6`、`CHANGELOG`、`AGENTS.md` 都引用了"只换 cutoff 实现 ⇒ 真实曲库
**55/448 改档**"这个数字。按项目规矩，引用的数字必须**能从仓库复算**——所以对比逻辑
放在这里（不是 gitignored 的 `out/`）。数据本身仍是**单一用户曲库**（不在仓库里），
所以只能复算"怎么比"，不能复算"比出来多少"——与 `docs/real_library.md` 的口径一致。

## 怎么产出两份输入（隔离到**只差 cutoff**）

    # ① 旧实现（v4 跨骑窗）：把旧 `detect_cutoff` 源码取回来覆盖模块函数后跑 CLI
    python packaging/cutoff_impact.py --make-old out/music_old.json --list <清单> --jobs 8
    # ② 新实现（v4.1）：直接跑 CLI
    python -m audiocheck --list <清单> --jobs 8 --json out/music_new.json
    # ③ 对比
    python packaging/cutoff_impact.py --old out/music_old.json --new out/music_new.json

`--make-old` 用 `git show <rev>:src/...spectrum.py` 取旧版 `detect_cutoff` 源码，
`exec` 进当前模块命名空间（**其余代码完全相同**）⇒ 结果差异可**唯一归因**到 cutoff。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SPEC = "src/audiocheck/core/analyzers/spectrum.py"
#: v4（跨骑通带窗）→ v4.1（不跨骑）的分界提交：旧实现在它的**父提交**。
_DEFAULT_OLD_REV = "2e76280^"


def _load(p: str) -> dict:
    d = json.load(open(p, encoding="utf-8"))
    return {r["path"]: r for r in d["results"]}


def run_diff(old_p: str, new_p: str) -> int:
    old, new = _load(old_p), _load(new_p)
    keys = sorted(set(old) & set(new))
    lo = Counter(old[k].get("level") for k in keys)
    ln = Counter(new[k].get("level") for k in keys)
    print("共同文件 %d ｜ old %d ｜ new %d" % (len(keys), len(old), len(new)))
    print("\n== 等级分布 ==")
    for lv in ("ok", "warn", "bad", "error"):
        print("  %-6s old %4d  →  new %4d" % (lv, lo.get(lv, 0), ln.get(lv, 0)))
    flips = [k for k in keys if old[k].get("level") != new[k].get("level")]
    print("\n== 等级翻转 %d/%d ==" % (len(flips), len(keys)))
    cdiff = sorted((abs((new[k].get("cutoff_khz") or 0) - (old[k].get("cutoff_khz") or 0)),
                    (old[k].get("cutoff_khz") or 0), (new[k].get("cutoff_khz") or 0))
                   for k in keys if old[k].get("cutoff_khz") and new[k].get("cutoff_khz"))
    if cdiff:
        print("== cutoff 变化（kHz）：中位 %.3f ｜ 最大 %.3f ==" % (
            cdiff[len(cdiff) // 2][0], cdiff[-1][0]))
    vch = sum(1 for k in keys if old[k].get("verdict") != new[k].get("verdict"))
    print("== 判定文案变化 %d/%d ==" % (vch, len(keys)))
    return 0


def make_old(out_json: str, rev: str, rest: list[str]) -> int:
    """把 <rev> 的旧 `detect_cutoff` 覆盖进当前模块，再跑 CLI（隔离到只差 cutoff）。"""
    sys.path.insert(0, os.path.join(_ROOT, "src"))
    src = subprocess.run(["git", "-C", _ROOT, "show", "%s:%s" % (rev, _SPEC)],
                         capture_output=True, text=True, check=True).stdout
    i, j = src.index("def detect_cutoff"), src.index("def band_balance")
    import audiocheck.core.analyzers.spectrum as S
    exec(compile(src[i:j], "<old_spectrum>", "exec"), S.__dict__)   # 覆盖 S.detect_cutoff
    from audiocheck.cli import main
    sys.argv = ["audiocheck"] + rest
    return main()


def main() -> int:
    ap = argparse.ArgumentParser(description="隔离 cutoff 口径变更的判定影响")
    ap.add_argument("--old")
    ap.add_argument("--new")
    ap.add_argument("--make-old", metavar="OUT.json",
                    help="用 <rev> 的旧 detect_cutoff 跑 CLI 并把 json 写到此处")
    ap.add_argument("--rev", default=_DEFAULT_OLD_REV, help="旧实现所在提交（默认 %s）" % _DEFAULT_OLD_REV)
    ap.add_argument("rest", nargs="*", help="--make-old 时透传给 CLI 的参数（--list/--jobs 等）")
    a = ap.parse_args()
    if a.make_old:
        args = list(a.rest)
        if "--json" not in args:
            args += ["--json", a.make_old]
        return make_old(a.make_old, a.rev, args)
    if not (a.old and a.new):
        ap.error("需要 --old 与 --new，或用 --make-old")
    return run_diff(a.old, a.new)


if __name__ == "__main__":
    raise SystemExit(main())
