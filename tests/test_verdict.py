"""判定规则 —— 表驱动测试，覆盖两条核心设计：

  1. 「同一截止点在不同容器里含义不同」
  2. 「保真」与「可用性」是两件事，判定必须多轴

早期版本只有一个带宽轴定等级、其余维度降级为"标签"，
于是削波严重/动态压平/单声道的文件照样能拿 ok。
"""
from __future__ import annotations

import pytest

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.verdict import (
    advice,
    axis_bandwidth,
    axis_bitdepth,
    axis_channels,
    axis_dynamics,
    axis_level,
    cross_checks,
)

#: 判定函数的阈值参数（C0 收编后它们都要求显式传 `th`）。
#: **用默认 Thresholds**：这些用例验的是"规则本身"，不是"某组调过的阈值"。
_TH = Thresholds()

TH = Thresholds()


# ============================================================ 轴一：带宽
@pytest.mark.parametrize("fs,cutoff,expect_level,keyword", [
    # 44.1k 容器（砖墙陡 = 上游低通的硬证据）
    (44100, 22000, "ok", "CD 级满带宽"),
    (44100, 19500, "warn", "轻微带限"),
    (44100, 17000, "bad", "有损溯源"),
    (44100, 12000, "bad", "强有损"),
    # 48k 容器：21.4k 在这里是"疑似升频"，而不是"接近满带宽"
    (48000, 23500, "ok", "真 48kHz"),
    (48000, 21400, "bad", "升频"),
    (48000, 19600, "warn", "带宽偏窄"),
    (48000, 17000, "bad", "有损溯源"),
    # >48k 容器
    (96000, 19000, "bad", "伪 Hi-Res"),
    (96000, 44000, "ok", "真 Hi-Res"),
    (96000, 25000, "warn", "半 Hi-Res"),
    # 无内容
    (44100, None, "warn", "无法判定"),
])
def test_axis_bandwidth_table(fs, cutoff, expect_level, keyword):
    level, text, _conf = axis_bandwidth(fs, cutoff, 113.0, TH)
    assert level == expect_level
    assert keyword in text, "期望包含 %r，实得 %r" % (keyword, text)


def test_same_cutoff_differs_by_container():
    """21.4kHz：在 44.1k 容器里是"轻微带限"，在 48k 容器里是"疑似升频"。"""
    _, a, _ = axis_bandwidth(44100, 21400, 113.0, TH)
    _, b, _ = axis_bandwidth(48000, 21400, 113.0, TH)
    assert "升频" in b, "48k 容器的 21.4kHz 应判为升频，实得 %r" % b
    assert "升频" not in a, "44.1k 容器的 21.4kHz 不该判为升频，实得 %r" % a


def test_brickwall_steepness_changes_wording_and_confidence():
    """**同一截止点，砖墙陡缓要给不同强度的结论。**

    陡断崖（≥24 dB/oct）是"上游做了低通"的硬证据；平缓滚降可能
    只是母带/录音链本身的频响，不能当成转码的硬结论。
    """
    _, hard, conf_h = axis_bandwidth(44100, 19000, 113.0, TH)
    _, soft, conf_s = axis_bandwidth(44100, 19000, 5.0, TH)
    assert "砖墙" in hard and "陡" in hard
    assert "滚降平缓" in soft
    assert conf_h > conf_s, "陡砖墙的证据强度应更高：%.2f vs %.2f" % (conf_h, conf_s)


def test_confidence_is_none_without_brickwall_data():
    """没有砖墙数据时不能编造证据强度。"""
    _, _, conf = axis_bandwidth(44100, 19000, None, TH)
    assert conf is None


def test_confidence_is_normalized_slope_position():
    """★ conf 的**统计定义**（theory.md §11.2）：砖墙陡度在证据区间
    [conf_slope_lo, lo+span] 上的归一化位置——**不是概率**。

    端点与中点钉死：8dB/oct ⇒ 0（自然滚降，无证据）、24 ⇒ 1（硬砖墙，
    满证据）、16 ⇒ 0.5。区间外截断（≤8 恒 0，≥24 恒 1）。
    写"概率/置信度%"的文案是**越界**——p(有损|谱) 需要先验，本项目没有。
    """
    lo, span = TH.conf_slope_lo, TH.conf_slope_span
    for slope, want in ((4.0, 0.0), (lo, 0.0), (lo + span / 2, 0.5),
                        (lo + span, 1.0), (60.0, 1.0)):
        _, _, conf = axis_bandwidth(44100, 17000, slope, TH)
        assert abs(conf - want) < 1e-9, "slope=%.1f ⇒ conf=%.3f，期望 %.1f" % (slope, conf, want)
    # 单调：陡度越大证据越强（这条是方向的，端点是值的）
    prev = -1.0
    for slope in (8.0, 12.0, 16.0, 20.0, 24.0, 40.0):
        _, _, conf = axis_bandwidth(44100, 17000, slope, TH)
        assert conf >= prev, "conf 非单调：%.1f ⇒ %.3f" % (slope, conf)
        prev = conf


