"""撤销/重做的判据：**逐位**验证，不看"像不像"。

架构前提（`docs/editor_roadmap.md` P0）：文档模型是 `(源, 操作栈)`、渲染是纯函数 ⇒
撤销 = 弹栈、重做 = 压回。于是"撤销对不对"可以**直接量**：

1. 撤销到空栈的渲染输出与"从未编辑"的渲染输出 **sha256 相同**；
2. 重做后的输出与撤销前 **sha256 相同**（同一栈 ⇒ 同一字节）；
3. `to_dict → from_dict` 还原出的栈渲染出 **同样的字节**（可复算）。
"""
from __future__ import annotations

import hashlib
import json

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core.edit import (
    BandGainOp,
    DeclickOp,
    DeclipOp,
    DehumOp,
    EditSession,
    GainOp,
    Project,
    op_from_dict,
)

FS = 48000


def _material(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return 0.3 * np.sin(2 * np.pi * 440 * t) + 0.05 * np.sin(2 * np.pi * 3000 * t)


def _src(tmp_path, x=None, name="src.wav") -> str:
    p = tmp_path / name
    sf.write(str(p), _material() if x is None else x, FS, subtype="FLOAT")
    return str(p)


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ---------------------------------------------------------------- 栈语义
def test_apply_undo_redo_preserve_the_stack_exactly():
    """压栈/弹栈/压回的是**同一个对象**（dataclass 相等），深度账目同步。"""
    s = EditSession("src.wav")
    a, b = GainOp(0.0, 0.5, -3.0), GainOp(0.5, 1.0, -6.0)
    assert (s.can_undo, s.can_redo, s.undo_depth, s.redo_depth) == (False, False, 0, 0)
    s.apply(a)
    s.apply(b)
    assert s.ops == (a, b) and s.can_undo and not s.can_redo
    assert s.undo() is True and s.undo() is True
    assert s.ops == () and s.undo_depth == 0 and s.redo_depth == 2
    assert s.undo() is False                       # 空栈不抛异常
    assert s.redo() is True and s.redo() is True
    assert s.ops == (a, b) and s.redo_depth == 0
    assert s.redo() is False


def test_new_apply_clears_the_redo_stack():
    """分叉必须显式建模 ⇒ 新操作清空 redo（标准语义）。"""
    s = EditSession("src.wav")
    s.apply(GainOp(0.0, 0.5, -3.0))
    s.undo()
    assert s.can_redo
    s.apply(GainOp(0.0, 0.5, -9.0))
    assert not s.can_redo and s.redo_depth == 0


def test_session_ops_cannot_be_mutated_from_outside():
    """`project()` 返回**拷贝**：外部往它的 ops 上追加不会穿透回会话。"""
    s = EditSession("src.wav")
    s.apply(GainOp(0.0, 0.5, -3.0))
    p = s.project()
    p.ops.append(GainOp(0.5, 1.0, -6.0))
    assert len(s.ops) == 1


# ---------------------------------------------------------------- 逐位判据
def test_undoing_everything_renders_the_same_bytes_as_never_editing(tmp_path):
    """★ **撤销到空栈 ≡ 从未编辑**：两次渲染的 sha256 必须相同。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.5, 0.8, +3.0))
    baseline = str(tmp_path / "baseline.wav")
    from audiocheck.core.edit import render
    render(Project(src, []), baseline)
    while s.undo():
        pass
    edited = str(tmp_path / "edited.wav")
    s.render(edited)
    assert _sha(baseline) == _sha(edited)


def test_redo_reproduces_identical_bytes(tmp_path):
    """★ **重做后与撤销前逐字节相同**：栈相同 ⇒ 输出确定。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.2, 0.9, -2.0))
    before = str(tmp_path / "before.wav")
    s.render(before)
    h0 = _sha(before)
    s.undo()
    mid = str(tmp_path / "mid.wav")
    s.render(mid)
    assert _sha(mid) != h0, "撤销没有改变输出——判据失去意义"
    s.redo()
    after = str(tmp_path / "after.wav")
    s.render(after)
    assert _sha(after) == h0


def test_render_ignores_undone_ops_in_the_audit(tmp_path):
    """审计里只出现**当前栈**的操作（撤销掉的不许还挂在记录上）。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.5, 0.8, +3.0))
    s.undo()
    audit = s.render(str(tmp_path / "out.wav"))
    assert len(audit["ops"]) == 1 and audit["ops"][0]["gain_db"] == -6.0


# ---------------------------------------------------------------- 记录 / 可复算
def test_project_record_round_trip_is_bit_exact(tmp_path):
    """`to_dict → json → from_dict` 后**渲染同样的字节**（可复算的核心判据）。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.2, 0.5, -9.0))
    text = json.dumps(s.to_dict(), ensure_ascii=False)
    s2 = EditSession.from_dict(json.loads(text))
    assert s2.ops == s.ops
    a, b = str(tmp_path / "a.wav"), str(tmp_path / "b.wav")
    s.render(a)
    s2.render(b)
    assert _sha(a) == _sha(b)


def test_record_keeps_provenance_fields():
    """去嗡的 `f0_source` 与 `harmonics` 必须原样往返（来源记账不是装饰）。"""
    op = DehumOp(0.0, 1.0, 49.98, q=10.0, harmonics=(1, 2), f0_source="auto")
    back = op_from_dict(op.to_dict())
    assert back == op
    assert back.f0_source == "auto" and back.harmonics == (1, 2)
    assert isinstance(back.harmonics, tuple)


def test_record_is_strict_about_schema():
    """未知 kind / 多字段 / 少字段 / 版本不对 ⇒ 都**拒绝**（不许宽松解析）。"""
    with pytest.raises(ValueError, match="未知操作"):
        op_from_dict({"kind": "denoise", "t0": 0.0, "t1": 1.0})
    with pytest.raises(ValueError, match="多"):
        op_from_dict({"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -3.0, "q": 10})
    with pytest.raises(ValueError, match="少"):
        op_from_dict({"kind": "gain", "t0": 0.0, "t1": 1.0})
    with pytest.raises(ValueError, match="版本"):
        Project.from_dict({"version": 999, "source": "x.wav", "ops": []})
    with pytest.raises(ValueError, match="source"):
        Project.from_dict({"version": 1, "ops": []})
    with pytest.raises(ValueError, match="source"):
        Project.from_dict({"version": 1, "source": "", "ops": []})   # 空串也算缺


def test_every_op_kind_round_trips():
    """五类操作各自 `to_dict → op_from_dict` 后**逐字段相等**。"""
    ops = [
        GainOp(0.0, 1.0, -3.0),
        BandGainOp(0.0, 1.0, 100.0, 1000.0, -6.0),
        DeclipOp(0.0, 1.0, 0.9, 18000.0, bandwidth_source="given", iterations=40),
        DeclickOp(0.0, 1.0, 5.0, 4),
        DehumOp(0.0, 1.0, 50.0, 10.0, (1, 2, 3), "given"),
    ]
    for op in ops:
        assert op_from_dict(op.to_dict()) == op


def test_from_dict_leaves_redo_empty():
    """载入记录后只能"撤销"（把叫进来的栈弹掉），不能"重做"任何东西。"""
    d = Project("x.wav", [GainOp(0.0, 1.0, -3.0)]).to_dict()
    s = EditSession.from_dict(d)
    assert s.can_undo and not s.can_redo
