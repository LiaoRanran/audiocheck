"""去噪算子的判据（`core/edit/denoise.py` + `DenoiseOp` + `render` 的 denoise 路径）。

本文件钉住五件事（都有实测依据，见 `packaging/denoise_probe.py`）：
1. **必须给噪声段**：盲估计被实测否决（+10 dB 持续音上 ΔSDR **−8.7 dB**）⇒ 缺噪声段就拒绝；
2. **真值改进**：0 dB SNR 的谐波音上 ΔSDR **+11.8 dB**、相对干净真值的能量比 **−0.46 dB**；
3. **实测压制**：噪声段被同一套增益压 **−19.9 dB**；`reduction_db=3` 时压 **−2.99 dB**（增益下限生效）；
4. **非破坏边界**：选区之外（含噪声段本身）**逐位不变**；空选区输出逐位为 0；
5. **如实代价**：瞬态会被削软（`docs/editor_research.md §16` 实测 −13.6 dB，这里只钉"不做假承诺"）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    DenoiseOp,
    GainOp,
    Project,
    corpus,
    denoise,
    render,
)
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
LEAD = 0.5
N_FFT = denoise.DEFAULT_N_FFT


def _pack(seed: int = 7):
    """1.5 s 内容（0.5 s 起）+ 前 0.5 s **纯噪声**（噪声谱真值一并返回）。"""
    tone = corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3)
    r = corpus.add_noise(tone, 0.0, "white", seed=seed)
    lead = int(LEAD * FS)
    clean = tone.copy()
    clean[:lead] = 0.0
    noisy = r["noisy"].copy()
    noisy[:lead] = r["noise"][:lead]
    return clean, noisy, r["noise"], lead


def _write(tmp_path, x, name="src.wav") -> str:
    p = tmp_path / name
    sf.write(str(p), x, FS, subtype="FLOAT")
    return str(p)


def _read(p: str) -> np.ndarray:
    x, _ = sf.read(p, dtype="float64", always_2d=True)
    return x


# ------------------------------------------------------------------ 1. 内核算式
def test_gain_rule_first_frame_matches_the_closed_form():
    """DD 递归首帧：γ=10、α=0.98 ⇒ ξ=0.98·1 + 0.02·9 = 1.16 ⇒ G=1.16/2.16（逐位）。"""
    P = np.full((2, 1, 1), 10.0)
    N = np.ones((2, 1))
    G = denoise._gains(P, N, g_min=0.01, alpha=0.98)
    assert np.allclose(G, 1.16 / 2.16, rtol=0.0, atol=1e-15)


def test_gain_is_between_the_floor_and_one():
    rng = np.random.default_rng(1)
    P = rng.uniform(0.0, 50.0, (64, 20, 2))
    N = rng.uniform(0.1, 2.0, (64, 2))
    G = denoise._gains(P, N, g_min=10.0 ** (-20 / 20.0), alpha=0.98)
    assert float(G.min()) >= 0.1 - 1e-12
    assert float(G.max()) <= 1.0 + 1e-12


def test_gain_floor_is_reached_when_noise_dominates():
    """γ→0 ⇒ Wiener 增益 →0，但必须被 `reduction_db` 的地板托住。"""
    P = np.full((4, 3, 1), 1e-12)
    N = np.full((4, 1), 1.0)
    G = denoise._gains(P, N, g_min=10.0 ** (-3 / 20.0), alpha=0.98)
    assert np.allclose(G, 10.0 ** (-3 / 20.0), rtol=0.0, atol=1e-15)


# ------------------------------------------------------------------ 2. 真值改进（同一把尺子）
def test_denoise_improves_sdr_on_noisy_tone_and_keeps_energy():
    clean, noisy, noise, lead = _pack()
    out, st = denoise.denoise(noisy[lead:], noise[:lead])
    before = sdr_db(noisy[lead:], clean[lead:])
    after = sdr_db(out, clean[lead:])
    assert after - before > 10.0, "实测 +11.82 dB"
    assert abs(energy_ratio_db(out, clean[lead:])) < 1.0, "实测 −0.46 dB"
    assert st["noise_reduction_measured_db"] < -15.0, "实测 −19.92 dB"
    assert st["processed_samples"] == out.size
    assert isinstance(st["processed_samples"], int)
    assert st["n_fft"] == N_FFT and st["hop"] == N_FFT // 4


def test_defaults_are_the_measured_choices():
    """默认值不是手感：4096/20.0/0.98 都在 `packaging/denoise_probe.py` 的扫描里定过。"""
    assert denoise.DEFAULT_N_FFT == 4096
    assert denoise.DEFAULT_REDUCTION_DB == 20.0
    assert denoise.ALPHA_DD == 0.98


def test_reported_numbers_are_rounded_to_three_decimals():
    """审计里的实测值固定三位小数（`_round` 的口径，ruff/判据都按这个读）。"""
    clean, noisy, noise, lead = _pack()
    _, st = denoise.denoise(noisy[lead:], noise[:lead])
    for key in ("noise_reduction_measured_db", "energy_ratio_measured_db"):
        v = st[key]
        assert v is not None and v == round(v, 3)


def test_lower_reduction_cap_limits_the_noise_suppression():
    """`reduction_db=3` 时噪声段实测只被压 **−2.99 dB**（地板生效，压不更深）。"""
    _, _, noise, _ = _pack()
    _, st = denoise.denoise(noise, noise, reduction_db=3.0)
    assert -3.2 < st["noise_reduction_measured_db"] < -2.8


def test_denoise_is_deterministic_and_stereo_safe():
    clean, noisy, noise, lead = _pack()
    stereo = np.concatenate([noisy, noisy * 0.5], axis=1)
    nz = np.concatenate([noise, noise], axis=1)
    a, st = denoise.denoise(stereo, nz)
    b, _ = denoise.denoise(stereo, nz)
    assert np.array_equal(a, b)
    assert a.shape == stereo.shape
    assert not np.array_equal(a[:, 0], a[:, 1])
    assert st["processed_samples"] == stereo.size


def test_zero_noise_profile_does_not_blow_up():
    """噪声段**全零**（静音当噪声段）时，`N` 的 1e-30 守卫必须挡住除零（否则 NaN）。"""
    seg = corpus.white_noise(0.5, FS, rms=0.1, seed=1)
    out, st = denoise.denoise(seg, np.zeros((N_FFT, 1)))
    assert np.all(np.isfinite(out))
    assert st["noise_reduction_measured_db"] is None       # 噪声段全零 ⇒ 能量比无定义


def test_all_zero_segment_stays_exactly_zero():
    """空选区（全零）经 STFT 往返仍**逐位为 0**（没有浮点噪声被放大）。"""
    _, _, noise, _ = _pack()
    out, _ = denoise.denoise(np.zeros((FS, 1)), noise[:N_FFT])
    assert np.array_equal(out, np.zeros((FS, 1)))


# ------------------------------------------------------------------ 3. 输入守卫
def test_short_noise_profile_is_rejected():
    _, noisy, noise, lead = _pack()
    with pytest.raises(ValueError, match="噪声段太短"):
        denoise.denoise(noisy[lead:], noise[:N_FFT - 1])


def test_bad_reduction_and_bad_stft_params_are_rejected():
    _, noisy, noise, lead = _pack()
    with pytest.raises(ValueError, match="压制上限"):
        denoise.denoise(noisy[lead:], noise[:N_FFT], reduction_db=0.0)
    with pytest.raises(ValueError, match="hop"):
        denoise.denoise(noisy[lead:], noise[:N_FFT], hop=N_FFT)
    with pytest.raises(ValueError, match="声道数"):
        denoise.denoise(np.zeros((100, 1)),
                        np.concatenate([noise[:N_FFT], noise[:N_FFT]], axis=1))


def test_op_validates_its_ranges_and_params():
    with pytest.raises(ValueError, match="时间选区"):
        DenoiseOp(1.0, 0.5, 0.0, 0.2)
    with pytest.raises(ValueError, match="噪声段"):
        DenoiseOp(0.0, 1.0, 0.4, 0.4)
    with pytest.raises(ValueError, match="压制上限"):
        DenoiseOp(0.0, 1.0, 0.0, 0.2, reduction_db=0.0)
    with pytest.raises(ValueError, match="压制上限"):
        DenoiseOp(0.0, 1.0, 0.0, 0.2, reduction_db=-1.0)
    with pytest.raises(ValueError, match="n_fft"):
        DenoiseOp(0.0, 1.0, 0.0, 0.2, n_fft=1)
    with pytest.raises(ValueError, match="hop"):
        DenoiseOp(0.0, 1.0, 0.0, 0.2, hop=0)
    # 边界侧：n_fft=2 与 hop=1 **合法**（校验是 >= / <，不是把边界也拒掉）
    assert DenoiseOp(0.0, 1.0, 0.0, 0.2, n_fft=2).n_fft == 2
    assert DenoiseOp(0.0, 1.0, 0.0, 0.2, hop=1).hop == 1


# ------------------------------------------------------------------ 4. 渲染路径（非破坏边界）
def test_selection_outside_and_profile_are_bit_exact(tmp_path):
    """选区之外（含**噪声段本身**）逐位不变；选区内确实被改。

    选区**不在文件末尾**（`[0.5, 1.5)`）：这样"读多了"的变异体会被看出来。
    """
    clean, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [DenoiseOp(LEAD, 1.5, 0.0, LEAD)]), dst)
    a, b = _read(src), _read(dst)
    assert np.array_equal(a[:lead], b[:lead]), "噪声段被写动了"
    assert np.array_equal(a[int(1.5 * FS):], b[int(1.5 * FS):]), "选区之后被写动了"
    assert not np.array_equal(a[lead:int(1.5 * FS)], b[lead:int(1.5 * FS)]), (
        "选区内没有被改——判据失去意义")
    assert audit["domain"] == "denoise" and audit["algorithm"] == "wiener_dd_floor_tracked"
    rec = audit["ops"][0]
    assert rec["noise_samples"] == lead
    for key in ("noise_t0", "noise_t1", "noise_samples", "noise_frames",
                "noise_reduction_measured_db", "energy_ratio_measured_db",
                "reduction_db", "n_fft", "hop", "alpha_dd", "g_min_db"):
        assert key in rec, key


def test_profile_not_starting_at_zero_is_read_exactly(tmp_path):
    """噪声段 `[0.2, 0.5)`：读的帧数必须**恰好** 0.3 s（读多了/读少了都会被这条抓住）。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    audit = render(Project(src, [DenoiseOp(LEAD, 1.5, 0.2, LEAD)]),
                   str(tmp_path / "out.wav"))
    assert audit["ops"][0]["noise_samples"] == int(0.3 * FS)
    assert audit["ops"][0]["noise_frames"] >= 1