def test_strength_wording_never_claims_probability():
    """强度文案纪律：成因候选用"强度"，**不许**写"概率/置信度"（§11.2）。"""
    import inspect

    from audiocheck.core.analyzers import causes
    for name in ("cause_candidates", "usability_matrix"):
        src = inspect.getsource(getattr(causes, name))
        assert "概率" not in src and "置信度" not in src, "%s 的文案越界了" % name


def test_thresholds_are_overridable():
    th = Thresholds(cd_full=18.0, ltd=15.0)
    level, text, _ = axis_bandwidth(44100, 19000, 113.0, th)
    assert level == "ok", "放宽阈值后 19k 应算满带宽，实得 %s / %s" % (level, text)


# ============================================================ 轴二：位深
def test_bitdepth_flags_padded_container():
    """24bit 容器装 16bit 内容 → 必须判出来（早期完全没进判定）。"""
    r = {"bits": "PCM_24", "bit_padded": True, "bit_origin": 16,
         "bit_note": "样本最低 8 位恒为零"}
    lv, txt = axis_bitdepth(r, TH)
    assert lv == "warn"
    assert "伪位深" in txt and "16" in txt


def test_bitdepth_ok_for_honest_container():
    assert axis_bitdepth({"bits": "PCM_16", "bit_effective": 16}, TH) == ("ok", "")


def test_bitdepth_flags_ineffective_low_bits():
    lv, txt = axis_bitdepth({"bits": "PCM_24", "bit_effective": 18}, TH)
    assert lv == "warn" and "有效位深" in txt


def test_bitdepth_honest_24bit_passes():
    assert axis_bitdepth({"bits": "PCM_24", "bit_effective": 23}, TH)[0] == "ok"


# ============================================================ 轴三：电平
def test_level_flags_severe_clipping_as_bad():
    lv, txt = axis_level({"clip_pct": 1.2}, TH)
    assert lv == "bad" and "削波严重" in txt


def test_intersample_overload_is_advisory_not_a_downgrade():
    """**采样间过载只提示、不降级。**

    它与削波性质不同：削波是样本被截平、信息真的丢了（文件的问题）；
    dBTP>0 是样本都好好的、DAC 重建时过冲（播放链的问题）。
    而且现代母带里极常见，拿它降级会让大半个曲库都"不达标"。

    早先把它当降级项，结果**所有合成回归用例**（满刻度正弦恒有 +0.48 dBTP）
    从 ok 变 warn —— 它们本来没问题。这条测试钉住修正后的行为。
    """
    lv, txt = axis_level({"clip_pct": 0.0, "true_peak_dbfs": 1.2}, TH)
    assert lv == "ok", "采样间过载不该降级，实得 %s" % lv
    assert "采样间过载" in txt, "仍应提示出来"
    assert "播放链" in txt, "并说明它属于播放链而非文件问题"


def test_real_clipping_still_downgrades():
    """真削波仍然降级——它确实是文件的问题。"""
    assert axis_level({"clip_pct": 0.05}, TH)[0] == "warn"
    assert axis_level({"clip_pct": 1.2}, TH)[0] == "bad"


def test_level_clean_signal_is_ok():
    assert axis_level({"clip_pct": 0.0, "true_peak_dbfs": -1.0}, TH)[0] == "ok"


# ============================================================ 轴四：动态
def test_dynamics_flags_loudness_war():
    lv, txt = axis_dynamics({"lra": 1.7, "dr_db": 5.0, "lufs": -7.4}, TH)
    assert lv == "warn"
    assert "动态压平" in txt and "响度战" in txt


def test_dynamics_good_master_is_ok():
    assert axis_dynamics({"lra": 12.0, "dr_db": 14.0, "lufs": -16.0}, TH)[0] == "ok"


