#!/usr/bin/env python3
"""denoise_probe：**去噪先量四个候选**（判据先行，别先写算子）。

## 问题

`docs/editor_roadmap.md §4` 的 P3 还剩"denoise（多分辨率谱减）"。但谱减法这一族
有一个众所周知的副作用——**musical noise**（逐帧增益随机抖动 ⇒ 听成鸟叫）。
`docs/editor_research.md §7` 已经量过：musical noise 的机制是"**增益由带噪估计导出、
逐帧随机**"；去噪的掩码正是这种形态 ⇒ 这个副作用**在我们这里会发生**，必须量。

## 候选（同一套 STFT、同一个噪声估计）

1. **谱减（Berouti）**：`G = sqrt(max(1 − α·N/|Y|², β))`，α=4、β=0.02；
2. **Wiener + 判决引导先验 SNR**（Ephraim–Malah 1984 的 DD 递归，α_dd=0.98）；
3. **MMSE-LSA**（Ephraim–Malah 1985；`ξ/(1+ξ)·exp(½·E1(v))`）；
4. **Wiener + 逐帧真值噪声谱（oracle）**——用来判断瓶颈是"噪声估计"还是"增益规则"。

噪声估计（候选 1–3 共用，可复算）：按**帧总能量**排序，取最低 `q` 分位（默认 10%）
的帧，逐 bin 取平均功率。⚠ 这是**工程估计**（无外部出处），所以探针同时扫 q。

## 量什么（全部是"多少 dB"，不是"好不好"）

* `ΔSDR` = 处理后相对**干净真值**的 SDR − 处理前的 SDR（主判据）；
* **噪声段压制**（纯噪声输入，输出/输入的 RMS）；
* **musical-noise 代理**：纯噪声输入下，50 ms 帧 RMS 的**dB 标准差**在处理后相对
  处理前的**变化**（增益不改变这个量；谱减的随机抖动会让它变大）；
* **瞬态保真**：已知位置的单采样脉冲，处理后局部峰值相对处理前的变化（dB）；
* **能量比**（防"变安静被当成修好"）。

    python packaging/denoise_probe.py
"""
from __future__ import annotations

import sys

import numpy as np
from scipy.special import exp1

sys.path.insert(0, "src")
from audiocheck.core.edit import corpus, stft  # noqa: E402
from audiocheck.core.edit.metrics import energy_ratio_db, sdr_db  # noqa: E402

FS = 48000
T = 2.0
N_FFT = 4096
HOP = N_FFT // 4
Q = 0.10
REDUCTION_DB = 20.0
G_MIN = 10.0 ** (-REDUCTION_DB / 20.0)
ALPHA_DD = 0.98
SS_ALPHA = 4.0
SS_BETA = 0.02


# ------------------------------------------------------------------ STFT 工具
def _stft(x, n_fft=N_FFT, hop=HOP):
    S, meta = stft.analyze(x, n_fft=n_fft, hop=hop)
    return S, meta


def _istft(S, meta):
    return stft.synthesize(S, meta)


def _quiet_frames(P, q=Q):
    """按帧总能量取最低 `q` 分位的帧，逐 bin 平均功率 ⇒ 噪声 PSD `(bins, ch)`。"""
    bins, frames, ch = P.shape
    e = P.sum(axis=0)                                  # (frames, ch)
    k = max(1, int(round(q * frames)))
    N = np.zeros((bins, ch))
    for c in range(ch):
        order = np.argsort(e[:, c], kind="stable")[:k]
        N[:, c] = P[:, order, c].mean(axis=1)
    return N


def _gain_ss(P, N, *, alpha=SS_ALPHA, beta=SS_BETA):
    """Berouti 谱减的幅度增益（逐帧、逐 bin）。"""
    gamma = np.maximum(P, 1e-30) / np.maximum(N[:, None, :], 1e-30)
    return np.sqrt(np.maximum(1.0 - alpha / gamma, beta))