def test_adjacent_denoise_selections_are_allowed(tmp_path):
    """相邻（端点相接）的选区**不算重叠**：两条都处理，边界样本不被重复处理。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    audit = render(Project(src, [DenoiseOp(0.5, 1.0, 0.0, LEAD),
                                 DenoiseOp(1.0, 1.5, 0.0, LEAD)]),
                   str(tmp_path / "out.wav"))
    assert [o["applied_frames"] for o in audit["ops"]] == [int(0.5 * FS)] * 2


def test_profile_exactly_one_window_is_accepted(tmp_path):
    """噪声段**恰好一个窗长**（4096 采样）必须接受（守卫是 `<`，不是 `<=`）。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    exact = N_FFT / FS
    audit = render(Project(src, [DenoiseOp(LEAD, 1.5, 0.0, exact)]),
                   str(tmp_path / "out.wav"))
    assert audit["ops"][0]["noise_samples"] == N_FFT


def test_selection_at_or_beyond_the_end_is_a_noop(tmp_path):
    """选区落在文件末尾之外（空跨度）：照常写完整文件，不许去处理空段。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [DenoiseOp(2.0, 2.2, 0.0, LEAD)]), dst)
    assert audit["ops"][0]["applied_frames"] == 0
    assert np.array_equal(_read(src), _read(dst))


def test_truncated_source_is_reported_not_crashed(tmp_path):
    """截断 FLAC：如实记 `read_incomplete`，不 traceback；已读部分照常写出。"""
    _, noisy, _, lead = _pack()
    src = str(tmp_path / "src.flac")
    sf.write(src, noisy.astype(np.float32), FS, subtype="PCM_16")
    raw = open(src, "rb").read()
    open(src, "wb").write(raw[:int(len(raw) * 0.6)])
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [DenoiseOp(LEAD, 1.9, 0.0, LEAD)]), dst)
    assert audit["read_incomplete"] is True
    assert 0 < audit["frames"] < audit["frames_claimed"]


def test_short_noise_read_is_reported_even_when_the_segment_is_full(tmp_path, monkeypatch):
    """**只短读噪声段**（段本身读满）也必须上报并停下——不许"带着半截噪声继续算"。

    真容器里 WAV 截断会自校正、FLAC 截断会抛异常（见 `test_edit_stft`），所以这里
    直接打桩 `SoundFile.read`：把噪声段那次读**少给 1000 帧**，其余照常。
    """
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    nk0 = int(0.2 * FS)
    real_read = sf.SoundFile.read

    def short_noise_read(self, frames=-1, *a, **k):
        out = real_read(self, frames, *a, **k)
        start = self.tell() - len(out) if hasattr(self, "tell") else -1
        if frames and len(out) == frames and start == nk0:
            return out[:-1000]
        return out

    monkeypatch.setattr(sf.SoundFile, "read", short_noise_read)
    audit = render(Project(src, [DenoiseOp(LEAD, 1.5, 0.2, LEAD)]), dst)
    assert audit["read_incomplete"] is True
    assert audit["frames"] < audit["frames_claimed"], "短读后不该继续写完整文件"


def test_overlapping_denoise_selections_chain_in_stack_order(tmp_path):
    """★ 重叠不再拒绝（v1.1）：走链式渲染，栈顺序即语义（后者作用在前者结果上）。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [DenoiseOp(LEAD, 1.4, 0.0, LEAD),
                                 DenoiseOp(1.2, 2.0, 0.0, LEAD)]), dst)
    assert audit.get("chained") is True and len(audit["stages"]) == 2
    assert os.path.exists(dst)


