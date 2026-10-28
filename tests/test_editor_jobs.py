"""后台作业执行器的判据（可取消 / 进度节流 / 不阻塞 UI / 防 GC 硬崩）。

## 为什么这几条必须有

编辑工作站的导出此前**同步跑在 UI 线程**：10 分钟素材整轨渲染约 24 秒，
这期间窗口完全不响应（Windows 会挂"未响应"）。改成后台线程后，
四个性质必须钉住——漏任何一个都会退回"看起来异步、实际还是卡"：

1. **真的在别的线程**（用 thread id 判，不是看有没有 QThread 对象）；
2. **取消是协作式的、能真的停下**（不是让令牌变成摆设）；
3. **进度节流**（每像素发一次信号会淹没事件队列）；
4. **异常不吞**（后台线程抛异常默认没人接，会静默消失）。
"""
from __future__ import annotations

import sys
import threading
import time

import pytest

sys.path.insert(0, "src")

pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer  # noqa: E402

from audiocheck.ui.editor.jobs import (  # noqa: E402
    Cancelled,
    CancelToken,
    Job,
    JobRunner,
)


@pytest.fixture(scope="module")
def app():
    a = QCoreApplication.instance() or QCoreApplication([])
    yield a


def _pump(ms: int = 50) -> None:
    """跑一会儿事件循环（让信号真正投递）。"""
    loop = QEventLoop()
    QTimer.singleShot(ms, loop.quit)
    loop.exec()


def test_cancel_token_raises_on_check():
    """`check()` 在已取消时抛 `Cancelled`（不是返回 False——调用方容易漏判）。"""
    t = CancelToken()
    t.check()                                   # 未取消：不抛
    t.cancel()
    assert t.cancelled is True
    with pytest.raises(Cancelled):
        t.check()


def test_job_runs_off_the_main_thread(app):
    """★ 作业必须真的跑在**另一个线程**（不是主线程里同步跑然后假装异步）。"""
    main_tid = threading.get_ident()
    seen = {}

    def work(token, report):
        seen["tid"] = threading.get_ident()
        return 42

    job = Job(work)
    got = []
    job.done.connect(lambda v: got.append(v))
    job.start()
    job.wait(5000)
    _pump()
    assert seen["tid"] != main_tid, "作业跑在主线程上 ⇒ 根本没有异步"
    assert got == [42]


def test_job_cancellation_stops_work(app):
    """★ 取消必须真的停下（协作式检查点生效），并发出 `cancelled` 信号。"""
    progressed = []
    hit = []

    def work(token, report):
        for i in range(200):
            token.check()                     # 检查点
            progressed.append(i)
            time.sleep(0.002)
        hit.append("finished")
        return "done"

    job = Job(work, throttle_ms=0)
    was_cancelled = []
    job.cancelled.connect(lambda: was_cancelled.append(True))
    job.start()
    _pump(80)
    job.cancel()
    job.wait(5000)
    _pump()
    assert was_cancelled == [True], "应发出 cancelled 信号"
    assert not hit, "取消后不应跑到终点（说明检查点没生效）"
    assert len(progressed) < 200, "取消太晚（跑完了 200 步）"


def test_job_reports_progress_monotonically(app):
    """进度必须单调不减，且最后是 100。"""
    seen = []

    def work(token, report):
        for i in range(0, 101, 10):
            report(i)
            time.sleep(0.005)
        return None

    job = Job(work, throttle_ms=0)
    job.progress.connect(seen.append)
    job.start()
    job.wait(5000)
    _pump()
    assert seen == sorted(seen), "进度不能回退：%s" % seen
    assert seen[-1] == 100, "最后一次进度必须是 100：%s" % seen


def test_job_progress_is_throttled(app):
    """★ 节流：高频 report 不应原样转成高频信号（否则事件队列被淹没）。"""
    calls = []

    def work(token, report):
        for i in range(1000):
            report(i / 10.0)                  # 1000 次，0–100
        return None

    job = Job(work, throttle_ms=500)          # 500 ms 节流 ⇒ 1000 次最多发几次
    job.progress.connect(calls.append)
    job.start()
    job.wait(5000)
    _pump()
    assert len(calls) <= 5, "节流失效：1000 次 report 发出了 %d 个信号" % len(calls)
    assert calls[-1] == 100, "节流不能吞掉最终的 100"


def test_job_failure_is_surfaced_not_swallowed(app):
    """★ 后台异常必须通过 `failed` 信号报出来（不是静默消失）。"""
    errs = []

    def work(token, report):
        raise RuntimeError("后台探针失败")

    job = Job(work)
    job.failed.connect(errs.append)
    job.start()
    job.wait(5000)
    _pump()
    assert errs, "后台异常被吞掉了"
    assert "RuntimeError" in errs[0] and "后台探针失败" in errs[0]


def test_runner_rejects_concurrent_same_key(app):
    """同一个 key 不并发（防两次导出写同一个文件）。"""
    runner = JobRunner()
    started = []
    release = threading.Event()

    def slow(token, report):
        started.append(1)
        release.wait(3.0)
        return 1

    j1 = runner.submit(slow, "export")
    assert j1 is not None
    _pump(30)
    j2 = runner.submit(slow, "export")
    assert j2 is None, "同 key 已在跑时必须拒绝"
    release.set()
    j1.wait(5000)
    _pump()
    assert len(started) == 1


def test_runner_clears_key_after_completion(app):
    """作业结束后 key 必须释放（否则后续导出永远被拒）。"""
    runner = JobRunner()

    def quick(token, report):
        return "ok"

    j = runner.submit(quick, "export")
    assert j is not None
    j.wait(5000)
    _pump()
    assert not runner.busy("export")
    j2 = runner.submit(quick, "export")
    assert j2 is not None, "上一个结束后应能再次提交"
    j2.wait(5000)
    _pump()
