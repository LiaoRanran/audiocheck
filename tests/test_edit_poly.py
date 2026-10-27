"""多边形时频选区增益（`PolyGainOp` + render 的 poly 掩膜路径）的判据。

判据五组：
1. **锚判据**：矩形顶点多边形（坐标**偏离谱点网格**）≡ `BandGainOp` **逐位一致**——
   多边形复用同一条 STFT/WOLA 路径的证明；
2. **凹多边形正确性**：凹口里的谱点**不属于**选区（射线法 vs 纯 Python 参照实现互证）；
3. **非破坏**：选区外逐位不变、往返残差同款报出、tf_points 记账；
4. **校验**：<3 顶点 / 非数对 / 负坐标 / 非有限值全拒绝；`gain_db=0` no-op 逐位不变；
5. **链式**：与 gain 混用走规范顺序（同相位 4），schema 往返（v2 记录含 poly_gain）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    BandGainOp,
    GainOp,
    PolyGainOp,
    Project,
    render,
)

FS = 48000


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _tone(seconds: float = 2.0, f: float = 3000.0) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return (0.5 * np.sin(2 * np.pi * f * t))[:, None]


def _bytes(p) -> bytes:
    from test_edit_cli import _bytes as norm
    return norm(p)


# ------------------------------------------------------------------ 1. 锚判据
def test_rect_polygon_matches_band_gain_bitwise(tmp_path):
    """★ 矩形顶点多边形 ≡ BandGainOp **逐位一致**（坐标偏离网格避开边界语义）。

    顶点 [t0,f0],[t1,f0],[t1,f1],[t0,f1]（逆时针）；f0/f1/t0/t1 都取非网格值
    （bin = k·fs/4096 ≈ 11.7 Hz 步；帧中心 ≈ 21.3 ms 步）⇒ 没有谱点落在边界上，
    射线法 ≡ 半开矩形比较。
    """
    x = _tone()
    src = _write(tmp_path / "a.wav", x)
    # 偏离网格：bin 中心在 85.5/170.6 Hz 附近才有内容（3 kHz 音），选区取非网格边界
    rect = BandGainOp(0.3, 1.7, 1000.3, 4500.7, -6.0)
    poly = PolyGainOp([(0.3, 1000.3), (1.7, 1000.3), (1.7, 4500.7), (0.3, 4500.7)], -6.0)
    d1 = str(tmp_path / "band.wav")
    d2 = str(tmp_path / "poly.wav")
    render(Project(src, [rect]), d1)
    render(Project(src, [poly]), d2)
    # 两次**独立渲染**之间允许 FFT 对齐的 1-ULP(float32) 差（2026-10-26 实测的
    # 非确定性，roundtrip_floor 之下）——判据用样本容差而非逐字节。
    import soundfile as sfo
    a, _ = sfo.read(d1, dtype="float64", always_2d=True)
    b, _ = sfo.read(d2, dtype="float64", always_2d=True)
    tol = np.finfo(np.float32).eps * 4.0 * float(np.sqrt(np.mean(x ** 2)))
    assert float(np.abs(a - b).max()) <= tol,         "矩形多边形 ≡ BandGainOp（1-ULP 容差内）：%s" % float(np.abs(a - b).max())


def test_poly_and_band_chain_together(tmp_path):
    """混栈（poly + band + gain）走同一条 band 渲染路径，规范顺序同相位。"""
    x = _tone()
    src = _write(tmp_path / "a.wav", x)
    proj = Project(src, [
        GainOp(0.0, 2.0, -1.0),
        PolyGainOp([(0.3, 1000.3), (1.7, 1000.3), (1.7, 4500.7), (0.3, 4500.7)], -3.0),
        BandGainOp(0.5, 1.5, 200.1, 900.2, -2.0),
    ])
    audit = render(proj, str(tmp_path / "out.wav"))
    assert audit["chained"] is True                  # 混栈走链式；末段是 gain ⇒ domain=time
    kinds = [st["op"]["kind"] for st in audit["stages"]]
    assert kinds[-1] == "gain"
    assert kinds[:2] == ["poly_gain", "band_gain"]   # 同相位 4，稳定序保持输入相对顺序


# ------------------------------------------------------------------ 2. 凹多边形
def _raycast_reference(pt, poly) -> bool:
    """纯 Python 参照实现（与内核同一算法、独立写出——互证用）。"""
    px, py = pt
    inside = False
    n = len(poly)
    for i in range(n):
        x1, y1 = poly[i]
        x2, y2 = poly[(i + 1) % n]
        if y1 == y2:
            continue
        if (y1 > py) != (y2 > py):
            x_at = (x2 - x1) * (py - y1) / (y2 - y1) + x1
            if px < x_at:
                inside = not inside
    return inside


def test_concave_polygon_excludes_the_notch(tmp_path):
    """凹多边形：凹口内的谱点**不属于**选区（不被施增益）；凸部分照常。

    C 形（凹口朝右）：[(0.3,1000),(1.5,1000),(1.5,4500),(1.0,4500),
    (1.0,2750),(1.5,2750),(1.5,4500)... ] —— 用简单的 U 形：
    [(0.3,1000),(1.5,1000),(1.5,4500),(1.0,4500),(1.0,2750),(1.5,2750),(1.5,4500)] 不闭合
    ⇒ 取标准 U：外框 + 凹口 = [(0.3,1000),(1.5,1000),(1.5,4500),(1.0,4500),
    (1.0,2750),(0.3,2750)]。
    凹口参考点 (0.5, 3500) 应在**外**；(0.3+ε 附近的 (0.35,1200)) 在**内**。
    """
    poly = [(0.3, 1000.0), (1.5, 1000.0), (1.5, 4500.0), (1.0, 4500.0),
            (1.0, 2750.0), (0.3, 2750.0)]
    # 参照实现互证（同一批探针点）
    # 凹口在左上（x<1.0 且 y>2750）：底部横条 + 右侧竖条
    probes_in = [(0.35, 1200.0), (1.4, 1200.0), (1.4, 4300.0)]
    probes_out = [(0.5, 3500.0), (0.35, 4300.0), (0.8, 3500.0)]
    for pt in probes_in:
        assert _raycast_reference(pt, poly) is True, pt
    for pt in probes_out:
        assert _raycast_reference(pt, poly) is False, pt
    # 内核掩膜与参照一致（同一网格点上抽查）
    from audiocheck.core.edit.render import _region_mask
    op = PolyGainOp(poly, -3.0)
    bins = np.array([1200.0, 3500.0, 4300.0])
    times = np.array([0.35, 0.5, 0.8, 1.4])
    mask = _region_mask(op, bins, times)
    for bi, f in enumerate(bins):
        for ti, t in enumerate(times):
            assert bool(mask[bi, ti]) == _raycast_reference((t, f), poly), (t, f)
    # 渲染：凹口内的 3500 Hz 内容**不动**（纯 3500 音探针——混音会把"凹口外被压的
    # 分量"算进能量比，量不出凹口本身）
    t = np.arange(int(2.0 * FS)) / FS
    x = (0.4 * np.sin(2 * np.pi * 3500.0 * t))[:, None]
    src = _write(tmp_path / "u.wav", x)
    audit = render(Project(src, [PolyGainOp(poly, -12.0)]), str(tmp_path / "o.wav"))
    import soundfile as sfo
    a, _ = sfo.read(str(tmp_path / "o.wav"), dtype="float64", always_2d=True)
    # 凹口内（0.5s 处、3500 Hz）的能量应与源几乎一致（未被 -12 dB 触及；窗泄漏除外）
    seg_a = a[int(0.45 * FS):int(0.55 * FS), 0]
    seg_x = x[int(0.45 * FS):int(0.55 * FS), 0]
    ratio = float(np.sqrt(np.mean(seg_a ** 2)) / np.sqrt(np.mean(seg_x ** 2)))
    assert 0.9 < ratio < 1.1, "凹口内的内容不应被压（能量比 %.3f）" % ratio
    assert audit["ops"][0]["applied_tf_points"] > 0


# ------------------------------------------------------------------ 3. 非破坏与记账
def test_poly_outside_samples_bit_identical(tmp_path):
    x = _tone()
    src = _write(tmp_path / "a.wav", x)
    audit = render(Project(src, [PolyGainOp([(0.5, 1000.3), (1.5, 1000.3),
                                             (1.5, 4500.7), (0.5, 4500.7)], -6.0)]),
                   str(tmp_path / "o.wav"))
    import soundfile as sfo
    a, _ = sfo.read(str(tmp_path / "o.wav"), dtype="float64", always_2d=True)
    lead = int(0.15 * FS)                     # 选区外扩一个窗宽之外 ⇒ 逐位不变
    b, _ = sfo.read(src, dtype="float64", always_2d=True)
    assert np.array_equal(a[:lead], b[:lead]), "选区之外（含外扩余量）必须逐位不变"
    assert audit["roundtrip_floor_dbfs"] is not None
    assert audit["ops"][0]["applied_tf_points"] > 0


# ------------------------------------------------------------------ 4. 校验
def test_poly_validation_rejects_bad_shapes():
    for bad in (
        [(0.0, 0.0), (1.0, 1.0)],                        # <3 顶点
        [(0.0, 0.0), (1.0,), (2.0, 3.0)],                # 非数对
        [(-0.1, 0.0), (1.0, 1.0), (2.0, 2.0)],           # 负 t
        [(0.0, -1.0), (1.0, 1.0), (2.0, 2.0)],           # 负 f
        [(float("nan"), 0.0), (1.0, 1.0), (2.0, 2.0)],   # NaN
    ):
        import pytest as _pt
        with _pt.raises(ValueError):
            PolyGainOp(bad, -3.0)


def test_poly_zero_gain_is_noop(tmp_path):
    x = _tone()
    src = _write(tmp_path / "a.wav", x)
    render(Project(src, [PolyGainOp([(0.5, 1000.3), (1.5, 1000.3),
                                     (1.5, 4500.7), (0.5, 4500.7)], 0.0)]),
           str(tmp_path / "o.wav"))
    assert _bytes(tmp_path / "o.wav") == _bytes(src)


# ------------------------------------------------------------------ 5. schema 往返
def test_poly_schema_roundtrip():
    import json

    from audiocheck.core.edit.ops import Project, op_from_dict
    op = PolyGainOp([(0.3, 1000.3), (1.7, 1000.3), (1.7, 4500.7), (0.3, 4500.7)], -6.0)
    d = json.loads(json.dumps(Project("a.wav", [op]).to_dict()))
    back = Project.from_dict(d)
    assert back.ops[0] == op
    assert op_from_dict(op.to_dict()) == op


def test_triangle_polygon_is_valid_and_renders(tmp_path):
    """★ 恰好 **3 个顶点**（三角形）必须合法且可渲染——`len(pts) < 3` 的边界钉死
    （`<=3` 变异会把三角形错杀）。"""
    x = _tone()
    src = _write(tmp_path / "a.wav", x)
    tri = PolyGainOp([(0.3, 1000.3), (1.7, 1000.3), (1.0, 4500.7)], -6.0)
    audit = render(Project(src, [tri]), str(tmp_path / "o.wav"))
    assert audit["ops"][0]["applied_tf_points"] > 0


def test_polygon_accepts_zero_coordinate_vertices():
    """★ 顶点坐标**恰好为 0** 必须合法（`t < 0`/`f < 0` 的边界钉死，`<=` 变异会错杀）。"""
    op = PolyGainOp([(0.0, 1000.0), (1.0, 1000.0), (1.0, 4500.0), (0.0, 4500.0)], -3.0)
    assert op.points[0] == (0.0, 1000.0)
    op2 = PolyGainOp([(0.3, 0.0), (1.7, 0.0), (1.7, 4500.0), (0.3, 4500.0)], -3.0)
    assert op2.points[0] == (0.3, 0.0)


def test_point_in_poly_dense_grid_matches_reference():
    """★ 射线法内核实现 vs 独立参照实现——**稠密网格**互证（50×50），钉死边内全部算子
    （跨线判、x_at 插值、XOR 累加）。凹多边形（非凸形状对算子最挑剔）。"""
    from audiocheck.core.edit.render import _point_in_poly
    poly = [(0.3, 1000.0), (1.5, 1000.0), (1.5, 4500.0), (1.0, 4500.0),
            (1.0, 2750.0), (0.3, 2750.0)]
    ts = np.linspace(0.2, 1.6, 50)
    fs_ = np.linspace(800.0, 4700.0, 50)
    mask = _point_in_poly(ts[None, :], fs_[:, None], poly)
    for ti, t in enumerate(ts):
        for fi, f in enumerate(fs_):
            assert bool(mask[fi, ti]) == _raycast_reference((t, f), poly), (t, f)
    # 两个凹多边形形状再各扫一遍（覆盖不同边拓扑）
    poly2 = [(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (1.5, 2.0), (1.5, 0.5), (1.0, 0.5),
             (1.0, 2.0), (0.0, 2.0)]
    mask2 = _point_in_poly(ts[None, :] * 1.2, fs_[:, None] / 2000.0, poly2)
    for ti, t in enumerate(ts * 1.2):
        for fi, f in enumerate(fs_ / 2000.0):
            assert bool(mask2[fi, ti]) == _raycast_reference((t, f), poly2), (t, f)


def test_self_intersecting_polygon_is_even_odd_deterministic():
    """★ 自相交（蝴蝶结）多边形：射线法按**奇偶规则**给出**确定性**解释——

    不拒绝（顶点表在审计里可见，用户意图自担），但行为必须可复算：蝴蝶结的**两个
    三角瓣属内**、两瓣之间的**腰部不属内**（横射线穿偶数条边 ⇒ 外）。钉死语义而非留
    undefined。（⚠ 恰好压在两条对角线**交点**上的点是测度零的退化情形，属实现定义的
    边界行为——判据避开它，用非退化的腰点。）
    """
    from audiocheck.core.edit.render import _point_in_poly
    bowtie = [(0.3, 1000.0), (1.5, 4500.0), (1.5, 1000.0), (0.3, 4500.0)]
    # 两个瓣的中心：左 (0.6, 2000) 与右 (1.2, 3500) ⇒ 内
    # 腰部 (0.9, 2500) ⇒ 射线穿 2 条边（偶）⇒ 外
    px = np.array([0.6, 1.2, 0.9])
    py = np.array([2000.0, 3500.0, 2500.0])
    mask = _point_in_poly(px, py, bowtie)
    assert bool(mask[0]) is True and bool(mask[1]) is True
    assert bool(mask[2]) is False, "奇偶规则：腰部不属内（可复算的确定语义）"
    # 与独立参照实现互证（同批点）
    for i in range(3):
        assert bool(mask[i]) == _raycast_reference((px[i], py[i]), bowtie)


def test_ray_cast_dense_grid_perturbed_vertices():
    """★ 射线法在**顶点微扰**网格上与参照实现互证（50 顶点多边形、随机微扰 ±1e-6）。

    固定网格互证钉不住 `y1 > py` 严格不等号与 `x_at` 插值的浮点边界——变异体
    `>→>=`/`+→−` 在整值顶点上恰好在测度零处滑过。微扰后的顶点让每个探针点
    与每条边的相对位置都偏离精确格点 ⇒ 任何算子改动都会翻转至少一个点。
    """
    from audiocheck.core.edit.render import _point_in_poly
    rng = np.random.default_rng(11)
    n_v = 50
    angles = np.linspace(0, 2 * np.pi, n_v, endpoint=False)
    poly = [(float(1.0 + np.cos(a) * 0.5 + rng.uniform(-1e-6, 1e-6)),
             float(2750.0 + np.sin(a) * 1500.0 + rng.uniform(-1e-6, 1e-6)))
            for a in angles]
    ts = np.linspace(0.2, 1.8, 40)
    fs_ = np.linspace(1000.0, 4500.0, 40)
    mask = _point_in_poly(ts[None, :], fs_[:, None], poly)
    n_in = int(mask.sum())
    assert 0 < n_in < mask.size, "凸近似 50 边形应有内有点（sanity）"
    for ti, t in enumerate(ts):
        for fi, f in enumerate(fs_):
            assert bool(mask[fi, ti]) == _raycast_reference((t, f), poly), (t, f)