# ============================================================ 轴五：声道
def test_channels_flags_near_mono():
    # width_narrow_db = -18 dB（这是个负值阈值：越负越窄）
    lv, txt = axis_channels({"width_db": -25.0}, TH)
    assert lv == "warn" and "近乎单声道" in txt


def test_channels_does_not_drive_level():
    """**可用性轴不得驱动等级。**

    单声道、LRA=0 的合成测试信号曾被判成 warn —— 那不是文件不达标，
    是它的用途有限。等级必须只由保真轴（带宽/位深/削波）决定。
    """
    import inspect

    from audiocheck.core.analyzers.verdict import VerdictAnalyzer
    src = inspect.getsource(VerdictAnalyzer.run)
    assert "drives" in src, "聚合必须区分保真轴与可用性轴"
    for line in src.splitlines():
        if "level = _worst" in line:
            assert "if drives" in line, "_worst 只能聚合 drives=True 的轴，实得：%s" % line


def test_channels_wide_is_ok():
    assert axis_channels({"width_db": -3.0}, TH)[0] == "ok"


def test_channels_warns_on_severe_anti_phase():
    """**anti_scope 接入判定**（2026-10-16）：此前只算不判。

    `dsp.anti_phase_scan` 的 docstring 写着"给判定用"，但判定层从没读过它。
    现在声道轴消费它——反相是**真实的立体声缺陷**（错线 / 反相叠加 /
    处理链极性翻转），且声道轴属**可用性轴**（不驱动等级，只提示）。
    """
    lv, txt = axis_channels({"width_db": -3.0, "anti_scope": "严重",
                             "anti_energy_pct": 99.9, "anti_band_khz": 12.5}, TH)
    assert lv == "warn" and "局部反相" in txt and "严重" in txt


def test_channels_warns_on_obvious_anti_phase():
    lv, txt = axis_channels({"width_db": -3.0, "anti_scope": "明显",
                             "anti_energy_pct": 30.0, "anti_band_khz": None}, TH)
    assert lv == "warn" and "局部反相" in txt
    assert "kHz" not in txt.split("局部反相")[1].split("）")[0] or "集中在" not in txt, \
        "band 缺失时不得显示'集中在'"


def test_channels_mild_anti_phase_does_not_warn():
    """分档门限之下（轻微/无）不告警——门限在 config.Thresholds，可被设置改。"""
    assert axis_channels({"width_db": -3.0, "anti_scope": "轻微"}, TH)[0] == "ok"
    assert axis_channels({"width_db": -3.0, "anti_scope": "无"}, TH)[0] == "ok"


def test_channels_anti_phase_thresholds_are_overridable():
    """门限收编进 config（C0 口径）：改设置必须真的改变行为。"""
    import numpy as np

    from audiocheck.core import dsp
    fs, n = 44100, 44100 * 3
    rng = np.random.default_rng(1)
    base = rng.standard_normal(n).astype(np.float32)
    # 反相能量占比被抬到刚好跨过默认"轻微"线（1%）之上：默认档是"轻微"，
    # 把 minor 门限抬到 0.5 之后就应变成"无"——证明门限真被读。
    x = np.stack([base, -base], axis=1).astype(np.float32)
    r1 = dsp.anti_phase_scan(x[:, 0], x[:, 1], fs,
                             scope_bands=(0.01, 0.10, 0.50))
    r2 = dsp.anti_phase_scan(x[:, 0], x[:, 1], fs,
                             scope_bands=(1.5, 1.6, 1.7))
    assert r1["anti_scope"] == "严重" and r2["anti_scope"] == "无", \
        "分档门限没有生效：%s vs %s" % (r1, r2)
    # 门限只改**档位文案**，不改测量本身 ⇒ 两次的能量占比必须逐位一致
    assert r1["anti_energy_pct"] == r2["anti_energy_pct"]


# ============================================================ 交叉核对
def test_cross_check_detects_bit_depth_fraud():
    r = {"bits": "PCM_24", "bit_padded": True, "bit_origin": 16}
    cc = cross_checks(r, 96000, 19000.0, _TH)
    assert any("位深造假" in x for x in cc)


def test_cross_check_detects_sample_rate_upsampling():
    """高采样率容器 + 内容止于 44.1k 系 → 采样率造假。"""
    cc = cross_checks({}, 96000, 21000.0, _TH)
    assert any("采样率造假" in x for x in cc)


