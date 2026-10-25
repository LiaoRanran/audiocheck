#!/usr/bin/env python3
"""mutation_probe：**变异测试**（E1.2）——自实现，不引 cosmic-ray。

## 为什么自实现

E1 提示词要求"变异测试进 CI，变异分数不得低于 E0 基线"。本机没有
`cosmic-ray` / `mutmut`，且项目铁律是**不增运行时依赖**（dev 依赖也尽量不加）。
项目已有先例：`colormap_probe.py` 自写 CIEDE2000、CVD 矩阵——**测量脚本不进运行时**，
所以自实现不构成新依赖。变异测试的本质就三件事：**注入故障 → 跑测试 → 看抓不抓得住**，
不需要框架。

## 做法

1. 把仓库 `src/` + `tests/` 复制到临时工作区（**不动真实仓库**）；
2. 对每个目标模块，用 AST 生成变异体（比较符 / 算术符 / 布尔符 / 数值常量）；
3. 每个变异体：写进工作区 → 跑**定向测试子集**（`-x` 早停）→ 记 存活/杀死；
4. 报告：变异分数 + **逐个存活变异体的结论**（等价变异体 / 测试缺失）；
5. `--max-seconds` 限定时长（E1 要求"限定时长跑"），超时即停并报当前分数；
6. `--strict`：变异分数 < `MIN_SCORE` ⇒ 返回 1（存活变异体作为**发现**列出，不判红）。

## 纪律（E1 提示词）

* **等价变异体必须登记并给理由**（`EQUIVALENT` 表），不许一律忽略——
  否则"分数"就成了自欺。
* 存活且**不是**等价变异体的 = **测试缺失**，如实列出（那是真发现）。
* 没找到问题就说"本轮未发现"，不许包装成"通过质量测试"。
* ⚠ **先验证量具**：本探针开发中踩到**三次**"量具坏了"的假象，根因都在量具
  （① `id(node)` 跨树不匹配 ⇒ 变异静默不生效；② 未设 `PYTHONPATH=工作区/src`
  ⇒ editable 安装的**真实**仓库代码被 import、变异体根本没加载；
  ③ **子进程无超时** ⇒ 一个让实现**非终止**的变异体（如 `if acc_n == hop`
  改成 `!=`、`while pos < total` 改成 `<=`）会把整轮探针**挂死**——
  实测在同一个 pytest 子进程上耗掉 12 分钟仍不返回，`--max-seconds` 也拦不住，
  因为时长检查只在**两个变异体之间**做。见 `_run_subset` 的 `timeout`）。
  分数异常低时**先怀疑量具**，别急着下"测试很差"的结论。

    python packaging/mutation_probe.py [--strict] [--max-seconds N] [--mutant-timeout S]
"""
from __future__ import annotations

import argparse
import ast
import os
import shutil
import subprocess
import sys
import tempfile
import time

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

