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
import os

import numpy as np
import pytest
import soundfile as sf

from audiocheck.core.edit import (
    BandGainOp,
    DeclickOp,
    DeclipOp,
    DehumOp,
    DenoiseOp,
    EditSession,
    GainOp,
    PolyGainOp,
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
    """文件 SHA256——**PEAK 时间戳归一化**（与 test_edit_cli._bytes 同口径）。

    FLOAT WAV 的 PEAK 块带创建时间戳，两次渲染跨秒边界必差 1 字节（2026-10-26
    实测的秒边界 flaky）。这里是同一判据口径的哈希版。
    """
    from test_edit_cli import _bytes
    return hashlib.sha256(_bytes(path)).hexdigest()


# ---------------------------------------------------------------- 栈语义
def _cache_glob():
    """前缀缓存文件的 glob 模式——**必须跟随实现**的缓存目录。

    `EditSession._cache_dir()` 可用 `AUDIOCHECK_PREFIX_CACHE_DIR` 覆盖（并行
    渲染/并行测试时各工作区一个目录）。测试若硬写 `tempfile.gettempdir()`，
    在变异工作区里就会扫到**别的工作区/历史残留**的文件，把"有界"判据
    误判成失败（实测：8 路并发下 `assert 9 <= 8`，实际本会话只写了 2 个）。
    """
    import os
    import tempfile
    d = os.environ.get("AUDIOCHECK_PREFIX_CACHE_DIR") or tempfile.gettempdir()
    return os.path.join(d, "_ac_prefix_*.wav")


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


# ---------------------------------------------------------------- 互操作栈编辑
def test_move_reorders_stack_and_is_noop_for_same_domain(tmp_path):
    """`move` 改变操作顺序；纯同域栈（多个 gain）渲染**顺序无关** ⇒ 移动后输出逐位不变。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.5, 0.8, +3.0))
    before = str(tmp_path / "before.wav")
    s.render(before)
    assert s.move(0, 1) is True
    assert [o.gain_db for o in s.ops] == [3.0, -6.0]          # 顺序真的变了
    after = str(tmp_path / "after.wav")
    s.render(after)
    assert _sha(before) == _sha(after)                          # 同域 ⇒ 恒等
    assert s.move(0, 0) is False                                # 没动
    assert s.move(0, 9) is True                                 # dst 越界夹到末位(1) ⇒ 真移动
    assert s.move(99, 0) is False                               # 源越界 ⇒ False


def test_remove_at_drops_op_without_undo_and_changes_output(tmp_path):
    """`remove_at` 删除一项（不进 undo 栈）；删后输出与"少一项"逐位相同。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.5, 0.8, +3.0))
    assert s.remove_at(0) is True
    assert len(s.ops) == 1 and s.ops[0].gain_db == 3.0
    # 删除不进 undo：撤销栈应清空（与 apply 同语义）
    assert s.redo_depth == 0
    # 与"只压一个 +3"的渲染逐位相同
    s2 = EditSession(src)
    s2.apply(GainOp(0.5, 0.8, +3.0))
    a, b = str(tmp_path / "rm.wav"), str(tmp_path / "one.wav")
    s.render(a)
    s2.render(b)
    assert _sha(a) == _sha(b)
    assert s.remove_at(99) is False                              # 越界


