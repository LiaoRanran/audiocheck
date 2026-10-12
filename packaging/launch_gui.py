"""PyInstaller 的入口脚本（顶层模块，绝对导入）。

**为什么需要这个文件**：不能直接拿 `src/audiocheck/ui/app.py` 当入口。
PyInstaller 把入口脚本当成顶层模块跑，包上下文（`__package__`）会丢，
于是 `from .window import main` 报
    ImportError: attempted relative import with no known parent package
—— 实测：spec 看着完全合理，构建也成功，但 EXE 一启动就退，退出码 1。

规则：**入口必须是顶层模块，且用绝对导入**。包内的模块留给它 import。
"""
from __future__ import annotations

import sys


def main() -> int:
    from audiocheck.ui.app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    sys.exit(main())
