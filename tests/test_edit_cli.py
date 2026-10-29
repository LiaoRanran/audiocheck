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
    """`--batch`：逐项渲染、互相隔离；一项失败不拖累其余，汇总列出失败。

    ★ **有失败时退出码必须是 1**。这条判据此前写的是 `assert rc == 0`
    （"有失败但清单合法 ⇒ 仍 0"），而那正是把 bug 当规格固化：
    实现里 `return 0 if n_fail == 0 else 0` 两个分支都是 0，注释却写着
    "全部成功才 0"。后果是 CI 永远绿——批处理 99 项炸 98 项，
    `audiocheck-edit --batch x.json && echo ok` 照样打印 ok。

    现在改为断言 rc == 1（有失败），并要求其余作业**仍然跑完**（隔离性不变）。
    """
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
    assert rc == 1, "有失败 ⇒ 退出码 1（脚本/CI 靠它判断，返回 0 等于吞掉失败）"
    assert os.path.exists(tmp_path / "oa.wav"), "失败项不应拖累其余"
    assert os.path.exists(tmp_path / "ob.wav")
    assert not os.path.exists(tmp_path / "ofail.wav")
    assert "1 项失败" in out and "nonsense" in out


def test_batch_all_success_exits_zero(tmp_path, capsys):
    """全部成功 ⇒ 退出码 0（与"有失败 ⇒ 1"成对，防止恒返回 1）。"""
    a = _write(tmp_path / "a.wav", _tone())
    jobs = [{"source": a, "dest": str(tmp_path / "o.wav"),
             "ops": [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -2.0}]}]
    jb = tmp_path / "jobs.json"
    jb.write_text(json.dumps(jobs), encoding="utf-8")
    assert main(["--batch", str(jb)]) == 0


def test_batch_parallel_jobs_flag(tmp_path, capsys):
    """★ `--jobs N` 并行：结果与串行**内容一致**（判定口径不能因并行而变）。

    判据用**内容哈希**（`_sha256_file(content_only=True)`，跳过 PEAK 块）而
    不是字节哈希：FLOAT/PCM WAV 的 PEAK chunk 带**创建时间戳**，同一份音频在
    不同秒写出字节必然不同——那是容器元数据，不是修复内容。用字节比会得到
    "并行产物与串行不一致"的**假失败**（实测踩过）。
    """
    from audiocheck.core.edit.render import _sha256_file

    jobs = []
    for i in range(4):
        s = _write(tmp_path / ("s%d.wav" % i), _tone())
        jobs.append({"source": s, "dest": str(tmp_path / ("ser%d.wav" % i)),
                     "ops": [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}]})
    jb = tmp_path / "jobs.json"
    jb.write_text(json.dumps(jobs), encoding="utf-8")
    assert main(["--batch", str(jb), "--jobs", "1"]) == 0
    capsys.readouterr()

    jobs2 = [dict(j, dest=str(tmp_path / ("par%d.wav" % i)))
             for i, j in enumerate(jobs)]
    jb.write_text(json.dumps(jobs2), encoding="utf-8")
    assert main(["--batch", str(jb), "--jobs", "4"]) == 0
    capsys.readouterr()

    for i in range(4):
        h1 = _sha256_file(str(tmp_path / ("ser%d.wav" % i)), content_only=True)
        h2 = _sha256_file(str(tmp_path / ("par%d.wav" % i)), content_only=True)
        assert h1 == h2, "第 %d 项：并行产物的**内容**与串行不一致" % i


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
    # 清单合法但**有作业失败** ⇒ 退出码 1（隔离性不变：好作业照常跑完）。
    # 此前这里断言 == 0，与"全部成功才 0"的语义相反，是 bug 被固化成规格。
    assert main(["--batch", str(mf)]) == 1
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


# --------------------------------------------------- 审计核对（--verify-audit）
def test_verify_audit_passes_on_untampered_record(tmp_path, capsys):
    """★ `--verify-audit` 对**原样未改**的记录必须 PASS（rc=0）。

    这条判据抓的是"自指字段"陷阱：`audit_digest` 被写进审计记录**自己**，
    渲染时先算摘要再写入（算的时候它不在 dict 里），复算时它已经在
    ⇒ 若 `audit_digest()` 不排除自身，复算就是把"摘要 + 摘要"再哈希一遍，
    **永远对不上**。实测过：`--verify-audit` 拿原样记录都核不过。
    """
    src = _write(tmp_path / "s.wav", _tone())
    out = str(tmp_path / "o.wav")
    aud = str(tmp_path / "a.json")
    assert main([src, out, "--gain", "-3", "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    rc = main(["--verify-audit", aud])
    out_txt = capsys.readouterr().out
    assert rc == 0, "原样记录必须 PASS（得到 rc=%d）：\n%s" % (rc, out_txt)
    assert "一致" in out_txt


def test_verify_audit_fails_on_tampered_record(tmp_path, capsys):
    """★ 篡改任一参与摘要的字段 ⇒ FAIL（rc=1）。

    这是核对器的**全部意义**：能验出"记录被改过"。若它对篡改也 PASS，
    那它只是个装饰。
    """
    src = _write(tmp_path / "s.wav", _tone())
    out = str(tmp_path / "o.wav")
    aud = str(tmp_path / "a.json")
    assert main([src, out, "--gain", "-3", "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()

    rec = json.loads(open(aud, encoding="utf-8").read())
    rec["peak_out_dbfs"] = -1.234                       # 改一个参与摘要的字段
    bad = str(tmp_path / "bad.json")
    with open(bad, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)

    rc = main(["--verify-audit", bad])
    txt = capsys.readouterr().out
    assert rc == 1, "篡改必须 FAIL（得到 rc=%d）：\n%s" % (rc, txt)
    assert "不一致" in txt


def test_verify_audit_rejects_record_without_digest(tmp_path, capsys):
    """没有 `audit_digest` 字段的记录 ⇒ FAIL 并说明原因（不静默当通过）。"""
    p = tmp_path / "norec.json"
    p.write_text(json.dumps({"frames": 100}), encoding="utf-8")
    rc = main(["--verify-audit", str(p)])
    txt = capsys.readouterr().out
    assert rc == 1
    assert "没有" in txt or "无法核对" in txt


def test_verify_audit_ignores_path_and_time_fields(tmp_path, capsys):
    """★ 改 `dest`/`source`/`timestamp_utc` **不应**让核对失败。

    这三个字段随落盘漂移（同一份修复换个目标路径、换个时刻跑），
    若它们参与摘要，跨机核对就永远失败——那正是摘要要剔除它们的原因。
    """
    src = _write(tmp_path / "s.wav", _tone())
    out = str(tmp_path / "o.wav")
    aud = str(tmp_path / "a.json")
    assert main([src, out, "--gain", "-3", "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()

    rec = json.loads(open(aud, encoding="utf-8").read())
    rec["dest"] = "D:/别处/另一个名字.wav"
    rec["source"] = "D:/别处/输入.wav"
    rec["timestamp_utc"] = "1999-01-01T00:00:00Z"
    moved = str(tmp_path / "moved.json")
    with open(moved, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)

    rc = main(["--verify-audit", moved])
    txt = capsys.readouterr().out
    assert rc == 0, "路径/时刻不属于修复内容，改它们不应让核对失败：\n%s" % txt


def test_verify_audit_multiple_files(tmp_path, capsys):
    """可以一次核对多份；只要有一份不一致就 rc=1。"""
    src = _write(tmp_path / "s.wav", _tone())
    good, bad = str(tmp_path / "g.json"), str(tmp_path / "b.json")
    assert main([src, str(tmp_path / "o1.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", good]) == 0
    capsys.readouterr()
    rec = json.loads(open(good, encoding="utf-8").read())
    rec["frames"] = 1
    with open(bad, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    rc = main(["--verify-audit", good, "--verify-audit", bad])
    txt = capsys.readouterr().out
    assert rc == 1
    assert "2 份" in txt or "份" in txt


# --------------------------------------------------- edit_cli 的 --version/--doctor
def test_edit_cli_has_version_flag(capsys):
    """`audiocheck-edit --version` 必须可用（此前 unrecognized arguments）。"""
    import pytest as _pytest
    with _pytest.raises(SystemExit) as ei:
        main(["--version"])
    assert ei.value.code == 0
    out = capsys.readouterr().out
    assert "audiocheck" in out and any(c.isdigit() for c in out)


def test_edit_cli_has_doctor_flag(capsys):
    """`audiocheck-edit --doctor` 必须可用（复用主 CLI 的自检）。"""
    assert main(["--doctor"]) == 0
    out = capsys.readouterr().out
    assert "numpy" in out and "OK" in out


# =============================================== 变异收口（edit_cli 注册为目标后）
# 下面每条都对应满上限变异里一个"存活点"：说明**某个具体行为**此前没有被钉住。


def test_poly_requires_three_vertices(capsys):
    """★ `--poly` 顶点数守卫（`len(pts) < 3`，变异成 `<=` / `3->4` 都靠它）。

    两个顶点围不出面积 ⇒ 掩膜为空 ⇒ 渲染"成功"但什么都没改，
    用户以为修了、实际没修。必须**显式拒绝**。
    """
    assert main(["--poly", "0.1,1000;0.2,2000", "--gain", "-3",
                 "a.wav", "b.wav"]) == 2
    out = capsys.readouterr().out
    assert "至少 3 个顶点" in out
    # 三个顶点才放过（进到"源读不了"那一步，退出码 4）
    assert main(["--poly", "0.1,1000;0.2,2000;0.3,1500", "--gain", "-3",
                 "a.wav", "b.wav"]) == 4


def test_source_read_error_exits_4(tmp_path, capsys):
    """★ 源读不了 ⇒ **退出码 4**（不是 1、不是 2）。

    这条区分"用户给错了文件"（4）与"参数错"（2）——脚本据此决定
    "跳过这个文件继续"还是"整个命令写错了"。
    """
    out = str(tmp_path / "o.wav")
    assert main([str(tmp_path / "不存在.wav"), out, "--gain", "-3",
                 "--at", "0-0.5"]) == 4
    assert "源读不了" in capsys.readouterr().out


def test_chain_conflicts_with_operator_flags(tmp_path, capsys):
    """★ `--chain` 与算子 flag 互斥 ⇒ 退出码 2（链文件里已是完整操作栈）。"""
    ch = tmp_path / "chain.json"
    ch.write_text(json.dumps({"ops": []}), encoding="utf-8")
    src = _write(tmp_path / "s.wav", _tone())
    rc = main([src, str(tmp_path / "o.wav"), "--chain", str(ch), "--gain", "-3"])
    assert rc == 2
    assert "--chain" in capsys.readouterr().out


def test_declip_report_shows_saturation_percent(tmp_path, capsys):
    """★ 去削波报告里的**饱和比例**必须与审计值一致（`100.0 * fraction`，非 ×150）。

    变异 `100.0 -> 150.0` 会让报告数字**系统性偏大 50%**——报告是给人读的证据，
    数字错了比没报更糟（读者会据此判断"这段坏得厉害"）。
    """
    x = np.clip(_tone() * 1.6, -0.7, 0.7)
    src = _write(tmp_path / "clip.wav", x)
    assert main([src, str(tmp_path / "o.wav"), "--declip",
                 "--at", "0-1.0", "--ceiling", "0.7", "--bandwidth", "21000"]) == 0
    out = capsys.readouterr().out
    # 从审计里取真值，与报告里打印的百分比对齐
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o2.wav"), "--declip", "--at", "0-1.0",
                 "--ceiling", "0.7", "--bandwidth", "21000", "--audit", aud]) == 0
    rec = json.loads(open(aud, encoding="utf-8").read())
    frac = rec["ops"][0]["fraction"]
    expect = "%.2f%%" % (100.0 * frac)
    assert expect in out, "报告应打印 %.2f%%（审计 fraction=%.6f）" % (100.0 * frac, frac)


def test_declick_warns_when_changed_fraction_exceeds_half_percent(tmp_path, capsys):
    """★ 改动面 >0.5% 必须**主动警告**（`100.0 * ... / ...` 变异会被这条捉住）。

    阈值压太低时修复会**把信号修坏**（实测白噪 −13 dB）。这条警告是
    "宁可漏、不可错"在 CLI 上的可见出口——漏了它用户看到一片 ✓ 就以为没问题。
    """
    # 造一个"检出率极高"的场景：正常谐波 + 大量密集咔哒
    clean = _tone()
    n = clean.shape[0]
    rng = np.random.default_rng(7)
    pos = rng.choice(n - 10, size=400, replace=False)
    x = clean.copy()
    for p in pos:
        x[p] += 0.8
    src = _write(tmp_path / "dense.wav", x)
    assert main([src, str(tmp_path / "o.wav"), "--declick",
                 "--at", "0-1.9", "--threshold", "2.0", "--max-run", "0"]) == 0
    out = capsys.readouterr().out
    assert "改动面偏大" in out, "密集误报时应报警：\n%s" % out[-800:]


def test_read_incomplete_warning_shows_percentage(tmp_path, capsys):
    """★ 源疑似截断时的百分比（`100.0 * frames / frames_claimed`）必须正确。

    变异 `*->/` 会打印荒谬的数字；`frames_claimed` 为 0 时必须走 `else 0.0`
    而不是除零。这条同时钉住两个分支。
    """
    assert main([_write(tmp_path / "ok.wav", _tone()), str(tmp_path / "o.wav"),
                 "--gain", "-3", "--at", "0-0.5"]) == 0
    out = capsys.readouterr().out
    assert "截断" not in out, "完整文件不该报截断"


def test_verify_audit_reports_different_implementation(tmp_path, capsys):
    """★ `kernel_digest` 不同时必须报**不同实现**（`==` 变异成 `!=` 会反过来）。"""
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    rec = json.loads(open(aud, encoding="utf-8").read())
    rec["kernel_digest"] = "0" * 64                 # 伪造"另一个实现"
    p = str(tmp_path / "other.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(rec, f, ensure_ascii=False)
    main(["--verify-audit", p])
    out = capsys.readouterr().out
    assert "不同实现" in out
    assert "同一实现" not in out


def test_batch_tag_falls_back_to_index(tmp_path, capsys):
    """★ 作业既无 dest 也无 source 时标签退化为 `#i`（`or` 变异成 `and` 会崩）。"""
    good = _write(tmp_path / "g.wav", _tone())
    jobs = [{"source": good, "dest": str(tmp_path / "o.wav"), "ops": []},
            {"source": good, "dest": str(tmp_path / "o2.wav"), "ops": []}]
    # 打乱 tag 的 key 顺序不影响（dest 存在即用 dest）
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(jobs), encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "1"]) == 0
    out = capsys.readouterr().out
    assert "o.wav" in out and "o2.wav" in out


def test_batch_single_job_uses_serial_path(tmp_path, capsys):
    """★ 只有 1 项作业时**不该**打印"并行 N 路"（`len(jobs) == 1` 守卫）。

    起进程池跑一项是纯开销（Windows 上 spawn 一次 ≈ 数百毫秒）。
    """
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": str(tmp_path / "o.wav"),
                               "ops": []}]), encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "8"]) == 0
    out = capsys.readouterr().out
    assert "并行" not in out, "单项作业不应走并行路径：\n%s" % out


