"""审计报告：把 `render()` 返回的**审计记录**排成人可读、可存档的 Markdown。

## 为什么值得单独立一层

本项目的差异化定位是**可审计的修复**（`docs/editor_roadmap.md §2`）：竞品是黑盒——点了
"De-noise" 你不知道它改了什么。渲染层已经返回了机器可读的审计 dict（改了什么 / 依据什么 /
结果如何），但 dict 只对程序友好；**要给用户、给存档、给委托方看**，还需要一份
**纯机械排版**的报告：不新增任何判断、不改写任何数值、不隐藏任何字段。

三条纪律（与渲染层同源）
--------------------------
1. **机械生成**：本模块是审计 dict 的**纯函数**——同一条审计记录永远排出同一份报告
   （字段顺序固定、无时间戳、无本地化）。不引入任何"解读"。
2. **如实"未测"**：量具不可用（字段为 `None`）就写**未测**，绝不写成 0 或省略——
   "没量"和"量了是零"是两回事。
3. **不静默丢字段**：审计 dict 里有、而本模块没起中文名的字段，按原键名照排——
   渲染层将来新增的实测字段**自动**出现在报告里，而不是被排版层吞掉。
"""
from __future__ import annotations

import os

from ..context import io_path

#: 字段 → 中文标签 + 排版器（按算子分组）。**没进这张表的原样照排**（纪律 3）。
_OP_LABELS: dict[str, str] = {
    "applied_frames": "覆盖采样",
    "applied_tf_points": "实改时频点",
    "attenuation_measured_db": "带内衰减（实测）",
    "energy_ratio_measured_db": "能量比（实测）",
    "out_of_band_measured_db": "带外能量变化（实测）",
    "changed_samples": "实改采样",
    "detected_samples": "检出采样",
    "replaced_samples": "重建采样",
    "saturated_samples": "饱和采样",
    "fraction": "占比",
    "runs": "检出段数",
    "longest_run": "最长段",
    "rejected_runs": "被宽度守卫挡下",
    "residual_scale": "残差尺子（各声道）",
    "threshold": "检测阈值",
    "max_run": "宽度守卫",
    "ceiling": "削波电平",
    "ceiling_source": "削波电平来源",
    "bandwidth": "带宽先验",
    "bandwidth_source": "带宽来源",
    "iterations": "迭代次数",
    "band_limited": "带限",
    "noop": "整条跳过",
    "peak_in": "选区峰值",
    "f0": "基频",
    "f0_source": "基频来源",
    "q": "陷波 Q",
    "harmonics_used": "清掉的谐波",
    "skipped_harmonics": "越界跳过的谐波",
    "suppression_db": "各谐波实测压制",
    "noise_t0": "噪声段起",
    "noise_t1": "噪声段止",
    "noise_samples": "噪声段采样",
    "noise_frames": "学谱帧数",
    "processed_frames": "处理帧数",
    "processed_samples": "处理采样",
    "noise_reduction_measured_db": "纯噪声压制（实测）",
    "reduction_db": "压制上限",
    "g_min_db": "增益下限",
    "alpha_dd": "判决引导系数",
    "n_fft": "STFT 窗长",
    "hop": "STFT 跳步",
    "t0": "选区起",
    "t1": "选区止",
    "f1": "频率止",
    "gain_db": "增益",
}

#: 顶层概览字段 → 中文标签。
_TOP_LABELS: dict[str, str] = {
    "source": "源",
    "dest": "目标",
    "samplerate": "采样率",
    "channels": "声道",
    "subtype": "编码",
    "frames": "帧数",
    "duration_s": "时长",
    "peak_out_dbfs": "输出峰值",
    "clipped_frames": "整型钳位帧",
    "frames_claimed": "声称帧数",
}

#: 操作 kind → 中文题头（审计 dict 里只有 `to_dict` 的机器键，题头由这里机械映射）。
_KIND_TITLES: dict[str, str] = {
    "gain": "时间选区增益",
    "band_gain": "时频选区增益",
    "poly_gain": "多边形选区增益",
    "declip": "去削波",
    "declick": "去咔哒",
    "dehum": "去嗡",
    "denoise": "去噪",
}


def _op_title(rec: dict) -> str:
    """操作题头：中文 kind 名 + 选区（纯机械拼接，不加判断）。"""
    t = _KIND_TITLES.get(rec.get("kind", "?"), rec.get("kind", "?"))
    if "t0" in rec and "t1" in rec:
        t += " %.3f–%.3f s" % (rec["t0"], rec["t1"])
    if rec.get("kind") == "poly_gain" and isinstance(rec.get("points"), list):
        t += "（%d 顶点）" % len(rec["points"])
    return t


def _db(v) -> str:
    """dB 值排版：`None` ⇒ **未测**（纪律 2）。"""
    return "未测" if v is None else "%+.3f dB" % float(v)


def _num(v) -> str:
    if v is None:
        return "未测"
    if isinstance(v, bool):
        return "是" if v else "否"
    if isinstance(v, float):
        return "%.6g" % v
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_num(x) for x in v) + "]"
    return str(v)


