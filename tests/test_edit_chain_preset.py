"""修复链预设（`ops.save_chain` / `ops.load_chain` + CLI `--chain`）的判据。

修复链 = **只含操作栈、不绑源**的 JSON——"配方"而不是"作品"（项目记录 = 源 + 栈）。
判据四组：
1. **往返**：op 对象 → 存 → 读 → 与原对象逐字段相同（同一 kind、同一参数）；
2. **严格**：版本不符 / 缺 ops / 空 ops / 未知 kind / 多余字段**全部拒绝**（与项目记录、
   `op_from_dict` 同口径——"同一条记录只能解出一种操作"）;
3. **不绑源**：链文件里**没有** source 字段（这是它和项目记录的本质差别，钉住）；
4. **CLI**：`--chain` 套到新源上渲染成功、与手写同栈的 `Project` 渲染**字节相同**；
   与算子 flag 互斥。
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np
import pytest
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    DeclickOp,
    DeclipOp,
    DehumOp,
    GainOp,
    Project,
    load_chain,
    render,
    save_chain,
)
from audiocheck.edit_cli import main  # noqa: E402

FS = 48000


def _tone(seconds: float = 1.0) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return (0.5 * np.sin(2 * np.pi * 440 * t))[:, None]


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _stack():
    return [DeclickOp(0.0, 1.0, 5.0, 4), DeclipOp(0.0, 1.0, 15000.0),
            DehumOp(0.2, 0.8, 50.0), GainOp(0.3, 0.9, -2.0)]


# ------------------------------------------------------------------ 1. 往返
def test_chain_roundtrip_preserves_ops(tmp_path):
    p = str(tmp_path / "c.json")
    stack = _stack()
    save_chain(p, stack)
    got = load_chain(p)
    assert len(got) == len(stack)
    for a, b in zip(got, stack):
        assert type(a) is type(b)
        assert a.to_dict() == b.to_dict()


# ------------------------------------------------------------------ 2. 严格
def test_chain_rejects_wrong_version(tmp_path):
    p = tmp_path / "c.json"
    p.write_text(json.dumps({"version": 99, "ops": [
        {"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p))


def test_chain_rejects_missing_or_empty_ops(tmp_path):
    # 用**当前版本**的链文件测 ops 校验（版本校验在前，别让用例走错分支）
    p1 = tmp_path / "noops.json"
    p1.write_text(json.dumps({"version": 2}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p1))
    p2 = tmp_path / "empty.json"
    p2.write_text(json.dumps({"version": 2, "ops": []}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p2))


def test_chain_v1_file_migrates_denops(tmp_path):
    """★ 历史 v1 链文件（denoise 无 track_floor）显式迁移 ⇒ 可载入且语义不变。"""
    p = tmp_path / "v1.json"
    p.write_text(json.dumps({"version": 1, "ops": [
        {"kind": "denoise", "t0": 0.0, "t1": 1.0, "noise_t0": 0.0, "noise_t1": 0.5,
         "reduction_db": 20.0, "n_fft": 4096, "hop": None}]}), encoding="utf-8")
    ops = load_chain(str(p))
    assert ops[0].track_floor is False and ops[0].to_dict()["track_floor"] is False


def test_chain_unknown_version_rejected(tmp_path):
    p = tmp_path / "v9.json"
    p.write_text(json.dumps({"version": 9, "ops": [
        {"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p))


def test_chain_rejects_unknown_kind_and_extra_fields(tmp_path):
    p1 = tmp_path / "unk.json"
    p1.write_text(json.dumps({"version": 1, "ops": [{"kind": "nonsense"}]}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p1))
    p2 = tmp_path / "extra.json"
    p2.write_text(json.dumps({"version": 1, "ops": [
        {"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0, "surprise": 1}]}),
        encoding="utf-8")
    with pytest.raises(ValueError):
        load_chain(str(p2))


# ------------------------------------------------------------------ 3. 不绑源
def test_chain_file_has_no_source_field(tmp_path):
    """链是配方：文件里**没有** source——这是它与项目记录的本质差别。"""
    p = tmp_path / "c.json"
    save_chain(p, _stack())
    d = json.load(open(p, encoding="utf-8"))
    assert "source" not in d
    assert set(d) == {"version", "ops"}


# ------------------------------------------------------------------ 4. CLI
def test_cli_chain_renders_same_bytes_as_handwritten_project(tmp_path):
    x = _tone()
    x[:10, 0] = 1.2                          # 制造饱和，让 declip 真干活
    src = _write(tmp_path / "a.wav", x)
    chain = str(tmp_path / "c.json")
    save_chain(chain, [DeclickOp(0.0, 1.0), DeclipOp(0.0, 1.0, 15000.0)])
    dst1 = str(tmp_path / "out1.wav")
    assert main([src, dst1, "--chain", chain]) == 0
    # 同一栈手写成 Project 渲染（规范顺序相同）⇒ 字节必须相同
    dst2 = str(tmp_path / "out2.wav")
    render(Project(src, [DeclickOp(0.0, 1.0), DeclipOp(0.0, 1.0, 15000.0)]), dst2)
    # ⚠ FLOAT WAV 的字节比较必须归一化 PEAK 时间戳（2026-10-26 实测的秒边界 flaky）
    from test_edit_cli import _bytes
    assert _bytes(dst1) == _bytes(dst2)


def test_cli_chain_conflicts_with_op_flags(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    chain = str(tmp_path / "c.json")
    save_chain(chain, [GainOp(0.0, 1.0, -1.0)])
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--chain", chain, "--gain", "-3", "--at", "0-1"]) == 2
    assert "同给" in capsys.readouterr().out


def test_cli_chain_rejects_bad_file(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    p = tmp_path / "c.json"
    p.write_text("{broken", encoding="utf-8")
    dst = str(tmp_path / "out.wav")
    assert main([src, dst, "--chain", str(p)]) == 2
    assert "拒绝" in capsys.readouterr().out


def test_cli_chain_accepts_audit_and_report(tmp_path, capsys):
    src = _write(tmp_path / "a.wav", _tone())
    chain = str(tmp_path / "c.json")
    save_chain(chain, [DeclickOp(0.0, 1.0)])
    dst = str(tmp_path / "out.wav")
    aj = str(tmp_path / "a.json")
    rp = str(tmp_path / "r.md")
    assert main([src, dst, "--chain", chain, "--audit", aj, "--report", rp]) == 0
    out = capsys.readouterr().out
    assert ("链    %s（1 个操作）" % chain) in out
    txt = open(rp, encoding="utf-8").read()
    assert "去咔哒" in txt
