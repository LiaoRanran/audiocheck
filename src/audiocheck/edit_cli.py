"""编辑器命令行（P0 地基的**用户入口**）。

    audiocheck-edit <源> <目标> --gain -6 --at 12.5-14.0 [--subtype PCM_16] [--overwrite] [--audit 记录.json]
    audiocheck-edit <源> <目标> --gain -20 --at 12.5-14.0 --band 4000-8000     # 时频选区

三条约定（与 `core/edit/render.py` 一致，不在这一层偷改）
--------------------------------------------------------
* **非破坏**：目标 == 源一律拒绝（渲染层硬保证）；目标已存在时默认不覆盖（`--overwrite` 才覆盖）。
* **可审计**：跑完把审计记录**打出来**；`--audit` 再另存一份 JSON（"可审计的修复"的第一片）。
  频域编辑还会打出**实测的 STFT 往返残差**——"频谱编辑不是位精确的"这条要摆到台面上。
* **不许 traceback**：源读不了 / 参数非法 → **一行可读信息** + 明确退出码。

退出码：`0` 成功 ｜ `2` 参数或用法问题 ｜ `4` 源读不了 ｜ `5` 目标已存在且未给 `--overwrite`。
"""
from __future__ import annotations

import argparse
import json
import sys

from .cli import _setup_console
from .core.edit import BandGainOp, GainOp, Project, render


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


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="audiocheck-edit",
                                 description="非破坏编辑：把操作渲染到新文件，并给出审计记录")
    ap.add_argument("source", help="源音频（只读，永不修改）")
    ap.add_argument("dest", help="输出文件（不许等于源）")
    ap.add_argument("--gain", type=float, required=True,
                    help="选区增益（dB）。0 = 无操作（no-op，逐位不变）")
    ap.add_argument("--at", required=True, help="时间选区（秒，半开区间），形如 12.5-14.0")
    ap.add_argument("--band", default=None,
                    help="频段（Hz，半开区间），给了就走**时频**路径，形如 4000-8000")
    ap.add_argument("--n-fft", type=int, default=None,
                    help="时频路径的 STFT 窗长（默认 4096；只对 --band 有效）")
    ap.add_argument("--hop", type=int, default=None,
                    help="时频路径的 hop（默认 n_fft//4；只对 --band 有效）")
    ap.add_argument("--subtype", default=None, help="目标编码（默认沿用源，例如 PCM_16）")
    ap.add_argument("--overwrite", action="store_true", help="允许覆盖已存在的目标")
    ap.add_argument("--audit", default=None, help="把审计记录另存为 JSON")
    return ap


def main(argv=None) -> int:
    _setup_console()
    a = build_parser().parse_args(argv)

    try:
        t0, t1 = _parse_range(a.at)
        f0, f1 = _parse_freq(a.band) if a.band else (None, None)
    except ValueError as e:
        print("参数错误：%s" % e)
        return 2

    kw = {}
    if a.band:
        # 默认值由 `core/edit/render.py` 决定，这一层不重复一遍（免得两处默认值分叉）
        if a.n_fft is not None:
            kw["n_fft"] = a.n_fft
        if a.hop is not None:
            kw["hop"] = a.hop
    try:
        op = (BandGainOp(t0, t1, f0, f1, a.gain) if a.band
              else GainOp(t0, t1, a.gain))
        audit = render(Project(a.source, [op]), a.dest,
                       subtype=a.subtype, overwrite=a.overwrite, **kw)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
