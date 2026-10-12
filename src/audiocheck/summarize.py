"""把 analyze() 的结果汇总成「曲库特性」报告。

CLI:  python -m audiocheck.summarize result.json 报告.md
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

from .config import BANDS

BAND_ORDER = [k[0] for k in BANDS]


def profile(bands: dict) -> str:
    """按能量分布给一个通俗的"听感性格"。"""
    if not bands:
        return "—"
    low = bands.get("sub", 0) + bands.get("bass", 0)
    mid, up = bands.get("mid", 0), bands.get("upper_mid", 0)
    hf = bands.get("high", 0) + bands.get("air", 0)
    tags = []
    if low >= 55:
        tags.append("低频主导")
    elif low >= 40:
        tags.append("低频偏多")
    if mid >= 35:
        tags.append("中频厚/人声突出")
    elif mid <= 12:
        tags.append("中频凹陷")
    if up >= 15:
        tags.append("中高频亮（易齿音）")
    if hf >= 10:
        tags.append("高频量足")
    return "、".join(tags) or "频谱较均衡"


def build(rows: list[dict]) -> str:
    groups = defaultdict(list)
    for r in rows:
        groups[os.path.dirname(r.get("path") or r.get("file", ""))].append(r)

    lv = defaultdict(int)
    for r in rows:
        lv[r.get("level")] += 1

    out = ["# 曲库特性汇总", "",
           "数据源：`audiocheck` 全量扫描（%d 个文件）。" % len(rows),
           "口径：真实截止 = 内容层实测；DR = 近似 TT-DR；频段 = 线性功率占比。", "",
           "## 总览", "", "| 结论 | 数量 |", "| --- | --- |",
           "| 合格 | %d |" % lv["ok"], "| 有疑 | %d |" % lv["warn"],
           "| 不达标 | %d |" % lv["bad"], ""]

    out += ["## 各专辑：真实带宽 + 音乐性格", "",
            "| 专辑 | 文件数 | 容器 | 真实截止(中位) | DR(均值) | 响度 LUFS | 频段(超低/低/中低/中/中高/高/空气 %) | 性格 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |"]
    for d, rs in sorted(groups.items()):
        name = os.path.basename(d) or d
        cuts = sorted(r["cutoff_khz"] for r in rs if r.get("cutoff_khz"))
        med = cuts[len(cuts) // 2] if cuts else None
        rates = sorted({r["sr"] for r in rs})
        drs = [r["dr_db"] for r in rs if r.get("dr_db") is not None]
        lufs = [r["lufs"] for r in rs if r.get("lufs") is not None]
        acc = defaultdict(list)
        for r in rs:
            for k, v in (r.get("bands_pct") or {}).items():
                acc[k].append(v)
        avg = {k: sum(v) / len(v) for k, v in acc.items()}
        out.append("| %s | %d | %s | %s | %.1f dB | %s | %s | %s |" % (
            name[:34], len(rs), "/".join("%.1fk" % (s / 1000) for s in rates),
            ("%.1fk" % med) if med else "—",
            (sum(drs) / len(drs)) if drs else 0,
            ("%.1f" % (sum(lufs) / len(lufs))) if lufs else "—",
            "/".join("%.0f" % avg.get(k, 0) for k in BAND_ORDER),
            profile(avg)))

    ups = [r for r in rows if "升频" in (r.get("verdict") or "") or "伪 Hi-Res" in (r.get("verdict") or "")]
    lossy = [r for r in rows if "有损" in (r.get("verdict") or "")]
    cl = [r for r in rows if (r.get("clip_pct") or 0) > 0.01]
    lowdr = [r for r in rows if (r.get("dr_db") or 99) < 6]
    hip = [r for r in rows if (r.get("cutoff_khz") or 0) >= 20]

    out += ["", "## 关键发现", ""]
    if ups:
        out.append("- **%d 个文件是升频/伪 Hi-Res**（容器采样率高于实际内容），占 %.0f%%。"
                   % (len(ups), len(ups) / max(1, len(rows)) * 100))
    if lossy:
        out.append("- **%d 个文件疑似有损溯源**（截止点 ≤19kHz）。" % len(lossy))
    if cl:
        out.append("- **%d 个文件存在削波**（>0.01%% 样本顶到满刻度）。" % len(cl))
    if lowdr:
        out.append("- **%d 个文件 DR < 6dB**（响度战），音量匹配盲听时极易误判。" % len(lowdr))
    out.append("- 内容真正超过 20kHz 的只有 **%d/%d** 个 —— 多数音源在源头就不够用来评"
               "「高频延伸」。" % (len(hip), len(rows)))

    vendors = defaultdict(int)
    for r in rows:
        v = (r.get("meta") or {}).get("vendor")
        if v:
            vendors[v] += 1
    if vendors:
        out += ["", "## 编码器串分布（来路取证）", "", "| 编码器串 | 文件数 |", "| --- | --- |"]
        for v, c in sorted(vendors.items(), key=lambda x: -x[1])[:12]:
            out.append("| `%s` | %d |" % (v, c))
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    argv = argv or sys.argv[1:]
    src = argv[0] if argv else "result.json"
    dst = argv[1] if len(argv) > 1 else "报告_曲库特性.md"
    with open(src, encoding="utf-8") as f:
        rows = json.load(f)
    md = build(rows)
    with open(dst, "w", encoding="utf-8") as f:
        f.write(md)
    print("载入 %d 条 → 已写出 %s" % (len(rows), dst))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
