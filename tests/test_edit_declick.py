"""去咔哒算子的判据（`core/edit/declick.py` + `DeclickOp` + render 的 declick 路径）。

真值来源 = `corpus.click_train`（**位置已知**的咔哒）。判据分四组：

1. **只改检出样本**：选区外**逐位不变**，选区内**未检出样本逐位不变**（非破坏的硬承诺）；
2. **修得回来**：合成素材上 SDR 提升（平滑素材实测 **+40 dB**）；
3. **不许误伤合法瞬态**：带"该有的"起音的素材上，默认阈值下**检出数为 0 ⇒ 逐位不变**
   （实测 FPR = 0，`packaging/declick_probe.py`）——这条是**最重要**的一条：
   误报会把信号**修坏**（实测 FPR ~1e-3 时白噪 −13 dB）；
4. **材质相关**：平滑素材（预测性好）远好于噪声素材，这条**如实钉住**（不许只报一种素材）。
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
    DeclickOp,
    DeclipOp,
    Project,
    corpus,
    declick,
    render,
    render_declick,
)
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0


def _smooth(seconds: float = SECONDS) -> np.ndarray:
    """**平滑低波峰**素材（慢滑音）——去咔哒**设计上**该管用的那类（见探针的说明）。"""
    t = np.arange(int(seconds * FS)) / FS
    f = 300.0 + 450.0 * t / seconds
    ph = 2 * np.pi * np.cumsum(f) / FS
    return ((0.7 * np.sin(ph) + 0.25 * np.sin(ph * 1.01)) / 0.95)[:, None]


def _noisy(seconds: float = SECONDS) -> np.ndarray:
    return corpus.band_limited_noise(seconds, fs=FS, bandwidth=18000.0, seed=1)


def _transienty(seconds: float = SECONDS) -> np.ndarray:
    """带**合法瞬态**（陡起音）的素材——用来量"会不会误伤"。"""
    x = _noisy(seconds)[:, 0].copy()
    for i in np.arange(int(0.15 * FS), x.size - int(0.05 * FS), int(0.25 * FS)):
        L = int(0.005 * FS)
        env = np.exp(-np.arange(L) / (0.1 * L))
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
    return (x / np.abs(x).max() * 0.95)[:, None]


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


def _clicked(clean: np.ndarray, seconds: float = SECONDS) -> dict:
    return corpus.click_train(clean, list(np.arange(0.15, seconds - 0.15, 0.12)), fs=FS, amp=0.9)


# ------------------------------------------------------------------ 1. 只改检出样本
def test_only_detected_samples_change(tmp_path):
    """**未检出的样本逐位不变**（含立体声）——去咔哒不是"整段重放"。"""
    clean = np.column_stack([_smooth()[:, 0], _noisy()[:, 0] * 0.5])
    c = _clicked(clean)
    src = _write(tmp_path / "a.wav", c["clicked"])
    dst = str(tmp_path / "b.wav")
    audit = render_declick(src, dst, 0.0, SECONDS, overwrite=True)
    assert audit["domain"] == "declick" and audit["algorithm"] == "residual2_mad_linear"
    a, b = _read(src), _read(dst)
    det = np.zeros(a.shape[0], dtype=bool)
    for ch in range(a.shape[1]):
        m, _, _ = declick.detect(a[:, ch])
        det |= m
    assert np.array_equal(a[~det], b[~det]), "未检出的样本被动了"
    assert not np.array_equal(a[det], b[det]), "检出了却一个都没改"
    rec = audit["ops"][0]
    # 占比口径（立体声才分得出 `/(n·ch)` 与 `/(n/ch)`）与"实际改了几帧"的上界
    assert rec["fraction"] == rec["detected_samples"] / (rec["applied_frames"] * audit["channels"])
    assert 0 < rec["changed_samples"] <= rec["detected_samples"]
    # 尺子的**小数位**也钉住（与重算值同 8 位）
    _, _, s = declick.detect(a[:, 0])
    assert rec["residual_scale"][0] == round(s, 8)


def test_residual_is_zero_on_the_edges_and_needs_three_samples():
    """残差的两个**边界口径**：① 首尾样本**恒 0**（不检测）；② 3 个样本时中间那个要有残差。"""
    assert np.array_equal(declick.residual(np.array([1.0, 2.0, 4.0])), [0.0, -0.5, 0.0])
    assert np.array_equal(declick.residual(np.array([1.0, 2.0])), [0.0, 0.0])
    r = declick.residual(_smooth()[:, 0])
    assert r[0] == 0.0 and r[-1] == 0.0


def test_a_lone_click_in_digital_silence_is_not_detected_and_we_say_so():
    """**边界（如实钉住）**：数字静音里一个孤咔哒 ⇒ 残差中位数 = 0 ⇒ 尺子 = 0 ⇒ **不检测**。

    这是**保守侧的代价**：没有尺子就不敢判"这是咔哒"（判据把这条写死，不许将来偷偷放宽）。
    """
    y = np.zeros((FS, 1))
    y[FS // 2] = 0.9
    out, st = declick.declick(y)
    assert st["residual_scale"] == [0.0]
    assert st["detected_samples"] == 0 and np.array_equal(out, y)


def test_module_level_threshold_guard():
    """模块层也要守阈值（不只是 `DeclickOp`）：阈值必须 > 0（闭下界）。"""
    with pytest.raises(ValueError):
        declick.declick(_smooth(), 0.0)


def test_outside_the_selection_is_bit_exact(tmp_path):
    clean = _smooth()
    c = _clicked(clean)
    src = _write(tmp_path / "a.wav", c["clicked"])
    dst = str(tmp_path / "b.wav")
    render_declick(src, dst, 0.9, 1.1, overwrite=True)
    k0, k1 = int(math.ceil(0.9 * FS - 1e-9)), int(math.ceil(1.1 * FS - 1e-9))
    a, b = _read(src), _read(dst)
    assert np.array_equal(a[:k0], b[:k0]) and np.array_equal(a[k1:], b[k1:])


# ------------------------------------------------------------------ 2. 修得回来（且幅度不乱）
def test_smooth_material_is_repaired(tmp_path):
    """平滑素材（**预测性好**）：实测 +40 dB 量级 ⇒ 判据要求 ≥ +20 dB（留 2 倍余量）。"""
    clean = _smooth()
    c = _clicked(clean)
    src = _write(tmp_path / "a.wav", c["clicked"])
    audit = render_declick(src, str(tmp_path / "b.wav"), 0.0, SECONDS, overwrite=True)
    a, b = _read(src), _read(str(tmp_path / "b.wav"))
    assert sdr_db(b, clean) - sdr_db(a, clean) > 20.0
    e = audit["ops"][0]["energy_ratio_measured_db"]
    assert e is not None and abs(e) < 0.5, "修完电平跑偏了：%s dB" % e


def test_noise_like_material_is_barely_helped_and_we_say_so():
    """**材质相关**（如实钉住）：带限白噪上噪声本身不可预测，实测只 +1 dB 量级。

    ⚠ 这条判据**故意只要求"不更差"**——如果哪天有人在噪声素材上量出大幅改善，
    那多半是"误报把信号改坏了却碰巧抬了误差比"，得先解释清楚再改判据。
    """
    clean = _noisy()
    c = _clicked(clean)
    y = c["clicked"]
    out, st = declick.declick(y)
    assert st["detected_samples"] > 0
    assert sdr_db(out, clean) >= sdr_db(y, clean) - 0.2


# ------------------------------------------------------------------ 3. 不许误伤合法瞬态
def test_legitimate_transients_are_not_touched(tmp_path):
    """**最重要的一条**：带**陡起音**（合法内容）的素材上，默认阈值检出数必须是 **0**。

    实测（`packaging/declick_probe.py`，四种素材）：α=4 时 FPR ≤ 3.1e-4、且**合法瞬态素材上
    检出 0**。误报的代价不对称：实测 FPR 到 ~1e-3 时修复会把白噪**修坏 −13 dB**。
    """
    src = _write(tmp_path / "a.wav", _transienty())
    dst = str(tmp_path / "b.wav")
    audit = render_declick(src, dst, 0.0, SECONDS, overwrite=True)
    assert audit["ops"][0]["detected_samples"] == 0, "合法起音被当成咔哒了"
    assert np.array_equal(_read(src), _read(dst))


def test_threshold_is_a_calibrated_input_not_a_guess():
    """阈值调小 ⇒ 误报暴涨 ⇒ **会被修坏**：这条把"阈值不能凭空调小"钉成判据。

    实测：α=3 时白噪 ΔSDR **−13 dB**（同一素材 α=5 是 +2.0）；合法瞬态素材上 α=3 有
    305 个误报。判据只要求**方向正确**（调小 ⇒ 检出更多且 SDR 更差），不重复探针的具体数。
    """
    clean = _noisy()
    y = _clicked(clean)["clicked"]
    out5, st5 = declick.declick(y)
    out3, st3 = declick.declick(y, 3.0)
    assert st3["detected_samples"] > st5["detected_samples"], "阈值调小竟然没检出更多"
    assert sdr_db(out3, clean) < sdr_db(out5, clean), "阈值调小竟然没变差（那这条判据没意义）"


# ------------------------------------------------------------------ 4. 守卫与拒绝
def test_declick_op_validates_its_inputs():
    assert DeclickOp(0.0, 1.0).threshold == declick.DEFAULT_THRESHOLD == 5.0
    with pytest.raises(ValueError):
        DeclickOp(0.0, 1.0, 0.0)                     # 阈值必须 > 0（闭下界）
    with pytest.raises(ValueError):
        DeclickOp(1.0, 0.5)                          # t1 <= t0
    # 宽度守卫 `>= 1`：**恰好 1 合法**（`<` 改 `<=` 会把它拒掉）
    assert DeclickOp(0.0, 1.0, max_run=1).max_run == 1


def test_overlapping_selections_are_refused_but_mixing_is_chained(tmp_path):
    """同域**重叠**仍拒绝（同一段处理两遍的语义未立判据）；
    混用不同域的算子**不再拒绝** —— 2026-10-24 起按规范顺序链式渲染（见 `test_edit_chain.py`）。"""
    clean = _smooth()
    src = _write(tmp_path / "a.wav", _clicked(clean)["clicked"])
    with pytest.raises(ValueError, match="重叠"):
        render(Project(src, [DeclickOp(0.0, 1.0), DeclickOp(0.5, 1.5)]), str(tmp_path / "b.wav"))
    audit = render(Project(src, [DeclickOp(0.0, 1.0), DeclipOp(0.0, 1.0, 21600.0)]),
                   str(tmp_path / "c.wav"))
    assert audit["chained"] is True and len(audit["stages"]) == 2


def test_silence_is_a_noop(tmp_path):
    """数字静音：尺子为 0 ⇒ 不许除零、不许乱检，逐位不变。"""
    src = _write(tmp_path / "a.wav", np.zeros((FS, 1)))
    dst = str(tmp_path / "b.wav")
    audit = render_declick(src, dst, 0.0, 1.0, overwrite=True)
    assert audit["ops"][0]["detected_samples"] == 0
    assert np.array_equal(_read(src), _read(dst))


def test_selection_past_the_end_is_a_noop(tmp_path):
    src = _write(tmp_path / "a.wav", _clicked(_smooth())["clicked"])
    dst = str(tmp_path / "b.wav")
    audit = render_declick(src, dst, 5.0, 6.0, overwrite=True)
    assert audit["ops"][0]["applied_frames"] == 0
    assert np.array_equal(_read(src), _read(dst))


def test_default_threshold_is_pinned_to_the_calibrated_value():
    """默认阈值是**实测标定**出来的（α=5：三种素材误报 0、四种素材 ΔSDR 全为正）⇒ 钉住。"""
    assert declick.DEFAULT_THRESHOLD == 5.0
    assert declick.DEFAULT_MAX_RUN == 4


# ------------------------------------------------------------------ 5. 宽度守卫（第二级复核）
def _onset_material(ms: float = 5.0, seconds: float = SECONDS):
    """**平滑音 + 陡起音**（合法内容）。这组素材才是"误伤"的真实场景：
    残差尺度很小 ⇒ 起音在残差域里**很显眼**（噪声素材上反而不显眼）。"""
    base = _smooth()
    x = base[:, 0].copy()
    L = max(1, int(ms * FS / 1000.0))
    for i in np.arange(int(0.15 * FS), x.size - L - 10, int(0.25 * FS)):
        env = np.exp(-np.arange(L) / (0.1 * L))
        x[i:i + L] += 0.8 * env * np.sin(2 * np.pi * 3000.0 * np.arange(L) / FS)
    return (x / np.abs(x).max() * 0.95)[:, None], base


def test_width_guard_protects_legitimate_onsets():
    """**第二级复核的效力**（实测）：平滑音 + 5 ms 起音，α=5。

    * 无守卫：把起音也修了（离原素材 **30.55 dB**，动 300 采样）；
    * `max_run=4`：**56.51 dB**，只动 40 采样（降了 26 dB 的破坏）。
    ⇒ 判据要求"有守卫"至少比"无守卫"好 **20 dB**，且改动面 ≤ 1/5。
    """
    y, _ = _onset_material()
    no_guard, st0 = declick.declick(y, 5.0, None)
    guarded, st1 = declick.declick(y, 5.0, 4)
    assert st0["rejected_runs"] == 0 and st1["rejected_runs"] > 0
    d0 = sdr_db(no_guard, y)
    d1 = sdr_db(guarded, y)
    assert d1 - d0 > 20.0, "宽度守卫没起作用：%.2f vs %.2f" % (d0, d1)
    assert st1["changed_samples"] * 5 <= st0["changed_samples"]


def test_width_guard_keeps_one_and_two_sample_clicks():
    """**守卫不能把该修的咔哒挡掉**：1 采样与 2 采样咔哒的检出段宽度是 3 / 4，
    `max_run=4` 必须一个不丢（`max_run=3` 会把 2 采样咔哒整段丢掉——这就是取 4 的理由）。"""
    for w in (1, 2):
        clean = _smooth()
        pos = list(np.arange(0.15, SECONDS - 0.05, 0.12))
        y = clean.copy()
        for t in pos:
            i = int(round(t * FS))
            y[i:i + w, 0] += 0.9
        out, st = declick.declick(y, 5.0, 4)
        assert st["rejected_runs"] == 0
        assert sdr_db(out, clean) - sdr_db(y, clean) > 20.0, (w, st)
    # 3 会把 2 采样咔哒挡掉——判据钉住"为什么是 4 而不是 3"
    clean = _smooth()
    y = clean.copy()
    for t in np.arange(0.15, SECONDS - 0.05, 0.12):
        i = int(round(float(t) * FS))
        y[i:i + 2, 0] += 0.9
    _, st3 = declick.declick(y, 5.0, 3)
    assert st3["rejected_runs"] > 0, "max_run=3 应当挡掉 2 采样咔哒（这正是不取 3 的原因）"


def test_three_sample_plateau_is_only_edge_repaired_and_we_say_so():
    """**如实记账的缺口**：宽度 ≥3 的矩形咔哒**只被修两个边**（平台残差 ≈ 0 ⇒ 看不见）。

    实测：宽度 1 → +39 dB、2 → +37 dB，而 3 → **+1.5 dB**、8 → **+0.5 dB**。
    这条判据把"本算子修脉冲、不修台阶"钉死——将来谁想改，先看这条。
    """
    pos = list(np.arange(0.15, SECONDS - 0.05, 0.12))
    gains = {}
    for w in (1, 2, 3, 8):
        clean = _smooth()
        y = clean.copy()
        for t in pos:
            i = int(round(t * FS))
            y[i:i + w, 0] += 0.9
        out, _ = declick.declick(y, 5.0, 4)
        gains[w] = sdr_db(out, clean) - sdr_db(y, clean)
    assert gains[1] > 20.0 and gains[2] > 20.0
    assert gains[3] < 5.0 and gains[8] < 5.0, gains


def test_max_run_validation_and_reporting(tmp_path):
    """`max_run` 的取值校验（>=1 或 None）与审计字段（含被挡段数）。"""
    with pytest.raises(ValueError):
        DeclickOp(0.0, 1.0, 5.0, 0)
    assert DeclickOp(0.0, 1.0).max_run == 4
    assert "宽度守卫 ≤4 采样" in DeclickOp(0.0, 1.0).describe()
    assert "宽度守卫 关" in DeclickOp(0.0, 1.0, 5.0, None).describe()

    y, _ = _onset_material()
    src = _write(tmp_path / "a.wav", y)
    audit = render_declick(src, str(tmp_path / "b.wav"), 0.0, SECONDS, overwrite=True)
    rec = audit["ops"][0]
    assert rec["max_run"] == 4 and rec["rejected_runs"] > 0


def test_energy_ratio_guard_is_reported(tmp_path):
    """审计里的能量比必须与重算值同 3 位（与去削波同款口径）。"""
    src = _write(tmp_path / "a.wav", _clicked(_smooth())["clicked"])
    dst = str(tmp_path / "b.wav")
    audit = render_declick(src, dst, 0.0, SECONDS, overwrite=True)
    rec = audit["ops"][0]
    a, b = _read(src), _read(dst)
    assert rec["energy_ratio_measured_db"] == round(float(energy_ratio_db(b, a)), 3)
    assert rec["detected_samples"] == int(round(rec["fraction"] * rec["applied_frames"]))
