"""内存回归：交互过程中的图元 / 缓存 / 线程必须**有界**。

## 为什么值得单独立一份测试

这类问题不报错、不崩溃、日志里一个字都没有，只表现为
「开着它翻半小时文件，任务管理器里 1 GB」。修它之前用
`packaging/mem_probe.py` 实测到的数字：

    频谱图每次重画      +17.9 MB   （figure.axes 从 2 涨到 31）
    点选 10 个文件      +178.8 MB
    切换主题 30 次      +576.5 MB
    一整段流程结束净增  +845.7 MB

根因是 `SpectrogramCanvas.redraw()` 每次 `fig.colorbar(...)` 新建一个色条 Axes，
而 `ax.clear()` 带不走它；色条握着 mappable → 握着那份 S 矩阵。**不可回收**。

下面四条断言分别钉住一类「必须有界」的东西，任何一条挂掉都意味着
内存会随使用时间线性增长：

  1. 图元：重画 N 次后 figure.axes 恒为 2（主图 + 1 个色条），且色条是同一个对象
  2. 图元：悬停 N 次后轴上的 lines/texts/collections 数量不变
  3. 缓存：按**字节**淘汰，超预算就丢最旧的，且至少保留 1 项
  4. 线程：同一 (文件, 视图) 不重复起线程；跑完要从登记表里摘掉

这些断言都是**对象计数**，不是进程内存（后者受分配器与字体缓存影响，天生抖动，
放进 CI 只会得到一堆偶发失败）。
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import numpy as np  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.charts import SpectrogramCanvas, SpectrumCanvas  # noqa: E402

PP = T.plot_palette(T.DARK.p)


def spec_like(cols: int = 171, bins: int = 2049):
    """一份与真实频谱图同量级的数据：4096 点 STFT × 12 s → (2049, 171) float32。"""
    rng = np.random.default_rng(7)
    t = np.linspace(0, 12, cols)
    f = np.linspace(0, 22050, bins)
    S = (rng.standard_normal((bins, cols)) * 12 - 60).astype(np.float32)
    return t, f, S


def artist_count(ax) -> int:
    """轴上图元总数：悬停层只该"换"图元，不该"加"图元。"""
    return len(ax.lines) + len(ax.texts) + len(ax.collections)


def drain(w, timeout: float = 20.0) -> bool:
    """等所有视图线程结束（线程还在跑时销毁窗口会让进程硬崩）。"""
    end = time.time() + timeout
    while time.time() < end and w._view_workers:
        APP.processEvents()
        time.sleep(0.01)
    return not w._view_workers


class TestSpectrogramArtistsAreBounded(unittest.TestCase):
    """频谱图：色条必须复用，figure.axes 必须恒定。"""

    def setUp(self):
        self.cv = SpectrogramCanvas()
        self.cv.apply_theme(PP)
        self.data = spec_like()

    def test_colorbar_is_built_once(self):
        cv = self.cv
        cv.set_data(None, self.data)
        self.assertIsNotNone(cv._cbar, "首次画图应建出色条")
        first = cv._cbar
        for _ in range(12):
            cv.set_data(None, self.data)
        self.assertEqual(len(cv.fig.axes), 2,
                         "figure.axes 应为 2（主图 + 1 个色条），实际 %d 个"
                         "——色条在每次重画时被新建了" % len(cv.fig.axes))
        self.assertIs(cv._cbar, first,
                      "色条被重建：旧色条连同它握着的 S 矩阵会被 figure 一直引用")
        # 色条轴内部也不该堆图元（实测稳定值 2）
        self.assertLessEqual(len(first.ax.collections), 3,
                             "色条轴内的 collection 在累积")

    def test_axes_stable_across_theme_switches(self):
        """换肤会触发 redraw，这条路径曾经是"每切一次主题涨 19 MB"。"""
        cv = self.cv
        cv.set_data(None, self.data)
        for i in range(8):
            cv.apply_theme(T.plot_palette((T.LIGHT if i % 2 else T.DARK).p))
            cv.set_data(None, self.data)
        self.assertEqual(len(cv.fig.axes), 2)

    def test_empty_data_hides_colorbar_without_dropping_it(self):
        """清空后色条该藏起来，但不能销毁——再选文件还得用同一个。"""
        cv = self.cv
        cv.set_data(None, self.data)
        cb = cv._cbar
        cv.set_data(None, None, "生成中…")
        self.assertFalse(cb.ax.get_visible(), "空数据时色条应隐藏")
        cv.set_data(None, self.data)
        self.assertTrue(cb.ax.get_visible(), "有数据时色条应恢复可见")
        self.assertIs(cv._cbar, cb)
        self.assertEqual(len(cv.fig.axes), 2)


class TestHoverArtistsAreBounded(unittest.TestCase):
    """频谱曲线：悬停只重画自己那 3 个图元。"""

    def test_hover_does_not_accumulate_artists(self):
        cv = SpectrumCanvas()
        cv.apply_theme(PP)
        f = np.geomspace(20, 22050, 600)
        r = {"file": "a.wav", "sr": 44100, "level": "ok", "cutoff_hz": 18000,
             "curve": [[float(a), float(b)] for a, b in zip(f, -20 - np.log10(f))]}
        cv.set_data(r)
        n0 = artist_count(cv.ax)
        for i in range(200):
            cv._set_hover(20 * (1.28 ** (i % 60)))
        # 悬停层是"常驻 3 个图元 + 原地替换"，不是每次新增
        self.assertLessEqual(artist_count(cv.ax) - n0, 3,
                            "悬停层叠了 %d 个图元（应恒为 3）"
                            % (artist_count(cv.ax) - n0))
        cv._set_hover(None)
        self.assertEqual(artist_count(cv.ax), n0,
                         "鼠标移出后图元数从 %d 变成 %d：悬停层没删干净"
                         % (n0, artist_count(cv.ax)))
        cv.set_data(None)          # 收尾：清掉数据，别把轴留给下一个测试


class TestViewCacheIsBounded(unittest.TestCase):
    """视图缓存：按字节淘汰，不是按条数。"""

    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def _reset(self, budget: int):
        self.w._cache.clear()
        self.w.CACHE_MAX_BYTES = budget

    def test_oversized_cache_is_evicted_by_bytes(self):
        w = self.w
        per_entry = 512 * 256 * 4                    # S 的形状与 dtype 决定单份大小
        self._reset(per_entry * 3)                   # 预算 = 3 份
        t, f = np.zeros(256), np.zeros(512)
        for i in range(12):
            S = np.zeros((512, 256), dtype=np.float32) + i
            w._view_ready("/mem_%d.wav" % i, "spec", t, f, S)
            self.assertLessEqual(w._cache_bytes(), w.CACHE_MAX_BYTES,
                                 "加第 %d 份后缓存越过了字节预算" % i)
            self.assertGreaterEqual(len(w._cache), 1, "至少该留下一份可用的数据")
        self.assertIn(("/mem_11.wav", "spec"), w._cache, "最新的那份不该被淘汰")

    def test_fifo_order_drops_oldest_first(self):
        w = self.w
        per_entry = 512 * 256 * 4
        self._reset(per_entry * 2)
        t, f = np.zeros(256), np.zeros(512)
        for i in range(5):
            w._view_ready("/fifo_%d.wav" % i, "spec", t, f,
                          np.zeros((512, 256), dtype=np.float32))
        keys = list(w._cache)
        self.assertNotIn(("/fifo_0.wav", "spec"), keys, "应当先丢最旧的")
        self.assertEqual(keys[-1], ("/fifo_4.wav", "spec"), "最新的一份必须还在")

    @classmethod
    def tearDownClass(cls):
        cls.w.CACHE_MAX_BYTES = W.MainWindow.CACHE_MAX_BYTES
        cls.w._cache.clear()
        cls.w.close()


class TestViewWorkersAreBounded(unittest.TestCase):
    """视图线程：同一 key 不重复起，跑完摘掉登记。"""

    @classmethod
    def setUpClass(cls):
        cls.w = W.MainWindow()

    def tearDown(self):
        drain(self.w)

    def test_same_view_is_not_computed_twice(self):
        w, p = self.w, os.path.join(tempfile.gettempdir(), "audiocheck_missing.wav")
        w._load_view(p, "spec")
        w._load_view(p, "spec")
        self.assertEqual(list(w._view_workers), [(p, "spec")],
                         "同一个 (文件, 视图) 起了多个线程：会白读一遍盘 + 白做一遍 STFT")
        self.assertTrue(drain(w), "线程没有结束")
        self.assertEqual(w._view_workers, {}, "线程跑完后没从登记表摘掉")

    def test_only_the_visible_view_is_generated(self):
        """点选文件时只算当前页要的视图，切过去再补。"""
        w, p = self.w, "/mem_visible.wav"
        w.items = [{"path": p, "file": "a.wav",
                    "result": {"file": "a.wav", "sr": 44100, "level": "ok"}}]
        w._idx = {p: 0}
        w._last_sig = (p, 1)
        w._navigate("overview")
        self.assertEqual(w._view_workers, {},
                         "总览页不该为频谱图/瀑布图起线程")
        w._navigate("spectro")
        self.assertEqual(list(w._view_workers), [(p, "spec")],
                         "切到频谱图页时应当补算 spec")
        drain(w)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
