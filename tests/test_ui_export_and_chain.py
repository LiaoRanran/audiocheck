"""导出配方对话框 + 修复链 GUI 出入口（offscreen）。

* 导出对话框：容器→子类型联动以 libsndfile 的回答为准（FLAC 里**没有** FLOAT 可选）；
  校验失败时 `_accept` 拒绝接受（`_cfg` 保持 None）。
* 修复链 GUI：`存修复链` 落盘无源 JSON；`载修复链` 逐个 apply 进会话并渲染预览——
  与内核 `save_chain/load_chain` 同一条路径，GUI 只是薄壳。
"""
from __future__ import annotations

import json
import os

from audiocheck.ui import theme as T
from audiocheck.ui.editor.shell import EditorWindow

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


# ------------------------------------------------------------------ 导出配方对话框
def test_export_dialog_refills_subtypes_by_format():
    _app()
    from audiocheck.ui.editor.export_dialog import build_export_dialog

    d = build_export_dialog()
    d.fmt.setCurrentText("WAV")
    wav_subs = [d.sub.itemText(i) for i in range(d.sub.count())]
    assert "FLOAT" in wav_subs and "PCM_24" in wav_subs
    d.fmt.setCurrentText("FLAC")
    flac_subs = [d.sub.itemText(i) for i in range(d.sub.count())]
    assert "FLOAT" not in flac_subs, "FLAC 的子类型下拉里不许出现 FLOAT（libsndfile 不收）"
    assert "PCM_24" in flac_subs
    d.deleteLater()


def test_export_dialog_rejects_dither_on_float():
    _app()
    from audiocheck.ui.editor.export_dialog import build_export_dialog

    d = build_export_dialog()
    d.fmt.setCurrentText("WAV")
    d.sub.setCurrentText("FLOAT")
    d.dither.setChecked(True)
    d._accept()
    assert d._cfg is None, "浮点+抖动必须被拒（内核 _check_dither 同口径）"
    d.dither.setChecked(False)
    d._accept()
    assert d._cfg == {"fmt": "WAV", "subtype": "FLOAT", "dither": False}
    d.deleteLater()


# ------------------------------------------------------------------ 修复链 GUI
def test_save_and_load_chain_through_shell(audio, tmp_path):
    _app()
    from PySide6.QtWidgets import QFileDialog

    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(0.2, 0.8, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("declick"))
    w.ops._apply()
    assert len(w.ops._session.ops) == 1

    chain = str(tmp_path / "c.json")
    real_save = QFileDialog.getSaveFileName
    QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (chain, ""))
    try:
        w._save_chain()
    finally:
        QFileDialog.getSaveFileName = staticmethod(real_save)
    assert os.path.exists(chain)
    d = json.load(open(chain, encoding="utf-8"))
    assert "source" not in d and d["ops"][0]["kind"] == "declick"   # 无源配方

    # 撤销到空栈，再载链 ⇒ 栈恢复 + 渲染预览发出审计
    w.ops._undo()
    assert len(w.ops._session.ops) == 0
    real_open = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (chain, ""))
    try:
        w._load_chain()
    finally:
        QFileDialog.getOpenFileName = staticmethod(real_open)
    assert len(w.ops._session.ops) == 1
    assert w.ops._session.ops[0].to_dict()["kind"] == "declick"
    assert w.ops.last_audit() is not None, "载链后必须渲染出预览审计"
    w.deleteLater()


def test_load_chain_rejects_broken_file_via_status(audio, tmp_path):
    _app()
    from PySide6.QtWidgets import QFileDialog

    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    real_open = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (str(bad), ""))
    try:
        w._load_chain()
    finally:
        QFileDialog.getOpenFileName = staticmethod(real_open)
    assert len(w.ops._session.ops) == 0, "坏链不许污染会话"
    w.deleteLater()
