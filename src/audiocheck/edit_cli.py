"""编辑器命令行（P0 地基的**用户入口**）。

    audiocheck-edit <源> <目标> --gain -6 --at 12.5-14.0 [--subtype PCM_16] [--overwrite] [--audit 记录.json]
    audiocheck-edit <源> <目标> --gain -20 --at 12.5-14.0 --band 4000-8000     # 时频选区
    audiocheck-edit <源> <目标> --declip --at 3.0-3.5 [--bandwidth 18000] [--ceiling 0.98]   # 去削波
    audiocheck-edit <源> <目标> --declick --at 1.0-2.0 [--threshold 4.0]                    # 去咔哒
    audiocheck-edit <源> <目标> --dehum --at 0-999 --hum-f0 50 [--hum-q 10]                 # 去嗡
    audiocheck-edit <源> <目标> --denoise --at 10-60 --noise-at 0-2 [--reduction-db 20]     # 去噪
    audiocheck-edit <源> <目标> --gain -6 --at 12-14 --save-project 计划.json               # 存项目
    audiocheck-edit <源> <目标> --project 计划.json                                         # 按记录跑

五种操作**必须给且只给一种**：`--gain`（时间选区增益 / 加 `--band` 变时频选区）、
`--declip`（去削波）、`--declick`（去咔哒）、`--dehum`（去嗡）或 `--denoise`（去噪）。
⚠ `--denoise` **必须再给 `--noise-at`**（噪声段：只有噪声的一小段，用来学谱）——
盲估计被实测否决（见 `denoise.py`），不给就拒绝。
⚠ 复合操作（一条链里多个算子）走 `--project`：记录里的 source 必须与命令行一致；
  渲染按 `render.PHASE` 的规范顺序，审计记 `stages` 与 `reordered_from`。

三条约定（与 `core/edit/render.py` 一致，不在这一层偷改）
--------------------------------------------------------
* **非破坏**：目标 == 源一律拒绝（渲染层硬保证）；目标已存在时默认不覆盖（`--overwrite` 才覆盖）。
* **抖动是导出选项**：`--dither` 只对整型 subtype 有效，且是**全文件重量化**——开启后
  "未编辑处逐位不变"的承诺不再成立（只应在最终导出时用）。
* **可审计**：跑完把审计记录**打出来**；`--audit` 再另存一份 JSON（"可审计的修复"的第一片）。
  频域编辑还会打出**实测的 STFT 往返残差**——"频谱编辑不是位精确的"这条要摆到台面上。
  去削波还会打出**实测的能量比**与**带外变化**——证明"没把它修安静了"、且带限投影确实生效。
* **不许 traceback**：源读不了 / 参数非法 → **一行可读信息** + 明确退出码。

退出码：`0` 成功 ｜ `2` 参数或用法问题 ｜ `4` 源读不了 ｜ `5` 目标已存在且未给 `--overwrite`。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

from .cli import _setup_console
from .core.edit import (
    BandGainOp,
    DeclickOp,
    DeclipOp,
    DehumOp,
    DenoiseOp,
    GainOp,
    Project,
    op_from_dict,
    render,
)


def _parse_range(s: str) -> tuple[float, float]:
    """解析 `--at A-B`（秒，半开区间 `[A, B)`）。"""
    parts = s.split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("时间选区要写成 `起点-终点`（秒），例如 `12.5-14.0`，得到 %r" % s)
    try:
        t0, t1 = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError("时间选区两端必须是数字（秒），得到 %r" % s) from None
    if not t1 > t0 >= 0.0:
        raise ValueError("需要 0 <= 起点 < 终点，得到 [%g, %g)" % (t0, t1))
    return t0, t1


def _parse_freq(s: str) -> tuple[float, float]:
    """解析 `--band f0-f1`（Hz，半开区间 `[f0, f1)`）。"""
    parts = s.split("-")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("频段要写成 `低-高`（Hz），例如 `4000-8000`，得到 %r" % s)
    try:
        f0, f1 = float(parts[0]), float(parts[1])
    except ValueError:
        raise ValueError("频段两端必须是数字（Hz），得到 %r" % s) from None
    if not f1 > f0 >= 0.0:
        raise ValueError("需要 0 <= 低 < 高，得到 [%g, %g)" % (f0, f1))
    return f0, f1


def _bw_src_cn(src: str | None) -> str:
    """带宽来源的**人话**（与 `DeclipOp.bandwidth_source` 的取值一一对应）。"""
    return {"given": "给定",
            "auto_cutoff": "自动＝砖墙截止（测量层读数）",
            "auto_nyquist": "自动＝奈奎斯特（量不到带限 ⇒ 不作约束）"}.get(src, "%s" % src)


def _auto_bandwidth(source: str) -> tuple[float, str]:
    """`--bandwidth` 省略时的**自动读数**，返回 `(B, 来源标签)`。

    ⚠ 编辑层**不 import 测量层**（模块边界，见 `core/edit/__init__.py`）⇒ 由**这一层**
    （CLI = 边界层）把两边接起来：调 `spectrum.content_bandwidth`（口径写在那里，不在这里抄）。
    实测定的口径：**有砖墙截止 ⇒ 用它；量不到 ⇒ 奈奎斯特**（= 没有可用的带限先验，
    去削波会如实退化成逐位不变）。⚠ `canonical` 谱要求素材 ≥ ~1.4 s
    （`AnalysisContext.spectrum()` 的三分位取样）⇒ 太短就**明确拒绝**，不猜一个 `B` 出来。
    """
    from .core.analyzers.spectrum import content_bandwidth
    from .core.context import AnalysisContext, io_path
    ctx = AnalysisContext(path=io_path(source), deep=False, src_path=source)
    f, db = ctx.spectrum()
    if f is None:
        raise ValueError("素材太短，量不出 canonical 谱（三分位取样要求采样足够长）"
                         "⇒ 无法自动定带宽；请显式给 `--bandwidth`")
    info = content_bandwidth(f, db, ctx.fs, ctx.thresholds)
    return info["bandwidth_hz"], "auto_" + info["source"]


def _auto_hum_f0(source: str) -> tuple[float, dict]:
    """`--hum-auto`：调**测量层**的稳态检测（编辑层不 import 测量；CLI 是边界层）。

    返回 `(f0, 证据 dict)`；证据不足 ⇒ `ValueError`（把实测三项一起写进信息，
    让用户知道"为什么不敢自动填"）。
    """
    from .core.analyzers.hum import detect_hum
    from .core.context import AnalysisContext, io_path
    ctx = AnalysisContext(path=io_path(source), deep=False, src_path=source)
    x, fs = ctx.segment(seconds=6.0)
    if x is None:
        raise ValueError("素材太短，取不到 6 s 的检测段 ⇒ 无法自动定基频；请显式给 `--hum-f0`")
    r = detect_hum(x, fs, ctx.thresholds)
    if not r["accepted"]:
        raise ValueError("自动定基频失败：%s（候选 %s Hz ｜ excess %s dB ｜ 帧间稳定 %s dB ｜ "
                         "安静帧 %s dB）⇒ 请显式给 `--hum-f0`"
                         % (r["reason"], r.get("f0_hz"),
                            r.get("excess_db"), r.get("stability_db"),
                            r.get("quiet_excess_db")))
    return float(r["f0_hz"]), r


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="audiocheck-edit",
                                 description="非破坏编辑：把操作渲染到新文件，并给出审计记录")
    ap.add_argument("source", help="源音频（只读，永不修改）")
    ap.add_argument("dest", help="输出文件（不许等于源）")
    ap.add_argument("--gain", type=float, default=None,
                    help="选区增益（dB）。0 = 无操作（no-op，逐位不变）")
    ap.add_argument("--declip", action="store_true",
                    help="去削波（只改饱和样本）。`--ceiling` 与 `--bandwidth` 都可省略（自动）")
    ap.add_argument("--declick", action="store_true",
                    help="去咔哒（只改检出样本）。`--threshold` 可省略（实测标定默认 5.0）")
    ap.add_argument("--dehum", action="store_true",
                    help="去嗡（零相位陷波级联）。`--hum-f0` 或 `--hum-auto` 必须给一个")
    ap.add_argument("--denoise", action="store_true",
                    help="去噪（Wiener-DD，噪声谱从 `--noise-at` 学）。`--noise-at` 必须给")
    ap.add_argument("--noise-at", default=None,
                    help="**噪声段**（秒，半开区间，例如 0-0.5）：必须只有噪声，用来学谱。"
                         "⚠ 盲估计被实测否决（+10 dB 持续音上 ΔSDR −8.7 dB）")
    ap.add_argument("--reduction-db", type=float, default=None,
                    help="去噪的压制上限（dB，默认 20）：增益不低于 −该值。"
                         "调大压得更狠，但瞬态被削得更软、musical noise 更多")
    ap.add_argument("--hum-f0", type=float, default=None,
                    help="嗡的基频（Hz）。可测输入（频谱上的嗡线），不自动猜")
    ap.add_argument("--hum-auto", action="store_true",
                    help="从素材**自动**读基频：要求三条线都存在**且稳态**"
                         "（excess ≥4 dB、帧间稳定 ≤30 dB、安静帧仍高出邻域）；"
                         "证据不足会拒绝并打印实测值")
    ap.add_argument("--hum-q", type=float, default=None,
                    help="去嗡的陷波 Q（实测默认 10：±1 Hz 漂移下仍压 ≥17 dB 的最大 Q）")
    ap.add_argument("--hum-harmonics", default=None,
                    help="要清的谐波次数，逗号分隔（默认 1,2,3；k·f0 ≥ 奈奎斯特的会跳过并记账）")
    ap.add_argument("--threshold", type=float, default=None,
                    help="去咔哒的检测阈值 α（|r| > α·MAD）。**调小会把误报引进来**（实测会修坏）")
    ap.add_argument("--max-run", type=int, default=None,
                    help="去咔哒的**宽度守卫**：只修宽度 ≤ N 采样的检出段（实测默认 4；0 = 关闭）")
    ap.add_argument("--ceiling", type=float, default=None,
                    help="削波电平 c（线性幅度，0 < c <= 1；可测输入，见 --declip）")
    ap.add_argument("--bandwidth", type=float, default=None,
                    help="内容带宽上界 B（Hz）。**省略 ⇒ 自动**：调测量层读砖墙截止；"
                         "量不到带限 ⇒ 奈奎斯特（= 不作约束，去削波逐位不变）")
    ap.add_argument("--iterations", type=int, default=None,
                    help="去削波的 POCS 迭代次数（默认 128；更多更准但更慢，见 declip.py）")
    ap.add_argument("--at", default=None,
                    help="时间选区（秒，半开区间），形如 12.5-14.0。⚠ 用 `--project` 时不给")
    ap.add_argument("--project", default=None,
                    help="**项目记录**（JSON，`Project.to_dict` 的格式）：按记录里的操作栈"
                         "**链式渲染**（规范顺序）。与逐操作参数互斥；记录里的 source 必须与"
                         "命令行给的一致")
    ap.add_argument("--save-project", default=None,
                    help="把**本次实际执行**的项目记录写成 JSON（可复算）。"
                         "⚠ 不能与 `--project` 同时用（读进来的记录不需要再存一遍）")
    ap.add_argument("--band", default=None,
                    help="频段（Hz，半开区间），给了就走**时频**路径，形如 4000-8000")
    ap.add_argument("--n-fft", type=int, default=None,
                    help="时频路径的 STFT 窗长（默认 4096；只对 --band 有效）")
    ap.add_argument("--hop", type=int, default=None,
                    help="时频路径的 hop（默认 n_fft//4；只对 --band 有效）")
    ap.add_argument("--subtype", default=None, help="目标编码（默认沿用源，例如 PCM_16）")
    ap.add_argument("--dither", action="store_true",
                    help="导出**TPDF 抖动**（±1 LSB，只对整型 subtype）。"
                         "⚠ 全文件重量化：未编辑处的逐位不变承诺不再成立")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的目标")
    ap.add_argument("--audit", default=None, help="把审计记录另存为 JSON")
    return ap


def main(argv=None) -> int:
    _setup_console()
    a = build_parser().parse_args(argv)
    if a.project is not None:
        return _main_project(a)
    if a.at is None:
        print("参数错误：需要 `--at`（时间选区，例如 12.5-14.0）；"
              "要按项目记录跑请用 `--project`")
        return 2

    n_modes = ((1 if a.gain is not None else 0) + (1 if a.declip else 0)
               + (1 if a.declick else 0) + (1 if a.dehum else 0)
               + (1 if a.denoise else 0))
    if n_modes != 1:
        print("参数错误：必须且**只**给一种操作：`--gain`（增益，可加 `--band`）／"
              "`--declip`（去削波）／`--declick`（去咔哒）／`--dehum`（去嗡）／"
              "`--denoise`（去噪）")
        return 2
    if a.denoise and a.noise_at is None:
        print("参数错误：`--denoise` 需要 `--noise-at`（只有噪声的一小段，用来学谱）——"
              "盲估计被实测否决（+10 dB 持续音上 ΔSDR −8.7 dB），宁可不做")
        return 2
    if not a.denoise and (a.noise_at is not None or a.reduction_db is not None):
        print("参数错误：`--noise-at` / `--reduction-db` 只对 `--denoise` 有效")
        return 2
    if a.dehum and a.hum_f0 is None and not a.hum_auto:
        print("参数错误：`--dehum` 需要 `--hum-f0` 或 `--hum-auto`——"
              "基频是**可测输入**，不自动猜；自动路径也要先过稳态判据")
        return 2
    if a.dehum and a.hum_auto and a.hum_f0 is not None:
        print("参数错误：`--hum-auto` 与 `--hum-f0` 只能给一个（自动读数和手动指定是两条口径）")
        return 2
    if not a.dehum and (a.hum_f0 is not None or a.hum_q is not None
                        or a.hum_harmonics is not None or a.hum_auto):
        print("参数错误：`--hum-f0` / `--hum-auto` / `--hum-q` / `--hum-harmonics` "
              "只对 `--dehum` 有效")
        return 2
    if a.declip and (a.band or a.n_fft is not None or a.hop is not None):
        print("参数错误：`--band` / `--n-fft` / `--hop` 只对 `--gain` 有效")
        return 2
    if a.declick and (a.band or a.n_fft is not None or a.hop is not None
                      or a.ceiling is not None or a.bandwidth is not None
                      or a.iterations is not None):
        print("参数错误：`--band` / `--n-fft` / `--hop` / `--ceiling` / `--bandwidth` / "
              "`--iterations` 都不是去咔哒的参数（它只有 `--threshold` 与 `--max-run`）")
        return 2
    if a.dehum and (a.band or a.n_fft is not None or a.hop is not None
                    or a.ceiling is not None or a.bandwidth is not None
                    or a.iterations is not None or a.threshold is not None
                    or a.max_run is not None):
        print("参数错误：`--band` / `--n-fft` / `--hop` / `--ceiling` / `--bandwidth` / "
              "`--iterations` / `--threshold` / `--max-run` 都不是去嗡的参数"
              "（它只有 `--hum-f0` / `--hum-q` / `--hum-harmonics`）")
        return 2
    if a.denoise and (a.band or a.n_fft is not None or a.hop is not None
                      or a.ceiling is not None or a.bandwidth is not None
                      or a.iterations is not None or a.threshold is not None
                      or a.max_run is not None or a.hum_f0 is not None
                      or a.hum_q is not None or a.hum_harmonics is not None
                      or a.hum_auto):
        print("参数错误：去噪没有 `--band` / `--n-fft` / `--hop` / `--ceiling` / "
              "`--bandwidth` / `--iterations` / `--threshold` / `--max-run` / "
              "`--hum-*` 这些参数（它只有 `--at` / `--noise-at` / `--reduction-db`）")
        return 2

    try:
        t0, t1 = _parse_range(a.at)
        f0, f1 = _parse_freq(a.band) if a.band else (None, None)
        n0, n1 = _parse_range(a.noise_at) if a.noise_at else (None, None)
    except ValueError as e:
        print("参数错误：%s" % e)
        return 2

    # 去削波：`--bandwidth` 省略时走**自动**——读数由**测量层**给（编辑层只接受输入）。
    bw, bw_src = a.bandwidth, "given"
    if a.declip and a.bandwidth is None:
        try:
            bw, bw_src = _auto_bandwidth(a.source)
        except ValueError as e:
            print("参数错误：%s" % e)
            return 2
        except Exception as e:                   # noqa: BLE001 —— 源读不了 / 容器怪
            print("**源读不了**：%s" % e)
            return 4

    kw = {}
    if a.band:
        # 默认值由 `core/edit/render.py` 决定，这一层不重复一遍（免得两处默认值分叉）
        if a.n_fft is not None:
            kw["n_fft"] = a.n_fft
        if a.hop is not None:
            kw["hop"] = a.hop
    try:
        if a.declip:
            dkw = {} if a.iterations is None else {"iterations": a.iterations}
            op = DeclipOp(t0, t1, bw, a.ceiling, bandwidth_source=bw_src, **dkw)
        elif a.declick:
            dkw = {}
            if a.threshold is not None:
                dkw["threshold"] = a.threshold
            if a.max_run is not None:
                dkw["max_run"] = None if a.max_run == 0 else a.max_run
            op = DeclickOp(t0, t1, **dkw)
        elif a.denoise:
            dkw = {}
            if a.reduction_db is not None:
                dkw["reduction_db"] = a.reduction_db
            op = DenoiseOp(t0, t1, n0, n1, **dkw)
        elif a.dehum:
            dkw = {}
            if a.hum_q is not None:
                dkw["q"] = a.hum_q
            if a.hum_harmonics is not None:
                try:
                    dkw["harmonics"] = tuple(int(x) for x in a.hum_harmonics.split(",") if x.strip())
                except ValueError:
                    raise ValueError("`--hum-harmonics` 要写成逗号分隔的整数，例如 1,2,3"
                                     "（得到 %r）" % a.hum_harmonics) from None
            if a.hum_auto:
                f0_hum, ev = _auto_hum_f0(a.source)
                dkw["f0_source"] = "auto"
                print("自动基频  %.2f Hz（excess %s dB ｜ 帧间稳定 %s dB ｜ 安静帧 %s dB）"
                      % (f0_hum, "/".join("%.1f" % v for v in ev["excess_db"]),
                         "/".join("%.1f" % v for v in ev["stability_db"]),
                         "/".join("%.1f" % v for v in ev["quiet_excess_db"])))
            else:
                f0_hum = a.hum_f0
            op = DehumOp(t0, t1, f0_hum, **dkw)
        else:
            op = (BandGainOp(t0, t1, f0, f1, a.gain) if a.band
                  else GainOp(t0, t1, a.gain))
        proj = Project(a.source, [op])
        audit = render(proj, a.dest,
                       subtype=a.subtype, overwrite=a.overwrite, dither=a.dither, **kw)
    except FileExistsError as e:
        print("目标已存在：%s（要覆盖请加 --overwrite）" % e)
        return 5
    except ValueError as e:
        print("拒绝：%s" % e)
        return 2
    except Exception as e:                       # noqa: BLE001 —— 源读不了的各种形态
        print("**源读不了**：%s" % e)
        return 4

    print("源    %s" % audit["source"])
    print("目标  %s" % audit["dest"])
    print("格式  %d Hz ｜ %d 声道 ｜ %s" % (audit["samplerate"], audit["channels"],
                                            audit["subtype"]))
    _print_dither(audit)
    print("长度  %d 帧（%.3f s）" % (audit["frames"], audit["duration_s"]))
    if audit["domain"] == "band":
        for op in audit["ops"]:
            m = op.get("attenuation_measured_db")
            print("操作  时频选区 %.3f–%.3f s × %.1f–%.1f Hz：设定 %+.1f dB ｜ 实测 %s ｜ 改动 %d 个时频点"
                  % (op["t0"], op["t1"], op["f0"], op["f1"], op["gain_db"],
                     "—" if m is None else "%+.2f dB" % m, op["applied_tf_points"]))
        print("STFT  n_fft %d ｜ hop %d ｜ 窗 %s"
              % (audit["n_fft"], audit["hop"], audit["window"]))
        # 实测为什么可能比设定浅/深：在**同一套 STFT 表示**里量，所以这里报的是
        # "掩码实际乘下去多少"；带内本来没能量（no-op/静默素材）时报 "—"。
        if any(o.get("attenuation_measured_db") is not None
               and abs(o["attenuation_measured_db"] - o["gain_db"]) > 0.01 for o in audit["ops"]):
            print("注  实测是掩码在时频域**实际乘下去**的量（设定与实测的差来自掩码的边缘 bin/帧）")
    elif audit["domain"] == "declip":
        for op in audit["ops"]:
            e = op.get("energy_ratio_measured_db")
            ob = op.get("out_of_band_measured_db")
            print("操作  去削波 %.3f–%.3f s：削波电平 %.4f（%s ｜ 区内峰值 %.4f）｜ "
                  "带宽 %.0f Hz（%s）｜ 迭代 %d"
                  % (op["t0"], op["t1"], op["ceiling"],
                     "给定" if op.get("ceiling_source") == "given" else "自动=观测峰值",
                     op["peak_in"], op["bandwidth"], _bw_src_cn(op.get("bandwidth_source")),
                     op["iterations"]))
            print("     饱和 %d / %d 采样（%.2f%% ｜ %d 段 ｜ 最长 %d）｜ 实际替换 %d 帧"
                  % (op["saturated_samples"], op["total_samples"], 100.0 * op["fraction"],
                     op["runs"], op["longest_run"], op["replaced_samples"]))
            # 「设定 vs 实测」+ 两条守卫：能量比防「修安静了」，带外变化证明带限投影真生效
            print("     实测  能量比 %s dB（防『变安静被当成修好』）｜ 带外变化 %s dB ｜ %s"
                  % ("—" if e is None else "%+.3f" % e,
                     "—" if ob is None else "%+.3f" % ob,
                     "无带限约束（逐位不变）" if op.get("noop") else "带限投影生效"))
            if op.get("ambiguity_margin") is not None:
                if op["ambiguous"]:
                    print("⚠ **解不唯一（欠定）**：可辨识性余量 %+.3f ≤ 0（饱和 %.1f%% 超过 "
                          "1−B/(fs/2)）⇒ 迭代在解集里漂，**加迭代也逼近不了真值**"
                          "（实测 400→20000 次 SDR 13.9→14.2 dB 不动）"
                          % (op["ambiguity_margin"], 100.0 * op["fraction"]))
                else:
                    print("可辨识性  余量 %+.3f > 0（定解）｜ 迭代末步 %s dB%s"
                          % (op["ambiguity_margin"], op["step_rms_db"],
                             "（已到浮点精度）" if op.get("converged")
                             else "（还能动 ⇒ 想要更准可加 --iterations）"))
        print("算法  %s（带限投影 + 削波不等式投影）" % audit["algorithm"])
    elif audit["domain"] == "declick":
        for op in audit["ops"]:
            e = op.get("energy_ratio_measured_db")
            print("操作  去咔哒 %.3f–%.3f s：检测阈值 %.2f×MAD ｜ 宽度守卫 %s ｜ 残差尺子 %s"
                  % (op["t0"], op["t1"], op["threshold"],
                     "关" if op["max_run"] is None else "≤%d 采样" % op["max_run"],
                     op["residual_scale"]))
            print("     检出 %d / %d 采样（%.4f%%）｜ 实际替换 %d 帧 ｜ "
                  "宽度守卫挡下 %d 段 ｜ 实测 能量比 %s dB"
                  % (op["detected_samples"], op["applied_frames"] * audit["channels"],
                     100.0 * op["fraction"], op["changed_samples"], op["rejected_runs"],
                     "—" if e is None else "%+.3f" % e))
            if op["changed_samples"] * 100.0 / max(1, op["applied_frames"] * audit["channels"]) > 0.5:
                print("⚠ **改动面偏大**（>0.5% 的采样）：阈值可能压到了误报区——实测"
                      "误报率到 ~1e-3 时修复会**把信号修坏**（白噪 −13 dB）⇒ 宁可漏、不可错")
        print("算法  %s（两点预测残差 + 全段 MAD 尺子 + 宽度守卫 + 线性插值）" % audit["algorithm"])
    elif audit["domain"] == "denoise":
        for op in audit["ops"]:
            e = op.get("energy_ratio_measured_db")
            nr = op.get("noise_reduction_measured_db")
            print("操作  去噪 %.3f–%.3f s：噪声段 %.3f–%.3f s（%d 采样 / %d 帧）｜ "
                  "压制上限 %.0f dB ｜ STFT %d/%d"
                  % (op["t0"], op["t1"], op["noise_t0"], op["noise_t1"],
                     op["noise_samples"], op["noise_frames"], op["reduction_db"],
                     op["n_fft"], op["hop"]))
            print("     实测 噪声段压制 %s dB（同一套增益作用在噪声段上量的）｜ "
                  "选区能量比 %s dB ｜ 处理 %d 帧"
                  % ("—" if nr is None else "%+.2f" % nr,
                     "—" if e is None else "%+.3f" % e, op["processed_frames"]))
        print("⚠ **去噪改整段选区**（逐帧逐 bin 增益）；**瞬态会被削软**（实测单采样脉冲 "
              "−13.6 dB）——要保住瞬态就降低 `--reduction-db` 或把去噪放在最后。"
              "**非稳态噪声**没做（N 是噪声段的平均谱）")
        print("算法  %s（判决引导先验 SNR 的 Wiener + 从噪声段学谱）" % audit["algorithm"])
    elif audit["domain"] == "dehum":
        for op in audit["ops"]:
            e = op.get("energy_ratio_measured_db")
            sup = op.get("suppression_db") or []
            used = op.get("harmonics_used") or []
            print("操作  去嗡 %.3f–%.3f s：基频 %.2f Hz（%s）｜ Q=%.1f ｜ 谐波 %s"
                  % (op["t0"], op["t1"], op["f0"],
                     "自动＝稳态检测" if op.get("f0_source") == "auto" else "给定",
                     op["q"], "/".join(str(k) for k in used)))
            print("     实测压制 %s dB ｜ 改动 %d / %d 采样 ｜ 实测 能量比 %s dB"
                  % (" / ".join("—" if v is None else "%+.1f" % v for v in sup),
                     op["changed_samples"], op["total_samples"],
                     "—" if e is None else "%+.3f" % e))
            if op.get("skipped_harmonics"):
                print("     跳过谐波 %s（k·f0 ≥ 奈奎斯特，不猜着折回）"
                      % "/".join(str(k) for k in op["skipped_harmonics"]))
        print("⚠ **陷波是 LTI：整段选区都被改动**（选区之外逐位不变）。"
              "收益由嗡的能量占比决定——无嗡素材上它同样会改（实测 SDR ~27.6 dB 量级）")
        print("算法  %s（正反各滤一遍，相位相互抵消）" % audit["algorithm"])
    else:
        for op in audit["ops"]:
            print("操作  %+.1f dB @ [%.3f, %.3f) s ｜ 生效 %d 帧"
                  % (op["gain_db"], op["t0"], op["t1"], op["applied_frames"]))
    print("输出峰值  %s dBFS" % audit["peak_out_dbfs"])

    # 两个**必须显眼**的如实上报（专业工具不许把这两件事埋在日志里）
    if audit["clipped_frames"]:
        print("⚠ **削波 %d 帧**：目标编排放不下这个峰值，已按编排放满钳位（改回浮点或先降增益）"
              % audit["clipped_frames"])
    if audit["read_incomplete"]:
        print("⚠ **源疑似截断**：容器声称 %d 帧，实际只读到 %d 帧（%.1f%%）；已按实读部分导出"
              % (audit["frames_claimed"], audit["frames"],
                 100.0 * audit["frames"] / audit["frames_claimed"] if audit["frames_claimed"] else 0.0))
    if audit["domain"] == "band":
        # 第三个必须显眼的上报：**时频编辑不是位精确的**（有实测值可依，不许含糊）
        half_ms = 500.0 * audit["n_fft"] / audit["samplerate"]
        floor = audit["roundtrip_floor_dbfs"]
        print("⚠ **时频编辑不是位精确的**：往返残差实测 %s dBFS（在 %d 个采样上量的）；"
              "受影响的采样 = 选区 ± %.1f ms，之外逐位不变"
              % ("无（全零信号）" if floor is None else "%.1f" % floor,
                 audit["roundtrip_floor_samples"], half_ms))

    if a.audit:
        try:
            with open(a.audit, "w", encoding="utf-8") as f:
                json.dump(audit, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    if a.save_project:
        try:
            with open(a.save_project, "w", encoding="utf-8") as f:
                json.dump(proj.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("项目记录写不出去：%s" % e)
            return 2
        print("项目  %s" % a.save_project)
    return 0


def _print_dither(audit: dict) -> None:
    if audit.get("dither"):
        print("抖动  TPDF ±1 LSB（%.3g）｜ **全文件重量化**：未编辑处的逐位不变不再成立"
              % audit["dither_lsb"])


def _flag_conflicts(a) -> list[str]:
    """`--project` 模式下**不许**出现的逐操作参数（返回人可读的名字）。"""
    names = []
    for attr in ("gain", "declip", "declick", "dehum", "denoise", "band",
                 "noise_at", "reduction_db", "hum_f0", "hum_auto", "hum_q",
                 "hum_harmonics", "threshold", "max_run", "ceiling", "bandwidth",
                 "iterations", "n_fft", "hop", "at"):
        v = getattr(a, attr)
        if v is not None and v is not False:
            names.append("--" + attr.replace("_", "-"))
    return names


def _print_chain(audit: dict) -> None:
    """链式审计的人话摘要（逐阶段：操作 / 算法 / 覆盖帧数）。"""
    stages = audit.get("stages") or []
    print("链式  %d 个阶段（规范顺序，`render.PHASE`）" % len(stages))
    for i, s in enumerate(stages, 1):
        try:
            desc = op_from_dict(s["op"]).describe()
        except ValueError:                        # 记录本身来自本仓，不会走到；防御性
            desc = str(s["op"])
        frames = sum((x or 0) for x in (s.get("applied_frames") or []))
        print("  %d. %s ｜ 算法 %s ｜ 覆盖 %d 帧"
              % (i, desc, s.get("algorithm") or "—", frames))
        if s.get("n_fft"):
            floor = s.get("roundtrip_floor_dbfs")
            print("     STFT n_fft %d ｜ hop %d ｜ 往返残差 %s dBFS"
                  % (s["n_fft"], s["hop"],
                     "无（全零信号）" if floor is None else "%.1f" % floor))
    if audit.get("reordered_from"):
        print("注  顺序被规范化（原始顺序见审计 `reordered_from`）")


def _main_project(a) -> int:
    """`--project`：按保存的项目记录链式渲染（复合操作的 CLI 入口）。"""
    if a.save_project is not None:
        print("参数错误：`--project` 与 `--save-project` 不能同时用"
              "（读进来的记录不需要再存一遍）")
        return 2
    bad = _flag_conflicts(a)
    if bad:
        print("参数错误：`--project` 与逐操作参数互斥，收到 %s" % " / ".join(bad))
        return 2
    try:
        with open(a.project, encoding="utf-8") as f:
            record = json.load(f)
        proj = Project.from_dict(record)
    except OSError as e:
        print("项目记录读不了：%s" % e)
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        print("项目记录不可用：%s" % e)
        return 2
    if (os.path.normcase(os.path.abspath(proj.source))
            != os.path.normcase(os.path.abspath(a.source))):
        print("项目记录里的源是 %s，与命令行给的 %s 不一致——"
              "要按记录跑请用记录里的源；要换源请改记录（不静默替换）"
              % (proj.source, a.source))
        return 2
    try:
        audit = render(proj, a.dest, subtype=a.subtype, overwrite=a.overwrite,
                       dither=a.dither)
    except FileExistsError as e:
        print("目标已存在：%s（要覆盖请加 --overwrite）" % e)
        return 5
    except ValueError as e:
        print("拒绝：%s" % e)
        return 2
    except Exception as e:                         # noqa: BLE001 —— 源读不了的各种形态
        print("**源读不了**：%s" % e)
        return 4

    print("源    %s" % audit["source"])
    print("目标  %s" % audit["dest"])
    print("格式  %d Hz ｜ %d 声道 ｜ %s" % (audit["samplerate"], audit["channels"],
                                            audit["subtype"]))
    _print_dither(audit)
    print("长度  %d 帧（%.3f s）" % (audit["frames"], audit["duration_s"]))
    if audit.get("chained"):
        _print_chain(audit)
    else:
        print("操作  空栈（恒等渲染）" if not audit["ops"] else "操作  单算子渲染")
    print("输出峰值  %s dBFS" % audit["peak_out_dbfs"])
    if audit["clipped_frames"]:
        print("⚠ **削波 %d 帧**：目标编排放不下这个峰值，已按编排放满钳位"
              % audit["clipped_frames"])
    if audit["read_incomplete"]:
        print("⚠ **源疑似截断**：容器声称 %d 帧，实际只读到 %d 帧；已按实读部分导出"
              % (audit["frames_claimed"], audit["frames"]))
    if a.audit:
        try:
            with open(a.audit, "w", encoding="utf-8") as f:
                json.dump(audit, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    return 0


if __name__ == "__main__":
    sys.exit(main())
