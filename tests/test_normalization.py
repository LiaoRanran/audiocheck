"""归一化目标诊断：验证增益推算与告警阈值。

不测绝对值（那取决于平台策略），测的是**可手算的增益关系**：
gain = 平台目标 − 实测 LUFS。
"""
from __future__ import annotations

from audiocheck.core.analyzers.normalization import NormalizationAnalyzer


class _Ctx:
    def __init__(self, result):
        self.result = result


def _by_platform(r):
    return {x["platform"]: x for x in r["norm_targets"]}


def test_gain_is_target_minus_measured():
    r = NormalizationAnalyzer().run(_Ctx({"lufs": -14.0}))
    g = _by_platform(r)
    assert g["Spotify"]["gain_db"] == 0.0, "正好等于 Spotify 目标时不应调整"
    assert g["EBU R128 广播"]["gain_db"] == -9.0, "−23 − (−14) = −9 dB"
    assert g["Apple Music"]["gain_db"] == -2.0


def test_over_loud_file_flagged():
    r = NormalizationAnalyzer().run(_Ctx({"lufs": -14.0}))
    assert r["norm_over_loud"] is True
    assert r["norm_max_atten_db"] == 9.0
    assert "EBU R128" in r["norm_note"]


def test_quiet_file_not_flagged():
    r = NormalizationAnalyzer().run(_Ctx({"lufs": -23.0}))
    # 全部平台都只需**提升**，不需要衰减
    assert r["norm_over_loud"] is False
    assert r["norm_max_atten_db"] == 0.0


def test_missing_lufs_returns_empty():
    assert NormalizationAnalyzer().run(_Ctx({})) == {}


def test_extremely_loud_file():
    """−7 LUFS（响度战典型值）在 Spotify 上要被压 7 dB。"""
    r = NormalizationAnalyzer().run(_Ctx({"lufs": -7.0}))
    g = _by_platform(r)
    assert g["Spotify"]["gain_db"] == -7.0
    assert g["EBU R128 广播"]["gain_db"] == -16.0
    assert r["norm_max_atten_db"] == 16.0
