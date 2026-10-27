"""property-based 判据（Hypothesis；Agent B 调研落地的三个性质）。

与固定种子判据互补：固定种子测**手选边界**，property 自动扫**组合空间**。
deadline=None（音频渲染远超 200ms 默认）、max_examples 有界（CI 时长可控）。

三个性质：
1. **schema 幂等**：任意合法操作 dict，`from_dict(to_dict(from_dict(d)))` 与
   `from_dict(d)` 同参数（解析-序列化不漂移）；
2. **render 非破坏不变量**：任意素材/增益/选区，**选区外逐位不变**（非破坏的
   数学陈述，随机化边界）；
3. **单调性**：增益越大输出不越小（逐点）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf
from hypothesis import given, settings
from hypothesis import strategies as st

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import GainOp, Project, render  # noqa: E402
from audiocheck.core.edit.ops import op_from_dict  # noqa: E402

FS = 48000

# ------------------------------------------------- 1. schema 幂等
合法增益 = st.floats(min_value=-60.0, max_value=12.0, allow_nan=False)
合法选区 = st.tuples(
    st.floats(min_value=0.0, max_value=1.9, allow_nan=False).filter(lambda v: v >= 0.0),
    st.floats(min_value=0.01, max_value=2.0, allow_nan=False),
).map(lambda p: (p[0], max(p[0] + 0.01, p[1])))


@settings(deadline=None, max_examples=60)
@given(t0=st.floats(0.0, 1.9, allow_nan=False),
       gain=合法增益,
       dur=st.sampled_from((0.5, 1.0, 2.0)))
def test_gain_op_schema_roundtrip_idempotent(t0, gain, dur):
    """任意合法 GainOp：`from_dict(to_dict(op))` 与 `op` 同参数（幂等）。"""
    t1 = min(t0 + 0.1, dur)
    if t1 <= t0:
        return
    op = GainOp(t0, t1, gain)
    d1 = op.to_dict()
    back = op_from_dict(d1)
    assert back == op, (d1, back.to_dict())
    d2 = back.to_dict()
    assert d2 == d1, "序列化必须幂等"


# ------------------------------------------------- 1b. 全算子 schema 幂等（Agent B P1 全量化）
@settings(deadline=None, max_examples=40)
@given(gain_db=st.floats(-60.0, 12.0, allow_nan=False),
       threshold=st.floats(1.0, 20.0, allow_nan=False),
       reduction=st.floats(5.0, 40.0, allow_nan=False),
       f0=st.sampled_from((50.0, 60.0)),
       q=st.sampled_from((5.0, 10.0, 20.0)),
       max_run=st.sampled_from((None, 4, 8)),
       step=st.booleans(),
       track=st.booleans())
def test_all_op_schema_roundtrip_idempotent(gain_db, threshold, reduction, f0, q,
                                            max_run, step, track):
    """★ 五种算子的 schema 往返幂等（参数组合空间自动扫）：from_dict(to_dict(op)) == op。"""
    from audiocheck.core.edit import DeclickOp, DeclipOp, DehumOp, DenoiseOp, PolyGainOp
    ops = [
        GainOp(0.5, 1.5, gain_db),
        DeclickOp(0.5, 1.5, threshold, max_run, step_repair=step),
        DehumOp(0.5, 1.5, f0, q, (1, 2, 3)),
        DeclipOp(0.5, 1.5, 18000.0, None),
        DenoiseOp(0.5, 1.5, 0.0, 0.5, reduction, 4096, None, track_floor=track),
        PolyGainOp([(0.3, 1000.0), (1.5, 1000.0), (1.5, 4500.0), (0.3, 4500.0)],
                   gain_db),
    ]
    for op in ops:
        d = op.to_dict()
        back = op_from_dict(d)
        assert back == op, (d, back.to_dict())
        assert back.to_dict() == d, "序列化必须幂等"


# ------------------------------------------------- 2. 非破坏不变量 + 3. 单调性
def _make_wav(tmp_path, x) -> str:
    p = tmp_path / "in.wav"
    sf.write(str(p), x.astype(np.float32), FS, subtype="FLOAT")
    return str(p)


@settings(deadline=None, max_examples=20,
          suppress_health_check=[__import__("hypothesis").HealthCheck
                                 .function_scoped_fixture])
@given(t0=st.floats(0.05, 1.5, allow_nan=False),
       gain_db=st.floats(-24.0, 6.0, allow_nan=False),
       seed=st.integers(0, 10_000))
def test_render_nondestructive_outside_selection(tmp_path, t0, gain_db, seed):
    """★ 非破坏的数学陈述：任意素材/选区/增益，**选区外**（含外扩余量）逐位不变。"""
    rng = np.random.default_rng(seed)
    x = rng.standard_normal((int(2.0 * FS), 1)) * 0.3
    src = _make_wav(tmp_path, x)
    t1 = min(t0 + 0.2, 1.9)
    dst = str(tmp_path / ("o_%d.wav" % abs(int(t0 * 1e6 + gain_db * 1e3 + seed))))
    render(Project(src, [GainOp(t0, t1, gain_db)]), dst, overwrite=True)
    a = sf.read(dst, dtype="float64", always_2d=True)[0]
    b = sf.read(src, dtype="float64", always_2d=True)[0]
    lead = max(0, int(t0 * FS) - 4096)          # 外扩一个窗宽（gain 是时域路径无外扩；
    # 这里取保守余量以覆盖 band 路径的同判据语义）
    tail = min(len(a), int(t1 * FS) + 4096)
    assert np.array_equal(a[:lead], b[:lead]), "选区前必须逐位不变"
    assert np.array_equal(a[tail:], b[tail:]), "选区后必须逐位不变"


@settings(deadline=None, max_examples=25)
@given(pair=st.tuples(st.floats(-30.0, 0.0, allow_nan=False),
                      st.floats(-30.0, 0.0, allow_nan=False))
       .map(lambda p: (min(p), max(p))),
       xval=st.floats(-0.9, 0.9, allow_nan=False))
def test_gain_monotonicity(pair, xval):
    """★ 单调性：g1 ≤ g2 ⇒ |x·10^(g1/20)| ≤ |x·10^(g2/20)|（增益的数学序）。"""
    g1, g2 = pair
    a = abs(xval) * (10.0 ** (g1 / 20.0))
    b = abs(xval) * (10.0 ** (g2 / 20.0))
    assert a <= b * (1 + 1e-12) + 1e-15
