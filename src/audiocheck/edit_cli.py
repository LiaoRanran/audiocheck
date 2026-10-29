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

from . import __version__
from .cli import _setup_console
from .core.edit import (
    BandGainOp,
    DeclickOp,
    DeclipOp,
    DehumOp,
    DenoiseOp,
    GainOp,
    PolyGainOp,
    Project,
    op_from_dict,
    render,
)


def _parse_poly(s: str):
    """`--poly` 顶点串 → [(t, f), …]：分号分隔、逗号成对；数值交给 `PolyGainOp` 校验。"""
    pts = []
    for chunk in s.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        parts = chunk.split(",")
        if len(parts) != 2:
            raise ValueError("顶点必须是 \"t,f\" 数对，得到 %r" % chunk)
        pts.append((float(parts[0]), float(parts[1])))
    if len(pts) < 3:
        raise ValueError("多边形至少 3 个顶点，得到 %d" % len(pts))
    return pts


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


#: `--op` 规格里允许的算子名 → (最小必填参数, 说明)。
#: ⚠ 与 `build_parser` 的单算子开关**一一对应**：加新算子时两处都要改
#: （`docs/extending_edit.md` 的触点清单里有这一条）。
_OP_SPECS = {
    "gain": ("at", "增益（`gain=dB`）"),
    "band_gain": ("at,band", "时频增益（`gain=dB`）"),
    "poly_gain": ("points", "多边形增益（`points=t,f;t,f;...`，`gain=dB`）"),
    "declip": ("at", "去削波（可选 `ceiling`/`bandwidth`/`iterations`）"),
    "declick": ("at", "去咔哒（可选 `threshold`/`max_run`）"),
    "dehum": ("at,f0", "去嗡（可选 `q`/`harmonics=1,2,3`）"),
    "denoise": ("at,noise", "去噪（`noise=t0-t1`，可选 `reduction_db`/`no_floor_track`）"),
}


def _parse_op_specs(specs) -> str:
    """解析可重复的 `--op` 规格串，返回**规范化的描述**（供 `--save-project` 与报告）。

    语法（一条 `--op` = 一个算子，**出现顺序就是操作栈顺序**）：

        --op "kind@t0-t1:key=value,key=value"
        --op "declip@0-1:ceiling=0.5,bandwidth=21000"
        --op "declick@0-1:threshold=4"
        --op "dehum@0-1:f0=50,q=8,harmonics=1,2,3"
        --op "denoise@0-2:noise=0-0.3,reduction_db=15"
        --op "gain@0-1:gain=-3"
        --op "band_gain@0-1:band=1000-4000,gain=-6"
        --op "poly_gain:points=0.3,1000;1.5,1000;1.5,4500,gain=-6"

    ⚠ `harmonics=1,2,3` 里的逗号会与参数分隔符冲突 ⇒ 该键的取值**吃到下一个
    `键=` 之前**（见 `_split_kv`）。
    """
    ops = []
    for spec in specs:
        head, _, rest = spec.partition(":")
        kind, _, span = head.partition("@")
        kind = kind.strip()
        if kind not in _OP_SPECS:
            raise ValueError("不认识的算子 %r；可用：%s"
                             % (kind, "／".join(sorted(_OP_SPECS))))
        kv = _split_kv(rest) if rest else {}
        # 位置参数：时间选区从 `@t0-t1` 取，或从 `kv["at"]` 取
        t0 = t1 = None
        if span:
            rng = _parse_range(span)
            t0, t1 = rng
        elif "at" in kv:
            t0, t1 = _parse_range(kv.pop("at"))
        need = _OP_SPECS[kind][0].split(",")
        if "at" in need and (t0 is None or t1 is None):
            raise ValueError("`%s` 需要时间选区：写成 `%s@t0-t1` 或 `%s:at=t0-t1`"
                             % (kind, kind, kind))
        if "band" in need and "band" not in kv:
            raise ValueError("`%s` 需要 `band=f0-f1`" % kind)
        if "f0" in need and "f0" not in kv and "auto_f0" not in kv:
            raise ValueError("`%s` 需要 `f0=Hz`（或 `auto_f0=1`）" % kind)
        if "noise" in need and "noise" not in kv:
            raise ValueError("`%s` 需要 `noise=t0-t1`（只有噪声的一小段，用来学谱）" % kind)
        if "points" in need and "points" not in kv:
            raise ValueError("`%s` 需要 `points=t,f;t,f;...`（≥3 对顶点）" % kind)
        ops.append(_build_one_op(kind, t0, t1, kv, source_hint=None))
    return ops


def _split_kv(rest: str) -> dict:
    """把 `key=value,key=value` 切成 dict。

    ⚠ **`harmonics=1,2,3` 的逗号是取值的一部分**，不是分隔符：规则是
    "取值一直吃到下一个 `、` 或 `,` 之后紧跟 `标识符=` 的位置"。
    这样 `harmonics=1,2,3` 与 `harmonics=1,2,q=8` 都能正确切开。
    """
    import re

    out = {}
    for m in re.finditer(r"(\w+)=((?:(?!,?\w+=).)*)", rest):
        out[m.group(1)] = m.group(2).strip().rstrip(",")
    return out


