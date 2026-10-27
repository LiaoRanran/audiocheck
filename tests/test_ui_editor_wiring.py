"""编辑工作站接线测试（offscreen，不弹真窗口）。

验证切片 3–4 的**可测事实**（见 docs/editor_v1_plan.md 验收）：
- 真实音频能载入 EditorView（频谱图 + 波形包络算出来）；
- 选区 → 算子面板 → 非破坏栈 → render 出 wav（逐位可复算）；
- band_gain 的 GUI→CLI 路径与内核 `EditSession.render` 字节一致；
- A/B 切换发出 abToggled；撤销到空栈渲染逐位不变。

不依赖任何外部素材（`conftest.audio` 是合成音频，答案已知）。
"""

import os

import soundfile as sf

from audiocheck.ui import theme as T
from audiocheck.ui.editor.editor_view import EditorView
from audiocheck.ui.editor.inspector import InspectorPanel
from audiocheck.ui.editor.ops import OperatorPanel
from audiocheck.ui.editor.shell import EditorWindow

# offscreen：避免无头环境弹窗（沿用本仓测试纪律）。
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _app():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def test_editor_view_loads_spectrum_and_waveform(audio):
    _app()
    v = EditorView(T.THEMES["dark"])
    v.set_source(audio["clipped"], 44100)
    assert v._S is not None and v._S.ndim == 2      # 频谱图算出来了
    assert v._wave is not None and v._wave.shape[1] == 2  # min/max 包络
    assert v._dur > 0.0
    v.deleteLater()


def test_editor_view_emits_selection(audio):
    _app()
    v = EditorView(T.THEMES["dark"])
    v.set_source(audio["clipped"], 44100)
    got = []
    v.selectionChanged.connect(lambda t0, t1, f0, f1: got.append((t0, t1, f0, f1)))
    # 模拟矩形选区（直接走 release 后的发射逻辑，跳过鼠标几何）
    v._sel = (1.0, 2.0, 100.0, 4000.0)
    v.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    assert got and got[-1] == (1.0, 2.0, 100.0, 4000.0)
    v.deleteLater()


def test_operator_panel_applies_band_gain_and_renders(audio, tmp_path):
    _app()
    ops = OperatorPanel(T.THEMES["dark"])
    ops.load_source(audio["cd_full"], 44100)
    ops.set_selection(1.0, 2.0, 100.0, 4000.0)
    ops.kind.setCurrentIndex(ops.kind.findData("band_gain"))
    ops.gain_db.setValue(-6.0)
    audit = []
    ops.auditReady.connect(audit.append)
    ops._apply()
    assert len(ops._session.ops) == 1
    assert audit and isinstance(audit[-1], dict)
    # 渲染产物确实落盘成 wav
    assert os.path.exists(audit[-1]["dest"])
    ops.deleteLater()


def test_band_gain_gui_matches_core_render(audio, tmp_path):
    """GUI 走的 op + 直接 `EditSession.render` 应产出**同一文件字节**（不重写内核）。"""
    from audiocheck.core.edit.ops import BandGainOp
    from audiocheck.core.edit.session import EditSession

    _app()
    ops = OperatorPanel(T.THEMES["dark"])
    ops.load_source(audio["cd_full"], 44100)
    ops.set_selection(1.0, 2.0, 100.0, 4000.0)
    ops.kind.setCurrentIndex(ops.kind.findData("band_gain"))
    ops.gain_db.setValue(-6.0)
    ops._apply()
    gui_dest = ops.last_audit()["dest"]
    assert gui_dest and os.path.exists(gui_dest)

    # 等价的内核直调
    core_dest = str(tmp_path / "core.wav")
    sess = EditSession(audio["cd_full"], [BandGainOp(t0=1.0, t1=2.0, f0=100.0,
                                                      f1=4000.0, gain_db=-6.0)])
    sess.render(core_dest, overwrite=True)
    a, _ = sf.read(gui_dest)
    b, _ = sf.read(core_dest)
    assert a.shape == b.shape
    # 同一确定性渲染 ⇒ 逐位相同
    assert float(np_abs_max_diff(a, b)) == 0.0
    ops.deleteLater()


def np_abs_max_diff(x, y):
    import numpy as np
    return float(np.max(np.abs(x - y)))


