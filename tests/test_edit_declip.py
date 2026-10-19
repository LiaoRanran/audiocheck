"""去削波算子的判据（`core/edit/declip.py` + `DeclipOp` + `render` 的 declip 路径）。

**判据先行**（`docs/editor_roadmap.md §4`）：算法落地前，先在**有真值的合成语料**
（`corpus.py`）上把"提升多少"量出来——`packaging/declip_probe.py` 那张表就是选型依据：
局部插值会把 SDR **修坏**（43.5→21.3 等），只有"带限 + 削波不等式"的 POCS 才真提升。

本文件钉住五件事：
1. **只改饱和样本**：选区内的非饱和样本与选区外**逐位不变**（非破坏的硬承诺）；
2. **真值改进**：SDR 提升（实测 +16.3 dB）且**能量不缩水**（PHAIN 的警告）；
3. **两个可测输入**的语义：`ceiling`（含自动取观测峰值）与 `bandwidth`；
4. **两条拒绝**：选区重叠、与增益混用；
5. **效力边界**（如实记录，不许粉饰）：float32 存回后的量化边界、`B = 奈奎斯特` 时无事可做、
   饱和越密提升越小。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    DeclipOp,
    GainOp,
    Project,
    corpus,
    declip,
    render,
    render_declip,
)
from audiocheck.core.edit.metrics import band_gain_db, energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
B = 0.9 * FS / 2.0            # 干净素材的带宽真值（`corpus.default_clean`）
NYQ = FS / 2.0


def _sample_at(t: float, fs: int = FS) -> int:
    return int(math.ceil(t * fs - 1e-9))


def _pack(tier: str = "moderate", seconds: float = 1.0, stereo: bool = False,
          seed: int = 1) -> dict:
    if stereo:
        clean = np.column_stack([corpus.default_clean(seconds, fs=FS, seed=seed)[:, 0],
                                 corpus.default_clean(seconds, fs=FS, seed=seed + 7)[:, 0]])
    else:
        clean = corpus.default_clean(seconds, fs=FS, seed=seed)
    return corpus.clipped(clean, tier)


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


# ------------------------------------------------------------------ 1. 只改饱和样本
@pytest.mark.parametrize("stereo", [False, True])
def test_only_saturated_samples_change(tmp_path, stereo):
    """**非饱和样本逐位不变**（含立体声两声道）——去削波不是"整段重放"。"""
    p = _pack(stereo=stereo)
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, overwrite=True)
    a, b = _read(src), _read(dst)
    mask = corpus.saturation_mask(a, audit["ops"][0]["ceiling"])
    assert np.array_equal(a[~mask], b[~mask]), "非饱和样本被动了"
    assert not np.array_equal(a[mask], b[mask]), "饱和样本一个都没动（那没在修）"


def test_outside_the_selection_is_bit_exact(tmp_path):
    """选区 `[0.4, 0.6)` 之外**逐位不变**（原样拷贝，不经过任何处理）。"""
    p = _pack("severe")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    render_declip(src, dst, 0.4, 0.6, B, overwrite=True)
    k0, k1 = _sample_at(0.4), _sample_at(0.6)
    a, b = _read(src), _read(dst)
    assert np.array_equal(a[:k0], b[:k0]) and np.array_equal(a[k1:], b[k1:])


def test_selection_edge_convention_matches_gain_op(tmp_path):
    """半开区间口径与 `GainOp` **逐位一致**：`k` 受影响 ⟺ `t0 ≤ k/fs < t1`。"""
    p = _pack("severe")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.4, 0.6, B, overwrite=True)
    assert audit["ops"][0]["applied_frames"] == _sample_at(0.6) - _sample_at(0.4)


# ------------------------------------------------------------------ 2. 真值改进
def test_sdr_improves_and_the_energy_does_not_shrink(tmp_path):
    """合成语料上：SDR 提升（实测 23.25 → 39.51 dB，**+16.26**）且能量**没缩水**。

    两条**一起**报是 `docs/theory.md §12.2` 的硬要求：只报 SDR 会把"变安静"算成改进
    （PHAIN 的警告）。实测能量比 +0.158 dB（略升，因为重建出来的峰比削平的值高一点）。
    """
    p = _pack("moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, overwrite=True)
    a, b = _read(src), _read(dst)
    s0, s1 = sdr_db(a, p["clean"]), sdr_db(b, p["clean"])
    assert s0 is not None and s1 is not None
    assert s1 - s0 > 10.0, "SDR 提升不足（实测 +16.3 dB）：%.2f → %.2f" % (s0, s1)
    e = audit["ops"][0]["energy_ratio_measured_db"]
    assert e is not None and e > -0.5, "修完变安静了（能量比 %s dB）" % e


def test_tone_material_also_improves(tmp_path):
    """换一种素材（谐波音，带宽真值 17.6 kHz）：同样提升，不是只对白噪有效。"""
    clean = corpus.harmonic_tone(1.0, fs=FS, f0=440.0, n_partials=40, seed=2)
    p = corpus.clipped(clean, "moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    render_declip(src, dst, 0.0, 1.0, 17600.0, overwrite=True)
    a, b = _read(src), _read(dst)
    assert sdr_db(b, p["clean"]) - sdr_db(a, p["clean"]) > 3.0


def test_more_iterations_do_not_hurt(tmp_path):
    """POCS 收敛是**慢**的（`declip.py` 的收敛表）：迭代多必须不更差（可复算的单调性）。"""
    p = _pack("moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    outs = {}
    for it in (16, 400):
        dst = str(tmp_path / ("b%d.wav" % it))
        render(Project(src, [DeclipOp(0.0, 1.0, B, iterations=it)]), dst, overwrite=True)
        outs[it] = sdr_db(_read(dst), p["clean"])
    assert outs[400] >= outs[16], outs


def test_out_of_band_energy_is_removed_so_the_band_limit_really_bites(tmp_path):
    """削波会在带外造出能量；带限投影必须把它收回去（实测 −15.5 dB）。"""
    p = _pack("moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    audit = render_declip(src, str(tmp_path / "b.wav"), 0.0, 1.0, B, overwrite=True)
    oob = audit["ops"][0]["out_of_band_measured_db"]
    assert oob is not None and oob < -5.0, oob


# ------------------------------------------------------------------ 3. 两个可测输入
def test_auto_ceiling_is_the_observed_peak_of_the_source(tmp_path):
    """`ceiling=None` ⇒ 取**本段观测峰值**（审计记 `auto_max` 与 `peak_in`）。"""
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    audit = render_declip(src, str(tmp_path / "b.wav"), 0.0, 1.0, B, overwrite=True)
    rec = audit["ops"][0]
    assert rec["ceiling_source"] == "auto_max"
    assert rec["ceiling"] == rec["peak_in"] == pytest.approx(float(np.abs(_read(src)).max()))
    assert rec["saturated_samples"] > 0


def test_given_ceiling_comes_from_the_source_not_from_the_pre_write_value(tmp_path):
    """**效力边界**：`c` 必须取自**被编辑文件自己**的样本值。

    素材存成 float32 后，样本是**量化过**的：写入前的 float64 峰值 0.5225 存回后成了
    0.52249997 ⇒ `|y| >= 0.5225` **一个都匹配不到**（饱和集合判空 ⇒ 逐位不变）。
    这不是 bug 而是必须让用户看见的事实——它正是"自动取观测峰值"这条默认值的理由。
    """
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, ceiling=p["ceiling"], overwrite=True)
    rec = audit["ops"][0]
    assert rec["saturated_samples"] == 0, "float64 的理论 c 竟然匹配上了 float32 的样本？"
    assert np.array_equal(_read(src), _read(dst)), "没有饱和样本时应当逐位不变"
    assert rec["peak_in"] < p["ceiling"], "观测峰值应当**低于**写入前的理论值（量化）"


def test_given_ceiling_above_the_peak_makes_it_a_noop(tmp_path):
    """`c` 高于观测峰值 ⇒ 集合为空 ⇒ 逐位不变（不假装修过）。"""
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, ceiling=2.0, overwrite=True)
    assert audit["ops"][0]["saturated_samples"] == 0
    assert np.array_equal(_read(src), _read(dst))


def test_bandwidth_at_nyquist_is_a_noop(tmp_path):
    """`B ≥ 奈奎斯特` ⇒ 没有约束可利用 ⇒ **逐位不变**，审计如实记 `noop`。"""
    p = _pack("severe")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, NYQ, overwrite=True)
    rec = audit["ops"][0]
    assert rec["noop"] is True and rec["band_limited"] is False
    assert rec["replaced_samples"] == 0
    assert rec["out_of_band_measured_db"] is None, "B = 奈奎斯特时没有'带外'可量 ⇒ 必须是 None"
    assert np.array_equal(_read(src), _read(dst))


def test_silence_is_a_noop(tmp_path):
    """全零素材：没有削波可言（`peak_in == 0`）⇒ 逐位不变，不许除零/报错。"""
    src = _write(tmp_path / "a.wav", np.zeros((FS, 1)))
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, overwrite=True)
    assert audit["ops"][0]["noop"] is True
    assert np.array_equal(_read(src), _read(dst))


# ------------------------------------------------------------------ 4. 两条拒绝
def test_overlapping_declip_selections_are_refused(tmp_path):
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    proj = Project(src, [DeclipOp(0.0, 0.5, B), DeclipOp(0.4, 0.9, B)])
    with pytest.raises(ValueError, match="重叠"):
        render(proj, str(tmp_path / "b.wav"))


def test_disjoint_declip_selections_are_applied_in_order(tmp_path):
    """不重叠的两段：两段都生效，且**两段之外逐位不变**（顺序可复算）。"""
    p = _pack("severe")
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render(Project(src, [DeclipOp(0.0, 0.4, B), DeclipOp(0.5, 1.0, B)]),
                   dst, overwrite=True)
    assert audit["ops"][0]["replaced_samples"] > 0
    assert audit["ops"][1]["replaced_samples"] > 0
    k0, k1 = _sample_at(0.4), _sample_at(0.5)
    a, b = _read(src), _read(dst)
    assert np.array_equal(a[k0:k1], b[k0:k1]), "两段之间的空隙被动了"


def test_declip_mixed_with_gain_is_refused(tmp_path):
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    proj = Project(src, [GainOp(0.0, 1.0, -6.0), DeclipOp(0.0, 1.0, B)])
    with pytest.raises(ValueError, match="混用"):
        render(proj, str(tmp_path / "b.wav"))


# ------------------------------------------------------------------ 5. 内核守卫
def test_default_iterations_is_pinned_to_the_measured_choice():
    """迭代次数的默认值是**实测**定的（128 ≈ 400 次的 93%）⇒ 钉住，别让它悄悄漂。"""
    assert declip.DEFAULT_ITERATIONS == 128
    assert DeclipOp(0.0, 1.0, B).iterations == 128


def test_internal_run_finder_agrees_with_the_public_one():
    """`declip._runs` 与 `corpus.saturation_runs` **同一口径**（两处会分叉就等于两把尺子）。"""
    m = np.array([0, 1, 1, 0, 0, 1, 0], dtype=bool)
    assert declip._runs(m) == corpus.saturation_runs(m) == [(1, 3), (5, 6)]


def test_audit_names_the_algorithm_and_the_domain(tmp_path):
    """审计必须报**用的是哪套重建**（可复算的口径名）——"可审计的修复"的一部分。"""
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    audit = render_declip(src, str(tmp_path / "b.wav"), 0.0, 1.0, B, overwrite=True)
    assert audit["domain"] == "declip"
    assert audit["algorithm"] == "pocs_declip"


def test_stats_report_the_saturated_geometry(tmp_path):
    """审计里的几何量必须**对得上**：占比、段数、最长段、"真改了几帧"。"""
    p = _pack("moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    audit = render_declip(src, str(tmp_path / "b.wav"), 0.0, 1.0, B, overwrite=True)
    rec = audit["ops"][0]
    assert rec["fraction"] == pytest.approx(rec["saturated_samples"] / rec["total_samples"])
    runs = corpus.saturation_runs(corpus.saturation_mask(_read(src), rec["ceiling"])[:, 0])
    assert rec["runs"] == len(runs)
    assert rec["longest_run"] == max(e - s for s, e in runs)
    # 真正被改的帧数**不超过**饱和帧数（削波不等式可能把某些饱和样本钳回原值）
    assert 0 < rec["replaced_samples"] <= rec["saturated_samples"]
    # 两条**实测**必须等于"同一把尺子重算、保留 3 位"的值（口径可复算，小数位也钉住）
    a, b = _read(src), _read(str(tmp_path / "b.wav"))
    assert rec["energy_ratio_measured_db"] == round(float(energy_ratio_db(b, a)), 3)
    assert rec["out_of_band_measured_db"] == round(float(band_gain_db(a, b, FS, B, float(FS))), 3)


def test_out_of_band_measurement_covers_the_nyquist_bin(tmp_path):
    """带外能量的上界取 `fs`（半开 ⇒ **含奈奎斯特那条 bin**）——这条口径只有"奈奎斯特上
    有内容"的素材才分得出来（普通带限素材上两种写法一模一样）。

    构造：带限白噪 + 一条**恰好 ±奈奎斯特**的谱线（`(-1)**n`），削一刀后去削波；
    审计里的 `out_of_band_measured_db` 必须等于"按 `[B, fs)` 重算"的值。
    """
    x = corpus.default_clean(1.0, fs=FS, seed=1)[:, 0] + 0.3 * ((-1.0) ** np.arange(FS))
    src = _write(tmp_path / "a.wav", corpus.clipped(x[:, None], ceiling=0.5)["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 0.0, 1.0, B, overwrite=True)
    a, b = _read(src), _read(dst)
    got = audit["ops"][0]["out_of_band_measured_db"]
    assert got is not None and got == round(float(band_gain_db(a, b, FS, B, float(FS))), 3)


def test_a_selection_past_the_end_is_a_noop(tmp_path):
    """选区完全落在文件之外（`k0 == k1 == 帧数`）⇒ **什么都不做**、逐位不变（不许崩）。"""
    p = _pack()
    src = _write(tmp_path / "a.wav", p["clipped"])
    dst = str(tmp_path / "b.wav")
    audit = render_declip(src, dst, 5.0, 6.0, B, overwrite=True)      # 文件只有 1 s
    assert audit["ops"][0]["applied_frames"] == 0
    assert np.array_equal(_read(src), _read(dst))


def test_declip_op_validates_its_inputs():
    """三个边界都是**闭**的：`ceiling=0` / `bandwidth=0` / `t1<=t0` 拒绝，`iterations=1` 允许。"""
    with pytest.raises(ValueError):
        DeclipOp(0.0, 1.0, B, 0.0)                     # ceiling = 0（闭区间下界）
    with pytest.raises(ValueError):
        DeclipOp(0.0, 1.0, 0.0)                        # bandwidth = 0
    with pytest.raises(ValueError):
        DeclipOp(1.0, 0.5, B)                          # t1 <= t0
    assert DeclipOp(0.0, 1.0, B, None, 1).iterations == 1


def test_one_iteration_is_allowed():
    """迭代 1 次是合法输入（边界闭）；只有 0 才拒绝。已知样本照样逐位不变。"""
    y = np.zeros((512, 1))
    y[100:104] = 0.9                       # 一段饱和（|y| ≥ c）
    c = 0.5
    out, st = declip.reconstruct(y, c, FS, B, iterations=1)
    assert st["iterations"] == 1 and st["band_limited"] is True
    known = np.abs(y[:, 0]) < c
    assert np.array_equal(out[known], y[known])


def test_reconstruct_rejects_bad_params():
    y = np.zeros((256, 1))
    with pytest.raises(ValueError):
        declip.reconstruct(y, 0.5, FS, FS)             # 带宽 > fs/2
    with pytest.raises(ValueError, match="削波电平"):
        declip.reconstruct(y, 0.0, FS, B)              # 削波电平 = 0（边界闭）
    with pytest.raises(ValueError, match="削波电平"):
        declip.reconstruct(y, -0.1, FS, B)
    with pytest.raises(ValueError, match="迭代次数"):
        declip.reconstruct(y, 0.5, FS, B, iterations=0)


def test_reconstruct_refuses_a_fully_saturated_segment():
    """整段都在饱和 ⇒ 一个已知样本都没有 ⇒ 重建纯属编造，**抛错**（宁可不做）。"""
    with pytest.raises(ValueError, match="整段都在饱和"):
        declip.reconstruct(np.full((256, 1), 0.5), 0.5, FS, B)
