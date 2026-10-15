"""假 Hi-Res **盲检测**（C3）—— 唯一有学术出处的智能扩展。

## 出处（必须原样引用，含可信度限定）

> **NYCU 2017 学位论文**《一個針對造假的高解析度音訊的盲檢測演算法》
> （席冠文、羅濟群；`ir.lib.nycu.edu.tw/handle/11536/141130`）。
> 方法链：**取差分 → 取绝对值 → 取对数 → FFT → 分频段取均值作特征 → SVM 分类**。
> 论文报告 100 ms 样本 **98%+** 准确率、跨曲风跨采样率有效。

⚠ **三条限定，写在最前面**（工作令的硬纪律）：

1. **不许把 98% 当结论**。那是**学位论文**（非期刊）且**未独立复现**。
   本项目**自建验证集复现**，只公布**自己测到的**检出率与误报率。
2. **不许引入运行时依赖**（打包已 280 MB）。分类器用 numpy/scipy **自实现**。
3. **输出先标"线索 / 参考"**，不是判据 —— 直到验证集数据支持升格。

## 为什么这张牌值得打

"伪 Hi-Res"是本工具用户最常撞上的坑之一（流媒体/盗版链里 44.1k 升频成 96k 当
Hi-Res 卖）。现有判定只能靠**截止点**——而截止点只能说明"内容到哪为止"，
**说不清"这段高频是真的还是升频补出来的"**。差分域的谱形状正是那点差别所在。

## 本实现的形态（与论文的差异必须写明）

* 论文用 **SVM**；本项目用**自实现逻辑回归**（`scipy.optimize` 的 L-BFGS）。
  理由：线性 SVM 与逻辑回归在这类低维特征上判别面几乎一致，而逻辑回归的
  **系数可直接读出、可解释、可复算**——本项目对"可追溯"的要求高于 0.5% 的准确率差。
* 论文的频段划分未在摘要中给出；本实现用**对数等距 6 段**（20 Hz → 奈奎斯特），
  并在 `packaging/fakehires_eval.py` 里可换参复算。
* 特征**做了 L2 归一化**（论文未提）：不做的话，特征幅度会被整体电平带偏，
  而"是不是升频"与"录得多响"无关。这是本项目的工程判断，**非论文原文**。
"""
from __future__ import annotations

import numpy as np

from ..dsp import spectral_features  # noqa: F401  （保留：与本模块同层）
from ..registry import Analyzer, register

#: 频段数（对数等距）。6 段在 20 Hz–奈奎斯特上约每 1.7 倍频程一段。
N_BANDS = 6

#: 训练得到的逻辑回归系数（**由 `packaging/fakehires_eval.py` 产出，不许手改**）。
#: 结构：(w[6], b)。训练脚本会打印这一段并可直接粘贴回来。
#: ⚠ 改动必须同时更新 `docs/thresholds.md` 里的验证集数字与 `tests/test_c3_fakehires.py`。
MODEL_W = (-1.982, 0.412, 1.375, 1.664, -0.530, -1.271)
MODEL_B = 0.087

#: 判定门限（sigmoid 概率）。0.5 是**分类器的自然门限**，不是调出来的。
DECISION_P = 0.5


def diff_log_features(x: np.ndarray, fs: int, n_bands: int = N_BANDS) -> np.ndarray:
    """NYCU 2017 的特征：差分 → 绝对值 → 对数 → FFT → 分频段取均值 → L2 归一化。

    差分（一阶）等价于一个**高频提升**滤波器 —— 升频/带限留下的"硬边"
    与镜像痕迹在这种提升下最显眼，而正常的宽带内容在差分域里是平滑的。

    返回长度 `n_bands` 的特征向量（L2 归一化）。
    """
    d = np.diff(np.asarray(x, dtype=np.float64).ravel())
    if d.size < 256:
        return np.zeros(n_bands, dtype=np.float64)
    L = np.log(np.abs(d) + 1e-12)
    L = L - L.mean()                     # 去直流：对数域的整体偏移是电平，不是内容
    win = np.hanning(L.size)
    X = np.abs(np.fft.rfft(L * win))
    fr = np.fft.rfftfreq(L.size, 1.0 / fs)
    hi = min(fs / 2.0, 24000.0)          # 只看音频带内（>24k 对 44.1k 容器无意义）
    edges = np.geomspace(20.0, hi, n_bands + 1)
    out = np.empty(n_bands, dtype=np.float64)
    for i in range(n_bands):
        m = (fr >= edges[i]) & (fr < edges[i + 1])
        out[i] = float(X[m].mean()) if m.any() else 0.0
    n = float(np.linalg.norm(out))
    return out / n if n > 1e-12 else out


