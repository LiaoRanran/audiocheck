"""审计报告（`core/edit/report.py`）的判据。

被测对象是**纯排版函数**：审计 dict → Markdown。判据分五组：

1. **纯函数**：同一条审计记录 ⇒ 两次排版逐字节相同（无时间戳/无随机/无本地化）；
2. **如实"未测"**：`None` 字段排成"未测"，绝不出现裸 `None`（"没量"≠"量了是零"）；
3. **不静默丢字段**：审计里有、标签表里没有的字段，**原键名照排**（渲染层将来新增的
   实测字段自动进报告，不被排版层吞掉）；
4. **真实渲染集成**：真渲染出来的审计（单算子 / 链式 / 抖动 / 时频）能排出完整报告——
   算法名、实测 dB、链式阶段表、规范化顺序、抖动警告、往返残差全部在场；
5. **落盘**：`write_report` 写出的文件内容与 `audit_report` 逐字节一致。
"""
from __future__ import annotations

import os
import sys

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import (  # noqa: E402
    BandGainOp,
    DeclickOp,
    DeclipOp,
    GainOp,
    Project,
    audit_report,
    render,
    write_report,
)

FS = 48000


def _write(path, x, subtype: str = "FLOAT") -> str:
    sf.write(str(path), x.astype(np.float32), FS, subtype=subtype)
    return str(path)


def _tone(seconds: float = 1.0, f: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * FS)) / FS
    return (0.5 * np.sin(2 * np.pi * f * t))[:, None]


# ------------------------------------------------------------------ 1. 纯函数
def test_report_is_a_pure_function():
    a = {"source": "a.wav", "dest": "b.wav", "samplerate": 48000, "channels": 1,
         "subtype": "FLOAT", "frames": 100, "duration_s": 0.002,
         "ops": [{"kind": "gain", "t0": 0.0, "t1": 0.1, "gain_db": -3.0,
                  "applied_frames": 100}]}
    assert audit_report(a) == audit_report(a)


# ------------------------------------------------------------------ 2. 如实"未测"
def test_none_fields_are_reported_as_weiced_and_never_leak():
    a = {"source": "a.wav", "dest": "b.wav", "samplerate": 48000, "channels": 1,
         "subtype": "FLOAT", "frames": 10, "duration_s": 0.0002,
         "peak_out_dbfs": None,
         "ops": [{"kind": "declip", "t0": 0.0, "t1": 0.1, "ceiling": None,
                  "out_of_band_measured_db": None, "applied_frames": 10}]}
    r = audit_report(a)
    assert "未测" in r, "None 必须排成'未测'"
    assert "None" not in r, "报告里不许泄漏裸 None：%r" % r


# ------------------------------------------------------------------ 3. 不静默丢字段
def test_unknown_fields_pass_through_with_their_key_names():
    """渲染层将来新增的实测字段（标签表没起中文名）必须**原键名照排**——排版层不许吞。"""
    a = {"source": "a.wav", "dest": "b.wav", "samplerate": 48000, "channels": 1,
         "subtype": "FLOAT", "frames": 10, "duration_s": 0.0002,
         "ops": [{"kind": "gain", "t0": 0.0, "t1": 0.1, "gain_db": -3.0,
                  "applied_frames": 10, "some_future_measured_field": 42}]}
    r = audit_report(a)
    assert "`some_future_measured_field`：42" in r, r


# ------------------------------------------------------------------ 4. 真实渲染集成
def test_single_declip_report_has_algorithm_and_measured_values(tmp_path):
    x = _tone()
    x[:10, 0] = 1.2                          # 制造饱和
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [DeclipOp(0.0, 1.0, 15000.0)]), str(tmp_path / "out.wav"))
    r = audit_report(audit)
    assert "audiocheck 编辑审计报告" in r
    assert "去削波" in r and "算法：`pocs_declip`" in r
    assert "削波电平来源" in r
    assert "能量比（实测）" in r and "dB" in r
    assert "带外能量变化（实测）" in r
    assert "| 源读取 | 完整 |" in r


