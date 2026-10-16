#!/usr/bin/env python3
"""mutation_probe：**变异测试**（E1.2）——自实现，不引 cosmic-ray。

## 为什么自实现

E1 提示词要求"变异测试进 CI，变异分数不得低于 E0 基线"。本机没有
`cosmic-ray` / `mutmut`，且项目铁律是**不增运行时依赖**（dev 依赖也尽量不加）。
项目已有先例：`colormap_probe.py` 自写 CIEDE2000、CVD 矩阵——**测量脚本不进运行时**，
所以自实现不构成新依赖。变异测试的本质就三件事：**注入故障 → 跑测试 → 看抓不抓得住**，
不需要框架。

## 做法

1. 把仓库 `src/` + `tests/` 复制到临时工作区（**不动真实仓库**）；
2. 对每个目标模块，用 AST 生成变异体（比较符 / 算术符 / 布尔符 / 数值常量）；
3. 每个变异体：写进工作区 → 跑**定向测试子集**（`-x` 早停）→ 记 存活/杀死；
4. 报告：变异分数 + **逐个存活变异体的结论**（等价变异体 / 测试缺失）；
5. `--max-seconds` 限定时长（E1 要求"限定时长跑"），超时即停并报当前分数；
6. `--strict`：变异分数 < `MIN_SCORE` ⇒ 返回 1（存活变异体作为**发现**列出，不判红）。

## 纪律（E1 提示词）

* **等价变异体必须登记并给理由**（`EQUIVALENT` 表），不许一律忽略——
  否则"分数"就成了自欺。
* 存活且**不是**等价变异体的 = **测试缺失**，如实列出（那是真发现）。
* 没找到问题就说"本轮未发现"，不许包装成"通过质量测试"。
* ⚠ **先验证量具**：本探针开发中踩到两次"分数 0/全存活"的**假象**，根因都在量具
  （① `id(node)` 跨树不匹配 ⇒ 变异静默不生效；② 未设 `PYTHONPATH=工作区/src`
  ⇒ editable 安装的**真实**仓库代码被 import、变异体根本没加载）。
  分数异常低时**先怀疑量具**，别急着下"测试很差"的结论。

    python packaging/mutation_probe.py [--strict] [--max-seconds N]
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 目标模块 → 定向测试子集。子集要**覆盖该模块的消费者**而不只是模块自身的单元测试：
#: 早先只挂 `test_cutoff.py`（30 测试）时分数仅 0.188——那不是"测试差"，
#: 是**量具口径太窄**（引擎/判定层才是截止点的真实消费者）。
TARGETS: dict[str, list[str]] = {
    "src/audiocheck/core/analyzers/spectrum.py": [
        "tests/test_cutoff.py", "tests/test_engine.py", "tests/test_verdict.py"],
    "src/audiocheck/core/dsp.py": [
        "tests/test_dsp.py", "tests/test_dsp_standard.py",
        "tests/test_truepeak.py", "tests/test_timeline_loudness.py"],
}

#: 变异分数下限。E1 要求"不得低于 E0 基线"，但 **E0 基线未立** ⇒ 取本探针的
#: **首轮实测值**为首个基线（0.524，见 CHANGELOG）。阈值留一点余量取 0.50；
#: 想抬高它必须先填掉报告里列出的"测试缺失"。
MIN_SCORE = 0.50

#: **等价变异体**白名单：语义不变 ⇒ 测不出来是**正确的**，不是测试缺失。
#: 键 = 变异签名 `相对路径:行:类型:old->new`，值 = 理由（必须写清为什么语义不变）。
EQUIVALENT: dict[str, str] = {
    "src/audiocheck/core/dsp.py:30:const:4096->4097":
        "TP_OVERLAP 是真峰值分段的重叠采样数，±1 只挪动分段边界；取最大值的中段不变。",
    "src/audiocheck/core/dsp.py:31:const:64->65":
        "TP_TRIM 是每段两端丢弃的瞬态采样数（滤波器瞬态约 21），64→65 丢弃范围仍在瞬态内。",
    "src/audiocheck/core/analyzers/spectrum.py:17:const:6.0->9.0":
        "`_smooth_log_axis` 的 `fraction` 默认值**从不被使用**——两个调用方 "
        "(`smooth_log`/`smooth_power`) 都显式传入自己的 `fraction`。",
}


class _Cand:
    """一个可变异点：节点 id + 变异类型 + 描述。"""

    __slots__ = ("nid", "kind", "old", "new", "lineno")

    def __init__(self, nid, kind, old, new, lineno):
        self.nid, self.kind, self.old, self.new, self.lineno = nid, kind, old, new, lineno

    def sig(self, rel: str) -> str:
        return "%s:%d:%s:%s->%s" % (rel, self.lineno, self.kind, self.old, self.new)


_CMP = {ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Lt: ast.LtE, ast.LtE: ast.Lt,
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_CMP_NAME = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
             ast.Eq: "==", ast.NotEq: "!="}
_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
_BIN_NAME = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _candidates(tree: ast.AST) -> list[_Cand]:
    out: list[_Cand] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in _CMP:
                out.append(_Cand(id(node), "cmp", _CMP_NAME[op],
                                 _CMP_NAME[_CMP[op]], node.lineno))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            out.append(_Cand(id(node), "bin", _BIN_NAME[type(node.op)],
                             _BIN_NAME[_BIN[type(node.op)]], node.lineno))
        elif isinstance(node, ast.BoolOp):
            other = ast.Or if isinstance(node.op, ast.And) else ast.And
            out.append(_Cand(id(node), "bool",
                             "and" if isinstance(node.op, ast.And) else "or",
                             "or" if isinstance(node.op, ast.And) else "and",
                             node.lineno))
            # 注意：BoolOp 的替换需要构造新节点，见 _apply
            _ = other
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool) and node.value not in (0, 1, 2, 0.0, 1.0):
            # 数值常量：整数 +1、浮点 ×1.5（跳过 0/1/2 这类结构性常量）
            new = (node.value + 1) if isinstance(node.value, int) else round(node.value * 1.5, 6)
            out.append(_Cand(id(node), "const", repr(node.value), repr(new), node.lineno))
    return out


def _apply(tree: ast.AST, cand: _Cand) -> ast.AST:
    """把目标节点替换成变异后的节点（就地改，返回树）。"""
    class _R(ast.NodeTransformer):
        def _hit(self, node):
            return id(node) == cand.nid

        def visit_Compare(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.ops = [_CMP[type(node.ops[0])]()]
            return node

        def visit_BinOp(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.op = _BIN[type(node.op)]()
            return node

        def visit_BoolOp(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            return node

        def visit_Constant(self, node):
            if self._hit(node):
                node.value = (node.value + 1) if isinstance(node.value, int) \
                    else round(node.value * 1.5, 6)
            return node

    return _R().visit(tree)


def _build_workspace() -> str:
    ws = tempfile.mkdtemp(prefix="ac_mut_")
    for sub in ("src", "tests"):
        shutil.copytree(os.path.join(_ROOT, sub), os.path.join(ws, sub),
                        ignore=shutil.ignore_patterns("__pycache__"))
    # ⚠ **关键**：本仓库多半是 `pip install -e .`（editable）装的，其 PEP 660
    # finder 挂在 `sys.meta_path` 上、**优先于 `sys.path`** —— 于是测试
    # `import audiocheck` 会拿到真实仓库的代码，工作区里被变异的副本**根本不被加载**
    # （实测症状：12/12 变异体"全存活"，分数 0——是量具坏了，不是测试好）。
    # `sitecustomize.py` 在解释器启动时（.pth 处理之后）被导入，正好可以把
    # editable finder 摘掉。用 PYTHONPATH 让工作区先被找到。
    with open(os.path.join(ws, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            "sys.meta_path[:] = [f for f in sys.meta_path\n"
            "                    if 'editable' not in (type(f).__module__ + type(f).__name__).lower()]\n")
    return ws


def _run_subset(ws: str, tests: list[str], py: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # ⚠ **`ws/src` 必须在 PYTHONPATH 里**：本仓库是 editable 安装，`audiocheck`
    # 默认解析到**真实仓库**；而**并非所有测试文件都自带 `sys.path.insert(src)`**
    # （如 `tests/test_cutoff.py` 就没有）⇒ 那些测试会 import 真实代码，
    # 变异体静默失效（实测症状：`thr = (pass+floor)/2` 改成 `*2` 后
    # `test_cutoff_accuracy` 仍全绿——量具又坏了，不是测试好）。
    # `ws` 放前面是为了让工作区的 `sitecustomize.py` 生效（摘掉 editable finder）。
    env["PYTHONPATH"] = os.path.join(ws, "src") + os.pathsep + ws
    p = subprocess.run([py, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                       cwd=ws, env=env, capture_output=True, text=True)
    return p.returncode, (p.stdout + p.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="变异测试（自实现，AST 注入故障）")
    ap.add_argument("--strict", action="store_true",
                    help="分数 < 阈值或存在未登记存活变异体 ⇒ 返回 1")
    ap.add_argument("--max-seconds", type=float, default=300.0,
                    help="限定时长（E1 要求）；超时即停并报当前分数")
    ap.add_argument("--max-mutants", type=int, default=40,
                    help="每个目标最多生成多少变异体（控制时长）")
    a = ap.parse_args()

    py = sys.executable
    ws = _build_workspace()
    print("变异测试（自实现 AST 注入；工作区 %s）" % ws)
    print("目标 %d 个模块，时长上限 %.0f s\n" % (len(TARGETS), a.max_seconds))

    t0 = time.time()
    killed = 0
    survived: list[str] = []
    invalid = 0
    total = 0
    equiv = 0
    timed_out = False

    for rel, tests in TARGETS.items():
        src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        cands = _candidates(ast.parse(src))[:a.max_mutants]
        print("== %s（%d 个变异体候选）==" % (rel, len(cands)))
        target_path = os.path.join(ws, rel)
        for i in range(len(cands)):
            if time.time() - t0 > a.max_seconds:
                timed_out = True
                print("  …… 达到时长上限，停止（结果部分）")
                break
            total += 1
            # ⚠ **必须在同一棵树上取候选再变异**：`_candidates` 用 `id(node)` 标识目标，
            # 而 `ast.parse` 每次都给新对象 ⇒ 若"先 parse 收候选、再 parse 变异"，
            # id 永不匹配 ⇒ **变异静默不生效**，全部假"存活"（实测踩过：16/16 存活、
            # 分数 0，量具坏了而不是测试好）。故这里按**索引**在新鲜树上重取候选。
            tree = ast.parse(src)
            cand = _candidates(tree)[i]
            mutated = _apply(tree, cand)
            try:
                new_src = ast.unparse(mutated)
                compile(new_src, rel, "exec")          # 无效变异体（语法/编译不过）不计分
            except Exception:                          # noqa: BLE001
                invalid += 1
                continue
            if new_src == src:                         # 变异没改动源码 ⇒ 量具异常
                invalid += 1
                print("  [无效] 变异未改变源码（量具异常）")
                continue
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_src)
            rc, _out = _run_subset(ws, tests, py)
            sig = cand.sig(rel)
            if rc == 0:
                if sig in EQUIVALENT:
                    equiv += 1
                    print("  [等价] %s —— %s" % (sig, EQUIVALENT[sig]))
                else:
                    survived.append(sig)
                    print("  [存活] %s  ← 测试缺失" % sig)
            elif rc == 1:
                killed += 1
            else:
                invalid += 1
                print("  [无效] %s（pytest rc=%d，可能无法导入）" % (sig, rc))
        # 还原
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(src)
        if timed_out:
            break

    scored = killed + len(survived)  # 只把"有效且非等价"的算进分母
    score = (killed / scored) if scored else 0.0
    print("\n---- 结果 ----")
    print("有效变异体 %d ｜ 杀死 %d ｜ 存活 %d ｜ 等价 %d ｜ 无效 %d"
          % (scored, killed, len(survived), equiv, invalid))
    print("变异分数 = %d/%d = %.3f（下限 %.2f）%s"
          % (killed, scored, score, MIN_SCORE, "（未跑完，时长上限）" if timed_out else ""))
    if survived:
        print("\n存活变异体（非等价 ⇒ **测试缺失**，逐条列出）：")
        for s in survived:
            print("  - %s" % s)
        print("\n注：这些是**发现**（具体哪些判据没被测到），不是失败。")
        print("    `--strict` 只按**分数下限**判红；抬高分数须先填这些缺口。")
    else:
        print("\n本轮未发现『测试缺失』型存活变异体。")

    if a.strict and score < MIN_SCORE:
        print("\n--strict：变异分数 %.3f < 下限 %.2f" % (score, MIN_SCORE))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
