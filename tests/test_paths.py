"""路径处理：Windows **MAX_PATH（260）** 长路径（`\\\\?\\` 前缀）。

真实踩到过（2026-10-16 真实曲库）：268 字符的 `.wav` 被 `isfile` 判 False ⇒
**从结果里静默消失**。本文件钉住修复：
  ① 纯字符串换算 `io_path` / `display_path`（跨平台可测）；
  ② Windows 上真实造一个 >260 字符路径的 wav，验证**能收录、能分析、结果路径干净**。
实测（2026-10-17）：`\\\\?\\` 被 libsndfile / `open` / `mutagen` / `os.walk` 全部接受，
普通长路径在 libsndfile 处直接 `System error`。
"""
from __future__ import annotations

import os
import shutil
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.context import display_path, io_path  # noqa: E402
from audiocheck.core.engine import analyze, collect_files  # noqa: E402


# --------------------------------------------------------------- ① 纯字符串
def test_display_path_strips_prefix():
    assert display_path("\\\\?\\C:\\a\\b.wav") == "C:\\a\\b.wav"


def test_display_path_unc():
    assert display_path("\\\\?\\UNC\\srv\\share\\x.wav") == "\\\\srv\\share\\x.wav"


def test_display_path_normal_is_untouched():
    assert display_path("C:\\a\\b.wav") == "C:\\a\\b.wav"
    assert display_path("/home/a/b.wav") == "/home/a/b.wav"


def test_io_path_idempotent_on_windows_prefix():
    # 已带前缀的输入不应被再加一层（否则 `\\?\` 会出现在中间，路径失效）
    assert io_path("\\\\?\\C:\\a\\b.wav") == "\\\\?\\C:\\a\\b.wav"


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 有 MAX_PATH / \\\\?\\ 语义")
def test_io_path_prepends_on_windows():
    p = io_path("C:\\a\\b.wav")
    assert p.startswith("\\\\?\\")
    assert display_path(p) == "C:\\a\\b.wav"


@pytest.mark.skipif(os.name != "nt", reason="UNC 语义仅 Windows")
def test_io_path_unc_form():
    assert io_path("\\\\srv\\share\\x.wav") == "\\\\?\\UNC\\srv\\share\\x.wav"


# ------------------------------------------------ ② Windows 真长路径端到端
@pytest.mark.skipif(os.name != "nt", reason="MAX_PATH 仅 Windows")
def test_long_path_file_is_collected_and_analyzed(tmp_path):
    import numpy as np
    import soundfile as sf

    # 堆到 >260 字符
    deep = str(tmp_path)
    for i in range(4):
        deep = os.path.join(deep, "%02d" % i + "d" * 60)
    os.makedirs(io_path(deep), exist_ok=True)
    f = os.path.join(deep, "n" * 70 + ".wav")
    if len(f) <= 260:                                     # tmp_path 太短时补齐
        pytest.skip("临时目录太短，构造不出 >260 的路径（len=%d）" % len(f))
    try:
        sf.write(io_path(f), np.zeros((4410, 2), dtype="float32"), 44100, subtype="PCM_16")

        # 收录
        got = collect_files([deep])
        assert any(os.path.abspath(g) == os.path.abspath(f) for g in got), \
            "长路径文件没有被 collect_files 收录"

        # 分析
        res = analyze(f)
        assert res["level"] != "error", "长路径文件被当成读不了：%s" % res.get("verdict")
        assert res["sr"] == 44100
        assert "\\\\?\\" not in res["path"], "结果里的 path 带出了内部前缀"
    finally:
        shutil.rmtree(io_path(str(tmp_path)), ignore_errors=True)
