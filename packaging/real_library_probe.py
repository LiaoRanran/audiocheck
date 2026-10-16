#!/usr/bin/env python3
"""real_library_probe：**真实曲库大盘点**（外部有效性）。

## 为什么入库

`docs/real_library.md` 的全部数字都由这个脚本产出——**报告里的数字必须能从仓库复算**，
否则那份报告就只是一段"我测过"的叙述（本项目最忌讳的形式）。三个模式：

    # ① 分布总账（读 CLI 的 --json 导出）
    python packaging/real_library_probe.py --json out/music_all.json --dist
    # ② 已关闭的假 Hi-Res 分类器在真实曲库上的误报率/检出率
    python packaging/real_library_probe.py --json out/music_all.json --fakehires
    # ③ 独立复核某一个文件（不看工具的 cutoff，自己算 Welch 谱）
    python packaging/real_library_probe.py --verify <音频路径>

## 口径（写在最前面，避免误读）

* 参照真值**不是**第三方标注：②的"真/假"用**本项目自己的截止点判据**分组
  （循环性必须在报告里写明）；③才是真正独立的量。
* 曲库来自**单一用户**（n=448），**不是抽样** ⇒ 基线率不可外推。
* 没有 `--strict`：这是**调查**不是门禁。要当门禁，得先有独立标注与第二个来源的曲库。

    python packaging/real_library_probe.py --json <cli 导出的 json> --dist --fakehires
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(_ROOT, "src"))


# ------------------------------------------------------------------ ① 分布总账
def _bucket(x, edges):
    if x is None:
        return "None"
    for lo, hi in zip(edges[:-1], edges[1:]):
        if lo <= x < hi:
            return "[%g,%g)" % (lo, hi)
    return ">=%g" % edges[-1]


def report_dist(rows) -> None:
    print("文件数 %d" % len(rows))
    print("\n== 判定分布 ==")
    for k, v in Counter(r.get("level") for r in rows).most_common():
        print("  %-6s %4d  (%.1f%%)" % (k, v, 100.0 * v / len(rows)))
    print("\n== 容器分布（采样率 / 容器位深）==")
    for k, v in Counter("%s/%s" % (r.get("sr"), r.get("bits")) for r in rows).most_common(10):
        print("  %-22s %4d" % (k, v))
    print("\n== 分析器告警 ==")
    errs = [r for r in rows if r.get("errors")]
    print("  有告警的文件 %d" % len(errs))
    for k, v in Counter(e.split(":")[0] for r in errs for e in r["errors"]).most_common():
        print("    %-10s %d" % (k, v))
    print("\n== 位深升位（伪 Hi-Res）==")
    print("  bit_padded=True %d / %d ｜ 名义位深 %s"
          % (sum(1 for r in rows if r.get("bit_padded")), len(rows),
             dict(Counter(r.get("bit_nominal") for r in rows))))
    print("\n== 截止点分布（kHz）==")
    edges = [0, 16, 18, 19.5, 20, 20.5, 21, 22, 23, 24, 30, 45, 100]
    for k, v in sorted(Counter(_bucket(r.get("cutoff_khz"), edges) for r in rows).items()):
        print("  %-10s %4d" % (k, v))
    print("  硬砖墙(≥24 dB/oct) %d ｜ 平缓(<8) %d ｜ 未检出 %d"
          % (sum(1 for r in rows if (r.get("brickwall_db_per_oct") or 0) >= 24),
             sum(1 for r in rows if r.get("brickwall_db_per_oct") is not None
                 and r["brickwall_db_per_oct"] < 8),
             sum(1 for r in rows if r.get("brickwall_db_per_oct") is None)))
    print("\n== 判定文案归类 ==")
    keys = ["满带宽", "轻微带限", "有损", "伪 Hi-Res", "半 Hi-Res", "升频", "响度战", "削波",
            "真 Hi-Res"]
    cnt = Counter()
    for r in rows:
        v = r.get("verdict") or ""
        for k in keys:
            if k in v:
                cnt[k] += 1
                break
        else:
            cnt["其它/未归类"] += 1
    for k, v in cnt.most_common():
        print("  %-12s %4d" % (k, v))
    print("\n== 反相（anti_scope）==")
    for k, v in Counter(r.get("anti_scope") for r in rows).most_common():
        print("  %-6s %4d" % (k, v))


# --------------------------------------------------- ② 假 Hi-Res 分类器（真实曲库）
def report_fakehires(rows) -> None:
    import numpy as np
    import soundfile as sf

    from audiocheck.core.analyzers.fakehires import fake_probability

    def prob(path):
        with sf.SoundFile(path) as f:
            fs, ch, frames = f.samplerate, f.channels, f.frames
            n = min(frames, int(fs * 20))
            if n < int(fs * 2):
                return None
            f.seek(max(0, (frames - n) // 2))
            x = f.read(n, dtype="float64", always_2d=True)
        return float(np.mean([fake_probability(x[:, c], fs) for c in range(ch)]))

    rows = [r for r in rows if r.get("level") and r.get("sr", 0) > 48000 and r.get("cutoff_khz")]
    print("参与统计（容器 >48k 且有截止点）：%d ｜ 参照『真』%d ｜ 参照『假』%d\n"
          % (len(rows), sum(1 for r in rows if r["cutoff_khz"] >= 24.5),
             sum(1 for r in rows if r["cutoff_khz"] < 24.5)))
    for label, group, want in (("真（该不报）", [r for r in rows if r["cutoff_khz"] >= 24.5], False),
                               ("假（该报）", [r for r in rows if r["cutoff_khz"] < 24.5], True)):
        ps = [(r, prob(r["path"])) for r in group]
        ps = [(r, p) for r, p in ps if p is not None]
        flagged = sum(1 for _r, p in ps if p >= 0.5)
        arr = np.array([p for _r, p in ps]) if ps else np.zeros(1)
        print("== 参照为%s ==" % label)
        print("  n=%d ｜ 报高(p≥0.5) %d ｜ **%s %.3f**（中位 p %.3f ｜ p10 %.3f ｜ p90 %.3f）"
              % (len(ps), flagged, "误报率" if not want else "检出率",
                 flagged / max(len(ps), 1), float(np.median(arr)),
                 float(np.percentile(arr, 10)), float(np.percentile(arr, 90))))
        print()
    print("⚠ 参照是**本项目自己的截止点判据**，不是第三方标注 ⇒ 只能说"
          "\"它不比截止点强、且两组分布与类别无关\"，不能说\"分类器准确率多少\"。")


# ------------------------------------------------------------- ③ 独立 Welch 复核
def verify_one(path, nperseg=16384, max_sec=30.0, nseg=3) -> None:
    import numpy as np
    import soundfile as sf
    from scipy import signal

    with sf.SoundFile(path) as f:
        fs, ch, frames = f.samplerate, f.channels, f.frames
        n = min(frames, int(fs * max_sec))
        starts = ([int(i * (frames - n) / max(1, nseg - 1)) for i in range(nseg)]
                  if frames > n else [0])
        tot = None
        for s in starts:
            f.seek(s)
            x = f.read(n, dtype="float64", always_2d=True)
            acc = None
            for c in range(ch):
                cc = x[:, c] - x[:, c].mean()
                fr, p = signal.welch(cc, fs=fs, nperseg=nperseg,
                                     noverlap=nperseg // 2, window="hann")
                acc = p if acc is None else acc + p
            tot = acc if tot is None else tot + acc
    f_, p = fr, tot / len(starts)
    db = 10 * np.log10(p + 1e-30)
    ref = float(np.percentile(db[f_ > 100], 90))

    def lvl(lo, hi):
        m = (f_ >= lo) & (f_ < hi)
        return (float(np.percentile(db[m], 90)) - ref) if m.any() else float("nan")

    print("  %s" % os.path.basename(path))
    print("    fs=%d ｜ 通带参考 %.1f dB（100Hz 以上的 90 分位）" % (fs, ref))
    print("    相对通带: 15–18k %+.1f ｜ 18–20k %+.1f ｜ 20–22k %+.1f ｜ 22–24k %+.1f ｜ "
          "24–30k %+.1f ｜ 30–45k %+.1f"
          % (lvl(15000, 18000), lvl(18000, 20000), lvl(20000, 22000),
             lvl(22000, 24000), lvl(24000, 30000), lvl(30000, 45000)))


def main() -> int:
    ap = argparse.ArgumentParser(description="真实曲库大盘点（外部有效性）")
    ap.add_argument("--json", help="CLI `--json` 导出的结果文件")
    ap.add_argument("--dist", action="store_true", help="打印分布总账")
    ap.add_argument("--fakehires", action="store_true", help="测假 Hi-Res 分类器")
    ap.add_argument("--verify", metavar="FILE", help="独立复核一个文件（自算 Welch）")
    a = ap.parse_args()

    if a.verify:
        print("== 独立 Welch 复核（不看工具的 cutoff）==")
        verify_one(a.verify)
        return 0
    if not a.json:
        ap.error("--dist / --fakehires 需要 --json")
    rows = json.load(open(a.json, encoding="utf-8"))["results"]
    if a.dist:
        report_dist(rows)
    if a.fakehires:
        report_fakehires(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
