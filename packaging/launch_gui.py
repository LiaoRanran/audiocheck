"""PyInstaller 的入口脚本（顶层模块，绝对导入）。

**为什么需要这个文件**：不能直接拿 `src/audiocheck/ui/app.py` 当入口。
PyInstaller 把入口脚本当成顶层模块跑，包上下文（`__package__`）会丢，
于是 `from .window import main` 报
    ImportError: attempted relative import with no known parent package
—— 实测：spec 看着完全合理，构建也成功，但 EXE 一启动就退，退出码 1。

规则：**入口必须是顶层模块，且用绝对导入**。包内的模块留给它 import。

## 参数分派（2026-10-28 修，2026-10-29 补 edit）

发布包只有这一个 exe，但产品同时提供 GUI 与**两套** CLI 能力：

| 能力 | 模块 | 典型参数 |
|---|---|---|
| 体检 | `audiocheck.cli` | 文件路径 / `--json` / `--csv` / `--doctor` |
| 修复 | `audiocheck.edit_cli` | 源 + 目标 / `--gain` / `--declip` / `--batch` / `--verify-audit` |
| 界面 | `audiocheck.ui.app` | 无参数，或 `--seed/--mode/--vibe` |

早期版本把入口硬接在 `ui.app.main` 上 ⇒ **任何参数都开窗口进主循环**
（`--help` 实测卡死 180 s）；v1.1.4 只补了"体检 CLI"，**编辑 CLI 仍不可达**
——而修复可审计正是这个项目的核心差异化，压缩包用户却用不了。
（2026-10-29 实测：`audiocheck.exe 源.wav 目标.wav --gain -3 --at 0-0.5`
报 `unrecognized arguments`。）

现在三层分派：
* 无参数 / `--seed`/`--mode`/`--vibe` ⇒ GUI；
* 出现**编辑专属参数** ⇒ 编辑 CLI；
* 其余 ⇒ 体检 CLI。
"""
from __future__ import annotations

import sys

#: GUI 专属的主题参数：出现在 argv 里说明用户想开窗口（不是要跑 CLI）。
_GUI_ONLY_FLAGS = ("--seed", "--mode", "--vibe")

#: 编辑 CLI 的**专属**参数（体检 CLI 不认识它们）。只要出现一个，就走编辑路径。
#: ⚠ 不要把它们全列一遍：这里只放"能唯一判定是编辑命令"的招牌参数。
#: `--version`/`--doctor` 两边都有，**不能**用作判据（交给体检 CLI，它先注册）。
_EDIT_ONLY_FLAGS = (
    "--gain", "--declip", "--declick", "--dehum", "--denoise",
    "--at", "--band", "--poly", "--chain", "--project", "--batch",
    "--batch-help", "--save-project", "--noise-at", "--reduction-db",
    "--no-floor-track", "--hum-f0", "--hum-auto", "--hum-q", "--hum-harmonics",
    "--threshold", "--max-run", "--ceiling", "--bandwidth", "--iterations",
    "--n-fft", "--hop", "--subtype", "--dither", "--overwrite",
    "--audit", "--report", "--verify-audit",
)


def _flags(argv: list) -> set:
    """把 argv 里的旗标抽成集合（支持 `--flag=value` 与 `--flag value`）。"""
    return {a.split("=", 1)[0] for a in argv if a.startswith("-")}


def _wants_gui(argv: list) -> bool:
    if not argv:
        return True
    return bool(_flags(argv) & set(_GUI_ONLY_FLAGS))


def _wants_edit(argv: list) -> bool:
    """是否该走**编辑** CLI（而不是体检 CLI）。"""
    if not argv:
        return False
    return bool(_flags(argv) & set(_EDIT_ONLY_FLAGS))


def main() -> int:
    argv = sys.argv[1:]
    if _wants_gui(argv):
        from audiocheck.ui.app import main as gui_main
        return gui_main()
    if _wants_edit(argv):
        from audiocheck.edit_cli import main as edit_main
        return edit_main(argv)
    from audiocheck.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    # ⚠ **必须在任何多进程工作之前调用**：Windows 的 spawn 会重新执行本 exe，
    # `freeze_support()` 让 PyInstaller 的引导程序识别"我是被 spawn 出来的子进程"
    # 并直接转入 multiprocessing 的 worker 主循环，而不是重新跑一遍 CLI。
    # 不调它的后果：进程池里的 worker 一启动就被自己的引导程序杀掉
    # （`A process in the process pool was terminated abruptly`）。
    # `edit_cli` 侧还有一层保险：frozen 环境下批处理改用线程池。
    import multiprocessing

    multiprocessing.freeze_support()
    sys.exit(main())