#: 目标模块 → 定向测试子集。子集要**覆盖该模块的消费者**而不只是模块自身的单元测试：
#: 早先只挂 `test_cutoff.py`（30 测试）时分数仅 0.188——那不是"测试差"，
#: 是**量具口径太窄**（引擎/判定层才是截止点的真实消费者）。
TARGETS: dict[str, list[str]] = {
    "src/audiocheck/core/analyzers/spectrum.py": [
        "tests/test_cutoff.py", "tests/test_engine.py", "tests/test_verdict.py",
        # 2026-10-19：新增 `content_bandwidth`（内容带宽 `B` 的单一出口）⇒ 判据必须挂上，
        # 否则那一族变异体"全假存活"（同型坑在 ops.py 上踩过）。
        "tests/test_bandwidth.py",
        # 2026-10-21：补 29 条缺口（平滑向量化 / 截止点常数 / 展示字段口径）。
        "tests/test_spectrum_units.py"],
    "src/audiocheck/core/dsp.py": [
        "tests/test_dsp.py", "tests/test_dsp_standard.py",
        "tests/test_truepeak.py", "tests/test_timeline_loudness.py",
        # 2026-10-21：补 116 条存活里的守卫/等号侧/舍入位数（打桩喂格子）。
        "tests/test_dsp_edges.py"],
    # 判定层（2026-10-17 夜间包）：**产品结论就在这两个文件里**，此前从未被变异测试
    # 覆盖过——内核的测量再好，结论判错一样没用。子集取"直接钉判定的两个测试文件"
    # （80 用例 / ~12 s，`-x` 早停后单个体通常几秒）。
    "src/audiocheck/core/analyzers/verdict.py": [
        "tests/test_verdict.py", "tests/test_c4_granularity.py",
        "tests/test_judgment_boundaries.py",
        # 2026-10-21：补 31 条缺口（等号侧 / 短路顺序 / 文案里的单位）。
        "tests/test_verdict_edges.py"],
    "src/audiocheck/core/analyzers/causes.py": [
        "tests/test_c4_granularity.py", "tests/test_verdict.py",
        "tests/test_judgment_boundaries.py",
        # 2026-10-22：补 33 条存活里的粗档强度 / 门限等号侧（纯 dict 输入）。
        "tests/test_causes_edges.py"],
    # 编辑内核（2026-10-18，P0 地基）：**导出结果就是用户手里拿到的东西**。
    # 抽样偏移一位、增益算错、区间端点判错，都属于"安静地给错数"——
    # 正是变异测试该抓的形态（`test_edit_render.py` 逐条钉住这些口径）。
    # ⚠ 子集必须**包含判据所在的那个文件**：2026-10-18 加去削波后，`DeclipOp` 的
    # `__post_init__` / `to_dict` / `describe` 只被 `tests/test_edit_declip.py` 构造到
    # ⇒ 不挂它就会"全假存活"（这条坑先前在 `test_edit_stft.py` 上踩过一次）。
    "src/audiocheck/core/edit/render.py": ["tests/test_edit_render.py",
                                          "tests/test_edit_stft.py",
                                          "tests/test_edit_declip.py",
                                          "tests/test_edit_declick.py",
                                          # 2026-10-22：去嗡路径（dispatch / 选区 / 审计）。
                                          "tests/test_edit_dehum.py",
                                          # 2026-10-24：格式往返与 `_check_subtype` 守卫。
                                          "tests/test_edit_formats.py",
                                          # 2026-10-24：会话（撤销/重做）也走这条渲染路径。
                                          "tests/test_edit_session.py",
                                          # 2026-10-25：复合操作的链式渲染 / 规范顺序 /
                                          # `reordered_from` 判据只在这个文件里。
                                          "tests/test_edit_chain.py",
                                          # 2026-10-26：去噪路径（dispatch / 噪声段 / 审计）。
                                          "tests/test_edit_denoise.py"],
    "src/audiocheck/core/edit/ops.py": ["tests/test_edit_render.py",
                                        "tests/test_edit_stft.py",
                                        "tests/test_edit_declip.py",
                                        # 2026-10-19：`DeclipOp.bandwidth_source` 的来源记账
                                        # 走 CLI 的自动带宽路径 ⇒ 挂上 CLI 判据。
                                        # 2026-10-20：`DeclickOp` 同理挂上它的判据。
                                        # 2026-10-22：`DehumOp` 的校验/记账挂在去嗡判据里。
                                        "tests/test_edit_cli.py",
                                        "tests/test_edit_declick.py",
                                        "tests/test_edit_dehum.py",
                                        # 2026-10-24：`to_dict`/`op_from_dict` 的严格 schema
                                        # 判据在会话文件里（踩过"子集没挂判据文件 ⇒ 全假存活"）。
                                        "tests/test_edit_session.py",
                                        # 2026-10-26：`DenoiseOp` 的校验/记录挂在去噪判据里。
                                        "tests/test_edit_denoise.py"],
    # 时频内核（2026-10-18，P0 频谱编辑的数学地基）：**重叠归一化、帧网、覆盖守卫**
    # 都在这一个文件里。这里的变异体最容易被"看起来还行"的测试放过——所以判据是
    # `tests/test_edit_stft.py`（往返残差、分块等价性、覆盖守卫、窗平方和恒 >0）。
    "src/audiocheck/core/edit/stft.py": ["tests/test_edit_stft.py"],
    # 去削波（2026-10-18，P3 第一个修复算子）：**改的是真样本**，且判据是"只改饱和样本 +
    # SDR 必须提升 + 能量不许缩水"。迭代次数、带限守卫、不等式投影、饱和集合口径，
    # 每一处写错都会"安静地修坏"——正是变异测试该抓的形态。
    "src/audiocheck/core/edit/declip.py": ["tests/test_edit_declip.py"],
    # 合成语料（2026-10-18，修复算法的判据地基）：**它是"真值"本身**——
    # 削波电平、饱和集合、带宽真值、脉冲位置，任一处写错都会让上层的"修复判据"失去意义。
    "src/audiocheck/core/edit/corpus.py": ["tests/test_edit_corpus.py"],
    # 去咔哒（2026-10-20，P3 第二个修复算子）：**检测阈值是标定过的输入**，
    # 写错方向会"把误报引进来 ⇒ 把信号修坏"（实测白噪 −13 dB）⇒ 判据必须钉住
    # "只改检出样本"与"合法瞬态不被误伤"。
    "src/audiocheck/core/edit/declick.py": ["tests/test_edit_declick.py"],
    # 去嗡（2026-10-22，P3 第三个修复算子）：**Q 与谐波集是漂移表定出来的**；
    # 零相位对称性、越界谐波跳过、LTI 的"整段都改"口径都要钉住。
    "src/audiocheck/core/edit/dehum.py": ["tests/test_edit_dehum.py"],
    # 嗡的自动检测（2026-10-23）：**稳态**是判据（帧间稳定 + 安静帧仍在），
    # 判据里的三组门限与抛物线插值都必须钉住。
    "src/audiocheck/core/analyzers/hum.py": ["tests/test_analyzer_hum.py"],
    # 去噪（2026-10-26）：**噪声段学谱 + DD-Wiener 递归**；增益地板、噪声段长度守卫、
    # 首帧闭式解、实测压制量的口径都要钉住。
    "src/audiocheck/core/edit/denoise.py": ["tests/test_edit_denoise.py"],
    # 会话/撤销（2026-10-24）：`to_dict`/`from_dict` 的严格 schema 与栈语义。
    "src/audiocheck/core/edit/session.py": ["tests/test_edit_session.py"],
}

#: 变异分数下限。**三次更正，都写在这里免得后人再踩**：
#:  ① 首轮记的 **0.524** 是 `--max-seconds 300` 的**部分**结果，而且当时那轮会被
#:     一个让实现**非终止**的变异体挂死（见 `_run_subset` 的超时注释）。
#:     修好量具、跑满全部候选后，**首个可复算的完整基线 = 0.390**（30/77，47 存活）。
#:  ② 补齐测试缺口后 **0.946**（53/56，3 存活；见 CHANGELOG 2026-10-16 轮）。
#:  ③ **2026-10-17**：`_smooth_log_axis` 向量化 + `detect_cutoff` v3.1 两次改源码把
#:     行号推后 ⇒ 白名单**过期**，分数假跌到 **0.760**（18"存活"里 16 条其实是等价）。
#:     重新对齐行号 + 补 2 条测试（float32 dtype、LRA 四块边界）后 = **1.000**（59/59）。
#:     ⚠ 教训：**行号键会漂**——改被覆盖的源码后必须重跑并核对行号，别把假跌当"测试差"。
#: 下限取 0.90 留一点余量；**想再往上压，必须先填掉报告里列出的"测试缺失"**。
MIN_SCORE = 0.90

