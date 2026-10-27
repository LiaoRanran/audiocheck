"""i18n 基线判据（Agent I 路线第一步的可测部分）。

tr() 迁移的**不变量**：包装后（无 .qm 装载时）按钮文本必须与原字符串**逐位相同**
——tr() 在无翻译时返回原文 ⇒ 迁移永远不改变现有 UI 显示。这条保证迁移可以
逐模块渐进，不需要一次全量。
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def test_transport_tr_wrapped_buttons_keep_source_text():
    """★ tr() 无翻译时返回原文：迁移前后按钮文本逐位一致（迁移零风险判据）。"""
    from PySide6.QtWidgets import QApplication

    _app = QApplication.instance() or QApplication([])
    from audiocheck.ui import theme as T
    from audiocheck.ui.editor.transport import TransportBar

    bar = TransportBar(T.THEMES["dark"])
    # tr() 的字符串在 **tooltip**（按钮 text 是图标字形）
    assert bar.btn_play.toolTip() == "播放"
    assert bar.btn_stop.toolTip() == "停止"
    assert bar.btn_lasso.toolTip() == "套索工具（自由多边形选区）"
    bar.deleteLater()


def test_ts_baseline_covers_transport_strings():
    """★ .ts 基线覆盖 TransportBar 的全部 tr() 字符串（lupdate 抽取在场）。"""
    import xml.etree.ElementTree as ET

    ts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                      "i18n", "editor_zh.ts")
    root = ET.parse(ts).getroot()
    ctx = [c for c in root.findall("context")
           if c.find("name").text == "TransportBar"]
    assert ctx, "TransportBar 上下文必须在场"
    sources = {m.find("source").text
               for m in ctx[0].findall("message")}
    for want in ("播放", "暂停", "停止", "放大", "缩小", "复位缩放", "选区工具"):
        assert want in sources, want