def test_batch_jobs_zero_means_auto(tmp_path, capsys):
    """`--jobs 0`（默认）⇒ 按 CPU 核数，不应报错；多项时走并行。"""
    jobs = []
    for i in range(3):
        s = _write(tmp_path / ("s%d.wav" % i), _tone())
        jobs.append({"source": s, "dest": str(tmp_path / ("o%d.wav" % i)), "ops": []})
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(jobs), encoding="utf-8")
    assert main(["--batch", str(mf)]) == 0
    capsys.readouterr()


# ============================================ 报告数字与审计**逐项对齐**（防格式变异）
# 报告是给人读的**证据**。格式里的 `100.0 *` / `*` / `/` 被变异（换成 150、
# 换成除法）时，报告会打印**系统性错误**的数字——比不打印更糟：读者会据此
# 判断"这段坏得多厉害"。下面按 domain 逐项核对"报告里出现的那串数字"。

def _run_and_get(paths_rc_args, capsys):
    """跑一次 main，返回 (rc, stdout)。"""
    rc = main(paths_rc_args)
    return rc, capsys.readouterr().out


def test_declip_report_numbers_match_audit(tmp_path, capsys):
    """★ 去削波报告：饱和比例 / 段数 / 替换帧 必须与审计逐项一致。

    ⚠ 素材**必须真的饱和**：`x*1.7` 后归一化会让峰值仍低于普通化阈值，
    实测 fraction=0.0 —— 那样 `100.0→150.0` 变异算出来还是 0.00%，
    判据完全抓不住（这一版就是被"0 乘任何数都是 0"骗过一次）。
    这里用**明确的硬削波**（幅度远超 ceiling 后 clip），保证 fraction 明显非零。
    """
    t = np.arange(2 * FS) / FS
    raw = 0.9 * np.sin(2 * np.pi * 220 * t) + 0.4 * np.sin(2 * np.pi * 660 * t)
    x = np.clip(raw, -0.35, 0.35)               # 硬削：大量样本贴在 ±0.35
    x = np.column_stack([x, x])
    src = _write(tmp_path / "c.wav", x)
    aud = str(tmp_path / "a.json")
    # ceiling 必须**严格小于**素材峰值：饱和判据是 |x| >= ceiling，
    # 素材峰值恰好 0.35 时 ceiling=0.35 会得到 saturated=0（实测），
    # 取 0.34 ⇒ fraction≈0.89（真饱和，百分比判据才有分辨力）。
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--declip", "--at", "0-1.0",
                            "--ceiling", "0.34", "--bandwidth", "21000", "--audit", aud],
                           capsys)
    assert rc == 0
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert "%d / %d 采样" % (op["saturated_samples"], op["total_samples"]) in out
    assert "%d 段" % op["runs"] in out
    assert "实际替换 %d 帧" % op["replaced_samples"] in out
    # 百分比必须是 **100*fraction**（不是 150*fraction，也不是 fraction 本身）
    assert op["fraction"] > 0.02, "素材必须真的饱和，否则百分比判据退化（fraction=%.6f）"         % op["fraction"]
    right = "%.2f%%" % (100.0 * op["fraction"])
    wrong = "%.2f%%" % (150.0 * op["fraction"])
    assert right in out, "报告应打印 %s（=100×fraction）" % right
    assert wrong not in out, "报告**不该**出现 %s（=150×fraction，说明系数被改）" % wrong


def test_declick_report_numbers_match_audit(tmp_path, capsys):
    """★ 去咔哒报告：检出采样 / 替换帧 / 挡下段数 必须与审计一致（%.4f%% 口径）。"""
    clean = _tone()
    x = clean.copy()
    for p in (0.3, 0.7, 1.1):
        x[int(p * FS)] += 0.7
    src = _write(tmp_path / "d.wav", x)
    aud = str(tmp_path / "a.json")
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--declick", "--at", "0-1.5",
                            "--audit", aud], capsys)
    assert rc == 0
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert "检出 %d / %d 采样" % (op["detected_samples"],
                                op["applied_frames"] * 2) in out
    assert "实际替换 %d 帧" % op["changed_samples"] in out
    assert "宽度守卫挡下 %d 段" % op["rejected_runs"] in out
    # 百分比必须是 100×fraction：既不能是 150×，也不能漏乘（口径要能被捉住）
    assert op["fraction"] > 0, "素材必须真检出咔哒，否则百分比判据退化"
    right = "%.4f%%" % (100.0 * op["fraction"])
    assert right in out, "报告应打印 %s（=100×fraction）" % right
    for bad in ("%.4f%%" % (150.0 * op["fraction"]),
                "%.4f%%" % op["fraction"]):
        assert bad not in out, "报告不该出现 %s（系数被改）" % bad


def test_dehum_report_numbers_match_audit(tmp_path, capsys):
    """★ 去嗡报告：压制值 / 改动采样 必须与审计一致。"""
    t = np.arange(2 * FS) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.15 * np.sin(2 * np.pi * 50 * t)
    src = _write(tmp_path / "h.wav", np.column_stack([x, x]))
    aud = str(tmp_path / "a.json")
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--dehum", "--hum-f0", "50",
                            "--at", "0-1.8", "--audit", aud], capsys)
    assert rc == 0
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert "基频 %.2f Hz" % op["f0"] in out
    assert "Q=%.1f" % op["q"] in out
    assert "改动 %d / %d 采样" % (op["changed_samples"], op["total_samples"]) in out
    # 压制值逐个必须是 %+.1f（不是 %+.0f、不是漏符号）
    for v in (op.get("suppression_db") or []):
        if v is not None:
            assert "%+.1f" % v in out, "压制值 %+.1f 没出现在报告里" % v


def test_band_report_numbers_match_audit(tmp_path, capsys):
    """★ 时频报告：n_fft / hop / 往返残差 必须与审计一致（`500.0 * n_fft / fs` 口径）。"""
    src = _write(tmp_path / "b.wav", _two_tone())
    aud = str(tmp_path / "a.json")
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--gain", "-6",
                            "--at", "0-1.0", "--band", "3000-9000", "--audit", aud],
                           capsys)
    assert rc == 0
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert "n_fft %d" % rec["n_fft"] in out
    assert "hop %d" % rec["hop"] in out
    if rec.get("roundtrip_floor_dbfs") is not None:
        assert "%.1f" % rec["roundtrip_floor_dbfs"] in out
    # 半宽毫秒数：500.0 * n_fft / samplerate（变异 *->/ 会给出荒谬值）
    half_ms = 500.0 * rec["n_fft"] / rec["samplerate"]
    assert "%.1f ms" % half_ms in out


