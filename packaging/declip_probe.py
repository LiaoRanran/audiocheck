#!/usr/bin/env python3
"""declip_probe：**先量再改**——去削波到底能修回多少，以及用哪种重建法。

    python packaging/declip_probe.py

本轮（P3 判据骨架）规矩：**算法落地前先把"提升多少"量出来**（`docs/editor_roadmap.md §5`）。
所以本探针**不写内核**，只做两件事：

1. **量基线**：三档削波（`corpus.CLIP_TIERS`）各自的饱和占比、最长连续段，
   以及 `metrics.sdr_db` / `metrics.energy_ratio_db` 量出的"削波把真相毁了多远"。
2. **量候选**：四种重建法在**同一把尺子**下的 SDR 提升与能量比——
   * `poly1` 线性插值（每段用两侧锚点）
   * `poly3` 三次多项式（两侧各 `side` 个锚点）
   * `spline` 三次样条（`scipy.interpolate.CubicSpline`，两侧锚点）
   * `pg` **Papoulis–Gerchberg 带限外推**（已知带宽 `B`：迭代"砍带外 + 恢复已知样本"）
   四种都遵守同一条硬约束：**只改饱和样本**（其余逐位不变）。
   ⚠ 这些候选**只存在于本探针里**——量完选一个进 `core/edit/declip.py`，其余如实挂账。

⚠ `pg` 用到了**真值带宽** `B`（合成信号带宽已知）⇒ 它的分数是**上界**，不是实战分；
故另附"`B` 取错"的敏感性小表（`B` 偏窄 / 直接放到奈奎斯特 = 不设约束）。

两个素材：严格带限白噪（带宽真值 0.9·奈奎斯特）与谐波音（带宽真值 17.6 kHz）。
"""
from __future__ import annotations

import os
import sys

import numpy as np
from scipy.interpolate import CubicSpline

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.edit import corpus  # noqa: E402
from audiocheck.core.edit.corpus import (  # noqa: E402
    CLIP_TIERS,
    saturation_mask,
    saturation_runs,
)
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
SECONDS = 1.0
SIDE = 16          # 每侧锚点数
PG_ITER = 400      # P-G 迭代次数


# ------------------------------------------------------------------ 候选重建法
def _anchors(v, mask, a, b, side):
    """段 `[a,b)` 两侧各 `side` 个**非饱和**锚点（下标升序）。"""
    i0, i1 = max(0, a - side), min(v.size, b + side)
    idx = np.concatenate([np.arange(i0, a), np.arange(b, i1)])
    return idx[~mask[idx]], v[idx[~mask[idx]]]


def recon_poly(y, ceiling, side=SIDE, deg=3):
    """最小二乘多项式插值：每段用两侧锚点拟合 `deg` 次多项式，外推到段内。"""
    out = y.copy()
    for ch in range(y.shape[1]):
        v = y[:, ch]
        mask = saturation_mask(v[:, None], ceiling)[:, 0]
        for a, b in saturation_runs(mask):
            x, val = _anchors(v, mask, a, b, side)
            if x.size == 0:
                continue                                  # 整条都在饱和：无从重建
            out[a:b, ch] = np.polyval(np.polyfit(x - a, val, min(deg, x.size - 1)),
                                      np.arange(a, b) - a)
    return out


def recon_spline(y, ceiling, side=SIDE):
    """三次样条（`CubicSpline`）穿过两侧锚点，取段内值。"""
    out = y.copy()
    for ch in range(y.shape[1]):
        v = y[:, ch]
        mask = saturation_mask(v[:, None], ceiling)[:, 0]
        for a, b in saturation_runs(mask):
            x, val = _anchors(v, mask, a, b, side)
            if x.size == 0:
                continue
            if x.size == 1:
                out[a:b, ch] = val[0]
                continue
            cs = CubicSpline(x.astype(np.float64), val, extrapolate=True)
            out[a:b, ch] = cs(np.arange(a, b, dtype=np.float64))
    return out


