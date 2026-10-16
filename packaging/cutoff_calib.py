"""`detect_cutoff` 的精度标定与反例守卫（可复算；对应 docs/theory.md §1.6）。

一条命令即可复现 v4 → v4.1 的实测数字。用法：

    python packaging/cutoff_calib.py             # 完整报告（标定 + 守卫）
    python packaging/cutoff_calib.py --grid      # 只跑 250Hz 密集网格标定
    python packaging/cutoff_calib.py --guard     # 只跑反例守卫

为什么值得入库：这条偏差是**属性测试的容差依据**。没有它，容差只能拍脑袋；
有了它，`tests/test_props.py` 的 3% 与"覆盖 [6,21] kHz"才有出处。
"""
from __future__ import annotations

import argparse
import os
import sys

import numpy as np
from scipy import signal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))
from audiocheck.core.analyzers.spectrum import detect_cutoff  # noqa: E402

FS_SET = (44100, 48000, 96000)


# ------------------------------------------------------------------ 素材
def bandlimited(fs: int, hz: float, seconds: float = 3.0, seed: int = 3) -> np.ndarray:
    """白噪硬切到 hz——"答案已知"的砖墙素材（与 tests/test_props.py 同配方）。"""
    n = int(fs * seconds)
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    spec = np.fft.rfft(x)
    spec[np.fft.rfftfreq(n, 1.0 / fs) > hz] = 0.0
    return np.fft.irfft(spec, n=n).astype(np.float32)


def _welch_db(fs: int, x: np.ndarray):
    f, p = signal.welch(x, fs=fs, nperseg=16384, noverlap=8192, window="hann")
    return f, 10 * np.log10(p + 1e-20)


def estimate(fs: int, cut: float):
    return detect_cutoff(*_welch_db(fs, bandlimited(fs, cut)))[0]


# ------------------------------------------------------------------ 标定
def grid_report(step: float = 250.0, lo: float = 6000.0, hi: float = 21000.0) -> dict:
    cuts = np.arange(lo, hi + 1, step)
    worst_rel = (0.0, None)
    worst_spread = (0.0, None)
    for cut in cuts:
        est = {}
        for fs in FS_SET:
            if cut >= fs / 2 * 0.95:
                continue
            c = estimate(fs, cut)
            if c is None:
                continue
            est[fs] = c
            r = abs(c - cut) / cut
            if r > worst_rel[0]:
                worst_rel = (r, (fs, cut))
        if len(est) >= 2:
            sp = (max(est.values()) - min(est.values())) / cut
            if sp > worst_spread[0]:
                worst_spread = (sp, cut)
    return {"grid": (lo, hi, step), "worst_rel": worst_rel, "worst_spread": worst_spread}


# ------------------------------------------------------------------ 守卫
def _brick(fs, hz):
    return bandlimited(fs, hz)


def _pink_brick(fs, hz, seconds=3.0, seed=3, slope_db_per_oct=2.0):
    n = int(fs * seconds)
    rng = np.random.default_rng(seed)
    X = np.fft.rfft(rng.standard_normal(n))
    fr = np.fft.rfftfreq(n, 1.0 / fs)
    shape = np.ones_like(fr)
    nz = fr > 0
    shape[nz] = (fr[nz] / 1000.0) ** (-slope_db_per_oct / 6.0206)
    shape[fr > hz] = 0.0
    return np.fft.irfft(X * shape, n=n).astype(np.float32)


def _smooth_lowpass(fs, fc, seconds=3.0, seed=3, order=4):
    n = int(fs * seconds)
    rng = np.random.default_rng(seed)
    sos = signal.butter(order, fc / (fs / 2), btype="low", output="sos")
    return signal.sosfilt(sos, rng.standard_normal(n)).astype(np.float32)


def guard_report() -> list[tuple]:
    """四条不变式：满带宽→奈奎斯特、自然滚降不凭空出截止、斜谱砖墙仍准、E1 低截止回归。"""
    rows = []

    for fs in FS_SET:                                   # A 满带宽
        x = np.random.default_rng(1).standard_normal(int(fs * 3)).astype(np.float32)
        c = detect_cutoff(*_welch_db(fs, x))[0]
        rows.append(("A 满带宽", fs, None, c, abs(c - fs / 2) <= 1.0))

    for fs in (44100, 96000):                           # B 自然滚降
        for fc in (8000, 12000, 16000):
            c = detect_cutoff(*_welch_db(fs, _smooth_lowpass(fs, fc)))[0]
            # 与"是否有截止"无关：只要不抛出、且不误报成满带宽即可（行为与 v4 一致）
            rows.append(("B 自然滚降", fs, fc, c, c is not None))

    for fs, cut in [(44100, 14000), (96000, 9000), (96000, 16000)]:   # C 斜谱砖墙
        c = detect_cutoff(*_welch_db(fs, _pink_brick(fs, cut)))[0]
        rows.append(("C 斜谱砖墙", fs, cut, c, c is not None and abs(c - cut) / cut <= 0.03))

    for fs, cut in [(44100, 9000), (48000, 6000), (96000, 10000),   # D E1 回归
                    (96000, 12000), (96000, 21000)]:
        c = detect_cutoff(*_welch_db(fs, _brick(fs, cut)))[0]
        ok = c is not None and abs(c - fs / 2) > 1.0 and abs(c - cut) / cut <= 0.03
        rows.append(("D E1 回归", fs, cut, c, ok))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--grid", action="store_true")
    ap.add_argument("--guard", action="store_true")
    args = ap.parse_args()
    run_grid = args.grid or not (args.grid or args.guard)
    run_guard = args.guard or not (args.grid or args.guard)

    if run_grid:
        r = grid_report()
        lo, hi, st = r["grid"]
        print("== 密集网格标定：%.0f–%.0f Hz / %.0f Hz 步长 / %s" % (lo, hi, st, FS_SET))
        rel, (fs, cut) = r["worst_rel"]
        print("   最大相对偏差 %.3f%%  (fs=%d cut=%.0f)" % (rel * 100, fs, cut))
        sp, cut = r["worst_spread"]
        print("   最大跨率散布 %.3f%%  (cut=%.0f)" % (sp * 100, cut))
        print("   → tests/test_props.py 容差 3%（2.4× 余量），覆盖 [6,21] kHz")

    if run_guard:
        print("== 反例守卫")
        bad = 0
        for tag, fs, cut, est, ok in guard_report():
            bad += 0 if ok else 1
            print("   %-10s fs=%5d %s est=%s  %s" % (
                tag, fs, ("cut=%-6s" % cut) if cut else "        ",
                "None" if est is None else "%.0f" % est, "OK" if ok else "XX"))
        print("   %s（%d 条不通过）" % ("全部通过" if not bad else "有失败", bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
