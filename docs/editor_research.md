# 编辑器方向：市场 · 文献 · 数学基础（2026-10-18）

> **口径**：本文只记**本次检索到出处**的条目；没核到的写在 §5「未核清单」，**不许编**。
> 市场判断多为**二手来源**（厂商页 / 评测文章），属**推断**而非测量——已逐条标注可信度。

---

## 1. 市场（二手来源，标可信度）

| 工具 | 官方/评测怎么说 | 可信度 |
|---|---|---|
| iZotope RX（Advanced） | "行业标准"；**Spectral Repair 用模式化插值**（pattern-based interpolation）在被污染区填邻域干净音频；De-hum / De-click / De-clip 用"智能检测"定位异常；新版引入 ML | 二手（厂商页 + 评测站） |
| Steinberg SpectraLayers Pro | **分层式频谱编辑**（像图像图层）、**谐波/噪声分离**、Noise Painting 直接"涂掉"伪影；ARA 与 DAW 时间线联动 | 二手（评测站） |
| Acon Digital Acoustica | "更性价比的编辑 + 修复套装" | 二手（评测站） |
| Audacity 4 | 免费开源**起点**；有基础频谱图 + Noise Reduction；**"缺 RX 那种外科级精度"** | 二手（评测站） |
| 文件层修复 | "若文件打不开或缺数据，用容器/文件修复流程——**修复处理无法重建缺失的字节**" | 二手（评测站） |

**结论（推断，非测量）**：Windows 原生 + 开源 + **外科级频谱修复**没有强玩家；
但注意最后一行——**"缺字节"与"可修复"是两回事**，这正是本项目判定层已经在做的事，
编辑器应当继承这条诚实（**不许承诺"重建已经不存在的数据"**）。

⚠ **不做**任何市场份额/用户量的量化断言——没有权威源。

---

## 2. 文献（按算法簇；每条给"对实现意味着什么"）

### 2.1 统一框架：**Audio Inpainting**（修复的理论伞）
- **A. Adler, V. Emiya, M. G. Jafari, M. Elad, R. Gribonval, M. D. Plumbley.
  *Audio Inpainting*. IEEE Trans. Audio, Speech, and Language Processing, 20(3), 2012.**
  把"脉冲噪声、削波、丢包"统一看成**已知位置的缺失**问题。
  ⇒ **意义**：declip / declick / 谱修复**不该各写各的**，应共享一个"缺失 + 重建"的骨架与判据。
- **PHAIN: *Audio Inpainting via Phase-Aware Optimization with Instantaneous Frequency*. IEEE, 2024.**
  指出纯稀疏推广的两大弊病：**幅度被压小**、**忽略正弦成分的时间连续性**，改用相位感知约束。
  ⇒ **意义**：我们的去削波若走纯 ℓ1 路线，**必须量"幅度缩水"**（否则听起来"变小声"还以为是修好了）。

### 2.2 去削波 declipping
- **P. Záviška, P. Rajmic, A. Ozerov, L. Rencker. *A Survey and an Extensive Evaluation of
  Popular Audio Declipping Methods*. IEEE JSTSP, 2020（DOI 10.1109/JSTSP.2020.3042071）.**
  逐算法列**假设 / 建模域 / 优化算法**，用 **SDR** 与感知指标在真实音频上大规模评测。
- **Záviška et al. *Sparsity-Based Audio Declipping Methods: Selected Overview, New Algorithms,
  and Large-Scale Evaluation*.（IEEE，2021）** 提出 synthesis vs analysis sparsity +
  结构化稀疏的统一框架；**并明确指出"既有基准的削波程度偏温和"，呼吁更狠的基准**。
  ⇒ **意义**：我们自建合成语料时，**削波程度要覆盖到"感知上显著"的档**，否则重演"基准太软"。

### 2.3 去咔哒 / 去爆音（declick / decrackle）
- 统一框架见 2.1（脉冲噪声 = 缺失）。经典统计模型（Godsill & Rayner）**本次未逐字核原文** ⇒ 见 §5。