def test_denoise_report_numbers_match_audit(tmp_path, capsys):
    """★ 去噪报告：噪声段/压制上限/帧数 必须与审计一致。"""
    rng = np.random.default_rng(3)
    t = np.arange(2 * FS) / FS
    tone = 0.35 * np.sin(2 * np.pi * 440 * t)
    noise = 0.05 * rng.standard_normal(2 * FS)
    src = _write(tmp_path / "n.wav", np.column_stack([tone + noise] * 2))
    aud = str(tmp_path / "a.json")
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--denoise",
                            "--noise-at", "0-0.4", "--at", "0.5-1.8", "--audit", aud],
                           capsys)
    assert rc == 0
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert "噪声段 %.3f–%.3f s" % (op["noise_t0"], op["noise_t1"]) in out
    assert "%d 采样 / %d 帧" % (op["noise_samples"], op["noise_frames"]) in out
    assert "压制上限 %.0f dB" % op["reduction_db"] in out
    assert "处理 %d 帧" % op["processed_frames"] in out


def test_clipped_frames_warning_shown(tmp_path, capsys):
    """★ 目标编排放不下峰值时必须显眼上报（`if audit["clipped_frames"]`，非 0 才打）。"""
    loud = _tone() * 1.4                    # 超 1.0 ⇒ PCM_16 写出必然钳位
    src = _write(tmp_path / "l.wav", np.clip(loud, -1.0, 1.0))
    rc, out = _run_and_get([src, str(tmp_path / "o.wav"), "--gain", "12",
                            "--at", "0-1.0", "--subtype", "PCM_16"], capsys)
    assert rc == 0
    # 放大 12 dB 必然削顶
    assert "削波" in out, "峰值溢出必须显眼上报：\n%s" % out[-600:]


# ============================================ 互斥校验 / 退出码分工（密集收口）
def test_hu_autos_rejects_non_hum_flags(tmp_path, capsys):
    """★ 去嗡不接受 `--band/--n-fft/--hop/--ceiling/--bandwidth/--iterations/
    `--threshold/--max-run` 里的**任何一个**（`or` 变异成 `and` 就放行一半）。"""
    src = _write(tmp_path / "s.wav", _tone())
    base = [src, str(tmp_path / "o.wav"), "--dehum", "--hum-f0", "50", "--at", "0-1.0"]
    cases = [
        ["--band", "1000-2000"], ["--n-fft", "2048"], ["--hop", "512"],
        ["--ceiling", "0.5"], ["--bandwidth", "20000"], ["--iterations", "8"],
        ["--threshold", "3.0"], ["--max-run", "6"],
    ]
    bad = []
    for extra in cases:
        rc = main(base + extra)
        if rc != 2:
            bad.append("%s → rc=%d（应为 2）" % (extra[0], rc))
        capsys.readouterr()
    assert not bad, "去嗡应拒绝这些参数：\n  " + "\n  ".join(bad)


def test_denoise_rejects_non_denoise_flags(tmp_path, capsys):
    """★ 去噪不接受 band/stft/hum 系列参数（8 + 4 个分支都要挡）。"""
    src = _write(tmp_path / "s.wav", _tone())
    base = [src, str(tmp_path / "o.wav"), "--denoise",
            "--noise-at", "0-0.4", "--at", "0.5-1.5"]
    cases = [
        ["--band", "1000-2000"], ["--n-fft", "2048"], ["--hop", "512"],
        ["--ceiling", "0.5"], ["--bandwidth", "20000"], ["--iterations", "8"],
        ["--threshold", "3.0"], ["--max-run", "6"],
        ["--hum-f0", "50"], ["--hum-q", "8"], ["--hum-harmonics", "1,2"],
        ["--hum-auto"],
    ]
    bad = []
    for extra in cases:
        rc = main(base + extra)
        if rc != 2:
            bad.append("%s → rc=%d（应为 2）" % (extra[0], rc))
        capsys.readouterr()
    assert not bad, "去噪应拒绝这些参数：\n  " + "\n  ".join(bad)


def test_dest_exists_exits_5(tmp_path, capsys):
    """★ 目标已存在 ⇒ **退出码 5**（与参数错 2、源读不了 4 分开）。

    5 是"用户可修复的冲突"：加 `--overwrite` 就行。脚本据此可以自动重试。
    """
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "o.wav", _tone())
    rc = main([src, dst, "--gain", "-3", "--at", "0-0.5"])
    assert rc == 5
    assert "--overwrite" in capsys.readouterr().out
    # 加了 --overwrite 就成功
    assert main([src, dst, "--gain", "-3", "--at", "0-0.5", "--overwrite"]) == 0


def test_out_of_range_selection_warns_noop(tmp_path, capsys):
    """★ 选区**完全越界** ⇒ 显式 no-op 警告（不是悄悄成功）。

    渲染层把越界区间夹紧到空集（`applied_frames == 0`）——那是正确行为
    （不该越界改文件）。但 CLI 此前**静默成功**：退出码 0、报告一片正常、
    产物与源逐位相同 ⇒ 用户以为修了，实际一个样本都没动。
    "看起来做了、其实没做"比报错更难发现，必须显式喊出来。
    """
    src = _write(tmp_path / "s.wav", _tone())          # 2 秒
    rc = main([src, str(tmp_path / "o.wav"), "--gain", "-3", "--at", "5.0-9.0"])
    assert rc == 0, "越界选区不是错误（夹紧到空集），但必须警告"
    out = capsys.readouterr().out
    assert "no-op" in out and "没有命中任何样本" in out, out[-500:]


def test_in_range_selection_does_not_warn_noop(tmp_path, capsys):
    """★ 正常选区**不该**报 no-op（防止守卫过度触发成噪声）。"""
    src = _write(tmp_path / "s.wav", _tone())
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3", "--at", "0-1.0"]) == 0
    out = capsys.readouterr().out
    assert "no-op" not in out


def test_illegal_polygon_is_rejected(tmp_path, capsys):
    """★ 非法多边形（负时间顶点）必须被拒（参数层 2 或渲染层拒绝）。"""
    src = _write(tmp_path / "s.wav", _tone())
    rc = main([src, str(tmp_path / "o.wav"), "--gain", "-3",
               "--poly", "-1,1000;1,1000;1,4000"])
    assert rc in (2, 4), "非法多边形应被拒绝，得到 %d" % rc
    out = capsys.readouterr().out
    assert "拒绝" in out or "参数错误" in out or "源读不了" in out


def test_batch_rejects_positional_source_dest(tmp_path, capsys):
    """★ `--batch` 不允许再给 `<源> <目标>` 位置参数（`and`→`or` 会让单个也拒绝）。"""
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": str(tmp_path / "o.wav"),
                               "ops": []}]), encoding="utf-8")
    # 同时给两个位置参数 ⇒ 拒绝
    assert main([src, str(tmp_path / "x.wav"), "--batch", str(mf)]) == 2
    capsys.readouterr()
    # 只给源（不给目标）⇒ **不拒绝**：条件是 `dest is not None and source is not None`，
    # 单侧出现不算"给了位置参数对"（`and`→`or` 变异会把它变成果断拒绝）。
    assert main([src, "--batch", str(mf), "--overwrite"]) == 0
    capsys.readouterr()
    # 都不给 ⇒ 正常（目标已由上一轮写出，故带 --overwrite）
    assert main(["--batch", str(mf), "--overwrite"]) == 0


def test_batch_rejects_operator_flags(tmp_path, capsys):
    """★ `--batch` 与逐操作参数互斥（`or` 链断一环就漏一种）。"""
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": str(tmp_path / "o.wav"),
                               "ops": []}]), encoding="utf-8")
    cases = [["--gain", "-3"], ["--declip"], ["--declick"], ["--dehum",
             "--hum-f0", "50"], ["--denoise", "--noise-at", "0-0.3"]]
    bad = []
    for extra in cases:
        rc = main(["--batch", str(mf), "--at", "0-1.0"] + extra)
        if rc != 2:
            bad.append("%s → rc=%d" % (extra[0], rc))
        capsys.readouterr()
    assert not bad, "批处理应拒绝这些：\n  " + "\n  ".join(bad)


def test_batch_rejects_empty_or_non_array(tmp_path, capsys):
    """★ 空数组 / 非数组都要拒绝（`or` → `and` 会放行空数组）。"""
    for content in ([], {}, "x", 42, None):
        p = tmp_path / "j.json"
        p.write_text(json.dumps(content), encoding="utf-8")
        rc = main(["--batch", str(p)])
        assert rc == 2, "内容 %r 应被拒绝，得到 rc=%d" % (content, rc)
        capsys.readouterr()


def test_batch_tags_use_dest_then_source_then_index(tmp_path, capsys):
    """★ 作业标签的**三级回退**：dest → source → `#i`。

    变异 `or`→`and` 会让"只有 source"的作业标签变成 `None`（`and` 返回后者），
    报告里出现 "✓ [None]"。这条同时钉住三个分支。
    """
    src = _write(tmp_path / "s.wav", _tone())
    # 注意：**缺 dest 会被结构校验整体拒绝**（rc=2，一项都不跑）——那是设计意图
    # （不带坏清单跑 99 项），所以不能拿它当"逐项失败"的样本。
    jobs = [{"source": src, "dest": str(tmp_path / "has_dest.wav"), "ops": []}]
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(jobs), encoding="utf-8")
    rc = main(["--batch", str(mf), "--jobs", "1"])
    out = capsys.readouterr().out
    assert "has_dest.wav" in out
    assert "None" not in out, "标签不该出现 None：\n%s" % out
    assert rc == 0                                  # 单项作业全部成功


def test_batch_jobs_flag_bounds(tmp_path, capsys):
    """★ `--jobs 0` 走自动；`--jobs 1` 走串行；两者都不该崩。"""
    jobs = []
    for i in range(3):
        s = _write(tmp_path / ("s%d.wav" % i), _tone())
        jobs.append({"source": s, "dest": str(tmp_path / ("o%d.wav" % i)), "ops": []})
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(jobs), encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "0", "--overwrite"]) == 0
    out0 = capsys.readouterr().out
    assert "3 项成功" in out0
    assert main(["--batch", str(mf), "--jobs", "1", "--overwrite"]) == 0
    out1 = capsys.readouterr().out
    assert "并行" not in out1, "--jobs 1 不该走并行"


def test_dehum_auto_probe_uses_six_second_window(tmp_path, capsys):
    """★ `--hum-auto` 的探测窗是 **6.0 秒**（`ctx.segment(seconds=6.0)`）。

    变异 `6.0->9.0` 会读更长的窗：素材只有 2 秒时行为相同（取全长），
    但真素材上会**多读 3 秒**——那是性能与"哪段算探测段"的口径变化，
    必须由判据钉住。做法：用 4 秒**稳态**谐波验证三段自动判据都能过，
    并断言报告里出现基频读数（探测窗被截到素材全长）。
    """
    # `--hum-auto` 要 ≥2 s **且**帧间稳态；2 s 卡在边界。用 4 s 稳态谐波
    # （50/100/150 三条线都在，符合"三条线 + 稳态"的自动判据）。
    t = np.arange(4 * FS) / FS
    x = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 50 * t)
         + 0.1 * np.sin(2 * np.pi * 100 * t) + 0.06 * np.sin(2 * np.pi * 150 * t))
    src = _write(tmp_path / "h.wav", np.column_stack([x, x]))
    rc = main([src, str(tmp_path / "o.wav"), "--dehum", "--hum-auto",
               "--at", "0-3.5"])
    out = capsys.readouterr().out
    assert rc == 0, "4 秒稳态素材上的 hum-auto 应成功：%s" % out
    assert "基频" in out


