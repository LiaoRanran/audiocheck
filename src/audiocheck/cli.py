"""命令行入口：audiocheck / aca-check。"""
from __future__ import annotations

import argparse
import json
import os
import sys

from . import __version__
from .config import Thresholds, provenance_summary
from .core.engine import analyze, collect_files, list_analyzers
from .core.pipeline import ResultCache, analyze_many

ICON = {"ok": "[OK] ", "warn": "[!]  ", "bad": "[X]  "}


def _bar(frac: float, width: int = 28) -> str:
    n = int(max(0.0, min(1.0, frac)) * width)
    return "[" + "#" * n + "-" * (width - n) + "]"


def doctor() -> int:
    """环境自检：把"为什么跑不起来"说清楚，而不是抛一堆栈。"""
    import importlib.util as u
    print("audiocheck %s 环境自检\n" % __version__)
    ok = True
    mods = [("numpy", True), ("scipy", True), ("soundfile", True), ("mutagen", False),
            ("matplotlib", False), ("PySide6", False)]
    for m, required in mods:
        has = u.find_spec(m) is not None
        mark = "OK  " if has else ("缺失" if required else "可选缺失")
        if required and not has:
            ok = False
        print("  %-12s %-8s %s" % (m, mark, "" if has else "(pip install %s)" % m))
    try:
        import soundfile as sf
        print("\n  libsndfile  %s" % sf.__libsndfile_version__)
    except Exception:                                      # noqa: BLE001
        pass
    import os
    print("  CPU 核心    %s" % (os.cpu_count() or 1))
    print("\n结论: %s" % ("可以运行" if ok else "缺少必需依赖"))
    return 0 if ok else 1


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
    # C4.1 成因层：每条候选一行——"疑似"限定词 + 强度 + 证据（反证/无法区分附后）。
    # 现象与成因**分开写**（C4.4：不许把成因糊进判定行，也不许压成一个结论）。
    for c in r.get("causes") or []:
        line = ("成因  疑似%s（强度 %.2f，%s）：%s"
                % (c["key"], c.get("strength") or 0.0, c.get("prov") or "工程估计",
                   "；".join(c.get("evidence") or [])))
        if c.get("counter"):
            line += " ｜ 反证/存疑：" + "；".join(c["counter"])
        if c.get("ambiguous"):
            line += " ｜ 与「%s」无法区分（并列候选）" % "／".join(c.get("ambiguous_with") or [])
        print("      %s" % line)
    # C4.3 时间定位：问题段（与整轨结论并列，不是替代）
    _probs = r.get("timeline_problems") or []
    for _p in _probs[:6]:
        print("      时段  %s" % _p["text"])
    if len(_probs) > 6:
        print("      时段  ……另有 %d 段异常" % (len(_probs) - 6))
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
    p.add_argument("--jobs", type=int, default=0, help="并行进程数（0=自动，按 CPU 核数）")
    p.add_argument("--cache", default=None, help="结果缓存文件（跳过已算过的文件）")
    p.add_argument("--doctor", action="store_true", help="环境自检后退出")
    p.add_argument("--version", action="version", version="audiocheck %s" % __version__)
    return p


def _setup_console() -> None:
    """让中文在 Windows 控制台里不乱码、也不因编码编不出来而崩。

    Windows 默认代码页是 GBK(936)，而本程序所有文案都是 UTF-8 源码里的中文，
    其中还有 `｜`、`…` 这类 GBK 编不出来的符号——`print()` 会直接抛
    `UnicodeEncodeError`，用户看到的是 traceback 而不是报告。

    两步：把控制台切到 UTF-8（现代终端都支持，其它平台是 no-op）；
    再给 stdout/stderr 挂 `errors="replace"` 兜底——**宁可有问号，也不能崩**。
    """
    import sys

    try:
        if sys.platform == "win32":
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
    except Exception:                                          # noqa: BLE001
        pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:                                      # noqa: BLE001
            pass


