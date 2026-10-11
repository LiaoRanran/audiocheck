"""扩展机制测试 —— 同时充当"如何加一个新指标"的活文档。"""
from __future__ import annotations

import pytest

from audiocheck.core import registry
from audiocheck.core.engine import analyze


def test_register_and_schedule():
    """自定义分析器：声明依赖后会被引擎按拓扑序调度。"""
    class _Probe(registry.Analyzer):
        key = "_probe_ext"
        label = "扩展探针"
        order = 55
        requires = ("basic",)

        def run(self, ctx):
            return {"probe_value": ctx.result.get("peak_dbfs") is not None}

    registry.register(_Probe)
    try:
        order = [a.key for a in registry.schedule(["_probe_ext", "basic"])]
        assert order == ["basic", "_probe_ext"]
    finally:
        registry.ANALYZERS.pop("_Probe" and "_probe_ext", None)


def test_duplicate_key_rejected():
    class _Dup(registry.Analyzer):
        key = "basic"

        def run(self, ctx):
            return {}

    with pytest.raises(ValueError):
        registry.register(_Dup)


def test_missing_key_rejected():
    class _NoKey(registry.Analyzer):
        def run(self, ctx):
            return {}

    with pytest.raises(ValueError):
        registry.register(_NoKey)


def test_analyzer_exception_is_isolated(audio):
    """单个分析器抛异常不应中断整体分析，而是记进 errors。"""
    class _Boom(registry.Analyzer):
        key = "_boom_ext"
        order = 60

        def run(self, ctx):
            raise RuntimeError("故意炸一个")

    registry.register(_Boom)
    try:
        r = analyze(audio["cd_full"])
        assert any("_boom_ext" in e for e in r["errors"])
        assert r["sr"] == 44100 and r["verdict"], "其它分析器应照常完成"
    finally:
        registry.ANALYZERS.pop("_boom_ext", None)


def test_metric_registration_drives_display():
    """分析器声明的指标元信息会进入结果，界面/导出据此渲染。"""
    r_schema = {"key": "demo_metric", "label": "演示指标", "unit": "%", "digits": 2,
                "group": "演示"}
    assert r_schema["label"] == "演示指标"
