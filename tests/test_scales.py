"""频率刻度变换的回归：单调、互逆、锚点、网格。

这三条不变量如果破了，画布上的**轴会撒谎**（位置与标签对不上），
而那比没有刻度更糟。所以这里量的是数学本身，不是"画出来看着对"。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core import scales  # noqa: E402

_NAMES = [n for n in scales.SCALES]
_F = np.geomspace(20.0, 20000.0, 400)


@pytest.mark.parametrize("name", _NAMES)
def test_forward_is_strictly_increasing(name):
    fwd = scales.get(name)[0]
    y = fwd(_F)
    d = np.diff(y)
    assert np.all(d > 0), "%s 刻度在 [20,20000] 上非严格递增（最小步长 %.3g）" % (name, d.min())


@pytest.mark.parametrize("name", _NAMES)
def test_forward_inverse_roundtrip(name):
    fwd, inv = scales.get(name)[0], scales.get(name)[1]
    back = inv(fwd(_F))
    rel = np.abs(back - _F) / _F
    assert rel.max() < 1e-9, "%s 互逆失败：最大相对误差 %.3g" % (name, rel.max())


@pytest.mark.parametrize("name,at,expect,tol", [
    ("bark", 1000.0, 8.527, 0.01),      # Traunmüller 1990：1 kHz ≈ 8.5 Bark
    ("mel", 1000.0, 1000.0, 0.5),       # HTK 定义：1 kHz = 1000 Mel
    ("erb", 1000.0, 15.62, 0.05),       # Glasberg-Moore 1990：1 kHz ≈ 15.6 ERB
    ("log", 1000.0, 3.0, 1e-12),        # log10(1000) = 3
    ("linear", 1000.0, 1000.0, 1e-12),
])
def test_anchor_values(name, at, expect, tol):
    """锚点是**出处里的数字**，不是拍脑袋——破了说明公式抄错了。"""
    got = float(scales.get(name)[0](at))
    assert abs(got - expect) <= tol, "%s(%.0fHz) = %.4f，应为 %.4f±%.3f" % (
        name, at, got, expect, tol)


@pytest.mark.parametrize("name", _NAMES)
def test_display_grid_is_uniform_in_that_scale(name):
    """显示网格必须**在该刻度上等距**——否则"换刻度"只是换标签。"""
    rows = 360
    fl, lo, hi = scales.display_grid(20000.0, rows, name)
    assert len(fl) == rows
    assert np.all(np.diff(fl) > 0), "%s 网格非递增" % name
    fwd = scales.get(name)[0]
    x = fwd(fl)
    steps = np.diff(x)
    assert np.allclose(steps, steps.mean(), rtol=1e-9), \
        "%s 网格在自身刻度上不等距（步长 %.3g…%.3g）" % (name, steps.min(), steps.max())
    # 端点必须正好是 20 Hz 与 f_max（画布 extent 直接用这两个值）
    assert abs(float(fl[0]) - scales.F_MIN) < 1e-6
    assert abs(float(fl[-1]) - 20000.0) < 1e-6
    assert abs(float(fwd(scales.F_MIN)) - lo) < 1e-9
    assert abs(float(fwd(20000.0)) - hi) < 1e-9


def test_unknown_scale_falls_back_instead_of_raising():
    """拼错名字不许让整块画布白掉：回落默认 + 可判定（is_known 说 false）。"""
    assert not scales.is_known("bogus")
    fwd, _inv, label, _note = scales.get("bogus")
    assert fwd is scales.get(scales.DEFAULT)[0]
    assert label == scales.get(scales.DEFAULT)[2]
    assert scales.is_known(None)          # None 走默认


def test_bark_has_one_implementation():
    """**一处数学**：`charts.bark`（画临界带线用）必须就是 `scales.bark`。

    两份实现迟早会分叉，而"线"与"刻度"用的是同一个 Bark 定义——
    分叉后线与刻度就会对不上，且没人看得出来。
    """
    from audiocheck.ui import charts
    assert charts.bark is scales.bark, "charts.bark 与 scales.bark 不是同一个函数"