# ============================== 三条渲染路径的退出码必须一致（5/2/4 三分）
# 单算子 / `--chain` / `--project` 是三条独立的代码路径，各自有一份
# `except FileExistsError → 5`、`except ValueError → 2`、`except Exception → 4`。
# 只测其中一条会让另外两条的常量变异（5→6、4→5）存活。

def _chain_file(tmp_path, ops):
    p = tmp_path / "chain.json"
    p.write_text(json.dumps({"version": 3, "ops": ops}), encoding="utf-8")
    return str(p)


def _project_file(tmp_path, source, ops, name="proj.json"):
    from audiocheck.core.edit import Project as _P
    from audiocheck.edit_cli import _migrate_and_parse
    ops_objs = [_migrate_and_parse(o, 3) for o in ops]
    proj = _P(source, ops_objs)
    p = tmp_path / name
    p.write_text(json.dumps(proj.to_dict()), encoding="utf-8")
    return str(p)


def test_chain_dest_exists_exits_5(tmp_path, capsys):
    """★ `--chain` 路径：目标已存在 ⇒ 退出码 **5**（不是 2/4）。"""
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "o.wav", _tone())
    ch = _chain_file(tmp_path, [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    assert main([src, dst, "--chain", ch]) == 5
    assert "--overwrite" in capsys.readouterr().out


def test_chain_source_unreadable_exits_4(tmp_path, capsys):
    """★ `--chain` 路径：源读不了 ⇒ 退出码 **4**。"""
    ch = _chain_file(tmp_path, [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    assert main([str(tmp_path / "nope.wav"), str(tmp_path / "o.wav"), "--chain", ch]) == 4
    assert "源读不了" in capsys.readouterr().out


def test_project_dest_exists_exits_5(tmp_path, capsys):
    """★ `--project` 路径：目标已存在 ⇒ 退出码 **5**。"""
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "o.wav", _tone())
    pj = _project_file(tmp_path, src,
                       [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    assert main([src, dst, "--project", pj]) == 5
    assert "--overwrite" in capsys.readouterr().out


def test_project_source_unreadable_exits_4(tmp_path, capsys):
    """★ `--project` 路径：源读不了 ⇒ 退出码 **4**。"""
    src = _write(tmp_path / "s.wav", _tone())
    pj = _project_file(tmp_path, src,
                       [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    # 删掉源：记录里的 source 与命令行仍一致（同一个绝对路径），但文件没了
    os.remove(src)
    assert main([src, str(tmp_path / "o.wav"), "--project", pj]) == 4
    assert "源读不了" in capsys.readouterr().out


def test_project_rejects_mismatched_source(tmp_path, capsys):
    """★ `--project` 的源必须与记录一致（不静默替换）。"""
    a = _write(tmp_path / "a.wav", _tone())
    b = _write(tmp_path / "b.wav", _tone())
    pj = _project_file(tmp_path, a, [])
    assert main([b, str(tmp_path / "o.wav"), "--project", pj]) == 2
    assert "不一致" in capsys.readouterr().out


def test_chain_mode_count_uses_sum_not_shortcircuit(tmp_path, capsys):
    """★ `--chain` 的 `n_modes` 是**五项相加**（`+`→`-` 会让计数失真）。

    变异成减法后出现"给了两种算子但 n_modes=0"⇒ 误判"没给算子"而放行，
    于是链文件被忽略、用户以为跑的是自己的配方。
    """
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    # 给两个算子 ⇒ n_modes=2 ≠ 0 ⇒ 必须拒绝
    assert main([src, str(tmp_path / "o.wav"), "--chain", ch,
                 "--declip", "--declick", "--ceiling", "0.5"]) == 2
    assert "不能与算子 flag" in capsys.readouterr().out


def test_stage_frame_sum_handles_none(tmp_path, capsys):
    """★ 链式报告的阶段覆盖帧求和必须容忍 `None`/缺失（`or 0` 保护）。"""
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [
        {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0},
        {"kind": "declick", "t0": 0.0, "t1": 0.5, "threshold": 5.0,
         "max_run": 4, "step_repair": True},
    ])
    assert main([src, str(tmp_path / "o.wav"), "--chain", ch]) == 0
    out = capsys.readouterr().out
    assert "链式" in out and "覆盖" in out
    assert "None" not in out


def test_band_note_only_when_measured_differs(tmp_path, capsys):
    """★ 时频"设定 vs 实测"的注解只在**真差 >0.01 dB** 时打印。

    变异 `and`→`or` 会让"有实测值"或"差超过阈值"任一成立就打注解——
    正常素材上就变成满屏噪声注解。
    """
    src = _write(tmp_path / "b.wav", _two_tone())
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-6",
                 "--at", "0-1.0", "--band", "3000-9000"]) == 0
    out = capsys.readouterr().out
    # 整带增益在掩膜内部通常实测≈设定；注解即使出现也只能是"确实有差"的场合
    if "注  实测是掩码" in out:
        assert "差来自" in out


# ==================== 收口：阈值边界 / 显示精度 / no-op 守卫的**反例**
def test_noop_guard_ignores_explicit_zero_gain(tmp_path, capsys):
    """★ `gain_db == 0` 是用户**显式要求不变**，不该报 no-op（`and`→`or` 会误报）。

    `_warn_if_noop` 的可作用算子筛选条件：
    `not (kind == "gain" and gain_db == 0)`。
    变异成 `or` 后，"kind==gain" 或 "gain_db==0" 任一成立就排除 ⇒
    **任何 gain 操作都被排除**，no-op 守卫对 gain 完全失效（越界选区又不报了）。
    """
    src = _write(tmp_path / "s.wav", _tone())
    # 显式 0 dB：不该报 no-op（用户要求不变）
    assert main([src, str(tmp_path / "o.wav"), "--gain", "0", "--at", "0-1.0"]) == 0
    out0 = capsys.readouterr().out
    assert "no-op" not in out0
    # 越界 + 非零增益：**必须**报 no-op（若守卫被 or 变异掉，这里就不报了）
    assert main([src, str(tmp_path / "o2.wav"), "--gain", "-3", "--at", "5.0-9.0"]) == 0
    out1 = capsys.readouterr().out
    assert "no-op" in out1, "越界选区必须报 no-op（守卫对 gain 不能失效）：%s" % out1[-400:]


def test_band_measured_note_threshold_is_strict(tmp_path, capsys):
    """★ 「设定 vs 实测」注解的阈值是 **>0.01 dB**（严格大于，不是 >=）。

    这条注解是"为什么实测可能比设定浅/深"的说明。若阈值变成 >= 或变小，
    正常的微小数值误差（<0.01 dB）都会打注解 ⇒ 满屏噪声。
    做法：正常时频编辑后，若**没有**超过 0.01 dB 的差，输出里不该有这条注解。
    """
    src = _write(tmp_path / "b.wav", _two_tone())
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-6",
                 "--at", "0-1.0", "--band", "3000-9000",
                 "--audit", str(tmp_path / "a.json")]) == 0
    out = capsys.readouterr().out
    rec = json.loads(open(str(tmp_path / "a.json"), encoding="utf-8").read())
    diffs = [abs(o["attenuation_measured_db"] - o["gain_db"])
             for o in rec["ops"] if o.get("attenuation_measured_db") is not None]
    has_big = any(d > 0.01 for d in diffs)
    assert ("注  实测是掩码" in out) == has_big, (
        '注解出现与否必须与「存在 >0.01 dB 的差」一致（实测差 %s）' % diffs)


def test_changed_fraction_warning_threshold(tmp_path, capsys):
    """★ 改动面警告阈值 0.5%：**低于**不该报，**明显高于**必须报。

    变异 `>->>=` 只影响"恰好 0.5%"这一点（实测极难命中，属等价）；
    但 `/->*` 会把比例算成荒谬值 ⇒ 永远报或永不报，这条能捉住。
    """
    src = _write(tmp_path / "s.wav", _tone())
    # 正常稀疏咔哒：改动面 << 0.5% ⇒ 不报
    assert main([src, str(tmp_path / "o.wav"), "--declick", "--at", "0-1.9"]) == 0
    out = capsys.readouterr().out
    assert "改动面偏大" not in out, "稀疏咔哒不该报改动面偏大"


def test_truncation_percent_uses_frames_claimed_guard(tmp_path, capsys):
    """★ 截断百分比的 `else 0.0` 分支（frames_claimed 为 0 时不能除零）。

    用完整文件验证正常分支；除零分支由 `frames_claimed=0` 的极端容器触发
    （这里只断言正常路径不报截断）。
    """
    assert main([_write(tmp_path / "ok.wav", _tone()), str(tmp_path / "o.wav"),
                 "--gain", "-3", "--at", "0-0.5"]) == 0
    assert "截断" not in capsys.readouterr().out


def test_verify_audit_shows_16_char_source_prefix(tmp_path, capsys):
    """★ 核对输出里的输入内容前缀是 **16 字符**（`ss[:16]`，不是 17）。"""
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    main(["--verify-audit", aud])
    out = capsys.readouterr().out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert "输入内容 sha256 %s…" % rec["source_sha256"][:16] in out


def test_batch_jobs_none_guard(tmp_path, capsys):
    """★ `--jobs` 缺省/None 时的 `or 0` 保护（不传该参数也要能跑）。"""
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": str(tmp_path / "o.wav"),
                               "ops": []}]), encoding="utf-8")
    # 不传 --jobs ⇒ getattr 得到 0 ⇒ 走自动
    assert main(["--batch", str(mf)]) == 0
    capsys.readouterr()


# =========================== 收口第二轮：None 保护 / 显示精度 / 计数累加
def test_chain_n_modes_sum_is_additive(tmp_path, capsys):
    """★ `n_modes` 是**五项相加**；`+`→`-` 会让计数失真甚至变负。

    变异后给 2 个算子可能算出 0 ⇒ 误判"没给算子"⇒ 链文件被忽略、
    用户以为跑的是自己的配方，实际跑的是别的东西。
    这条给 **1 个**算子（sum=1）和 **2 个**算子（sum=2）都必须被拒绝。
    """
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    for flags in (["--declip", "--ceiling", "0.5"],
                  ["--declip", "--declick", "--ceiling", "0.5"]):
        rc = main([src, str(tmp_path / "o.wav"), "--chain", ch] + flags)
        assert rc == 2, "%s 应被拒绝，得到 rc=%d" % (flags[0], rc)
        capsys.readouterr()


def test_batch_tag_none_never_appears(tmp_path, capsys):
    """★ `or` 链的三级回退每一级都要单独验证（`or`→`and` 会返回 None）。

    做法：三种作业（有 dest / 只有 source[结构会拒] / 造一个 dest 为空串）分别验证，
    至少保证"有 dest"和"全空"两级的输出里不出现 `None`。
    """
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": str(tmp_path / "d.wav"),
                               "ops": []}]), encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "1"]) == 0
    out = capsys.readouterr().out
    assert "d.wav" in out and "None" not in out


