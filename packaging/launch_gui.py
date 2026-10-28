"""PyInstaller 的入口脚本（顶层模块，绝对导入）。

**为什么需要这个文件**：不能直接拿 `src/audiocheck/ui/app.py` 当入口。
PyInstaller 把入口脚本当成顶层模块跑，包上下文（`__package__`）会丢，
于是 `from .window import main` 报
    ImportError: attempted relative import with no known parent package
—— 实测：spec 看着完全合理，构建也成功，但 EXE 一启动就退，退出码 1。

规则：**入口必须是顶层模块，且用绝对导入**。包内的模块留给它 import。

## 参数分派（2026-10-28 修）

发布包只有这一个 exe，但产品同时提供 GUI 与 CLI 两套能力。早期版本把
入口硬接在 `ui.app.main` 上 ⇒ **包里的 exe 完全没有 CLI**：`audiocheck --help`
会直接弹出窗口并卡住主循环（实测超时 180 s），`--json`/`--doctor`/批量体检
全都不可达，而 README 宣传了它们。

现在按参数分派：
* **无参数** ⇒ GUI（双击 exe 的正常行为）；
* **有参数** ⇒ CLI（`--help`、`--version`、`--doctor`、`--edit`、文件路径…）。
  唯一例外是 GUI 自己的主题参数（`--seed/--mode/--vibe`），它们仍走 GUI。
"""
from __future__ import annotations

import sys

#: GUI 专属的主题参数：出现在 argv 里说明用户想开窗口（不是要跑 CLI）。
_GUI_ONLY_FLAGS = ("--seed", "--mode", "--vibe")


def _wants_gui(argv: list) -> bool:
    if not argv:
        return True
    return any(a.split("=", 1)[0] in _GUI_ONLY_FLAGS for a in argv)


def main() -> int:
    argv = sys.argv[1:]
    if _wants_gui(argv):
        from audiocheck.ui.app import main as gui_main
        return gui_main()
    from audiocheck.cli import main as cli_main
    return cli_main(argv)


if __name__ == "__main__":
    sys.exit(main())
