"""E2.1 不可信输入健壮性：畸形文件必须走**显式错误**路径。

依据 E2 提示词：本工具的核心动作是**读别人给的音频**，这是攻击面。
畸形 / 截断 / 伪造头部的文件必须
**有错误码 + 可读信息**，**不许 traceback、不许崩溃、不许静默通过**。

三层判据（都量真实行为，不量"应该会怎样"）：
  ① `analyze()` 对畸形语料**不抛异常**，且 `level` 只能是
     ok/warn/bad/error 四值之一（别的值 = 静默通过）；
  ② `level == "error"` 的结果**必须有可读 verdict**（错误码要有话说）；
  ③ **真实 CLI 子进程**输出里不许出现 `Traceback`，退出码必须在 0/2/3/4 内。

另附**有界随机变异**（固定种子，可复现）：随机改写正常 WAV 的字节，
任何未捕获异常都算失败。`deep=False` 让每个变异体只走容器读取路径
（崩溃几乎都出在那里），把测试时间压到可接受。
"""
from __future__ import annotations

import os
import random
import subprocess
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.engine import analyze  # noqa: E402

_LEVELS = {"ok", "warn", "bad", "error"}


def _valid_wav_bytes() -> bytes:
    import numpy as np
    import soundfile as sf

    p = os.path.join(tempfile.mkdtemp(), "v.wav")
    sf.write(p, np.column_stack([np.zeros(22050, dtype="float32")] * 2),
             44100, subtype="PCM_16")
    with open(p, "rb") as f:
        return f.read()


