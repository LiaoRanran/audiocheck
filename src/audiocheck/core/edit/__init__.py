"""非破坏编辑内核（P0 地基的第一步）。

模块边界（别越界）：
  * 本包**只做**"源 + 操作栈 → 目标文件"的确定性渲染，**不碰 UI、不碰播放、不碰测量**。
  * 测量内核（`core.analyzers` / `core.dsp`）**不 import 本包**；反过来本包也**不依赖**测量
    ——两步互证通过"调用方各跑一次"完成（见 `tests/test_edit_render.py::test_measurement_crosscheck`）。
  * `stft` 是**编辑专用**的时频内核（`docs/theory.md §12.1` 的 COLA/NOLA 落地），
    与 `dsp`（测量）各有一套：测量的 Welch 参数要统计平滑，编辑的 STFT 参数要局部性。
"""
from .ops import BandGainOp, DeclickOp, DeclipOp, DehumOp, DenoiseOp, GainOp, Project, op_from_dict
from .render import (
                  render,
                  render_band,
                  render_declick,
                  render_declip,
                  render_dehum,
                  render_denoise,
                  render_gain,
)
from .session import EditSession

__all__ = ["BandGainOp", "DeclickOp", "DeclipOp", "DehumOp", "DenoiseOp", "EditSession",
           "GainOp", "Project", "op_from_dict", "render", "render_band", "render_declick",
           "render_declip", "render_dehum", "render_denoise", "render_gain"]
