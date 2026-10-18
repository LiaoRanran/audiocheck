"""非破坏编辑内核（P0 地基的第一步）。

模块边界（别越界）：
  * 本包**只做**"源 + 操作栈 → 目标文件"的确定性渲染，**不碰 UI、不碰播放、不碰测量**。
  * 测量内核（`core.analyzers` / `core.dsp`）**不 import 本包**；反过来本包也**不依赖**测量
    ——两步互证通过"调用方各跑一次"完成（见 `tests/test_edit_render.py::test_measurement_crosscheck`）。
"""
from .ops import GainOp, Project
from .render import render, render_gain

__all__ = ["GainOp", "Project", "render", "render_gain"]