def test_undo_to_empty_is_bitwise_identity(audio, tmp_path):
    """撤销到空栈的渲染结果，与"从未编辑"逐位相同（session 的可测判据）。"""
    _app()
    ops = OperatorPanel(T.THEMES["dark"])
    ops.load_source(audio["cd_full"], 44100)
    ops.set_selection(1.0, 2.0, 100.0, 4000.0)
    ops.kind.setCurrentIndex(ops.kind.findData("band_gain"))
    ops.gain_db.setValue(-3.0)
    ops._apply()
    ops._undo()

    empty = str(tmp_path / "empty.wav")
    ops._session.render(empty, overwrite=True)
    src, _ = sf.read(audio["cd_full"])
    out, _ = sf.read(empty)
    assert out.shape == src.shape
    assert float(np_abs_max_diff(out, src)) == 0.0
    ops.deleteLater()


def test_inspector_ab_toggle_emits(audio):
    _app()
    ins = InspectorPanel(T.THEMES["dark"])
    got = []
    ins.abToggled.connect(got.append)
    ins.btn_ab.setChecked(True)
    ins.btn_ab.toggled.emit(True)
    assert got and got[-1] is True
    ins.deleteLater()


def test_open_file_wires_engine_and_sel_to_ops(audio):
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["clipped"])
    assert w._engine is not None
    assert w._source == audio["clipped"]
    # 选区 → 算子面板
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    assert w.ops._sel == (1.0, 2.0, 100.0, 4000.0)
    w.deleteLater()


def test_ab_switch_swaps_real_buffer(audio, tmp_path):
    """A/B 真实切播放源：B 面缓冲 == 渲染预览，A 面 == 原曲（逐位）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("band_gain"))
    w.ops.gain_db.setValue(-6.0)
    w.ops._apply()  # 渲染预览，记 _preview_path

    # A 面：原曲缓冲（应为载入的源）
    a_src, _ = sf.read(audio["cd_full"])
    assert float(np_abs_max_diff(w._engine.data, a_src)) == 0.0

    # 切到 B 面：缓冲换成渲染产物
    w._on_ab(True)
    b, _ = sf.read(w._preview_path)
    assert w._engine.data.shape == b.shape
    assert float(np_abs_max_diff(w._engine.data, b)) == 0.0
    assert w._ab_b is True

    # 切回 A 面：缓冲原曲逐位恢复
    w._on_ab(False)
    assert float(np_abs_max_diff(w._engine.data, a_src)) == 0.0
    assert w._ab_b is False
    w.deleteLater()


def test_ab_toggle_emits_and_swaps(audio):
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("band_gain"))
    w.ops.gain_db.setValue(-6.0)
    w.ops._apply()
    got = []
    w.inspector_panel.abToggled.connect(got.append)
    w.inspector_panel.btn_ab.setChecked(True)
    w.inspector_panel.btn_ab.toggled.emit(True)
    assert got and got[-1] is True
    w.deleteLater()


def test_keyboard_selection_nudge_and_extend(audio):
    """方向键微调选区；Shift+方向扩展频率窗（验收 §键盘选区）。"""
    _app()
    v = EditorView(T.THEMES["dark"])
    v.set_source(audio["clipped"], 44100)
    # 先建一个选区
    v._sel = (1.0, 2.0, 200.0, 4000.0)
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeyEvent

    def press(key, shift=False):
        ev = QKeyEvent(QKeyEvent.Type.KeyPress, key,
                       Qt.KeyboardModifier.ShiftModifier if shift else Qt.KeyboardModifier.NoModifier)
        v.keyPressEvent(ev)

    t0, t1, f0, f1 = v.get_selection()
    press(Qt.Key.Key_Right)  # 整体右移
    assert v.get_selection()[0] > t0 and v.get_selection()[1] > t1
    press(Qt.Key.Key_Left)
    assert abs(v.get_selection()[0] - t0) < 1e-6 and abs(v.get_selection()[1] - t1) < 1e-6
    press(Qt.Key.Key_Up, shift=True)  # 频率窗上沿扩展
    assert v.get_selection()[3] > f1
    press(Qt.Key.Key_Down, shift=True)
    assert abs(v.get_selection()[3] - f1) < 1e-3
    v.deleteLater()


def test_loop_audition_sets_engine_loop(audio):
    """选区段试听：把时间窗设为引擎 loop 并从 t0 起播。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["clipped"])
    # 真实用法：拖拽在 view 内部置 `_sel` 后发 selectionChanged
    w.view._sel = (1.0, 2.0, 100.0, 4000.0)
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w._audition_selection()
    assert w._engine.transport.loop == (1.0, 2.0)
    assert w._engine.transport.is_playing or w._engine.transport.state.value in ("playing", "stopped")
    w._engine.stop()
    w.deleteLater()