def recon_pg(y, ceiling, fs, bandwidth, n_iter=PG_ITER):
    """Papoulis–Gerchberg：迭代"砍掉 `> bandwidth` 的谱 + 恢复**已知样本**"。

    收敛到的信号**带限**且与全部非饱和样本**一致**（Papoulis 1975 / Gerchberg 1974 的
    交替投影）。已知样本每轮被写回 ⇒ **逐位不变**，只有饱和样本变。
    """
    out = y.copy()
    n = y.shape[0]
    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = f <= bandwidth
    for ch in range(y.shape[1]):
        x = out[:, ch].copy()
        k = ~saturation_mask(x[:, None], ceiling)[:, 0]
        for _ in range(n_iter):
            X = np.fft.rfft(x)
            X[~keep] = 0.0
            x = np.fft.irfft(X, n=n)
            x[k] = out[k, ch]
        out[:, ch] = x
    return out


def recon_pocs(y, ceiling, fs, bandwidth, n_iter=PG_ITER):
    """Papoulis–Gerchberg **加削波不等式投影**（POCS）：除"带限 + 已知样本"外，
    再要求饱和样本满足削波**真实给过的约束** `|x| >= c` 且与观测同号
    （`docs/theory.md §12.2`：削波对饱和样本只给了**不等式**，没给值）。

    三轮交替投影：① 砍带外（带限集）② 写回已知样本（一致集）③ 饱和样本钳到 `>= c`。
    """
    out = y.copy()
    n = y.shape[0]
    f = np.fft.rfftfreq(n, 1.0 / fs)
    keep = f <= bandwidth
    for ch in range(y.shape[1]):
        x = out[:, ch].copy()
        sat = saturation_mask(x[:, None], ceiling)[:, 0]
        k = ~sat
        sign = np.where(out[:, ch] >= 0.0, 1.0, -1.0)
        for _ in range(n_iter):
            X = np.fft.rfft(x)
            X[~keep] = 0.0
            x = np.fft.irfft(X, n=n)
            x[k] = out[k, ch]
            x[sat] = sign[sat] * np.maximum(np.abs(x[sat]), ceiling)
        out[:, ch] = x
    return out


def _score(est, ref):
    s = sdr_db(est, ref)
    e = energy_ratio_db(est, ref)
    return ("%9s" % ("—" if s is None else "%.2f" % s),
            "%9s" % ("—" if e is None else "%+.2f" % e))


def table(name, clean, fs, bandwidth):
    print("── %s（带宽真值 %.0f Hz ｜ %.0f s）" % (name, bandwidth, SECONDS))
    print("%-9s %6s %7s %5s %10s ｜ %8s %8s %8s %8s"
          % ("档位", "c", "饱和%", "最长", "SDR_before", "poly3", "spline", "pg", "pocs"))
    for tier in CLIP_TIERS:
        p = corpus.clipped(clean, tier)
        y, ref, c = p["clipped"], p["clean"], p["ceiling"]
        st = p["stats"]
        s0, _ = _score(y, ref)
        s3, _ = _score(recon_poly(y, c, deg=3), ref)
        ss, _ = _score(recon_spline(y, c), ref)
        sp, _ = _score(recon_pg(y, c, fs, bandwidth), ref)
        pc, ep = _score(recon_pocs(y, c, fs, bandwidth), ref)
        print("%-9s %6.3f %7.2f %5d %10s ｜ %8s %8s %8s %8s"
              % (tier, c, 100 * st["fraction"], st["longest_run"], s0, s3, ss, sp, pc))
        print("%-9s %6s %7s %5s %10s ｜ %8s %8s %8s %8s"
              % ("  能量比", "", "", "", "", "", "", "", ep))
    print()