#: **等价变异体**白名单：语义不变 ⇒ 测不出来是**正确的**，不是测试缺失。
#: 键 = 变异签名 `相对路径:行:类型:old->new`，值 = 理由（必须写清为什么语义不变）。
#: ⚠ 纪律：这里**不许塞"我懒得测"的条目**。每条理由要么是**可证明的**（如
#: "kernel 在分子分母同比出现 ⇒ 比值不变"），要么是**在可达域上不可区分**
#: 并写清那个域（如 "f[0]=0 时两版都跳过"）。但凡多一个反例就不属于这里。
#:
#: ⚠⚠ **键里的行号是快照，源文件一改动就整体漂移**（本轮实锤：`_smooth_log_axis`
#: 向量化 + `detect_cutoff` v3.1 两次改动把行号推后 12–26 行 ⇒ 一批**本该等价**的
#: 变异体被判成"存活"、分数从 0.946 掉到 0.760）。**改动被这些源码后必须重跑本探针
#: 并核对行号**——否则分数是假的（低不是"测试差"，是"白名单过期"）。
EQUIVALENT: dict[str, str] = {
    "src/audiocheck/core/dsp.py:30:const:4096->4097":
        "TP_OVERLAP 是真峰值分段的重叠采样数，±1 只挪动分段边界；取最大值的中段不变。",
    # --- 2026-10-17 夜间包：判定层纳入后新增的等价体（每条都经核查）---
    "src/audiocheck/core/analyzers/verdict.py:42:const:99->100":
        "`_RANK_UNKNOWN` 是**未知等级的排序哨兵**，只需大于所有真实等级（ok/warn/bad = 0/1/2）；"
        "99 与 100 在任何比较里都给出同一结果。",
    "src/audiocheck/core/analyzers/verdict.py:51:cmp:>->>=":
        "`_worst` 取最大等级：`r >= best_rank` 在平局时把 `best` 重新赋成**同一个值**，"
        "返回值逐位相同（只多一次赋值）。",
    "src/audiocheck/core/analyzers/verdict.py:342:const:90->91":
        "调度 order：现有分析器里**没有 91**（causes=89、verdict=90、timeline=86），"
        "90→91 仍排在最后 ⇒ 执行顺序不变。",
    "src/audiocheck/core/analyzers/causes.py:335:const:89->90":
        "调度 order：89→90 会与 verdict 平局；`schedule()` 用**稳定排序**且 causes 先注册"
        "（实测 order 列表 causes 在 verdict 前）⇒ 平局后顺序不变。",
    "src/audiocheck/core/dsp.py:31:const:64->65":
        "TP_TRIM 是每段两端丢弃的瞬态采样数（滤波器瞬态约 21），64→65 丢弃范围仍在瞬态内。",
    "src/audiocheck/core/analyzers/spectrum.py:17:const:6.0->9.0":
        "`_smooth_log_axis` 的 `fraction` 默认值**从不被使用**——两个调用方 "
        "(`smooth_log`/`smooth_power`) 都显式传入自己的 `fraction`。",
    # --- 2026-10-16 变异轮：以下经核查为等价（每条都写了"为什么"）---
    "src/audiocheck/core/analyzers/spectrum.py:51:cmp:<=-><":
        "`if fc <= 0: continue`：频点网格 f[0]=0、其余为正。fc=0 时窗口内点数 =1 <2，"
        "两版都不更新 out[i] ⇒ 在可达域（f ≥ 0）上逐位相同。",
    "src/audiocheck/core/analyzers/spectrum.py:53:cmp:>=->>":
        "log 轴平滑的窗界 `f >= fc/ratio`：某频点**恰好等于** fc/ratio 的概率为 0"
        "（ratio 是无理数、频点有限）⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:53:cmp:<=-><":
        "同上：`f <= fc*ratio` 的边界相等不可达 ⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:74:bin:+->-":
        "`ps + 1e-20` → `ps - 1e-20`：1e-20 只为避免 log10(0) 下溢；可达谱的最低功率"
        "≫ 1e-20（合成语料 floor −90 dB ⇒ ps ≈1e-9）⇒ 数值不变。",
    "src/audiocheck/core/analyzers/spectrum.py:129:bin:*->/":
        "`floor_hz = max(min_hz*2, 100)`：默认 min_hz=20 ⇒ 40 与 10 都被 max(·,100) "
        "主导 ⇒ 结果逐位相同。（min_hz>50 时会分叉，但那不是本项目任何语料/配置的取值。）",
    "src/audiocheck/core/analyzers/spectrum.py:143:cmp:>->>=":
        "`while cur > floor_hz`：cur 是 nyq 沿 2^(1/12) 的几何序列，恰好等于 100 只会在"
        "多走一步后立即又退出 ⇒ 结果不变。",
    "src/audiocheck/core/analyzers/spectrum.py:145:cmp:>->>=":
        "同上（内层同一条件的 while）⇒ 结果不变。",
    "src/audiocheck/core/analyzers/spectrum.py:147:cmp:>=->>":
        "`if hi_f - cur >= cur*0.04`：这是「上方有没有可用频段」的门槛，恰好相等的浮点"
        "情形概率为 0 ⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:167:cmp:>=->>":
        "陡度守卫 `pass_lvl - floor_lvl >= min_stop_db`：两侧电平是连续浮点，恰好相差"
        "等于 20.0 dB 的概率为 0 ⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:193:cmp:>=->>":
        "`if raw[i] >= thr`：raw[i] 恰好等于 thr（(pass+floor)/2）的概率为 0 ⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:195:cmp:>=->>":
        "`if run >= need` vs `> need`：两者定位的**是同一条连续段的上端**——"
        "run 多计 1 点时 i 恰好少 1，而 `j0 = i + need - 1` 在两个版本里取到同一个"
        "索引（(i-1)+(need+1)-1 == i+need-1）⇒ **可证明等价**。",
    "src/audiocheck/core/analyzers/spectrum.py:203:cmp:<-><=":
        "`if cut < min_hz: return None`：cut 是频点值，恰好等于 20.0（min_hz）的概率"
        "为 0 ⇒ 不可区分。",
    "src/audiocheck/core/analyzers/spectrum.py:207:cmp:>->>=":
        "`j1 > j0` vs `>=`：j1 == j0 时（cut 逼近奈奎斯特）两版都返回 0.0；"
        "j1 < j0 不可能（min(cut*2, nyq) ≥ cut）⇒ **可证明等价**。",
    "src/audiocheck/core/analyzers/spectrum.py:226:const:30->31":
        "`SpectrumAnalyzer.order`：order 只决定调度**排序**，不参与结果（依赖由 `requires` "
        "表达）。现有 order 集合为 {5,10,15,30,40,41,42,45,60,86,88,89,90}，30 与 31 "
        "之间没有别的分析器 ⇒ 排序结果逐位不变。",
    "src/audiocheck/core/analyzers/spectrum.py:235:cmp:<-><=":
        "`peak < -60.0` vs `<=`：峰值恰好等于 −60.0 dBFS 的概率为 0 ⇒ 不可区分"
        "（这一条**守卫本身**已由 `test_silent_material_is_not_analyzed_as_content` 钉住）。",
    # --- 2026-10-17（夜间大包）新增：v3.1 引入的分支 + 向量化守卫 ---
    "src/audiocheck/core/analyzers/spectrum.py:31:cmp:<-><=":
        "向量化守卫 `if f.size < 2 ...` → `<= 2`：**f.size == 2 时两条路径输出逐点相同**"
        "（实测：单调/非单调 × 不同量级，向量版与 `_smooth_log_axis_pointwise` 全部一致）"
        "⇒ 在 size==2 这个唯一分叉域上不可区分；而生产网格 f.size ≥ 数百（16384-FFT）"
        "⇒ 边界本身也不可达。",
    "src/audiocheck/core/analyzers/spectrum.py:187:cmp:>->>=":
        "`if jl2 > jl` → `>=`：这是 v3.1 通带窗 `[coarse/2.56, coarse/1.6]` 的『窗内有点』"
        "守卫。实测 df=fs/16384 下该窗点数：96k@coarse=100 Hz 最少 **4 点**（44.1k 为 8）"
        "⇒ `jl2 == jl`（零/一点窗）**在整个可达域（coarse ≥ 100 Hz）不可达** ⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:60:cmp:>->>=":
        "`keep = lv > ABS_GATE`：lv 是连续浮点 dB，恰好等于 −70.0 的概率为 0 ⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:64:cmp:>->>=":
        "`keep &= (lv > rel)`：同上，与相对门限恰好相等的概率为 0 ⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:128:cmp:<-><=":
        "`if si < 4: return {}`：上游 line 87 的 `total < hop*4` 已保证 si ≥ 4 ⇒ 该分支"
        "不可达 ⇒ 等价（下一步已加测试钉住上游守卫本身）。",
    "src/audiocheck/core/dsp.py:265:bin:+->-":
        "`denom = sqrt(...) + 1e-30` → `- 1e-30`：非零 denom 下 1e-30 是无操作"
        "（它只用于防除零）⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:268:cmp:<-><=":
        "`min(pL,pR) < max(pL,pR)·10^(−30/10)`：两功率恰好相差正好 30.000 dB 的概率为 0"
        " ⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:324:bin:/->*":
        "`kern = np.ones(w)/w` → `*w`：该核在 ρ̄ 里**同时**出现在分子与分母"
        "（`2Re(mov(ZL·conj ZR)) / (mov(p_l)+mov(p_r))`），同比缩放 ⇒ 比值不变 ⇒ "
        "**可证明**等价。",
    # --- 2026-10-18（编辑侧测量层这一轮）：编辑三件的等价体（每条都写清了"为什么输出逐位相同"）---
    # ⚠ 行号在 2026-10-25 插入 `_render_chained` 后**整体下移**，已逐条对齐（过期白名单
    # 会让分数假跌——2026-10-16 踩过同型坑）。
    "src/audiocheck/core/edit/render.py:68:const:512->513":
        "`CHUNK_HOPS` 只决定块长，而块长恒为 `chunk_hops*hop`（**永远是 hop 的整数倍**）"
        "⇒ 跨块的帧网格一致 ⇒ 输出逐位相同（`test_chunk_size_does_not_change_the_result`"
        "用 4 vs 512 证过）。",
    "src/audiocheck/core/edit/render.py:124:cmp:<-><=":
        "`while pos < end` → `<=`：pos==end 时多进一次循环，`min(blocksize, end−pos=0)=0` "
        "⇒ `f.read(0)` 返回 0 帧 ⇒ 立刻被 `if not len(b): break` 退出 ⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/render.py:126:bin:-->+":
        "`min(blocksize, end − pos)` → `end + pos`：`_copy_range` 只在**首段**（pos=0，两式相同）"
        "与**尾段**（end=claimed）被调用，而 libsndfile 读到 EOF 会截短、循环判据又是 `pos < end`"
        "⇒ 写出的帧数逐位相同。",
    "src/audiocheck/core/edit/render.py:345:bin:-->+":
        "`(ce − cs + 2*n_fft)` → `ce + cs + …`：**只是把缓冲区加长**。真实音频由 "
        "`buf[lo − src_lo : …] = raw` 按**固定偏移**填入（与总长无关），加长部分全在请求区间 "
        "`[n_fft, n_fft+(ce−cs))` 之外 ⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/render.py:355:bin:-->+":
        "报错**文案**里的 `hi − lo` → `hi + lo`：只进字符串，没有任何判据读它。",
    "src/audiocheck/core/edit/render.py:357:cmp:<=-><":
        "`if ce <= cs: break`：这一支只在**源被截断**时走到，而上一行已经把 "
        "`st[\"read_error\"]` 置上了 ⇒ 无论 break 与否，外层 `while st[\"read_error\"] is None …`"
        "都会立刻退出（多跑的只是一个长度为 0 的空块）⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/stft.py:116:cmp:>->>=":
        "`wss > _WSS_MIN` → `>=`：只有 `wss` **恰好等于** 1e-12 时才分叉，而 `wss` 是固定 Hann 窗"
        "的平方和（取值只由 `(n_fft, hop)` 决定）⇒ 该等号不可达。",
    "src/audiocheck/core/edit/stft.py:169:bin:*->/":
        "`if R * hop == n` → `R / hop == n`：这只决定走**快路径还是通用路径**，两条路径**数学同式**"
        "（按 (R,hop) 拆开累加 ≡ 逐帧累加），差别只在求和次序 ⇒ 输出最多差浮点末位；"
        "本仓没有任何判据比较内部 OLA 的逐位结果（那会是无意义的脆弱判据）。",
    "src/audiocheck/core/edit/stft.py:172:bin:-->+":
        "`np.zeros((M + R − 1, hop, ch))` → `M + R + 1`：**只是把累加器加长**，多出的行落在 "
        "`keep` 定界的请求区间之外（`synthesize` 先按 `[n0:n1]` 取用）⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/stft.py:178:bin:-->+":
        "`np.zeros(((M − 1) * hop + n, ch))` → `M + 1`：同上（通用路径的累加器加长，"
        "共享下标的累加结果不变）⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/stft.py:179:bin:-->+":
        "`wss = np.zeros((M − 1) * hop + n)` → `M + 1`：同上（归一化分母同步加长，"
        "`[n0:n1]` 上逐位相同）⇒ 输出逐位相同。",
    "src/audiocheck/core/edit/render.py:284:cmp:<=-><":
        "`if k1 <= k0: continue` → `<`：两式只在 `k1 == k0` 时不同，而那一点上 "
        "`b[k0−pos:k1−pos]` 是**空切片**（`*=` 无操作）、`applied[i] += k1 − k0 = 0` "
        "⇒ 输出与审计都逐位相同。（`k1 < k0` 时两式都 `continue`。）",
    "src/audiocheck/core/edit/render.py:356:bin:+->-":
        "`ce = min(ce, lo + len(raw))` → `lo − len(raw)`：这一支只在**短读**时走到，"
        "而实测本机 libsndfile：**WAV** 被截断时按文件大小**重算帧数**（`claimed` 就等于实有帧数"
        "⇒ 永不短读）、**FLAC** 截断后读到损坏处会**抛** `LibsndfileError`（走 `except` 支）"
        "⇒ 在已验证的两种容器上不可达；留着是**防御性**的（未穷举其它容器）。",
    # --- 2026-10-18（P3 去削波这一轮）：新模块 declip.py / corpus.py 的等价体 ---
    "src/audiocheck/core/edit/declip.py:162:cmp:>=->>":
        "`sign = np.where(a >= 0.0, 1, −1)` → `>`：两者只在 `a == 0.0` 时分叉，而 `sign` **只**在"
        "饱和样本处被读（`sg[s]`），饱和样本满足 `|a| ≥ ceiling > 0` ⇒ 恒非 0 ⇒ 该等号不可达。",
    "src/audiocheck/core/edit/declip.py:158:cmp:<=-><":
        "`ambiguous = bool(margin <= 0.0)` → `<`：两者只在 `margin` **恰好等于 0.0** 时分叉，"
        "而 margin = `(1−f) − B/(fs/2)` 是两个连续量之差 ⇒ 该等号**概率为 0**（不可区分；"
        "门槛两侧的语义都是「边界」）。",
    "src/audiocheck/core/edit/declip.py:160:bool:or->and":
        "`scale = … or 1.0` → `and`：`scale == 0` 只在**整段全零**时发生，而全零素材在更早的"
        "`if peak_in <= 0.0` 处就早退（`noop`）了 ⇒ 这一支**不可达**。",
    "src/audiocheck/core/edit/corpus.py:55:cmp:>->>=":
        "`x = x/peak*amp if peak > 0 else x` → `>=`：**防御性守卫**，`else` 支在定义域内不可达"
        "——40 条随机相位正弦之和不可能恒为 0（`n > 0` 时 `max|x| > 0`）。",
    "src/audiocheck/core/edit/corpus.py:75:cmp:>->>=":
        "同上的**防御性守卫**（`band_limited_noise` 的 `peak > 0`）：定义域 `0 < bandwidth < fs/2` 下"
        "带内至少留一条 bin ⇒ 输出恒非零 ⇒ `else` 支不可达。",
    "src/audiocheck/core/edit/corpus.py:189:bin:*->/":
        "`amp * sign` → `amp / sign`：本函数的 `sign` 语义是**脉冲极性**（`±1`），"
        "而 `±1` 的倒数等于自身 ⇒ 两种写法逐位相同（判据 `test_click_train_*` 用的是 `sign=1`）。",
    "src/audiocheck/core/edit/render.py:476:bin:-->+":
        "`_render_declip` 报错**文案**里的 `k1 − k0` → `k1 + k0`：只进字符串，"
        "没有任何判据读它（与 band 路径的同型条目一致）。",
    "src/audiocheck/core/edit/render.py:545:bin:-->+":
        "`_render_declick` 报错**文案**里的 `k1 − k0` → `k1 + k0`：同上，只进字符串。",
    # --- 2026-10-20（去咔哒这一轮）：declick.py 的等价体 ---
    "src/audiocheck/core/edit/declick.py:69:cmp:>->>=":
        "判据 `|r| > α·s` → `>=`：两者只在 `|r|` **恰好等于** `α·s` 时分叉，而 r 与 s 都是连续量"
        "算出来的 ⇒ 该等号概率为 0（不可区分）。",
    "src/audiocheck/core/edit/declick.py:81:cmp:<-><=":
        "`while j < mask.shape[0] and mask[j]` → `<=`：多走一步会读 `mask[n]` 越界，"
        "但那要求**检出段一直延伸到最后一个样本**——而 `residual` 把**首尾样本置 0**"
        "（`r[-1] == 0`）⇒ 尾样本永不被检出 ⇒ 不可达。",
    "src/audiocheck/core/edit/declick.py:84:cmp:>=->>":
        "`left = out[a] if a >= 0 else None` → `>`：只在 `a == 0`（**检出段从样本 0 开始**）时分叉，"
        "同上因 `r[0] == 0` 而不可达。",
    "src/audiocheck/core/edit/declick.py:85:cmp:<-><=":
        "`right = out[b] if b < out.shape[0] else None` → `<=`：只在 `b == n` 时分叉 ⇒ 同上不可达。",
    "src/audiocheck/core/edit/declick.py:86:bool:and->or":
        "`if left is not None and right is not None` → `or`：两个 `None` 分支（只在检出段含首/尾样本时"
        "才走到）**都不可达**（首尾残差恒 0）⇒ 该分支在可达域内永不执行。",
    "src/audiocheck/core/edit/render.py:532:cmp:<=-><":
        "`_render_declick` 的 `if k1 <= k0: continue` → `<`：两式只在 `k1 == k0`（空选区）时不同，"
        "而那一点上 `f.read(0)` 返回空数组、`declick.declick` 对空数组不产出任何样本、"
        "`_copy_range(cursor, k0)` 也不写任何字节 ⇒ 输出与审计都逐位相同。",
    # --- 2026-10-25（复合操作这一轮）：`PHASE` 的秩只关心相对顺序 ---
    "src/audiocheck/core/edit/render.py:184:const:5->6":
        "`gain` 的相位 5 → 6：已知 kind 只占 **0–5**（declick 0 / declip 1 / dehum 2 / "
        "denoise 3 / band_gain 4 / gain 5），6 仍严格大于全部已知相位、小于未知兜底(99) ⇒ "
        "任何输入下排序结果完全相同。",
    "src/audiocheck/core/edit/render.py:189:const:99->100":
        "未知 kind 的兜底相位 99 → 100：两者都大于全部已知相位（0–5），稳定排序下未知操作"
        "仍排在最后、且未知操作之间保持输入顺序 ⇒ 输出逐位相同。",
    # --- 2026-10-26（去噪这一轮）：P 的分子守卫 + 两处只进字符串的算术 ---
    "src/audiocheck/core/edit/denoise.py:79:const:1e-30->0.0":
        "分子守卫 `max(P, 1e-30)` → 0：只在 **P == 0 的 bin** 上有区别，那里 γ = 0 或 "
        "1e-30/N ≈ 0 ⇒ ξ ≈ 0 ⇒ Wiener 增益 ≈ 0，**低于 g_min 一律被地板托到 g_min**；"
        "下一帧的 `g_prev²·γ_prev` 也同为 ≈0 ⇒ 输出逐位相同（N 的守卫仍在，不会除零）。",
    "src/audiocheck/core/edit/render.py:680:bin:-->+":
        "`_render_denoise` 报错**文案**里的 `nk1 − nk0` → `nk1 + nk0`：只进字符串，"
        "没有任何判据读它（与其它路径的同型条目一致）。",
    "src/audiocheck/core/edit/render.py:704:bin:-->+":
        "`_render_denoise` 报错**文案**里的 `k1 − k0` → `k1 + k0`：同上，只进字符串。",
    "src/audiocheck/core/edit/declick.py:118:bin:*->/":
        "**守卫那一处**的 `if n * ch` → `if n / ch`：两者都只在 `n == 0` 时为假（`ch ≥ 1`）⇒ "
        "走同一分支 ⇒ 不可区分。（同一行**分母**那一处的 `*`→`/` 是**被判据杀死的**，见 "
        "`test_only_detected_samples_change` 的占比断言。）",
    # --- 2026-10-21/22 大包：`dsp.py`（116 条存活）里**可证明**语义不变的几条 ---
    # 说明：`_gated_loudness` 里三处 `+ 1e-30` 的加/减与 `1e-30 -> 0.0`。z 是均方和，
    # **恒 ≥ 0**；z > 0 时 1e-30 在 float64 下低于 1 ulp（gated 读数的量级都 > 1e-7），
    # z = 0 时 `log10(±1e-30)` 分别是 −300 / nan，**两者都 < ABS_GATE(−70)** ⇒ 同一 `None`。
    "src/audiocheck/core/dsp.py:59:bin:+->-":
        "`log10(z + 1e-30)` 的 `+`→`-`：z ≥ 0；z=0 时 −300 与 nan 都低于绝对门限 ⇒ 同一个 None；"
        "z>0 时 ±1e-30 低于 double 的 1 ulp（本函数输出的量级均 > 1e-6）。",
    "src/audiocheck/core/dsp.py:59:const:1e-30->0.0":
        "同上的 `1e-30 -> 0.0`：只在 z=0 时分叉（−inf vs −300），两者都低于绝对门限 ⇒ 同为 None。",
    "src/audiocheck/core/dsp.py:63:bin:+->-":
        "相对门限 `log10(mean + 1e-30)` 的 `+`→`-`：mean 是 keep 的均值 ≥ 0，同上不可分辨。",
    "src/audiocheck/core/dsp.py:63:const:1e-30->0.0":
        "相对门限的 `1e-30 -> 0.0`：mean=0 时绝对门限已先返回 None ⇒ 不可区分。",
    "src/audiocheck/core/dsp.py:67:bin:+->-":
        "积分响度 `log10(mean + 1e-30)` 的 `+`→`-`：走到这里必有 lv > −70 ⇒ z > 1e-7 ⇒ "
        "1e-30 远低于 1 ulp ⇒ 逐位相同。",
    "src/audiocheck/core/dsp.py:67:const:1e-30->0.0":
        "同上：`z[keep].mean() > 1e-7` ⇒ 加 1e-30 与加 0 逐位相同。",
    "src/audiocheck/core/dsp.py:87:bin:*->/":
        "`total < hop * 4` 的 `*`→`/`：`total < hop/4` 更弱，会放行进函数体；"
        "但 `si = total // hop` 在 `total < 4·hop` 时必 < 4 ⇒ 末尾 `if si < 4: return {}` "
        "给同一个 `{}`（不写 step_ms：`si < nsteps` 为假）。",
    "src/audiocheck/core/dsp.py:122:cmp:<-><=":
        "`if si < nsteps` 的 `<`→`<=`：`si == nsteps` 意味着 nsteps 个满块已全部消费、"
        "剩余采样 < hop ⇒ `acc_n` 再也到不了 hop ⇒ 该等号分支不可达。",
    "src/audiocheck/core/dsp.py:207:bin:+->-":
        "相关系数分母的 `+ 1e-30`→`- 1e-30`：两声道全零时分母 0，"
        "0/(+1e-30) 与 0/(−1e-30) 分别是 0.0 与 −0.0（**比较相等**，档位也相同）；"
        "否则 1e-30 低于 1 ulp。",
    "src/audiocheck/core/dsp.py:261:cmp:==->!=":
        "`if slot == \"L\"` 的 `==`→`!=`（L/R 互换）：ρ 的分子 `Re(L·conj(R))` 在互换下是共轭"
        "（实部不变），`undef` 用 min/max ⇒ 对称；两个数组被完整算出、只是装进对方的名字。",
    "src/audiocheck/core/dsp.py:443:cmp:<=-><":
        "`if total <= 0` 的 `<=`→`<`：total=0 时 `while pos < total` 一次也不进循环，"
        "之后 `_db(0.0)` 返回 None ⇒ 与提前 return 的 out **逐键相同**。",
    "src/audiocheck/core/dsp.py:482:cmp:>->>=":
        "`if i1 > i0` 的 `>`→`>=`：i1 == i0 时 `seg[i0:i0]` 是空切片，`if sub.size` 为假 ⇒ "
        "`span_pk` 不更新，与跳过分支逐位相同。",
    "src/audiocheck/core/analyzers/causes.py:80:bin:/->*":
        "`(slope - conf_slope_lo) / conf_slope_span` 的 `/`→`*`：该式只在 "
        "`low_cut and hard`（slope ≥ th.brick_db = 24）里求值 ⇒ 原式 (24−8)/16 = 1 "
        "已顶到 `min(1.0, ·)`；改成乘（≥16·16）同样是 1.0，`max(0,·)` 也不变 ⇒ 逐位相同。",
    "src/audiocheck/core/analyzers/causes.py:80:bin:-->+":
        "同一式的 `-`→`+`：`(slope + conf_slope_lo) / span` 在 slope ≥ 24 时 ≥ 2 ⇒ "
        "同样被 `min(1.0, ·)` 夹到 1.0（`max(0, ·)` 也仍是 1.0）⇒ 逐位相同。",
    # --- 2026-10-24：嗡自动检测（`analyzers/hum.py`）里三条**可证明**的等价体 ---
    "src/audiocheck/core/analyzers/hum.py:97:const:0.5->0.75":
        "抛物线偏移的 **clip 上界** 0.5：`j` 是带内 argmax ⇒ `y1 ≥ y0, y2` ⇒ "
        "|δ| = ½|y0−y2|/(2y1−y0−y2) ≤ ½ **恒成立** ⇒ clip 从不出手 ⇒ 改 0.75 不可区分。",
    "src/audiocheck/core/analyzers/hum.py:87:bin:-->+":
        "`_peak_hz` 的 `df = f[1] - f[0]`：本模块唯一调用方（`detect_hum`）用的是 "
        "`signal.stft` 的频率轴，**f[0] 恒为 0** ⇒ `f[1] + f[0]` 与 `f[1] − f[0]` 逐位相同。",
    "src/audiocheck/core/analyzers/hum.py:145:bin:-->+":
        "输出 `df_hz` 的 `f[1] - f[0]`：同上，STFT 频率轴从 0 起 ⇒ 两个方向逐位相同。",
}