def test_profile_outside_the_file_is_rejected_before_writing(tmp_path):
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    with pytest.raises(ValueError, match="噪声段太短"):
        render(Project(src, [DenoiseOp(LEAD, 2.0, 2.0, 2.5)]), dst)
    assert not os.path.exists(dst)


def test_short_but_nonzero_profile_is_rejected_before_writing(tmp_path):
    """段内但短于一个窗长（1.95–2.0 s = 2400 采样）⇒ 渲染层**先**拒绝，不留半截目标。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    with pytest.raises(ValueError, match="噪声段太短"):
        render(Project(src, [DenoiseOp(LEAD, 1.5, 1.95, 2.0)]), dst)
    assert not os.path.exists(dst)


def test_denoise_chains_with_gain(tmp_path):
    """与增益混用走链式：`denoise` 在前、`gain` 在后（`render.PHASE`）。"""
    _, noisy, _, lead = _pack()
    src = _write(tmp_path, noisy)
    dst = str(tmp_path / "out.wav")
    audit = render(Project(src, [GainOp(LEAD, 2.0, -3.0),
                                 DenoiseOp(LEAD, 2.0, 0.0, LEAD)]), dst)
    assert audit["chained"] is True
    assert [s["op"]["kind"] for s in audit["stages"]] == ["denoise", "gain"]


# ------------------------------------------------- 逐帧噪声底跟踪（2026-10-27，schema v2）
def _stepped_pack():
    """台阶噪声床语料（与 denoise_nonstat_probe 同构）：内容三段 低→高→低。"""
    import numpy as np
    FS = 48000
    clean = corpus.harmonic_tone(6.0, FS, amp=0.5, seed=3)
    n = clean.shape[0]
    p = float(np.sum(clean ** 2))

    def rms_for(snr):
        return float(np.sqrt(p / n / (10.0 ** (snr / 10.0))))

    def w(k, rms, seed):
        return np.random.default_rng(seed).standard_normal((k, 1)) * rms

    pad = FS
    prefix = np.concatenate([w(pad // 2, rms_for(10.0), 10), w(pad - pad // 2, rms_for(0.0), 12)])
    body = np.concatenate([w(n // 3, rms_for(10.0), 11), w(n // 3, rms_for(0.0), 12),
                           w(n - 2 * (n // 3), rms_for(10.0), 13)])
    clean_full = np.concatenate([np.zeros((pad, 1)), clean])
    return {"clean": clean_full, "noisy": clean_full + np.concatenate([prefix, body]),
            "pure_low": (0.0, 0.5)}


def test_floor_tracking_recovers_stepped_floor(tmp_path):
    """★ 台阶噪声床 + 低床参考：跟踪把"留白"收回（低床段 ΔSDR +2.04 → +9.03 实测）。

    钉 ≥ +6 dB：跟踪关时该段只有 +2.04（欠压制），开时 +9.03——这是本特性的存在理由。
    """
    from audiocheck.core.edit.metrics import sdr_db
    pack = _stepped_pack()
    FS = 48000
    y = pack["noisy"]
    nz = y[0:int(0.5 * FS)]
    out, st = denoise.denoise(y[int(1.0 * FS):int(7.0 * FS)], nz, track_floor=True)
    ref = pack["clean"][int(5.0 * FS):int(7.0 * FS)]
    d = sdr_db(out[int(4.0 * FS):int(6.0 * FS)], ref) - \
        sdr_db(y[int(5.0 * FS):int(7.0 * FS)], ref)
    assert d > 6.0, "低床段应从 +2.04 提到 +9.03 量级，得到 %+.2f dB" % d
    assert st["track_floor"] is True and "floor_track_db" in st


def test_floor_tracking_stationary_cost_is_bounded():
    """★ 稳态素材上跟踪的代价有界（实测 ≤0.66 dB）：钉 |Δ| ≤ 1.5 dB（四格最差留余量）。"""
    import numpy as np

    from audiocheck.core.edit.metrics import sdr_db
    FS = 48000
    for snr, kind, seed in ((10.0, "white", 41), (0.0, "white", 42), (-5.0, "white", 43),
                            (0.0, "pink", 44)):
        pack = corpus.add_noise(corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3),
                                snr, kind, seed=seed)
        clean, y = pack["clean"], pack["noisy"]
        nz = corpus.white_noise(0.5, FS,
                                rms=float(np.sqrt(np.mean(pack["noise"] ** 2))), seed=99)
        d0 = sdr_db(denoise.denoise(y, nz)[0], clean) - sdr_db(y, clean)
        d1 = sdr_db(denoise.denoise(y, nz, track_floor=True)[0], clean) - sdr_db(y, clean)
        assert abs(d1 - d0) <= 1.5, \
            "%s @%+g：跟踪代价 |Δ|=%.2f 超界（基线 %.2f）" % (kind, snr, abs(d1 - d0), d0)


def test_track_floor_off_is_v1_algorithm_name():
    """track_floor=False ⇒ 口径名回到 `wiener_dd_profile`（语义变了名字必须变，双向都钉）。"""
    import numpy as np
    pack = corpus.add_noise(corpus.harmonic_tone(1.0, 48000, amp=0.5, seed=3), 0.0,
                            "white", seed=45)
    nz = corpus.white_noise(0.5, 48000,
                            rms=float(np.sqrt(np.mean(pack["noise"] ** 2))), seed=46)
    _, st_off = denoise.denoise(pack["noisy"], nz, track_floor=False)
    _, st_on = denoise.denoise(pack["noisy"], nz, track_floor=True)
    assert st_off["track_floor"] is False and "floor_track_db" not in st_off
    assert st_on["track_floor"] is True


# ------------------------------------------------- _floor_track 单元钉死（变异轮缺口）
def test_floor_track_exact_values_on_synthetic():
    """★ 合成小数组**逐值**钉死 `_floor_track`（因果语义：out[t] = clip(r[0..t-1] 的
    λ 混合)；r = median_b(P/N)·(1/ln2)）。序列同时让**上下 clip 都绑定**：

    N=1、P（单 bin，5 帧）= [0.1, 100, 0.5, 3, 3] ⇒ r = P/ln2
    = [0.1443, 144.27, 0.7213, 4.328, 4.328]。**先发后更**（out[t] = r[0..t−1] 的混合）：
    out0 = clip(0.1443) = **0.25**（下界在场）
    out1 = clip(r0) = 0.25；L←0.8·0.1443+0.2·144.27 = 28.97
    out2 = clip(28.97) = **4.0**（上界在场，TRACK_SCALE_MAX 钉死）；L←23.32
    out3 = 4.0；out4 = 4.0
    比值若被改成乘、ln2 校正若被改除、λ 混合若被改除——数值全部对不上 ⇒ 全杀。
    """
    import numpy as np

    from audiocheck.core.edit.denoise import _floor_track
    P = np.array([[[0.1], [100.0], [0.5], [3.0], [3.0]]])    # (bins=1, frames=5, ch=1)
    N = np.array([[1.0]])                                    # (bins=1, ch=1)
    out = _floor_track(P, N)
    got = out[0, :, 0]
    lv = 0.1 / np.log(2.0)
    vals = []
    for p in [0.1, 100.0, 0.5, 3.0, 3.0]:
        vals.append(min(max(lv, 0.25), 4.0))      # 先发
        lv = 0.8 * lv + 0.2 * (p / np.log(2.0))   # 后更（与实现同序）
    assert np.allclose(got, vals, rtol=1e-12), (got, vals)
    assert abs(got[0] - 0.25) < 1e-12 and abs(got[2] - 4.0) < 1e-12


def test_floor_track_output_is_finite_when_noise_segment_is_silent():
    """★ 噪声段=数字静音：N=0 ⇒ 守卫 `max(N,1e-30)` 在场 ⇒ 输出**有限**（不许 NaN 扩散）。

    1e-30→0.0 的变异会让 ratio=P/0=inf、中位数=NaN、增益 NaN、输出 NaN——
    "用户拿了一段静音当噪声段"是真实误操作，产出必须是有限值。
    """
    import numpy as np
    FS = 48000
    pack = corpus.add_noise(corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3), 0.0,
                            "white", seed=47)
    out, st = denoise.denoise(pack["noisy"], np.zeros((FS, 1)), track_floor=True)
    assert np.isfinite(out).all(), "静音噪声段不许产出 NaN/inf"
    # N=0 ⇒ floor_track_db 退化为地板值（0/0 → 1e-30 → −300 dB）：有限、如实，不装有意义
    assert st["floor_track_db"]["max"] == -300.0
    assert np.isfinite(st["floor_track_db"]["max"])


def test_floor_track_stats_match_recomputation():
    """★ `floor_track_db` 必须等于 `20·log10(N_track/N)` 的实测重算（报告式钉死）。"""
    import numpy as np

    from audiocheck.core.edit import stft as _stft
    from audiocheck.core.edit.denoise import _floor_track
    FS = 48000
    pack = corpus.add_noise(corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3), 0.0,
                            "white", seed=48)
    nz = corpus.white_noise(0.5, FS, rms=float(np.sqrt(np.mean(pack["noise"] ** 2))), seed=49)
    out, st = denoise.denoise(pack["noisy"], nz, track_floor=True)
    S, _ = _stft.analyze(pack["noisy"], n_fft=4096, hop=1024)
    Sn, _ = _stft.analyze(nz, n_fft=4096, hop=1024)
    N = (np.abs(Sn) ** 2).mean(axis=1)
    Nt = _floor_track(np.abs(S) ** 2, N)
    db = 10.0 * np.log10(np.maximum(Nt / np.maximum(N[:, None, :], 1e-30), 1e-30))
    assert st["floor_track_db"]["max"] == round(float(db.max()), 3)
    assert st["floor_track_db"]["min"] == round(float(db.min()), 3)
    assert st["floor_track_db"]["mean"] == round(float(db.mean()), 3)


def test_floor_track_ln2_correction_without_clip_saturation():
    """★ ln2 偏置校正在**不饱和区间**内逐值钉死（×/÷ 变异被 clip 饱和吞不掉的补丁）。

    r = median·(1/ln2)（正确）vs median·ln2（变异）差 2.08 倍——取 r 全程落在
    (0.25, 4) 内的序列，逐值分开。
    """
    import numpy as np

    from audiocheck.core.edit.denoise import _floor_track
    P = np.array([[[0.7], [1.4], [0.7]]])                # (1, 3, 1)
    N = np.array([[1.0]])
    out = _floor_track(P, N)
    got = out[0, :, 0]
    lv = 0.7 / np.log(2.0)                               # 1.0101
    vals = []
    for p in [0.7, 1.4, 0.7]:
        vals.append(min(max(lv, 0.25), 4.0))
        lv = 0.8 * lv + 0.2 * (p / np.log(2.0))
    assert np.allclose(got, vals, rtol=1e-12), (got, vals)
    assert abs(got[0] - 0.7 / np.log(2.0)) < 1e-12       # 正确方向：/ln2（1.010），不是 ×ln2（0.485）


def test_digital_silence_inside_segment_stays_finite():
    """★ 选区内含**数字静音**段：gamma 守卫 `max(P,1e-30)` 在场 ⇒ 输出有限（不许 NaN）。"""
    import numpy as np
    FS = 48000
    pack = corpus.add_noise(corpus.harmonic_tone(2.0, FS, amp=0.5, seed=3), 0.0,
                            "white", seed=50)
    y = pack["noisy"].copy()
    y[int(0.5 * FS):int(0.7 * FS)] = 0.0                 # 段内 0.2 s 数字静音
    nz = corpus.white_noise(0.5, FS, rms=float(np.sqrt(np.mean(pack["noise"] ** 2))), seed=51)
    out, st = denoise.denoise(y, nz, track_floor=True)
    assert np.isfinite(out).all(), "段内静音不许产出 NaN/inf"
    assert np.isfinite(st["energy_ratio_measured_db"] or 0.0)
