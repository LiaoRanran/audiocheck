#!/usr/bin/env python3
"""阈值区分力普查（C1 / P0-1）。

## 它回答什么问题

`AGENTS.md` §3.4 立的规矩：

> 一个阈值如果把人全判到同一侧，它就是没用的。**新加任何阈值都要跑一次这个检验。**

本项目此前**一次都没跑过**（内核审查报告 P0-1）。本脚本补上，口径对标同族的
`headphone-lab/src/audibility_survey.py`：那边会明确打印"该阈值没有区分力"。

## 判据（三条，逐字段）

1. **越阈比例**：语料里有多少条落在阈值的"触发侧"；
2. **双向分布**：两侧都要有样本，阈值才有信息量；
3. **是否只判到同一侧** —— 全部落在一侧 ⇒ **打印"无区分力"**。

## 语料

`examples/` 里只有文档、没有音频，所以**自建合成集**：按每个阈值的两侧
**定向造样**（带限/削波/单声道/反相/静音/不同采样率/不同位深…）。
合成文件写在临时目录，**不入库**（体积与版权）；真实文件可选补入，只用于统计。

    python packaging/threshold_survey.py            # 报数（恒返回 0）
    python packaging/threshold_survey.py --strict   # 有"无区分力"字段则返回 1
    python packaging/threshold_survey.py --json out.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile

import numpy as np
import soundfile as sf

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.config import Thresholds  # noqa: E402
from audiocheck.core import engine  # noqa: E402


# --------------------------------------------------------------------- 合成语料
def _bandlimited(fs: int, cutoff_hz: float, seconds: float = 3.0, seed: int = 0):
    """白噪经**理想低通**（FFT 截断）⇒ 真正的砖墙，正好用来探截止类阈值。"""
    n = int(fs * seconds)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    fr = np.fft.rfftfreq(n, 1.0 / fs)
    X[fr > cutoff_hz] = 0.0
    x = np.fft.irfft(X, n)
    return x / (np.abs(x).max() + 1e-12)


def _write(path: str, x: np.ndarray, fs: int, *, mono: bool = False,
           antiphase: bool = False, level_db: float = -20.0, clip: float = 0.0,
           subtype: str | None = None, quantize_bits: int | None = None):
    """写一个合成文件。

    ⚠ `quantize_bits` **必须最后做**：早先我把量化放在电平归一化**之前**，
    而归一化乘的是一个非 2 的幂的因子 ⇒ **把低位重新填满**，于是"24bit 容器装
    16bit 内容"的样本被分析器正确地读成"真 24bit"，阈值那边报"无区分力"。
    **那是语料造错，不是阈值没用**——普查的价值一半在于逼出这种错。
    """
    x = np.asarray(x, dtype=np.float64)
    x = x / (np.abs(x).max() + 1e-12) * (10.0 ** (level_db / 20.0))
    if clip:
        x = np.clip(x * clip, -1.0, 1.0)
    if quantize_bits is not None:
        step = 2.0 ** -(quantize_bits - 1)
        x = np.round(x / step) * step
    if mono:
        y = x[:, None]
    elif antiphase:
        y = np.stack([x, -x], axis=1)
    else:
        # 左右给一点差异 ⇒ 真立体声（width_db 落在正常范围）
        y = np.stack([x, np.roll(x, 137) * 0.9], axis=1)
    kw = {"subtype": subtype} if subtype else {}
    sf.write(path, y, fs, **kw)


def build_corpus(tmp: str) -> list[tuple[str, str]]:
    """造覆盖各阈值**两侧**的合成文件。返回 [(标签, 路径)]。"""
    out: list[tuple[str, str]] = []

    def add(label, name, *a, **kw):
        p = os.path.join(tmp, name + ".wav")
        _write(p, *a, **kw)
        out.append((label, p))

    # --- 截止类阈值（cd_full / ltd / lossy / full_48 / up_44 / hi_ok / hi_ratio）---
    # 44.1k 容器：从强有损到满带宽，逐档造
    for k in (4.0, 10.0, 14.0, 15.5, 16.5, 18.0, 19.5, 21.5, 22.05):
        add("44.1k/%.1fk" % k, "c44_%04d" % (k * 100), _bandlimited(44100, k * 1000), 44100)
    # 48k 容器
    for k in (16.5, 19.5, 21.5, 23.5, 24.0):
        add("48k/%.1fk" % k, "c48_%04d" % (k * 100), _bandlimited(48000, k * 1000), 48000)
    # 高采样率容器（含"内容止于 44.1k"的升频形态）
    for fs_, k in ((88200, 22.05), (96000, 22.05), (96000, 30.0), (96000, 45.0),
                   (192000, 24.0), (192000, 40.0), (192000, 90.0)):
        add("%dk/%.1fk" % (fs_ // 1000, k), "chi_%d_%04d" % (fs_ // 1000, k * 100),
            _bandlimited(fs_, k * 1000), fs_)

    # --- 削波类（clip_pct / clip_severe_pct / clip_advice_pct）---
    base = _bandlimited(44100, 20000)
    add("无削波", "clip_none", base, 44100, level_db=-20.0)
    add("轻微削波", "clip_mild", base, 44100, level_db=-6.0, clip=1.05)
    add("中度削波", "clip_mid", base, 44100, level_db=-6.0, clip=1.6)
    add("严重削波", "clip_hard", base, 44100, level_db=-3.0, clip=3.0)

    # --- 响度类（loud_war_lufs）---
    for lv in (-30.0, -20.0, -12.0, -6.0):
        add("电平%.0f" % lv, "lvl_%02d" % int(-lv), base, 44100, level_db=lv)

    # --- 声道类（width_narrow_db）---
    add("真立体声", "ch_stereo", base, 44100)
    add("单声道", "ch_mono", base, 44100, mono=True)
    add("反相立体声", "ch_anti", base, 44100, antiphase=True)
    # 声道宽度要**跨过 -18 dB 的两侧**。
    # ⚠ 这里有个反直觉的坑（实测踩到）：`width_db = 10·log10(side/mid)`，
    #   而 side=(L−R)/2、mid=(L+R)/2。所以让 R **变小**并不缩小 side——
    #   R=0.01L 时 side≈0.495L、mid≈0.505L ⇒ width ≈ 0 dB（**根本不是单声道**）。
    #   真正要造的是 **R 略小于 L**：R=L(1−ε) ⇒ side=εL/2、mid≈L ⇒ width≈10·log10(ε/2)。
    #   实测：ε=0.001→−64.8 / 0.032→−35.8 / 0.1→−25.6 / 0.3→−15.1 dB。
    for eps, tag in ((0.001, "极近单声道"), (0.032, "近单声道"),
                     (0.1, "偏窄立体声"), (0.3, "较宽立体声")):
        pth = os.path.join(tmp, "w_%03d.wav" % int(eps * 1000))
        yy = np.stack([base, base * (1.0 - eps)], axis=1)
        sf.write(pth, yy / (np.abs(yy).max() + 1e-12) * 10 ** (-20.0 / 20.0), 44100)
        out.append((tag, pth))

    # --- 位深类（bit_gap_warn）---
    add("16bit", "bd_16", base, 44100, subtype="PCM_16")
    add("24bit（真 24）", "bd_24", base * 1e-4, 44100, level_db=0.0, subtype="PCM_24")
    # 24bit 容器装 16bit 内容：**量化放最后**（否则低位被归一化填满，见 _write 的注释）
    add("24bit（装 16bit）", "bd_pad", base, 44100, subtype="PCM_24", quantize_bits=16)

    # --- 动态类（lra_flat / dr_low_db）---
    # ⚠ 白噪的幅度分布本身就宽，TT-DR 天生偏高 ⇒ 用**正弦**才造得出低 DR。
    # ⚠ LRA 的坑：**恒定电平**的正弦算不出 LRA（`lra=None`），必须给一点起伏。
    #   实测：±0.4 dB 的慢速调幅 → lra=0.2（触发 <3）；强弱交替 → lra=8.8（不触发）。
    t = np.arange(int(44100 * 6.0)) / 44100.0
    tone = np.sin(2 * np.pi * 1000.0 * t)
    add("恒定正弦（低 DR）", "dyn_lowdr", tone, 44100)
    add("轻微起伏（低 LRA）", "dyn_flat", tone * (1 + 0.05 * np.sin(2 * np.pi * 0.5 * t)), 44100)
    add("强弱交替（高 LRA）", "dyn_high",
        np.concatenate([tone, tone * 0.01, tone, tone * 0.01]), 44100)

    # --- 频段类（advice_low_pct / advice_upper_mid_pct）---
    # 低频为主：150 Hz 低通 ⇒ sub+bass 占比高、upper_mid 占比低
    add("低频为主", "band_low", _bandlimited(44100, 150.0), 44100)
    add("高频为主", "band_high", _bandlimited(44100, 20000.0) - _bandlimited(44100, 6000.0), 44100)

    return out


# --------------------------------------------------------------------- 字段映射
#: `Thresholds` 字段 → (观测量, 比较方向, 单位, 说明)。
#: `None` 表示"算法参数"——它不定义判定，只影响检出，所以只报检出率不报两侧分布。
FIELD_MAP: dict[str, tuple | None] = {
    "cd_full": ("cutoff_khz", ">=", "kHz", "44.1k 容器满带宽"),
    "ltd": ("cutoff_khz", ">=", "kHz", "带限/有损分界"),
    "lossy": ("cutoff_khz", ">=", "kHz", "有损/强有损分界"),
    "full_48": ("cutoff_khz", ">=", "kHz", "48k 容器真 48k"),
    "up_44": ("cutoff_khz", ">=", "kHz", "48k 容器疑似升频"),
    "hi_ok": ("cutoff_khz", "<=", "kHz", "高采样率容器伪 Hi-Res"),
    "hi_ratio": ("cutoff_ratio", ">=", "比例", "内容占奈奎斯特比例"),
    "brick_db": ("brickwall_db_per_oct", ">=", "dB/oct", "硬砖墙"),
    "brick_flat_db_per_oct": ("brickwall_db_per_oct", "<", "dB/oct", "滚降平缓"),
    "cutoff_low_khz": ("cutoff_khz", "<", "kHz", "截止点偏低"),
    "clip_pct": ("clip_pct", ">", "%", "削波告警线"),
    "clip_severe_pct": ("clip_pct", ">", "%", "削波严重线"),
    "clip_advice_pct": ("clip_pct", ">", "%", "削波建议文案线"),
    "loud_war_lufs": ("lufs", ">", "LUFS", "响度战"),
    "lra_flat": ("lra", "<", "LU", "动态压平"),
    "dr_low_db": ("dr_db", "<", "dB", "DR 偏低"),
    "width_narrow_db": ("width_db", "<", "dB", "近乎单声道"),
    "fs_44_container": ("sr", "<=", "Hz", "44.1k 档容器"),
    "fs_48_container": ("sr", "<=", "Hz", "48k 档容器"),
    "bit_gap_warn": ("bit_gap", ">=", "bit", "位深差告警"),
    "upsample_fs": ("sr", ">", "Hz", "高采样率容器起点"),
    "upsample_content_khz": ("cutoff_khz", "<=", "kHz", "内容止于 44.1k 系"),
    "advice_hf_ok_khz": ("cutoff_khz", ">=", "kHz", "建议：高频充足"),
    "advice_hf_limited_khz": ("cutoff_khz", ">=", "kHz", "建议：高频受限"),
    "advice_low_pct": ("low_pct", ">", "%", "建议：低频占比高"),
    "advice_upper_mid_pct": ("upper_mid_pct", ">", "%", "建议：中高频偏高"),
    "conf_low": ("conf", "<", "比例", "证据强度低"),
    "conf_slope_lo": ("brickwall_db_per_oct", "<=", "dB/oct", "自然滚降端点"),
    # C4 成因候选门限（**全部工程估计**，只作候选证据；见 verdict.causes）
    "vinyl_rumble_pct": ("low_pct", ">=", "%", "黑胶候选：低频隆隆"),
    "broadcast_low_cut_pct": ("low_pct", "<=", "%", "广播候选：低频缺失"),
    "noise_like_flatness": ("flatness", ">=", "比例", "类噪守卫：占比证据的前提"),
    "fake_stereo_corr": ("corr", ">=", "比例", "假立体声候选：声道相关"),
    "fake_stereo_width_db": ("width_db", "<=", "dB", "假立体声候选：侧向能量"),
    # 局部反相分档（2026-10-16 接入判定：verdict.axis_channels 对明显/严重告警）
    "anti_scope_minor": ("anti_share", ">=", "比例", "反相分档：轻微起"),
    "anti_scope_major": ("anti_share", ">=", "比例", "反相分档：明显起（声道轴告警线）"),
    "anti_scope_severe": ("anti_share", ">=", "比例", "反相分档：严重"),
    # 算法参数：不定义判定，只影响"能不能检出"
    "min_drop_db": None, "min_stop_db": None, "cutoff_min_hz": None, "cutoff_need": None,
    "conf_slope_span": None,
    "seg_seconds": None, "seg_overlap": None, "seg_min_count": None,
    # 视图渲染选项（C7）：只影响频谱图画质，不影响任何判定
    "dual_resolution_stft": None,
    # 去嗡自动检测（2026-10-23）：**不在普查管线的结果字段里**——它由
    # `analyzers/hum.detect_hum` 直接从谱图量（excess / 帧间稳定 / 安静帧 excess），
    # 全量分析管线不算这三项。两侧区分力已实测：`packaging/hum_detect_probe.py`
    # （嗡 / 干净白噪 / 三种贝斯）与 `tests/test_analyzer_hum.py` 的 8 条判据。
    # ⇒ 标 None **不代表没检验**，代表"普查这把尺子量不到它"。
    "hum_min_excess_db": None, "hum_max_stability_db": None, "hum_min_quiet_excess_db": None,
}

#: 视图渲染选项（与"算法参数"并列但性质不同）：算法参数决定**能不能检出**，
#: 视图选项连检出率都不影响——标签不能混用（"只报检出率"对它不成立）。
VIEW_OPTIONS = {"dual_resolution_stft"}


def _observe(r: dict) -> dict:
    """从分析结果里取各观测量。取不到的留 None（**不许拿 0 冒充**）。"""
    fs = r.get("sr") or 0
    cut = r.get("cutoff_hz")
    b = r.get("bands_pct") or {}
    claimed = str(r.get("bits") or "").replace("PCM_", "")
    try:
        gap = int(claimed) - int(r["bit_effective"]) if r.get("bit_effective") is not None else None
    except (TypeError, ValueError):
        gap = None
    bw = r.get("brickwall_db_per_oct")
    conf = None
    if bw is not None:
        lo, span = 8.0, 16.0          # 与 verdict 同式（这里只做普查，取默认端点）
        conf = max(0.0, min(1.0, (bw - lo) / span))
    return {
        "cutoff_khz": (cut / 1000.0) if cut else None,
        "cutoff_ratio": (cut / (fs / 2.0)) if (cut and fs) else None,
        "brickwall_db_per_oct": bw,
        "clip_pct": r.get("clip_pct"),
        "lufs": r.get("lufs"),
        "lra": r.get("lra"),
        "dr_db": r.get("dr_db"),
        "width_db": r.get("width_db"),
        "sr": fs or None,
        "bit_gap": gap,
        "low_pct": (b.get("sub", 0) + b.get("bass", 0)) if b else None,
        "upper_mid_pct": b.get("upper_mid") if b else None,
        "conf": conf,
        # C4 成因候选的观测量（黑胶隆隆 / 广播低切 / 类噪守卫 / 假立体声）
        "flatness": r.get("flatness"),
        "corr": r.get("corr"),
        # 局部反相分档门限（`anti_scope_*`）：dsp 报的是百分比，门限是比例
        "anti_share": (r.get("anti_energy_pct") / 100.0)
        if r.get("anti_energy_pct") is not None else None,
    }


def _split(key: str, op: str, val: float, rows: list[dict]) -> tuple[int, int, int]:
    """把样本按 `op val` 分成两侧，返回 (触发侧, 另一侧, 有效样本数)。"""
    hi = lo = 0
    for r in rows:
        v = r["_obs"].get(key)
        if v is None:
            continue
        hit = (v >= val) if op == ">=" else (v > val) if op == ">" else \
              (v <= val) if op == "<=" else (v < val)
        if hit:
            hi += 1
        else:
            lo += 1
    return hi, lo, hi + lo


#: **负面对照**（E 块"反面参照"惯例，同 colormap 的 jet、变异的等价体）：
#: 故意造**不可能有区分力**的字段规格。它测的不是阈值，而是**这把尺子本身**——
#: 若连这种一眼单侧/全缺的输入都报不出"无区分力"，那"全部字段有区分力"
#: 就是空话（检查恒真 = 没有信息量）。
#: ⚠ 本检查此前**从未命中过**（零命中）：因为语料是按"跨每个阈值两侧"手工造的，
#: 结论由构造保证 ⇒ 无失败模式。负面对照补上这个失败模式。
NEGATIVE_CONTROLS: list[tuple[str, tuple]] = [
    ("__neg_impossible__",
     ("cutoff_khz", ">=", 999.0, "kHz", "负面对照：不可能达到的截止点（应报无区分力）")),
    ("__neg_all_missing__",
     ("__no_such_observable__", ">=", 0.0, "", "负面对照：观测量全缺（应报无样本）")),
]


def main() -> int:
    ap = argparse.ArgumentParser(description="阈值区分力普查")
    ap.add_argument("--strict", action="store_true", help="有无区分力字段则返回 1")
    ap.add_argument("--json", help="把逐字段结果写成 JSON")
    a = ap.parse_args()

    th = Thresholds()
    with tempfile.TemporaryDirectory(prefix="audiocheck_survey_") as tmp:
        corpus = build_corpus(tmp)
        print("合成语料 %d 个文件（临时目录，不入库）" % len(corpus))
        rows = []
        for label, path in corpus:
            try:
                r = engine.analyze(path, deep=True)
            except Exception as e:                       # noqa: BLE001
                print("  !! %s 分析失败：%s" % (label, e))
                continue
            r["_label"] = label
            r["_obs"] = _observe(r)
            rows.append(r)

    print("成功分析 %d / %d\n" % (len(rows), len(corpus)))

    # ---- 逐字段：越阈比例 / 双向分布 ----
    blind = []
    report = {}
    print("=" * 100)
    print("%-24s %8s %10s %10s %8s   %s" % ("字段", "阈值", "触发侧", "另一侧", "有效n", "判定"))
    print("-" * 100)
    for fname, spec in FIELD_MAP.items():
        val = getattr(th, fname)
        if spec is None:
            note = ("视图渲染选项（不影响判定与检出率）" if fname in VIEW_OPTIONS
                    else "算法参数（不定义判定，只报检出率）")
            print("%-24s %8s %10s %10s %8s   %s"
                  % (fname, val, "—", "—", "—", note))
            continue
        key, op, unit, desc = spec
        hi, lo, n = _split(key, op, val, rows)
        if n == 0:
            verdict = "**无样本**（观测量全缺）"
        elif hi == 0 or lo == 0:
            verdict = "**无区分力**（全落在一侧）"
            blind.append((fname, desc, hi, lo, val, unit, op))
        else:
            verdict = "有区分力"
        report[fname] = {"threshold": val, "unit": unit, "op": op, "desc": desc,
                         "triggered": hi, "other": lo, "n": n, "verdict": verdict}
        print("%-24s %8s %10d %10d %8d   %s"
              % (fname, val, hi, lo, n, verdict))
    print("=" * 100)

    # ---- 负面对照：证明这把尺子**有牙齿**（能报出单侧/无样本）----
    neg_fail: list[str] = []
    print("\n负面对照（检验的检验）：")
    for name, spec in NEGATIVE_CONTROLS:
        key, op, val, unit, desc = spec
        hi, lo, n = _split(key, op, val, rows)
        flagged = (n == 0) or (hi == 0 or lo == 0)
        if not flagged:
            neg_fail.append(name)
        print("  %-20s 触发 %d / 另一侧 %d / 有效 %d ⇒ %s  %s"
              % (name, hi, lo, n,
                 "**未报出（尺子坏了）**" if not flagged else "正确报出（单侧 / 无样本）",
                 "← **失败**" if not flagged else ""))
        report[name] = {"threshold": val, "unit": unit, "op": op, "desc": desc,
                        "triggered": hi, "other": lo, "n": n,
                        "verdict": "无样本" if n == 0 else ("无区分力" if flagged else "有区分力"),
                        "negative_control": True}
    if neg_fail:
        print("\n**负面对照未通过**：本检查连明显单侧都报不出来 ⇒ "
              "'全部字段有区分力'的结论**不可信**（尺子坏了）。")
        return 1
    print("  两例均被正确报出 ⇒ 本检查**有牙齿**（不是恒真）。")

    # ---- 检出率（算法参数 + 总体）----
    det = sum(1 for r in rows if r.get("cutoff_hz"))
    print("\n截止点检出率：%d / %d = %.1f%%（`min_drop_db`/`min_stop_db`/`cutoff_need` "
          "是算法参数，决定的就是这个）" % (det, len(rows), 100.0 * det / max(1, len(rows))))

    # ---- 汇总 ----
    if blind:
        print("\n**无区分力的字段（%d 个）**——这些阈值在本语料上判不出差别：" % len(blind))
        for fname, desc, hi, lo, val, unit, _op in blind:
            print("  * %-24s %s（阈值 %s%s，触发 %d / 另一侧 %d）"
                  % (fname, desc, val, unit, hi, lo))
        print("\n处置建议（**不自动改阈值**）：")
        print("  ① 语料没覆盖到该阈值的另一侧 ⇒ **补语料**（这是最常见的原因，先查这个）；")
        print("  ② 语料覆盖了但仍单侧 ⇒ 阈值对现实音源没有区分力，记账并考虑降级为'工程估计'。")
    else:
        # ⚠ **措辞纪律**："两侧都有样本"只说明**语料覆盖了两侧**，不等于
        # "该阈值在真实音源上有区分力"——语料是手工按跨阈值两侧造的，
        # 这条结论由构造保证。真实音源上的区分力**仍需外部曲库验证**。
        print("\n全部字段**两侧都有样本** ⇒ 语料覆盖了两侧；"
              "负面对照已证明本检查能报出单侧（有牙齿）。")
        print("  ⚠ 但这**不等于**『阈值对真实音源有区分力』——语料是手工按跨阈值"
              "两侧造的，本结论由构造保证；真实曲库上的区分力**未验证**。")

    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump({"corpus_size": len(corpus), "analyzed": len(rows),
                       "fields": report}, f, ensure_ascii=False, indent=1)
        print("\n逐字段结果已写入 %s" % a.json)

    return 1 if (blind and a.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
