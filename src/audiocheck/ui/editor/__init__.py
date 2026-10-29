"""编辑工作站 UI（重型工业级 / REAPER 式停靠面板）。

本包是分析 `MainWindow` 之外的**独立**窗口类，零回归风险：分析侧代码一字不改，
设计系统（`theme`/`palette`/`widgets`/`motion`）全部复用。

信息架构（见 `docs/editor_v1_plan.md`）：
- 三个 `QDockWidget`：文件 / 编辑 / 检视，可浮动·标签化·重排；
- 顶部 `TransportBar` 常驻（全局走带）；
- `CommandPalette` 沿用既有 Ctrl+K。

本文件只搭**外壳骨架**（切片 2）：三 dock + 走带条 + 主题/命令面板复用。
编辑视图（频谱+波形+矩形选区）与算子接入在后续切片。
"""

from __future__ import annotations

from .shell import EditorWindow
from .transport import TransportBar

__all__ = ["EditorWindow", "TransportBar", "main"]


def main(argv=None) -> int:
    """`audiocheck-edit` 命令的入口（pyproject 的 gui-scripts 指向这里）。

    ⚠ 这个函数**必须存在**：`[project.gui-scripts] audiocheck-edit =
    "audiocheck.ui.editor:main"` 声明了这个符号，但本包此前只导出
    `EditorWindow`/`TransportBar` ⇒ pip 装完敲 `audiocheck-edit` 立刻
    `AttributeError: module 'audiocheck.ui.editor' has no attribute 'main'`。
    构建与 import 都不会报错，只有真正启动才炸——由
    `tests/test_packaging_meta.py` 的入口点判据兜住。

    支持 `--theme dark|light`（默认 dark），其余位置参数当作**待载入的音频文件**
    （`audiocheck-edit a.wav b.wav` —— 与 `audiocheck --edit a.wav` 同一行为）。
    """
    import os
    import sys

    from PySide6.QtWidgets import QApplication

    from .. import theme as T

    args = list(sys.argv[1:] if argv is None else argv)
    name = "dark"
    paths: list = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--theme" and i + 1 < len(args):
            name = args[i + 1]
            i += 2
            continue
        if a.startswith("--theme="):
            name = a.split("=", 1)[1]
            i += 1
            continue
        paths.append(a)
        i += 1

    app = QApplication.instance() or QApplication(sys.argv[:1])
    T.init_fonts(app)
    w = EditorWindow(T.active_theme(name))
    w.resize(1280, 800)
    for p in paths:
        if os.path.isfile(p):
            w.open_file(p)
    w.show()
    return app.exec()
