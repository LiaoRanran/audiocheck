"""判定规则 —— 表驱动测试，覆盖"同一截止点在不同容器里含义不同"这一核心设计。"""
from __future__ import annotations

import pytest

from audiocheck.config import Thresholds
from audiocheck.core.analyzers.verdict import advice, bandwidth_verdict, quality_tags

TH = Thresholds()


@pytest.mark.parametrize("fs,cutoff,expect_level,keyword", [
    # 44.1k 容器
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
    (96000, 44000, "ok", "真 Hi-Res"),        # ≥ 85% × 48k
    (96000, 25000, "warn", "半 Hi-Res"),      # 上端大片留空
    # 无内容
    (44100, None, "warn", "无法判定"),
])
def test_bandwidth_verdict(fs, cutoff, expect_level, keyword):
    text, level = bandwidth_verdict(fs, cutoff, TH)
    assert level == expect_level
    assert keyword in text, "期望包含 %r，实得 %r" % (keyword, text)


def test_same_cutoff_differs_by_container():
    """21.4kHz：在 44.1k 容器里是"轻微带限"，在 48k 容器里是"疑似升频"。"""
    a, _ = bandwidth_verdict(44100, 21400, TH)
    b, _ = bandwidth_verdict(48000, 21400, TH)
    assert "升频" in b, "48k 容器的 21.4kHz 应判为升频，实得 %r" % b
    assert "升频" not in a, "44.1k 容器的 21.4kHz 不该判为升频，实得 %r" % a


def test_quality_tags_detect_brickwall_and_clipping():
    r = {"brickwall_db_per_oct": 113.0, "cutoff_khz": 19.0,
         "clip_pct": 0.5, "true_peak_dbfs": 1.2, "lufs": -7.4, "lra": 1.7}
    tags = quality_tags(r, TH)
    joined = "｜".join(tags)
    assert "砖墙" in joined
    assert "削波" in joined
    assert "采样间过载" in joined
    assert "响度战" in joined
    assert "动态压平" in joined


def test_quality_tags_quiet_material_has_no_tags():
    r = {"brickwall_db_per_oct": 5.0, "cutoff_khz": 21.0, "clip_pct": 0.0,
         "true_peak_dbfs": -3.0, "lufs": -16.0, "lra": 12.0}
    assert quality_tags(r, TH) == []


def test_advice_mentions_hf_limit():
    txt = advice({"cutoff_hz": 19000, "bands_pct": {"bass": 70}}, TH)
    assert "高频延伸" in txt and "低频" in txt


def test_advice_flags_inaudiible_dynamics():
    txt = advice({"cutoff_hz": 21000, "lra": 1.5, "lufs": -7.0}, TH)
    assert "动态" in txt


def test_thresholds_are_overridable():
    th = Thresholds(cd_full=18.0, ltd=15.0)
    text, level = bandwidth_verdict(44100, 19000, th)
    assert level == "ok", "放宽阈值后 19k 应算满带宽，实得 %s / %s" % (level, text)
