"""编辑器 CLI 判据（P0 的用户入口）。

钉住的是**用户看得到的行为**：退出码、可读信息、**不许 traceback**、审计记录落盘、
以及 no-op 的"逐位不变"承诺在 CLI 这条路上也成立。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.edit_cli import _parse_freq, _parse_range, main  # noqa: E402

FS = 48000


def _tone(seconds: float = 2.0) -> np.ndarray:
    n = int(seconds * FS)
    t = np.arange(n) / FS
    x = 0.4 * np.sin(2 * np.pi * 440 * t)
    return np.column_stack([x, x])


def _two_tone(seconds: float = 2.0) -> np.ndarray:
    n = int(seconds * FS)
    t = np.arange(n) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.3 * np.sin(2 * np.pi * 8000 * t)
    return np.column_stack([x, x])


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _read(path) -> np.ndarray:
    a, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return a


# ------------------------------------------------------------------ 参数解析
def test_parse_range_accepts_and_rejects():
    assert _parse_range("12.5-14.0") == (12.5, 14.0)
    assert _parse_range("0-1") == (0.0, 1.0)
    for bad in ("14-12.5", "abc-1", "12.5", "12.5-", "-1-2"):
        try:
            _parse_range(bad)
        except ValueError:
            continue
        raise AssertionError("本该拒绝：%r" % bad)


def test_parse_freq_accepts_and_rejects():
    assert _parse_freq("4000-8000") == (4000.0, 8000.0)
    for bad in ("8000-4000", "abc-1", "4000", "4000-", "-1-2"):
        try:
            _parse_freq(bad)
        except ValueError:
            continue
        raise AssertionError("本该拒绝：%r" % bad)


# ------------------------------------------------------------------ 时频路径
def test_band_knocks_out_the_band(tmp_path, capsys):
    """`--band` 走时频路径：8 kHz 那条线被压掉，440 不动；并把**实测残差**打在台面上。"""
    src = _write(tmp_path / "a.wav", _two_tone())
    dst = str(tmp_path / "b.wav")
    rc = main([src, dst, "--gain", "-60", "--at", "0.2-1.8", "--band", "7000-9000"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "时频选区" in out and "改动" in out and "STFT" in out
    assert "时频编辑不是位精确的" in out, "实测往返残差必须显眼打出来（不是藏在 JSON 里）"
    assert "设定" in out and "实测" in out, "「设定 vs 实测」必须一起打（可审计的修复）"
    a, b = _read(src), _read(dst)
    k0, k1 = int(0.6 * FS), int(1.4 * FS)
    w = np.hanning(k1 - k0)

    def amp(x, f):
        return np.abs(np.fft.rfft(x[k0:k1, 0] * w)[int(round(f * (k1 - k0) / FS))])

    assert 20 * np.log10(amp(b, 8000.0) / amp(a, 8000.0)) < -55.0, "8 kHz 没被压掉"
    assert abs(20 * np.log10(amp(b, 440.0) / amp(a, 440.0))) < 0.05, "440 被动了"


def test_band_n_fft_hop_are_honoured(tmp_path, capsys):
    """`--n-fft` / `--hop` 要真的传下去（审计里报的是**实际用的**参数）。"""
    src = _write(tmp_path / "a.wav", _two_tone())
    aud = str(tmp_path / "audit.json")
    assert main([src, str(tmp_path / "b.wav"), "--gain", "-6", "--at", "0.5-1.0",
                 "--band", "7000-9000", "--n-fft", "2048", "--hop", "1024",
                 "--audit", aud]) == 0
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["domain"] == "band" and rec["n_fft"] == 2048 and rec["hop"] == 1024
    assert rec["roundtrip_floor_dbfs"] < -200.0


def test_band_op_is_refused_when_dest_is_the_source(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _two_tone())
    assert main([src, src, "--gain", "-6", "--at", "0.0-1.0", "--band", "7000-9000"]) == 2
    assert "拒绝" in capsys.readouterr().out


def test_bad_band_returns_2(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _two_tone())
    assert main([src, str(tmp_path / "b.wav"), "--gain", "-6", "--at", "0.0-1.0",
                 "--band", "9000-7000"]) == 2
    assert "参数错误" in capsys.readouterr().out


# ------------------------------------------------------------------ 正常路径
def test_gain_writes_file_and_audit(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    aud = str(tmp_path / "audit.json")
    rc = main([src, dst, "--gain", "-6", "--at", "0.5-1.0", "--audit", aud])
    assert rc == 0
    out = capsys.readouterr().out
    assert "生效 24000 帧" in out and "输出峰值" in out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["ops"][0]["applied_frames"] == 24000
    k0, k1 = 24000, 48000
    a, b = _read(src), _read(dst)
    assert np.array_equal(a[:k0], b[:k0]) and np.array_equal(a[k1:], b[k1:])
    np.testing.assert_allclose(b[k0:k1], a[k0:k1] * 10.0 ** (-6.0 / 20.0), rtol=1e-6)


def test_gain_zero_is_still_bit_exact(tmp_path):
    """`--gain 0` 走 CLI 也必须**逐位不变**（no-op 不留痕）。"""
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--gain", "0", "--at", "0.0-2.0"]) == 0
    assert np.array_equal(_read(src), _read(dst))


def test_clip_warning_is_printed(tmp_path, capsys):
    loud = np.full((int(FS * 0.5), 2), 0.9)
    src = _write(tmp_path / "a.wav", np.vstack([loud, loud]), subtype="PCM_16")
    rc = main([src, str(tmp_path / "b.wav"), "--gain", "6", "--at", "0.0-0.5",
               "--subtype", "PCM_16"])
    assert rc == 0
    assert "削波" in capsys.readouterr().out


# ------------------------------------------------------------------ 去削波路径
def _clipped(seconds: float = 1.0) -> np.ndarray:
    return corpus.clipped(corpus.default_clean(seconds, fs=FS, seed=1), "moderate")["clipped"]


def _clipped_bandlimited(seconds: float = 2.0, b: float = 18000.0) -> np.ndarray:
    """2 s、**砖墙带限**到 18 kHz 再削一刀（`--bandwidth` 自动路径的判据素材）。"""
    return corpus.clipped(corpus.band_limited_noise(seconds, fs=FS, bandwidth=b, seed=1),
                          "moderate")["clipped"]


def test_declip_cli_reports_the_measurements(tmp_path, capsys):
    """`--declip` 把**实测**摊在台面上：饱和样本数、能量比、带外变化（可审计的修复）。"""
    src = _write(tmp_path / "a.wav", _clipped())
    aud = str(tmp_path / "audit.json")
    rc = main([src, str(tmp_path / "b.wav"), "--declip", "--at", "0.0-1.0",
               "--bandwidth", "21600", "--audit", aud])
    assert rc == 0
    out = capsys.readouterr().out
    assert "去削波" in out and "饱和" in out and "能量比" in out and "带外变化" in out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["domain"] == "declip" and rec["algorithm"] == "pocs_declip"
    op = rec["ops"][0]
    assert op["ceiling_source"] == "auto_max" and op["replaced_samples"] > 0
    assert op["energy_ratio_measured_db"] > -0.5


def _smooth_clicked(seconds: float = 2.0) -> np.ndarray:
    """2 s 平滑滑音 + 三处已知咔哒（`--declick` 端到端判据的素材）。"""
    t = np.arange(int(seconds * FS)) / FS
    f = 300.0 + 450.0 * t / seconds
    ph = 2 * np.pi * np.cumsum(f) / FS
    clean = ((0.7 * np.sin(ph) + 0.25 * np.sin(ph * 1.01)) / 0.95)[:, None]
    return corpus.click_train(clean, [0.5, 1.0, 1.5], fs=FS, amp=0.9)["clicked"]


def test_declick_cli_end_to_end(tmp_path, capsys):
    """`--declick` 走 CLI：把检出数 / 阈值 / 能量比打在台面上，审计落盘且域与算法名可查。"""
    src = _write(tmp_path / "a.wav", _smooth_clicked())
    aud = str(tmp_path / "audit.json")
    assert main([src, str(tmp_path / "b.wav"), "--declick", "--at", "0.0-2.0",
                 "--audit", aud]) == 0
    out = capsys.readouterr().out
    assert "去咔哒" in out and "检出" in out and "MAD" in out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["domain"] == "declick" and rec["algorithm"] == "residual2_mad_linear"
    op = rec["ops"][0]
    assert op["threshold"] == 5.0 and op["detected_samples"] >= 3
    assert op["fraction"] <= 2e-3, "改动面过大（误报区）"


def test_declick_refuses_flags_that_are_not_its_own(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _clipped())
    assert main([src, str(tmp_path / "b.wav"), "--declick", "--at", "0.0-1.0",
                 "--ceiling", "0.5"]) == 2
    assert "参数错误" in capsys.readouterr().out


def test_declip_cli_warns_when_the_solution_is_not_unique(tmp_path, capsys):
    """欠定（`f > 1 − B/(fs/2)`）时必须**在台面上**报"解不唯一 ⇒ 加迭代也没用"。"""
    p = corpus.clipped(corpus.band_limited_noise(2.0, fs=FS, bandwidth=18000.0, seed=1), "severe")
    src = _write(tmp_path / "a.wav", p["clipped"])
    aud = str(tmp_path / "audit.json")
    assert main([src, str(tmp_path / "b.wav"), "--declip", "--at", "0.0-2.0",
                 "--bandwidth", "21600", "--audit", aud]) == 0
    assert "解不唯一" in capsys.readouterr().out
    rec = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert rec["ambiguous"] is True and rec["ambiguity_margin"] <= 0.0
    assert rec["bandwidth_source"] == "given"


def test_declip_cli_reports_the_convergence_state_when_determined(tmp_path, capsys):
    """定解时要报**还能不能动**（末步 dB / 是否已到浮点精度），而不是让用户猜。"""
    p = corpus.clipped(corpus.band_limited_noise(2.0, fs=FS, bandwidth=18000.0, seed=1), "moderate")
    src = _write(tmp_path / "a.wav", p["clipped"])
    assert main([src, str(tmp_path / "b.wav"), "--declip", "--at", "0.0-2.0",
                 "--bandwidth", "18000"]) == 0
    out = capsys.readouterr().out
    assert "可辨识性" in out and "余量" in out and "迭代末步" in out


def test_declip_auto_bandwidth_reads_the_measurement_layer(tmp_path, capsys):
    """`--bandwidth` 省略 ⇒ **自动调测量层**：砖墙素材上读出 ≈ 真实带宽，审计记下来源。"""
    src = _write(tmp_path / "a.wav", _clipped_bandlimited())          # 2 s，砖墙在 18 kHz
    aud = str(tmp_path / "audit.json")
    assert main([src, str(tmp_path / "b.wav"), "--declip", "--at", "0.0-2.0",
                 "--audit", aud]) == 0
    assert "自动＝砖墙截止" in capsys.readouterr().out
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["bandwidth_source"] == "auto_cutoff"
    assert abs(op["bandwidth"] / 18000.0 - 1.0) < 0.03, op["bandwidth"]     # 已校准的截止读数
    assert op["replaced_samples"] > 0


def test_declip_auto_bandwidth_on_full_band_degrades_to_noop(tmp_path, capsys):
    """量不到带限（满带宽素材）⇒ `B = 奈奎斯特` ⇒ 去削波**逐位不变**（如实记，不假装修）。"""
    full = corpus.clipped(corpus.band_limited_noise(2.0, fs=FS, bandwidth=FS / 2, seed=4),
                          "moderate")
    src = _write(tmp_path / "a.wav", full["clipped"])
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--declip", "--at", "0.0-2.0"]) == 0
    assert "自动＝奈奎斯特" in capsys.readouterr().out
    assert np.array_equal(_read(src), _read(dst))


def test_declip_auto_bandwidth_refuses_short_material(tmp_path, capsys):
    """素材太短（canonical 谱量不出来）⇒ **明确拒绝**，不猜一个 `B` 出来。"""
    src = _write(tmp_path / "a.wav", _clipped(0.5))                    # 0.5 s
    assert main([src, str(tmp_path / "b.wav"), "--declip", "--at", "0.0-0.5"]) == 2
    assert "太短" in capsys.readouterr().out


def test_exactly_one_mode_is_required(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _clipped())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--at", "0.0-1.0"]) == 2                  # 一种都没给
    assert main([src, dst, "--gain", "-6", "--declip", "--bandwidth", "21600",
                 "--at", "0.0-1.0"]) == 2                            # 给了两种
    assert main([src, dst, "--declip", "--bandwidth", "21600", "--band", "4000-8000",
                 "--at", "0.0-1.0"]) == 2                            # --band 只对 gain 有效
    assert "参数错误" in capsys.readouterr().out


# ------------------------------------------------------------------ 错误路径
def test_bad_range_returns_2_without_traceback(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    assert main([src, str(tmp_path / "b.wav"), "--gain", "-3", "--at", "1.0-0.5"]) == 2
    assert "参数错误" in capsys.readouterr().out


def test_dest_equals_source_is_refused(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    assert main([src, src, "--gain", "-3", "--at", "0.0-1.0"]) == 2
    assert "拒绝" in capsys.readouterr().out
    assert np.array_equal(_read(src), _read(src))       # 源没被动过


def test_existing_dest_returns_5(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    dst = _write(tmp_path / "b.wav", _tone())
    assert main([src, dst, "--gain", "-3", "--at", "0.0-1.0"]) == 5
    assert "已存在" in capsys.readouterr().out
    assert main([src, dst, "--gain", "-3", "--at", "0.0-1.0", "--overwrite"]) == 0


def test_unreadable_source_returns_4_in_a_subprocess(tmp_path):
    """真 CLI 子进程：非音频文件 → rc=4、**输出里不许有 Traceback**（E2.1 规矩）。"""
    bad = tmp_path / "not_audio.wav"
    bad.write_text("这不是音频", encoding="utf-8")
    p = subprocess.run([sys.executable, "-m", "audiocheck.edit_cli", str(bad),
                        str(tmp_path / "out.wav"), "--gain", "-3", "--at", "0-1"],
                       capture_output=True, text=True,
                       env={**os.environ, "PYTHONPATH": os.path.join(
                           os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")})
    assert p.returncode == 4, p.stderr
    assert "Traceback" not in (p.stdout + p.stderr)
    assert "源读不了" in p.stdout

# ------------------------------------------------------------------ 去嗡路径
def _hummed(seconds: float = 2.0) -> np.ndarray:
    """440 Hz 内容 + 50/100 Hz 嗡（嗡比内容低 ~24 dB）。"""
    n = int(seconds * FS)
    t = np.arange(n) / FS
    base = 0.3 * np.sin(2 * np.pi * 440 * t)
    hum = 0.02 * (np.sin(2 * np.pi * 50 * t) + 0.5 * np.sin(2 * np.pi * 100 * t))
    return np.column_stack([base + hum, base + hum])


def test_dehum_cli_end_to_end(tmp_path, capsys):
    """CLI 必须：退出码 0、打印**实测压制量**、输出里 50 Hz 真被压下去。"""
    from audiocheck.core.edit.metrics import band_gain_db

    src = _write(tmp_path / "a.wav", _hummed())
    dst = str(tmp_path / "b.wav")
    rc = main([src, dst, "--dehum", "--at", "0.0-2.0", "--hum-f0", "50"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "去嗡" in out and "实测压制" in out
    assert "整段选区都被改动" in out          # LTI 的如实口径必须打出来
    g = band_gain_db(_read(src), _read(dst), FS, 45.0, 55.0)
    assert g is not None and g <= -15.0, "50 Hz 只压了 %s dB" % g


def test_dehum_requires_the_base_frequency(tmp_path, capsys):
    """`--dehum` 不给 `--hum-f0` ⇒ 退出码 2（基频是可测输入，不自动猜）。"""
    src = _write(tmp_path / "a.wav", _hummed(0.5))
    assert main([src, str(tmp_path / "b.wav"), "--dehum", "--at", "0.0-0.5"]) == 2
    assert "--hum-f0" in capsys.readouterr().out


def test_dehum_refuses_flags_that_are_not_its_own(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _hummed(0.5))
    assert main([src, str(tmp_path / "b.wav"), "--dehum", "--at", "0.0-0.5",
                 "--hum-f0", "50", "--threshold", "4"]) == 2
    assert "不是去嗡的参数" in capsys.readouterr().out


def test_hum_flags_are_refused_without_dehum(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _hummed(0.5))
    assert main([src, str(tmp_path / "b.wav"), "--gain", "-3", "--at", "0.0-0.5",
                 "--hum-f0", "50"]) == 2


def test_dehum_harmonics_list_is_parsed_and_validated(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _hummed(0.5))
    dst = str(tmp_path / "b.wav")
    aud = str(tmp_path / "a.json")
    assert main([src, dst, "--dehum", "--at", "0.0-0.5", "--hum-f0", "50",
                 "--hum-harmonics", "1,2", "--audit", aud]) == 0
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["ops"][0]["harmonics"] == [1, 2]
    assert main([src, str(tmp_path / "c.wav"), "--dehum", "--at", "0.0-0.5",
                 "--hum-f0", "50", "--hum-harmonics", "1,x"]) == 2