def test_toggle_enabled_excludes_op_from_render(tmp_path):
    """`toggle_enabled` 临时关掉某步：关掉后再渲染与"该步不存在"逐位相同，且不删栈。"""
    src = _src(tmp_path)
    s = EditSession(src)
    s.apply(GainOp(0.1, 0.4, -6.0))
    s.apply(GainOp(0.5, 0.8, +3.0))
    full = str(tmp_path / "full.wav")
    s.render(full)
    now_enabled = s.toggle_enabled(1)                            # 关掉 +3 那步
    assert now_enabled is False
    assert len(s.ops) == 2                                        # 栈没变
    assert 1 in s.disabled
    only_first = str(tmp_path / "only.wav")
    s.render(only_first)
    s2 = EditSession(src)
    s2.apply(GainOp(0.1, 0.4, -6.0))
    ref = str(tmp_path / "ref.wav")
    s2.render(ref)
    assert _sha(only_first) == _sha(ref)                         # 关掉的步骤确实没参与
    assert s.toggle_enabled(1) is True                           # 再开回来
    back = str(tmp_path / "back.wav")
    s.render(back)
    assert _sha(back) == _sha(full)                              # 开回来 ⇒ 与全量一致


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
        op_from_dict({"kind": "eq", "t0": 0.0, "t1": 1.0})
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
    """六类操作各自 `to_dict → op_from_dict` 后**逐字段相等**。"""
    ops = [
        GainOp(0.0, 1.0, -3.0),
        BandGainOp(0.0, 1.0, 100.0, 1000.0, -6.0),
        DeclipOp(0.0, 1.0, 0.9, 18000.0, bandwidth_source="given", iterations=40),
        DeclickOp(0.0, 1.0, 5.0, 4),
        DehumOp(0.0, 1.0, 50.0, 10.0, (1, 2, 3), "given"),
        DenoiseOp(0.0, 1.0, 0.0, 0.5, reduction_db=20.0, n_fft=4096, hop=None),
    ]
    for op in ops:
        assert op_from_dict(op.to_dict()) == op


def test_from_dict_leaves_redo_empty():
    """载入记录后只能"撤销"（把叫进来的栈弹掉），不能"重做"任何东西。"""
    d = Project("x.wav", [GainOp(0.0, 1.0, -3.0)]).to_dict()
    s = EditSession.from_dict(d)
    assert s.can_undo and not s.can_redo


def test_prefix_cache_mixed_domain_reuses_and_stays_recomputable(tmp_path):
    """★ 前缀缓存（混域栈）：连续两次渲染同一栈，第二次命中缓存；
    缓存产物与直渲**逐位相同**（混域栈本来就走 FLOAT 中间层，口径不变）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    d1 = str(tmp_path / "o1.wav")
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    a1 = sess.render(d1, overwrite=True)
    assert a1.get("prefix_cache_used", 0) >= 0
    d2 = str(tmp_path / "o2.wav")
    a2 = sess.render(d2, overwrite=True)
    assert a2.get("prefix_cache_hits", 0) > 0, "第二次渲染应命中前缀缓存"
    # 与"从源直渲同一栈"逐位一致（可复算承诺）
    d3 = str(tmp_path / "o3.wav")
    from audiocheck.core.edit import render
    render(Project(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)]), d3,
           overwrite=True)
    a = sf.read(d2, dtype="float64", always_2d=True)[0]
    b = sf.read(d3, dtype="float64", always_2d=True)[0]
    # 混域栈的 FLOAT 中间层允许 1-ULP(float32) 差（实测的非确定性口径）
    tol = np.finfo(np.float32).eps * 4.0 * float(np.abs(a).max() + 1e-12)
    assert float(np.abs(a - b).max()) <= tol, float(np.abs(a - b).max())


def test_prefix_cache_single_domain_bypasses(tmp_path):
    """同域栈**不走**前缀缓存（float64 直渲路径保 undo/redo 逐位承诺）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), GainOp(0.2, 0.8, -3.0)])
    d1 = str(tmp_path / "o1.wav")
    a1 = sess.render(d1, overwrite=True)
    assert "prefix_cache_hits" not in a1


