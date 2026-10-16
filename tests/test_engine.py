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


def test_natural_rolloff_is_not_flagged_as_lossy(audio):
    """回归：自然滚降（2 阶巴特沃斯）不该被打上"有损"标签。

    这里只断言 **不是 bad**，而不要求 ok：2 阶巴特沃斯@18kHz 在 44.1k 容器里
    信号延伸到 ~21kHz 后落入 16bit 量化噪底，检出约 20.5kHz → "轻微带限(warn)"
    是合理结论；但只要不落到"有损溯源(bad)"这一档，就不会冤枉正常音源。
    """
    r = analyze(audio["natural_rolloff"])
    assert r["level"] != "bad", "自然滚降被误判为有损：%s" % r["verdict"]
    assert (r.get("brickwall_db_per_oct") or 0) < 40, "缓滚降不该有砖墙特征"


def test_full_bandwidth_cd_is_not_flagged_as_band_limited(audio):
    """回归：早期算法恒定偏低 2 步，把满带宽 CD 误判成"轻微带限"。"""
    r = analyze(audio["cd_full"])
    assert r["level"] == "ok"
    assert "满带宽" in r["verdict"]
    assert r["cutoff_hz"] > 21500, "44.1k 全带宽文件的截止点应接近奈奎斯特，实得 %s" % r["cutoff_hz"]


def test_silent_material_is_not_analyzed_as_content(tmp_path):
    """★ 静音素材没有"带宽"可言——`SpectrumAnalyzer` 的静音守卫必须生效。

    守卫是 `if peak is None or peak < -60.0: return {"silent": True}`。两条都要测：
      * **极安静素材**（peak ≈ −80 dBFS）⇒ 判静音，且 `cutoff_hz` 为 `None`
        （不设这道门，相对阈值会把整段静音都当成内容）；
      * **峰值正好 0.0 dBFS 不算静音**——满刻度是"正常素材"，早期写成
        `x or -999` 会因 0.0 是假值把它误判成静音（源码注释里记着这个坑）。
    """
    import numpy as np
    import soundfile as sf

    fs = 44100
    n = int(fs * 3)
    t = np.arange(n) / fs
    loud = np.sin(2 * np.pi * 1000 * t).astype(np.float32)                    # 幅度 1.0
    quiet = (10 ** (-80 / 20) * np.sin(2 * np.pi * 1000 * t)).astype(np.float32)

    def _write(x, name):
        p = tmp_path / name
        sf.write(str(p), np.column_stack([x, x]), fs, subtype="FLOAT")
        return str(p)

    rq = analyze(_write(quiet, "quiet.wav"))
    assert rq.get("silent") is True, "极安静素材应判静音，实得 %s" % rq.get("silent")
    assert rq.get("cutoff_hz") is None

    rl = analyze(_write(loud, "loud.wav"))
    assert not rl.get("silent"), "满刻度（峰值 0.0 dBFS）素材不许被判静音"

