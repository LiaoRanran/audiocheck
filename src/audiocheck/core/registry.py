"""分析器注册表 —— 本项目的扩展点。

**加一个新指标 = 在 `core/analyzers/` 下新建一个文件**，写好类、挂上 `@register`，
其余全部自动生效（引擎调度、JSON 导出、GUI 指标卡、--list-analyzers 命令）。

    from audiocheck import Analyzer, register

    @register
    class PolarityAnalyzer(Analyzer):
        key = "polarity"
        label = "极性 / 相位"
        order = 45                    # 越大越晚跑
        requires = ("spectrum",)      # 声明依赖，引擎按拓扑序调度

        def run(self, ctx):
            return {"polarity_inverted": ...}

约定
----
* `run()` 必须返回 **可 JSON 序列化的原生类型**（numpy 标量请用 `float()/int()` 包一层）。
* 抛异常不会中断整个分析：引擎会捕获并把错误记进 `result["errors"]`。
* 通过 `ctx.metric(key, ...)` 声明指标的显示元信息（单位/小数位/分组），GUI 自动渲染。
"""
from __future__ import annotations

from typing import Any, Iterable


class Analyzer:
    """分析器基类。子类至少要有 `key`，并实现 `run()`。"""

    key: str = ""
    label: str = ""
    order: int = 100                 # 调度优先级（小到大）
    requires: tuple[str, ...] = ()   # 依赖的其它分析器 key
    enabled: bool = True             # 置 False 可临时停用

    def run(self, ctx) -> dict:      # pragma: no cover - 抽象
        raise NotImplementedError

    # -- 便利方法 --
    def __repr__(self) -> str:       # pragma: no cover
        return "<Analyzer %s>" % (self.key or type(self).__name__)


#: key -> Analyzer 实例
ANALYZERS: dict[str, Analyzer] = {}


def register(cls: type) -> type:
    """类装饰器：把分析器实例登记进注册表。"""
    inst = cls()
    if not inst.key:
        raise ValueError("%s 必须定义 key" % cls.__name__)
    if inst.key in ANALYZERS:
        raise ValueError("分析器 key 重复: %s" % inst.key)
    ANALYZERS[inst.key] = inst
    return cls


def get(name: str) -> Analyzer | None:
    return ANALYZERS.get(name)


def all_analyzers() -> list[Analyzer]:
    return list(ANALYZERS.values())


def enabled_analyzers() -> list[Analyzer]:
    return [a for a in ANALYZERS.values() if a.enabled]


def schedule(keys: Iterable[str] | None = None) -> list[Analyzer]:
    """按依赖拓扑序返回待执行的分析器；`keys` 为 None 表示全部启用项。"""
    pool = [a for a in enabled_analyzers() if keys is None or a.key in keys]
    names = {a.key for a in pool}
    done: list[Analyzer] = []
    seen: set[str] = set()
    pending = sorted(pool, key=lambda a: (a.order, a.key))

    while pending:
        progressed = False
        for a in list(pending):
            if all((d in seen) or (d not in names) for d in a.requires):
                done.append(a)
                seen.add(a.key)
                pending.remove(a)
                progressed = True
        if not progressed:                       # 存在环或缺依赖
            done.extend(pending)
            break
    return done