def test_chained_report_has_stage_table_and_reorder_note(tmp_path):
    x = _tone()
    x[:10, 0] = 1.2
    src = _write(tmp_path / "in.wav", x)
    # 故意"乱序"给（gain 在前）：规范顺序会把它排到修复类之后 ⇒ 触发 reordered_from
    proj = Project(src, [GainOp(0.2, 0.8, -3.0), DeclipOp(0.0, 1.0, 15000.0)])
    audit = render(proj, str(tmp_path / "out.wav"))
    r = audit_report(audit)
    assert "## 链式渲染" in r
    assert "| 阶段 | 操作 | 算法 |" in r
    assert "顺序被规范化" in r
    assert "时间选区增益" in r and "去削波" in r
    assert "pocs_declip" in r               # 各阶段的算法在阶段表里在场


def test_dither_report_warns_about_full_requantization(tmp_path):
    x = _tone()
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [GainOp(0.0, 1.0, -1.0)]), str(tmp_path / "out.wav"),
                   subtype="PCM_16", dither=True)
    r = audit_report(audit)
    assert "TPDF" in r and "全文件重量化" in r, r
    assert "抖动" in r


def test_band_report_carries_the_roundtrip_floor(tmp_path):
    x = _tone(2.0, 3000.0)
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [BandGainOp(0.2, 0.8, 1000.0, 4000.0, -6.0)]),
                   str(tmp_path / "out.wav"))
    r = audit_report(audit)
    assert "频域往返残差" in r
    assert "不是位精确" in r
    assert "实改时频点" in r and "带内衰减（实测）" in r


def test_declick_report_carries_guard_and_detection_fields(tmp_path):
    x = _tone()
    x[500, 0] += 0.9                          # 单采样咔哒
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [DeclickOp(0.0, 1.0)]), str(tmp_path / "out.wav"))
    r = audit_report(audit)
    assert "去咔哒" in r and "算法：`residual2_mad2scale_step_cascade`" in r
    assert "检出采样" in r and "被宽度守卫挡下" in r


# ------------------------------------------------------------------ 5. 落盘
def test_write_report_file_matches_audit_report(tmp_path):
    x = _tone()
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [GainOp(0.0, 1.0, -2.0)]), str(tmp_path / "out.wav"))
    p = write_report(audit, str(tmp_path / "r.md"))
    assert os.path.exists(p)
    with open(p, encoding="utf-8") as fh:
        assert fh.read() == audit_report(audit)


# ------------------------------------------------- 变异轮：钉死排版细节（12 个存活的缺口）
def _mini(ops, **extra):
    d = {"source": "a.wav", "dest": "b.wav", "samplerate": 48000, "channels": 1,
         "subtype": "FLOAT", "frames": 48000, "duration_s": 1.0}
    d.update(extra)
    d["ops"] = ops
    return d


def test_op_title_needs_both_bounds():
    """题头的选区只在 **t0 与 t1 都在** 时拼接（`and`）：只有 t0 不许崩、也不许出半个区间。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.5, "gain_db": -1.0,
                             "applied_frames": 10}]))
    assert "时间选区增益" in r
    assert "–" not in r.split("### 1.")[1].split("\n")[0], "半个区间不许出现"


def test_frames_row_carries_duration_inline():
    """帧数行**内联时长**（`key == "frames"` 特判）：不许退化成两行独立计数。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 48000}]))
    assert "| 帧数 | 48000（1.0 s） |" in r, r
    assert "| 时长 |" not in r, "时长不许再单列一行"


def test_read_error_fallback_text_when_none():
    """截断审计的 `read_error=None` ⇒ 回落文案"记录在审计 JSON"（`or`），不许排成 None。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 10}],
                            read_incomplete=True, read_error=None,
                            frames_claimed=99999))
    assert "记录在审计 JSON" in r
    assert "None" not in r


def test_measured_db_suffix_gets_db_formatting():
    """任何以 `_measured_db` 结尾的字段都按 dB 排版（`endswith` 支路）：不许裸数。"""
    r = audit_report(_mini([{"kind": "declick", "t0": 0.0, "t1": 1.0,
                             "energy_ratio_measured_db": -0.023,
                             "applied_frames": 10}]))
    assert "- 能量比（实测）：-0.023 dB" in r, r


def test_fraction_is_percentage_times_100():
    """占比 = 小数×100 的百分比（`*`）：0.25 ⇒ 25.0000%，不是别的。"""
    r = audit_report(_mini([{"kind": "declick", "t0": 0.0, "t1": 1.0,
                             "fraction": 0.25, "applied_frames": 10}]))
    assert "- 占比：25.0000%" in r, r


def test_labeled_keys_are_not_duplicated_as_raw_keys():
    """起了中文名的字段**只出现一次**（passthrough 的 `in seen` 守卫）：不许再按原键名重排。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 10}]))
    assert "- 覆盖采样：10" in r
    assert "`applied_frames`" not in r, r


