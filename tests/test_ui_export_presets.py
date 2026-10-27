"""导出预设（`ui/editor/export_dialog.py` 纯函数部分）的判据。

判据三条线：
1. **镜像内核规则**：`validate` 的回答必须与渲染内核的守卫一致——FLAC 不收 FLOAT
   （内核 `_check_subtype`）、抖动只对整型（内核 `_check_dither`）。提前报错，
   不许"点完导出才炸"。
2. **以 libsndfile 的回答为准**：`available_subtypes` 不背表，逐个用 `sf.check_format`
   过滤 ⇒ 判据只锁"FLAC 无 FLOAT / WAV 四件套都在"这类**可实测**的事实。
3. **预设落盘**：存/读往返一致；坏 JSON 不抛、返回空（坏文件不许挡导出）。
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.ui.editor.export_dialog import (  # noqa: E402
    available_subtypes,
    filter_for,
    load_export_presets,
    save_export_preset,
    validate,
)


# ------------------------------------------------------------------ 1. 镜像内核规则
def test_flac_rejects_float_like_the_kernel():
    assert "FLOAT" not in available_subtypes("FLAC")
    assert validate("FLAC", "FLOAT", False) is not None, "FLAC+FLOAT 必须被弹窗拦下"


def test_wav_accepts_the_standard_family():
    subs = available_subtypes("WAV")
    for want in ("PCM_16", "PCM_24", "PCM_32", "FLOAT"):
        assert want in subs, subs


def test_dither_only_for_integer_subtypes():
    assert validate("WAV", "PCM_16", True) is None
    assert validate("WAV", "FLOAT", True) is not None, "浮点上抖动必须被拦（内核同口径）"


def test_validate_accepts_a_working_recipe():
    assert validate("WAV", "PCM_24", False) is None
    assert validate("FLAC", "PCM_24", False) is None
    assert validate("AIFF", "PCM_16", True) is None


def test_validate_rejects_unknown_format_and_empty_subtype():
    assert validate("MP3", "PCM_16", False) is not None
    assert validate("WAV", "", False) is not None


# ------------------------------------------------------------------ 2. 过滤器与扩展名
def test_filter_follows_container():
    assert "*.wav" in filter_for("WAV")
    assert "*.flac" in filter_for("FLAC")
    assert "*.aiff" in filter_for("AIFF")


# ------------------------------------------------------------------ 3. 预设落盘
def test_preset_roundtrip_and_overwrite(tmp_path):
    p = str(tmp_path / "presets.json")
    save_export_preset(p, "母带", {"fmt": "WAV", "subtype": "PCM_24", "dither": True})
    save_export_preset(p, "存档", {"fmt": "FLAC", "subtype": "PCM_24", "dither": False})
    got = load_export_presets(p)
    assert got["母带"] == {"fmt": "WAV", "subtype": "PCM_24", "dither": True}
    assert got["存档"]["fmt"] == "FLAC"
    # 覆盖同名
    save_export_preset(p, "母带", {"fmt": "WAV", "subtype": "PCM_16", "dither": False})
    got = load_export_presets(p)
    assert got["母带"]["subtype"] == "PCM_16" and len(got) == 2


def test_bad_preset_file_returns_empty_not_raise(tmp_path):
    p = tmp_path / "broken.json"
    p.write_text("{not json", encoding="utf-8")
    assert load_export_presets(str(p)) == {}
    # 非顶层 dict 同样按坏文件处理
    p2 = tmp_path / "list.json"
    p2.write_text("[1,2]", encoding="utf-8")
    assert load_export_presets(str(p2)) == {}
    # 坏文件之后再存：不被旧内容污染
    save_export_preset(str(p), "新", {"fmt": "WAV", "subtype": "PCM_16", "dither": False})
    got = load_export_presets(str(p))
    assert list(got) == ["新"]


def test_saved_preset_file_is_readable_json(tmp_path):
    p = str(tmp_path / "x.json")
    save_export_preset(p, "a", {"fmt": "WAV", "subtype": "PCM_16", "dither": False})
    data = json.load(open(p, encoding="utf-8"))
    assert data["a"]["fmt"] == "WAV"
    assert os.path.exists(p)