# ------------------------------------------------- 前缀缓存单元钉死（变异轮缺口）
def test_prefix_files_keys_are_content_addressed(tmp_path):
    """★ `_prefix_files` 的键必须**内容寻址**：同栈同源 ⇒ 同路径；改任一算子参数
    ⇒ 该前缀之后的路径全部变（哈希链）；改源 ⇒ 全变。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    c1 = sess._prefix_files(list(sess.live_ops()))
    c2 = sess._prefix_files(list(sess.live_ops()))
    assert c1[1] == c2[1] and c1[2] == c2[2], "同栈同源必须同键"
    # 改参数 ⇒ 前缀 2 变（前缀 1 不变）
    sess.apply(GainOp(0.2, 0.8, -3.0))
    sess._ops[1] = DehumOp(0.0, 1.0, 60.0)
    c3 = sess._prefix_files(list(sess.live_ops()))
    # 注意：apply 后栈长 3，重建同长对比——替换 [1] 后前缀 1 相同、2+ 不同
    sess2 = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 60.0)])
    c3 = sess2._prefix_files(list(sess2.live_ops()))
    assert c3[1] == c1[1], "前缀 1 未变 ⇒ 键不变"
    assert c3[2] != c1[2], "参数变 ⇒ 后续前缀键全变（哈希链）"
    # 改源 ⇒ 全变
    src2 = _src(tmp_path, np.zeros((FS, 1)), name="src2.wav")
    sess3 = EditSession(src2, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    c4 = sess3._prefix_files(list(sess3.live_ops()))
    assert c4[1] != c1[1], "源变 ⇒ 全部键变"


def test_prefix_cache_files_are_bounded(tmp_path):
    """★ 缓存文件**有界**（_CACHE_LIMIT=8）：渲染后 tempdir 里的前缀文件 ≤ 上限。"""
    import glob
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    sess.render(str(tmp_path / "o.wav"), overwrite=True)
    files = glob.glob(_cache_glob())
    assert len(files) <= EditSession._CACHE_LIMIT


def test_move_and_remove_keep_render_recomputable(tmp_path):
    """★ `move`/`remove_at` 后渲染仍可复算（同栈 ⇒ 同输出；掩码下标跟随不破坏）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0),
                             GainOp(0.2, 0.8, -3.0)])
    sess.toggle_enabled(1)
    assert sess.move(0, 2) is True
    d1 = str(tmp_path / "o1.wav")
    a1 = sess.render(d1, overwrite=True)
    sess2 = EditSession(src, list(sess.ops))
    sess2._disabled = set(sess.disabled)
    d2 = str(tmp_path / "o2.wav")
    a2 = sess2.render(d2, overwrite=True)
    assert a1["frames"] == a2["frames"] and a1["ops"] == a2["ops"]
    # remove_at：删除后重渲不崩、帧数一致
    assert sess.remove_at(0) is True
    d3 = str(tmp_path / "o3.wav")
    sess.render(d3, overwrite=True)
    assert os.path.exists(d3)


# ------------------------------------------------- move/remove 边界（变异轮缺口）
def test_move_rejects_out_of_range_and_noop(tmp_path):
    """★ `move` 边界：负/越界 src ⇒ False；src == dst ⇒ False（`==` 早退在场）。"""
    src = _src(tmp_path, np.zeros((FS, 1)), name="mv_src.wav")
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    assert sess.move(-1, 0) is False
    assert sess.move(5, 0) is False
    assert sess.move(1, 1) is False, "src == dst 必须早退"


def test_remove_at_keeps_disabled_mask_consistent(tmp_path):
    """★ `remove_at` 的掩码收缩语义：删 idx 后，>idx 的禁用键前移、idx 本身消失。

    `k > idx`→`k-1` 的两处算术（`>` 严格、`-1` 前移）都钉在行为上。
    """
    src = _src(tmp_path, np.zeros((FS, 1)), name="rm_src.wav")
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0),
                             GainOp(0.2, 0.8, -3.0)])
    sess.toggle_enabled(2)                      # 禁用最后一个
    assert sess.disabled == {2}
    assert sess.remove_at(0) is True            # 删第 0 个 ⇒ 禁用键 2 → 1
    assert sess.disabled == {1}
    assert len(sess.ops) == 2


def test_prefix_cache_limit_is_enforced(tmp_path):
    """★ 缓存上限（_CACHE_LIMIT=8）：把 limit 压到 1，旧缓存文件被清理。"""
    import glob
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    old_limit = EditSession._CACHE_LIMIT
    try:
        EditSession._CACHE_LIMIT = 1
        sess.render(str(tmp_path / "o.wav"), overwrite=True)
        # 渲染后清理循环在 finally 里跑：tempdir 的 _ac_prefix_*.wav ≤ limit
        files = glob.glob(_cache_glob())
        assert len(files) <= 1
    finally:
        EditSession._CACHE_LIMIT = old_limit


