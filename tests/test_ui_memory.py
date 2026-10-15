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
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_SRC = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

import numpy as np  # noqa: E402
from PySide6.QtCore import QDeadlineTimer, QEvent, QEventLoop, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

APP = QApplication.instance() or QApplication([])

from audiocheck.ui import theme as T  # noqa: E402
from audiocheck.ui import window as W  # noqa: E402
from audiocheck.ui.charts import (  # noqa: E402
    SpectrogramCanvas,
    SpectrumCanvas,
    WaterfallCanvas,
)

PP = T.plot_palette(T.DARK.p)


def spec_like(cols: int = 171, bins: int = 2049):
    """一份与真实频谱图同量级的数据：4096 点 STFT × 12 s → (2049, 171) float32。"""
    rng = np.random.default_rng(7)
    t = np.linspace(0, 12, cols)
    f = np.linspace(0, 22050, bins)
    S = (rng.standard_normal((bins, cols)) * 12 - 60).astype(np.float32)
    return t, f, S


def curve_result() -> dict:
    """一份频谱曲线结果（`curve` 是 engine 产出的 [[f, dB], …] 形式）。"""
    f = np.geomspace(20, 22050, 600)
    return {"file": "a.wav", "sr": 44100, "level": "ok", "cutoff_hz": 18000,
            "curve": [[float(a), float(b)] for a, b in zip(f, -20 - np.log10(f))]}


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
        # 必须真的显示：画布是惰性重画的（不可见时只标脏，等 showEvent 补画），
        # 不 show 的话 set_data 不会画任何东西，测出来的"稳定"是假的稳定。
        self.cv.show()
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
        cv.resize(900, 500)
        cv.show()
        cv.set_data(curve_result())
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

    def _register(self, paths):
        """把路径登记进列表。

        `_view_ready` 现在会**先确认路径还在列表里**（worker 慢一步返回时，
        条目可能已被删掉；那种结果属于"没人要的东西"，不该占缓存）。
        所以灌缓存必须先登记——这也让测试更贴近真实调用路径。
        """
        w = self.w
        w.items = [{"path": p, "file": os.path.basename(p), "result": None} for p in paths]
        w._idx = {it["path"]: i for i, it in enumerate(w.items)}

    def test_oversized_cache_is_evicted_by_bytes(self):
        w = self.w
        per_entry = 512 * 256 * 4                    # S 的形状与 dtype 决定单份大小
        self._reset(per_entry * 3)                   # 预算 = 3 份
        t, f = np.zeros(256), np.zeros(512)
        self._register(["/mem_%d.wav" % i for i in range(12)])
        for i in range(12):
            S = np.zeros((512, 256), dtype=np.float32) + i
            w._view_ready("/mem_%d.wav" % i, "spec", 4096, t, f, S)
            self.assertLessEqual(w._cache_bytes(), w.CACHE_MAX_BYTES,
                                 "加第 %d 份后缓存越过了字节预算" % i)
            self.assertGreaterEqual(len(w._cache), 1, "至少该留下一份可用的数据")
        self.assertIn(("/mem_11.wav", "spec", 4096), w._cache, "最新的那份不该被淘汰")

    def test_fifo_order_drops_oldest_first(self):
        w = self.w
        per_entry = 512 * 256 * 4
        self._reset(per_entry * 2)
        t, f = np.zeros(256), np.zeros(512)
        self._register(["/fifo_%d.wav" % i for i in range(5)])
        for i in range(5):
            w._view_ready("/fifo_%d.wav" % i, "spec", 4096, t, f,
                          np.zeros((512, 256), dtype=np.float32))
        keys = list(w._cache)
        self.assertNotIn(("/fifo_0.wav", "spec", 4096), keys, "应当先丢最旧的")
        self.assertEqual(keys[-1], ("/fifo_4.wav", "spec", 4096), "最新的一份必须还在")

    def test_different_fft_tiers_do_not_share_cache(self):
        """B14：视图精度可调后，**同一路径不同 FFT 档是两份数据**。

        键里少了 nperseg 的后果：先按 4096 算一次、切成 8192，第二次直接命中
        旧缓存 ⇒ 图上"换了没反应"。这是静默失效，所以钉一条回归。
        """
        w = self.w
        self._register(["/tier.wav"])
        t, f = np.zeros(256), np.zeros(512)
        w._view_ready("/tier.wav", "spec", 4096, t, f,
                      np.zeros((512, 256), dtype=np.float32))
        w._view_ready("/tier.wav", "spec", 8192, t, f,
                      np.zeros((512, 256), dtype=np.float32))
        self.assertEqual(len(w._cache), 2, "同路径不同 FFT 档必须各存一份")
        self.assertIn(("/tier.wav", "spec", 4096), w._cache)
        self.assertIn(("/tier.wav", "spec", 8192), w._cache)

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
        self.assertEqual(list(w._view_workers), [(p, "spec", 4096)],
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
        self.assertEqual(list(w._view_workers), [(p, "spec", 4096)],
                         "切到频谱图页时应当补算 spec")
        drain(w)

    @classmethod
    def tearDownClass(cls):
        cls.w.close()


class TestRedrawIsLazy(unittest.TestCase):
    """看不见的画布不重画 —— 一次重绘 90–370 ms，白付不起。"""

    def test_hidden_canvas_defers_until_shown(self):
        cv = SpectrogramCanvas()
        cv.apply_theme(PP)
        cv.set_data(None, spec_like())
        self.assertTrue(cv._stale, "不可见的画布不该立刻重画")
        self.assertIsNone(cv._cbar, "不可见时连图元都不该建")
        cv.show()
        self.assertFalse(cv._stale, "show() 时必须把欠的画补上")
        self.assertIsNotNone(cv._cbar, "补画后该有色条")

    def test_theme_switch_does_not_redraw_hidden_canvas(self):
        cv = SpectrumCanvas()
        cv.apply_theme(T.plot_palette(T.LIGHT.p))
        cv.resize(900, 500)
        cv.show()
        cv.set_data(curve_result())
        self.assertFalse(cv._stale)
        cv.hide()                              # 等价于"切到别的页去了"
        cv.apply_theme(PP)
        self.assertTrue(cv._stale, "看不见的画布不该在换肤时重画")
        cv.show()
        self.assertFalse(cv._stale, "切回来必须重画")


class TestWaterfallDragIsThrottled(unittest.TestCase):
    """3D 拖拽必须限流：本机实测一帧 367 ms（2.7 fps），不限流会越拖越卡。"""

    def setUp(self):
        self.cv = WaterfallCanvas()
        self.cv.apply_theme(PP)
        self.cv.resize(900, 500)
        self.cv.show()
        t = np.linspace(0, 25, 44)
        f = np.linspace(0, 22050, 512)
        S = (np.random.default_rng(3).standard_normal((512, 44)) * 10 - 60).astype(np.float32)
        self.cv.set_data(None, (t, f, S))
        self.calls: list[int] = []
        self.cv.redraw = lambda: self.calls.append(1)   # 真画一帧 367 ms，这里只数次数

    def _ev(self, x, y):
        return SimpleNamespace(x=x, y=y, button=1, inaxes=self.cv.ax, xdata=None)

    def test_drag_redraws_are_coalesced(self):
        self.cv._on_press(self._ev(100, 100))
        for i in range(20):                    # 20 次连续移动 = 一次快速拖拽
            self.cv._on_drag(self._ev(100 + i, 100))
        self.assertGreaterEqual(len(self.calls), 1, "按下拖拽后至少要有一帧响应")
        self.assertLessEqual(len(self.calls), 2,
                             "20 次移动触发了 %d 次重绘：拖拽没有限流" % len(self.calls))

    def test_every_move_still_updates_angle_and_release_draws_final(self):
        """限流只限重绘，不能丢状态：松手那一帧必须是最终角度。"""
        self.cv._on_press(self._ev(100, 100))
        for i in range(1, 21):
            self.cv._on_drag(self._ev(100 + i * 3, 100))
        self.assertAlmostEqual(self.cv._azim, -58 - 60 * 0.35, places=6,
                               msg="被节流掉的移动也必须更新角度")
        self.cv.mouseReleaseEvent(QMouseEvent(
            QEvent.Type.MouseButtonRelease, QPointF(10, 10),
            Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier))
        self.assertEqual(len(self.calls), 2, "松手要补一帧最终画面")


def pump(ms: int = 60) -> None:
    """走事件循环（本文件自己的版本；必须派发 DeferredDelete）。"""
    deadline = QDeadlineTimer(int(ms))
    while not deadline.hasExpired():
        APP.processEvents(QEventLoop.AllEvents, 5)
    APP.sendPostedEvents(None, QEvent.DeferredDelete)


class TestCursorBus(unittest.TestCase):
    """B14 补强⑤：跨画布频率游标（三图联动）的三条性质。

    联动看着是个小功能，但它的失败方式全是**静默**的：线画到轴外被裁掉
    （像没生效）、来源画布自己收到自己的广播（可能自激）、3D 那张每个鼠标
    移动都触发 257 ms 重绘（越用越卡）。三条各钉一个。
    """

    def setUp(self):
        import numpy as np

        from audiocheck.ui import theme as T
        from audiocheck.ui.charts import SpectrogramCanvas
        pp = T.plot_palette(T.DARK.p, T.DARK)
        f = np.geomspace(20, 20000, 800)
        ff = np.linspace(0, 22050, 400)
        S = (np.random.default_rng(1).standard_normal((400, 200)) * 10 - 60).astype("float32")
        # curve 的**真实形态是 list-of-lists**（`core/engine.py:73` 为 JSON 友好
        # 特意转成那样）。第一版这里传了 ndarray，`_has_data()` 的
        # `bool(self.r.get("curve"))` 当场抛 "truth value of an array is
        # ambiguous"——**是测试数据不真实**，不是生产代码的错。
        self.curve = [[round(float(a), 1), round(float(b), 2)]
                      for a, b in zip(f, -40 - 10 * np.log10(f / 20))]
        # ⚠ **每条用例自建自拆**：第一版把画布建在 setUpClass 里，而本文件前一个
        # 测试类已经 `deleteLater()` 过同名对象，本类再摸就撞
        # "Internal C++ object already deleted"。
        self.sg = SpectrogramCanvas()
        self.sg.apply_theme(pp)
        self.sg.set_data(None, (np.linspace(0, 20, 200), ff, S))
        self.sg.resize(700, 420)
        self.sg.show()
        pump()

    def tearDown(self):
        self.sg.close()
        self.sg.deleteLater()
        pump()

    def test_source_does_not_receive_its_own_broadcast(self):
        """来源画布不能收到自己的广播（否则会自激 / 双重刷新）。

        频谱图没有 `_hover` 字段，所以这里验**它自己的参考线不被自己点亮**
        ——`set_crosshair` 是"别处指过来"的入口，来源就是它自己时不该画。
        """
        self.sg.clear_crosshair()
        from audiocheck.ui.charts import CURSOR
        CURSOR.emit(1000.0, self.sg)
        self.assertTrue(self.sg._xhair is None or not self.sg._xhair.get_visible(),
                        "频谱图收到了自己的广播——来源画布被漏过了")

    def test_out_of_range_frequency_draws_nothing(self):
        """频率落在轴外 ⇒ **不画**（画到轴外会被裁掉，看着像没生效）。"""
        self.sg.set_crosshair(0.0)          # 20 Hz 以下：轴外
        self.assertTrue(self.sg._xhair is None or not self.sg._xhair.get_visible(),
                        "轴外频率画了参考线——它会被裁掉，等于骗人")
        self.sg.set_crosshair(-5.0)
        self.assertTrue(self.sg._xhair is None or not self.sg._xhair.get_visible())
        self.sg.clear_crosshair()

    def test_waterfall_crosshair_is_throttled_not_instant(self):
        """3D 联动必须节流（一次 257 ms），且最终会落到那一帧上。"""
        import numpy as np

        from audiocheck.ui import theme as T
        from audiocheck.ui.charts import WaterfallCanvas
        wf = WaterfallCanvas()
        wf.apply_theme(self.pp if hasattr(self, "pp") else T.plot_palette(T.DARK.p, T.DARK))
        ff = np.linspace(0, 22050, 400)
        S = (np.random.default_rng(1).standard_normal((400, 44)) * 10 - 60).astype("float32")
        wf.set_data(None, (np.linspace(0, 25, 44), ff, S))
        wf.resize(700, 420)
        wf.show()
        pump()
        try:
            for i in range(6):              # 6 次连续广播 = 一次快速拖动
                wf.set_crosshair(1000.0 + i)
            self.assertTrue(wf._x_timer.isActive(),
                            "3D 联动没有节流——每次广播都会触发整帧重画（257 ms）")
            self.assertEqual(wf._xhz, 1005.0, "节流不能不更新状态：最终必须是最新频率")
        finally:
            wf._xhz = None
            wf._x_timer.stop()
            wf.close()
            wf.deleteLater()
            pump()

    def test_dead_subscriber_is_dropped_not_crashed_on(self):
        """**订阅者生命周期**：画布删掉之后，总线不许再去调它。

        第一版总线只 append 不退订 ⇒ 关掉一批画布后再广播就撞
        `Internal C++ object already deleted`。这条 bug 只在"先建过又关掉过"
        之后复现（单跑新用例看不见，跑全量才炸），所以必须**显式**建-删-广播。
        """
        import numpy as np

        from audiocheck.ui import theme as T
        from audiocheck.ui.charts import CURSOR, SpectrogramCanvas
        pp = T.plot_palette(T.DARK.p, T.DARK)
        doomed = SpectrogramCanvas()
        doomed.apply_theme(pp)
        ff = np.linspace(0, 22050, 400)
        doomed.set_data(None, (np.linspace(0, 20, 200), ff,
                               (np.random.default_rng(2).standard_normal((400, 200))
                                * 10 - 60).astype("float32")))
        doomed.show()
        pump()
        self.assertIn(doomed, CURSOR._subs, "新画布没被登记进总线")
        doomed.close()
        doomed.deleteLater()
        pump()                       # 派发 DeferredDelete，C++ 对象真的没了
        # 关键：这里**不许**抛 RuntimeError
        CURSOR.emit(1000.0, self.sg)
        self.assertNotIn(doomed, CURSOR._alive(), "死掉的订阅者还留在总线上")

    def test_clear_hides_the_line(self):
        self.sg.set_crosshair(1000.0)
        self.assertIsNotNone(self.sg._xhair)
        self.sg.clear_crosshair()
        self.assertFalse(self.sg._xhair.get_visible(), "clear 之后参考线还挂着")


if __name__ == "__main__":
    unittest.main(verbosity=2)