def main(argv=None) -> int:
    _setup_console()
    args = build_parser().parse_args(argv)

    if args.doctor:
        return doctor()

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

    # 配置错误必须**在分析之前**报出来并退出。
    # 早期 Thresholds.load 会吞掉一切异常，坏配置退化成"悄悄用默认值"，
    # 而非法类型还会一路带到判定器里抛 TypeError、被引擎吞成"分析器告警"——
    # 最终表现是判定整段消失、汇总全 0、退出码仍是 0。
    try:
        th = Thresholds.load(args.config) if args.config else Thresholds()
    except (ValueError, OSError) as e:
        print("[X] %s" % e, file=sys.stderr)
        return 2
    unk = getattr(th, "__dict__", {}).get("_unknown_keys")
    if unk:
        print("[!] 配置里有无法识别的键，已忽略：%s（检查是否拼错）"
              % "、".join(unk), file=sys.stderr)

    only = [s.strip() for s in args.only.split(",")] if args.only else None
    print("待检测 %d 个文件\n" % len(files))

    cache = ResultCache(args.cache) if args.cache else None
    rows = []
    if only:
        # 只跑指定分析器时逐个执行，便于定位问题
        for p in files:
            try:
                rows.append(analyze(p, deep=not args.shallow, thresholds=th, only=only))
            except Exception as e:                                 # noqa: BLE001
                print("[X]  %s —— 读取失败：%s" % (os.path.basename(p), e))
    else:
        def on_prog(frac, eta):
            tail = ("  剩余约 %ds" % int(eta)) if eta else "         "
            sys.stderr.write("\r  %s %3d%%%s" % (_bar(frac), int(frac * 100), tail))
            sys.stderr.flush()

        rows = analyze_many(files, deep=not args.shallow, thresholds=th,
                            jobs=args.jobs, cache=cache,
                            keep_curve=bool(args.plot), on_progress=on_prog)
        sys.stderr.write("\r" + " " * 62 + "\r")
        sys.stderr.flush()
        if cache is not None:
            cache.save()

    for r in rows:
        show(r, args.quiet)
        if args.plot:
            _plot(r, args.plot)

    n = len(rows)
    ok = sum(1 for r in rows if r.get("level") == "ok")
    warn = sum(1 for r in rows if r.get("level") == "warn")
    bad = [r for r in rows if r.get("level") == "bad"]
    # C0-4：**未判定要单独报**。原先它既不计入 ok/warn/bad，也不出现在汇总里
    # ⇒ 浅模式（--shallow）跑完显示"合格 0 ｜ 有疑 0 ｜ 不达标 0"，
    # 读起来像"全部没问题"。查不到必须与没问题可区分。
    undecided = n - ok - warn - len(bad)
    print("=" * 68)
    line = "汇总：%d 个文件 ｜ 合格 %d ｜ 有疑 %d ｜ 不达标 %d" % (n, ok, warn, len(bad))
    if undecided:
        line += " ｜ **未判定 %d**（浅模式或判定器未跑）" % undecided
    print(line)
    if bad:
        print("\n不达标清单（标签可能虚高）：")
        for r in bad:
            print("  [X] %-44s %s" % (r["file"][:44], r.get("verdict")))

    # ---- C2：阈值出处**降级措辞** ----
    # 没有出处的阈值不许被当成有依据的判据。这里把"本轮判定用了多少个工程估计"
    # 直接印进汇总——**看得见才谈得上诚实**（规矩见 docs/thresholds.md）。
    _pv = provenance_summary()
    _est = len(_pv["engineering_estimates"])
    print("阈值出处：%d 个字段 ｜ 标准/一手文档 %d ｜ 工程估计（无出处）%d"
          % (_pv["total_fields"],
             _pv["counts"].get("标准", 0) + _pv["counts"].get("一手文档", 0), _est))
    if _est:
        print("  标'工程估计'的阈值是本项目的取值、不是外部标准；"
              "完整账目见 docs/thresholds.md")

    # ---- C4.5 效力边界：这一轮**不能**告诉用户什么（每次运行印一次）----
    # "未知"必须出现在用户可见处——否则"没测"会被读成"没问题"。
    from .core.analyzers.causes import boundaries as _bounds
    print("效力边界（本工具不能告诉你的）：%s" % "；".join(_bounds()))

    if args.json:
        # 导出的 JSON **自带出处**：拿到这份结果的人要知道哪些数字有依据
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump({"threshold_provenance": _pv, "results": rows},
                      f, ensure_ascii=False, indent=1)
        print("\n结果已写入 %s" % args.json)
    if args.csv:
        _csv(rows, args.csv)
        print("CSV 已写入 %s" % args.csv)

    # ---- 退出码 ----
    # 分析器报错必须反映到退出码。早期无论发生什么都 return 0，
    # 于是"判定整段消失、汇总全 0"这种事在脚本/CI 里完全看不出来。
    # 实测场景：配置里一个阈值类型写错，判定器抛 TypeError 被引擎吞成
    # "分析器告警"，而退出码仍是 0 —— 一个静默的错误结果。
    #
    # 0 = 全部正常 ｜ 2 = 输入/配置有问题（含"没找到文件"）｜ 3 = 分析已完成但有告警
    errs = [r for r in rows if r.get("errors")]
    if errs:
        print("\n[X] %d 个文件有分析器告警，结果可能不完整：" % len(errs),
              file=sys.stderr)
        for r in errs[:5]:
            for e in (r.get("errors") or [])[:2]:
                print("      %-34s %s" % (r["file"][:34], e), file=sys.stderr)
        if len(errs) > 5:
            print("      ……另有 %d 个" % (len(errs) - 5), file=sys.stderr)
        return 3
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