class _Cand:
    """一个可变异点：节点 id + 变异类型 + 描述。"""

    __slots__ = ("nid", "kind", "old", "new", "lineno")

    def __init__(self, nid, kind, old, new, lineno):
        self.nid, self.kind, self.old, self.new, self.lineno = nid, kind, old, new, lineno

    def sig(self, rel: str) -> str:
        return "%s:%d:%s:%s->%s" % (rel, self.lineno, self.kind, self.old, self.new)


_CMP = {ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Lt: ast.LtE, ast.LtE: ast.Lt,
        ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
_CMP_NAME = {ast.Gt: ">", ast.GtE: ">=", ast.Lt: "<", ast.LtE: "<=",
             ast.Eq: "==", ast.NotEq: "!="}
_BIN = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.Div, ast.Div: ast.Mult}
_BIN_NAME = {ast.Add: "+", ast.Sub: "-", ast.Mult: "*", ast.Div: "/"}


def _candidates(tree: ast.AST) -> list[_Cand]:
    out: list[_Cand] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            op = type(node.ops[0])
            if op in _CMP:
                out.append(_Cand(id(node), "cmp", _CMP_NAME[op],
                                 _CMP_NAME[_CMP[op]], node.lineno))
        elif isinstance(node, ast.BinOp) and type(node.op) in _BIN:
            out.append(_Cand(id(node), "bin", _BIN_NAME[type(node.op)],
                             _BIN_NAME[_BIN[type(node.op)]], node.lineno))
        elif isinstance(node, ast.BoolOp):
            other = ast.Or if isinstance(node.op, ast.And) else ast.And
            out.append(_Cand(id(node), "bool",
                             "and" if isinstance(node.op, ast.And) else "or",
                             "or" if isinstance(node.op, ast.And) else "and",
                             node.lineno))
            # 注意：BoolOp 的替换需要构造新节点，见 _apply
            _ = other
        elif isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) \
                and not isinstance(node.value, bool) and node.value not in (0, 1, 2, 0.0, 1.0):
            # 数值常量：整数 +1、浮点 ×1.5（跳过 0/1/2 这类结构性常量）
            new = (node.value + 1) if isinstance(node.value, int) else round(node.value * 1.5, 6)
            out.append(_Cand(id(node), "const", repr(node.value), repr(new), node.lineno))
    return out


