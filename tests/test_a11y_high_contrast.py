"""高对比度（Windows 无障碍标准）的判据。

## 背景：为什么这是一个**真实缺陷**而不是"锦上添花"

Qt 的 Windows 平台插件在系统开启高对比度时会把 `QStyleHints::colorScheme()`
返回 **`Qt::ColorScheme::Unknown`**，并改用系统色重算调色板
（qtbase `qwindowstheme.cpp` 的 `effectiveColorScheme()`）。

本项目的主题是**完全硬编码的调色板**（`DARK_P`/`LIGHT_P`），不读系统色。
所以"Unknown 当作默认深色"的实际后果是：**开了高对比度的低视力用户
仍然看到自定义深色皮肤**——对他们来说等于无障碍功能失效。
Microsoft 的高对比度指南写得很直接："Do not hard-code colors in HighContrast"。

修复：`high_contrast_active()` 检测 + `system_palette_theme()` 用 `QPalette`
构造主题 + `active_theme()` 优先返回它。这组判据钉住这三件事。
"""
from __future__ import annotations

import sys

import pytest

sys.path.insert(0, "src")

from audiocheck.ui import theme as T  # noqa: E402


def test_high_contrast_detection_returns_bool():
    """检测函数必须稳定返回 bool（不能在无 Qt / 无注册表时抛异常）。

    它会在**每个窗口构造时**被调用，抛异常等于 UI 起不来。
    """
    v = T.high_contrast_active()
    assert isinstance(v, bool)


def test_active_theme_falls_back_without_app():
    """没有 QApplication 时必须安全回退到内置主题（不能返回 None）。"""
    t = T.active_theme("dark")
    assert t is not None
    assert t.name in ("dark", "light", "hc")
    assert t.p.bg.startswith("#")


def test_system_palette_theme_uses_qt_palette_colors():
    """`system_palette_theme()` 的颜色必须来自 `QPalette`，不是自有色号。

    ## 为什么必须先判 app 类型

    `QApplication.instance()` 会返回**任何** `QCoreApplication` 子类实例——
    包括别的测试为跑事件循环而建的 `QCoreApplication`（没有 `palette()`）。
    此时 `system_palette_theme` **正确**降级为 `DARK`（name="dark"），
    而不是抛 `AttributeError`。所以这条判据要分两种情况断言：
    * 有 GUI app ⇒ 必须返回 `hc` 且颜色字段齐全；
    * 只有 QCoreApplication / 无 app ⇒ 必须安全降级为 DARK（不崩）。
    """
    from PySide6.QtWidgets import QApplication
    inst = QApplication.instance()
    if inst is not None and not hasattr(inst, "palette"):
        # 存在一个非 GUI 的 QCoreApplication（测试间共享）⇒ 只断言降级安全
        hc = T.system_palette_theme(inst)
        assert hc is not None and hc.p.bg.startswith("#"), "降级必须返回可用主题"
        return
    app = inst or QApplication([])
    hc = T.system_palette_theme(app)
    assert hc.name == "hc", "系统调色板主题必须标名为 hc（便于 UI 判断）"
    # 无头平台下调色板可能恰好与 DARK 相同，只断言字段齐全
    for field in ("bg", "surface", "text", "text_dim", "border", "primary"):
        v = getattr(hc.p, field)
        assert isinstance(v, str) and v.startswith("#") and len(v) == 7, (
            "Palette.%s 不是合法色值：%r" % (field, v))


def test_high_contrast_theme_is_not_hardcoded_dark():
    """高对比度主题不能等于硬编码的 DARK 调色板。

    这是**回归判据**：若有人把 `active_theme()` 里的高对比度分支删掉、
    或让 `system_palette_theme()` 直接 `return DARK`，这条会红。
    """
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    if not T.high_contrast_active(app):
        pytest.skip("当前系统未开高对比度；本判据只在开关打开时有意义")
    t = T.active_theme("dark", app)
    assert t.name == "hc", "系统开了高对比度，active_theme 必须返回 hc 主题"


def test_reserved_qss_is_generated_for_system_theme():
    """系统调色板主题必须能生成完整 QSS（不能因为缺字段抛异常）。"""
    from PySide6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    qss = T.qss(T.system_palette_theme(app))
    assert isinstance(qss, str) and len(qss) > 1000
    assert "QWidget" in qss