### 2.4 抖动与噪声整形（dither / noise shaping）—— **导出链路的基础**
- **S. Lipshitz, J. Vanderkooy, R. A. Wannamaker. *Minimally Audible Noise Shaping*
  （JAES，1991 前后）**，及其 dither 系列（*Quantization and Dither: A Theoretical Survey*）。
  第三方文献反复称其为该领域权威；"整形后的噪声按**听感敏感度曲线**摆放"。
  ⇒ **意义**：写 16-bit 导出时**必须有抖动/噪声整形**，否则"再量化"会把本可避免的失真留在成品里。

### 2.5 采样率变换 / 时频基础 —— 见 §5（本次未核到正式出处）

---

## 3. 数学基础（要写进 `docs/theory.md` 的推导清单）

| # | 主题 | 为什么它是**判据**而不只是知识 |
|---|---|---|
| 1 | **STFT 与完美重建（COLA/NOLA）** | 决定了"分析窗 / 合成窗 / hop"的可选集；改 hop 就改可重建性——这决定 View 与 Edit 的**同一批参数**能不能共用 |
| 2 | **窗 sinc / 带限插值**（重采样） | 重采样的**通带-阻带**指标（passband ripple、stopband atten）是**可测**的，不许"听起来行就行" |
| 3 | **量化误差谱与噪声整形**（Lipshitz 等） | 16-bit 导出的**误差谱**可算可画 ⇒ 抖动方案能被**验证**，不是玄学 |
| 4 | **削波失真模型**：`x = clip(s)`，观测为线性约束 | 去削波 = 在**已知约束**下估 `s`；"削了多少样本、多深"是**可测输入**，不是猜 |
| 5 | **Inpainting 的 analysis vs synthesis 稀疏** | 两种算子的**解不同**（前者求系数、后者求信号），对幅度缩水的影响不同 ⇒ 选型要有依据 |
| 6 | **相位感知 / 瞬时频率** | "时间连续的正弦"是硬约束；不做它，修复处会出现**相位不连续**（可测：修复边界的相位跳变） |

---

## 4. 落地映射（研究 → 代码）

| 算法簇 | 计划中的 `Op` | 先建的**判据**（合成语料 + 客观指标） | 依赖 |
|---|---|---|---|
| 时间选区增益 | `GainOp` ✅ 已实现 | 区外**逐位不变**；区内恰为设定 dB | — |
| 去削波 | `DeclipOp` | 已知真值的**合成削波**：SDR 提升 + **幅度缩水**上限 | 2.1 / 2.2 |
| 去咔哒 | `DeclickOp` | 合成脉冲：检出率/误报率 + 残余脉冲能量 | 2.1 |
| 去嗡声 | `DehumOp` | 50/100/150 Hz 峰下降 + **非目标频段差异 < 阈值** | 待核（§5） |
| 去噪 | `DenoiseOp` | SNR 提升 + **音乐性伪影**（musical noise）指标 | 待核（§5） |
| 导出抖动 | `render(..., dither=…)` | 误差谱按整形目标**可复算** | 2.4 |

**纪律**：每行的"判据"必须先落地（合成语料 + 客观指标 + 效力边界写进 UI），**再**实现算法。

---

## 5. 未核清单（诚实，别当已核用）

- **去噪经典出处**（Boll 1979 谱减法 / Ephraim & Malah 1985 MMSE-LSA）：**本次未复核**。
- **采样率变换理论**（soxr / Julius O. Smith 的 band-limited interpolation）：**本次未核**。
- **Godsill & Rayner** 原书/论文细节：**未核**。
- **STFT 经典出处**（Allen & Rabiner 1977 等）：**未核**。
- **市场数据**（份额、用户量）：**无权威源** ⇒ 不做量化断言。

> 下一轮把这些逐条补核，能补到同行评审锚点就升格进 §2，补不到就留在本节。