def test_single_op_stack_renders_direct(tmp_path):
    """★ 单操作栈走**直渲**（`len(live) <= 1` 早退，不经缓存路径）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    sess = EditSession(src, [GainOp(0.0, 1.0, -2.0)])
    d = str(tmp_path / "o.wav")
    a = sess.render(d, overwrite=True)
    assert "prefix_cache_hits" not in a and "prefix_cache_used" not in a
    assert os.path.exists(d)


# ------------------------------------------------- 顶点级编辑（v1.1.1）
def test_replace_at_swaps_op_and_keeps_depth(tmp_path):
    """★ `replace_at`（新增）：就地替换一个算子（顶点编辑的底座）。

    非破坏语义不变：替换只是构造新 op，源文件只读；撤销深度不变（不是 apply/undo 的
    组合，避免污染 redo 栈）。
    """
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0)])
    assert s.replace_at(0, GainOp(0.0, 1.0, -6.0)) is True
    assert s.ops[0].gain_db == -6.0 and s.undo_depth == 1
    assert s.redo_depth == 0, "替换不该压 redo"
    assert s.replace_at(5, GainOp(0.0, 1.0, -1.0)) is False, "越界必须拒绝"


def test_poly_vertex_edit_is_recomputable(tmp_path):
    """★ 顶点编辑端到端：改一个顶点 ⇒ 新 op 替换 ⇒ 渲染结果随之改变且可复算。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0)])
    a1 = s.render(str(tmp_path / "o1.wav"), overwrite=True)
    s.apply(PolyGainOp([(0.0, 100.0), (1.0, 100.0), (1.0, 5000.0), (0.0, 5000.0)], -6.0))
    a2 = s.render(str(tmp_path / "o2.wav"), overwrite=True)
    assert a2["audit_digest"] != a1["audit_digest"]
    # 顶点编辑：把上边界从 5000 收到 2000 ⇒ 改动量必须下降
    edited = PolyGainOp([(0.0, 100.0), (1.0, 100.0), (1.0, 2000.0), (0.0, 2000.0)], -6.0)
    s.replace_at(1, edited)
    a3 = s.render(str(tmp_path / "o3.wav"), overwrite=True)
    assert a3["ops"][-1]["points"] == [[0.0, 100.0], [1.0, 100.0], [1.0, 2000.0], [0.0, 2000.0]]
    assert a3["audit_digest"] != a2["audit_digest"]


# ------------------------------------------------- 变异轮收口（7 个存活的针对性钉死）
def test_move_boundary_semantics(tmp_path):
    """★ `move` 边界三连：负 src 拒绝（`0 <= src` 的下界）、src==dst 拒绝（`==` 早退）、
    合法移动真的换位。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.5, -1.0), GainOp(0.5, 1.0, -2.0),
                          GainOp(1.0, 1.5, -3.0)])
    assert s.move(-1, 0) is False, "负 src 必须 False"
    assert s.move(3, 0) is False, "越界 src 必须 False"
    assert s.move(0, 0) is False, "src == dst 必须 False（`==` 早退）"
    assert s.move(2, 0) is True
    assert [o.gain_db for o in s.ops] == [-3.0, -1.0, -2.0], "移动后顺序必须正确"


def test_remove_at_shifts_disabled_mask_down(tmp_path):
    """★ `remove_at` 的掩码收缩算术（`k > idx → k-1`）：删 idx 后，右侧禁用键前移、
    idx 本身消失、左侧不动——三段行为都钉。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.5, -1.0), GainOp(0.5, 1.0, -2.0),
                          GainOp(1.0, 1.5, -3.0), GainOp(1.5, 2.0, -4.0)])
    s.toggle_enabled(0)                       # 禁用 0（idx 左侧，不动）
    s.toggle_enabled(2)                       # 禁用 2（idx 右侧，应前移到 1）
    s.toggle_enabled(3)                       # 禁用 3（应前移到 2）
    assert s.disabled == {0, 2, 3}
    assert s.remove_at(1) is True             # 删 idx=1
    assert s.disabled == {0, 1, 2}, "右侧键前移、左侧不动"