def _build_one_op(kind: str, t0, t1, kv: dict, source_hint=None):
    """按 `kind` 与键值字典构造算子对象（`--op` 与单算子开关**共用**这一份）。

    `source_hint` 非空时，`declip` 的自动带宽与 `dehum` 的 `auto_f0` 会去读它。
    """
    kv = dict(kv)
    if kind == "gain":
        if "gain" not in kv:
            raise ValueError("`gain` 需要 `gain=dB`")
        return GainOp(t0, t1, float(kv["gain"]))
    if kind == "band_gain":
        if "gain" not in kv:
            raise ValueError("`band_gain` 需要 `gain=dB`")
        f0, f1 = _parse_freq(kv["band"])
        return BandGainOp(t0, t1, f0, f1, float(kv["gain"]))
    if kind == "poly_gain":
        pts = _parse_poly(kv["points"])
        return PolyGainOp(points=pts, gain_db=float(kv.get("gain", -6.0)))
    if kind == "declip":
        bw = float(kv["bandwidth"]) if "bandwidth" in kv else None
        src = "given"
        if bw is None:
            if source_hint is None:
                raise ValueError("`declip` 省略 `bandwidth` 时需要在单算子模式下运行"
                                 "（`--op` 里请显式给 `bandwidth=`）")
            bw, src = _auto_bandwidth(source_hint)
        dkw = {}
        if "iterations" in kv:
            dkw["iterations"] = int(kv["iterations"])
        return DeclipOp(t0, t1, bw,
                        float(kv["ceiling"]) if "ceiling" in kv else None,
                        bandwidth_source=src, **dkw)
    if kind == "declick":
        dkw = {}
        if "threshold" in kv:
            dkw["threshold"] = float(kv["threshold"])
        if "max_run" in kv:
            mr = int(kv["max_run"])
            dkw["max_run"] = None if mr == 0 else mr
        return DeclickOp(t0, t1, **dkw)
    if kind == "dehum":
        dkw = {}
        if "q" in kv:
            dkw["q"] = float(kv["q"])
        if "harmonics" in kv:
            try:
                dkw["harmonics"] = tuple(
                    int(x) for x in kv["harmonics"].split(",") if x.strip())
            except ValueError:
                raise ValueError("`harmonics` 要写成逗号分隔整数，例如 `1,2,3`"
                                 "（得到 %r）" % kv["harmonics"]) from None
        if "auto_f0" in kv:
            if source_hint is None:
                raise ValueError("`auto_f0=1` 只在单算子模式下可用"
                                 "（`--op` 里请显式给 `f0=`）")
            f0v, _ev = _auto_hum_f0(source_hint)
            dkw["f0_source"] = "auto"
        else:
            f0v = float(kv["f0"])
        return DehumOp(t0, t1, f0v, **dkw)
    if kind == "denoise":
        n0, n1 = _parse_range(kv["noise"])
        dkw = {}
        if "reduction_db" in kv:
            dkw["reduction_db"] = float(kv["reduction_db"])
        if "no_floor_track" in kv:
            dkw["track_floor"] = False
        return DenoiseOp(t0, t1, n0, n1, **dkw)
    raise ValueError("不认识的算子 %r" % kind)


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
    ap.add_argument("source", nargs="?", default=None,
                    help="源音频（只读，永不修改）。`--batch`／`--batch-help` 模式下可省略")
    ap.add_argument("dest", nargs="?", default=None,
                    help="输出文件（不许等于源）。`--batch` 模式下省略（目标写在清单里）")
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
    ap.add_argument("--no-floor-track", action="store_true",
                    help="关掉**逐帧噪声底跟踪**（默认开：噪声床在选区里台阶变化时跟随；"
                         "稳态素材上代价 ≤0.7 dB，关掉回到 v1 固定谱行为）")
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
    ap.add_argument("--batch", default=None,
                    help="**批处理**：JSON 作业清单（见 `--batch-help`），每项是一个独立的小"
                         "工程（source + 操作栈 + 目标）。逐项渲染、**互相隔离**（一项失败不会"
                         "拖累其余）；汇总每项的成功/失败。与 `--project`／逐操作参数互斥")
    ap.add_argument("--batch-help", action="store_true",
                    help="打印批处理作业清单的格式说明并退出")
    ap.add_argument("--save-project", default=None,
                    help="把**本次实际执行**的项目记录写成 JSON（可复算）。"
                         "⚠ 不能与 `--project` 同时用（读进来的记录不需要再存一遍）")
    ap.add_argument("--band", default=None,
                    help="频段（Hz，半开区间），给了就走**时频**路径，形如 4000-8000")
    ap.add_argument("--poly", default=None,
                    help='**多边形选区**（与 --gain 搭配，代替 --at/--band）：顶点串'
                         '="t1,f1;t2,f2;…"（秒,Hz，≥3 对；隐式闭合）。属内判据=射线法')
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
    ap.add_argument("--report", default=None,
                    help="把审计记录排成**人可读的 Markdown 报告**（机械排版：不新增判断、"
                         "不改写数值；None 排成'未测'；与 --audit 同用可得机器+人两份")
    ap.add_argument("--chain", default=None,
                    help="套用**修复链预设**（只含操作栈、不绑源的 JSON，`save_chain` 产出）："
                         "把同一套修复配方套到本命令给的源上；与算子 flag/--at/--project 互斥")
    ap.add_argument("--op", action="append", default=None, metavar="SPEC",
                    help="**复合算子**（可给多次，出现顺序就是操作栈顺序）："
                         "`kind@t0-t1:key=value,...`，例如 "
                         "`--op \"declick@0-1:threshold=4\" "
                         "--op \"declip@0-1:ceiling=0.5,bandwidth=21000\"`。"
                         "可用算子：gain / band_gain / poly_gain / declip / declick / "
                         "dehum / denoise（各算子必填参数见 `--op` 说明或 "
                         "docs/scripting.md）。与单算子开关／`--project`／`--chain` 互斥。"
                         "⚠ 多算子走**链式渲染**（每步一个 FLOAT 中间层），"
                         "单算子路径仍是全 float64。")
    ap.add_argument("--verify-audit", action="append", default=None, metavar="JSON",
                    help="**核对审计记录**（可给多次）：重算 `audit_digest` 并比对，"
                         "输出 PASS/FAIL。这是「跨机核对同一份修复」的 CLI 入口——"
                         "拿到对方给的审计 JSON，一条命令就能验它是不是同一份修复、"
                         "同一份输入、同一份实现（三个 digest 分别对应）。"
                         "退出码 0=全 PASS，1=有 FAIL。不渲染任何音频。"
                         "参数可给**目录**（递归找 *.json），便于整批核对。")
    ap.add_argument("--verify-json", default=None, metavar="OUT",
                    help="把 `--verify-audit` 的结果另存为**机器可读 JSON**"
                         "（CI 直接消费，不必解析人读文本）。"
                         "结构：`{ok, n_total, n_bad, records:[{path, ok, "
                         "audit_digest{claimed,recomputed,match}, "
                         "kernel_digest{recorded,local,same_implementation}, "
                         "source_sha256, tool_version}]}`")
    ap.add_argument("--jobs", type=int, default=0,
                    help="批处理的并行进程数（0=自动按 CPU 核数，1=串行）。"
                         "每项作业互相隔离（各自独立进程），失败不影响其余")
    ap.add_argument("--version", action="version",
                    version="audiocheck %s" % __version__,
                    help="打印版本并退出")
    ap.add_argument("--doctor", action="store_true",
                    help="环境自检（依赖/编解码器/后端）后退出")
    return ap


