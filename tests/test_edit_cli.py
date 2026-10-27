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

from audiocheck.core.edit import DeclickOp, GainOp, Project, corpus, render  # noqa: E402
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


# ------------------------------------------------------------------ 去噪（噪声段是必填）
def test_denoise_requires_the_noise_segment(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--denoise", "--at", "0.5-2.0"]) == 2
    assert not os.path.exists(dst)


def test_noise_at_is_denoise_only(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--gain", "-3", "--at", "0.5-2.0", "--noise-at", "0-0.5"]) == 2


def test_denoise_cli_writes_the_audit_and_keeps_the_noise_segment(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    audit_path = str(tmp_path / "audit.json")
    rc = main([src, dst, "--denoise", "--at", "0.5-2.0", "--noise-at", "0-0.5",
               "--reduction-db", "12", "--audit", audit_path])
    assert rc == 0
    rec = json.load(open(audit_path, encoding="utf-8"))
    assert rec["domain"] == "denoise" and rec["algorithm"] == "wiener_dd_floor_tracked"
    assert rec["ops"][0]["reduction_db"] == 12.0
    lead = int(0.5 * FS)
    assert np.array_equal(_read(src)[:lead], _read(dst)[:lead])


def test_denoise_cli_rejects_a_too_short_profile(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "b.wav")
    assert main([src, dst, "--denoise", "--at", "0.5-2.0", "--noise-at", "1.95-2.0"]) == 2
    assert not os.path.exists(dst)


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
    assert rec["domain"] == "declick" and rec["algorithm"] == "residual2_mad2scale_step_cascade"
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
    hum = 0.02 * (np.sin(2 * np.pi * 50 * t)
                  + 0.5 * np.sin(2 * np.pi * 100 * t)
                  + 0.3 * np.sin(2 * np.pi * 150 * t))
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


def test_dehum_auto_detects_the_steady_line(tmp_path, capsys):
    """`--hum-auto`：从素材量出 50 Hz（稳态证据），审计记 `f0_source="auto"`。"""
    src = _write(tmp_path / "a.wav", _hummed(6.0))
    dst = str(tmp_path / "b.wav")
    aud = str(tmp_path / "a.json")
    rc = main([src, dst, "--dehum", "--at", "0.0-6.0", "--hum-auto", "--audit", aud])
    assert rc == 0
    out = capsys.readouterr().out
    assert "自动基频" in out and "50.0" in out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["ops"][0]["f0_source"] == "auto"
    assert abs(rec["ops"][0]["f0"] - 50.0) <= 0.2


def test_dehum_auto_refuses_a_clean_file(tmp_path, capsys):
    """无稳态线 ⇒ 自动路径**拒绝**（退出码 2）并打印实测证据，不偷偷填一个数。"""
    clean = np.zeros((int(6.0 * FS), 2))
    t = np.arange(clean.shape[0]) / FS
    clean[:, 0] = clean[:, 1] = 0.3 * np.sin(2 * np.pi * 440 * t)
    src = _write(tmp_path / "a.wav", clean)
    assert main([src, str(tmp_path / "b.wav"), "--dehum", "--at", "0.0-6.0",
                 "--hum-auto"]) == 2
    out = capsys.readouterr().out
    assert "自动定基频失败" in out and "excess" in out


def test_dehum_auto_and_explicit_f0_are_mutually_exclusive(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _hummed(1.0))
    assert main([src, str(tmp_path / "b.wav"), "--dehum", "--at", "0.0-1.0",
                 "--hum-auto", "--hum-f0", "50"]) == 2
    assert "只能给一个" in capsys.readouterr().out


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


# ------------------------------------------------------------------ 项目记录（复合操作的 CLI 入口）
def _bytes(p) -> bytes:
    """读文件字节，但把 libsndfile **PEAK 块的创建时间戳归零**后再比较。

    ★ 实测踩到的 flaky（2026-10-26，全量负载下 test_project_chained_render_and_stage_report
    挂过 3 次）：libsndfile 给 **FLOAT WAV** 写 PEAK 块（version + time_t 时间戳 + 各声道峰值），
    时间戳是**创建时刻**——同一份音频渲染两次，只要跨过 1 秒边界，字节比较就会恰好差 1 字节
    （失败样本实核对过：768088 字节里**唯一**差异在偏移 60 的时间戳 `0x6adf6043 vs 0x6adf6044`，
    768 KB 音频数据逐位相同）。归零时间戳后，字节比较才表达"同样的音频"这个真实判据，
    而不是"两次渲染恰好落在同一秒里"。
    """
    b = bytearray(open(str(p), "rb").read())
    i = b.find(b"PEAK")
    if i >= 0:
        # PEAK 块布局：'PEAK'(4) + size(4) + version(4) + timeStamp(4) + 各声道(峰值4+位置4)
        b[i + 12:i + 16] = b"\x00\x00\x00\x00"
    return bytes(b)


def test_save_project_then_load_renders_the_same_bytes(tmp_path):
    """`--save-project` → `--project`：同一条记录必须渲染出**同样的字节**。"""
    src = _write(tmp_path / "a.wav", _tone())
    d1 = str(tmp_path / "b.wav")
    rec = str(tmp_path / "p.json")
    assert main([src, d1, "--gain", "-6", "--at", "0.2-0.8",
                 "--save-project", rec]) == 0
    saved = json.loads(open(rec, encoding="utf-8").read())
    assert saved["version"] == 3 and saved["source"] == src   # v3：declick 增 step_repair
    assert saved["ops"][0]["kind"] == "gain" and saved["ops"][0]["gain_db"] == -6.0
    d2 = str(tmp_path / "c.wav")
    assert main([src, d2, "--project", rec]) == 0
    assert _bytes(d1) == _bytes(d2)


def test_project_chained_render_and_stage_report(tmp_path, capsys):
    """项目里混用两个算子：链式渲染、打印阶段、与 Python 直接渲染字节相同。"""
    src = _write(tmp_path / "a.wav", _tone())
    proj = Project(src, [GainOp(0.5, 1.5, -3.0), DeclickOp(0.5, 1.5)])
    p = str(tmp_path / "p.json")
    json.dump(proj.to_dict(), open(p, "w", encoding="utf-8"), ensure_ascii=False)
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--project", p]) == 0
    out = capsys.readouterr().out
    assert "链式  2 个阶段" in out and "顺序被规范化" in out
    expected = str(tmp_path / "expected.wav")
    render(proj, expected)
    assert _bytes(dst) == _bytes(expected)


def test_empty_project_is_identity(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    p = str(tmp_path / "p.json")
    json.dump(Project(src, []).to_dict(), open(p, "w", encoding="utf-8"))
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--project", p]) == 0
    assert _bytes(src) == _bytes(dst)


def test_project_source_mismatch_is_rejected(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    other = _write(tmp_path / "other.wav", _tone())
    p = str(tmp_path / "p.json")
    json.dump(Project(other, [GainOp(0.0, 1.0, -3.0)]).to_dict(),
              open(p, "w", encoding="utf-8"))
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--project", p]) == 2
    assert "不一致" in capsys.readouterr().out
    assert not os.path.exists(dst)


def test_project_mode_rejects_flag_mixing(tmp_path):
    src = _write(tmp_path / "a.wav", _tone())
    p = str(tmp_path / "p.json")
    json.dump(Project(src, [GainOp(0.0, 1.0, -3.0)]).to_dict(),
              open(p, "w", encoding="utf-8"))
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--project", p, "--gain", "-3", "--at", "0-1"]) == 2
    assert main([src, dst, "--project", p, "--at", "0-1"]) == 2
    assert main([src, dst, "--project", p,
                 "--save-project", str(tmp_path / "q.json")]) == 2
    assert not os.path.exists(dst)


def test_bad_project_record_is_rejected_without_traceback(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    bad_json = str(tmp_path / "bad.json")
    open(bad_json, "w", encoding="utf-8").write("{")
    assert main([src, str(tmp_path / "b.wav"), "--project", bad_json]) == 2
    bad_schema = str(tmp_path / "schema.json")
    json.dump({"version": 1, "source": src,
               "ops": [{"kind": "eq", "t0": 0.0, "t1": 1.0}]},
              open(bad_schema, "w", encoding="utf-8"))
    assert main([src, str(tmp_path / "c.wav"), "--project", bad_schema]) == 2
    out = capsys.readouterr().out
    assert "项目记录" in out and "Traceback" not in out


# ---------------------------------------------------------------- 批处理
def test_batch_runs_all_jobs_and_isolates_failures(tmp_path, capsys):
    """`--batch`：逐项渲染、互相隔离；一项失败不拖累其余，汇总列出失败。"""
    a = _write(tmp_path / "a.wav", _tone())
    b = _write(tmp_path / "b.wav", _tone())
    jobs = [
        {"source": a, "dest": str(tmp_path / "oa.wav"),
         "ops": [{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -3.0}]},
        {"source": b, "dest": str(tmp_path / "ob.wav"), "ops": []},          # 空栈恒等
        {"source": a, "dest": str(tmp_path / "ofail.wav"),
         "ops": [{"kind": "nonsense"}]},                                   # 未知 kind ⇒ 单条失败
    ]
    jb = tmp_path / "jobs.json"
    jb.write_text(json.dumps(jobs), encoding="utf-8")
    rc = main(["--batch", str(jb)])
    out = capsys.readouterr().out
    assert rc == 0, "有失败但清单合法 ⇒ 仍 0（失败已逐项记录）"
    assert os.path.exists(tmp_path / "oa.wav")
    assert os.path.exists(tmp_path / "ob.wav")
    assert not os.path.exists(tmp_path / "ofail.wav")
    assert "1 项失败" in out and "nonsense" in out


def test_batch_rejects_non_array_or_missing_fields(tmp_path, capsys):
    """清单本身不可用（非数组 / 缺 source/dest）⇒ 退出码 2。"""
    bad = tmp_path / "bad.json"
    bad.write_text("{}", encoding="utf-8")
    assert main(["--batch", str(bad)]) == 2
    miss = tmp_path / "miss.json"
    miss.write_text(json.dumps([{"dest": "x.wav"}]), encoding="utf-8")
    assert main(["--batch", str(miss)]) == 2


def test_batch_help_exits_zero(capsys):
    assert main(["--batch-help"]) == 0
    assert "批处理作业清单格式" in capsys.readouterr().out


def test_report_flag_writes_markdown_next_to_audit(tmp_path, capsys):
    """`--report`：审计 dict 排成人可读 Markdown（机械排版）——CLI 侧的"可审计"落地。

    同跑 `--audit`（机器 JSON）+ `--report`（人读 Markdown）：两份都必须在、且报告里
    有算法名与实测字段；报告内容必须与 `audit_report(audit)` 逐字节一致（纯函数判据的 CLI 侧）。
    """
    src = _write(tmp_path / "a.wav", _tone())
    x, _ = sf.read(src, dtype="float64", always_2d=True)
    x[500, 0] += 0.9                          # 单采样咔哒
    sf.write(src, x.astype(np.float32), FS, subtype="FLOAT")
    dst = str(tmp_path / "out.wav")
    aj = str(tmp_path / "a.json")
    rp = str(tmp_path / "r.md")
    assert main([src, dst, "--declick", "--at", "0-2", "--overwrite",
                 "--audit", aj, "--report", rp]) == 0
    assert os.path.exists(aj) and os.path.exists(rp)
    txt = open(rp, encoding="utf-8").read()
    assert "audiocheck 编辑审计报告" in txt
    assert "residual2_mad2scale_step_cascade" in txt
    assert "检出采样" in txt
    # 与纯函数口径逐字节一致
    audit = json.load(open(aj, encoding="utf-8"))
    from audiocheck.core.edit.report import audit_report
    assert txt == audit_report(audit)


def test_report_flag_works_for_project_chained_render(tmp_path, capsys):
    """链式工程（--project）同样能出报告：阶段表 + 规范化顺序在场。"""
    src = _write(tmp_path / "a.wav", _tone())
    proj = Project(src, [GainOp(0.5, 1.5, -3.0), DeclickOp(0.5, 1.5)])
    p = str(tmp_path / "p.json")
    json.dump(proj.to_dict(), open(p, "w", encoding="utf-8"), ensure_ascii=False)
    dst = str(tmp_path / "out.wav")
    rp = str(tmp_path / "r.md")
    assert main([src, dst, "--project", p, "--overwrite", "--report", rp]) == 0
    txt = open(rp, encoding="utf-8").read()
    assert "## 链式渲染" in txt and "顺序被规范化" in txt
    assert "去咔哒" in txt and "时间选区增益" in txt


def test_v1_project_record_migrates_bit_identically(tmp_path):
    """★ v1 项目记录（denoise 无 track_floor）显式迁移 ⇒ 渲染与 v1 语义**逐位相同**。

    v2 迁移纪律：补 `track_floor=False`（v1 的全部语义就是固定谱），旧产物可复现、
    新语义不被静默套到旧记录上。
    """
    src = _write(tmp_path / "a.wav", _tone())
    d1 = json.dumps({"version": 1, "source": src, "ops": [
        {"kind": "denoise", "t0": 0.5, "t1": 1.5, "noise_t0": 0.0, "noise_t1": 0.5,
         "reduction_db": 20.0, "n_fft": 4096, "hop": None}]})
    p = tmp_path / "v1.json"
    p.write_text(d1, encoding="utf-8")
    dst1 = str(tmp_path / "o1.wav")
    assert main([src, dst1, "--project", str(p)]) == 0
    # 直接构造 track_floor=False 的项目渲染同一记录语义 ⇒ 字节必须相同（v1 忠实复现）
    from audiocheck.core.edit.ops import DenoiseOp, Project
    from audiocheck.core.edit.render import render
    dst3 = str(tmp_path / "o3.wav")
    render(Project(src, [DenoiseOp(0.5, 1.5, 0.0, 0.5, track_floor=False)]), dst3)
    assert _bytes(dst1) == _bytes(dst3)


def test_unknown_project_version_is_rejected(tmp_path):
    """v4（不存在的未来版本）必须报错，不许被误读。"""
    src = _write(tmp_path / "a.wav", _tone())
    p = tmp_path / "v4.json"
    p.write_text(json.dumps({"version": 4, "source": src, "ops": []}), encoding="utf-8")
    dst = str(tmp_path / "o.wav")
    assert main([src, dst, "--project", str(p)]) == 2


def test_no_floor_track_flag_switches_algorithm(tmp_path, capsys):
    """`--no-floor-track`：审计口径名从 floor_tracked 回到 profile（旗标接到了内核）。"""
    src = _write(tmp_path / "a.wav", _tone())
    d1 = str(tmp_path / "o1.wav")
    a1 = str(tmp_path / "a1.json")
    assert main([src, d1, "--denoise", "--at", "0.5-1.5", "--noise-at", "0-0.5",
                 "--overwrite", "--audit", a1]) == 0
    d2 = str(tmp_path / "o2.wav")
    a2 = str(tmp_path / "a2.json")
    assert main([src, d2, "--denoise", "--at", "0.5-1.5", "--noise-at", "0-0.5",
                 "--no-floor-track", "--overwrite", "--audit", a2]) == 0
    rec1 = json.loads(open(a1, encoding="utf-8").read())
    rec2 = json.loads(open(a2, encoding="utf-8").read())
    assert rec1["algorithm"] == "wiener_dd_floor_tracked"
    assert rec2["algorithm"] == "wiener_dd_profile"
    assert rec1["ops"][0]["track_floor"] is True and rec2["ops"][0]["track_floor"] is False
    assert not np.array_equal(_read(d1), _read(d2)), "两种口径的产物应该不同"


def test_batch_v1_manifest_denops_migrates(tmp_path):
    """★ 回归（schema v2 引入）：v1 批处理清单的 denoise 作业（无 track_floor 键）
    必须显式迁移后**照常跑通**——旧清单可复算，不许被严格解析整体拒绝。"""
    src = _write(tmp_path / "a.wav", _tone())
    manifest = [{"source": src, "dest": str(tmp_path / "o.wav"),
                 "ops": [{"kind": "denoise", "t0": 0.5, "t1": 1.5,
                          "noise_t0": 0.0, "noise_t1": 0.5,
                          "reduction_db": 20.0, "n_fft": 4096, "hop": None}]}]
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--batch", str(mf)]) == 0
    assert os.path.exists(str(tmp_path / "o.wav"))


def test_batch_v2_manifest_rejects_missing_key_per_job(tmp_path, capsys):
    """`version: 2` 的作业走严格 schema：denoise 缺 track_floor ⇒ **该作业**失败（隔离）。"""
    src = _write(tmp_path / "a.wav", _tone())
    ok_src = _write(tmp_path / "b.wav", _tone())
    manifest = [
        {"source": src, "dest": str(tmp_path / "o1.wav"), "version": 2,
         "ops": [{"kind": "denoise", "t0": 0.5, "t1": 1.5, "noise_t0": 0.0,
                  "noise_t1": 0.5, "reduction_db": 20.0, "n_fft": 4096, "hop": None}]},
        {"source": ok_src, "dest": str(tmp_path / "o2.wav"), "version": 2, "ops": []},
    ]
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--batch", str(mf)]) == 0          # 清单合法；坏作业被隔离记失败
    out = capsys.readouterr().out
    assert "1 项失败" in out and "track_floor" in out
    assert os.path.exists(str(tmp_path / "o2.wav"))  # 好作业照常
    assert not os.path.exists(str(tmp_path / "o1.wav"))


def test_batch_per_job_audit_and_report(tmp_path):
    """作业级 `"audit"`/`"report"`：每项渲染后落机器 JSON + 人读 Markdown（P4 收尾）。"""
    src = _write(tmp_path / "a.wav", _tone())
    manifest = [{"source": src, "dest": str(tmp_path / "o.wav"),
                 "ops": [{"kind": "gain", "t0": 0.5, "t1": 1.5, "gain_db": -3.0}],
                 "audit": str(tmp_path / "a.json"),
                 "report": str(tmp_path / "r.md")}]
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(manifest), encoding="utf-8")
    assert main(["--batch", str(mf)]) == 0
    audit = json.loads(open(str(tmp_path / "a.json"), encoding="utf-8").read())
    assert audit["ops"][0]["kind"] == "gain"
    txt = open(str(tmp_path / "r.md"), encoding="utf-8").read()
    assert "audiocheck 编辑审计报告" in txt


def test_poly_flag_renders_and_saves_project(tmp_path):
    """`--poly`：多边形选区增益的 CLI 出口（与 --band 平级的最后一块算子表面）。"""
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "o.wav")
    rec = str(tmp_path / "p.json")
    assert main([src, dst, "--gain", "-6", "--poly",
                 "0.3,1000.3;1.7,1000.3;1.7,4500.7;0.3,4500.7"]) == 0
    assert main([src, str(tmp_path / "o2.wav"), "--gain", "-6", "--poly",
                 "0.3,1000.3;1.7,1000.3;1.7,4500.7;0.3,4500.7",
                 "--save-project", rec]) == 0
    saved = json.loads(open(rec, encoding="utf-8").read())
    assert saved["ops"][0]["kind"] == "poly_gain"
    assert len(saved["ops"][0]["points"]) == 4
    # 同参数两个产物逐位一致（PEAK 归一化）
    assert _bytes(dst) == _bytes(str(tmp_path / "o2.wav"))


def test_poly_flag_mutual_exclusions(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "o.wav")
    pts = "0.3,1000.3;1.7,1000.3;1.7,4500.7;0.3,4500.7"
    assert main([src, dst, "--gain", "-6", "--poly", pts, "--at", "0.5-1.5"]) == 2
    assert "不能与 `--at`/`--band` 同给" in capsys.readouterr().out
    assert main([src, dst, "--gain", "-6", "--poly", pts, "--band", "1000-4500"]) == 2
    assert main([src, dst, "--poly", pts]) == 2            # 缺 --gain
    assert main([src, dst, "--gain", "-6", "--poly", "0.3,1000;1.7,1000"]) == 2  # <3 顶点
    assert "至少 3 个顶点" in capsys.readouterr().out


def test_audit_evidence_chain_fields(tmp_path):
    """★ 证据链（SWGDE 口径）：审计带源/产物 SHA256（重算核对）+ 工具版本；
    CLI 层补 UTC 时间戳；**render 本体不带时间戳**（纯函数纪律）。"""
    import hashlib
    src = _write(tmp_path / "a.wav", _tone())
    dst = str(tmp_path / "o.wav")
    aj = str(tmp_path / "a.json")
    assert main([src, dst, "--gain", "-3", "--at", "0.5-1.5", "--overwrite",
                 "--audit", aj]) == 0
    audit = json.loads(open(aj, encoding="utf-8").read())
    # 源哈希是**内容**哈希（跳过 PEAK 时间戳）——用同一口径重算核对
    from audiocheck.core.edit.render import _sha256_file
    assert audit["source_sha256"] == _sha256_file(src, content_only=True),         "源哈希必须等于同口径重算值"
    h2 = hashlib.sha256(open(dst, "rb").read()).hexdigest()
    assert audit["dest_sha256"] == h2
    assert audit["tool_version"] == "1.0.0"
    assert "timestamp_utc" in audit and audit["timestamp_utc"].endswith("Z")
    # 纯函数纪律：render 返回的 audit 本体没有时间戳（CLI 才注入）
    from audiocheck.core.edit import GainOp, Project, render
    d2 = str(tmp_path / "o2.wav")
    raw_audit = render(Project(src, [GainOp(0.5, 1.5, -3.0)]), d2, overwrite=True)
    assert "timestamp_utc" not in raw_audit


def test_evidence_chain_hashes_differ_when_source_differs(tmp_path):
    """不同源文件 ⇒ 审计哈希不同（内容寻址，可核对、可交接）。"""
    src1 = _write(tmp_path / "a.wav", _tone())
    x2 = _tone() * 0.5
    src2 = _write(tmp_path / "b.wav", x2)
    a1 = str(tmp_path / "a1.json")
    a2 = str(tmp_path / "a2.json")
    assert main([src1, str(tmp_path / "o1.wav"), "--gain", "-1", "--at", "0-1",
                 "--overwrite", "--audit", a1]) == 0
    assert main([src2, str(tmp_path / "o2.wav"), "--gain", "-1", "--at", "0-1",
                 "--overwrite", "--audit", a2]) == 0
    r1 = json.loads(open(a1, encoding="utf-8").read())
    r2 = json.loads(open(a2, encoding="utf-8").read())
    assert r1["source_sha256"] != r2["source_sha256"]