def test_prefix_hash_key_length_is_16(tmp_path):
    """★ 前缀键截断**恰好 16 个十六进制字符**（`hexdigest()[:16]`）：文件名含它。"""
    import re
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    c = s._prefix_files(list(s.live_ops()))
    for i, path in c.items():
        m = re.search(r"_ac_prefix_[0-9a-f]{16}_(\d+)_([0-9a-f]{16})\.wav$", path)
        assert m, "文件名必须含 16 位源键 + 前缀长 + 16 位内容键：%s" % path
        assert int(m.group(1)) == i, "文件名里的前缀长必须与键一致"


def test_cache_limit_nine_would_break_boundary(tmp_path):
    """★ 缓存上限=8 的**行为**钉法：造 9 个前缀文件（手工），渲染后必须只剩 ≤8。"""
    import glob
    import os
    src = _src(tmp_path, np.zeros((FS, 1)))
    # 手工塞 9 个假的旧前缀文件（模拟"超限"状态）
    stale = []
    for i in range(9):
        f = os.path.join(os.path.dirname(_cache_glob()), "_ac_prefix_deadbeef_%d_%s.wav"
                         % (i, "ab" * 8))
        open(f, "w").close()
        stale.append(f)
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    s.render(str(tmp_path / "o.wav"), overwrite=True)
    left = glob.glob(_cache_glob())
    assert len(left) <= 8, "渲染后缓存文件必须 ≤ _CACHE_LIMIT（8），得到 %d" % len(left)
    for f in stale:
        if os.path.exists(f):
            os.remove(f)


def test_single_op_renders_direct_even_with_two_ops_disabled(tmp_path):
    """★ `len(live) <= 1` 直渲界：两个算子但**一个被禁用** ⇒ live==1 ⇒ 仍走直渲
    （`<=` vs `<` 的边界：恰好 1 个 live 必须直渲）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(0.0, 1.0, 50.0)])
    s.toggle_enabled(1)                       # 禁用第二个 ⇒ live == 1
    d = str(tmp_path / "o.wav")
    a = s.render(d, overwrite=True)
    assert "prefix_cache_hits" not in a, "live==1 必须直渲（不经缓存）"
    assert os.path.exists(d)


def test_move_dst_clamp_is_strict_upper_bound(tmp_path):
    """★ `dst` 夹紧上界是 `len-1`（严格）：3 元素栈上 `move(2, 3)` 必须 False
    （`-1` → `+1` 变异会让它"成功移动到越界位置"）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.5, -1.0), GainOp(0.5, 1.0, -2.0),
                          GainOp(1.0, 1.5, -3.0)])
    assert s.move(2, 3) is False, "dst 越界（=len）必须 False"


def test_move_same_position_returns_false(tmp_path):
    """★ `src == dst` 早退（`==`）：同位移动必须 False（不是 True + 无操作）。"""
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.5, -1.0), GainOp(0.5, 1.0, -2.0)])
    assert s.move(0, 0) is False
    assert s.move(1, 1) is False


