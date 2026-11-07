"""Qt 事件循环泵（测试共享助手）——**自包含**，不依赖 `packaging/` 下的探针脚本。

## 为什么从 mem_probe 复制出来而不是 import

`tests/test_ui_b9_chart.py` 等 4 个测试此前 `import perf_probe`（re-export
`mem_probe.pump`）。这在真仓库能跑，但在**变异工作区**里必炸：工作区只复制
`src/` 与 `tests/`，`packaging/` 不在里面 ⇒ `ModuleNotFoundError: No module
named 'perf_probe'` ⇒ 每个变异体都被 `-x` 停在同一个失败上，该模块的变异
判定整片失真（palette 首轮 23 个"无效"的根因）。

测试的共享助手就该住在 `tests/` 里——判据要能在"只有 src + tests"的环境下自证。

## 实现说明（与 `packaging/mem_probe.pump` 同一份语义，勿分叉）

**必须手动派发 DeferredDelete**：`deleteLater()` 投的是延迟删除事件，而
`processEvents()` 不保证把它送出去（Qt 只在"事件循环层级匹配"时派发）。
界面层大量用 deleteLater 做卡片/提示的回收，漏派发就会出现
"每点一次涨 0.6 MB"这种假泄漏，白查半天。
真实运行时 `QApplication.exec()` 会自己派发，所以这是探针/判据的保真度问题。
"""
from __future__ import annotations

import os
import time


def make_wav(path: str, seconds: float = 70.0, fs: int = 44100) -> str:
    """合成素材：白噪 + 砖墙 16k（与 `packaging/mem_probe.make_wav` 同一份语义）。

    时长必须够长，否则 STFT 列数到不了 max_cols=900，测出来的 S 就不是真实大小
    （真实曲目 3–5 分钟必然到顶）。已存在则直接返回（幂等）。
    """
    if os.path.exists(path):
        return path
    import numpy as np
    import soundfile as sf

    rng = np.random.default_rng(20261012)
    x = (rng.standard_normal(int(fs * seconds)) * 0.2).astype(np.float32)
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(len(x), 1.0 / fs) > 16000] = 0.0
    x = np.fft.irfft(spec, n=len(x)).astype(np.float32)
    sf.write(path, np.column_stack([x, x * 0.9]), fs, subtype="PCM_16")
    return path


def pump(app, seconds: float, events: bool = True) -> None:
    """转事件循环 `seconds` 秒（每轮 ~4 ms，带 DeferredDelete 派发）。"""
    from PySide6.QtCore import QCoreApplication, QEvent

    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        if events:
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        time.sleep(0.004)


def wait(cond, app, timeout: float = 120.0) -> bool:
    """轮询 `cond()` 直到成立或超时（期间持续 pump）。"""
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return True
        pump(app, 0.004)
    return False
