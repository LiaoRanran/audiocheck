"""分析引擎 —— 调度分析器、组装结果。"""
from __future__ import annotations

import os

from ..config import AUDIO_EXTS, Thresholds
from .context import AnalysisContext, to_native
from .views import compact_curve
from . import analyzers  # noqa: F401  触发分析器自动注册（不要删）
from .registry import all_analyzers, schedule


def collect_files(targets) -> list[str]:
    """把"文件或目录"混搭的输入展开成音频文件列表（去重、排序）。"""
    out: list[str] = []
    for t in targets:
        if os.path.isdir(t):
            for dp, _, fns in os.walk(t):
                for fn in fns:
                    if fn.lower().endswith(AUDIO_EXTS):
                        out.append(os.path.join(dp, fn))
        elif os.path.isfile(t) and t.lower().endswith(AUDIO_EXTS):
            out.append(t)
    return sorted(dict.fromkeys(out))


def analyze(path: str, *, deep: bool = True, thresholds: Thresholds | None = None,
            only: list[str] | None = None) -> dict:
    """分析单个文件。

    参数
    ----
    path        音频文件路径
    deep        False 时跳过频谱与响度等重活（只做容器与电平统计）
    thresholds  自定义判定阈值，默认用 config.DEFAULT
    only        只跑指定 key 的分析器（调试用）

    返回
    ----
    扁平 dict：容器字段 + 各分析器输出 + `metrics`（指标元信息）+ `errors`。
    """
    th = thresholds or Thresholds()
    ctx = AnalysisContext(path=path, thresholds=th, deep=deep)
    ctx.seed_metrics()
    ctx.result["errors"] = []

    info = ctx.info
    ctx.result.update({
        "file": os.path.basename(path),
        "path": os.path.abspath(path),
        "sr": info.samplerate,
        "bits": info.subtype,
        "channels": info.channels,
        "format": info.format,
        "seconds": round(ctx.seconds, 1),
        "size_mb": round(os.path.getsize(path) / 1e6, 1),
        "kbps": round(os.path.getsize(path) * 8 / ctx.seconds / 1000) if ctx.seconds else None,
        "deep": deep,
    })

    for a in schedule(only):
        try:
            out = a.run(ctx)
            if out:
                ctx.result.update(out)
        except Exception as e:                                   # noqa: BLE001
            ctx.result["errors"].append("%s: %s" % (a.key, str(e)[:120]))

    # 频谱原始数组不进 JSON：抽稀成紧凑曲线，供界面/报告复用
    f, db = ctx.result.pop("_f", None), ctx.result.pop("_db", None)
    cf, cd = compact_curve(f, db)
    if cf is not None:
        ctx.result["curve"] = [[round(float(a), 1), round(float(b), 2)] for a, b in zip(cf, cd)]

    res = to_native(ctx.result)
    res["metrics"] = {k: {"label": m.label, "unit": m.unit, "digits": m.digits,
                          "group": m.group, "primary": m.primary}
                      for k, m in ctx.metrics.items()}
    res["analyzers"] = [a.key for a in all_analyzers()]
    return res


def list_analyzers() -> list[dict]:
    """给 CLI `--list-analyzers` 用。"""
    return [{"key": a.key, "label": a.label, "order": a.order,
             "requires": list(a.requires), "enabled": a.enabled}
            for a in all_analyzers()]
