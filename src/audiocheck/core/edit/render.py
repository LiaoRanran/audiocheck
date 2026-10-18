"""渲染引擎：把 `(源, 操作栈)` 算成目标文件，**并返回一张审计记录**。

为什么值得单独立一层（而不是在 UI 里就地改数组）
------------------------------------------------
* **非破坏**：源只读；目标绝不等于源（见 `_check_dest`）。导出是纯函数 ⇒ 可复算。
* **可审计**：返回 dict，逐操作记录"实际生效了多少样本"、输出峰值、**削波样本数**。
  闭源工具点了按钮你只知道"它改了"，这里你能拿到**改了什么、依据什么、结果如何**。
* **分块流式**：整轨不进内存，长素材（几十分钟）也不会炸。

口径（与 `ops.GainOp` 的 docstring 逐条对齐，改动前先读那两条 ⚠）
--------------------------------------------------------------
* 选区半开 `[t0, t1)`：样本 `k` 受影响 ⟺ `t0 <= k/fs < t1`。`ceil(t*fs-eps)` 落点，
  `eps=1e-9` 只为吃掉浮点误差（`t=0.5, fs=100` 必须落在样本 50 而不是 49）。
* `gain_db == 0.0` 的操作**整条跳过**（no-op 不留痕）⇒ 未被选中的采样**逐位不变**。
* **削波不静默**：整数 subtype 写盘时 libsndfile 会钳位，本层**如实统计**钳位样本数
  并写进审计（浮点 subtype 不会削波，故不统计）。
"""
from __future__ import annotations

import math
import os

import numpy as np
import soundfile as sf

from ..context import io_path
from .ops import GainOp, Project

#: 一次读入的帧数（与 `analyzers/basic.py` 的读块同量级）。
BLOCKSIZE = 1 << 16

#: 会被 libsndfile 钳位到 [−1, 1] 的整型 subtype（浮点不钳位）。
_INT_SUBTYPES = {"PCM_S8", "PCM_U8", "PCM_16", "PCM_24", "PCM_32"}

_EPS = 1e-9


def _sample_at(t: float, fs: int) -> int:
    """秒 → 样本下标（半开区间左端）：`ceil(t*fs - eps)`。"""
    return int(math.ceil(t * fs - _EPS))


def _check_dest(source: str, dest: str, overwrite: bool) -> tuple[str, str]:
    """目标路径守卫：**不许覆盖源**（非破坏的硬保证），默认也不许覆盖已存在文件。"""
    src, dst = os.path.abspath(source), os.path.abspath(dest)
    if os.path.normcase(src) == os.path.normcase(dst):
        raise ValueError("导出目标不能是源文件（非破坏保证）：%s" % dest)
    if not overwrite and os.path.exists(io_path(dst)):
        raise FileExistsError("目标已存在（要覆盖请显式 overwrite=True）：%s" % dest)
    return io_path(src), io_path(dst)


def render(project: Project, dest: str, *, subtype: str | None = None,
           blocksize: int = BLOCKSIZE, overwrite: bool = False) -> dict:
    """把 `project` 渲染到 `dest`，返回**审计记录**（dict）。

    参数
    ----
    subtype   : 目标编码；`None` = 沿用源的 subtype（保证"没动的地方逐位不变"的前提）。
    overwrite : 是否允许覆盖已存在的目标（源文件永远不许，见 `_check_dest`）。
    """
    src, dst = _check_dest(project.source, dest, overwrite)
    applied = [0] * len(project.ops)          # 每个操作实际生效的样本数
    peak = 0.0
    clipped = 0
    frames = 0
    claimed = 0
    read_error: Exception | None = None

    with sf.SoundFile(src) as f:
        fs = int(f.samplerate)
        ch = int(f.channels)
        sub = subtype or f.subtype
        clips_int = sub in _INT_SUBTYPES
        claimed = int(f.frames or 0)
        with sf.SoundFile(dst, "w", samplerate=fs, channels=ch, subtype=sub) as g:
            pos = 0
            while True:
                # ⚠ 源**可能是截断文件**（真实曲库里就有：容器头声称满长帧数，
                # 读到 99.5% 才抛）。按 E2.1 的规矩：**不许 traceback**，如实记录
                # "读不满"，并把已读到的部分正常写出（否则整条编辑都白做）。
                try:
                    b = f.read(blocksize, dtype="float64", always_2d=True)
                except Exception as e:            # noqa: BLE001 —— libsndfile 抛的形形色色
                    read_error = e
                    break
                if not len(b):
                    break
                n = int(b.shape[0])
                for i, op in enumerate(project.ops):
                    if not isinstance(op, GainOp) or op.is_noop:
                        continue
                    k0 = max(pos, _sample_at(op.t0, fs))
                    k1 = min(pos + n, _sample_at(op.t1, fs))
                    if k1 <= k0:
                        continue
                    b[k0 - pos:k1 - pos] *= 10.0 ** (op.gain_db / 20.0)
                    applied[i] += k1 - k0
                if n:
                    peak = max(peak, float(np.abs(b).max()))
                if clips_int:
                    clipped += int((np.abs(b) > 1.0).sum())
                g.write(b)
                pos += n

        frames = pos

    audit = {
        "source": os.path.abspath(project.source),
        "dest": os.path.abspath(dest),
        "samplerate": fs,
        "channels": ch,
        "subtype": sub,
        "frames": frames,
        "duration_s": round(frames / fs, 6) if fs else 0.0,
        "peak_out_dbfs": (round(20.0 * math.log10(peak), 3) if peak > 0 else None),
        "clipped_frames": clipped,          # 只对整型 subtype 统计；浮点恒为 0
        # 截断源：显式上报（frames_claimed > frames 即"读不满"）。已读部分照常写出。
        "read_incomplete": read_error is not None,
        "frames_claimed": claimed,
        "ops": [{**op.to_dict(), "applied_frames": applied[i]}
                for i, op in enumerate(project.ops)],
    }
    return audit


def render_gain(source: str, dest: str, t0: float, t1: float, gain_db: float,
                **kw) -> dict:
    """便捷入口：单个时间选区增益（`render()` 的薄封装，参数同它）。"""
    return render(Project(source, [GainOp(t0, t1, gain_db)]), dest, **kw)
