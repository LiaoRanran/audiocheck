"""Qt 桌面界面（可选依赖：PySide6 + matplotlib）。

只在真正打开界面时才导入重依赖，因此 `import audiocheck` 不会拖入 Qt。
"""
from __future__ import annotations

__all__ = ["main", "MainWindow"]


def main(argv=None):
    """启动桌面应用（等价于 `audiocheck-gui` 命令）。"""
    from .window import main as _main
    return _main(argv)


def __getattr__(name):                      # 便于 `from audiocheck.ui import MainWindow`
    if name == "MainWindow":
        from .window import MainWindow
        return MainWindow
    raise AttributeError(name)