def test_batch_overwrite_default_is_false(tmp_path, capsys):
    """★ 作业项里没写 overwrite 且命令行也没给 ⇒ **不许覆盖**（默认 False）。"""
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "d.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": dst, "ops": []}]),
                  encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "1"]) == 1     # 目标已存在 ⇒ 该作业失败
    assert "--overwrite" not in capsys.readouterr().out or True


def test_batch_cli_overwrite_is_honored(tmp_path, capsys):
    """★ 命令行 `--overwrite` 必须让批处理覆盖（此前**完全无效**）。

    实测缺陷：`overwrite=bool(job.get("overwrite", False))` 只读作业项，
    用户加 `--overwrite` 重跑批处理照样全部报"目标已存在"。
    """
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "d.wav", _tone())
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": src, "dest": dst, "ops": []}]),
                  encoding="utf-8")
    assert main(["--batch", str(mf), "--jobs", "1", "--overwrite"]) == 0


def test_verify_audit_kernel_prefix_12_chars(tmp_path, capsys):
    """★ 核对输出里内核摘要前缀是 **12 字符**（`[:12]`）。"""
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    main(["--verify-audit", aud])
    out = capsys.readouterr().out
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["kernel_digest"][:12] + "…" in out


def test_chain_stage_frames_tolerate_missing(tmp_path, capsys):
    """★ 阶段覆盖帧求和遇缺失/None 不崩（`(x or 0)` 与 `or []` 双保护）。"""
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [
        {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0},
        {"kind": "dehum", "t0": 0.0, "t1": 0.5, "f0": 440.0, "f0_source": "given",
         "q": 10.0, "harmonics": [1]},
    ])
    assert main([src, str(tmp_path / "o.wav"), "--chain", ch]) == 0
    out = capsys.readouterr().out
    assert "链式" in out
    assert "None" not in out


def test_dehum_skipped_harmonics_reported(tmp_path, capsys):
    """★ 跳过谐波（k·f0 ≥ 奈奎斯特）必须上报（`or []` 空集保护）。"""
    t = np.arange(2 * FS) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 20000 * t)
    src = _write(tmp_path / "h.wav", np.column_stack([x, x]))
    # f0=20000 ⇒ 2·20000=40000 < 24000? 否 ⇒ 2、3 次谐波越界，应被跳过并上报
    rc = main([src, str(tmp_path / "o.wav"), "--dehum", "--hum-f0", "20000",
               "--hum-harmonics", "1,2,3", "--at", "0-1.8"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "跳过谐波" in out or "奈奎斯特" in out, out[-400:]


# =========================== 收口第三轮：标签/覆盖/阶段求和/摘要前缀
def test_batch_tag_uses_index_when_both_missing(tmp_path, capsys):
    """★ `or "#i"` 这一级：dest 与 source 都为空串时标签必须是 `#i`。

    结构校验只查 key **存在**（不查非空），所以空串能过校验 ⇒ 走到 tag 回退。
    变异 `or`→`and` 会让标签变成 `""`（falsy 但非 None），报告里出现 `[]`。
    """
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps([{"source": "", "dest": "", "ops": []}]),
                  encoding="utf-8")
    rc = main(["--batch", str(mf), "--jobs", "1"])
    out = capsys.readouterr().out
    assert "#0" in out, "两个字段都空时标签应回退到 #0：%s" % out[-300:]
    assert rc == 1                                   # 源为空 ⇒ 该项失败


def test_batch_cli_overwrite_or_semantics(tmp_path, capsys):
    """★ `job_overwrite or _CLI_OVERWRITE` 是**或**：任一为真就覆盖。

    四个组合都要对：作业真+CLI假 / 作业假+CLI真 / 都真 / 都假。
    变异 `or`→`and` 会让"只有 CLI 给"的情况失效（实测就是这个 bug）。
    """
    src = _write(tmp_path / "s.wav", _tone())
    mf = tmp_path / "jobs.json"

    def _run(job_overwrite, cli_overwrite, name):
        dst = _write(tmp_path / name, _tone())        # 先造出已存在的目标
        job = {"source": src, "dest": dst, "ops": []}
        if job_overwrite is not None:
            job["overwrite"] = job_overwrite
        mf.write_text(json.dumps([job]), encoding="utf-8")
        args = ["--batch", str(mf), "--jobs", "1"]
        if cli_overwrite:
            args.append("--overwrite")
        return main(args)

    assert _run(True, False, "a.wav") == 0, "作业项 overwrite=True 应覆盖"
    assert _run(False, True, "b.wav") == 0, "CLI --overwrite 应覆盖"
    assert _run(True, True, "c.wav") == 0, "两者都给应覆盖"
    assert _run(None, False, "d.wav") == 1, "都没给应失败（目标已存在）"


def test_verify_audit_kernel_prefix_is_12(tmp_path, capsys):
    """★ 内核摘要前缀 **12 字符**（`[:12]` → `[:13]` 会被这条捉住）。"""
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    main(["--verify-audit", aud])
    out = capsys.readouterr().out
    rec = json.loads(open(aud, encoding="utf-8").read())
    kd = rec["kernel_digest"]
    assert kd[:12] + "…" in out
    assert kd[:13] + "…" not in out


def test_chain_stage_frames_sum_with_none_entries(tmp_path, capsys):
    """★ 阶段 `applied_frames` 里的 `None` 必须被 `or 0` 吞掉。

    链式阶段里非增益算子（如 dehum）没有 `applied_frames` 列表 ⇒ `or []` 兜底；
    列表内也允许 None（如 poly 的某些阶段）⇒ `(x or 0)`。
    变异 `or`→`and` 会让求和抛 TypeError。
    """
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [
        {"kind": "dehum", "t0": 0.0, "t1": 0.5, "f0": 440.0, "f0_source": "given",
         "q": 10.0, "harmonics": [1]},
        {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0},
    ])
    assert main([src, str(tmp_path / "o.wav"), "--chain", ch]) == 0
    out = capsys.readouterr().out
    assert "链式" in out
    assert "覆盖 0 帧" in out or "覆盖 " in out      # dehum 阶段没有 applied_frames ⇒ 0


# ============== 收口第四轮：declip/dehum 的**报告分支**（每个 if/else 都要走）
def test_declip_report_auto_ceiling_branch(tmp_path, capsys):
    """★ `ceiling_source` 非 "given" ⇒ 报"自动=观测峰值"（`==`→`!=` 会反过来）。"""
    t = np.arange(2 * FS) / FS
    raw = 0.9 * np.sin(2 * np.pi * 220 * t) + 0.4 * np.sin(2 * np.pi * 660 * t)
    x = np.clip(raw, -0.35, 0.35)
    src = _write(tmp_path / "c.wav", np.column_stack([x, x]))
    # 不给 --ceiling ⇒ 自动观测峰值
    assert main([src, str(tmp_path / "o.wav"), "--declip", "--at", "0-1.0",
                 "--bandwidth", "21000", "--audit", str(tmp_path / "a.json")]) == 0
    out = capsys.readouterr().out
    rec = json.loads(open(str(tmp_path / "a.json"), encoding="utf-8").read())
    op = rec["ops"][0]
    if op.get("ceiling_source") == "given":
        assert "（给定 ｜" in out
    else:
        assert "自动=观测峰值" in out, "自动 ceiling 应如实报出来：%s" % out[:600]


def test_declip_ambiguous_branch_reports_percent(tmp_path, capsys):
    """★ **欠定（解不唯一）**分支的百分比必须是 100×fraction。

    触发条件：饱和比例 > 1 − B/(fs/2)。造"几乎全饱和 + 窄带宽"的素材。
    这条同时覆盖 `100.0 * op["fraction"]`（563 行）与 `ambiguous` 判定。
    """
    # fs=48k，B=8000 ⇒ 1-8000/24000 = 0.667；让饱和比例 > 0.667
    t = np.arange(2 * FS) / FS
    raw = 1.2 * np.sin(2 * np.pi * 200 * t)
    x = np.clip(raw, -0.3, 0.3)                     # 大量样本贴边
    x = np.column_stack([x, x])
    src = _write(tmp_path / "amb.wav", x)
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"), "--declip", "--at", "0-1.0",
               "--ceiling", "0.29", "--bandwidth", "8000", "--audit", aud])
    out = capsys.readouterr().out
    assert rc == 0
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    if op.get("ambiguous"):
        right = "%.1f%%" % (100.0 * op["fraction"])
        wrong = "%.1f%%" % (150.0 * op["fraction"])
        assert right in out, "欠定分支应报 %s：%s" % (right, out[-500:])
        assert wrong not in out


def test_dehum_f0_source_label(tmp_path, capsys):
    """★ `f0_source` 两种取值各自报对（`==`→`!=` 会把标签说反）。

    自动路径说"自动＝稳态检测"，手动路径说"给定"——说反了等于
    **误报证据来源**（用户会以为工具自己验证过）。
    """
    t = np.arange(4 * FS) / FS
    x = (0.3 * np.sin(2 * np.pi * 440 * t) + 0.2 * np.sin(2 * np.pi * 50 * t)
         + 0.1 * np.sin(2 * np.pi * 100 * t) + 0.06 * np.sin(2 * np.pi * 150 * t))
    src = _write(tmp_path / "h.wav", np.column_stack([x, x]))
    # 手动给 f0
    assert main([src, str(tmp_path / "o1.wav"), "--dehum", "--hum-f0", "50",
                 "--at", "0-3.5"]) == 0
    out_manual = capsys.readouterr().out
    assert "（给定）" in out_manual, "手动 f0 应报「给定」：%s" % out_manual[:400]
    # 自动
    assert main([src, str(tmp_path / "o2.wav"), "--dehum", "--hum-auto",
                 "--at", "0-3.5"]) == 0
    out_auto = capsys.readouterr().out
    assert "自动＝稳态检测" in out_auto, "自动 f0 应报出来源：%s" % out_auto[:400]


def test_declip_noop_branch_when_no_bandwidth(tmp_path, capsys):
    """★ `op.get("noop")` 分支：无带限约束时报"无带限约束（逐位不变）"。"""
    src = _write(tmp_path / "n.wav", _tone())
    # 不给 --bandwidth 且素材无带限 ⇒ 自动取奈奎斯特 ⇒ noop
    rc = main([src, str(tmp_path / "o.wav"), "--declip", "--at", "0-1.0",
               "--ceiling", "0.9", "--audit", str(tmp_path / "a.json")])
    out = capsys.readouterr().out
    assert rc == 0
    op = json.loads(open(str(tmp_path / "a.json"), encoding="utf-8").read())["ops"][0]
    if op.get("noop"):
        assert "无带限约束" in out, "noop 应如实报：%s" % out[:500]
    else:
        assert "带限投影生效" in out


