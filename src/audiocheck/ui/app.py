"""桌面应用入口（`audiocheck-gui` 命令指向这里）。

单独成文件，是为了让 `ui/__init__.py` 保持轻量——只有真的要开窗口时才付 Qt 的导入代价。
"""
from __future__ import annotations

import sys

from .window import main

if __name__ == "__main__":
    sys.exit(main())
