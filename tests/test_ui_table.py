"""表格增量刷新与线程生命周期：四条"不该有的行为"的反面。

## 为什么值得单独立一份

这五条各自对应一个**不报错但会出事**的场景：

1. 分析途中再按「开始分析」→ `self.worker` 被覆盖 → 正在跑的 QThread
   失去最后引用 → PySide 可能在 C++ 侧销毁它 → **进程直接没**（不是异常）。
2. 本批结束后队列里还有文件 → 用户得守在机器旁按第二次。
3. 删掉条目后缓存里还留着它的数据 → 白占字节预算，把有用的挤出去。
4. worker 慢一步返回，而条目已被删除 → 结果写给"没人要"的路径。
5. 分析结果回来时重建整张表 → O(N²)，文件越多越卡；
   而**增量更新单行**必须与全量重建**走同一处填行代码**，否则两套口径迟早走岔。
"""
from __future__ import annotations

import os
import sys
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import window as W  # noqa: E402


def result(path, level="ok", dr=12.0, cutoff=18.0):
    """一份**够用**的结果：自动选中会一路走到指标卡与元数据表，
    那些地方要 format / channels / seconds / size_mb 等字段（缺一个就 KeyError）。"""
    return {"path": path, "file": os.path.basename(path), "sr": 44100, "bits": "PCM_16",
            "format": "flac", "channels": 2, "seconds": 214, "size_mb": 33.4,
            "level": level, "dr_db": dr, "clip_pct": 0.0, "cutoff_khz": cutoff,
            "cutoff_hz": cutoff * 1000, "cutoff_ratio": cutoff / 22.05,
            "verdict": "合格", "curve": [[20.0, -1.0], [20000.0, -2.0]],
            "bands_pct": {}, "meta": {}}


class FakeWorker:
    """只回答 `isRunning()`——测的就是"正在跑时不许被覆盖"。

    `wait` / `stop` 是 `closeEvent` 会调的（窗口关闭时要等线程收尾），
    少了它们 tearDown 会在 closeEvent 里抛 AttributeError。
    """

    def __init__(self, running=True):
        self._running = running

    def isRunning(self):                                        # noqa: N802
        return self._running

    def wait(self, _ms=0):
        return True

    def stop(self):
        self._running = False


def row_of(w, path) -> int:
    """按路径找行号。**不能假设"第 0 行 = 第一个入队的文件"**——
    表格是排序过的（默认按文件名升序），行序由文件名决定。"""
    for r in range(w.table.rowCount()):
        cell = w.table.item(r, 0)
        if cell is not None and cell.data(Qt.UserRole) == path:
            return r
    return -1


class TestWorkerLifecycle(unittest.TestCase):
    def setUp(self):
        self.w = W.MainWindow()
        self.w.items, self.w._idx, self.w.queue = [], {}, []

    def tearDown(self):
        self.w.close()

    def test_second_start_keeps_the_running_worker(self):
        self.w.enqueue(["C:/a.wav", "C:/b.wav"])
        dummy = FakeWorker(True)
        self.w.worker = dummy
        queued = list(self.w.queue)
        self.w.start()
        self.assertIs(self.w.worker, dummy, "运行中的 worker 被覆盖了 → 线程会被 GC")
        self.assertEqual(self.w.queue, queued, "文件被吞掉了（应当留在队列里）")

    def test_finish_continues_the_queue(self):
        calls = []
        self.w.start = lambda: calls.append(1)
        self.w.queue = ["C:/later.wav"]
        self.w._finish(3)
        self.assertEqual(calls, [1], "本批结束后没有接着分析队列里的文件")

    def test_finish_without_queue_does_not_restart(self):
        calls = []
        self.w.start = lambda: calls.append(1)
        self.w.queue = []
        self.w._finish(3)
        self.assertEqual(calls, [])


