"""P0 地基判据：非破坏编辑内核（`core/edit/`）。

每条判据对应 `docs/editor_roadmap.md §5` 的一条"极致/专业"定义：
  1. **无损往返**：未被编辑的采样**逐位不变**（浮点与整型各测一遍）。
  2. **测量互证**：改完用**同一个测量内核**再量一次，证明确实只改了目标（见 `test_measurement_crosscheck`）。
  3. 选区口径与 `ops.GainOp` 的 docstring **逐位对齐**（半开区间 `[t0,t1)`）。
  4. 非破坏硬保证：**不许覆盖源**；削波**不静默**，要如实上报。
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import DeclickOp, GainOp, Project, render, render_gain  # noqa: E402

FS = 48000


def _sample_at(t: float, fs: int = FS) -> int:
    """与 `render._sample_at` 同一口径（半开区间左端）。"""
    return int(math.ceil(t * fs - 1e-9))


def _tone(seconds: float = 2.0, fs: int = FS) -> np.ndarray:
    n = int(seconds * fs)
    t = np.arange(n) / fs
    x = 0.5 * np.sin(2 * np.pi * 440 * t) + 0.1 * np.sin(2 * np.pi * 1234 * t)
    return np.column_stack([x, x * 0.9])


def _burst(seconds: float = 2.0, fs: int = FS) -> np.ndarray:
    """安静 0.1 + 在 [0.5,1.0) 一声 0.9 的突发（供测量互证用：峰值只在选区内）。"""
    n = int(seconds * fs)
    x = np.full(n, 0.1)
    x[int(0.5 * fs):int(1.0 * fs)] = 0.9
    return np.column_stack([x, x])


def _write(path, x, subtype: str = "FLOAT", fs: int = FS) -> str:
    sf.write(str(path), x.astype(np.float32), fs, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


# --------------------------------------------------- 1. 无损往返（浮点）
def test_no_op_is_bit_exact(tmp_path):
    """`gain_db == 0.0` 是 no-op：**整轨逐位不变**（浮点 subtype）。"""
    src = _write(tmp_path / "src.wav", _tone())
    p = Project(src, [GainOp(0.2, 1.8, 0.0)])
    assert p.is_identity()
    audit = render(p, str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav")), "no-op 竟然改了采样"
    assert audit["ops"][0]["applied_frames"] == 0


def test_empty_ops_is_bit_exact(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    render(Project(src), str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav"))


# --------------------------------------------------- 2. 选区：只动选中处
def test_gain_only_touches_selection(tmp_path):
    """−6 dB 施加于 `[0.5, 1.0)`：**区外逐位不变**，区内恰为 ∓6 dB。"""
    src = _write(tmp_path / "src.wav", _tone())
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")

    assert np.array_equal(a[:k0], b[:k0]), "选区**之前**的采样被动了"
    assert np.array_equal(a[k1:], b[k1:]), "选区**之后**的采样被动了"
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), rtol=1e-6,
                               err_msg="选区内的实际增益不是 −6 dB")
    assert audit["ops"][0]["applied_frames"] == k1 - k0 == int(0.5 * FS)


def test_selection_edge_convention(tmp_path):
    """半开区间：`[t0, t1)` —— 首样本受影响、`t1` 处那个样本**不**受影响。
    改口径（比如把 `ceil` 换成 `int`）会让这条红。"""
    src = _write(tmp_path / "src.wav", _tone())
    render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")
    assert not np.array_equal(a[k0], b[k0]), "区间**左端**的第一个样本应当受影响"
    assert not np.array_equal(a[k1 - 1], b[k1 - 1]), "区间**右端**的前一个样本应当受影响"
    assert np.array_equal(a[k1], b[k1]), "半开区间：t1 处的样本不应受影响"


def test_ops_compose_in_order(tmp_path):
    """多个操作按加入顺序依次作用；互不重叠的选区各自独立。"""
    src = _write(tmp_path / "src.wav", _tone())
    p = Project(src, [GainOp(0.0, 0.5, -3.0), GainOp(1.0, 1.5, 3.0)])
    render(p, str(tmp_path / "dst.wav"))
    a, b = _read(src), _read(tmp_path / "dst.wav")
    r0, r1 = _sample_at(0.5), _sample_at(1.0)
    np.testing.assert_allclose(b[:r0], a[:r0] * 10.0 ** (-3.0 / 20.0), rtol=1e-6)
    np.testing.assert_allclose(b[r1:_sample_at(1.5)], a[r1:_sample_at(1.5)] * 10.0 ** (3.0 / 20.0),
                               rtol=1e-6)
    assert np.array_equal(a[r0:r1], b[r0:r1]), "两个选区之间的采样被动了"


# --------------------------------------------------- 3. 无损往返（整型 PCM）
def test_pcm16_roundtrip_is_bit_exact(tmp_path):
    """整型 PCM_16 源：no-op 导出必须**逐位不变**（读→写不能悄悄重量化）。"""
    src = _write(tmp_path / "src.wav", _tone(), subtype="PCM_16")
    render(Project(src), str(tmp_path / "dst.wav"))
    assert np.array_equal(_read(src), _read(tmp_path / "dst.wav")), "PCM_16 往返不逐位相等"


def test_pcm16_gain_outside_is_bit_exact(tmp_path):
    """整型源 + 选区增益：**区外仍逐位不变**（区内允许 1 LSB 量化误差）。"""
    src = _write(tmp_path / "src.wav", _tone(), subtype="PCM_16")
    k0, k1 = _sample_at(0.5), _sample_at(1.0)
    render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")
    assert np.array_equal(a[:k0], b[:k0])
    assert np.array_equal(a[k1:], b[k1:])
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), atol=2.0 / 32768)


# --------------------------------------------------- 4. 审计与非破坏保证
def test_audit_record_contents(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.5, 1.0, -6.0)
    assert audit["samplerate"] == FS and audit["channels"] == 2
    assert audit["subtype"] == "FLOAT"
    assert audit["frames"] == int(2.0 * FS)
    assert abs(audit["duration_s"] - 2.0) < 1e-6
    assert audit["ops"][0]["kind"] == "gain" and audit["ops"][0]["gain_db"] == -6.0
    assert audit["clipped_frames"] == 0


def test_refuses_to_overwrite_source(tmp_path):
    """非破坏硬保证：目标 == 源 → 直接拒绝（`overwrite=True` 也不行）。"""
    src = _write(tmp_path / "src.wav", _tone())
    with pytest.raises(ValueError):
        render_gain(src, src, 0.0, 1.0, -6.0)
    with pytest.raises(ValueError):
        render_gain(src, src, 0.0, 1.0, -6.0, overwrite=True)


def test_refuses_existing_dest_without_overwrite(tmp_path):
    src = _write(tmp_path / "src.wav", _tone())
    dst = _write(tmp_path / "dst.wav", _tone())
    with pytest.raises(FileExistsError):
        render_gain(src, dst, 0.0, 1.0, -6.0)
    render_gain(src, dst, 0.0, 1.0, -6.0, overwrite=True)      # 显式允许才覆盖


def test_clipping_is_reported_not_silent(tmp_path):
    """整数 subtype 上 +6 dB 必然削波：**必须如实上报**（不许静默钳位）。"""
    loud = np.full((int(0.5 * FS), 2), 0.9)
    src = _write(tmp_path / "src.wav", np.vstack([loud, loud]), subtype="PCM_16")
    audit = render_gain(src, str(tmp_path / "dst.wav"), 0.0, 0.5, 6.0)
    assert audit["clipped_frames"] > 0, "削波没有被统计（静默钳位=糊弄）"
    out = _read(tmp_path / "dst.wav")
    assert np.abs(out).max() <= 1.0 + 1e-9


def test_truncated_source_is_reported_not_crashed(tmp_path):
    """截断源（真实曲库里就有这种下载没下全的文件）：渲染**不许 traceback**，
    要如实记 `read_incomplete`，并把已读部分照常写出。"""
    src = str(tmp_path / "src.flac")
    sf.write(src, _tone(3.0).astype(np.float32), FS, subtype="PCM_16")
    raw = open(src, "rb").read()
    open(src, "wb").write(raw[:int(len(raw) * 0.6)])          # 砍掉 40%
    dst = str(tmp_path / "dst.wav")
    audit = render_gain(src, dst, 0.0, 1.0, -3.0, subtype="PCM_16")   # 不许抛
    assert audit["read_incomplete"] is True, "截断没有被上报"
    assert 0 < audit["frames"] < audit["frames_claimed"], (
        "应当只读出一部分：实读 %d / 声称 %d" % (audit["frames"], audit["frames_claimed"]))
    got, _ = sf.read(dst, always_2d=True)
    assert got.shape[0] == audit["frames"], "写出的帧数应与实读一致"


def test_selection_edge_survives_float_rounded_time(tmp_path):
    """`_EPS` 的存在理由：`t·fs` 的浮点乘积会**恰好落在整数上方一丁点**，`ceil` 必须压回来。

    实测扫过 `(fs, t)` 网格（`t` 步长 0.0001 s、到 300 s、5 种采样率）：fs=48000 下
    **第一个可区分**的 t 是 **0.0085**（`0.0085·48000 = 408.00000000000006`）——
    没有 `_EPS` 时 `ceil` 到 **409**，整段选区向右错一个采样。
    ⚠ `0.1 s` 那类"看起来典型"的时间**区分不了**（`0.1·48000 = 4800.0` 恰好是整数）——
    上一版判据就写成了它，于是变异体照样存活（2026-10-18 探针的教训）。
    """
    src = _write(tmp_path / "src.wav", _tone())
    render_gain(src, str(tmp_path / "dst.wav"), 0.0085, 0.05, -6.0)
    a, b = _read(src), _read(tmp_path / "dst.wav")
    k0 = _sample_at(0.0085)
    assert k0 == 408, "0.0085 s @48 kHz 应当是样本 408（不是 409），实得 %d" % k0
    assert not np.array_equal(a[k0], b[k0]), "左端第一个样本（408）必须受影响"
    assert np.array_equal(a[k0 - 1], b[k0 - 1]), "前一个样本（407）不该受影响"


def test_small_blocksize_and_late_selection(tmp_path):
    """小块长 + 偏后的选区：`k0/k1` 是**相对当前块**算的（`k0 − pos`），块起点 `pos` 必须在场。

    这条同时钉住三处（都是 2026-10-18 变异探针的存活项）：
      * `if k1 <= k0: continue` —— 选区**还没到**当前块时要跳过（写成 `<` 会让
        `applied_frames` 被减去一个块长，变成负数）；
      * `b[k0 − pos : k1 − pos]` —— 写成 `+ pos` 时，**只有 pos=0 的第一块**看起来是对的，
        所以小块长必须被真的跑到；
      * `applied_frames` 在跨块时的累加口径。
    """
    src = _write(tmp_path / "src.wav", _tone())              # 2 s
    k0, k1 = _sample_at(1.0), _sample_at(1.2)                # 48000 / 57600，远在第一个小块之后
    audit = render(Project(src, [GainOp(1.0, 1.2, -6.0)]), str(tmp_path / "dst.wav"),
                   blocksize=1024)
    assert audit["ops"][0]["applied_frames"] == k1 - k0 == 9600
    a, b = _read(src), _read(tmp_path / "dst.wav")
    assert np.array_equal(a[:k0], b[:k0]), "选区**之前**的采样被动了"
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), rtol=1e-6)
    assert np.array_equal(a[k1:], b[k1:])


def test_full_scale_samples_are_not_counted_as_clipped(tmp_path):
    """**满刻度样本本身不算削波**：判据是严格 `> 1.0`（PCM_16 的 ±1.0 是合法码值）。

    ⚠ 写成 `>= 1.0` 会把每一张正常母带里的满刻度样本都报成"被钳位"——那是**误报**，
    而"削波不静默"这条规矩靠的就是这个计数可信。
    """
    x = np.full((2048, 1), -32768, dtype=np.int16)      # −32768 ⇒ 读出恰是 −1.0
    src = str(tmp_path / "src.wav")
    sf.write(src, x, FS, subtype="PCM_16")
    audit = render(Project(src), str(tmp_path / "dst.wav"))     # no-op，只走记账那一段
    assert audit["clipped_frames"] == 0, "满刻度的合法码值被当成削波了"


# --------------------------------------------------- 5. 与测量内核互证
def test_measurement_crosscheck(tmp_path):
    """**用同一个测量内核再量一次**：证明增益确实只改了选中处。

    素材设计（关键是让**峰值**本身成为证据）：安静 0.1，选区内一声 0.9。
    对选区 −20 dB 后：突发变成 0.09 ⇒ **新的峰值必须是选区外的 0.1（≈ −20 dBFS），
    而不是 0.9×0.1（≈ −40.9 dBFS）**。
      * 若峰值 ≈ −20.0 dBFS ⇒ 突发被压了 20 dB，**且选区外没被压**（否则峰值会掉到 0.01）。
      * 若实现把整轨都压了，或选区挪位，这条会立刻红。
    """
    from audiocheck.core.engine import analyze

    src = _write(tmp_path / "src.wav", _burst())
    dst = str(tmp_path / "dst.wav")
    render_gain(src, dst, 0.5, 1.0, -20.0)

    p_src = analyze(src, deep=False)["peak_dbfs"]
    p_dst = analyze(dst, deep=False)["peak_dbfs"]
    assert abs(p_src - 20 * math.log10(0.9)) < 0.05, "素材峰值不是 0.9"
    assert abs(p_dst - 20 * math.log10(0.1)) < 0.05, (
        "导出后峰值 %.2f dBFS 不等于选区外的 0.1（%.2f）——增益没有只作用在选区内"
        % (p_dst, 20 * math.log10(0.1)))


# ------------------------------------------------- 审计摘要与实现自证（借鉴 queyi_core）
def test_audit_digest_is_stable_and_path_independent(tmp_path):
    """★ `audit_digest`：同一修复的两个落盘位置 ⇒ **同摘要**（dest 不参与）；
    不同参数 ⇒ 不同摘要；`kernel_digest` 在场且等长。"""
    import numpy as np

    from audiocheck.core.edit import GainOp, Project, render

    src = str(tmp_path / "a.wav")
    sf.write(src, (0.4 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS))[:, None].astype(np.float32),
             FS, subtype="FLOAT")
    a1 = render(Project(src, [GainOp(0.2, 0.8, -3.0)]), str(tmp_path / "o1.wav"))
    a2 = render(Project(src, [GainOp(0.2, 0.8, -3.0)]), str(tmp_path / "o2.wav"))
    assert a1["audit_digest"] == a2["audit_digest"], "同修复不同路径 ⇒ 同摘要"
    a3 = render(Project(src, [GainOp(0.2, 0.8, -6.0)]), str(tmp_path / "o3.wav"))
    assert a3["audit_digest"] != a1["audit_digest"], "不同参数 ⇒ 不同摘要"
    assert len(a1["kernel_digest"]) == 64 and a1["kernel_digest"] == a3["kernel_digest"]


def test_audit_digest_is_recomputable_from_stored_record(tmp_path):
    """★ **自指字段**：对已落盘的记录复算 `audit_digest` 必须得到同一个值。

    这是"核对"能成立的前提。陷阱在于：渲染时先算摘要、再把它写进记录
    （算的时候 `audit_digest` 不在 dict 里），而复算时它**已经在** ⇒
    若 `audit_digest()` 不排除自身，复算就是"摘要 + 摘要"再哈希，**永远对不上**。

    实测症状（2026-10-28）：`--verify-audit` 拿**原样未改**的记录都核不过，
    报"摘要不一致"——核对器一上线就全是假失败，等于不可用。
    """
    import numpy as np

    from audiocheck.core.edit import GainOp, Project, render
    from audiocheck.core.edit.render import audit_digest

    src = str(tmp_path / "a.wav")
    sf.write(src, (0.4 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS))[:, None]
             .astype(np.float32), FS, subtype="FLOAT")
    a = render(Project(src, [GainOp(0.2, 0.8, -3.0)]), str(tmp_path / "o.wav"))
    assert audit_digest(a) == a["audit_digest"], (
        "对已落盘记录复算摘要必须一致（自指字段没被排除？）")


def test_audit_digest_ignores_self_field():
    """`audit_digest()` 必须**排除自身**：往 dict 里塞不同的 digest 值不影响复算。"""
    from audiocheck.core.edit.render import audit_digest
    base = {"frames": 100, "domain": "time", "ops": []}
    d1 = dict(base, audit_digest="0" * 64)
    d2 = dict(base, audit_digest="f" * 64)
    assert audit_digest(d1) == audit_digest(d2) == audit_digest(base)


def test_canonical_json_is_deterministic():
    """★ `canonical_json`：键序无关、紧凑、非 ASCII 直出（跨平台同字节）。"""
    from audiocheck.core.edit.render import canonical_json
    a = canonical_json({"b": 1, "a": "中文", "c": [3, 2]})
    b = canonical_json({"c": [3, 2], "a": "中文", "b": 1})
    assert a == b, "键序不同 ⇒ 同字节"
    assert a == '{"a":"中文","b":1,"c":[3,2]}'


def test_audit_digest_excludes_timestamp(tmp_path):
    """★ `timestamp_utc`（CLI 注入的墙钟）不参与摘要——同一修复跨时刻可核对。"""
    import numpy as np

    from audiocheck.core.edit import GainOp, Project, render
    from audiocheck.core.edit.render import audit_digest

    src = str(tmp_path / "a.wav")
    sf.write(src, (0.3 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS))[:, None].astype(np.float32),
             FS, subtype="FLOAT")
    a = render(Project(src, [GainOp(0.0, 1.0, -2.0)]), str(tmp_path / "o.wav"))
    a_ts1 = dict(a, timestamp_utc="2026-01-01T00:00:00Z")
    a_ts2 = dict(a, timestamp_utc="2026-12-31T23:59:59Z")
    assert audit_digest(a_ts1) == audit_digest(a_ts2)


def test_wav_content_hash_skips_peak_timestamp(tmp_path):
    """★ WAV **内容**哈希跳过 PEAK 时间戳：同一份音频两次写出（跨秒）⇒ 同源哈希。

    根因（2026-10-27 演示脚本抓到）：FLOAT WAV 的 PEAK chunk 带 time_t，原始字节
    哈希跨秒必不同 ⇒ "同一修复跨机核对"会假失败。
    """
    from audiocheck.core.edit.render import _sha256_file
    x = (0.4 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS))[:, None]
    p1, p2 = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    sf.write(p1, x.astype(np.float32), FS, subtype="FLOAT")
    sf.write(p2, x.astype(np.float32), FS, subtype="FLOAT")
    # 内容哈希一致；原始字节哈希可能不同（PEAK 时间戳）——两档口径都要在
    assert _sha256_file(p1, content_only=True) == _sha256_file(p2, content_only=True)
    # 非 WAV 或未开 content_only ⇒ 整文件哈希（口径明确，不是"总是跳过"）
    assert _sha256_file(p1) == _sha256_file(p1)


def test_source_hash_is_content_addressed(tmp_path):
    """★ 审计的 `source_sha256` 是**内容**哈希：同内容不同文件 ⇒ 同源哈希，
    但 `dest_sha256` 仍是产物字节哈希（两档口径各司其职）。"""
    from audiocheck.core.edit import GainOp, Project, render
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS))[:, None]
    s1, s2 = str(tmp_path / "s1.wav"), str(tmp_path / "s2.wav")
    sf.write(s1, x.astype(np.float32), FS, subtype="FLOAT")
    sf.write(s2, x.astype(np.float32), FS, subtype="FLOAT")
    a1 = render(Project(s1, [GainOp(0.0, 1.0, -2.0)]), str(tmp_path / "o1.wav"))
    a2 = render(Project(s2, [GainOp(0.0, 1.0, -2.0)]), str(tmp_path / "o2.wav"))
    assert a1["source_sha256"] == a2["source_sha256"], "同内容源 ⇒ 同源哈希"
    assert a1["audit_digest"] == a2["audit_digest"], "⇒ 同审计摘要"


# ------------------------------------------------- 同域多算子：重叠改为顺序串联（v1.1）
def test_overlapping_same_domain_ops_chain_in_stack_order(tmp_path):
    """★ **同域重叠不再拒绝**：两个 DeclickOp 重叠 ⇒ 按栈顺序串联（后者作用在前者结果上）。

    此前实现直接拒绝（"先改哪段没有定义"）。现在有定义：**操作栈的顺序就是语义**——
    与混域链式渲染同一条原则。判据：重叠两算子的结果 == 逐个串行渲染的中间产物
    （1-ULP float32 容差，链式中间文件是 FLOAT）。
    """
    import numpy as np
    x = (0.4 * np.sin(2 * np.pi * 440 * np.arange(2 * FS) / FS))[:, None].copy()
    for pos in (0.5, 1.0, 1.5):
        x[int(pos * FS), 0] += 0.9
    src = str(tmp_path / "a.wav")
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")

    ops = [DeclickOp(0.0, 1.2), DeclickOp(0.8, 2.0)]      # 重叠 [0.8, 1.2)
    d1 = str(tmp_path / "o1.wav")
    audit = render(Project(src, list(ops)), d1)
    assert audit.get("chained") is True, "同域多算子应走链式路径"
    assert len(audit["stages"]) == 2

    # 串行等价：先渲第一个，再把结果当源渲第二个
    mid = str(tmp_path / "mid.wav")
    render(Project(src, [ops[0]]), mid)
    d2 = str(tmp_path / "o2.wav")
    render(Project(mid, [ops[1]]), d2)
    a, _ = sf.read(d1, dtype="float64", always_2d=True)
    b, _ = sf.read(d2, dtype="float64", always_2d=True)
    tol = np.finfo(np.float32).eps * 8.0 * float(np.abs(a).max() + 1e-12)
    assert float(np.abs(a - b).max()) <= tol, float(np.abs(a - b).max())


def test_non_overlapping_same_domain_still_single_pass(tmp_path):
    """不重叠的同域多算子**仍可**走单趟路径（性能不变）——不强制链式。"""
    x = (0.3 * np.sin(2 * np.pi * 440 * np.arange(2 * FS) / FS))[:, None].copy()
    src = str(tmp_path / "a.wav")
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")
    audit = render(Project(src, [DeclickOp(0.0, 0.4), DeclickOp(1.0, 1.4)]),
                   str(tmp_path / "o.wav"))
    assert not audit.get("chained"), "不重叠应保持单趟"


def test_audit_digest_recomputable_for_chained_render(tmp_path):
    """★ **链式渲染**（`_render_chained`）的摘要也必须可复算。

    链式路径有自己的一套收尾：它拿**最后一段**的审计当 `final`，
    然后往里并 `ops`/`stages`/`chained`/`source`/`reordered_from`。
    这些字段在最后一段算摘要时**都还不存在** ⇒ 若不在并完之后重算，
    记录与摘要就永久不对应。

    实测症状：`--verify-audit` 对**原样未改**的链式审计报"不一致"
    （22 个参与字段 vs 摘要当时只覆盖 18 个）。这条判据钉住"并完必须重算"。
    """
    import numpy as np

    from audiocheck.core.edit import DeclickOp, DeclipOp, DehumOp, Project, render
    from audiocheck.core.edit.render import audit_digest

    src = str(tmp_path / "a.wav")
    t = np.arange(FS) / FS
    x = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.15 * np.sin(2 * np.pi * 50 * t)
    x = np.clip(x, -0.7, 0.7)
    for pos in (0.2, 0.5):
        x[int(pos * FS)] += 0.5
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")

    audit = render(Project(src, [DehumOp(0.1, 0.9, 50.0),
                                 DeclipOp(0.1, 0.9, 21000.0),
                                 DeclickOp(0.1, 0.9)]),
                   str(tmp_path / "o.wav"), overwrite=True)
    assert audit.get("chained") is True, "三个不同算子应走链式路径"
    assert audit_digest(audit) == audit["audit_digest"], (
        "链式审计的摘要必须覆盖 `ops`/`stages`/`chained` 等链级字段")


def test_audit_digest_recomputable_for_band_render(tmp_path):
    """★ **时频路径**的摘要也必须可复算（`algorithm`/`n_fft`/`roundtrip_*` 在其后追加）。"""
    import numpy as np

    from audiocheck.core.edit import BandGainOp, Project, render
    from audiocheck.core.edit.render import audit_digest

    src = str(tmp_path / "b.wav")
    x = (0.4 * np.sin(2 * np.pi * 440 * np.arange(FS) / FS)).astype(np.float32)
    sf.write(src, x[:, None], FS, subtype="FLOAT")
    audit = render(Project(src, [BandGainOp(0.1, 0.5, 1000.0, 4000.0, -6.0)]),
                   str(tmp_path / "bo.wav"), overwrite=True)
    assert "n_fft" in audit and "roundtrip_floor_dbfs" in audit
    assert audit_digest(audit) == audit["audit_digest"], (
        "时频审计的摘要必须覆盖 `n_fft`/`roundtrip_floor_*` 等后续追加字段")


def test_audit_digest_recomputable_for_each_domain(tmp_path):
    """每个 domain 的审计都要能复算（防止某个分支把字段加在摘要之后）。"""
    import numpy as np

    from audiocheck.core.edit import (
        BandGainOp,
        DeclickOp,
        DeclipOp,
        DehumOp,
        DenoiseOp,
        GainOp,
        Project,
        render,
    )
    from audiocheck.core.edit.render import audit_digest

    src = str(tmp_path / "s.wav")
    t = np.arange(FS) / FS
    x = (0.4 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    sf.write(src, x[:, None], FS, subtype="FLOAT")

    cases = [
        ("time", [GainOp(0.1, 0.5, -3.0)]),
        ("band", [BandGainOp(0.1, 0.5, 1000.0, 4000.0, -6.0)]),
        ("declip", [DeclipOp(0.1, 0.5, 21000.0)]),
        ("declick", [DeclickOp(0.1, 0.5)]),
        ("dehum", [DehumOp(0.1, 0.5, 440.0)]),
        # 噪声段需 ≥1 个窗长（4096/48k ≈ 0.085 s）
        ("denoise", [DenoiseOp(0.3, 0.9, 0.0, 0.2)]),
    ]
    bad = []
    for name, ops in cases:
        try:
            a = render(Project(src, ops), str(tmp_path / ("o_%s.wav" % name)),
                       overwrite=True)
        except Exception as e:                                 # noqa: BLE001
            bad.append("%s：渲染失败 %s" % (name, e))
            continue
        if audit_digest(a) != a["audit_digest"]:
            bad.append("%s：摘要不可复算" % name)
    assert not bad, "以下 domain 的审计摘要不可复算：\n  " + "\n  ".join(bad)