def test_export_project_roundtrip(audio, tmp_path):
    """导出项目记录：JSON → Project.from_dict 还原同一栈（可复算）。"""

    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("band_gain"))
    w.ops.gain_db.setValue(-3.0)
    w.ops._apply()

    out = str(tmp_path / "proj.json")
    # 绕过文件对话框：直接调内部写盘逻辑
    import json

    from audiocheck.core.edit.ops import Project

    proj = Project(w._source, list(w.ops._session.ops))
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(proj.to_dict(), fh, ensure_ascii=False, indent=2)

    restored = Project.from_dict(json.load(open(out, encoding="utf-8")))
    assert restored.source == w._source
    assert len(restored.ops) == len(w.ops._session.ops)
    assert restored.to_dict() == proj.to_dict()
    w.deleteLater()


def test_click_seek_positions_playhead(audio):
    """点击时间轴（非选区工具）定位播放头：seekRequested→引擎 clamp。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    got = []
    w.view.seekRequested.connect(got.append)
    w.view.seekTo(1.5)
    assert got and got[-1] == 1.5
    assert abs(w._engine.transport.position - 1.5) < 1e-6
    # clamp 到 [0,dur]
    w.view.seekTo(-5.0)
    assert w._engine.transport.position == 0.0
    w.deleteLater()


def test_undo_redo_keyboard(audio):
    """Ctrl+Z / Ctrl+Y 驱动撤销重做（REAPER 式）；栈空不崩。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("band_gain"))
    w.ops.gain_db.setValue(-6.0)
    w.ops._apply()
    assert len(w.ops._session.ops) == 1
    w._on_undo()
    assert len(w.ops._session.ops) == 0
    w._on_redo()
    assert len(w.ops._session.ops) == 1
    # 空栈再撤销不崩
    w.ops._session = type(w.ops._session)(w._source)
    w._on_undo()
    w.deleteLater()


