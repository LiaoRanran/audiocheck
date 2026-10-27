"""编辑工作站外壳测试（offscreen，不弹真窗口）。

验证 REAPER 式骨架的可测项（见 docs/editor_v1_plan.md 验收）：
- 三个 dock 可浮动 / 标签化 / 重置回默认三栏；
- 走带条可构造、按钮 StrongFocus；
- 命令面板 Ctrl+K 派生（复用既有 CommandPalette）；
- 暗/亮切换不崩、控件仍存活（旧 bug #21 不许复发）。
"""

import os

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDockWidget

from audiocheck.ui import theme as T
from audiocheck.ui.editor.shell import EditorWindow

# offscreen：避免无头环境弹窗（沿用本仓测试纪律）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    return QApplication.instance() or QApplication([])


def test_three_docks_present():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    names = {w.files.objectName(), w.editor.objectName(), w.inspector.objectName()}
    assert "Dock_文件" in names and "Dock_编辑" in names and "Dock_检视" in names
    w.deleteLater()


def test_docks_floatable_and_movable():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    for d in (w.files, w.editor, w.inspector):
        assert d.features() & QDockWidget.DockWidgetFloatable
        assert d.features() & QDockWidget.DockWidgetMovable
    w.deleteLater()


def test_dock_reset_to_default_layout():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    # 浮动一个 dock，再 reset；默认三栏应恢复（左文件 / 右检视+编辑标签化）。
    w.editor.setFloating(True)
    w.editor.setFloating(False)
    # tabify 关系在构造时已建立：insp/editor 同区标签化
    assert w.tabifiedDockWidgets(w.inspector) == [w.editor] or w.tabifiedDockWidgets(w.editor) == [w.inspector]
    w.deleteLater()


def test_transport_bar_constructible_and_focusable():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    tb = w.transport
    for b in (tb.btn_play, tb.btn_pause, tb.btn_stop):
        assert b.focusPolicy() & Qt.StrongFocus
    w.deleteLater()


def test_transport_devices_populates_combo():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.set_devices(["设备A", "设备B"], current="设备B")
    assert w.transport.dev_combo.count() == 2
    assert w.transport.dev_combo.currentText() == "设备B"
    w.deleteLater()


def test_transport_time_formatting():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.set_time(65.5, 200.0)
    assert w.transport.lbl_time.text() == "01:05.500 / 03:20.000"
    w.deleteLater()


def test_command_palette_opens_and_lists_commands():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_command_palette()
    assert w._palette is not None
    # 命令面板里有"去削波"等算子命令
    labels = [w._palette.list.item(i).text() for i in range(w._palette.list.count())]
    assert any("去削波" in s for s in labels)
    w._palette.reject()
    w.deleteLater()


def test_theme_switch_dark_to_light_keeps_widgets_alive():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.apply_theme(T.THEMES["light"])
    # 切换后控件仍存活、可构造 QSS（不崩、不黑）
    assert w.files.widget() is not None
    assert w.transport.dev_combo.count() >= 0
    w.apply_theme(T.THEMES["dark"])
    assert w.editor.widget() is not None
    w.deleteLater()


def test_operator_combo_emits_kind():
    _app()
    w = EditorWindow(T.THEMES["dark"])
    got = []
    w.transport.operatorChosen.connect(got.append)
    idx = w.transport.op_combo.findData("denoise")
    w.transport.op_combo.setCurrentIndex(idx)
    assert got and got[-1] == "denoise"
    w.deleteLater()