def _gain_dd(P, N, *, mmse=False, tg_factor=None, tg_frac=None, tg_gamma=4.0,
             alpha=ALPHA_DD, g_min=G_MIN):
    """判决引导先验 SNR 的 Wiener / MMSE-LSA 增益；`N` 可为 `(bins, ch)` 或逐帧。

    两个**瞬态守卫候选**（都只做实验，未定）：
    * `tg_factor`：帧总能量 > `tg_factor` × 全段中位数 ⇒ 该帧不压制（实测：无效，
      单采样脉冲只占帧能量的千分之几，永远过不了这条）；
    * `tg_frac`：**逐帧看"有多少 bin 的信噪比 > `tg_gamma`"**——脉冲是宽带的，
      会让大量"内容本来没有的 bin"同时抬高；音乐瞬态同理。比例超过 `tg_frac`
      就判为瞬态帧，不做压制。
    """
    bins, frames, ch = P.shape
    Nf = N if N.ndim == 3 else np.repeat(N[:, None, :], frames, axis=1)
    G = np.empty_like(P)
    g_prev = np.ones((bins, ch))
    gamma_prev = np.ones((bins, ch))
    trans_energy = None
    if tg_factor is not None:
        e = P.sum(axis=0)                                  # (frames, ch)
        trans_energy = e > tg_factor * np.median(e, axis=0, keepdims=True)
    for t in range(frames):
        gamma = np.maximum(P[:, t, :], 1e-30) / np.maximum(Nf[:, t, :], 1e-30)
        xi = (alpha * (g_prev ** 2) * gamma_prev
              + (1.0 - alpha) * np.maximum(gamma - 1.0, 0.0))
        g = xi / (1.0 + xi)
        if mmse:
            v = xi * gamma / (1.0 + xi)
            g = g * np.exp(0.5 * exp1(np.maximum(v, 1e-30)))
        g = np.maximum(g, g_min)
        if trans_energy is not None:
            g[:, trans_energy[t]] = 1.0
        if tg_frac is not None:
            hot = np.mean(gamma > tg_gamma, axis=0) > tg_frac
            g[:, hot] = 1.0
        G[:, t, :] = g
        g_prev, gamma_prev = g, gamma
    return G


def _noise_from_profile(P, frames, t0, t1, n_fft=N_FFT, hop=HOP):
    """从**噪声段** `[t0, t1)`（秒）的帧里取平均功率（真正可用的"学习噪声谱"）。"""
    start = -n_fft
    times = (start + np.arange(frames) * hop + n_fft / 2.0) / FS
    sel = (times >= t0) & (times < t1)
    if not sel.any():
        raise ValueError("噪声段 [%r, %r) 里没有一帧" % (t0, t1))
    return P[:, sel, :].mean(axis=1)


def denoise(y, method="wiener", *, q=Q, noise=None, profile=None,
            n_fft=N_FFT, hop=None, alpha=ALPHA_DD, reduction_db=REDUCTION_DB):
    """跑一个候选。`noise` 只给 oracle 用（已知噪声）；`profile` = 学噪声的时段。"""
    hop = n_fft // 4 if hop is None else hop
    g_min = 10.0 ** (-reduction_db / 20.0)
    S, meta = _stft(y, n_fft, hop)
    P = np.abs(S) ** 2
    if method == "ss":
        N = (_quiet_frames(P, q) if profile is None
             else _noise_from_profile(P, S.shape[1], *profile, n_fft, hop))
        G = _gain_ss(P, N)
    elif method in ("wiener", "mmse", "wiener_tf05", "wiener_tf10", "wiener_tf20"):
        N = (_quiet_frames(P, q) if profile is None
             else _noise_from_profile(P, S.shape[1], *profile, n_fft, hop))
        tg = {"wiener_tf05": 0.05, "wiener_tf10": 0.10, "wiener_tf20": 0.20}.get(method)
        G = _gain_dd(P, N, mmse=(method == "mmse"), tg_frac=tg,
                     alpha=alpha, g_min=g_min)
    elif method == "oracle":
        if noise is None:
            raise ValueError("oracle 需要已知噪声")
        Sn, _ = _stft(noise, n_fft, hop)
        G = _gain_dd(P, np.abs(Sn) ** 2, alpha=alpha, g_min=g_min)
    else:
        raise ValueError("未知候选 %r" % method)
    return _istft(S * G, meta)


# ------------------------------------------------------------------ 指标
def _frame_rms_db(x, win=int(0.05 * FS), hop=int(0.025 * FS)):
    a = x[:, 0]
    idx = np.arange(0, max(1, len(a) - win + 1), hop)
    r = np.array([np.sqrt(np.mean(a[i:i + win] ** 2)) for i in idx])
    r = r[r > 0]
    return 20.0 * np.log10(r)