def _overview(audit: dict) -> list[str]:
    out = ["## 概览", "", "| 项 | 值 |", "|---|---|"]
    for key, label in _TOP_LABELS.items():
        if key in audit:
            v = audit[key]
            if key == "frames" and audit.get("samplerate"):
                out.append("| %s | %s（%s s） |" % (label, v, audit.get("duration_s", "未测")))
                continue
            if key == "duration_s":
                continue                      # 已并进帧数行
            out.append("| %s | %s |" % (label, _num(v)))
    read = ("**读不满**（见下）" if audit.get("read_incomplete")
            else "完整" if "read_incomplete" in audit else None)
    if read is not None:
        extra = ""
        if audit.get("read_incomplete"):
            extra = "；error=%s" % (audit.get("read_error") or "（记录在审计 JSON）")
        out.append("| 源读取 | %s%s |" % (read, extra))
    if audit.get("dither"):
        out.append("| 抖动 | TPDF ±1 LSB（LSB=%s）⚠ **全文件重量化**：未编辑处不再逐位不变 |"
                   % _num(audit.get("dither_lsb")))
    return out


#: 证据等级 → 中文短标签（报告里跟在数值后面）。
_LEVEL_LABEL = {"L1_measured": "实测", "L2_derived": "推导", "L3_setting": "设定",
                "L4_protocol": "协议", "L5_unknown": "弃权"}


def _op_lines(rec: dict, audit_levels: dict | None = None) -> list[str]:
    """一个操作的审计 dict → 报告行（**按固定键序**；未知键原样照排）。"""
    out = []
    seen = {"kind"}
    for key, label in _OP_LABELS.items():
        if key not in rec or key in seen:
            continue
        seen.add(key)
        v = rec[key]
        if key == "attenuation_measured_db" or key.endswith("_measured_db"):
            txt = _db(v)
        elif key == "fraction":
            txt = "未测" if v is None else "%.4f%%" % (100.0 * float(v))
        else:
            txt = _num(v)
        # 证据等级后缀（L1 实测 / L2 推导 / L3 设定）——让读者一眼看出这条数字的性质
        lvl = (audit_levels or {}).get(key)
        if lvl in ("L1_measured", "L2_derived", "L3_setting"):
            txt += "  〔%s〕" % _LEVEL_LABEL.get(lvl, lvl)
        out.append("- %s：%s" % (label, txt))
    for key in sorted(rec):                    # 未起中文名的字段原样照排（纪律 3）
        if key in seen or key.startswith("_"):
            continue
        seen.add(key)
        out.append("- `%s`：%s" % (key, _num(rec[key])))
    return out


def audit_report(audit: dict) -> str:
    """审计 dict → Markdown 报告（**纯函数**；同一条记录 ⇒ 同一份报告）。"""
    out = ["# audiocheck 编辑审计报告", "",
           "> 本报告由审计记录**机械生成**：不新增判断、不改写数值。"
           "所有 dB/样本数都是**实测**；“未测”= 该量具在此操作上不可用。", ""]
    out += _overview(audit)
    ops = audit.get("ops") or []
    if audit.get("chained"):
        out += ["", "## 链式渲染", "",
                "本工程混用多个域的算子，按规范顺序（`render.PHASE`）**逐算子**链式渲染，"
                "每步落一个中间文件（FLOAT）。"]
        stages = audit.get("stages") or []
        if stages:
            out += ["", "| 阶段 | 操作 | 算法 | 峰值 (dBFS) |", "|---|---|---|---|"]
            for st in stages:
                op = st.get("op") or {}
                out.append("| %s | %s | %s | %s |" % (
                    st.get("index", "未测"), _op_title(op),
                    st.get("algorithm") or "—", _num(st.get("peak"))))
        if audit.get("reordered_from"):
            out += ["", "**顺序被规范化**。原始顺序："]
            out += ["%d. %s" % (i + 1, _op_title(r))
                    for i, r in enumerate(audit["reordered_from"])]
    if ops:
        out += ["", "## 操作（%d 个）" % len(ops)]
        for i, rec in enumerate(ops):
            algo = audit.get("algorithm") if len(ops) == 1 else None
            out += ["", "### %d. %s" % (i + 1, _op_title(rec))]
            if algo:
                out += ["- 算法：`%s`" % algo]
            elif audit.get("chained") and rec.get("kind"):
                out += ["- kind：`%s`" % rec["kind"]]
            out += _op_lines(rec, audit.get("evidence_levels"))
    if audit.get("audit_digest") or audit.get("kernel_digest"):
        out += ["", "## 证据锚", "",
                "| 项 | 值 |", "|---|---|",
                "| 审计摘要（audit_digest） | `%s` |" % (audit.get("audit_digest") or "未测"),
                "| 渲染实现自证（kernel_digest） | `%s` |" % (audit.get("kernel_digest") or "未测")]
        out += ["", "> 数值后的〔实测〕= 本次渲染量出来的；〔推导〕= 由实测确定性算出；"
                    "〔设定〕= 调用方给的参数（不是测量结果）。"]
    if "roundtrip_floor_dbfs" in audit:
        out += ["", "## 频域往返残差", "",
                "时频编辑**不是位精确的**：选区时段内未受影响采样的 STFT 往返残差实测为 "
                "**%s dBFS**（在 %s 个采样上量得）。这是本法底线，不是缺陷。"
                % (_db(audit.get("roundtrip_floor_dbfs")).replace("+", ""),
                   _num(audit.get("roundtrip_floor_samples")))]
    if audit.get("read_incomplete"):
        out += ["", "## ⚠ 源读取不完整", "",
                "源文件声称 %s 帧，实际读不满（读到 %s 帧）。已读部分照常写出——"
                "这是显式降级，不是静默截断。"
                % (_num(audit.get("frames_claimed")), _num(audit.get("frames")))]
    return "\n".join(out) + "\n"


def write_report(audit: dict, path: str) -> str:
    """把报告写到 `path`（经 `io_path` 落盘），返回绝对路径。**纯排版**，不做任何计算。"""
    p = io_path(os.path.abspath(path))
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(audit_report(audit))
    return p
