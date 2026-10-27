#!/usr/bin/env python3
"""denoise_nonstat_probe：**非稳态噪声**（噪声床台阶变化）对现行去噪伤害多少？（先量，再定）

## 问题（P3 去噪的最后一条挂账）

现行 `DenoiseOp` 从**一段纯噪声**学一个平均谱 `N`，整段选区用同一份 `N`——
前提是噪声**稳态**。真实素材的噪声床会变（设备增益变化、环境噪声进出、
磁带段与段之间底噪不同）。本探针构造**台阶噪声**（低→高→低，各 2 s），
量两个方向的实际伤害：

* **噪声段取低床段**（用户顺手拿的开头）⇒ 高床段**欠压制**（噪声留下）；
* **噪声段取高床段** ⇒ 低床段**过压制**（把内容当噪声减掉——最危险的方向）。

对照：稳态噪声（全段同一床）上的现行表现（`denoise_probe.py` 已量过：ΔSDR +11.7…+14）。

    python packaging/denoise_nonstat_probe.py
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, denoise  # noqa: E402
from audiocheck.core.edit.metrics import sdr_db  # noqa: E402

FS = 48000
SEC = 2.0                                   # 每段 2 s，共 3 段
SNR_LOW = 10.0                              # 低床：+10 dB SNR（与 denoise_probe 的"安静"档同口径）
SNR_HIGH = 0.0                              # 高床：0 dB SNR（denoise_probe 实测 +12.7 的档）


def _white(n: int, rms: float, seed: int) -> np.ndarray:
    """与 `corpus.add_noise` 同口径的白色噪声（声道独立、可复算）。"""
    rng = np.random.default_rng(seed)
    return rng.standard_normal((n, 1)) * rms


def build() -> dict:
    """文件结构（隔离"非稳态"这一个变量——噪声段**必须是纯噪声**，见 `DenoiseOp` 契约）：

        [0–0.5s 纯噪声·低床][0.5–1s 纯噪声·高床][1–3s 内容+低][3–5s 内容+高][5–7s 内容+低]

    选区 = 1–7 s（全部含内容段）；噪声段按配置取前缀的两段纯噪声之一。
    """
    clean = corpus.harmonic_tone(6 * SEC, FS, amp=0.5, seed=3)   # 6 s 内容
    n = clean.shape[0]
    pad = int(1.0 * FS)                                          # 前 1 s 纯噪声前缀

    def rms_for(snr_db: float) -> float:
        p = float(np.sum(clean ** 2))
        return float(np.sqrt(p / (n * 1) / (10.0 ** (snr_db / 10.0))))

    r_lo, r_hi = rms_for(SNR_LOW), rms_for(SNR_HIGH)
    prefix = np.concatenate([_white(pad // 2, r_lo, 10), _white(pad - pad // 2, r_hi, 12)])
    body = np.concatenate([
        _white(n // 3, r_lo, 11),
        _white(n // 3, r_hi, 12),
        _white(n - 2 * (n // 3), r_lo, 13),
    ])
    noise = np.concatenate([prefix, body])
    clean_full = np.concatenate([np.zeros((pad, 1)), clean])
    noisy = clean_full + noise
    # 段（秒，文件坐标）：内容三段 + 两个纯噪声前缀段
    return {"clean": clean_full, "noisy": noisy, "noise": noise,
            "sel": (1.0, 7.0),
            "sections": [("内容·低床+10dB", 1.0, 3.0), ("内容·高床0dB", 3.0, 5.0),
                         ("内容·低床+10dB", 5.0, 7.0)],
            "pure": {"低床": (0.0, 0.5), "高床": (0.5, 1.0)}}


def measure(pack, noise_t0: float, noise_t1: float) -> dict:
    """现行内核：单一纯噪声段学谱，选区 1–7 s 去噪；报整体与**逐内容段** ΔSDR。"""
    clean, y = pack["clean"], pack["noisy"]
    s0, s1 = (int(t * FS) for t in pack["sel"])
    out, st = denoise.denoise(y[s0:s1], y[int(noise_t0 * FS):int(noise_t1 * FS)])
    ref = clean[s0:s1]
    overall = sdr_db(out, ref) - sdr_db(y[s0:s1], ref)
    per = []
    for label, a, b in pack["sections"]:
        sl = slice(int(a * FS) - s0, int(b * FS) - s0)
        per.append((label, sdr_db(out[sl], ref[sl]) - sdr_db(y[s0:s1][sl], ref[sl])))
    return {"overall": overall, "per": per, "stats": st}


def main() -> int:
    pack = build()
    s0, s1 = (int(t * FS) for t in pack["sel"])
    ref = pack["clean"][s0:s1]
    print("台阶噪声（内容三段：低床+10dB → 高床0dB → 低床+10dB，各 2 s 谐波音；"
          "噪声段=选区外的纯噪声前缀）\n")
    print("== 基线：不去噪，选区 1–7 s ==")
    print("  SDR(总) = %.2f dB" % sdr_db(pack["noisy"][s0:s1], ref))
    for label, noise_at in (("噪声段取【低床】纯噪声 0–0.5 s", pack["pure"]["低床"]),
                            ("噪声段取【高床】纯噪声 0.5–1 s", pack["pure"]["高床"])):
        r = measure(pack, *noise_at)
        print("== %s ==" % label)
        print("  ΔSDR(总) = %+.2f dB" % r["overall"])
        for lab, d in r["per"]:
            print("    %-14s ΔSDR %+.2f dB" % (lab, d))
    print("""
读法（本次噪声段都是**纯噪声**，唯一变量是"床变了"）：
* "低床学谱"：高床段欠压制 ⇒ ΔSDR 应明显低于稳态基线（+12.7，denoise_probe）；
* "高床学谱"：低床段**过压制**（把内容当噪声减掉）⇒ ΔSDR 应显著为负——最危险方向；
* 两方向都 >−1 dB ⇒ 挂账可按"实测可容忍"收口；任一 <−3 dB ⇒ 才有换机制的话题
  （候选：逐帧噪声底跟踪，须确定性/可审计）。
""")
    return 0


if __name__ == "__main__":
    sys.exit(main())
