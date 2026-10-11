"""分析器包 —— **自动发现**。

把任何一个 `.py` 文件丢进本目录（内部用 `@register` 注册类）即可生效，
不需要改动 __init__、引擎或界面。下划线开头的文件会被跳过（放私有工具）。
"""
from __future__ import annotations

import importlib
import pkgutil


def load_all() -> list[str]:
    """导入本包下所有公开模块，触发 @register。返回已加载模块名。"""
    loaded = []
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_"):
            continue
        importlib.import_module("%s.%s" % (__name__, mod.name))
        loaded.append(mod.name)
    return loaded


#: 导入本包即完成注册（`from . import analyzers` 一处生效）
LOADED = load_all()

__all__ = ["load_all", "LOADED"]