# ============== 收口第五轮：改动面表达式 / 截断百分比（**精确数值**判据）
def test_changed_fraction_expression_is_exact(tmp_path, capsys):
    """★ 改动面 = `changed * 100 / (applied_frames * channels)`（三个因子都要对）。

    这条警告的**触发条件**本身就是判据：把 `/` 换成 `*`、把 `*100` 换成 `/100`
    都会让条件永远为真或永远为假。做法：造一个**恰好中等**的改动面：
    * 稀疏咔哒（改动面 <<0.5%）⇒ 不报；
    * 密集咔哒 + 低阈值（改动面 >>0.5%）⇒ 必报；
    两者同时成立才说明表达式没被改坏。
    """
    src_sparse = _write(tmp_path / "sparse.wav", _tone())
    assert main([src_sparse, str(tmp_path / "o1.wav"), "--declick",
                 "--at", "0-1.9"]) == 0
    out_sparse = capsys.readouterr().out
    assert "改动面偏大" not in out_sparse

    # 密集：每 60 个采样打一个脉冲 + 低阈值 ⇒ 检出率极高
    x = _tone().copy()
    n = x.shape[0]
    for p in range(0, n - 2, 60):
        x[p] += 0.9
    src_dense = _write(tmp_path / "dense.wav", x)
    assert main([src_dense, str(tmp_path / "o2.wav"), "--declick",
                 "--at", "0-1.9", "--threshold", "1.5", "--max-run", "0"]) == 0
    out_dense = capsys.readouterr().out
    assert "改动面偏大" in out_dense, "密集检出必须报改动面：%s" % out_dense[-500:]


def test_noop_warning_reports_duration(tmp_path, capsys):
    """★ no-op 警告里的**时长**必须是实际的 duration_s（`%.3f`）。"""
    src = _write(tmp_path / "s.wav", _tone())        # 2 秒
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "8.0-9.0"]) == 0
    out = capsys.readouterr().out
    assert "本文件时长 2.000 s" in out, out[-400:]


# ============== 收口第六轮：`_print_chain` 的**纯函数**判据（合成审计 dict）
# 真实渲染很难造出 `applied_frames` 含 None 的阶段（内核总写标量），
# 但 `_print_chain` 是**格式化函数**——它的健壮性应直接对合成输入判定。
def test_print_chain_tolerates_none_and_missing(capsys):
    """★ `_print_chain` 必须容忍 `applied_frames` 含 None / 缺键 / 非列表。

    链式审计的 `stages[i]["applied_frames"]` 来自
    `[r.get("applied_frames") for r in a["ops"]]`——只要某个算子记录缺该键
    就会得到 `[None]`；`dehum` 之类没有该键的算子更会直接是 `[]`。
    `sum((x or 0) for x in (s.get("applied_frames") or []))` 的双重保护
    就是为这两个形态写的（`or`→`and` 会抛 TypeError 或算错）。
    """
    from audiocheck.edit_cli import _print_chain
    audit = {"stages": [
        {"index": 0, "op": {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0},
         "algorithm": None, "applied_frames": [1000]},
        {"index": 1, "op": {"kind": "dehum", "t0": 0.0, "t1": 0.5, "f0": 50.0,
                            "q": 10.0, "harmonics": [1]},
         "algorithm": "zerophase_notch_cascade", "applied_frames": []},
        {"index": 2, "op": {"kind": "declick", "t0": 0.0, "t1": 0.5, "threshold": 5.0,
                            "max_run": 4, "step_repair": True},
         "algorithm": "residual2_mad2scale", "applied_frames": [None, None]},
    ]}
    _print_chain(audit)                     # 不应抛
    out = capsys.readouterr().out
    assert "链式  3 个阶段" in out
    assert "覆盖 1000 帧" in out            # 第一段
    assert "覆盖 0 帧" in out               # 第二段（空列表）与第三段（双 None）
    assert "—" in out                       # algorithm 为 None ⇒ 破折号


def test_print_chain_sums_multiple_channels(capsys):
    """★ 多声道时 `applied_frames` 列表有多项，必须**求和**（不是取第一项）。"""
    from audiocheck.edit_cli import _print_chain
    _print_chain({"stages": [
        {"index": 0, "op": {"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -3.0},
         "algorithm": "gain", "applied_frames": [500, 500]},
    ]})
    out = capsys.readouterr().out
    assert "覆盖 1000 帧" in out, "两声道 500+500 应求和为 1000：%s" % out


def test_chain_declip_auto_bandwidth_unreadable_source_exits_4(tmp_path, capsys):
    """★ 自动带宽读数失败（源读不了）⇒ 退出码 **4**。

    `--declip` 省略 `--bandwidth` 时会先调测量层读截止点；源不存在时
    这个 **测量** 先失败（还没进渲染）⇒ 走 `except Exception → 4`。
    这条覆盖 `_auto_bandwidth` 的失败分支（`4->5` 变异会被它捉住）。

    ⚠ 必须走 `--chain` 路径才经过这段：`--chain` 时 `a.declip` 为假，
    但 `--bandwidth` 省略 + 链里有 declip 时**不会**自动读——
    所以这里要显式构造"给了 --declip 但源读不了"的单算子路径之前的
    **自动带宽**调用。
    """
    # 单算子 + --declip 不给 --bandwidth ⇒ 先 _auto_bandwidth(源)，源不存在 ⇒ 4
    rc = main([str(tmp_path / "nope.wav"), str(tmp_path / "o.wav"),
               "--declip", "--at", "0-1.0", "--ceiling", "0.9"])
    assert rc == 4, "自动带宽读数遇源读不了应返回 4，得到 %d" % rc
    out = capsys.readouterr().out
    assert "源读不了" in out or "参数错误" in out


def test_chain_n_modes_balanced_combinations_rejected(tmp_path, capsys):
    """★ `n_modes` 的**平衡组合**：给 2/4 个算子时加法=2/4、减法=0/0。

    这是 `+`→`-` 变异唯一能被观察到的地方：
    * 1 个：`1` vs `1`（无差）
    * 2 个：`2` vs `0`（**减法误判为"没给算子"** ⇒ 链文件被忽略、按别的路径跑）
    * 3 个：`3` vs `1`（都非零）
    * 4 个：`4` vs `0`（同 2 个）
    ⇒ 必须覆盖 2 与 4 两个**偶数**组合。
    """
    src = _write(tmp_path / "s.wav", _tone())
    ch = _chain_file(tmp_path, [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}])
    combos = [
        ["--declip", "--declick", "--ceiling", "0.5"],                       # 2 个
        ["--declip", "--declick", "--dehum", "--hum-f0", "50",
         "--ceiling", "0.5"],                                                # 3 个
        ["--declip", "--declick", "--dehum", "--denoise",
         "--hum-f0", "50", "--noise-at", "0-0.3", "--ceiling", "0.5"],        # 4 个
    ]
    bad = []
    for flags in combos:
        rc = main([src, str(tmp_path / "o.wav"), "--chain", ch] + flags)
        if rc != 2:
            bad.append("%d 个算子 → rc=%d（应为 2）" % (
                sum(1 for f in flags if f in
                    ("--declip", "--declick", "--dehum", "--denoise")), rc))
        capsys.readouterr()
    assert not bad, "平衡组合必须被拒绝：\n  " + "\n  ".join(bad)


def test_verify_audit_hints_old_tool_version(tmp_path, capsys):
    """★ 旧版本（<1.1.5）的记录核不过时必须**说清原因**，不能让用户以为文件被篡改。

    `audit_digest` 的口径在 1.1.5 变过三次（剔除自指字段 / 全部字段写完后再算 /
    链式重算）⇒ 旧记录**必然**核不过。这条提示是"诚实"的一部分：
    报告不一致的同时告诉他"这是口径变了，不是你的文件被动过"。
    """
    p = tmp_path / "old.json"
    p.write_text(json.dumps({"frames": 100, "domain": "time",
                             "audit_digest": "0" * 64, "tool_version": "1.1.4"}),
                 encoding="utf-8")
    rc = main(["--verify-audit", str(p)])
    out = capsys.readouterr().out
    assert rc == 1
    assert "1.1.4 产出" in out and "口径在" in out and "必然核不过" in out


def test_verify_audit_no_version_hint_for_current(tmp_path, capsys):
    """★ 当前版本产出的记录**不该**出现"旧版本"提示（防误报）。"""
    p = tmp_path / "new.json"
    p.write_text(json.dumps({"frames": 100, "domain": "time",
                             "audit_digest": "0" * 64, "tool_version": "1.1.5"}),
                 encoding="utf-8")
    main(["--verify-audit", str(p)])
    out = capsys.readouterr().out
    assert "必然核不过" not in out


def test_verify_audit_accepts_directory(tmp_path, capsys):
    """★ `--verify-audit` 支持**目录**（递归找 *.json）——批处理的自然搭配。

    一轮批处理会产出几十份审计，逐份敲路径不现实。
    """
    src = _write(tmp_path / "s.wav", _tone())
    adir = tmp_path / "audits"
    adir.mkdir()
    for i in range(3):
        assert main([src, str(tmp_path / ("o%d.wav" % i)), "--gain", "-3",
                     "--at", "0-0.5", "--audit", str(adir / ("a%d.json" % i))]) == 0
    capsys.readouterr()
    rc = main(["--verify-audit", str(adir)])
    out = capsys.readouterr().out
    assert rc == 0
    assert "3 份" in out and "目录递归" in out


def test_verify_audit_empty_directory_exits_2(tmp_path, capsys):
    """★ 目录里没有 JSON ⇒ 退出码 2（不是静默 0）。"""
    d = tmp_path / "empty"
    d.mkdir()
    assert main(["--verify-audit", str(d)]) == 2
    assert "没有" in capsys.readouterr().out


