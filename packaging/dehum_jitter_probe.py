#!/usr/bin/env python3
"""dehum_jitter_probe：**非稳态嗡**（频率抖动/漂移）到底伤多少？Q=10 还撑得住吗？（先量，再定）

## 问题（`docs/editor_roadmap.md` 第八步的挂账）

"非稳态嗡（漂移/抖动）"一直挂着：第 2 轮实测过 ±1 Hz **静态**漂移（Q=10 仍压 ~17 dB），
也实测过"漂移分散陷波"三连（更差，不采用）。缺的一块是**时间性抖动**——磁带机抖晃、
发电机频率波动造成的**瞬时**频率偏离。电网稳态容差是 ±0.5%（50 Hz ⇒ ±0.25 Hz，
IEC 60038）；抖晃可到 ±1 Hz 量级。

## 量什么

FM 抖动嗡 `f_i(t) = f0·k + dev·sin(2π·fj·t)`（调频，瞬时频率偏离 ±dev）× 三种幅度
（0.1 = 电网正常 / 0.25 = IEC 容差边界 / 1.0 = 磁带抖晃）× 两档速率（0.5 / 3 Hz），
外加一个**慢漂移斜坡**（0→+1 Hz over 2 s）。对照三个 Q：**10（现行）** / 5 / 3——
看"加宽陷波"能不能救抖动、在零抖动时又付出什么。

    python packaging/dehum_jitter_probe.py
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, dehum  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
SECONDS = 2.0
HARMONICS = (1, 2, 3)
REL_DB = -20.0


def hum_fm(clean: np.ndarray, f0: float, dev: float, fj: float,
           rel_db: float = REL_DB, harmonics=HARMONICS, fs: int = FS) -> np.ndarray:
    """FM 抖动嗡：瞬时频率 `f0·k + dev·sin(2π·fj·t)`（相位积分解析式）。"""
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(20261022)
    h = np.zeros(n)
    for k in harmonics:
        phase = 2 * np.pi * f0 * k * t - (dev * k / fj) * np.cos(2 * np.pi * fj * t)
        h += np.sin(phase + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def hum_drift(clean: np.ndarray, f0: float, total: float,
              rel_db: float = REL_DB, harmonics=HARMONICS, fs: int = FS) -> np.ndarray:
    """慢漂移斜坡：瞬时频率从 `f0` 线性漂到 `f0 + total`（电网慢漂/磁带拉伸）。"""
    n = clean.shape[0]
    t = np.arange(n) / fs
    rng = np.random.default_rng(20261022)
    h = np.zeros(n)
    span = t[-1] if t.size else 1.0
    for k in harmonics:
        inst = f0 * k + total * k * (t / span)            # 线性斜坡
        phase = 2 * np.pi * np.cumsum(inst) / fs
        h += np.sin(phase + rng.uniform(0, 2 * np.pi)) / k
    h *= (10 ** (rel_db / 20.0)) * float(np.abs(clean).max()) / float(np.abs(h).max())
    return clean + h[:, None]


def run(label: str, noisy: np.ndarray, clean: np.ndarray, f0: float, q: float) -> dict:
    out, st = dehum.dehum(noisy, f0, FS, q, HARMONICS)
    return {"label": label, "q": q, "dsdr": sdr_db(out, clean) - sdr_db(noisy, clean),
            "er": energy_ratio_db(out, clean),
            "sup": st["suppression_db"], "changed": st["changed_samples"]}


def show(r: dict) -> str:
    sup = "/".join("—" if v is None else "%.1f" % v for v in r["sup"])
    return ("Q=%-4g ΔSDR %+7.2f  能量 %+6.2f  压制[%s] dB  改 %d"
            % (r["q"], r["dsdr"], r["er"], sup, r["changed"]))


def main() -> int:
    clean = corpus.harmonic_tone(SECONDS, FS, amp=0.5, seed=3)
    print("非稳态嗡对照（谐波音 %g s，嗡相对峰值 %g dB，谐波 1/2/3）\n" % (SECONDS, REL_DB))
    print("== 0) 零抖动基线（Q 加宽的代价在这里量）==")
    for q in (10.0, 5.0, 3.0):
        print("  %s ｜ %s" % ("静止 50.000", show(run("static", hum_fm(clean, 50.0, 0, 1),
                                                      clean, 50.0, q))))
    for dev, fj in ((0.1, 0.5), (0.25, 0.5), (0.25, 3.0), (1.0, 0.5), (1.0, 3.0)):
        print("== FM 抖动 ±%g Hz @ %g Hz ==" % (dev, fj))
        for q in (10.0, 5.0, 3.0):
            noisy = hum_fm(clean, 50.0, dev, fj)
            print("  %s ｜ %s" % ("FM", show(run("fm", noisy, clean, 50.0, q))))
    print("== 慢漂移 0→+1 Hz（2 s 斜坡）==")
    for q in (10.0, 5.0, 3.0):
        noisy = hum_drift(clean, 50.0, 1.0)
        print("  %s ｜ %s" % ("DRIFT", show(run("drift", noisy, clean, 50.0, q))))
    print("""
读法：
* Q=10 在静止时压制最好；抖动下看压制掉了多少 dB；
* Q=5/3 宽陷波若在抖动下反超、且静止时代价可接受（干净素材 SDR 高）⇒ 才有"换 Q"的话题；
* ΔSDR 对比的是"不去嗡"的基线：正 = 净赚。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