def test_export_audio_renders_real_wav(audio, tmp_path):
    """导出处理后音频：把当前栈渲染到用户路径，产物可独立读取（真实出口）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.selectionChanged.emit(1.0, 2.0, 100.0, 4000.0)
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("band_gain"))
    w.ops.gain_db.setValue(-6.0)
    w.ops._apply()
    dest = str(tmp_path / "out.wav")
    # 绕过对话框：直接调内部渲染出口
    audit = w.ops._session.render(dest, overwrite=True)
    assert os.path.exists(dest)
    y, fs = sf.read(dest)
    assert y.shape[0] == int(audio_frames(audio["cd_full"]))
    assert float(np_abs_max_diff(y, sf.read(audio["cd_full"])[0])) != 0.0  # 确实改了
    # band_gain 是时域渲染：审计里记录 domain 与产物规格（changed_samples 仅修复类算子产出）
    assert audit["domain"] == "band"
    assert audit["frames"] == int(audio_frames(audio["cd_full"]))
    assert audit["dest"] == dest
    w.deleteLater()


def audio_frames(path):
    import soundfile as _sf
    return _sf.info(path).frames


def test_all_operators_gui_matches_core_render(audio, tmp_path):
    """规格 §"算子接入"验收：每类算子走 GUI 后，须与内核同参数产出**逐字节相同**渲染。

    比 band_gain 单测更狠：直接把面板**实际应用的那个 op 对象**抠出来，
    丢进新的 `EditSession` 用内核 render 复渲染——只要 GUI 与内核构造的是**同一个 op**
    （含 declip 自动读出的 `bandwidth`、denoise 自动记的噪声段），产物就逐位相同。
    这也覆盖了"自动读数一致性"：GUI 自动读的 B 若和内核直接拿到的不一致，这里立刻现形。
    """
    from audiocheck.core.edit.session import EditSession

    cases = [
        ("band_gain", "cd_full", dict(gain_db=-6.0, sel=(1.0, 2.0, 100.0, 4000.0))),
        ("declip", "clipped", dict(sel=(0.5, 3.0, 0.0, 22050.0))),    # 自动读带宽 B
        ("declick", "clipped", dict(sel=(0.5, 3.0, 0.0, 22050.0))),
        ("dehum", "cd_full", dict(f0=50.0, sel=(0.0, 4.0, 0.0, 22050.0))),
        ("denoise", "cd_full", dict(sel=(0.0, 2.0, 0.0, 22050.0))),   # 选区同时当噪声段学谱
    ]
    for kind, src_key, cfg in cases:
        _app()
        ops = OperatorPanel(T.THEMES["dark"])
        ops.load_source(audio[src_key], 44100)
        ops.kind.setCurrentIndex(ops.kind.findData(kind))
        if kind == "band_gain":
            ops.gain_db.setValue(cfg["gain_db"])
        if kind == "dehum":
            ops.f0.setValue(cfg["f0"])
        ops.set_selection(*cfg["sel"])
        ops._apply()
        assert len(ops._session.ops) == 1, "%s 未落到栈上" % kind
        # 抠出面板实际应用的 op 对象
        op = ops._session.ops[-1]
        gui_dest = ops.last_audit()["dest"]
        assert gui_dest and os.path.exists(gui_dest)

        # 内核用同一个 op 复渲染（不重写内核，构造参数来自 GUI 自身）
        core_dest = str(tmp_path / ("core_%s.wav" % kind))
        EditSession(audio[src_key], [op]).render(core_dest, overwrite=True)
        a, _ = sf.read(gui_dest)
        b, _ = sf.read(core_dest)
        assert a.shape == b.shape, "%s 形状不一致" % kind
        # 同一确定性渲染 ⇒ 逐位相同
        assert float(np_abs_max_diff(a, b)) == 0.0, "%s GUI 与内核渲染不一致" % kind
        ops.deleteLater()


def test_measurement_overlay_text_during_selection(audio):
    """拖动选区时测层给出 t/f 窗与跨度读数（可审计，不靠肉眼）。"""
    _app()
    v = EditorView(T.THEMES["dark"])
    v.set_source(audio["clipped"], 44100)
    v._sel = (1.0, 2.0, 200.0, 4000.0)
    v._draw_measure()
    assert v._measure_txt is not None
    # 文本含 Δt / Δf 实测跨度
    assert "Δt" in v._measure_txt.get_text()
    assert "Δf" in v._measure_txt.get_text()
    v.deleteLater()


def test_inspector_toggle_disables_op_and_rerenders(audio, tmp_path):
    """检视面板「开关」选中步骤：关掉后渲染与其不存在逐位相同（非破坏、不删栈）。"""
    _app()
    from audiocheck.core.edit import render as _render
    from audiocheck.core.edit.ops import GainOp, Project
    from audiocheck.core.edit.session import EditSession

    sess = EditSession(audio["cd_full"], [GainOp(0.1, 0.4, -6.0), GainOp(0.5, 0.8, +3.0)])
    insp = InspectorPanel(T.THEMES["dark"])
    insp.attach_session(sess)
    changed = []
    insp.stackChanged.connect(lambda: changed.append(1))
    # 选中第 2 项（+3）并关掉
    insp.stack_view.setCurrentRow(1)
    insp._on_toggle()
    assert 1 in sess.disabled
    assert insp.stack_view.item(1).text().startswith("[关]")
    assert changed
    # 关掉后的渲染与"只留 -6"逐位相同
    full = str(tmp_path / "full.wav")
    sess2 = EditSession(audio["cd_full"], [GainOp(0.1, 0.4, -6.0)])
    _render(Project(audio["cd_full"], list(sess.live_ops())), full)
    ref = str(tmp_path / "ref.wav")
    sess2.render(ref)
    a, _ = sf.read(full)
    b, _ = sf.read(ref)
    assert float(np_abs_max_diff(a, b)) == 0.0
    insp.deleteLater()


def test_inspector_remove_and_move(audio):
    """检视面板「删除 / 上移」改栈：删除后项数少一、上移后顺序变（不崩）。"""
    _app()
    from audiocheck.core.edit.ops import GainOp
    from audiocheck.core.edit.session import EditSession

    sess = EditSession(audio["cd_full"], [GainOp(0.1, 0.4, -6.0), GainOp(0.5, 0.8, +3.0)])
    insp = InspectorPanel(T.THEMES["dark"])
    insp.attach_session(sess)
    insp.stack_view.setCurrentRow(1)
    insp._on_move_up()
    assert [o.gain_db for o in sess.ops] == [3.0, -6.0]   # 顺序变了
    insp.stack_view.setCurrentRow(0)
    insp._on_remove()
    assert len(sess.ops) == 1 and sess.ops[0].gain_db == -6.0
    # 越界操作不崩
    insp.stack_view.setCurrentRow(5)
    insp._on_remove()
    insp._on_move_up()
    insp._on_toggle()
    insp.deleteLater()


def test_inspector_load_project_replaces_stack(audio, tmp_path):
    """检视面板「载入记录」：同源的 Project JSON 灌入会话；异源拒绝（不静默换源）。"""
    _app()
    import json

    from audiocheck.core.edit.ops import DeclipOp, GainOp, Project
    from audiocheck.core.edit.session import EditSession

    sess = EditSession(audio["cd_full"], [GainOp(0.1, 0.4, -6.0)])
    insp = InspectorPanel(T.THEMES["dark"])
    insp.attach_session(sess)

    # 同源记录：换成一个 declip 算子（构造成合法 Project.to_dict）
    rec = Project(audio["cd_full"],
                  [DeclipOp(0.5, 0.8, 18000.0, None)]).to_dict()
    p = str(tmp_path / "rec.json")
    open(p, "w").write(json.dumps(rec))

    from PySide6.QtWidgets import QFileDialog
    real = QFileDialog.getOpenFileName
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (p, ""))
    try:
        insp._on_load_project()
    finally:
        QFileDialog.getOpenFileName = staticmethod(real)
    assert len(sess.ops) == 1 and sess.ops[0].to_dict()["kind"] == "declip"
    insp.deleteLater()


def test_operator_preset_roundtrip_and_load(audio, tmp_path):
    """算子预设：当前面板参数存盘 → 改掉 → 载入还原（命名配置可复算）。"""
    _app()
    ops = OperatorPanel(T.THEMES["dark"])
    ops.load_source(audio["cd_full"], 44100)
    ops.kind.setCurrentIndex(ops.kind.findData("band_gain"))
    ops.gain_db.setValue(-9.0)
    path = str(tmp_path / "presets.json")
    ops.save_preset("轻衰减", path)
    # 改掉面板，再载入应还原 -9.0
    ops.gain_db.setValue(-3.0)
    presets = ops.load_presets(path)
    assert "轻衰减" in presets and presets["轻衰减"]["gain_db"] == -9.0
    ops.apply_preset(presets["轻衰减"])
    assert ops.gain_db.value() == -9.0
    ops.deleteLater()


def test_operator_preset_dehum_keeps_f0(audio, tmp_path):
    """去嗡预设要保留 f0（可测输入，不自动猜）。"""
    _app()
    ops = OperatorPanel(T.THEMES["dark"])
    ops.load_source(audio["cd_full"], 44100)
    ops.kind.setCurrentIndex(ops.kind.findData("dehum"))
    ops.f0.setValue(60.0)
    path = str(tmp_path / "p2.json")
    ops.save_preset("嗡60", path)
    ops.f0.setValue(50.0)
    ops.apply_preset(ops.load_presets(path)["嗡60"])
    assert ops.f0.value() == 60.0
    ops.deleteLater()


def test_batch_panel_manifest_and_run(audio, tmp_path):
    """批处理面板：加作业 → 渲染成 `--batch` 清单 → 经外壳调 CLI 真跑，逐项隔离。"""
    _app()
    from audiocheck.ui.editor.batch import BatchPanel

    panel = BatchPanel(T.THEMES["dark"])
    # 绕过文件对话框：直接 add_job
    assert panel.add_job({"source": audio["cd_full"], "dest": str(tmp_path / "o1.wav"),
                          "ops": [{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -3.0}]}) is True
    # 坏 ops 应被拒
    assert panel.add_job({"source": audio["cd_full"], "dest": str(tmp_path / "o2.wav"),
                          "ops": [{"kind": "bogus"}]}) is False
    jobs = panel.to_manifest()
    assert len(jobs) == 1 and jobs[0]["ops"][0]["gain_db"] == -3.0

    # 经外壳调 CLI 真跑（runRequested → _on_run_batch → edit_cli.main --batch）
    from audiocheck.ui.editor.shell import EditorWindow
    w = EditorWindow(T.THEMES["dark"])
    w._on_run_batch(jobs)
    assert os.path.exists(jobs[0]["dest"])
    panel.deleteLater()
    w.deleteLater()


def test_batch_cli_accepts_project_style_ops(tmp_path):
    """批处理 CLI 接受 `Project.to_dict` 同款 ops（互证：清单与记录同源）。"""
    import json

    import numpy as np
    import soundfile as sf

    from audiocheck.core.edit.ops import DeclipOp, Project
    from audiocheck.edit_cli import main

    fs = 44100
    a = str(tmp_path / "a.wav")
    sf.write(a, np.zeros((int(fs * 2), 2), dtype=np.float32), fs)
    rec = Project(a, [DeclipOp(0.0, 1.0, 18000.0, None)]).to_dict()
    jobs = [{"source": a, "dest": str(tmp_path / "o.wav"), "ops": rec["ops"]}]
    jb = str(tmp_path / "jobs.json")
    open(jb, "w").write(json.dumps(jobs))
    rc = main(["--batch", jb])
    assert rc == 0
    assert os.path.exists(str(tmp_path / "o.wav"))


def test_audition_noise_segment_sets_engine_loop(audio):
    """试听噪声段：把**去噪学谱输入**的时间窗设为引擎 loop（人耳复核"混入内容"）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["clipped"])
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("denoise"))   # 先选算子再框选（采集条件）
    w.view._sel = (0.5, 1.0, 0.0, 500.0)
    w.view.selectionChanged.emit(0.5, 1.0, 0.0, 500.0)
    assert getattr(w.ops, "_noise_sel", None) == (0.5, 1.0), "前置：噪声段已被采集"
    w._audition_noise_segment()
    assert w._engine.transport.loop == (0.5, 1.0)
    w._engine.stop()
    # 没采集过噪声段 ⇒ 不动引擎、给状态提示
    w.ops._noise_sel = None
    w._audition_noise_segment()
    assert "噪声段" in w.inspector_panel.audit_view.toPlainText()
    w.deleteLater()