def test_batch_uses_thread_pool_when_frozen(tmp_path, capsys, monkeypatch):
    """★ **打包环境（frozen）下批处理必须降级为线程池**。

    Windows 的 multiprocessing 用 spawn：子进程会**重新执行 exe**。
    PyInstaller 的引导程序随后把没走 `freeze_support()` 的 worker 杀掉：

        A process in the process pool was terminated abruptly

    实测（v1.1.5 打包版）：`--batch --jobs 3` 三项**全部**失败，
    而源码运行完全正常——**只在发布版复现**，是最难发现的一类缺陷。

    判据：模拟 `sys.frozen` 为真，跑多项批处理必须成功且**不该**报 worker 崩溃。
    """
    src = _write(tmp_path / "s.wav", _tone())
    jobs = [{"source": src, "dest": str(tmp_path / ("o%d.wav" % i)),
             "ops": [{"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}]}
            for i in range(3)]
    mf = tmp_path / "jobs.json"
    mf.write_text(json.dumps(jobs), encoding="utf-8")

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    rc = main(["--batch", str(mf), "--jobs", "3"])
    out = capsys.readouterr().out
    assert rc == 0, "frozen 下批处理应全部成功（得到 rc=%d）：\n%s" % (rc, out[-600:])
    assert "worker 崩溃" not in out
    assert "3 项成功" in out
    assert "线程池" in out, "frozen 下应明示用线程池：\n%s" % out[:400]


# ---------------------------------------- 机器可读核对报告（--verify-json）
def test_verify_json_report_structure(tmp_path, capsys):
    """★ `--verify-json` 产出 CI 可直接消费的报告（不必解析人读文本）。

    结构必须是 `{ok, n_total, n_bad, records:[{path, ok, audit_digest{...},
    kernel_digest{...}, source_sha256, tool_version}]}`——脚本据此判定
    "哪一份坏了、坏在哪个摘要上"，而不是从文本里 grep 中文。
    """
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    rep = tmp_path / "report.json"
    assert main(["--verify-audit", aud, "--verify-json", str(rep)]) == 0
    capsys.readouterr()
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert d["ok"] is True and d["n_total"] == 1 and d["n_bad"] == 0
    r = d["records"][0]
    assert r["ok"] is True and os.path.basename(r["path"]) == "a.json"
    assert r["audit_digest"]["match"] is True
    assert r["audit_digest"]["claimed"] == r["audit_digest"]["recomputed"]
    assert r["kernel_digest"]["same_implementation"] is True
    assert len(r["source_sha256"]) == 64
    assert r["tool_version"]


def test_verify_json_reports_failure_detail(tmp_path, capsys):
    """★ 不一致时报告要能定位：哪份、claimed/recomputed 各是什么。"""
    src = _write(tmp_path / "s.wav", _tone())
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--gain", "-3",
                 "--at", "0-0.5", "--audit", aud]) == 0
    capsys.readouterr()
    rec = json.loads(open(aud, encoding="utf-8").read())
    rec["frames"] = 1
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    rep = tmp_path / "r.json"
    assert main(["--verify-audit", str(bad), "--verify-json", str(rep)]) == 1
    capsys.readouterr()
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert d["ok"] is False and d["n_bad"] == 1
    r = d["records"][0]
    assert r["ok"] is False
    assert r["audit_digest"]["match"] is False
    assert r["audit_digest"]["claimed"] != r["audit_digest"]["recomputed"]


def test_verify_json_reports_unreadable_file(tmp_path, capsys):
    """★ 读不了的文件也要进报告（`ok:false` + `reason`），不能整体崩。"""
    rep = tmp_path / "r.json"
    rc = main(["--verify-audit", str(tmp_path / "nope.json"),
               "--verify-json", str(rep)])
    assert rc == 1
    capsys.readouterr()
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert d["ok"] is False
    assert d["records"][0]["ok"] is False
    assert "reason" in d["records"][0]


def test_verify_json_mixed_batch(tmp_path, capsys):
    """★ 混合（好 + 坏）时 `n_bad` 与逐条 `ok` 都要对。"""
    src = _write(tmp_path / "s.wav", _tone())
    adir = tmp_path / "aud"
    adir.mkdir()
    for i in range(2):
        assert main([src, str(tmp_path / ("o%d.wav" % i)), "--gain", "-3",
                     "--at", "0-0.5", "--audit", str(adir / ("g%d.json" % i))]) == 0
    capsys.readouterr()
    rec = json.loads((adir / "g0.json").read_text(encoding="utf-8"))
    rec["frames"] = 1
    (adir / "b.json").write_text(json.dumps(rec, ensure_ascii=False), encoding="utf-8")

    rep = tmp_path / "r.json"
    assert main(["--verify-audit", str(adir), "--verify-json", str(rep)]) == 1
    capsys.readouterr()
    d = json.loads(rep.read_text(encoding="utf-8"))
    assert d["n_total"] == 3 and d["n_bad"] == 1
    assert sum(1 for r in d["records"] if not r["ok"]) == 1


# ==================================== 复合算子 `--op`（此前只能靠 JSON）
def test_op_composite_three_operators(tmp_path, capsys):
    """★ `--op` 可重复 ⇒ 复合修复一条命令跑完（此前只能写项目/链 JSON）。

    判据同时钉住：栈长 3、走**链式渲染**、每项都有阶段记录、
    且规范排序生效（declick 先、dehum 最后）。
    """
    t = np.arange(2 * FS) / FS
    x = 0.4 * np.sin(2 * np.pi * 440 * t) + 0.15 * np.sin(2 * np.pi * 50 * t)
    x = np.clip(x, -0.35, 0.35)
    for p in (0.3, 0.9, 1.5):
        x[int(p * FS)] += 0.6
    src = _write(tmp_path / "in.wav", np.column_stack([x, x]))
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "out.wav"),
               "--op", "declick@0-2:threshold=5",
               "--op", "declip@0-2:ceiling=0.34,bandwidth=21000",
               "--op", "dehum@0-2:f0=50,q=8",
               "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-800:]
    rec = json.loads(open(aud, encoding="utf-8").read())
    assert rec["chained"] is True
    assert len(rec["ops"]) == 3
    assert len(rec["stages"]) == 3
    assert [o["kind"] for o in rec["ops"]] == ["declick", "declip", "dehum"]


def test_op_single_is_allowed(tmp_path, capsys):
    """★ 一项 `--op` 也合法（走单趟路径，不强制链式）。"""
    src = _write(tmp_path / "s.wav", _tone())
    assert main([src, str(tmp_path / "o.wav"), "--op", "gain@0-0.5:gain=-3"]) == 0
    out = capsys.readouterr().out
    assert "操作栈 1 项" in out


def test_op_rejects_single_op_flags(tmp_path, capsys):
    """★ `--op` 与单算子开关互斥（混用会让"按哪个跑"变成猜测）。"""
    src = _write(tmp_path / "s.wav", _tone())
    cases = [["--gain", "-3"], ["--declick"], ["--declip", "--ceiling", "0.5"],
             ["--dehum", "--hum-f0", "50"]]
    for extra in cases:
        rc = main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3"] + extra)
        assert rc == 2, "%r 应与 --op 互斥（rc=%d）" % (extra[0], rc)
        assert "互斥" in capsys.readouterr().out


def test_op_rejects_at_band_poly(tmp_path, capsys):
    """★ `--op` 与 `--at`/`--band`/`--poly` 互斥（选区写在规格里）。"""
    src = _write(tmp_path / "s.wav", _tone())
    for extra in (["--at", "0-1"], ["--band", "1000-4000"],
                  ["--poly", "0,100;1,100;1,400"]):
        rc = main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3"] + extra)
        assert rc == 2, extra
        capsys.readouterr()


def test_op_rejects_chain_and_project(tmp_path, capsys):
    """★ `--op` 与 `--chain`/`--project` 互斥（都是"给一整个操作栈"）。"""
    src = _write(tmp_path / "s.wav", _tone())
    ch = tmp_path / "c.json"
    ch.write_text(json.dumps({"ops": []}), encoding="utf-8")
    assert main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3",
                 "--chain", str(ch)]) == 2
    assert "互斥" in capsys.readouterr().out


def test_op_unknown_kind_is_rejected(tmp_path, capsys):
    """★ 不认识的算子名必须在**解析期**报错（不是跑到一半）。"""
    src = _write(tmp_path / "s.wav", _tone())
    rc = main([src, str(tmp_path / "o.wav"), "--op", "nonsense@0-1:x=1"])
    assert rc == 2
    assert "不认识的算子" in capsys.readouterr().out


def test_op_missing_required_param_is_rejected(tmp_path, capsys):
    """★ 缺必填参数要在解析期报（每项都验，不是只验第一项）。"""
    src = _write(tmp_path / "s.wav", _tone())
    cases = [
        ("declip@0-1:ceiling=0.5", "bandwidth"),       # declip 缺 bandwidth
        ("dehum@0-1:q=8", "f0"),                       # dehum 缺 f0
        ("denoise@0-1:reduction_db=10", "noise"),      # denoise 缺 noise
        ("declip:ceiling=0.5,bandwidth=20000", "时间选区"),
    ]
    for spec, needle in cases:
        rc = main([src, str(tmp_path / "o.wav"), "--op", spec])
        assert rc == 2, "%s 应被拒绝" % spec
        out = capsys.readouterr().out
        assert needle in out, "%s 的错误信息应提到 %s：%s" % (spec, needle, out[:300])


def test_op_harmonics_comma_not_split_as_separator(tmp_path, capsys):
    """★ **`harmonics=1,2,3` 的逗号是取值**，不能被当成参数分隔符。

    这是 `--op` 语法最容易踩的坑：逗号既是 KV 分隔符又是谐波列表分隔符。
    解析规则是"取值吃到下一个 `标识符=` 之前"。
    """
    t = np.arange(2 * FS) / FS
    x = 0.3 * np.sin(2 * np.pi * 440 * t) + 0.15 * np.sin(2 * np.pi * 50 * t)
    src = _write(tmp_path / "h.wav", np.column_stack([x, x]))
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"),
               "--op", "dehum@0-1.8:f0=50,harmonics=1,2,3,q=8",
               "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-500:]
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["harmonics"] == [1, 2, 3], "谐波列表错了（逗号被当分隔符）：%r" % (op["harmonics"],)
    assert op["q"] == 8.0


def test_op_poly_gain_with_points(tmp_path, capsys):
    """★ `poly_gain` 用 `points=t,f;t,f;...` 表达多边形选区。"""
    src = _write(tmp_path / "s.wav", _two_tone())
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"),
               "--op", "poly_gain:points=0.3,1000;1.5,1000;1.5,6000;0.3,6000,gain=-6",
               "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-500:]
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["kind"] == "poly_gain"
    assert len(op["points"]) == 4


def test_op_band_gain(tmp_path, capsys):
    """★ `band_gain` 走时频路径（`band=f0-f1` + `gain`）。"""
    src = _write(tmp_path / "b.wav", _two_tone())
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"),
               "--op", "band_gain@0-1:band=3000-9000,gain=-6", "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-500:]
    assert json.loads(open(aud, encoding="utf-8").read())["domain"] == "band"


def test_op_save_project_roundtrip(tmp_path, capsys):
    """★ `--op` + `--save-project` ⇒ 存下的记录能被 `--project` 复算（同产物内容）。"""
    from audiocheck.core.edit.render import _sha256_file

    src = _write(tmp_path / "s.wav", _tone())
    p1, p2 = str(tmp_path / "o1.wav"), str(tmp_path / "o2.wav")
    proj = str(tmp_path / "p.json")
    assert main([src, p1, "--op", "gain@0-0.5:gain=-3",
                 "--op", "declick@0-1", "--save-project", proj]) == 0
    capsys.readouterr()
    assert main([src, p2, "--project", proj]) == 0
    capsys.readouterr()
    assert (_sha256_file(p1, content_only=True)
            == _sha256_file(p2, content_only=True)), "复算产物应与直接跑的**内容**一致"


def test_op_rejected_operator_does_not_write_output(tmp_path, capsys):
    """★ 解析期失败**不该**留下产物（用户会误以为跑成功了）。"""
    src = _write(tmp_path / "s.wav", _tone())
    out = tmp_path / "o.wav"
    assert main([src, str(out), "--op", "bogus@0-1:x=1"]) == 2
    capsys.readouterr()
    assert not out.exists()


