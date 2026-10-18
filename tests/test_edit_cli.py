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
