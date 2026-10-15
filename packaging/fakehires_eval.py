#!/usr/bin/env python3
"""假 Hi-Res 盲检测的**自建验证集 + 训练 + 评估**（C3）。

## 为什么必须有这个脚本

工作令的硬纪律 1：

> **不许把论文的 98% 当结论。** 那是学位论文、未独立复现。
> 本项目**自建验证集复现**，**公布自己测到的检出率与误报率**。

这个脚本就是那句"公布"的落地：它造验证集、训练分类器、打印**混淆矩阵**，
并把系数直接打印成可粘贴回 `core/analyzers/fakehires.py` 的形式。

## 验证集的构型（现实链的抽象）

**同一个源信号**，经不同"链"产出，容器采样率相同 —— 这正是"真 Hi-Res vs 升频"的分野：

| 类别 | 造法 | 现实对应 |
|---|---|---|
| **真**（label=1） | 在目标采样率上**原生**生成全带宽内容 | 真高解析录音/母带 |
| **假**（label=0） | 先在 44.1k 上生成（内容止于 22.05k），再**升采样**到目标率 | CD 升频冒充 Hi-Res |
| **假·有损**（label=0） | 先在 44.1k 生成 → **MP3 编码** → 升采样到目标率 | 流媒体/盗版常见链 |

第三条最要紧：**先有损再升频**之后，截止点被抹得不像"硬砖墙"了，
单看截止点会含糊，而差分域的特征还在。

## 划分（决定"泛化"这句话有没有意义）

**按源信号划分**：训练用粉噪 + 白噪，**测试用"类音乐"信号**（谐波堆叠 + 包络）。
理由：论文声称"跨曲风有效"，那就在**没见过的内容类型**上验。
在训练集上自报家门没有意义。

    python packaging/fakehires_eval.py            # 训练 + 评估 + 打印系数
    python packaging/fakehires_eval.py --strict   # 测试集准确率低于门槛则返回 1
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile

import numpy as np
import soundfile as sf
from scipy import signal as sps
from scipy.optimize import minimize

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from audiocheck.core.analyzers.fakehires import (  # noqa: E402
    DECISION_P,
    _sigmoid,
    diff_log_features,
)

FS_LOW = 44100          # "低解析"源：内容止于 22.05 kHz
#: 目标容器采样率。
#: ⚠ **不含 44100**——这是第一版验证集的设计错误（实测踩到）：
#: 44.1k 容器上"真"与"升频"**本来就等价**（内容都止于 22.05 kHz），
#: 同一特征配相反标签 ⇒ 分类器只能塌成多数类（实测 recall=0）。
#: "假 Hi-Res"这个问题只在**容器宣称超过内容**时才有意义。
#: 48000 是**困难档**（真=24k vs 假=22.05k，只差 2 kHz），单列出来看。
FS_TARGETS = (48000, 96000, 192000)
SECONDS = 3.0
#: 每个 (源, 链, 采样率) 组合的重复次数。
#: ⚠ 第一版只取 1：训练集总共 24 条、且正负 6:18 严重不平衡，
#:   逻辑回归被拉成"全判负"（实测 recall=0）。
#:   **样本量与类别不平衡是验证集的设计问题，不是方法问题**——同 C1 的教训。
REPEATS = 4


# --------------------------------------------------------------------- 源信号
def _pink(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    x = rng.standard_normal(n)
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(n)
    X[1:] /= np.sqrt(f[1:])            # 1/f 功率 ⇒ 粉噪
    X[0] = 0.0
    y = np.fft.irfft(X, n)
    return y / (np.abs(y).max() + 1e-12)


def _white(n: int, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y = rng.standard_normal(n)
    return y / (np.abs(y).max() + 1e-12)


def _musiclike(n: int, seed: int) -> np.ndarray:
    """"类音乐"：谐波堆叠 + 慢包络 + **宽带高频成分**（空气感/镲片尾/房间声）。

    ⚠ **高频成分是必须的**（第一版踩过的坑）：谐波堆叠的能量只到 f0×40 ≈ 4.4 kHz，
    于是"genuine"样本在 22 kHz 以上根本没有内容 —— **标签名不副实**，
    测试集上分类器全线误报（实测 0.806）。
    **语料必须真的实现它宣称的类别**，否则验的是别的东西（同 C1 的教训）。

    刻意与噪声源在统计上不同（谱不平坦、有起落），用来检验跨内容类型的泛化。
    """
    rng = np.random.default_rng(seed)
    t = np.arange(n) / 48000.0
    f0 = 110.0 * (1.0 + 0.3 * rng.random())
    y = np.zeros(n)
    for k in range(1, 40):
        y += (1.0 / k ** 1.1) * np.sin(2 * np.pi * f0 * k * t + rng.random() * 6.28)
    env = 1.0 + 0.6 * np.sin(2 * np.pi * 0.7 * t + rng.random() * 6.28)
    y = y * env
    # 宽带高频：5 kHz 以上按粉噪滚降 + 幅度包络（"空气感"）
    fs = 48000.0
    hf = _pink(n, seed + 9001)
    H = np.fft.rfft(hf)
    fr = np.fft.rfftfreq(n)
    H[fr < 5000.0 / fs] = 0.0                      # 只留 5 kHz 以上
    H *= 1.0 / (1.0 + (fr * fs / 5000.0) ** 1.2)   # 再滚一档
    hf2 = np.fft.irfft(H, n)
    hf2 *= 1.0 + 0.5 * np.sin(2 * np.pi * 3.0 * t)
    y = y + 0.10 * hf2
    y += 0.01 * rng.standard_normal(n)
    return y / (np.abs(y).max() + 1e-12)


SOURCES = {"pink": _pink, "white": _white, "music": _musiclike}


# --------------------------------------------------------------------- 链
def _bandlimit(x: np.ndarray, fs: int, hi_hz: float) -> np.ndarray:
    """理想低通（FFT 截断）——模拟"内容止于 hi_hz"。"""
    X = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1.0 / fs)
    X[f > hi_hz] = 0.0
    return np.fft.irfft(X, len(x))


def _resample(x: np.ndarray, fs_from: int, fs_to: int) -> np.ndarray:
    g = np.gcd(fs_from, fs_to)
    return sps.resample_poly(x, fs_to // g, fs_from // g)


def build_case(src_name: str, chain: str, fs_target: int, seed: int):
    """按 (源, 链, 目标采样率) 造一个样本，返回 (x, fs, label)。"""
    n_low = int(FS_LOW * SECONDS)
    x_low = SOURCES[src_name](n_low, seed)
    if chain == "genuine":
        # 原生全带宽：直接在目标率上生成（内容真到奈奎斯特）
        n = int(fs_target * SECONDS)
        x = SOURCES[src_name](n, seed)
        return x, fs_target, 1
    if chain == "upsampled":
        # 内容止于 22.05k（CD 级），再升采样 ⇒ 假 Hi-Res
        x = _bandlimit(x_low, FS_LOW, 22050.0 * 0.999)
        return _resample(x, FS_LOW, fs_target), fs_target, 0
    if chain == "lossy_then_upsampled":
        tmp = os.path.join(tempfile.gettempdir(), "_fh_%s_%d.mp3" % (src_name, seed))
        sf.write(tmp, (x_low * 0.7).astype("float32"), FS_LOW, format="MP3")
        x, _fs = sf.read(tmp, dtype="float64", always_2d=False)
        os.remove(tmp)
        return _resample(x, FS_LOW, fs_target), fs_target, 0
    if chain == "bandlimited":
        x = _bandlimit(x_low, FS_LOW, 16000.0)
        return _resample(x, FS_LOW, fs_target), fs_target, 0
    raise ValueError(chain)


CHAINS = ("genuine", "upsampled", "lossy_then_upsampled", "bandlimited")


# --------------------------------------------------------------------- 训练
def _fit_logreg(X: np.ndarray, y: np.ndarray, l2: float = 1e-3):
    """自实现逻辑回归（L-BFGS）+ **类权重**。
    **不引运行时依赖**（工作令硬纪律 2）。

    ⚠ 类权重是必须的：真/假样本约 1:3，不加权时最优解会整体偏向多数类，
    **打分全部落到 0 以下**（实测 recall=0）——判据在"排序"上是对的、
    在"操作点"上是废的。加权把正类的有效样本提上来，0.5 才重新成为自然门限。
    """
    n, d = X.shape
    pos, neg = float((y > 0.5).sum()), float((y <= 0.5).sum())
    sw = np.where(y > 0.5, (n / 2.0) / max(pos, 1.0), (n / 2.0) / max(neg, 1.0))
    Xb = np.hstack([X, np.ones((n, 1))])

    def obj(theta):
        z = Xb @ theta
        p = _sigmoid(z)
        eps = 1e-12
        loss = -np.mean(y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps))
        swsum = float(sw.sum())
        loss -= float(np.sum(sw * (y * np.log(p + eps) + (1 - y) * np.log(1 - p + eps)))) / swsum
        loss += l2 * float(np.dot(theta[:-1], theta[:-1]))
        g = Xb.T @ (sw * (p - y)) / swsum
        g[:-1] += 2 * l2 * theta[:-1]
        return loss, g

    res = minimize(obj, np.zeros(d + 1), jac=True, method="L-BFGS-B",
                   options={"maxiter": 500})
    return res.x


def _evaluate(w, b, rows) -> dict:
    tp = tn = fp = fn = 0
    for x, fs, y in rows:
        p = _sigmoid(float(np.dot(w, diff_log_features(x, fs))) + b)
        pred = 1 if p >= DECISION_P else 0
        if y == 1 and pred == 1:
            tp += 1
        elif y == 0 and pred == 0:
            tn += 1
        elif y == 0 and pred == 1:
            fp += 1
        else:
            fn += 1
    n = tp + tn + fp + fn
    acc = (tp + tn) / n if n else 0.0
    rec = tp / (tp + fn) if (tp + fn) else 0.0        # 检出率（真 Hi-Res 认得出）
    spec = tn / (tn + fp) if (tn + fp) else 0.0       # 特异度
    fpr = fp / (fp + tn) if (fp + tn) else 0.0        # 误报率（假 Hi-Res 被判成真）
    return {"n": n, "tp": tp, "tn": tn, "fp": fp, "fn": fn,
            "acc": acc, "recall": rec, "spec": spec, "fpr": fpr}


def _make_rows(src_names, targets, chains, repeats=1):
    rows = []
    seed = 1
    for s in src_names:
        for fs in targets:
            for ch in chains:
                for _r in range(repeats):
                    x, f_, y = build_case(s, ch, fs, seed)
                    seed += 1
                    rows.append((x, f_, y))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description="假 Hi-Res 盲检测：自建验证集 + 训练 + 评估")
    ap.add_argument("--strict", action="store_true", help="测试集准确率 <0.85 则返回 1")
    a = ap.parse_args()

    # ---- 划分：**按源信号**（训练用噪声，测试用"类音乐"）----
    train_src = ("pink", "white")
    test_src = ("music",)
    tr = _make_rows(train_src, FS_TARGETS, CHAINS, REPEATS)
    te = _make_rows(test_src, FS_TARGETS, CHAINS, REPEATS)

    Xtr = np.array([diff_log_features(x, fs) for x, fs, _y in tr])
    ytr = np.array([y for _x, _fs, y in tr], dtype=float)
    theta = _fit_logreg(Xtr, ytr)
    w, b = theta[:-1], theta[-1]

    print("验证集：源 %s 训练 / 源 %s 测试（**按内容类型划分**，不是随机划分）"
          % ("+".join(train_src), "+".join(test_src)))
    print("链：%s ｜ 目标采样率：%s ｜ 每样本 %.1f s"
          % (" / ".join(CHAINS), FS_TARGETS, SECONDS))
    print("样本数：训练 %d ｜ 测试 %d" % (len(tr), len(te)))
    print()
    for name, rows in (("训练集", tr), ("**测试集（没见过的内容类型）**", te)):
        r = _evaluate(w, b, rows)
        print("== %s ==" % name)
        print("  混淆矩阵  真=1 假=0 ｜ TP %d ｜ TN %d ｜ FP %d ｜ FN %d"
              % (r["tp"], r["tn"], r["fp"], r["fn"]))
        print("  准确率 %.3f ｜ 检出率(真Hi-Res) %.3f ｜ 特异度 %.3f ｜ **误报率 %.3f**"
              % (r["acc"], r["recall"], r["spec"], r["fpr"]))
        # 按采样率分开看：48000 是困难档（真 24k vs 假 22.05k，只差 2 kHz）
        for fs in FS_TARGETS:
            sub = [row for row in rows if row[1] == fs]
            if not sub:
                continue
            rs = _evaluate(w, b, sub)
            print("    %6d Hz  准确率 %.3f ｜ 检出 %.3f ｜ 误报 %.3f  (n=%d)"
                  % (fs, rs["acc"], rs["recall"], rs["fpr"], rs["n"]))
        print()

    rte = _evaluate(w, b, te)
    print("=" * 78)
    print("可粘贴回 `src/audiocheck/core/analyzers/fakehires.py` 的系数：")
    print("MODEL_W = (%s)" % ", ".join("%.3f" % v for v in w))
    print("MODEL_B = %.3f" % b)
    print("=" * 78)
    print()
    print("⚠ 本表是**合成验证集**上的结果。真实曲库（有损史、母带处理、多代转录）"
          "上的表现**未测**——")
    print("   所以 `fakehires.py` 的输出口径是**线索（非判据）**，"
          "升格为判据的前提是真实曲库误报率可接受。")
    if a.strict and rte["acc"] < 0.85:
        print("\n**测试集准确率 %.3f < 0.85** —— 判据未达标。" % rte["acc"])
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
