"""视图导航映射回归：VIEW_KEYS 必须与 QStackedWidget 的真实顺序一致。

为什么值得单独立一个测试文件：`VIEW_KEYS` 是一份**手写**的字符串列表，
而 `_stack()` 里 `addWidget` 的顺序是另一处事实。两者之间没有任何机制保证同步，
改一处忘另一处就会静默错位——实测症状：侧边栏点「元数据」显示的是对比画布，
真正的元数据页（两个 KVTable）永远到不了，而且**没有任何报错**。
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from PySide6.QtWidgets import QApplication, QTableWidget  # noqa: E402

APP = QApplication.instance() or QApplication([])


class TestViewKeysMatchStack(unittest.TestCase):
    """VIEW_KEYS[i] 声称的视图，必须是 stack.widget(i) 实际的那个。"""

    @classmethod
    def setUpClass(cls):
        from audiocheck.ui import window as W
        cls.W = W
        cls.w = W.MainWindow()

    def test_every_key_maps_to_expected_widget(self):
        w, W = self.w, self.W
        # 期望的控件类型（按 key）
        from audiocheck.ui.charts import (
            BandCanvas,
            CompareCanvas,
            OverviewCanvas,
            SpectrogramCanvas,
            SpectrumCanvas,
            StereoCanvas,
            TimelineCanvas,
            WaterfallCanvas,
        )
        expect = {
            "overview": None,            # 总览页是裸 QWidget，靠子控件区分
            "spectrum": SpectrumCanvas,
            "spectro": SpectrogramCanvas,
            "waterfall": WaterfallCanvas,
            "phase": StereoCanvas,       # B14 自研：立体声相位场
            "bands": BandCanvas,
            "timeline": TimelineCanvas,  # C4.3 响度维度：逐段 LUFS/真峰值/削波
            "compare": CompareCanvas,
            "batch": OverviewCanvas,
            "meta": None,                # 元数据页也是裸 QWidget
        }
        self.assertEqual(sorted(expect), sorted(W.MainWindow.VIEW_KEYS),
                         "期望集合与 VIEW_KEYS 不一致（有 key 增删？）")

        for i, key in enumerate(W.MainWindow.VIEW_KEYS):
            wd = w.stack.widget(i)
            if expect[key] is not None:
                self.assertIsInstance(
                    wd, expect[key],
                    "VIEW_KEYS[%d]=%r 应对应 %s，实际是 %s"
                    % (i, key, expect[key].__name__, type(wd).__name__))

    def test_meta_page_holds_the_two_tables(self):
        """元数据页必须真的装着 tb_meta / tb_metrics，否则「元数据」点了也白点。"""
        w, W = self.w, self.W
        page = w.stack.widget(W.MainWindow.VIEW_KEYS.index("meta"))
        tables = page.findChildren(QTableWidget)
        self.assertGreaterEqual(len(tables), 2,
                                "元数据页应含至少两个表格（容器标签 + 全部指标）")
        # 这两个表格必须就是 on_select 往里写数据的那两个
        self.assertTrue(any(t is w.tb_meta for t in tables))
        self.assertTrue(any(t is w.tb_metrics for t in tables))

    def test_overview_page_is_not_meta_page(self):
        """总览页与元数据页不能是同一个控件（曾因错位导致两者混淆）。"""
        w, W = self.w, self.W
        self.assertIsNot(w.stack.widget(W.MainWindow.VIEW_KEYS.index("overview")),
                         w.stack.widget(W.MainWindow.VIEW_KEYS.index("meta")))

    def test_navigate_reaches_the_right_widget(self):
        """_navigate(key) 之后，当前控件必须是对应那一个（端到端）。"""
        w, W = self.w, self.W
        from audiocheck.ui.charts import CompareCanvas
        for key in W.MainWindow.VIEW_KEYS:
            w._navigate(key)
            self.assertEqual(w.stack.currentIndex(), W.MainWindow.VIEW_KEYS.index(key))
        # 单独验一条最容易错的：compare 与 meta 不能互串
        w._navigate("compare")
        self.assertIsInstance(w.stack.currentWidget(), CompareCanvas)
        w._navigate("meta")
        self.assertIsNot(w.stack.currentWidget(), CompareCanvas)

    def test_empty_state_is_last_and_not_a_view_key(self):
        """空状态必须是最后一页，且不能被 VIEW_KEYS 覆盖到。"""
        w, W = self.w, self.W
        self.assertEqual(w._empty_idx, w.stack.count() - 1)
        self.assertLessEqual(len(W.MainWindow.VIEW_KEYS), w._empty_idx,
                             "VIEW_KEYS 不能占掉空状态那一页")


class TestSpectrumPowerMergeNotMean(unittest.TestCase):
    """B14：**判定用的谱**必须逐声道功率合并，不能 `x.mean(axis=1)` 压单声道。

    `mean` 会把 L/R **反相**的内容抵消掉，且**不报错**——而"反相"本身就是
    要查的录音缺陷。这条谱（`AnalysisContext.spectrum`）喂的是**结论**
    （截止频率 / 频谱倾斜 / 假 Hi-Res 盲检测），视图层 `views.py` 同型 bug
    已修但只管出图。两者都必须钉住。

    判据用四支**物理已知值**的信号，从四个方向夹死语义：
      反相 L=-R → 能量全在相位差里，均值下会消失、功率合并下**必须保留**；
      同相 L=+R → 与反相**电平必须完全相同**（总功率与相位无关）；
      双声道 vs 单声道 → 两只音箱 = 2 份能量，电平必须恰 **+3.01 dB**；
      静音右声道 → 功率为 0，电平必须与**真单声道文件**逐位相同。
    """

    FS = 44100
    TONE = 1000.0

    @classmethod
    def setUpClass(cls):
        import tempfile

        import numpy as np
        import soundfile as sf

        from audiocheck.core.context import AnalysisContext

        cls.tmp = tempfile.mkdtemp(prefix="audiocheck_antiphase_")
        t = np.arange(int(cls.FS * 2.0)) / cls.FS
        tone = (0.25 * np.sin(2 * np.pi * cls.TONE * t)).astype("float32")

        def write(name, arr):
            p = os.path.join(cls.tmp, name)
            sf.write(p, arr, cls.FS)
            return p

        cls.p_anti = write("anti.wav", np.stack([tone, -tone], axis=1))
        cls.p_phase = write("phase.wav", np.stack([tone, tone], axis=1))
        cls.p_mono2 = write("mono2.wav", np.stack([tone, np.zeros_like(tone)], axis=1))
        cls.p_mono1 = write("mono1.wav", tone[:, None])
        cls.Ctx = AnalysisContext

    def _level(self, path, lo=None, hi=None):
        """返回谱在 [lo, hi] 频带内的峰值电平（默认是 1 kHz 音所在的窄带）。"""
        lo = lo if lo is not None else self.TONE - 25
        hi = hi if hi is not None else self.TONE + 25
        f, db = self.Ctx(path).spectrum()
        self.assertIsNotNone(db, "spectrum() 返回空——三分位取样全被跳过")
        return float(db[(f >= lo) & (f <= hi)].max())

    def test_antiphase_content_survives(self):
        """反相内容必须在谱里活着（旧 `mean` 下它被抵消成静音）。"""
        self.assertGreater(self._level(self.p_anti) - self._level(self.p_anti, 4980, 5020),
                           40.0, "反相声道的 1 kHz 音消失了——又被均值抵消")

    def test_phase_relation_does_not_change_level(self):
        """同相与反相的**总电平必须相同**（总功率与相位无关）。"""
        self.assertAlmostEqual(self._level(self.p_anti), self._level(self.p_phase),
                               delta=0.01,
                               msg="反相 %.2f dB ≠ 同相 %.2f dB——合并域错了"
                                   % (self._level(self.p_anti), self._level(self.p_phase)))

    def test_stereo_is_exactly_plus_3db_over_mono(self):
        """两只音箱 = 2 份能量 ⇒ 双声道恰 +3.01 dB（物理定值，不是"约"）。"""
        got = self._level(self.p_anti) - self._level(self.p_mono1)
        self.assertAlmostEqual(got, 10 * np.log10(2.0), delta=0.05,
                               msg="双声道增益 %.3f dB ≠ +3.01 dB" % got)

    def test_silent_channel_adds_no_energy(self):
        """静音声道功率为 0：电平必须与真单声道文件相同（不许被算成能量）。"""
        self.assertAlmostEqual(self._level(self.p_mono2), self._level(self.p_mono1),
                               delta=0.01,
                               msg="右声道静音的 2 声道文件与真单声道电平不一致")

    def test_views_spectrogram_keeps_antiphase(self):
        """出图层（spectrogram/waterfall）同一语义：反相内容不许被抵消。"""
        from audiocheck.core import views
        t, f, S = views.spectrogram(self.p_anti)
        tone = float(S[(f >= self.TONE - 25) & (f <= self.TONE + 25), :].max())
        empty = float(S[(f >= 4980) & (f <= 5020), :].max())
        self.assertGreater(tone - empty, 40.0,
                           "频谱图把反相内容抵消了（测量版静默降级）")


class TestStereoField(unittest.TestCase):
    """B14 自研件：**立体声相位场**（ρ = L/R 时频相关系数）。

    这张图是本项目该有、常规工具没有的：频谱图丢相位、常规做法还把立体声
    压成单声道，所以"反相"这一类缺陷在别处**根本看不见**。

    判据用**物理已知**的三支信号，ρ 的期望值是定值：
      同相 L=R        → ρ = **+1**（正常单声道内容）
      反相 L=−R       → ρ = **−1**（要查的缺陷）
      正交 L⊥R        → ρ ≈ **0**（真立体声/不相关）
    三条同时过，才说明这张图画的是相位，不是能量。
    """

    FS = 44100
    TONE = 1000.0

    @classmethod
    def setUpClass(cls):
        import tempfile

        import soundfile as sf

        from audiocheck.core import views
        cls.tmp = tempfile.mkdtemp(prefix="audiocheck_phase_")
        cls.views = views
        n = int(cls.FS * 2.0)
        t = np.arange(n) / cls.FS
        a = (0.25 * np.sin(2 * np.pi * cls.TONE * t)).astype("float32")
        b = (0.25 * np.sin(2 * np.pi * 3000.0 * t)).astype("float32")

        def write(name, arr):
            p = os.path.join(cls.tmp, name)
            sf.write(p, arr, cls.FS)
            return p

        cls.p_phase = write("ph.wav", np.stack([a, a], axis=1))     # ρ=+1
        cls.p_anti = write("an.wav", np.stack([a, -a], axis=1))     # ρ=−1
        cls.p_orth = write("or.wav", np.stack([a, b], axis=1))      # ρ≈0
        cls.p_mono = write("mo.wav", a[:, None])                    # 单声道

    def _rho_at(self, path, hz, band=40.0):
        t, f, rho = self.views.stereo_field(path)
        self.assertIsNotNone(rho, "stereo_field 对立体声返回了空")
        m = (f >= hz - band) & (f <= hz + band)
        return float(np.abs(rho[m, :]).max() * np.sign(rho[m, :].mean()))

    def test_inphase_is_plus_one(self):
        self.assertGreater(self._rho_at(self.p_phase, self.TONE), 0.9,
                           "同相内容的 ρ 不是 +1——相位场画错了")

    def test_antiphase_is_minus_one(self):
        self.assertLess(self._rho_at(self.p_anti, self.TONE), -0.9,
                        "反相内容的 ρ 不是 −1——这张图的核心用途就是把它画出来")

    def test_orthogonal_is_near_zero(self):
        """L=1kHz、R=3kHz：在 1kHz 格上两声道不相关 ⇒ ρ≈0。"""
        r = self._rho_at(self.p_orth, self.TONE)
        self.assertLess(abs(r), 0.2, "正交内容的 ρ 不是 0（%.2f）" % r)

    def test_mono_has_no_phase_field(self):
        """单声道**没有**"立体声相位"这回事：必须返回空，不许拿 ρ≡+1 冒充。"""
        t, f, rho = self.views.stereo_field(self.p_mono)
        self.assertIsNone(rho, "单声道文件返回了相位场——那等于编造'完美同相立体声'")

    def test_peak_hold_is_signed_aware(self):
        """抽稀必须按 **|ρ| 最大**，不是按 max（否则整张图偏向 +1）。"""
        rho = np.array([[-0.95, 0.10], [-0.20, 0.05]], dtype="float32")
        t = np.array([0.0, 1.0])
        out, _tt = self.views._peak_hold_abs(rho, t, 1)
        self.assertLess(float(out.min()), -0.9,
                        "反相格被 max 抽稀吃掉了——图上会显示成同相")


if __name__ == "__main__":
    unittest.main(verbosity=2)