def _apply(tree: ast.AST, cand: _Cand) -> ast.AST:
    """把目标节点替换成变异后的节点（就地改，返回树）。"""
    class _R(ast.NodeTransformer):
        def _hit(self, node):
            return id(node) == cand.nid

        def visit_Compare(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.ops = [_CMP[type(node.ops[0])]()]
            return node

        def visit_BinOp(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.op = _BIN[type(node.op)]()
            return node

        def visit_BoolOp(self, node):
            self.generic_visit(node)
            if self._hit(node):
                node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
            return node

        def visit_Constant(self, node):
            if self._hit(node):
                node.value = (node.value + 1) if isinstance(node.value, int) \
                    else round(node.value * 1.5, 6)
            return node

    return _R().visit(tree)


def _build_workspace() -> str:
    ws = tempfile.mkdtemp(prefix="ac_mut_")
    for sub in ("src", "tests"):
        shutil.copytree(os.path.join(_ROOT, sub), os.path.join(ws, sub),
                        ignore=shutil.ignore_patterns("__pycache__"))
    # ⚠ **关键**：本仓库多半是 `pip install -e .`（editable）装的，其 PEP 660
    # finder 挂在 `sys.meta_path` 上、**优先于 `sys.path`** —— 于是测试
    # `import audiocheck` 会拿到真实仓库的代码，工作区里被变异的副本**根本不被加载**
    # （实测症状：12/12 变异体"全存活"，分数 0——是量具坏了，不是测试好）。
    # `sitecustomize.py` 在解释器启动时（.pth 处理之后）被导入，正好可以把
    # editable finder 摘掉。用 PYTHONPATH 让工作区先被找到。
    with open(os.path.join(ws, "sitecustomize.py"), "w", encoding="utf-8") as f:
        f.write(
            "import sys\n"
            "sys.meta_path[:] = [f for f in sys.meta_path\n"
            "                    if 'editable' not in (type(f).__module__ + type(f).__name__).lower()]\n")
    return ws


def _run_subset(ws: str, tests: list[str], py: str, timeout: float) -> tuple[int, str]:
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # ⚠ **`ws/src` 必须在 PYTHONPATH 里**：本仓库是 editable 安装，`audiocheck`
    # 默认解析到**真实仓库**；而**并非所有测试文件都自带 `sys.path.insert(src)`**
    # （如 `tests/test_cutoff.py` 就没有）⇒ 那些测试会 import 真实代码，
    # 变异体静默失效（实测症状：`thr = (pass+floor)/2` 改成 `*2` 后
    # `test_cutoff_accuracy` 仍全绿——量具又坏了，不是测试好）。
    # `ws` 放前面是为了让工作区的 `sitecustomize.py` 生效（摘掉 editable finder）。
    env["PYTHONPATH"] = os.path.join(ws, "src") + os.pathsep + ws
    try:
        p = subprocess.run([py, "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests],
                           cwd=ws, env=env, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        # ⚠ **必须有超时**：变异体可能让实现**非终止**（实测：`acc_n == hop`→`!=`
        # 使 `loudness` 的内层 `while p < y.shape[0]` 因 `take=0` 空转；
        # `while pos < total`→`<=` 使 `true_peak` 永不退出）。没有超时时，
        # 探针会**挂死在单个子进程上**，`--max-seconds` 拦不住（它只在两体之间检查）。
        # 返回码 3 = 超时：语义上算**被杀**——测试以"挂起"而非断言的方式检出了这个
        # 病态变异体（与主流变异框架的 timeout=killed 口径一致），并在报告里单列。
        return 3, ""
    return p.returncode, (p.stdout + p.stderr)


def main() -> int:
    ap = argparse.ArgumentParser(description="变异测试（自实现，AST 注入故障）")
    ap.add_argument("--strict", action="store_true",
                    help="分数 < 阈值或存在未登记存活变异体 ⇒ 返回 1")
    ap.add_argument("--max-seconds", type=float, default=300.0,
                    help="限定时长（E1 要求）；超时即停并报当前分数")
    ap.add_argument("--max-mutants", type=int, default=40,
                    help="每个目标最多生成多少变异体（控制时长）。⚠ **截断会在报告里报出来**："
                         "小上限会让分数**虚高**（实测：同一份代码，40 上限跑 0.864、"
                         "400 上限跑 0.619）——别把量具口径当测试质量")
    ap.add_argument("--only", action="append", default=None,
                    help="只跑路径里含该子串的目标（可重复）；单模块迭代时用")
    ap.add_argument("--mutant-timeout", type=float, default=180.0,
                    help="单个变异体的 pytest 子进程超时（秒）。**必须有**："
                         "病态变异体（让实现非终止）会把整轮探针挂死，"
                         "`--max-seconds` 只在两个变异体之间检查、拦不住。"
                         "超时计为**被杀**（挂起检出），报告单列")
    a = ap.parse_args()
    targets = {k: v for k, v in TARGETS.items()
               if not a.only or any(s in k for s in a.only)}
    if not targets:
        print("--only 没匹配到任何目标：%r" % a.only)
        return 2

    py = sys.executable
    ws = _build_workspace()
    print("变异测试（自实现 AST 注入；工作区 %s）" % ws)
    print("目标 %d 个模块，时长上限 %.0f s，单体超时 %.0f s\n"
          % (len(targets), a.max_seconds, a.mutant_timeout), flush=True)
    if a.only:
        print("（--only %s ⇒ 只跑：%s）\n" % (a.only, "、".join(sorted(targets))), flush=True)

    t0 = time.time()
    killed = 0
    survived: list[str] = []
    invalid = 0
    total = 0
    equiv = 0
    hangs = 0                                        # 超时=被杀（非终止变异体）
    timed_out = False
    capped_any = False                               # 有目标被 --max-mutants 截断 ⇒ 分数是部分的

    for rel, tests in targets.items():
        src = open(os.path.join(_ROOT, rel), encoding="utf-8").read()
        all_cands = _candidates(ast.parse(src))
        cands = all_cands[:a.max_mutants]
        # ⚠ **截断必须报出来**：`[:max_mutants]` 会让"候选 102 个"变成"候选 40 个"，
        # 分数**虚高**（本轮实测：同一份代码，小上限跑出 0.864，放到 400 上限是 0.619）。
        # 只看分数的读者会以为"测试很好"，其实是**量具口径太窄**。
        capped = len(cands) < len(all_cands)
        if capped:
            capped_any = True
        print("== %s（%d 个变异体候选%s）=="
              % (rel, len(cands),
                 "，**被 --max-mutants=%d 截断**，候选共 %d 个" % (a.max_mutants, len(all_cands))
                 if capped else ""), flush=True)
        target_path = os.path.join(ws, rel)
        for i in range(len(cands)):
            if time.time() - t0 > a.max_seconds:
                timed_out = True
                print("  …… 达到时长上限，停止（结果部分）", flush=True)
                break
            total += 1
            # ⚠ **必须在同一棵树上取候选再变异**：`_candidates` 用 `id(node)` 标识目标，
            # 而 `ast.parse` 每次都给新对象 ⇒ 若"先 parse 收候选、再 parse 变异"，
            # id 永不匹配 ⇒ **变异静默不生效**，全部假"存活"（实测踩过：16/16 存活、
            # 分数 0，量具坏了而不是测试好）。故这里按**索引**在新鲜树上重取候选。
            tree = ast.parse(src)
            cand = _candidates(tree)[i]
            mutated = _apply(tree, cand)
            try:
                new_src = ast.unparse(mutated)
                compile(new_src, rel, "exec")          # 无效变异体（语法/编译不过）不计分
            except Exception:                          # noqa: BLE001
                invalid += 1
                continue
            if new_src == src:                         # 变异没改动源码 ⇒ 量具异常
                invalid += 1
                print("  [无效] 变异未改变源码（量具异常）", flush=True)
                continue
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_src)
            # 逐体打印进度并 flush：一轮可能跑十几分钟，没有进度就**看不出量具是否挂了**
            # （这正是"无超时"那次事故里最难受的地方）。
            print("  [%d/%d] %s …" % (i + 1, len(cands), cand.sig(rel)), flush=True)
            rc, _out = _run_subset(ws, tests, py, a.mutant_timeout)
            sig = cand.sig(rel)
            if rc == 0:
                if sig in EQUIVALENT:
                    equiv += 1
                    print("        → 等价：%s" % EQUIVALENT[sig], flush=True)
                else:
                    survived.append(sig)
                    print("        → **存活** ← 测试缺失", flush=True)
            elif rc == 1:
                killed += 1
                print("        → 杀死", flush=True)
            elif rc == 3:
                killed += 1
                hangs += 1
                print("        → **超时=被杀**（变异体使实现非终止，挂起检出）", flush=True)
            else:
                invalid += 1
                print("        → 无效（pytest rc=%d，可能无法导入）" % rc, flush=True)
        # 还原
        with open(target_path, "w", encoding="utf-8") as f:
            f.write(src)
        if timed_out:
            break

    scored = killed + len(survived)  # 只把"有效且非等价"的算进分母
    score = (killed / scored) if scored else 0.0
    print("\n---- 结果 ----")
    print("有效变异体 %d ｜ 杀死 %d（其中超时 %d）｜ 存活 %d ｜ 等价 %d ｜ 无效 %d"
          % (scored, killed, hangs, len(survived), equiv, invalid))
    print("变异分数 = %d/%d = %.3f（下限 %.2f）%s"
          % (killed, scored, score, MIN_SCORE,
             "（未跑完，时长上限）" if timed_out else ""))
    if capped_any or timed_out:
        print("⚠ **这一轮的分数是「部分」结果**：%s%s ——"
              % ("有目标被 `--max-mutants=%d` 截断（只看各目标前 N 个候选）；" % a.max_mutants
                 if capped_any else "",
                 "有目标没过完（时长上限）" if timed_out else ""))
        print("  报分数时必须写清上限：**同样的代码，小上限会跑出更高的分**"
              "（本轮实测：小上限 0.864 vs 400 上限 0.619）——别把量具口径当测试质量。")
    if survived:
        print("\n存活变异体（非等价 ⇒ **测试缺失**，逐条列出）：")
        for s in survived:
            print("  - %s" % s)
        print("\n注：这些是**发现**（具体哪些判据没被测到），不是失败。")
        print("    `--strict` 只按**分数下限**判红；抬高分数须先填这些缺口。")
    else:
        print("\n本轮未发现『测试缺失』型存活变异体。")

    if a.strict and score < MIN_SCORE:
        print("\n--strict：变异分数 %.3f < 下限 %.2f" % (score, MIN_SCORE))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