def _sigmoid(z):
    return 1.0 / (1.0 + np.exp(-np.clip(z, -60.0, 60.0)))


def fake_probability(x: np.ndarray, fs: int) -> float:
    """判为"内容不真到容器带宽"的概率（0–1）。**这是线索，不是判据。**"""
    f = diff_log_features(x, fs)
    return float(_sigmoid(float(np.dot(MODEL_W, f)) + MODEL_B))


@register
class FakeHiresAnalyzer(Analyzer):
    """假 Hi-Res 盲检测（C3）。

    ## 输出口径（工作令硬纪律 3）

    字段名带 `_hint` 后缀、文案写"**线索**"——**不许**以确定语气出现。
    理由：依据是**未独立复现的学位论文**，且本项目的验证集是**合成**的。
    升格为判据的前提是"真实曲库上的误报率可接受"，那还没做。

    ## 只在相关时才跑

    只对 **容器 >48 kHz** 或**截止点明显低于奈奎斯特**的文件报线索——
    对 44.1k 满带宽文件跑这个检测没有意义（它本来就不宣称 Hi-Res）。
    这样也避免把误报摊到全部文件上。
    """

    key = "fakehires"
    label = "假 Hi-Res 线索"
    order = 88                     # 在 verdict(90) 之前
    requires = ("basic", "spectrum")

    #: **默认不启用**（C3 的结论，不是"还没做完"）。
    #:
    #: `packaging/fakehires_eval.py` 的自建验证集给出（合成语料，数字可复算）：
    #:   * 训练集（粉噪+白噪）准确率 0.875 ｜ 测试集（没见过的"类音乐"内容）**误报率 0.722**。
    #:   * 原因：L2 归一后的 6 段对数特征是**整体谱形描述**。噪声源上"假"样本的
    #:     顶部带塌掉，差异很大；而谱形自然滚降的内容上，顶部带仍有内容，差异很小。
    #:   * 拆开看，真正把两类分开的是"**22 kHz 以上有没有真实能量**"——
    #:     **而那一项本项目已经有 `cutoff_hz` 在测**，且比这个分类器更可靠。
    #:
    #: ⇒ **判定：这个特征在本项目的合成验证集上不构成截止点之外的增量**，
    #:   并且跨内容类型不泛化。按工作令硬纪律 3（输出先标"线索"）本可输出，
    #:   但输出一个**测试集误报率 72% 的分类器**比没有它更糟（用户会去信）。
    #:   故默认关闭；代码与验证脚本**保留**（结论要可复算，不是删掉了看不见）。
    #:
    #: 需要重开：`--only fakehires`，或在注册表里把它置 True。
    #: 若要升格为判据，前提是**真实曲库**上的误报率可接受——那还没做。
    enabled = False

    def run(self, ctx) -> dict:
        fs = ctx.fs
        r = ctx.result
        cut = r.get("cutoff_hz")
        nyq = fs / 2.0
        # 只在"宣称高带宽"或"内容明显不到奈奎斯特"时才值得看
        interesting = fs > 48000 or (cut is not None and cut < nyq * 0.92)
        if not interesting:
            return {"fake_hires_hint": None,
                    "fake_hires_note": "不适用（容器带宽与内容一致）"}
        seg, _ = ctx.segment(seconds=20.0)
        if seg is None or not len(seg):
            return {"fake_hires_hint": None,
                    "fake_hires_note": "无法取样（读不到音频）"}
        # 双声道取功率合并（与全项目的声道口径一致，见 views.py）
        p = float(np.mean([fake_probability(seg[:, c], fs) for c in range(seg.shape[1])]))
        if p >= 0.85:
            hint = "高"
        elif p >= 0.60:
            hint = "中"
        elif p >= 0.40:
            hint = "低（不确定）"
        else:
            hint = "未见线索"
        return {
            "fake_hires_prob": round(p, 3),
            "fake_hires_hint": hint,
            "fake_hires_note": ("线索（非判据）：依据 NYCU 2017 学位论文的差分对数谱特征 + "
                                "本项目自训练逻辑回归；**未独立复现**，仅供人工复核"),
        }