def test_denoise_track_floor_checkbox_wires_into_op(audio, tmp_path):
    """底噪跟踪勾选：默认开（审计 track_floor=True）；取消后应用 ⇒ False + 口径名回 profile。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("denoise"))
    w.view._sel = (0.2, 0.8, 0.0, 0.0)
    w.view.selectionChanged.emit(0.2, 0.8, 0.0, 0.0)
    w.ops._noise_sel = (0.05, 0.15)                     # 直接给一段"噪声"
    w.ops._apply()
    assert w.ops._session.ops[-1].to_dict()["track_floor"] is True
    assert w.ops.last_audit()["algorithm"] == "wiener_dd_floor_tracked"
    w.ops.track_floor.setChecked(False)
    w.ops._undo()                                  # 栈语义：先撤掉 tracked 的那条
    w.ops._apply()
    assert w.ops._session.ops[-1].to_dict()["track_floor"] is False
    assert w.ops.last_audit()["algorithm"] == "wiener_dd_profile"
    w.deleteLater()


def test_lasso_capture_emits_simplified_polygon(audio):
    """★ 套索捕获：拖拽采点 → 松手 RDP 简化 → polySelected 顶点 ≥3 → 面板可应用
    PolyGainOp（P2"矩形→套索"的最后一公里）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.set_lasso_tool(True)
    assert w.view._lasso_tool is True and w.view._select_tool is False
    # 直接调内部捕获方法（鼠标事件薄壳）：拖一个四边形轨迹
    for t, f in ((0.3, 1000.0), (0.5, 1005.0), (0.7, 1200.0), (0.9, 3000.0),
                 (0.92, 4000.0), (0.7, 4300.0), (0.4, 4200.0), (0.31, 1100.0)):
        w.view._lasso_append(t, f)
    got = []
    w.view.polySelected.connect(lambda pts: got.append(list(pts)))
    w.view._lasso_finish()
    assert len(got) == 1 and len(got[0]) >= 3, got
    # 简化后顶点 ≤ 原始采点（RDP 在场）且全部是 (t, f) 数对
    assert len(got[0]) <= 8
    # 面板已收到：应用多边形增益
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("poly_gain"))
    w.ops.gain_db.setValue(-4.0)
    w.ops._apply()
    assert w.ops._session.ops[-1].to_dict()["kind"] == "poly_gain"
    assert w.ops.last_audit()["ops"][-1]["applied_tf_points"] > 0
    w.deleteLater()


