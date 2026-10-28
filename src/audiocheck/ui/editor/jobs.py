"""UI 的后台作业执行器（**可取消、有进度、不阻塞主线程**）。

## 为什么需要

编辑工作站的导出与批处理此前都是**在 UI 线程内同步调用**：
`self.ops._session.render(...)` 直接跑在主线程上。10 分钟素材的整轨渲染
实测约 24 秒（`packaging/band_perf.py`）——这段时间窗口完全不响应、
Windows 会给标题栏挂上"未响应"，用户以为程序死了。

## 设计要点（每条都对应一个已知的 Qt 坑）

| 要点 | 依据 / 坑 |
|---|---|
| worker **绝不持有 widget** | Qt 官方："Widgets are not reentrant. They can only be used from the main thread" |
| 结果用**信号**回传（跨线程自动 queued） | "The slot is invoked when control returns to the event loop of the receiver's thread" |
| 取消用**协作式令牌**，不是杀线程 | `QThread::requestInterruption` 官方："That request is advisory and it is up to code running on the thread to decide if and how it should act" |
| 进度**节流** | 每 N ms 发一次；每像素发一次信号会淹没事件队列 |
| `deleteLater()` + `finished` 连接 | 官方示例；漏了会泄漏 QThread |
| **必须持有引用** | 本仓已踩过：QThread 被 GC 而线程仍在跑会让进程硬崩（`window.py` 的注释记着） |

## 与 `render()` 的关系

本模块**不改** `core.edit.session.render` 的签名——内核保持纯函数、可测、
无 Qt 依赖。取消只在**外层**生效：作业以"分段"为单位执行，
每段之间检查令牌。这样：
* 内核的逐位判据完全不受影响；
* 取消的粒度是"一段"，不是"一个样本"，语义诚实（不需假装能中断 STFT 内部）。
"""
from __future__ import annotations

import time
import traceback

from PySide6.QtCore import QObject, QThread, Signal


class CancelToken:
    """协作式取消令牌。**线程安全**（只读写一个 bool）。

    用法：作业内部在每段之间 `if token.cancelled: raise Cancelled`，
    或者用 `token.check()`（届时抛 `Cancelled`）。
    """

    __slots__ = ("_cancelled",)

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled

    def check(self) -> None:
        if self._cancelled:
            raise Cancelled("作业已取消")


class Cancelled(Exception):
    """作业被取消（**不是错误**，UI 不应弹错误框）。"""


class Job(QThread):
    """一次后台作业：`fn(token, report)`，可分段推进、可取消。

    信号：
    * `progress(int)`：0–100（**已节流**，最多每 `throttle_ms` 一次）
    * `done(object)`：返回值（作业正常结束）
    * `failed(str)`：人类可读的错误（含类型与消息，不含完整栈——栈进日志）
    * `cancelled()`：被取消
    """

    progress = Signal(int)
    done = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, fn, throttle_ms: int = 120, parent=None):
        super().__init__(parent)
        self._fn = fn
        self._token = CancelToken()
        self._throttle = max(0, throttle_ms) / 1000.0
        self._last_emit = 0.0
        self._last_pct = -1

    # ---- 给 UI 用 ----
    def cancel(self) -> None:
        """请求取消（协作式：作业在自己的检查点退出）。"""
        self._token.cancel()

    # ---- 给作业用 ----
    def report(self, pct: float) -> None:
        """报告进度 0–100。**节流**：同值不重发、间隔不足则丢弃。"""
        v = int(max(0.0, min(100.0, pct)))
        now = time.monotonic()
        if v == self._last_pct and v != 100:
            return
        if v != 100 and now - self._last_emit < self._throttle:
            return
        self._last_pct = v
        self._last_emit = now
        self.progress.emit(v)

    def run(self) -> None:                                  # noqa: D401 (Qt 接口)
        try:
            result = self._fn(self._token, self.report)
        except Cancelled:
            self.cancelled.emit()
            return
        except BaseException as e:                          # noqa: BLE001
            # 完整栈进日志（诊断包可带走），信号里只给人类可读的一行
            try:
                from ... import diagnostics
                diagnostics._log.error("后台作业失败：%s\n%s",
                                       e, traceback.format_exc())
            except Exception:                               # noqa: BLE001
                pass
            self.failed.emit("%s: %s" % (type(e).__name__, e))
            return
        self.progress.emit(100)
        self.done.emit(result)


class JobRunner(QObject):
    """持有作业引用并串行化执行（**防 GC 硬崩** + 防并发写同一文件）。

    本仓已踩过"QThread 被 GC 而线程仍在跑 ⇒ 进程硬崩"（`window.py` 的注释）。
    这里用字典按 key 持引用，作业结束后才移除——同时天然实现了"同一 key
    不并发"（重复提交返回 False）。
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._jobs: dict = {}

    def busy(self, key: str = "default") -> bool:
        j = self._jobs.get(key)
        return j is not None and j.isRunning()

    def any_busy(self) -> bool:
        return any(j.isRunning() for j in self._jobs.values())

    def cancel(self, key: str = "default") -> None:
        j = self._jobs.get(key)
        if j is not None:
            j.cancel()

    def submit(self, fn, key: str = "default", *,
               on_progress=None, on_done=None, on_failed=None,
               on_cancelled=None, throttle_ms: int = 120) -> Job | None:
        """提交作业。**同 key 已在跑 ⇒ 返回 None**（不排队，调用方应提示）。"""
        if self.busy(key):
            return None
        job = Job(fn, throttle_ms=throttle_ms, parent=None)
        self._jobs[key] = job

        def _cleanup():
            self._jobs.pop(key, None)
        if on_progress is not None:
            job.progress.connect(on_progress)
        if on_done is not None:
            job.done.connect(on_done)
        if on_failed is not None:
            job.failed.connect(on_failed)
        if on_cancelled is not None:
            job.cancelled.connect(on_cancelled)
        for sig in (job.done, job.failed, job.cancelled):
            sig.connect(_cleanup)
        job.finished.connect(job.deleteLater)
        job.start()
        return job