def _corpus() -> dict[str, bytes]:
    raw = _valid_wav_bytes()
    return {
        "empty": b"",
        "text": b"definitely not audio, plain text\n" * 40,
        "riff_only": b"RIFF" + (1000).to_bytes(4, "little") + b"WAVEfmt ",
        "header_only": raw[:44],
        "trunc_quarter": raw[: len(raw) // 4],
        "trunc_half": raw[: len(raw) // 2],
        "all_zero": b"\x00" * 4096,
        "huge_claim": raw[:44] + b"\x00" * (len(raw) - 44),
        "id3_only": b"ID3\x04\x00\x00\x00\x00\x00\x00" + b"\x00" * 200,
        "flac_trunc": b"fLaC" + b"\x00" * 40,
        "ogg_trunc": b"OggS" + b"\x00" * 30,
    }


@pytest.fixture(scope="module")
def corpus_dir():
    d = tempfile.mkdtemp(prefix="ac_rob_t_")
    out = {}
    for name, data in _corpus().items():
        p = os.path.join(d, name + ".wav")
        with open(p, "wb") as f:
            f.write(data)
        out[name] = p
    return out


@pytest.mark.parametrize("name", sorted(_corpus().keys()))
def test_malformed_file_never_raises_and_has_defined_level(name, corpus_dir):
    """① 不抛异常；② level 必须是四个合法值之一（不许静默通过）。"""
    try:
        r = analyze(corpus_dir[name])
    except Exception as e:                                     # noqa: BLE001
        pytest.fail("畸形文件 %s 抛异常：%s: %s" % (name, type(e).__name__, e))
    assert r.get("level") in _LEVELS, "level=%r（非法取值 = 静默通过）" % r.get("level")


@pytest.mark.parametrize("name", ["empty", "text", "flac_trunc", "ogg_trunc"])
def test_unreadable_file_gives_readable_error(name, corpus_dir):
    """③ 读不了的文件必须给**显式错误结果**（错误码 + 可读信息）。"""
    r = analyze(corpus_dir[name])
    assert r["level"] == "error", "%s 应判为 error，实得 %s" % (name, r["level"])
    assert (r.get("verdict") or "").strip(), "error 结果没有可读 verdict"
    assert "无法读取" in r["verdict"], "verdict 没说清是读不了：%r" % r["verdict"]
    assert r.get("errors"), "error 结果没带 errors 明细"


def test_cli_never_prints_traceback_on_malformed(corpus_dir):
    """真实 CLI 子进程：输出里不许有 `Traceback`，退出码必须在 0/2/3/4 内。"""
    for name in ("empty", "text", "flac_trunc"):
        p = subprocess.run([sys.executable, "-m", "audiocheck", corpus_dir[name]],
                           capture_output=True, text=True)
        blob = p.stdout + p.stderr
        assert "Traceback (most recent call last)" not in blob, \
            "CLI 对 %s 打了 traceback" % name
        assert p.returncode in (0, 2, 3, 4), "CLI 退出码 %d" % p.returncode


def test_cli_exit_code_4_marks_unreadable_input(corpus_dir):
    """读不了的文件给**独立错误码 4**（与"分析告警 3"区分）。"""
    p = subprocess.run([sys.executable, "-m", "audiocheck", corpus_dir["text"]],
                       capture_output=True, text=True)
    assert p.returncode == 4, "不可读输入的退出码应为 4，实得 %d" % p.returncode


def _write_wav(path, seconds=1.0):
    import numpy as np
    import soundfile as sf

    n = int(44100 * seconds)
    sf.write(str(path), np.column_stack([np.zeros(n, dtype="float32")] * 2),
             44100, subtype="PCM_16")
    return str(path)


def test_cli_warns_when_explicit_target_is_dropped(tmp_path):
    """★ 回归（真实曲库暴露）：显式给出的目标**没被收录**时必须说出来。

    不许静默少分析——真实踩到过两种成因：清单带 UTF-8 **BOM**（第一行路径作废）；
    路径超过 Windows **MAX_PATH**（260 字符）⇒ `isfile` 为 False。两种都会让文件
    从结果里**无声消失**，而汇总看不出少了什么。
    """
    good = _write_wav(tmp_path / "ok.wav")
    missing = str(tmp_path / "nope.wav")                    # 不存在
    p = subprocess.run([sys.executable, "-m", "audiocheck", "--quiet", good, missing],
                       capture_output=True, text=True)
    blob = p.stdout + p.stderr
    assert "个显式目标" in blob, blob
    assert "nope.wav" in blob, blob
    assert p.returncode == 0, "少一个目标不该改变退出码（保持向后兼容）"


def test_cli_list_accepts_utf8_bom(tmp_path):
    """★ 回归：`--list` 清单带 UTF-8 BOM 时，**第一行**路径不许被丢掉
    （PowerShell 的 `Out-File -Encoding utf8` 默认带 BOM；实测踩到）。"""
    wav = _write_wav(tmp_path / "a.wav")
    lst = tmp_path / "list.txt"
    lst.write_text("\ufeff" + wav + "\n", encoding="utf-8")   # 主动写入 BOM
    p = subprocess.run([sys.executable, "-m", "audiocheck", "--quiet", "--list", str(lst)],
                       capture_output=True, text=True)
    blob = p.stdout + p.stderr
    assert "待检测 1 个文件" in blob, blob
    assert "个显式目标" not in blob, blob


def test_truncated_but_openable_file_is_reported_explicitly(tmp_path):
    """★ 回归（**真实曲库**暴露）：截断的文件**能打开**——容器头部的 STREAMINFO 还在、
    帧数照旧声称满长——读到一半才失败（libsndfile 报 "flac decoder lost sync" /
    "Internal psf_fseek() failed."）。库里就有 3 个这样的文件（下载没下全）。

    早先不接这个异常，用户只会看到一句 libsndfile 英文错误，**看不出"文件被截断了"**，
    而结论是拿部分内容算的。现在要求：`read_incomplete` 为真、errors 里有可读的
    "截断"说明、并给出实读帧数。

    这是本文件里**唯一**一个"能打开但要报错"的语料——前 12 条都在打开阶段就失败。
    """
    import numpy as np
    import soundfile as sf

    fs = 48000
    x = (0.2 * np.sin(2 * np.pi * 440 * np.arange(fs * 30) / fs)).astype(np.float32)
    full = str(tmp_path / "full.flac")
    sf.write(full, np.column_stack([x, x]), fs, subtype="PCM_16")
    size = os.path.getsize(full)
    cut = str(tmp_path / "cut.flac")
    with open(full, "rb") as f, open(cut, "wb") as g:
        g.write(f.read(int(size * 0.7)))
    assert sf.info(cut).frames == fs * 30, "前提自检：截断后头部仍声称满长"   # 否则验的不是这件事

    r = analyze(cut, only=["basic"])
    assert r.get("read_incomplete") is True, r.get("errors")
    assert any("截断" in e for e in r["errors"]), r["errors"]
    assert r["read_frames"] < fs * 30
    # 可读部分仍在（不是被判 error 整条丢掉）
    assert r.get("peak_dbfs") is not None and r["read_frames"] > 0


def test_random_mutation_does_not_crash():
    """有界随机变异（固定种子、可复现）：任何未捕获异常都算失败。

    分两层断言（实测踩过：浅模式 `deep=False` **按设计不跑判定器**，
    结果里没有 `level` 键——那不是"静默通过"，是"未判定"）：
      * 深模式（6 个）：走完整判定路径，`level` 必须是四值之一；
      * 浅模式（14 个）：只走容器读取路径，只要求不抛异常、`errors` 字段在。
    """
    base = _valid_wav_bytes()
    rng = random.Random(20261016)
    p = os.path.join(tempfile.mkdtemp(), "fuzz.wav")
    for i in range(20):
        buf = bytearray(base)
        for _ in range(rng.randint(1, 24)):
            buf[rng.randrange(len(buf))] = rng.randrange(256)
        with open(p, "wb") as f:
            f.write(buf)
        deep = i < 6
        try:
            r = analyze(p, deep=deep)
        except Exception as e:                                     # noqa: BLE001
            pytest.fail("变异体 %d（deep=%s）抛异常：%s: %s"
                        % (i, deep, type(e).__name__, e))
        assert "errors" in r, "变异体 %d 的结果缺 errors 字段" % i
        if deep:
            assert r.get("level") in _LEVELS, "变异体 %d level=%r" % (i, r.get("level"))
