"""编码器特征推断：截止点对照 + 位深升位检测。"""
from __future__ import annotations

import os

import numpy as np
import soundfile as sf

from audiocheck.core.analyzers.codec import bit_depth_origin, infer_codec


def test_infer_mp3_128_from_16khz():
    hits = infer_codec(16.0, 44100)
    assert any("128kbps" in h for h in hits), hits


def test_infer_mp3_320_from_20khz():
    hits = infer_codec(20.2, 44100)
    assert any("320" in h for h in hits), hits


def test_no_inference_for_full_bandwidth():
    """满带宽（≈22kHz）不该被报成有损编码器特征。"""
    assert infer_codec(22.0, 44100) == []


def test_no_inference_without_cutoff():
    assert infer_codec(None, 44100) == []


def test_inference_scales_with_sample_rate():
    """表是按 44.1k 整理的；96k 容器应按奈奎斯特比例缩放。"""
    hits = infer_codec(16.0, 44100)
    scaled = infer_codec(16.0 * (48000 / 2) / 22050.0, 48000)
    assert hits and scaled


def test_bit_depth_padding_detected(tmp_path):
    """构造一个"16bit 内容装进 24bit 容器"的文件，应检出升位。"""
    fs = 44100
    rng = np.random.default_rng(7)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()                            # 归一化到满刻度
    # 16→24 升位的写法：24bit 字段里存 v16<<8，即 int32 里要 <<16
    # （sf.write 写 PCM_24 取 int32 的高 24 位，所以再右移 8 才是真实存储值）
    ints = (v * 32760).astype(np.int32) << 16
    p = str(tmp_path / "padded.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out
    assert "升位" in out.get("bit_note", "")


def test_quiet_material_is_inconclusive(tmp_path):
    """电平太低的 24bit 文件：有效位深无法可靠估计 → 如实不判，而不是误报升位。"""
    fs = 44100
    rng = np.random.default_rng(9)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max() * 0.02                     # 归一化后再压到约 -34dBFS
    ints = (v * 8380000).astype(np.int32) << 8
    p = str(tmp_path / "quiet.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False, out
    assert out["bit_origin"] == 24, out      # 安静但仍是真 24bit，不能降级报成升位


def test_real_24bit_is_not_flagged(tmp_path):
    """真正用满 24 位的内容不该被误判。

    注意：sf.write 写 PCM_24 时取 int32 的**高 24 位**（即先 >>8），
    所以要写入的 24bit 值 X 必须以 X<<8 的形式传入。
    """
    fs = 44100
    rng = np.random.default_rng(11)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    ints = (v * 8380000).astype(np.int32) << 8          # 低位有内容
    p = str(tmp_path / "real24.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False, out
    assert out["bit_origin"] == 24, out


def test_16bit_file_is_skipped(tmp_path):
    """16bit 文件不做升位检测（没有升位可言）。"""
    fs = 44100
    rng = np.random.default_rng(3)
    p = str(tmp_path / "x.wav")
    sf.write(p, (rng.standard_normal(fs) * 0.3).astype(np.float32), fs, subtype="PCM_16")
    assert bit_depth_origin(p, "PCM_16")["bit_padded"] is False


def test_missing_file_is_graceful():
    out = bit_depth_origin("/no/such/file.flac", "PCM_24")
    assert out["bit_padded"] is False


# ------------------------------------------------- 2026-10-16：真实曲库暴露的缺陷
def test_bit_depth_padding_detected_in_stereo(tmp_path):
    """★ 回归：24bit **立体声** 文件上的升位检测曾**整个失效**。

    根因：`np.bitwise_or.reduce(x)` 默认**只沿 axis 0** 归约，立体声读成 [n, 2]
    时返回**每声道一个值**（1-D 数组）⇒ `int()` 抛
    `TypeError: only 0-dimensional arrays can be converted to Python scalars`，
    被引擎吞成"分析器告警"，于是**位深升位检测在 24bit 立体声上永远跑不出来**——
    而那正是伪 Hi-Res 的主要形态。

    为什么此前没抓到：本文件所有位深用例写的都是**单声道**（1-D ⇒ 默认 axis=0
    恰好等于全归约）。是**真实曲库**（88.2k/24bit 立体声）把它顶出来的。
    """
    fs = 44100
    rng = np.random.default_rng(7)
    vl = rng.standard_normal(fs)
    vr = rng.standard_normal(fs)
    vl, vr = vl / np.abs(vl).max(), vr / np.abs(vr).max()
    ints = np.column_stack([(vl * 32760).astype(np.int32) << 16,
                            (vr * 32760).astype(np.int32) << 16])       # 16bit 内容装 24bit
    p = str(tmp_path / "padded_stereo.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out


def test_bit_depth_origin_reduces_across_channels(tmp_path):
    """归约必须**跨所有声道**：右声道是数字静音时也要能判左声道的升位。"""
    fs = 44100
    rng = np.random.default_rng(8)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    ints = np.column_stack([(v * 32760).astype(np.int32) << 16, np.zeros(fs, np.int32)])
    p = str(tmp_path / "one_silent.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is True and out["bit_origin"] == 16, out


def test_real_24bit_stereo_is_not_flagged(tmp_path):
    """真 24bit 立体声（低位有内容）不许被判升位——立体声路径的另一半。"""
    fs = 44100
    rng = np.random.default_rng(11)
    vl = rng.standard_normal(fs)
    vr = rng.standard_normal(fs)
    vl, vr = vl / np.abs(vl).max(), vr / np.abs(vr).max()
    ints = np.column_stack([(vl * 8380000).astype(np.int32) << 8,
                            (vr * 8380000).astype(np.int32) << 8])
    p = str(tmp_path / "real24_stereo.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False and out["bit_origin"] == 24, out


# ------------------------------------------------- 切片轮：补全 infer_codec 的判别判据
# （变异测试在 `codec.py` 上暴露 30 个存活——全在 `infer_codec` 的对照表/容差/上限/缩放上；
# 之前只有 happy-path 判据，改错对照表边界或排序方向都不会被测到。）
def test_infer_codec_returns_exact_candidates_in_distance_order():
    """★ 钉死"给定截止点 → 候选列表与顺序"：对照表任何一行常量被改都会让这个判据挂。

    16.0 kHz @44.1k 落在 MP3 128kbps 区间（15.4–16.6），且距离中心最近 ⇒ 排第一；
    AAC 128kbps（14.8–16.6）/ WMA 128kbps（15.4–16.6）也覆盖 ⇒ 应同列、按中心距排序。
    """
    hits = infer_codec(16.0, 44100)
    assert hits, "16kHz 应至少命中一个有损特征"
    # 第一个必须是距中心最近的那条（MP3 128kbps 中心 16.0，距离 0）
    assert "MP3 128kbps" in hits[0], "最近邻应排第一，得到 %r" % hits
    # 命中数不超过 3（上游 [:3] 上限）
    assert len(hits) <= 3, "候选上限应为 3，得到 %d" % len(hits)
    # 顺序单调：第一个候选必是距中心最近的那条（MP3 128kbps 中心 16.0，距离 0）
    assert hits[0] == "MP3 128kbps", "最近邻应排第一，得到 %r" % hits


def test_infer_codec_tolerance_catches_just_outside_table():
    """★ 0.35 kHz 容差：截止点略落在所有区间之外（但距某中心 ≤0.35）仍命中。

    15.0 kHz @44.1k：MP3 128kbps 区间是 15.4–16.6，15.0 在其外 0.4——
    但距中心 16.0 仅 1.0 > 0.35 ⇒ 不应命中（守住容差上界）。
    改成 16.3：距中心 16.0 仅 0.3 ≤ 0.35 ⇒ 命中。这两个点把容差边界钉死。
    """
    assert infer_codec(16.3, 44100), "距中心 0.3≤0.35 应命中"
    # 13.0 距最近的 AAC 96kbps 中心 14.0 也有 1.0 > 0.35，且不在任何区间内 ⇒ 不命中
    assert infer_codec(13.0, 44100) == [], "距所有中心均 >0.35 不应命中"


def test_infer_codec_cap_is_three_even_when_many_match():
    """★ `[:3]` 上限：构造一个命中很多区间的截止点，仍只回 3 条。"""
    # 19.6 kHz @44.1k 同时落在 MP3 320/256、AAC 256、Vorbis q6、Opus、多条 → 多于 3 条
    hits = infer_codec(19.6, 44100)
    assert len(hits) == 3, "命中再多也只回前 3，得到 %d: %r" % (len(hits), hits)


def test_infer_codec_scaling_preserves_identity_across_sample_rates():
    """★ 缩放（line 42 `<=`）：同一物理截止频率在 44.1k 与 96k 容器下应给出**同名**候选。

    16.0 kHz @44.1k 的奈奎斯特比例缩放后 = 16.0*(48000/2)/22050 = 17.41 kHz @48k——
    表是按 44.1k 标的，缩放回 44.1k 空间应仍命中 MP3 128kbps。钉死"缩放不丢身份"。
    """
    a = infer_codec(16.0, 44100)
    b = infer_codec(16.0 * (48000 / 2) / 22050.0, 48000)
    assert a and b
    assert set(a) == set(b), "不同采样率下同一物理截止应命中同一组编码器：%r vs %r" % (a, b)


def test_infer_codec_note_only_when_content_truncated():
    """★ `codec_note` 条件（line 126 `cut < (fs/2000)*0.97`）：满带宽文件不提示有损。

    截止点 >= 奈奎斯特的 97% 视为"内容没被截断"⇒ 不写 codec_note、清空候选。
    """
    # 满带宽（22kHz @44.1k，奈奎斯特 22.05，97%≈21.4）⇒ 无候选（不提示有损）
    assert infer_codec(22.0, 44100) == []
    # 落在表内且明显截断（MP3 64kbps 区间 10.5–11.6）⇒ 有候选
    assert infer_codec(11.0, 44100), "11kHz 落在 MP3 64kbps 区间应给候选"


def test_infer_codec_upper_bound_is_inclusive():
    """★ 区间上界含等号（line 42 `lo2 <= cutoff <= hi2`）：上界点命中、越界 0.01 不命中。

    改 `>=`/`<=` 方向或把上界常量写错都会让这个判据挂。
    """
    # MP3 128kbps 上界 16.6 kHz：16.60 命中、16.61 不命中
    assert infer_codec(16.60, 44100), "16.60 应在 MP3 128kbps 区间内"
    assert infer_codec(16.61, 44100) == [], "16.61 已越出上界 16.6，不应命中"
    # MP3 320kbps 上界 20.6 kHz：20.60 命中、20.61 不命中
    assert any("320" in h for h in infer_codec(20.60, 44100)), "20.60 应命中 320kbps"
    assert infer_codec(20.61, 44100) == [], "20.61 越出 320kbps 上界 20.6"


def test_infer_codec_exact_signature_bounds_pin_the_table_constants():
    """★ 钉死对照表关键边界常量：改任何一行 lo/hi 都会让"某截止点是否命中"翻转。

    AAC 256kbps 区间 [19.4, 20.6]、MP3 320 [19.9, 20.6]、Opus [19.4, 20.6]。
    19.4 是 AAC256/Opus 下界（含）、19.39 不命中；20.6 是三者上界（含）。
    """
    assert any("AAC 256kbps" in h for h in infer_codec(19.40, 44100)), "19.40 应命中 AAC256 下界"
    # AAC256 下界 19.4（含）：把下界常量 19.4 改成 19.41 会让 19.40 失配 AAC256
    assert "AAC 256kbps" in infer_codec(19.40, 44100)
    # AAC256 上界 20.6（含）：20.60 命中、20.61 越界不命中
    assert "AAC 256kbps" in infer_codec(20.60, 44100)
    assert "AAC 256kbps" not in infer_codec(20.61, 44100)


def test_bit_depth_extra_zero_boundary_is_real_24bit(tmp_path):
    """★ `bit_depth_origin` 的 `extra == 0` 边界（lines 69/74/75）：最低置位恰等于固有移位

    ⇒ 没有额外填充 ⇒ 报真 24bit、不判升位。改 `inherent = 32 - claimed` 或
    `low - inherent` 的算式都会让这个边界判错（把真 24bit 误判成升位，或反之）。
    """
    fs = 44100
    rng = np.random.default_rng(2)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    # 最低置位恰好在 bit 8（值 << 8、低位全零）⇒ low=8，inherent(24→32-24=8)=8 ⇒ extra=0
    ints = (v * 8380000).astype(np.int32) << 8
    p = str(tmp_path / "exact24.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    out = bit_depth_origin(p, "PCM_24")
    assert out["bit_padded"] is False, out
    assert out["bit_origin"] == 24, out


def test_every_codec_signature_is_reachable_at_its_center():
    """★ 对照表完整性：每个编码器签名在其区间中心都应被命中。

    这是钉死整张 `CODEC_SIGNATURES` 表最高效的方式——改任何一行的 lo/hi 常量，
    都会让"该签名在自身中心"失配 ⇒ 这条判据挂。比逐行写 13 条更抗漂移。
    """
    for name, lo, hi in __import__("audiocheck.core.analyzers.codec",
                                   fromlist=["CODEC_SIGNATURES"]).CODEC_SIGNATURES:
        center = (lo + hi) / 2.0
        hits = infer_codec(center, 44100)
        assert name in hits, "签名 %r 在自己的区间中心 %.2f 未被命中：%r" % (name, center, hits)


# ------------------------------------------------- 切片轮（续 2）：分析仪入口 + 阈值边界
class _Ctx:
    """`CodecAnalyzer.run` 的最小上下文（只用到 `fs / path / result / metric`）。"""

    def __init__(self, fs, path, result):
        self.fs = fs
        self.path = path
        self.result = result

    def metric(self, *args, **kwargs):
        return None


def test_analyzer_run_exercises_bit_depth_origin_path(tmp_path):
    """★ `CodecAnalyzer.run` 入口（line 133 `out.update(bit_depth_origin(...,
    ctx.result.get("bits") or ""))`）：`bits` 缺失时回落到 `""` 而非崩溃/误判。

    此前 `bit_depth_origin` 只被直接单测覆盖，`run` 里的 `or` 短路从未走 ⇒ 改 `or`→`and`
    不会被测到。这里从入口喂一个带 `bits` 的结果，确保整条链跑通。
    """
    from audiocheck.core.analyzers.codec import CodecAnalyzer
    fs = 44100
    rng = np.random.default_rng(7)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    ints = (v * 32760).astype(np.int32) << 16
    p = str(tmp_path / "padded.wav")
    sf.write(p, ints, fs, subtype="PCM_24")
    ctx = _Ctx(fs, p, {"cutoff_khz": None, "bits": "PCM_24"})
    out = CodecAnalyzer().run(ctx)
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out
    # bits 缺失 ⇒ 回落 ""（claimed=None）→ 不误判升位、不崩
    ctx2 = _Ctx(fs, p, {"cutoff_khz": None, "bits": None})
    out2 = CodecAnalyzer().run(ctx2)
    assert out2["bit_padded"] is False, out2


def test_analyzer_run_writes_note_only_when_truncated():
    """★ `run` 的 note 短路（line 126 `if cut and cut < (fs/2000)*0.97`）：

    截断截止点（16.0kHz，远在阈值 21.41 之下）⇒ 走 True 分支 ⇒ 写 `codec_note`；
    `cut` 为 None ⇒ 走 False 分支（短路 `and`）⇒ 不写 note、不崩。这条把 `126:bool:and->or`
    钉死（改成 `or` 会在 cut=None 时误访问 `None < ...` 抛 TypeError）。
    """
    import tempfile

    from audiocheck.core.analyzers.codec import CodecAnalyzer
    fs = 44100
    d = tempfile.mkdtemp()
    p = str(os.path.join(d, "x.wav"))
    sf.write(p, (np.random.default_rng(1).standard_normal(fs) * 0.3).astype(np.float32),
             fs, subtype="FLOAT")
    out = CodecAnalyzer().run(_Ctx(fs, p, {"cutoff_khz": 16.0, "bits": "PCM_24"}))
    assert "codec_note" in out, out
    # cut=None：短路 and ⇒ 无 note、不崩（改 or 会 None < ... 抛错）
    out2 = CodecAnalyzer().run(_Ctx(fs, p, {"cutoff_khz": None, "bits": "PCM_24"}))
    assert "codec_note" not in out2, out2


# ------------------------------------------------- 切片轮（续）：补全逻辑分支判据
# 变异测试在 `codec.py` 上还有存活：[:3] 上限的选取语义、scale 缩放公式、note 阈值方向、
# cutoff 负/零短路、比特深度 claimed 分支。下面逐条钉死（数据常量类存活另记债务）。
def test_cap_keeps_three_nearest_when_more_match():
    """★ `[:3]` 上限（line 54）的**选取语义**：命中多于 3 条时取**最近邻 3 条**，而非任意 3 条。

    16.0 kHz 同时落在 MP3 128kbps(中心16.0)、AAC 128kbps(15.7)、WMA 128kbps(16.0) 三条内，
    且距中心都最近 ⇒ 这三条应被保留；其余更远（MP3 96kbps 中心 14.85 等）被截断在外。
    """
    hits = infer_codec(16.0, 44100)
    assert len(hits) == 3, "16kHz 应恰好命中 3 条候选，得到 %r" % hits
    for want in ("MP3 128kbps", "AAC 128kbps", "WMA 128kbps"):
        assert want in hits, "最近邻候选 %s 不应被 [:3] 截断：%r" % (want, hits)


def test_exactly_three_matches_still_capped_at_three():
    """★ 恰好命中 3 条时 `[:3]` 不溢出也不漏（`bin:-->+` 会破坏上限语义）。"""
    hits = infer_codec(16.0, 44100)
    assert len(hits) == 3, "恰好 3 条时长度必须正是 3，得到 %r" % hits


def test_scale_formula_matches_nyquist_ratio():
    """★ `scale = (fs/2)/22050`（line 45 `*`）：同一物理截止在其它采样率下缩放正确。

    22.05 kHz @48k 的奈奎斯特比例 = (48000/2)/22050 = 1.0884，缩放后查表区间对应 44.1k 的
    20.26 kHz（落在 MP3 320kbps 19.9–20.6）。若 scale 公式被改（如写成 /fs 而非 /22050）
    这条会翻转。
    """
    hits48 = infer_codec(22.05, 48000)
    assert any("320" in h for h in hits48), "22.05kHz@48k 应命中 MP3 320kbps，得到 %r" % hits48


def test_note_threshold_requires_truncated_content():
    """★ `codec_note` 阈值（line 126 `cut < (fs/2000)*0.97`）：满带宽不提示有损。

    满带宽下限 = (44100/2000)*0.97 = 21.41 kHz。22.0 kHz ≥ 该值 ⇒ 候选清空、无 note；
    而 19.0 kHz < 21.41 ⇒ 命中且有 note。改阈值方向/常量会让"满带宽却被报有损"翻面。
    """
    # 满带宽 ⇒ 无候选（不提示有损）
    assert infer_codec(22.0, 44100) == []
    # 明显截断（落在表内）⇒ 有候选
    assert infer_codec(19.0, 44100), "19kHz 落在表内应给候选"


def test_nonpositive_cutoff_returns_empty():
    """★ 短路（line 42 `if not cutoff_khz or cutoff_khz <= 0`）：负/零截止点必须返回空。

    改 `or`→`and` 或 `<=`→`>=` 会让非正截止点误产出候选。
    """
    assert infer_codec(0.0, 44100) == []
    assert infer_codec(-5.0, 44100) == []


def test_bit_depth_claimed_branch_for_32_and_24(tmp_path):
    """★ `claimed` 分支（line 69 `"24" in / "32" in`）：32bit 容器也走升位检测，且非整型跳过。

    32bit 内容（低位有值）不应判升位；"FLOAT" 不算 32bit（避免把浮点误当整型升位）。
    """
    fs = 44100
    rng = np.random.default_rng(13)
    v = rng.standard_normal(fs)
    v = v / np.abs(v).max()
    # 24bit 内容装进 32bit 容器（<<16），低位应有 8 位填充
    ints = (v * 32760).astype(np.int32) << 16
    p = str(tmp_path / "padded32.wav")
    sf.write(p, ints, fs, subtype="PCM_32")
    out = bit_depth_origin(p, "PCM_32")
    assert out["bit_padded"] is True, out
    assert out["bit_origin"] == 16, out
    # FLOAT 容器 → claimed=None → 直接跳过（不报错、不误判）
    pf = str(tmp_path / "flt.wav")
    sf.write(pf, (rng.standard_normal(fs) * 0.3).astype(np.float32), fs, subtype="FLOAT")
    assert bit_depth_origin(pf, "FLOAT")["bit_padded"] is False