class TestCacheHygiene(unittest.TestCase):
    def setUp(self):
        self.w = W.MainWindow()
        self.w.items, self.w._idx, self.w.queue = [], {}, []

    def tearDown(self):
        self.w.close()

    def test_removing_selected_rows_drops_their_cache(self):
        self.w.enqueue(["C:/a.wav", "C:/b.wav"])
        self.w.items[0]["result"] = result("C:/a.wav")
        self.w.items[1]["result"] = result("C:/b.wav")
        self.w.refresh_table()
        self.w._cache[("C:/a.wav", "spec", 4096)] = (None, None, None)
        self.w._cache[("C:/b.wav", "spec", 4096)] = (None, None, None)
        self.w.table.setCurrentCell(row_of(self.w, "C:/a.wav"), 0)
        self.w._remove_selected()
        self.assertNotIn(("C:/a.wav", "spec", 4096), self.w._cache, "被删条目的缓存没清")
        self.assertIn(("C:/b.wav", "spec", 4096), self.w._cache)

    def test_view_ready_ignores_removed_paths(self):
        self.w.enqueue(["C:/a.wav"])
        import numpy as np
        t, f = np.array([0.0, 1.0]), np.array([100.0, 1000.0])
        S = np.zeros((2, 2), dtype="float32")
        self.w._view_ready("C:/gone.wav", "spec", 4096, t, f, S)
        self.assertEqual(self.w._cache, {}, "已不存在的路径被写进了缓存")


class TestIncrementalRows(unittest.TestCase):
    def setUp(self):
        self.w = W.MainWindow()
        self.w.items, self.w._idx, self.w.queue = [], {}, []

    def tearDown(self):
        self.w.close()

    def _texts(self, row):
        return [self.w.table.item(row, c).text() for c in range(self.w.table.columnCount())]

    def test_update_row_changes_only_that_row(self):
        self.w.enqueue(["C:/a.wav", "C:/b.wav", "C:/c.wav"])
        self.w.refresh_table()
        before = [self._texts(r) for r in range(3)]
        self.w.items[self.w._idx["C:/b.wav"]]["result"] = result("C:/b.wav",
                                                                 level="bad", dr=3.0)
        self.assertTrue(self.w._update_row("C:/b.wav"))
        after = [self._texts(r) for r in range(3)]
        hit = row_of(self.w, "C:/b.wav")
        for r in range(3):
            if r == hit:
                self.assertNotEqual(after[r], before[r], "目标行没有更新")
            else:
                self.assertEqual(after[r], before[r], "无关行被改动了")
        self.assertEqual(self.w.table.rowCount(), 3, "行数变了 → 这不是增量更新")

    def test_default_order_is_filename_ascending(self):
        """默认按文件名**升序**：`setSortingEnabled(True)` 会套用 Qt 的默认指示，
        而那是「第 0 列、降序」——整张专辑会最后一首排最上面。"""
        self.w.enqueue(["C:/zeta.wav", "C:/alpha.wav", "C:/mid.wav"])
        self.w.refresh_table()
        names = [self.w.table.item(r, 0).text() for r in range(3)]
        self.assertEqual(names, ["alpha.wav", "mid.wav", "zeta.wav"])

    def test_add_result_updates_row_without_rebuilding(self):
        self.w.enqueue(["C:/a.wav"])
        self.w.refresh_table()
        self.assertEqual(self.w.table.item(0, 5).text(), "待分析")
        self.w._add_result(result("C:/a.wav"))
        self.assertEqual(self.w.table.item(0, 5).text(), "合格")
        self.assertIsNotNone(self.w.table.item(0, 3))

    def test_both_paths_fill_rows_identically(self):
        """全量重建与增量更新必须得到**一模一样**的一行。

        两条填行路径分头实现时，改了一边忘了另一边是必然发生的事
        （对齐方式、提示文案、颜色都各改各的）。所以它们共用 `_fill_row`，
        这条断言就是钉住这个结构。
        """
        self.w.enqueue(["C:/a.wav"])
        self.w.items[0]["result"] = result("C:/a.wav", level="warn")
        self.w.refresh_table()
        full = self._texts(0)
        self.w.table.removeRow(0)
        self.w._row(self.w.items[0])
        self.assertEqual(self._texts(0), full)


if __name__ == "__main__":
    unittest.main(verbosity=2)