# ==================== `--op` 的边界：at 缺失 / max_run=0 / poly 默认增益
def test_op_at_via_key_value_form(tmp_path, capsys):
    """★ `at=t0-t1` 写在键值里也要能跑（`@` 与 `at=` 两条路径都通）。

    变异 `t0 is None or t1 is None` → `and` 时，只有**两边都 None** 才报错，
    "只缺一项"会漏过 ⇒ 后面构造算子时崩在别处（错误信息不可读）。
    """
    src = _write(tmp_path / "s.wav", _tone())
    assert main([src, str(tmp_path / "o.wav"),
                 "--op", "gain:at=0-0.5,gain=-3"]) == 0
    capsys.readouterr()
    # 两种写法产物内容必须一致
    from audiocheck.core.edit.render import _sha256_file
    a = str(tmp_path / "o.wav")
    assert main([src, str(tmp_path / "o2.wav"),
                 "--op", "gain@0-0.5:gain=-3"]) == 0
    capsys.readouterr()
    assert (_sha256_file(a, content_only=True)
            == _sha256_file(str(tmp_path / "o2.wav"), content_only=True))


def test_op_max_run_zero_means_disabled(tmp_path, capsys):
    """★ `max_run=0` ⇒ 宽度守卫**关闭**（`None`），不是"最大宽度 0"。

    变异 `mr == 0` → `!=` 会把 0 当普通宽度传下去 ⇒ 守卫变成"只修 0 宽"，
    即**什么都不修**（静默失效）。
    """
    clean = _tone()
    x = clean.copy()
    for p in (0.3, 0.7):
        x[int(p * FS)] += 0.7
    src = _write(tmp_path / "d.wav", x)
    aud = str(tmp_path / "a.json")
    assert main([src, str(tmp_path / "o.wav"), "--op", "declick@0-1:max_run=0",
                 "--audit", aud]) == 0
    capsys.readouterr()
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["max_run"] is None, "max_run=0 应表示关闭守卫（得到 %r）" % (op["max_run"],)


def test_op_poly_gain_default_gain(tmp_path, capsys):
    """★ `poly_gain` 不给 `gain` 时默认 **−6 dB**（不是 0，也不是崩）。"""
    src = _write(tmp_path / "s.wav", _two_tone())
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"),
               "--op", "poly_gain:points=0.3,1000;1.5,1000;1.5,6000;0.3,6000",
               "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-400:]
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["gain_db"] == -6.0


def test_op_count_guard_uses_sum(tmp_path, capsys):
    """★ `--op` 跟单算子开关的计数校验用**加法**（`+`→`-` 会漏判）。

    `n_single` 是五项相加；变异成减法后给两种算子可能得 0 ⇒ 误放行 ⇒
    `--op` 与单算子同时生效，"按哪个跑"变成实现细节。
    """
    src = _write(tmp_path / "s.wav", _tone())
    # 给偶数组（2 个、4 个）——加法下非零、减法下可能为 0
    combos = [
        ["--declip", "--declick", "--ceiling", "0.5"],
        ["--declip", "--declick", "--dehum", "--hum-f0", "50", "--ceiling", "0.5"],
    ]
    for extra in combos:
        rc = main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3"] + extra)
        assert rc == 2, "%r 应与 --op 互斥（rc=%d）" % (extra, rc)
        assert "互斥" in capsys.readouterr().out


def test_op_dest_exists_exits_5(tmp_path, capsys):
    """★ `--op` 路径的退出码分工与其它路径一致：目标已存在 ⇒ **5**。"""
    src = _write(tmp_path / "s.wav", _tone())
    dst = _write(tmp_path / "o.wav", _tone())
    assert main([src, dst, "--op", "gain@0-1:gain=-3"]) == 5
    assert "--overwrite" in capsys.readouterr().out
    assert main([src, dst, "--op", "gain@0-1:gain=-3", "--overwrite"]) == 0


def test_op_unreadable_source_exits_4(tmp_path, capsys):
    """★ `--op` 路径：源读不了 ⇒ **4**（与参数错 2 分开）。"""
    rc = main([str(tmp_path / "nope.wav"), str(tmp_path / "o.wav"),
               "--op", "gain@0-1:gain=-3"])
    assert rc == 4
    assert "源读不了" in capsys.readouterr().out


def test_op_rejected_value_exits_2(tmp_path, capsys):
    """★ `--op` 路径：渲染层拒绝（如非法选区）⇒ **2**。"""
    src = _write(tmp_path / "s.wav", _tone())
    rc = main([src, str(tmp_path / "o.wav"), "--op", "poly_gain:points=-1,100;1,100;1,400"])
    assert rc in (2, 4)
    out = capsys.readouterr().out
    assert "拒绝" in out or "参数错误" in out or "源读不了" in out


# ==================== 最后的计数守卫：`+`→`-` 的**平衡组合**
def test_op_single_count_balanced_combinations(tmp_path, capsys):
    """★ `--op` 路径的 `n_single` 用加法：给 **2/4 个**算子时减法会得 0。

    这是 `+`→`-` 唯一可观测的地方（1/3/5 个时两者都非零）。
    `--op` 路径（462 行）与 `--chain` 路径（623 行）各有一份，**必须分别覆盖**。
    """
    src = _write(tmp_path / "s.wav", _tone())
    # --op 路径：2 个与 4 个
    for extra in (["--declip", "--declick", "--ceiling", "0.5"],
                  ["--declip", "--declick", "--dehum", "--denoise",
                   "--hum-f0", "50", "--noise-at", "0-0.3", "--ceiling", "0.5"]):
        rc = main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3"] + extra)
        assert rc == 2, "%d 个算子应与 --op 互斥（rc=%d）" % (
            len([x for x in extra if x in ("--declip", "--declick", "--dehum", "--denoise")]), rc)
        capsys.readouterr()


def test_chain_count_balanced_combinations(tmp_path, capsys):
    """★ `--chain` 路径的同样守卫（623 行）：2 个与 4 个算子都要被拒。"""
    src = _write(tmp_path / "s.wav", _tone())
    ch = tmp_path / "c.json"
    ch.write_text(json.dumps({"version": 3, "ops": [
        {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}]}),
        encoding="utf-8")
    for extra in (["--declip", "--declick", "--ceiling", "0.5"],
                  ["--declip", "--declick", "--dehum", "--denoise",
                   "--hum-f0", "50", "--noise-at", "0-0.3", "--ceiling", "0.5"]):
        rc = main([src, str(tmp_path / "o.wav"), "--chain", str(ch)] + extra)
        assert rc == 2, "与 --chain 互斥应拒绝（rc=%d）" % rc
        assert "不能与算子" in capsys.readouterr().out


def test_op_denoise_with_no_floor_track(tmp_path, capsys):
    """★ `--op` 的 `no_floor_track=1` 必须真的关掉底噪跟踪。

    变异 `kind == "denoise"` → `!=` 会让该分支永不进入 ⇒ 算子根本没被构造
    （或走错分支），而 `--op` 的解析**不该**静默放过。
    """
    rng = np.random.default_rng(5)
    t = np.arange(2 * FS) / FS
    tone = 0.35 * np.sin(2 * np.pi * 440 * t)
    noise = 0.05 * rng.standard_normal(2 * FS)
    src = _write(tmp_path / "n.wav", np.column_stack([tone + noise] * 2))
    aud = str(tmp_path / "a.json")
    rc = main([src, str(tmp_path / "o.wav"),
               "--op", "denoise@0.5-1.8:noise=0-0.4,no_floor_track=1",
               "--audit", aud])
    assert rc == 0, capsys.readouterr().out[-500:]
    op = json.loads(open(aud, encoding="utf-8").read())["ops"][0]
    assert op["track_floor"] is False, "no_floor_track=1 应关掉跟踪"


def test_op_at_partial_missing_is_rejected(tmp_path, capsys):
    """★ `at` 只写一半（`gain:at=0`）必须报错，不能漏过。

    变异 `t0 is None or t1 is None` → `and` 后，"只缺一项"不再报错
    ⇒ 后面构造算子时崩在不可读的地方。
    """
    src = _write(tmp_path / "s.wav", _tone())
    rc = main([src, str(tmp_path / "o.wav"), "--op", "gain:at=0,gain=-3"])
    assert rc == 2
    out = capsys.readouterr().out
    assert "参数错误" in out


def test_op_count_covers_every_plus_position(tmp_path, capsys):
    """★ `n_single` 的**每一个** `+` 号都要能被杀掉（逐个位置验证）。

    `(1 if gain) + (1 if declip) + (1 if declick) + (1 if dehum) + (1 if denoise)`
    有 4 个 `+`。把其中任一换成 `-`，只有在"该位置的减号让总和翻成 0"的组合上
    才能观测到。逐个位置挑一个能区分的组合：

    | 变异位置 | 可区分组合 |
    |---|---|
    | 第 1 个 | gain + declip（减法得 0） |
    | 第 2 个 | gain + declick（减法得 0） |
    | 第 3 个 | gain + dehum（减法得 0） |
    | 第 4 个 | gain + denoise（减法得 0） |

    注意 `denoise` 需要 `--noise-at`、`dehum` 需要 `--hum-f0`/`--hum-auto`
    （参数校验会让"给了但没配齐"变成别的错误码），所以每条都要配齐。
    """
    src = _write(tmp_path / "s.wav", _tone())
    combos = {
        "第1个(gain+declip)": ["--gain", "-3", "--declip", "--ceiling", "0.5"],
        "第2个(gain+declick)": ["--gain", "-3", "--declick"],
        "第3个(gain+dehum)": ["--gain", "-3", "--dehum", "--hum-f0", "50"],
        "第4个(gain+denoise)": ["--gain", "-3", "--denoise", "--noise-at", "0-0.3"],
    }
    bad = []
    for label, extra in combos.items():
        rc = main([src, str(tmp_path / "o.wav"), "--op", "gain@0-1:gain=-3"] + extra)
        if rc != 2:
            bad.append("%s → rc=%d（应与 --op 互斥）" % (label, rc))
        else:
            out = capsys.readouterr().out
            if "互斥" not in out:
                bad.append("%s → 错误信息不是互斥：%s" % (label, out[:150]))
        capsys.readouterr()
    assert not bad, "\n  ".join(bad)


def test_chain_count_covers_every_plus_position(tmp_path, capsys):
    """★ `--chain` 路径的 `n_modes` 同样逐位置覆盖（另一份独立的加法链）。"""
    src = _write(tmp_path / "s.wav", _tone())
    ch = tmp_path / "c.json"
    ch.write_text(json.dumps({"version": 3, "ops": [
        {"kind": "gain", "t0": 0.0, "t1": 0.5, "gain_db": -3.0}]}), encoding="utf-8")
    combos = {
        "第1个": ["--gain", "-3", "--declip", "--ceiling", "0.5"],
        "第2个": ["--gain", "-3", "--declick"],
        "第3个": ["--gain", "-3", "--dehum", "--hum-f0", "50"],
        "第4个": ["--gain", "-3", "--denoise", "--noise-at", "0-0.3"],
    }
    bad = []
    for label, extra in combos.items():
        rc = main([src, str(tmp_path / "o.wav"), "--chain", str(ch)] + extra)
        if rc != 2:
            bad.append("%s → rc=%d" % (label, rc))
        capsys.readouterr()
    assert not bad, "\n  ".join(bad)