def _with_timestamp(audit: dict) -> dict:
    """CLI 层给审计补 **UTC 时间戳**（SWGDE 口径）——`render` 保持纯函数，墙钟属调用方。"""
    from datetime import datetime, timezone
    out = dict(audit)
    out["timestamp_utc"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return out


def _save_report(a, audit: dict) -> int:
    """`--report`：把审计记录排成人可读 Markdown（机械排版，见 `core/edit/report.py`）。"""
    if not a.report:
        return 0
    from audiocheck.core.edit.report import write_report
    try:
        write_report(audit, a.report)
    except OSError as e:
        print("审计报告写不出去：%s" % e)
        return 2
    print("报告  %s" % a.report)
    return 0


def _main_ops(a) -> int:
    """`--op`：**复合算子**（可重复；出现顺序就是操作栈顺序）。

    ## 为什么单独一个入口

    单算子开关（`--declip` / `--declick` / …）是"一次修一件事"的交互式入口，
    用 `n_modes != 1` 强制互斥。复合修复此前只能写项目记录 JSON 或修复链文件
    （`--project` / `--chain`）——对"临时试一套配方"太绕。
    `--op` 把这件事压成一条命令：

        audiocheck-edit-cli in.wav out.wav \
            --op "declick@0-1:threshold=4" \
            --op "declip@0-1:ceiling=0.5,bandwidth=21000" \
            --op "dehum@1-2:f0=50,q=8"

    ## 语义必须是"栈顺序"，不是"参数顺序"

    与 `--project` 一样走 `Project(source, ops)`：`ops` 里的顺序就是用户给的
    顺序，但**渲染时按 `render.PHASE` 做规范排序**（脉冲类先、稳态嗡最后），
    被重排时审计记 `reordered_from`。这是既有行为，`--op` 不改变它。

    ## 校验（与单算子开关互斥，且每项都必须在解析期失败）

    参数错误一律**在渲染之前**报出来（退出码 2）——不能跑一半才发现第三项写错。
    """
    # 与其它入口互斥：混用会让"到底按哪个跑"变成猜测
    n_single = ((1 if a.gain is not None else 0) + (1 if a.declip else 0)
                + (1 if a.declick else 0) + (1 if a.dehum else 0)
                + (1 if a.denoise else 0))
    if n_single or a.at is not None or a.poly is not None or a.band is not None:
        print("参数错误：`--op` 与单算子开关（`--gain`/`--declip`/`--declick`/"
              "`--dehum`/`--denoise`）及 `--at`/`--band`/`--poly` 互斥"
              "（复合规格里已经写了每项的选区）")
        return 2
    if a.chain is not None or a.project is not None:
        print("参数错误：`--op` 与 `--chain`/`--project` 互斥（都是「给一整个操作栈」）")
        return 2

    try:
        ops = _parse_op_specs(a.op)
    except (ValueError, TypeError) as e:
        print("参数错误：%s" % e)
        return 2

    kw = {}
    if a.n_fft is not None:
        kw["n_fft"] = a.n_fft
    if a.hop is not None:
        kw["hop"] = a.hop

    try:
        proj = Project(a.source, list(ops))
        audit = render(proj, a.dest, subtype=a.subtype, overwrite=a.overwrite,
                       dither=a.dither, **kw)
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
    print("操作栈 %d 项（`--op` 顺序；渲染按 `render.PHASE` 规范排序）" % len(ops))
    _print_dither(audit)
    print("长度  %d 帧（%.3f s）" % (audit["frames"], audit["duration_s"]))
    _warn_if_noop(audit)
    if audit.get("stages"):
        _print_chain(audit)

    if a.audit:
        try:
            with open(a.audit, "w", encoding="utf-8") as f:
                json.dump(_with_timestamp(audit), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    rc = _save_report(a, audit)
    if rc:
        return rc
    if a.save_project:
        try:
            with open(a.save_project, "w", encoding="utf-8") as f:
                json.dump(proj.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("项目记录写不出去：%s" % e)
            return 2
        print("项目  %s" % a.save_project)
    return 0


def _main_chain(a) -> int:
    """`--chain`：把**修复链预设**（无源操作栈）套到本命令的源上渲染。

    与 `--project` 的分工：项目记录 = 源 + 栈（复算一个具体产物，源必须一致）；
    修复链 = 栈（配方，套到**任何**源——同一套修复批量过曲库的入口）。
    渲染走的是同一条链式路径（规范顺序 + 审计），没有任何"链特例"。
    """
    from audiocheck.core.edit.ops import Project, load_chain

    try:
        ops = load_chain(a.chain)
    except OSError as e:
        print("修复链读不了：%s" % e)
        return 2
    except ValueError as e:
        print("拒绝：%s" % e)
        return 2
    proj = Project(a.source, list(ops))
    kw = {}
    if a.n_fft is not None:
        kw["n_fft"] = a.n_fft
    if a.hop is not None:
        kw["hop"] = a.hop
    try:
        audit = render(proj, a.dest, subtype=a.subtype,
                       overwrite=a.overwrite, dither=a.dither, **kw)
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
    print("链    %s（%d 个操作）" % (a.chain, len(ops)))
    print("长度  %d 帧（%.3f s）" % (audit["frames"], audit["duration_s"]))
    _warn_if_noop(audit)
    # ⚠ **`--chain` 路径此前不打印阶段明细**：`_print_chain` 只在 `--project`
    # 分支被调用，于是用修复链预设跑的用户看不到"每个阶段做了什么、覆盖多少帧"——
    # 而那正是链式渲染最需要交代的东西（多个算子按规范顺序串行）。
    # 审计 JSON 里有 `stages`，报告却不说，等于把可审计性留在了机器格式里。
    if audit.get("stages"):
        _print_chain(audit)
    if a.audit:
        try:
            with open(a.audit, "w", encoding="utf-8") as f:
                json.dump(_with_timestamp(audit), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    rc = _save_report(a, audit)
    if rc:
        return rc
    if a.save_project:
        try:
            with open(a.save_project, "w", encoding="utf-8") as f:
                json.dump(proj.to_dict(), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("项目记录写不出去：%s" % e)
            return 2
        print("项目  %s" % a.save_project)
    return 0


def main(argv=None) -> int:
    _setup_console()
    a = build_parser().parse_args(argv)
    if a.doctor:
        # 复用主 CLI 的 doctor（同一套依赖/编解码器检查，不重复实现）
        from .cli import doctor
        return doctor()
    if a.verify_audit:
        return _main_verify(a)
    if a.batch_help:
        _print_batch_help()
        return 0
    if a.batch is not None:
        return _main_batch(a)
    if a.project is not None:
        return _main_project(a)
    if a.op:
        return _main_ops(a)
    if a.chain is not None:
        n_modes = ((1 if a.gain is not None else 0) + (1 if a.declip else 0)
                   + (1 if a.declick else 0) + (1 if a.dehum else 0)
                   + (1 if a.denoise else 0))
        if n_modes != 0 or a.at is not None:
            print("参数错误：`--chain` 不能与算子 flag / `--at` 同给"
                  "（链文件里已经是完整操作栈）")
            return 2
        return _main_chain(a)
    if a.poly is not None and (a.at is not None or a.band is not None):
        print("参数错误：`--poly` 自带完整选区，不能与 `--at`/`--band` 同给")
        return 2
    if a.poly is not None and a.gain is None:
        print("参数错误：`--poly` 与 `--gain` 搭配使用（多边形内施加增益）")
        return 2
    if a.at is None and a.poly is None:
        print("参数错误：需要 `--at`（时间选区，例如 12.5-14.0）或 `--poly`（多边形选区）；"
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
        t0, t1 = _parse_range(a.at) if a.at is not None else (0.0, 0.0)
        f0, f1 = _parse_freq(a.band) if a.band else (None, None)
        n0, n1 = _parse_range(a.noise_at) if a.noise_at else (None, None)
        poly_pts = _parse_poly(a.poly) if a.poly is not None else None
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
            if a.no_floor_track:
                dkw["track_floor"] = False
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
        elif a.poly is not None:
            op = PolyGainOp(points=poly_pts, gain_db=a.gain)
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
    _warn_if_noop(audit)
    if audit["domain"] == "band":
        for op in audit["ops"]:
            m = op.get("attenuation_measured_db")
            if op.get("kind") == "poly_gain":
                print("操作  多边形选区（%d 顶点）：设定 %+.1f dB ｜ 实测 %s ｜ 改动 %d 个时频点"
                      % (len(op.get("points", [])), op["gain_db"],
                         "—" if m is None else "%+.2f dB" % m, op["applied_tf_points"]))
            else:
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
                json.dump(_with_timestamp(audit), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    rc = _save_report(a, audit)
    if rc:
        return rc
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


def _warn_if_noop(audit: dict) -> None:
    """★ **no-op 守卫**：选区完全落在文件之外时显式喊出来。

    渲染层把越界区间夹紧到空集（`applied_frames == 0`）——这本身是正确行为
    （不该越界改文件）；但 CLI 此前仍**静默成功**（退出码 0、报告一片正常）
    ⇒ 用户以为修了，实际一个样本都没动。

    实测：2 秒文件给 `--at 5.0-9.0` ⇒ `applied_frames=0`、`rc=0`、报告全绿。
    这是"看起来做了、其实没做"的最坏形态，比报错更难发现（报错至少会被看到）。

    只对**有选区的算子**判（gain/declip/declick/dehum/denoise/poly 都有
    `applied_frames`；纯格式转换的空栈没有，不警告）。
    """
    ops = audit.get("ops") or []
    if not ops:
        return                                    # 空栈 = 纯转格式，不是 no-op
    # 只看"应该有作用"的算子（gain_db == 0 是用户显式要求不变，见 GainOp 语义）
    actionable = [o for o in ops
                  if not (o.get("kind") == "gain" and o.get("gain_db") == 0)]
    if not actionable:
        return
    total = sum(int(o.get("applied_frames") or 0) for o in actionable)
    if total > 0:
        return
    dur = audit.get("duration_s")
    print("⚠ **选区没有命中任何样本（no-op）**：目标文件与源逐位相同。"
          "检查 `--at`/`--band`/`--poly` 是否落在素材范围内"
          "（本文件时长 %s s）" % ("?" if dur is None else "%.3f" % dur))


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


def _print_batch_help() -> None:
    print("批处理作业清单格式（JSON 数组，每项一个小工程）：")
    print()
    print('  [')
    print('    { "source": "in1.wav", "dest": "out1.wav",')
    print('      "ops": [ {"kind":"declip","t0":3.0,"t1":3.5,"ceiling":null,')
    print('                "bandwidth":18000,"bandwidth_source":"given","iterations":128} ],')
    print('      "subtype": "PCM_16", "overwrite": false },')
    print('    { "source": "in2.flac", "dest": "out2.flac", "ops": [ ... ] }')
    print('  ]')
    print()
    print("  ⚠ 每项的 `ops` 是 `Project.to_dict` 里 `ops` 数组的**同一个格式**（kind + 精确字段集）。")
    print("  ⚠ 与 `--project` 同源：记录里的 `source` 被忽略、**改用作业项自己的 source**")
    print("    （批处理里 source 必须可枚举，不藏在记录里）；`dest` 必须给。")
    print("  ⚠ 通用项：`subtype`（默认沿用各 source）、`overwrite`（默认 false）、`dither`（默认 false）。")
    print()
    print("退出码：0 = 全部成功 ｜ 2 = 清单本身不可用（读不了/不是数组/缺字段）｜")
    print("        否则仍 0，但汇总会列出**逐项**的成功/失败（失败项不阻断其余）。")


def _migrate_and_parse(op_dict, ver: int):
    """批处理作业的 op 解析：先按作业版本显式迁移，再走严格 schema（与 --project 同源）。"""
    from audiocheck.core.edit.ops import _migrate
    return op_from_dict(_migrate(op_dict, ver))


def _verify_one_audit(path: str) -> tuple[bool, str]:
    """核对**一份**审计 JSON：重算 `audit_digest` 并与记录比对。

    返回 `(ok, 人类可读说明)`。

    ## 为什么这是核心能力的最后一公里

    审计记录里早就有 `audit_digest`（剔除路径/时刻/产物哈希后的确定性摘要）、
    `kernel_digest`（实现自证）与 `evidence_levels`（每个数字的可信度）。
    但**此前没有任何 CLI 能核对它们**——用户拿到对方给的审计 JSON，只能自己
    写 Python 脚本重算。而"跨机核对同一份修复"正是这个项目区别于普通音频工具
    的承诺，承诺却要用户自己实现，等于没有。

    ## 三个 digest 分别证明什么（口径必须分清）

    * `audit_digest`：**同一份修复**（同输入内容 + 同操作栈 + 同产物特征）。
      它剔除了 `timestamp_utc`/`dest`/`dest_sha256`/`source`——这些随落盘漂移。
    * `kernel_digest`：**同一份实现**（渲染模块文件自身的哈希）。摘要相同但
      kernel 不同 ⇒ 两个不同版本的代码给出了相同结果（可能是巧合，也可能是
      有人在别处改了内核）。
    * `source_sha256`：**同一份输入内容**（跳过 PEAK 的 WAV 内容哈希）。

    所以核对要分三层报，而不是笼统给一个"通过"。
    """

    from .core.edit.render import audit_digest, kernel_self_digest

    def _fail(reason: str) -> tuple:
        return False, reason, {"path": path, "ok": False, "reason": reason,
                               "checks": {}}

    try:
        with open(path, encoding="utf-8") as f:
            rec = json.load(f)
    except OSError as e:
        return _fail("读不了 %s：%s" % (path, e))
    except json.JSONDecodeError as e:
        return _fail("%s 不是合法 JSON：%s" % (path, e))

    if not isinstance(rec, dict):
        return _fail("%s 顶层不是对象（审计记录应是 dict）" % path)
    claimed = rec.get("audit_digest")
    if not claimed:
        return _fail("%s 里没有 `audit_digest` 字段——"
                     "这是 v1.1.0 之前或手改过的记录，无法核对" % path)

    # 重算：与 `audit_digest()` 完全同口径（它自己剔除那四个字段）
    recomputed = audit_digest(rec)
    ok = recomputed == claimed

    lines = []
    lines.append("%s  audit_digest %s"
                 % ("✓" if ok else "✗", "一致" if ok else "**不一致**"))
    lines.append("    记录 %s" % claimed)
    if not ok:
        lines.append("    复算 %s" % recomputed)
        # 定位差异：逐字段比对 canonical_json 太粗，给出参与摘要的键集差异
        skip = ("timestamp_utc", "dest", "dest_sha256", "source")
        keys = sorted(k for k in rec if k not in skip)
        lines.append("    （参与摘要的字段 %d 个；摘要不一致说明记录被改过、"
                     "或用了不同版本的内核）" % len(keys))
        # 版本提示：`audit_digest` 的口径在 v1.1.5 变过（自指字段 + 计算顺序 +
        # 链式重算）。旧版本产出的记录**必然**核不过——这要说清，否则用户
        # 会以为自己手里的文件被篡改了。
        tv = rec.get("tool_version")
        if tv and tv < "1.1.5":
            lines.append("    ⚠ 该记录由 audiocheck %s 产出；`audit_digest` 的"
                         "计算口径在 **1.1.5** 变过（剔除自指字段 + 全部字段写完后再算"
                         " + 链式重算）⇒ **旧记录必然核不过**，这不代表文件被篡改。"
                         "要核对请用产出它的那个版本，或重新渲染生成新记录。" % tv)

    # kernel_digest：实现自证
    kd_rec = rec.get("kernel_digest")
    kd_now = kernel_self_digest()
    if kd_rec:
        same = kd_rec == kd_now
        lines.append("    内核 %s（记录 %s… ｜ 本机 %s…）"
                     % ("**同一实现**" if same else "**不同实现**",
                        kd_rec[:12], kd_now[:12]))
    else:
        lines.append("    内核 （记录里没有 kernel_digest）")

    # 输入内容
    ss = rec.get("source_sha256")
    if ss:
        lines.append("    输入内容 sha256 %s…" % ss[:16])

    info = {
        "path": path,
        "ok": ok,
        "audit_digest": {"claimed": claimed, "recomputed": recomputed,
                         "match": ok},
        "kernel_digest": ({"recorded": kd_rec, "local": kd_now,
                           "same_implementation": kd_rec == kd_now}
                          if kd_rec else None),
        "source_sha256": ss,
        "tool_version": rec.get("tool_version"),
        "n_digest_fields": len(keys) if not ok else None,
    }
    return ok, "\n".join(lines), info


def _main_verify(a) -> int:
    """`--verify-audit`：核对一份或多份审计 JSON（不渲染任何音频）。

    参数可以是 **JSON 文件**，也可以是**目录**（递归找 `*.json`）——
    后者是批处理的自然搭配：一轮批处理产出几十份审计，逐份敲路径不现实。

    `--verify-json OUT` 把结果写成**机器可读**报告（CI 直接消费，不必解析文本）：

        {"ok": false, "n_total": 3, "n_bad": 1,
         "records": [{"path": "...", "ok": true,
                      "audit_digest": {"claimed": "...", "recomputed": "...",
                                       "match": true},
                      "kernel_digest": {"recorded": "...", "local": "...",
                                        "same_implementation": true},
                      "source_sha256": "...", "tool_version": "1.1.5"}, ...]}

    ⚠ 文本输出仍是默认（人读）；JSON 是**附加**出口，不替换它。
    """
    targets: list = []
    for arg in a.verify_audit:
        if os.path.isdir(arg):
            found = sorted(
                os.path.join(root, f)
                for root, _dirs, files in os.walk(arg)
                for f in files if f.lower().endswith(".json"))
            if not found:
                print("[!] 目录里没有 .json：%s" % arg)
            targets.extend(found)
        else:
            targets.append(arg)

    if not targets:
        print("没有可核对的审计记录")
        return 2

    ok_all = True
    n_bad = 0
    infos: list = []
    for i, path in enumerate(targets):
        if i:
            print()
        ok, msg, info = _verify_one_audit(path)
        print(msg)
        ok_all = ok_all and ok
        infos.append(info)
        if not ok:
            n_bad += 1
    print()
    print("核对结果：%s（%d 份%s）"
          % ("全部一致" if ok_all else "**有不一致 %d 份**" % n_bad, len(targets),
             "，来自目录递归" if any(os.path.isdir(x) for x in a.verify_audit) else ""))

    out = getattr(a, "verify_json", None)
    if out:
        report = {"ok": ok_all, "n_total": len(targets), "n_bad": n_bad,
                  "records": infos}
        try:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
            print("机器可读报告  %s" % out)
        except OSError as e:
            print("机器可读报告写不出去：%s" % e)
            return 2
    return 0 if ok_all else 1


#: `--batch` 的 **CLI 级覆盖开关**。作业项里也可以写 `"overwrite": true`；
#: 两者取"或"（命令行是"这批都允许覆盖"的批量意图）。
#: ⚠ 必须是**模块级**：`ProcessPoolExecutor` 要把 worker 序列化送进子进程，
#: 闭包捕获的变量不会跟过去。
_CLI_OVERWRITE = False


def _batch_job_worker(ij) -> tuple:
    """**模块级** worker（进程池要求可 pickle）。

    ⚠ 不能写成 `_main_batch` 里的闭包：`ProcessPoolExecutor` 需要把可调用对象
    序列化送进子进程，闭包无法 pickle（会 `AttributeError: Can't pickle local
    object`）。所以逻辑必须落到模块顶层——这也让串行/并行共用同一份实现。
    """
    i, job = ij
    tag = job.get("dest") or job.get("source") or ("#%d" % i)
    try:
        ver = int(job.get("version", 1))
        if ver not in (1, 2):
            raise ValueError("不认识的作业版本 %r（支持 1 / 2）" % job.get("version"))
        ops = job.get("ops", [])
        proj = Project(job["source"], [_migrate_and_parse(x, ver) for x in ops])
        audit = render(proj, job["dest"],
                       subtype=job.get("subtype"),
                       # 命令行 `--overwrite` 与作业项里的 `"overwrite"` 取**或**：
                       # 此前只读作业项 ⇒ 用户加了 `--overwrite` 重跑批处理
                       # **完全无效**（实测：重跑时全部报"目标已存在"）。
                       # 命令行开关的语义是"这批作业都允许覆盖"，优先级最高。
                       overwrite=bool(job.get("overwrite", False)) or _CLI_OVERWRITE,
                       dither=bool(job.get("dither", False)))
        if job.get("audit"):
            with open(job["audit"], "w", encoding="utf-8") as fh:
                json.dump(audit, fh, ensure_ascii=False, indent=2)
        if job.get("report"):
            from audiocheck.core.edit.report import write_report
            write_report(audit, job["report"])
    except (ValueError, json.JSONDecodeError, FileExistsError, OSError) as e:
        return False, tag, str(e)
    except Exception as e:                              # noqa: BLE001
        return False, tag, "**源读不了**：%s" % e
    return True, tag, "→ %s（%d 个操作 ｜ %d 帧）" % (
        audit["dest"], len(audit["ops"]), audit["frames"])


def _main_batch(a) -> int:
    """`--batch`：逐项渲染一个作业清单，**互相隔离**（一项失败不影响其余）。

    这就是 `Project.to_dict` 记录的可复算能力在"多条音轨"上的延展：每条作业就是一个
    独立的小工程，复用 `--project` 已经验证过的 `Project.from_dict` + `render` 链路。
    与单条 `--project` 的区别：
    * source 在作业项里给（不藏在记录里），更符合"批处理要枚举一堆输入"的用法；
    * 任一项抛异常都被**就地接住**，记成该项的失败，继续跑下一项；
    * 末尾汇总"几项成功 / 几项失败 + 失败原因"，让用户一眼看到哪条没跑成。
    """
    global _CLI_OVERWRITE
    _CLI_OVERWRITE = bool(getattr(a, "overwrite", False))
    if a.dest is not None and a.source is not None:
        print("参数错误：`--batch` 是清单驱动，不能再用 `<源> <目标>` 位置参数"
              "（把源/目标写进清单每一项）")
        return 2
    if a.project is not None or a.at is not None or a.gain is not None \
            or a.declip or a.declick or a.dehum or a.denoise:
        print("参数错误：`--batch` 与 `--project`／逐操作参数互斥（清单自己决定每项做什么）")
        return 2
    try:
        with open(a.batch, encoding="utf-8") as f:
            jobs = json.load(f)
    except OSError as e:
        print("批处理清单读不了：%s" % e)
        return 2
    except json.JSONDecodeError as e:
        print("批处理清单不是合法 JSON：%s" % e)
        return 2
    if not isinstance(jobs, list) or not jobs:
        print("批处理清单必须是非空数组（每项一个小工程），得到 %r" % type(jobs).__name__)
        return 2

    # 先做**结构**校验：每一项都必须是 dict 且带 `source`/`dest`。这是"清单本身
    # 不可用"的判定（帮助里写的 `缺字段 ⇒ 2`）——只要有一项结构残缺就整体拒绝，
    # 不让用户带着坏清单跑掉 99 项才发现问题。运行期故障（kind 不认识、源读不了、
    # 写出失败）则留在下面的逐项 try/except 里**互相隔离**，不阻断其余。
    for i, job in enumerate(jobs):
        if not isinstance(job, dict):
            print("批处理清单第 %d 项不是对象（dict），得到 %r" % (i, type(job).__name__))
            return 2
        if "source" not in job or "dest" not in job:
            print("批处理清单第 %d 项缺 `source` 或 `dest`，结构不可用" % i)
            return 2

    n_ok = 0
    n_fail = 0
    failures = []

    def _run_job(i_job):
        """跑**一项**作业，返回 `(ok, tag, 详情)`。纯函数式：不碰外部计数。

        抽出来是为了让串行/并行走**同一份逻辑**（并行不能改变判定口径）。
        """
        i, job = i_job
        tag = job.get("dest") or job.get("source") or ("#%d" % i)
        try:
            # 作业可带 `"version": 1|2`（缺省 1 = 历史清单：denoise 无 track_floor 键
            # ⇒ 显式迁移补 False，旧清单可复算）。v2 = 严格 schema（缺键按缺字段拒）。
            ver = int(job.get("version", 1))
            if ver not in (1, 2):
                raise ValueError("不认识的作业版本 %r（支持 1 / 2）" % job.get("version"))
            ops = job.get("ops", [])
            proj = Project(job["source"], [_migrate_and_parse(x, ver) for x in ops])
            audit = render(proj, job["dest"],
                           subtype=job.get("subtype"),
                           # 命令行 `--overwrite` 与作业项里的 `"overwrite"` 取**或**：
                       # 此前只读作业项 ⇒ 用户加了 `--overwrite` 重跑批处理
                       # **完全无效**（实测：重跑时全部报"目标已存在"）。
                       # 命令行开关的语义是"这批作业都允许覆盖"，优先级最高。
                       overwrite=bool(job.get("overwrite", False)) or _CLI_OVERWRITE,
                           dither=bool(job.get("dither", False)))
            if job.get("audit"):
                with open(job["audit"], "w", encoding="utf-8") as fh:
                    json.dump(audit, fh, ensure_ascii=False, indent=2)
            if job.get("report"):
                from audiocheck.core.edit.report import write_report
                write_report(audit, job["report"])
        except (ValueError, json.JSONDecodeError, FileExistsError, OSError) as e:
            return False, tag, str(e)
        except Exception as e:                          # noqa: BLE001 —— 源读不了等杂项
            return False, tag, "**源读不了**：%s" % e
        return True, tag, "→ %s（%d 个操作 ｜ %d 帧）" % (
            audit["dest"], len(audit["ops"]), audit["frames"])

    jobs_n = max(1, int(getattr(a, "jobs", 0) or 0))
    if jobs_n == 0:
        jobs_n = min(os.cpu_count() or 1, len(jobs))

    if jobs_n <= 1 or len(jobs) == 1:
        for ij in enumerate(jobs):
            ok, tag, detail = _run_job(ij)
            if ok:
                n_ok += 1
                print("✓ [%s] 成功 %s" % (tag, detail))
            else:
                n_fail += 1
                failures.append((tag, detail))
                print("✗ [%s] 失败：%s" % (tag, detail))
    else:
        # **并行**：每个作业是独立小工程（各自的源/目标/审计文件），互不共享状态。
        #
        # ⚠ **打包环境（PyInstaller / frozen）必须降级为线程池**：
        # Windows 的 `multiprocessing` 用 spawn 启动子进程——它会**重新执行 exe**。
        # 未受 `if __name__ == "__main__"` 与 `multiprocessing.freeze_support()`
        # 保护的入口会在子进程里重新跑一遍主逻辑，PyInstaller 的引导程序随后
        # 把整个 worker 杀掉：
        #   `A process in the process pool was terminated abruptly`
        # （实测：打包后的 exe 跑 `--batch --jobs 3`，3 项**全部**报这个错，
        #  而源码运行完全正常——只在发布版复现，是最难发现的一类。）
        #
        # 渲染是 numpy/scipy 密集：它们**大量释放 GIL**（numpy 线程安全文档：
        # "Many NumPy operations release the GIL"），所以线程池在这里有真实并行度；
        # 且 `EditSession` 的前缀缓存已按目录隔离，不会跨线程互删。
        frozen = bool(getattr(sys, "frozen", False))
        if frozen:
            from concurrent.futures import ThreadPoolExecutor, as_completed
            print("并行 %d 路（共 %d 项；打包版用线程池——进程池在 frozen 下"
                  "会 spawn 重启 exe，需 freeze_support）" % (jobs_n, len(jobs)))
            pool_cls = ThreadPoolExecutor
        else:
            from concurrent.futures import ProcessPoolExecutor, as_completed
            print("并行 %d 路（共 %d 项；每项独立进程）" % (jobs_n, len(jobs)))
            pool_cls = ProcessPoolExecutor
        results = {}
        with pool_cls(max_workers=jobs_n) as ex:
            futs = {ex.submit(_batch_job_worker, ij): ij[0]
                    for ij in enumerate(jobs)}
            for fut in as_completed(futs):
                i = futs[fut]
                try:
                    results[i] = fut.result()
                except Exception as e:                  # noqa: BLE001
                    results[i] = (False, "#%d" % i, "worker 崩溃：%s" % e)
        for i in range(len(jobs)):                      # 按索引收拢（报告顺序稳定）
            ok, tag, detail = results.get(i, (False, "#%d" % i, "未执行"))
            if ok:
                n_ok += 1
                print("✓ [%s] 成功 %s" % (tag, detail))
            else:
                n_fail += 1
                failures.append((tag, detail))
                print("✗ [%s] 失败：%s" % (tag, detail))

    print("\n批处理汇总：%d 项成功 ／ %d 项失败（共 %d 项）"
          % (n_ok, n_fail, len(jobs)))
    if failures:
        print("失败明细：")
        for tag, why in failures:
            print("  - %s：%s" % (tag, why))
    # 全部成功才 0；**有失败 ⇒ 1**（脚本/CI 靠退出码判断，返回 0 等于把失败吞掉）。
    # 只有"清单本身不可用"才是 2（上面已 return）。
    #
    # ⚠ 这里原本写的是 `return 0 if n_fail == 0 else 0`——**两个分支都是 0**，
    # 即"无论成败都报成功"。注释写着"全部成功才 0"，实现却永远返回 0：
    # 清单里 99 项炸掉 98 项，`audiocheck-edit --batch x.json && echo ok`
    # 照样打印 ok。这是最容易被自动化吞掉的一类缺陷（CI 永远绿）。
    return 0 if n_fail == 0 else 1


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
    _warn_if_noop(audit)
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
                json.dump(_with_timestamp(audit), f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("审计记录写不出去：%s" % e)
            return 2
        print("审计  %s" % a.audit)
    rc = _save_report(a, audit)
    if rc:
        return rc
    return 0


if __name__ == "__main__":
    sys.exit(main())
