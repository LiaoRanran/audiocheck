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

__all__ = ["EditorWindow", "TransportBar"]