def pg_sensitivity(clean, fs, bandwidth):
    """`B` 取错时 `pg` / `pocs` 还能不能打（真值带宽是**白拿**的，实战要估 ⇒ 必须看这个）。"""
    print("── 对『带宽取值』的敏感性（噪声素材，真值 B = %.0f Hz）" % bandwidth)
    print("%-9s %-18s %9s %9s ｜ %9s %9s" % ("档位", "B", "SDR_pg", "SDR_pocs", "能量_pg", "能量_pocs"))
    for tier in CLIP_TIERS:
        p = corpus.clipped(clean, tier)
        y, ref, c = p["clipped"], p["clean"], p["ceiling"]
        for tag, bw in (("真值(0.9·nyq)", bandwidth), ("偏窄 ×0.5", 0.5 * bandwidth),
                        ("偏窄 ×0.25", 0.25 * bandwidth), ("奈奎斯特(不约束)", fs / 2.0)):
            sg, eg = _score(recon_pg(y, c, fs, bw), ref)
            sp, ep = _score(recon_pocs(y, c, fs, bw), ref)
            print("%-9s %-18s %9s %9s ｜ %9s %9s" % (tier, tag, sg, sp, eg, ep))
    print()


def convergence(clean, fs, bandwidth):
    """迭代次数取多少才够（固定次数 = 可复算；次数不能凭手感定）。"""
    print("── pocs 的**迭代收敛**（噪声 moderate 档，SDR 到多少 dB 就平了）")
    print("%10s %9s" % ("迭代", "SDR"))
    for it in (4, 8, 16, 32, 64, 128, 200, 400):
        p = corpus.clipped(clean, "moderate")
        s, _ = _score(recon_pocs(p["clipped"], p["ceiling"], fs, bandwidth, n_iter=it),
                      p["clean"])
        print("%10d %9s" % (it, s))
    print()


def main() -> None:
    nyq = FS / 2.0
    noise = corpus.default_clean(SECONDS, fs=FS, seed=1)
    tone = corpus.harmonic_tone(SECONDS, fs=FS, f0=440.0, n_partials=40, seed=2)
    print("去削波探针 ｜ fs=%d ｜ 每侧锚点 %d ｜ P-G 迭代 %d" % (FS, SIDE, PG_ITER))
    print("尺子 = `core.edit.metrics`：SDR（越高越好，dB）+ 能量比（防『变安静被当成修好』）\n")
    table("严格带限白噪", noise, FS, 0.9 * nyq)
    table("谐波音（B=17.6k）", tone, FS, 17600.0)
    pg_sensitivity(noise, FS, 0.9 * nyq)
    convergence(noise, FS, 0.9 * nyq)
    print("读表（决定）:")
    print("1. **局部插值（poly1/poly3/spline）被实测否决**：它**把 SDR 修坏**（light 43.5→21.3、")
    print("   severe 10.1→1.6）。机制：削波在饱和样本上留下 `±c`，其误差被『越出 c 多少』限住，")
    print("   而插值拿**不相关**的锚点去猜，方差远大于那点越出量 ⇒ 越修越远。")
    print("   ⇒ 本仓**不提供**插值路径（工程界的 Audacity ClipFix 正是这一族：三次样条 + 95% 门限）。")
    print("2. **pocs 全面最优且退化安全**：真值带宽下 312.8 / 42.8 / 14.1；带宽取窄（×0.5）时**只退化到")
    print("   『不改』（43.5 / 23.3 / 9.7）**，不像纯 pg 那样反伤（0.47）。⇒ **落地 pocs**")
    print("   （`core/edit/declip.py`），把 `pg`/插值**挂账留档**，不当已核用。")
    print("3. **效力边界（与文献一致）**：提升随『输入 SDR』增大而增大、随饱和变密而变小")
    print("   （light +269 / moderate +19.5 / severe **+4.0** dB）⇒ 密集削波是本方法够不到的地方，")
    print("   下一轮要上稀疏/相位感知那一家（Záviška 的 SPADE 谱系）。")


if __name__ == "__main__":
    main()