def test_stage_without_op_key_does_not_crash():
    """阶段表里**没有 `op` 键**的阶段也要排得出（`or {}` 回落），不许崩。"""
    r = audit_report(_mini([], chained=True,
                           stages=[{"index": 0, "algorithm": "x", "peak": -1.0}]))
    assert "| 0 |" in r and "x" in r


def test_reordered_list_is_numbered_from_one():
    """规范化顺序的编号从 **1** 起（`i + 1`）：不许从 0 起排。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 10}],
                           chained=True,
                           reordered_from=[{"kind": "gain", "t0": 0.0, "t1": 1.0}]))
    assert "1. 时间选区增益" in r, r


def test_single_op_report_has_no_raw_kind_line():
    """单算子报告只有"算法："行；`kind：` 行是**链式**专用（`chained and kind`）。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 10}]))
    assert "- 算法：" not in r            # gain 没有算法口径
    assert "- kind：" not in r, r


def test_op_sections_are_numbered_from_one():
    """操作题头编号从 **1** 起（`i + 1`）：`### 1.`，不许 `### 0.` / `### -1.`。"""
    r = audit_report(_mini([{"kind": "gain", "t0": 0.0, "t1": 1.0, "gain_db": -1.0,
                             "applied_frames": 10}]))
    assert "### 1. 时间选区增益" in r, r
    assert "### 0." not in r and "### -1." not in r


def test_poly_op_report_has_title_and_points():
    """poly_gain：中文题头 + 顶点数 + points 原样照排（顶点表=可审计的选区本身）。"""
    r = audit_report(_mini([{"kind": "poly_gain",
                             "points": [[0.3, 1000.3], [1.7, 1000.3],
                                        [1.7, 4500.7], [0.3, 4500.7]],
                             "gain_db": -6.0, "applied_tf_points": 42}]))
    assert "多边形选区增益（4 顶点）" in r, r
    assert "`points`" in r and "1000.3" in r


def test_report_shows_evidence_levels_and_digest(tmp_path):
    """★ 报告标注证据等级（实测/推导/设定）+ 证据锚（审计摘要/实现自证）。"""
    x = _tone()
    x[500, 0] += 0.9
    src = _write(tmp_path / "in.wav", x)
    audit = render(Project(src, [DeclickOp(0.0, 1.0)]), str(tmp_path / "o.wav"))
    r = audit_report(audit)
    assert "## 证据锚" in r
    assert "审计摘要（audit_digest）" in r and audit["audit_digest"][:8] in r
    assert "渲染实现自证（kernel_digest）" in r
    assert "〔实测〕" in r, "实测字段必须标注"
    assert "〔设定〕" in r, "设定参数必须标注（与测量结果区分）"
    assert "〔推导〕" in r, "推导字段必须标注"
    assert "〔实测〕= 本次渲染量出来的" in r


def test_evidence_levels_classify_core_fields():
    """★ `evidence_level` 的分类契约：实测/推导/设定/弃权各归其位。"""
    from audiocheck.core.edit.render import evidence_level
    assert evidence_level("energy_ratio_measured_db") == "L1_measured"
    assert evidence_level("detected_samples") == "L1_measured"
    assert evidence_level("fraction") == "L2_derived"
    assert evidence_level("threshold") == "L3_setting"
    assert evidence_level("gain_db") == "L3_setting"
    assert evidence_level("samplerate") == "L4_protocol"
    assert evidence_level("ambiguity_margin") == "L5_unknown"
