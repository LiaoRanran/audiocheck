"""命令行入口：audiocheck / aca-check。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import Thresholds
from .core.engine import analyze, collect_files, list_analyzers

ICON = {"ok": "[OK] ", "warn": "[!]  ", "bad": "[X]  "}


def _fmt(v, unit=""):
    return "—" if v is None else "%s%s" % (v, unit)


def show(r: dict, quiet: bool = False) -> None:
    print("%s%s" % (ICON.get(r.get("level"), "     "), r["file"]))
    print("      容器  %s %.1fkHz/%s  %d:%02d  %.2fMB  %d声道%s" % (
        r["format"], r["sr"] / 1000, r["bits"], r["seconds"] // 60, r["seconds"] % 60,
        r["size_mb"], r["channels"], "" if not r.get("kbps") else "  ≈%d kbps" % r["kbps"]))
    if r.get("cutoff_hz"):
        print("      带宽  截止 %.2fkHz（奈奎斯特 %.1fkHz，占 %.0f%%）%s" % (
            r["cutoff_khz"], r["sr"] / 2000, (r.get("cutoff_ratio") or 0) * 100,
            "" if not r.get("brickwall_db_per_oct") else "  砖墙 %.0fdB/oct" % r["brickwall_db_per_oct"]))
    print("      电平  峰值 %s ｜ RMS %s ｜ DR %s ｜ 削波 %s" % (
        _fmt(r.get("peak_dbfs")), _fmt(r.get("rms_dbfs")), _fmt(r.get("dr_db")), _fmt(r.get("clip_pct"))))
    if r.get("lufs") is not None:
        print("      响度  %.1f LUFS ｜ LRA %.1f LU ｜ 真峰值 %s ｜ PLR %s" % (
            r["lufs"], r.get("lra") or 0, _fmt(r.get("true_peak_dbfs"), " dBTP"),
            _fmt(r.get("plr"), " dB")))
    if r.get("bands_pct"):
        from .config import BAND_CN, BANDS
        print("      平衡  " + "  ".join("%s %.0f%%" % (BAND_CN[k], r["bands_pct"].get(k, 0))
                                         for k, _, _ in BANDS))
    md = r.get("meta") or {}
    if md.get("vendor") or (md.get("fields") or {}):
        from .core.analyzers.metadata import brief, tag_rows
        print("      标签  %s" % brief(md))
        for k, v in tag_rows(md)[:6]:
            print("            %-14s %s" % (k, str(v)[:70]))
    if r.get("verdict"):
        print("      判定  %s" % r["verdict"])
    if r.get("advice"):
        print("      建议  %s" % r["advice"])
    for e in r.get("errors") or []:
        print("      ! 分析器告警: %s" % e)
    if not quiet:
        print()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="audiocheck",
        description="音源质量检测：看内容，不看标签（带宽 / 响度 / 元数据 / 音乐特性）")
    p.add_argument("targets", nargs="*", help="音频文件或目录")
    p.add_argument("--list", dest="listfile", default=None,
                   help="从文本文件读取路径清单（每行一个，# 为注释）——路径含 [ ] 等字符时推荐")
    p.add_argument("--json", default=None, help="把完整结果写入 JSON")
    p.add_argument("--csv", default=None, help="导出汇总 CSV")
    p.add_argument("--plot", default=None, help="为每个文件输出频谱图到指定目录")
    p.add_argument("--quiet", action="store_true", help="紧凑输出")
    p.add_argument("--shallow", action="store_true", help="跳过频谱与响度（只做容器与电平）")
    p.add_argument("--only", default=None, help="只跑指定分析器（逗号分隔）")
    p.add_argument("--config", default=None, help="从 JSON 载入自定义阈值")
    p.add_argument("--list-analyzers", action="store_true", help="列出所有已注册分析器")
    p.add_argument("--version", action="version", version="audiocheck %s" % __version__)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_analyzers:
        print("%-14s %-26s %-7s %s" % ("key", "说明", "order", "依赖"))
        for a in list_analyzers():
            print("%-14s %-26s %-7d %s" % (a["key"], a["label"], a["order"],
                                           ", ".join(a["requires"]) or "-"))
        return 0

    targets = list(args.targets)
    if args.listfile:
        with open(args.listfile, encoding="utf-8") as f:
            targets += [ln.strip() for ln in f if ln.strip() and not ln.startswith("#")]
    files = collect_files(targets)
    if not files:
        print("没找到可分析的音频文件", file=sys.stderr)
        return 2

    th = Thresholds.load(args.config) if args.config else Thresholds()
    only = [s.strip() for s in args.only.split(",")] if args.only else None
    print("待检测 %d 个文件\n" % len(files))

    rows = []
    for p in files:
        try:
            r = analyze(p, deep=not args.shallow, thresholds=th, only=only)
        except Exception as e:                                     # noqa: BLE001
            print("[X]  %s —— 读取失败：%s" % (os.path.basename(p), e))
            continue
        rows.append(r)
        show(r, args.quiet)
        if args.plot:
            _plot(r, args.plot)

    n = len(rows)
    ok = sum(1 for r in rows if r.get("level") == "ok")
    warn = sum(1 for r in rows if r.get("level") == "warn")
    bad = [r for r in rows if r.get("level") == "bad"]
    print("=" * 68)
    print("汇总：%d 个文件 ｜ 合格 %d ｜ 有疑 %d ｜ 不达标 %d" % (n, ok, warn, len(bad)))
    if bad:
        print("\n不达标清单（标签可能虚高）：")
        for r in bad:
            print("  [X] %-44s %s" % (r["file"][:44], r.get("verdict")))

    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        print("\n结果已写入 %s" % args.json)
    if args.csv:
        _csv(rows, args.csv)
        print("CSV 已写入 %s" % args.csv)
    return 0


def _csv(rows, path):
    import csv
    from .config import BAND_CN, BANDS
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["文件", "路径", "格式", "采样率", "位深", "声道", "秒", "MB", "截止kHz",
                    "占奈奎斯特", "砖墙dB/oct", "峰值dBFS", "RMS", "DR", "削波%",
                    "LUFS", "LRA", "真峰值dBTP", "PLR", "相位相关", "声道宽度", "厂牌", "ISRC",
                    "编码器串", "判定"] + [BAND_CN[k[0]] + "%" for k in BANDS])
        for r in rows:
            md = r.get("meta") or {}
            fld = md.get("fields") or {}
            w.writerow([r["file"], r["path"], r["format"], r["sr"], r["bits"], r["channels"],
                        r["seconds"], r["size_mb"], r.get("cutoff_khz"), r.get("cutoff_ratio"),
                        r.get("brickwall_db_per_oct"), r.get("peak_dbfs"), r.get("rms_dbfs"),
                        r.get("dr_db"), r.get("clip_pct"), r.get("lufs"), r.get("lra"),
                        r.get("true_peak_dbfs"), r.get("plr"), r.get("corr"), r.get("width_db"),
                        fld.get("label", ""), fld.get("isrc", ""), md.get("vendor", ""),
                        r.get("verdict")]
                       + [(r.get("bands_pct") or {}).get(k[0], "") for k in BANDS])


def _plot(r, outdir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
        for fam in ("Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", "SimHei"):
            if any(fam.lower() in x.name.lower() for x in matplotlib.font_manager.fontManager.ttflist):
                plt.rcParams["font.sans-serif"] = [fam]
                break
        plt.rcParams["axes.unicode_minus"] = False
        cur = r.get("curve")
        if not cur:
            return
        os.makedirs(outdir, exist_ok=True)
        arr = np.asarray(cur, dtype=float)
        fig, ax = plt.subplots(2, 1, figsize=(10, 7), height_ratios=[2, 1])
        ax[0].semilogx(arr[:, 0], arr[:, 1], lw=0.9, color="#2b6cb0")
        if r.get("cutoff_hz"):
            ax[0].axvline(r["cutoff_hz"], color="#e53e3e", ls="--", lw=1.2)
            ax[0].annotate("%.2f kHz" % r["cutoff_khz"], xy=(r["cutoff_hz"], float(arr[:, 1].max())),
                           xytext=(6, -12), textcoords="offset points",
                           color="#e53e3e", fontweight="bold")
        ax[0].axvline(r["sr"] / 2, color="#888", ls=":", lw=1)
        ax[0].set_xlim(20, r["sr"] / 2)
        top = float(np.percentile(arr[:, 1], 99.5))
        ax[0].set_ylim(top - 92, top + 6)
        ax[0].grid(which="both", alpha=.25)
        ax[0].set_title("%s\n%s" % (r["file"], r.get("verdict", "")))
        b = r.get("bands_pct") or {}
        from .config import BAND_CN, BANDS
        keys = [k[0] for k in BANDS]
        ax[1].bar(range(len(keys)), [b.get(k, 0) for k in keys], color="#805ad5")
        ax[1].set_xticks(range(len(keys)))
        ax[1].set_xticklabels([BAND_CN[k] for k in keys])
        ax[1].set_ylabel("能量占比 %")
        plt.tight_layout()
        plt.savefig(os.path.join(outdir, os.path.splitext(r["file"])[0][:60] + ".png"), dpi=120)
        plt.close()
    except Exception as e:                                         # noqa: BLE001
        print("   (出图失败: %s)" % e)


if __name__ == "__main__":
    raise SystemExit(main())
