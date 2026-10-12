"""阈值配置的加载与校验。

## 为什么这个文件必须存在

`from_dict` 早期**只过滤键名、不校验类型**。而 dataclass 不做运行时类型检查，
于是 `{"cd_full": "不是数字"}` 被照单全收，一直等到判定器里做
`20.0 >= "不是数字"` 才抛 TypeError。

引擎又把分析器异常吞成"分析器告警"，最终表现是：

    判定整段消失 ｜ 汇总变成「合格 0 / 有疑 0 / 不达标 0」｜ 退出码仍是 0

**用户设了阈值、阈值没生效、判定没了、脚本还一切正常。**
这类静默降级比直接报错危险得多，所以必须在配置入口就拦下来。
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from audiocheck.config import Thresholds


# ---------------------------------------------------------------- 类型校验
@pytest.mark.parametrize("bad", [
    {"cd_full": "不是数字"},
    {"ltd": None},
    {"brick_db": "abc"},
    {"loud_war_lufs": [1, 2]},
    {"clip_pct": {"a": 1}},
])
def test_invalid_types_are_rejected(bad):
    with pytest.raises(ValueError) as ei:
        Thresholds.from_dict(bad)
    msg = str(ei.value)
    assert "类型不对" in msg
    for k in bad:
        assert k in msg, "报错信息应点名出问题的键"


def test_error_message_names_every_bad_key():
    """多个坏键要一次全报出来，别让用户改一个跑一次。"""
    with pytest.raises(ValueError) as ei:
        Thresholds.from_dict({"cd_full": "x", "ltd": None, "lossy": []})
    msg = str(ei.value)
    for k in ("cd_full", "ltd", "lossy"):
        assert k in msg


# ---------------------------------------------------------------- 容错
def test_numeric_strings_are_coerced():
    """手写 JSON 常把数字写成字符串，能救就救，不必让用户改文件。"""
    t = Thresholds.from_dict({"cd_full": "19.5", "ltd": "19"})
    assert t.cd_full == 19.5
    assert t.ltd == 19
    assert isinstance(t.ltd, (int, float))
    # 救回后必须真的可用于比较（这才是当初出事的地方）
    assert 20.0 >= t.cd_full


def test_unknown_keys_are_reported_not_fatal():
    """未知键不能报错（新版加字段不该让旧配置失效），但要能查到拼写错误。"""
    t = Thresholds.from_dict({"cd_full": 21.0, "cd_ful": 19.0})
    assert t.cd_full == 21.0
    assert t.__dict__.get("_unknown_keys") == ["cd_ful"]


def test_empty_and_none_are_safe():
    assert Thresholds.from_dict(None) == Thresholds()
    assert Thresholds.from_dict({}) == Thresholds()


# ---------------------------------------------------------------- 文件加载
def test_missing_file_uses_defaults():
    """文件不存在 → 默认值（这是正常路径，不该报错）。"""
    assert Thresholds.load("") == Thresholds()
    assert Thresholds.load(os.path.join(tempfile.gettempdir(), "绝不存在的.json")) == Thresholds()


def test_broken_json_raises_with_path():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write("{不是合法 JSON")
        with pytest.raises(ValueError) as ei:
            Thresholds.load(p)
        assert "不是合法 JSON" in str(ei.value)
        assert "bad.json" in str(ei.value)


def test_non_object_top_level_raises():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "list.json")
        with open(p, "w", encoding="utf-8") as f:
            json.dump([1, 2, 3], f)
        with pytest.raises(ValueError) as ei:
            Thresholds.load(p)
        assert "必须是对象" in str(ei.value)


def test_good_file_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "ok.json")
        t = Thresholds(cd_full=20.0, ltd=18.0)
        t.save(p)
        back = Thresholds.load(p)
        assert back.cd_full == 20.0 and back.ltd == 18.0


# ---------------------------------------------------------------- CLI 集成
def test_cli_rejects_bad_config_before_analyzing():
    """CLI 必须在**分析之前**拒掉坏配置，且退出码非 0。

    早期它一路跑到判定器才炸，还返回 0。
    """
    import subprocess
    import sys
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "bad.json")
        with open(p, "w", encoding="utf-8") as f:
            f.write('{"cd_full": "不是数字"}')
        # 用一个必然存在的路径参数是没必要的：配置校验在收集文件之前/之后都行，
        # 这里给个不存在的目标，重点看它有没有在配置上就退出
        r = subprocess.run(
            [sys.executable, "-m", "audiocheck", os.path.join(d, "x.flac"),
             "--config", p],
            capture_output=True, text=True, encoding="utf-8", errors="replace")
        assert r.returncode != 0, "坏配置必须非零退出，实得 %s" % r.returncode
        assert "Traceback" not in (r.stdout + r.stderr), "应优雅报错，不该抛回溯"
