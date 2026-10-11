"""引擎端到端：在"答案已知"的合成音频上验证判定链。"""
from __future__ import annotations

import json

import pytest

from audiocheck import analyze, collect_files
from audiocheck.core.engine import list_analyzers


def test_analyzers_autoregistered():
    keys = {a["key"] for a in list_analyzers()}
    assert {"basic", "spectrum", "loudness", "metadata", "verdict"} <= keys


def test_schedule_respects_dependencies():
    from audiocheck.core.registry import schedule
    order = [a.key for a in schedule()]
    assert order.index("basic") < order.index("stereo")
    assert order.index("spectrum") < order.index("verdict")
    assert order[-1] == "verdict" or order.index("verdict") > order.index("loudness")


@pytest.mark.parametrize("name,expect_level", [
    ("cd_full", "ok"),
    ("band_19k", "warn"),
    ("lossy_16k", "bad"),
    ("fake_hires", "bad"),
    ("true_hires", "ok"),
    ("up_44", "bad"),
])
def test_end_to_end_verdicts(audio, name, expect_level):
    r = analyze(audio[name])
    assert r["level"] == expect_level, "%s → %s（%s）" % (name, r["level"], r["verdict"])


def test_fake_hires_is_flagged_as_such(audio):
    r = analyze(audio["fake_hires"])
    assert r["sr"] == 96000
    assert r["cutoff_khz"] < 20.5, "96k 容器的内容应止于 19kHz 附近，实得 %s" % r["cutoff_khz"]
    assert "伪 Hi-Res" in r["verdict"]


def test_up44_is_flagged(audio):
    r = analyze(audio["up_44"])
    assert r["sr"] == 48000
    assert "升频" in r["verdict"]


def test_result_is_json_serializable(audio):
    """回归：numpy.float32 不是 float 子类，直接 dump 会 TypeError。"""
    r = analyze(audio["band_19k"])
    s = json.dumps(r, ensure_ascii=False)
    assert '"cutoff_khz"' in s


def test_result_contains_metrics_and_curve(audio):
    r = analyze(audio["cd_full"])
    assert "metrics" in r and isinstance(r["metrics"], dict)
    assert r["metrics"]["cutoff_hz"]["label"] == "真实截止"
    assert len(r.get("curve") or []) > 100


def test_shallow_mode_skips_spectrum(audio):
    r = analyze(audio["cd_full"], deep=False)
    assert r.get("cutoff_hz") is None
    assert r.get("verdict") is None
    assert r["sr"] == 44100
    assert r["clip_pct"] is not None


def test_only_runs_selected_analyzer(audio):
    r = analyze(audio["cd_full"], only=["metadata"])
    assert "meta" in r
    assert r.get("cutoff_hz") is None


def test_clipping_detected(audio):
    r = analyze(audio["clipped"])
    assert r["peak_dbfs"] > -0.5
    assert r["clip_pct"] > 0.5, "强削波素材应报出明显削波比例，实得 %s" % r["clip_pct"]


def test_collect_files_dedupes_and_sorts(tmp_path, audio):
    files = collect_files([audio["cd_full"], audio["cd_full"],
                           str(tmp_path), tmp_path / "不存在.wav"])
    assert files == [audio["cd_full"]]


def test_errors_field_present(audio):
    r = analyze(audio["tone_50"])
    assert r["errors"] == []


def test_natural_rolloff_is_not_flagged_as_cutoff(audio):
    """回归：自然滚降（巴特沃斯）不该被报成"截止"。

    没有陡度守卫时，普通音乐的高频自然衰减会被误判，进而给正常音源打上"有损"标签。
    """
    r = analyze(audio["natural_rolloff"])
    assert r["level"] == "ok", "自然滚降被误判：%s" % r["verdict"]
    assert (r.get("brickwall_db_per_oct") or 0) < 40


def test_full_bandwidth_cd_is_not_flagged_as_band_limited(audio):
    """回归：早期算法恒定偏低 2 步，把满带宽 CD 误判成"轻微带限"。"""
    r = analyze(audio["cd_full"])
    assert r["level"] == "ok"
    assert "满带宽" in r["verdict"]
    assert r["cutoff_hz"] > 21500, "44.1k 全带宽文件的截止点应接近奈奎斯特，实得 %s" % r["cutoff_hz"]