def test_remove_at_disabled_mask_shift_exact(tmp_path):
    """★ `remove_at` 掩码收缩**精确语义**：`k > idx → k-1`、idx 删除、`k < idx` 不动。

    变异 `> → >=` 会让"idx 本身被禁用"时错误地把 idx-1 也标记。构造：
    禁用 {1, 2}，删 idx=1 ⇒ 正确结果 {0→0 不动, 2→1} = {1}；变异会把 2 变 1 后
    又与 idx 冲突。用四算子+双禁用钉精确映射。
    """
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.25, -1.0), GainOp(0.25, 0.5, -2.0),
                          GainOp(0.5, 0.75, -3.0), GainOp(0.75, 1.0, -4.0)])
    s.toggle_enabled(1)                       # 禁用 idx=1
    s.toggle_enabled(3)                       # 禁用 idx=3
    assert s.disabled == {1, 3}
    assert s.remove_at(1) is True             # 删 idx=1
    # 期望：1 被删；3 → 2（前移）；0 不动
    assert s.disabled == {2}, "右侧键必须前移一位，左侧不动（得到 %s）" % s.disabled
    # **边界补强**：被删的 idx 本身**未被禁用**时，右侧禁用键仍要前移（`>` 严格性）
    s2 = EditSession(src, [GainOp(0.0, 0.25, -1.0), GainOp(0.25, 0.5, -2.0),
                           GainOp(0.5, 0.75, -3.0)])
    s2.toggle_enabled(2)                      # 只禁用 idx=2
    assert s2.remove_at(0) is True            # 删 idx=0
    assert s2.disabled == {1}, "删 0 后 2 → 1（得到 %s）" % s2.disabled


def test_single_live_op_renders_direct_even_when_cache_exists(tmp_path):
    """★ `len(live) <= 1` 直渲界（`<=` 语义）：live==1 必须**不经缓存路径**。

    变异 `<` 会让 live==1 也走缓存 ⇒ 同域 time 栈被 FLOAT 中间层引入 float32 量化
    （破坏"未编辑处逐位不变"）。判据：live==1 的渲染审计**无 prefix 字段**。

    ⚠ 两算子必须**不重叠**（时间上分离）：若重叠，`render()` 分派会让同域重叠
    走 `_render_chained`（v1.1.1 的语义），prefix 字段永远缺席 ⇒ 判据失去区分力。
    """
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 1.0, -2.0), DehumOp(2.0, 3.0, 50.0)])
    s.toggle_enabled(1)                       # live == 1（不重叠 ⇒ 单趟路径可达）
    d = str(tmp_path / "o.wav")
    a = s.render(d, overwrite=True)
    assert "prefix_cache_hits" not in a and "prefix_cache_used" not in a
    assert os.path.exists(d)


# ------------------------------------------------- 变异收口（对齐当前源码的 4 个存活）
def test_move_disabled_follows_moved_op(tmp_path):
    """★ `move` 的掩码跟随（`{dst if k == src else k}`）：移动**被禁用**的算子 ⇒
    禁用标记跟着走到新位置（`==→!=` 变异会让跟随反转）。

    3 元素栈、禁用 idx=1，move(1, 0) ⇒ 禁用应从 1 → 0（跟随算子）。
    """
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, [GainOp(0.0, 0.5, -1.0), GainOp(0.5, 1.0, -2.0),
                          GainOp(1.0, 1.5, -3.0)])
    s.toggle_enabled(1)
    assert s.disabled == {1}
    assert s.move(1, 0) is True
    assert s.disabled == {0}, "禁用标记必须跟随算子到新位置（得到 %s）" % s.disabled


def test_cache_limit_keeps_exactly_eight(tmp_path):
    """★ 缓存上限 8 的**清理语义**：10 个混域算子的栈渲染会产生 10 个前缀
    中间文件，`finally` 只保留最近修改的 `_CACHE_LIMIT` 个 ⇒ 渲染结束后
    tempdir 里 `_ac_prefix_*.wav` **恰好 8 个**（`8→9` 变异会留 9 个）。

    混域（Gain/Dehum 交替）才能走前缀缓存路径——同域栈不产生中间文件。
    """
    import glob
    ops = []
    for i in range(10):
        if i % 2 == 0:
            ops.append(GainOp(i * 0.1, i * 0.1 + 0.12, -1.0))
        else:
            ops.append(DehumOp(i * 0.1, i * 0.1 + 0.12, 50.0 + i))
    src = _src(tmp_path, np.zeros((FS, 1)))
    s = EditSession(src, ops)
    s.render(str(tmp_path / "o.wav"), overwrite=True)
    files = glob.glob(_cache_glob())
    assert len(files) == 8, "缓存上限应清理到恰好 8 个（得到 %d）" % len(files)