def _noise_modulation_db(x, k0=0, k1=None):
    """**纯噪声段**的 50 ms 帧 RMS 的 dB 标准差（musical-noise 代理）。

    ⚠ 必须只量**噪声段**：把内容段混进来，帧 RMS 的双峰分布（内容响、间隙静）
    会把"信号包络"算成"噪声抖动"——那是量具错误，不是伪影。
    """
    return float(np.std(_frame_rms_db(x[k0:k1 if k1 is not None else len(x)])))


def _rms_db(x):
    return 20.0 * np.log10(float(np.sqrt(np.mean(x ** 2))) + 1e-30)


def _click_peak_ratio_db(before, after, idx, half=3):
    """已知脉冲位置的局部峰值变化（dB）；多个位置取中位数。"""
    vals = []
    for i in idx:
        a = before[max(0, i - half):i + half + 1, 0]
        b = after[max(0, i - half):i + half + 1, 0]
        pa, pb = float(np.abs(a).max()), float(np.abs(b).max())
        if pa > 0:
            vals.append(20.0 * np.log10((pb + 1e-30) / pa))
    return float(np.median(vals)) if vals else None


# ------------------------------------------------------------------ 场景
LEAD = 0.5          # 每个场景前 0.5 s 纯噪声（用来"学噪声谱"，模拟专业工具的做法）


def _scenario(name, clean, noisy, *, noise=None, clicks=None, profile=None,
              mod_range=None):
    return {"name": name, "clean": clean, "noisy": noisy, "noise": noise,
            "clicks": clicks, "profile": profile, "mod_range": mod_range}


def _embed(clean, noisy, noise):
    """把前 `LEAD` 秒换成**纯噪声**：`clean` 置 0、`noisy` 用噪声本身。"""
    lead = int(LEAD * FS)
    c = clean.copy()
    c[:lead] = 0.0
    y = noisy.copy()
    y[:lead] = noise[:lead]
    return c, y, (0.0, LEAD - 0.03)


def scenarios():
    out = []
    lead = int(LEAD * FS)
    tone = corpus.harmonic_tone(T, FS, amp=0.5, seed=3)
    for snr in (10.0, 0.0, -5.0):
        r = corpus.add_noise(tone, snr, "white", seed=7)
        c, y, prof = _embed(tone, r["noisy"], r["noise"])
        out.append(_scenario("tone+white %+d dB" % snr, c, y, noise=r["noise"],
                             profile=prof, mod_range=(0, lead)))
    r = corpus.add_noise(tone, 0.0, "pink", seed=9)
    c, y, prof = _embed(tone, r["noisy"], r["noise"])
    out.append(_scenario("tone+pink 0 dB", c, y, noise=r["noise"],
                         profile=prof, mod_range=(0, lead)))

    # 噪声型内容（不可分离）：用来量"最坏情况下会伤多少"
    noisey = corpus.band_limited_noise(T, FS, 18000.0, amp=0.5, seed=5)
    r = corpus.add_noise(noisey, 0.0, "white", seed=11)
    c, y, prof = _embed(noisey, r["noisy"], r["noise"])
    out.append(_scenario("noise-content 0 dB", c, y, noise=r["noise"],
                         profile=prof, mod_range=(0, lead)))

    # 瞬态保真：谐波音 + 已知位置脉冲 + 噪声（脉冲都在内容段，别落进噪声段）
    base = corpus.harmonic_tone(T, FS, amp=0.35, seed=13)
    pos = [0.77, 1.23, 1.61]
    ck = corpus.click_train(base, pos, FS, amp=0.6)
    r = corpus.add_noise(ck["clicked"], 5.0, "white", seed=17)
    c, y, prof = _embed(ck["clicked"], r["noisy"], r["noise"])
    out.append(_scenario("tone+clicks+white 5 dB", c, y, noise=r["noise"],
                         clicks=ck["positions_samples"], profile=prof,
                         mod_range=(0, lead)))

    # 纯噪声：压制与 musical noise 代理
    nz = corpus.white_noise(T, FS, rms=0.1, seed=19)
    out.append(_scenario("noise-only", np.zeros_like(nz), nz, noise=nz,
                         profile=(0.0, T), mod_range=(0, len(nz))))
    return out


METHODS = (("ss", "谱减"), ("wiener", "Wiener-DD"), ("mmse", "MMSE-LSA"),
           ("wiener_tf05", "Wiener+瞬态5%"), ("wiener_tf10", "Wiener+瞬态10%"),
           ("wiener_tf20", "Wiener+瞬态20%"), ("oracle", "Wiener-oracle"))