def test_cross_check_warns_when_evidence_is_weak():
    """截止点低但滚降平缓 → 明确说"证据不足"，不硬下转码结论。"""
    cc = cross_checks({"brickwall_db_per_oct": 5.0}, 44100, 17000.0, _TH)
    assert any("证据不足" in x for x in cc)


def test_cross_check_detects_vendor_mismatch():
    r = {"format": "FLAC", "meta": {"vendor": "LAME 3.100"}}
    cc = cross_checks(r, 44100, 21000.0, _TH)
    assert any("编码器串可疑" in x for x in cc)


def test_cross_checks_clean_file_returns_nothing():
    r = {"format": "FLAC", "meta": {"vendor": "reference libFLAC 1.3.1"},
         "brickwall_db_per_oct": 30.0}
    assert cross_checks(r, 44100, 21500.0, _TH) == []


# ============================================================ 建议
def test_advice_mentions_hf_limit():
    txt = advice({"cutoff_hz": 19000, "bands_pct": {"bass": 70}}, TH)
    assert "高频延伸" in txt and "低频" in txt


def test_advice_flags_inaudible_dynamics():
    txt = advice({"cutoff_hz": 21000, "lra": 1.5, "lufs": -7.0}, TH)
    assert "动态" in txt


def test_advice_flags_near_mono_for_soundstage():
    txt = advice({"cutoff_hz": 21000, "width_db": -25.0}, TH)
    assert "声场" in txt


# ============================================================ 依赖声明
def test_verdict_declares_real_dependencies():
    """**requires 参与拓扑调度，必须声明真实依赖。**

    早期只写了 basic/spectrum，却读了 loudness/stereo/codec/bitdepth 的字段，
    靠 order=90 恰好最大才没出事——那是运气不是契约。
    """
    from audiocheck.core.analyzers.verdict import VerdictAnalyzer
    for dep in ("basic", "spectrum", "loudness", "stereo", "codec", "bitdepth"):
        assert dep in VerdictAnalyzer.requires, "verdict 应声明依赖 %s" % dep


# ==================================================== C0-4 三处静默降级（已修）
class TestSilentDegradationsAreFixed:
    """C0-4：**"查不到"不许与"没问题"长得一样**。

    这三条都是"修好了但很容易改回去"的形态——它们不报错、不崩，只是悄悄
    把"我不知道"说成"没问题"。所以每条都要有判据钉住。
    """

    def test_bitdepth_unmeasurable_is_not_ok(self):
        """位深**无法统计**必须报"有疑"，不能报 ok。

        分析器的 `bit_verdict` 早就区分了"不适用"与"无法统计"，判定层原先没读它。
        """
        lv, txt = axis_bitdepth({"bit_verdict": "无法统计"}, _TH)
        assert lv == "warn", "位深无法统计被判成了 %s——查不到与没问题又同形了" % lv
        assert "查不到" in txt

    def test_bitdepth_not_applicable_stays_ok(self):
        """**不适用**（浮点/32bit 整数）仍判 ok——别把"不适用"也当"查不到"。

        这条是上一条的反向保护：修"查不到"时最容易顺手把所有 None 都判 warn，
        那会让浮点文件平白降级。
        """
        lv, txt = axis_bitdepth({"bit_verdict": "不适用（浮点或 32bit 整数）"}, _TH)
        assert lv == "ok" and txt == ""

    def test_worst_treats_unknown_level_as_worst(self):
        """未知等级按**最差**处理。

        原实现 `_RANK.get(lv, 0)` 让未知等级静默取 0（=最好），
        于是"等级名写错"或"新增等级忘了登记"会让判定**悄悄偏向 ok**。
        """
        from audiocheck.core.analyzers.verdict import _worst
        assert _worst("ok", "typo_level") == "typo_level"
        assert _worst("ok", "warn", "typo_level") == "typo_level"
        # 正常等级不受影响
        assert _worst("ok", "warn") == "warn"
        assert _worst("bad", "ok") == "bad"
        assert _worst("ok", "") == "ok"        # 空串仍被跳过（不是"未知"）

    def test_summary_reports_undecided(self):
        """汇总必须**单列"未判定"**——否则浅模式跑完显示"全 0"，读起来像全都没问题。"""
        from audiocheck import summarize
        rows = [{"file": "a.wav", "path": "/x/a.wav", "level": "ok", "sr": 44100},
                {"file": "b.wav", "path": "/x/b.wav", "sr": 44100}]   # 没有 level = 未判定
        md = summarize.build(rows)
        assert "未判定" in md, "汇总没有单列未判定：\n%s" % md