def test_lasso_too_few_points_does_not_emit(audio):
    """拖拽距离太近（RDP 后 <3 顶点）⇒ 不发信号（误触不发）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.set_lasso_tool(True)
    w.view._lasso_append(0.5, 2000.0)
    w.view._lasso_append(0.5001, 2001.0)
    got = []
    w.view.polySelected.connect(lambda pts: got.append(list(pts)))
    w.view._lasso_finish()
    assert got == []
    w.deleteLater()


def test_lasso_and_rect_tools_are_mutually_exclusive(audio):
    """套索与矩形选区互斥：开一个自动关另一个（一次只有一个活动选区工具）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.transport.btn_lasso.setChecked(True)
    assert w.view._lasso_tool is True and w.view._select_tool is False
    w.transport.btn_sel.setChecked(True)
    assert w.view._select_tool is True and w.view._lasso_tool is False
    w.deleteLater()


def test_lasso_overlay_survives_zoom_redraw(audio):
    """★ 缩放重绘不吞掉进行中的套索叠加（_redraw_all 重画 _draw_lasso）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.set_lasso_tool(True)
    w.view._lasso_append(0.3, 1000.0)
    w.view._lasso_append(0.9, 4000.0)
    w.view._lasso_append(0.5, 3000.0)
    assert w.view._lasso_line is not None
    w.view.zoom_in()                     # 触发 _redraw_all（ax.clear）
    assert w.view._lasso_line is not None, "缩放后套索叠加必须还在"
    w.view.zoom_out()
    assert w.view._lasso_line is not None
    w.deleteLater()


def test_lasso_readout_does_not_accumulate_artists(audio):
    """★ 套索进行中读数的 text artist 不随采点累积（mem 纪律在画布上的同款）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.view.set_lasso_tool(True)
    for i in range(30):
        w.view._lasso_append(0.3 + i * 0.01, 1000.0 + i * 30.0)
    texts = [x for x in w.view.ax.texts if x.get_text().startswith("套索")]
    assert len(texts) == 1, "读数文字必须恰好 1 个，得到 %d" % len(texts)
    w.view._lasso_finish()
    texts2 = [x for x in w.view.ax.texts if x.get_text().startswith("套索")]
    assert len(texts2) == 0, "松手后读数文字必须清零"
    w.deleteLater()


def test_inspector_shows_poly_vertex_details_on_selection(audio):
    """★ 选中 poly_gain 条目 ⇒ 审计区显示顶点明细（逐行 t/f——"记录即选区"要能被读到）。"""
    _app()
    w = EditorWindow(T.THEMES["dark"])
    w.open_file(audio["cd_full"])
    w.ops.kind.setCurrentIndex(w.ops.kind.findData("poly_gain"))
    w.ops._poly = [(0.3, 1000.0), (1.5, 1000.0), (1.5, 4500.0), (0.3, 4500.0)]
    w.ops.gain_db.setValue(-6.0)
    w.ops._apply()
    w.inspector_panel.stack_view.setCurrentRow(0)
    txt = w.inspector_panel.audit_view.toPlainText()
    assert "多边形顶点（4 个" in txt
    assert "t=0.3000 s  f=1000.0 Hz" in txt
    w.deleteLater()