def _row(sc, key, label, profile):
    base_sdr = sdr_db(sc["noisy"], sc["clean"]) if np.any(sc["clean"]) else None
    k0, k1 = sc["mod_range"]
    base_mod = _noise_modulation_db(sc["noisy"], k0, k1)
    out = denoise(sc["noisy"], key, q=Q, noise=sc["noise"], profile=profile)
    s = sdr_db(out, sc["clean"]) if np.any(sc["clean"]) else None
    dsdr = None if s is None or base_sdr is None else s - base_sdr
    er = energy_ratio_db(out, sc["clean"]) if np.any(sc["clean"]) else None
    supp = _rms_db(out) - _rms_db(sc["noisy"])
    mod = _noise_modulation_db(out, k0, k1) - base_mod
    extra = ""
    if sc["clicks"]:
        extra = " ｜ 脉冲 %+.2f dB" % _click_peak_ratio_db(
            sc["noisy"], out, sc["clicks"])
    print("%-22s %-14s %8s %8s %8s %8s %8s%s" %
          (sc["name"], label,
           "—" if dsdr is None else "%+.2f" % dsdr,
           "—" if s is None else "%.2f" % s,
           "—" if er is None else "%+.2f" % er,
           "%+.2f" % supp, "%+.2f" % mod, extra))


def main() -> int:
    print("去噪候选对比（n_fft=%d / hop=%d / 压制上限 %.0f dB）\n"
          % (N_FFT, HOP, REDUCTION_DB))
    print("**噪声谱从每个场景前 %.2f s 的纯噪声段学出来**（`profile`）\n" % LEAD)
    print("%-22s %-14s %8s %8s %8s %8s %8s" %
          ("场景", "候选", "ΔSDR", "SDR", "能量比", "噪声压制", "帧RMS-σ"))
    print("-" * 90)
    for sc in scenarios():
        for key, label in METHODS:
            if key == "oracle" and sc["noise"] is None:
                continue
            _row(sc, key, label, sc["profile"])
        print("-" * 90)

    # 对照：**盲估计**（不学噪声谱，取最低 q 分位帧）——上一版实测在这里崩掉
    print("\n对照：同一素材、**盲估计**（最低 q=%.2f 分位帧，不给噪声段）\n" % Q)
    print("%-22s %-14s %8s %8s %8s %8s %8s" %
          ("场景", "候选", "ΔSDR", "能量比", "噪声压制", "帧RMS-σ", ""))
    print("-" * 90)
    for sc in scenarios():
        if sc["name"] not in ("tone+white +10 dB", "tone+white +0 dB"):
            continue
        for key, label in METHODS[:3]:
            _row(sc, key, label, None)
        print("-" * 70)
    # 参数扫描：窗长 / DD 平滑系数 / 压制上限（都在 tone+clicks 与纯噪声上量）
    clk = next(sc for sc in scenarios() if "clicks" in sc["name"])
    nz = next(sc for sc in scenarios() if sc["name"] == "noise-only")
    print("\n参数扫描（Wiener-DD，噪声谱来自 profile）\n")
    print("%-34s %8s %8s %8s %8s %8s" %
          ("设置", "ΔSDR", "能量比", "脉冲", "噪声压制", "帧RMS-σ"))
    print("-" * 82)

    def sweep(label, **kw):
        out = denoise(clk["noisy"], "wiener", profile=clk["profile"], **kw)
        s = sdr_db(out, clk["clean"])
        base = sdr_db(clk["noisy"], clk["clean"])
        outp = denoise(nz["noisy"], "wiener", profile=nz["profile"], **kw)
        print("%-34s %8s %8s %8s %8s %8s" %
              (label, "%+.2f" % (s - base),
               "%+.2f" % energy_ratio_db(out, clk["clean"]),
               "%+.2f" % _click_peak_ratio_db(clk["noisy"], out, clk["clicks"]),
               "%+.2f" % (_rms_db(outp) - _rms_db(nz["noisy"])),
               "%+.2f" % (_noise_modulation_db(outp) - _noise_modulation_db(nz["noisy"]))))

    for n in (1024, 2048, 4096):
        sweep("n_fft=%d" % n, n_fft=n)
    print("-" * 82)
    for a in (0.90, 0.95, 0.98, 0.99):
        sweep("alpha_dd=%.2f" % a, alpha=a)
    print("-" * 82)
    for d in (10.0, 20.0, 30.0):
        sweep("压制上限 %.0f dB" % d, reduction_db=d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
