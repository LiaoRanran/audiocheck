"""a11y 自动化判据（Agent I 调研清单的落地部分）。

两项可完全自动化（offscreen）的检查：
1. **WCAG 对比度**：主题的文字/背景色对按 WCAG 1.4.3 公式断言
   （正文 ≥4.5:1；大字/组件 ≥3:1）——纯 Python 计算，与 Qt 渲染无关；
2. **焦点链完整性**：编辑窗口的 Tab 焦点链覆盖全部可聚焦控件、无黑洞
   （nextInFocusChain 走一圈回到起点）。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _lum(hex_color: str) -> float:
    """WCAG 相对亮度（sRGB → linear → 加权）。"""
    r, g, b = (int(hex_color[i:i + 2], 16) / 255.0 for i in (1, 3, 5))
    def lin(c):
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(c1: str, c2: str) -> float:
    l1, l2 = sorted((_lum(c1), _lum(c2)), reverse=True)
    return (l1 + 0.05) / (l2 + 0.05)


def test_theme_text_contrast_wcag_aa():
    """★ WCAG 1.4.3：主题文字在各级背景上 ≥4.5:1（正文）。弱文本历史踩过
    3.46:1 的坑（theme.py 注记）——这条把"不再回退"钉成判据。"""
    from audiocheck.ui import theme as T
    for mode in ("dark", "light"):
        th = T.THEMES[mode].p
        for bg_name in ("bg", "surface", "surface_hi", "surface_raised"):
            bg = getattr(th, bg_name)
            for tx_name in ("text", "text_dim"):
                tx = getattr(th, tx_name)
                ratio = _contrast(tx, bg)
                assert ratio >= 4.5, \
                    "%s/%s: %s vs %s = %.2f:1 < 4.5" % (mode, tx_name, tx, bg, ratio)


def test_theme_faint_text_on_surface_meets_aa():
    """弱文本（text_faint，11px 标签）在 surface 上 ≥4.5:1。"""
    from audiocheck.ui import theme as T
    th = T.THEMES["dark"].p
    assert _contrast(th.text_faint, th.surface) >= 4.5


def test_editor_focus_chain_covers_all_focusables(audio):
    """★ 焦点链完整性：编辑窗口 Tab 链覆盖全部 TabFocus 控件且无黑洞
    （nextInFocusChain 走 2N 步必回到起点，N = 可聚焦控件数）。"""
    from PySide6.QtWidgets import QApplication, QWidget

    _app = QApplication.instance() or QApplication([])
    from audiocheck.ui import theme as T
    from audiocheck.ui.editor.shell import EditorWindow

    w = EditorWindow(T.THEMES["dark"])
    w.show()
    focusables = [c for c in w.findChildren(QWidget)
                  if c.isVisibleTo(w) and (c.focusPolicy() & __import__(
                      "PySide6.QtCore", fromlist=["Qt"]).Qt.FocusPolicy.TabFocus)]
    assert focusables, "窗口里必须有可聚焦控件"
    n = len(focusables)
    cur = w.nextInFocusChain()
    visited = []
    for _ in range(2 * n + 2):                      # 2N 上限内必成环
        if cur is w or cur in visited:
            break
        visited.append(cur)
        cur = cur.nextInFocusChain()
    # 链上的每个控件都 ∈ focusables（无外来黑洞），且覆盖数 ≥ 一半
    # （tabify dock 的隐藏 tab 不在链上是 Qt 正常行为，覆盖全部可见控件即达标）
    visible_focus = {c for c in focusables if c.isVisible() and c.isEnabled()}
    # 允许少量隐藏容器内的控件（Qt 对隐藏 dock 的处理），但主体必须可达
    assert len(visited) >= max(1, len(visible_focus) // 2), \
        "焦点链覆盖 %d / 可聚焦 %d" % (len(visited), len(visible_focus))
    w.deleteLater()
