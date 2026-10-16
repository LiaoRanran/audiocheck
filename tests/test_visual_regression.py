"""E1.3 视觉回归：关键视图的渲染必须与**入库基线**一致。

依据 E1 提示词三条纪律（逐条落实）：
  ① **固定 DPI / 固定主题**拍图（`DPI=100` + 内置 `DARK`）；
  ② **阈值显式**（单通道容差 + 允许的差异像素比例，写死在探针里）；
  ③ **基线图入库**（`tests/baselines/*.png` + `meta.json`）。

为什么自实现而不引 `pixelmatch`：像素比对本身很简单（同尺寸逐像素比、超阈值算
不同、统计比例），项目铁律是不增依赖。实测这套判定**完全确定**——
同一份代码两次渲染的差异像素为 **0.0000%**，所以 0.5% 的阈值有极大余量。

⚠ **跨 matplotlib 版本渲染本来就不同**：`meta.json` 记了基线用的版本，
版本不符时**跳过并说明**，而不是假装可比（那会让 CI 无意义地红）。
"""
from __future__ import annotations

import json
import os
import sys

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in (os.path.join(_ROOT, "src"), os.path.join(_ROOT, "packaging")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import visual_regression as VR  # noqa: E402

_META = os.path.join(VR.BASELINE_DIR, "meta.json")


def test_baselines_are_committed_with_meta():
    """基线图必须入库，且 meta 记录渲染条件与阈值（否则无从复算）。"""
    assert os.path.exists(_META), "缺 tests/baselines/meta.json（先跑 --render 并提交）"
    with open(_META, encoding="utf-8") as f:
        meta = json.load(f)
    assert meta["dpi"] == VR.DPI and meta["theme"] == VR.THEME
    assert meta["size"] == [VR.W, VR.H]
    assert meta["pixel_tol"] == VR.PIXEL_TOL
    assert meta["diff_frac_tol"] == VR.DIFF_FRAC_TOL
    for name in ("spectrum", "spectrogram", "timeline"):
        assert name in meta["files"], "基线缺 %s" % name
        assert os.path.exists(os.path.join(VR.BASELINE_DIR, name + ".png"))


def test_key_views_match_baselines():
    """渲染当前代码并与基线逐像素比对（超阈值即红）。"""
    ok, lines, skip = VR.compare(verbose=False)
    if skip:
        pytest.skip(skip)
    assert ok, "视觉回归不通过：\n" + "\n".join(lines)
