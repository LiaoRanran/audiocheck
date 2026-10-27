# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [1.1.0] — 2026-10-27

> **主题：从"音频体检"到"可审计的专业修复工作站"。**
> 新增非破坏编辑内核（P0–P4 全线）、五修复算子、REAPER 式工作站、多 Agent 科研体系。
> 全部 1383 项测试绿；编辑内核 10 靶满上限变异 9 靶 1.000、最低 0.924。

### 本版新增（摘要）

- **编辑内核**：`Project(源, 操作栈)` 非破坏纯函数渲染；schema **v3** + 三步显式迁移链；
  撤销/重做；前缀缓存（混域栈）；**audit_digest / kernel_digest / evidence_levels**
  （跨机可核对 + 实现自证 + 每个数字标可信度）。
- **五修复算子**：去削波（POCS 128 次，欠定性判据）、去咔哒（**双臂级联**：两点残差
  + 长窗中值台阶臂，多平台 w=3 **+24 dB**）、去嗡（零相位陷波 Q=10，21 格抖动实测）、
  去噪（Wiener-DD + **逐帧噪声底跟踪**，台阶床 +2.74→**+9.40 dB**）、decrackle
  （实测判"不落地"：就是更松的 DeclickOp）。
- **时频编辑**：矩形 + **多边形（套索）**增益；ROI 快速重绘 **27.9×**（133→5ms）。
- **工作流**：批处理（逐项隔离 + per-job 审计/报告）、A/B、项目记录、**修复链预设**、
  审计 Markdown 报告、CLI `--chain/--report/--poly`。
- **产品化**：脚本化入口（示例即判据）、i18n 第一步（tr() + lupdate 基线）、
  PyInstaller spec（UPX 关 + 编辑模块齐全）、`docs/extending_edit.md`（9 文件触点清单）。
- **质量体系**：1383 测试、41 探针、property-based、自研变异测试（白名单自动巡检）、
  文档数字对账门禁、a11y 自动化（WCAG + 焦点链）、三类 flake 根因清零。

### 本版调研结论（全部有实测出处）

核心算法在自有语料上**连续胜出**：POCS 早停（128 次仍涨 14.2 dB）、IMCRA 门控中位数、
Gerkmann–Hendriks 软 SPP、一致性交叉淡化（+0.00）、稀疏/ADMM 去削波（密集场景更差）、
神经修复路线（破坏确定性与可审计）——**六项否决、零引入**，详见
`docs/editor_research.md §17–§20`。

---

## [1.1.1] — 2026-10-27

### 改进（**同域多算子：重叠不再拒绝，改为顺序串联**）

- **★ 同域重叠**此前直接拒绝（"先改哪段没有定义"）——**现在有定义了：操作栈的顺序就是
  语义**，后一个算子作用在前一个的结果上（与混域链式同一条原则）。实现走 `render()`
  分派：同域多算子且选区重叠 ⇒ 转 `_render_chained`（不重叠仍走单趟，性能不变）。
- 判据 5 条（重叠 == 逐个串行渲染的产物，1-ULP float32 容差；不重叠仍单趟）；
  4 条"重叠被拒绝"的旧判据按新语义改写。
- 顺带清掉 4 处**已不可达**的重叠守卫（改为内部不变量断言）与过时 docstring 注释
  ——代码不再自相矛盾。
- 白名单巡检：103 条全部对齐（分派逻辑新增导致 render.py 行号漂移，逐条重定位）。

---

## [1.1.2] — 2026-10-27

### 新增（**套索微调与顶点级编辑**）

- **套索键盘微调**：方向键平移、Shift+方向键缩放、Alt+↑/↓ 频率平移，夹紧到文件范围
  （画完不必重画）；步长口径与矩形选区一致（视野 2%）。
- **★ Inspector 顶点级编辑（套索能力上限）**：顶点表加选中标记 ▶，↑/↓ 选顶点后
  ←/→、Shift、Alt 微调**该顶点**；经 `session.replace_at` 落回栈（构造新 op
  ⇒ 非破坏语义不变），并触发重渲染。
- **`EditSession.replace_at(idx, op)`**（新增内核 API）：就地替换算子，保持栈位置、
  不进 undo/redo（"同一作品状态的精修"而非新编辑）；越界返回 `False`。
- 判据 3 条（替换不污染 redo 栈、顶点编辑端到端可复算、只动选中顶点）。
- 发布物料：制品 126.6MB zip + SHA256；GUI 冒烟（offscreen 启动成功）。

---

## [未发布]

### 改进（**无上限递归轮：科研→建设→测试→门禁（探针全量 + 变异靶面扩展）**）

> 接切片 8。用户指令"下一轮直接无上限递归做，建设/测试/科研循环到关机"。本轮不做新功能，
> 而是**把已有的科学探针全量跑一遍找真实弱点**，并把变异测试靶面从 `playback.py` 扩到整条
> 编辑内核 + 决策层，按"探针暴露缺口 → 补判别性判据 → 跑门禁"循环。

- **科研（全量探针，全部完成）**：`bandwidth` / `declip` / `denoise` / `dehum` / `robustness` /
  `op_order` / `real_library` 七条探针跑完，结论：
  * **核心算法全部经实测确认为最优选择**——`declip` 用 pocs（插值族被探针否决）、`dehum`
    用 `filtfilt` 零相位 + 默认 Q=10（零相位 > 因果 2–3.6 dB）、`denoise` 用 `n_fft=4096 / α=0.98`
    （扫描里最好）、`op_order` 的规范顺序把 6 种排列的 SDR 极差吸收到 **0.44 dB**。
    ⇒ 这一轮**没有发现需要改的算法缺陷**，只验证了既有选择。
  * **`dehum` 频率漂移鲁棒性**：Q=10 在 +1 Hz 漂移下仍压 ~17 dB（Q=40 只剩 ~3 dB），
    确认 Q=10 是"漂移 vs 漏嗡"的最优折中；实测了一族**漂移分散陷波**（每谐波 f0±0.5 Hz
    三连）⇒ **反而更差**（零漂移 SDR 26→22、谐波音 17→16，且 +1 Hz 压制更好但 SDR 仍不如
    单陷波）——故不采用，单陷波 Q=10 维持。
  * **健壮性**：13/13 畸形语料走显式路径（无 traceback / 无静默通过），1074 个 fuzz 变异体
    未发现崩溃。注：`flac_openable_trunc` 返 `rc=3`（可开但中途错）与其余"完全读不了 `rc=4`"
    是**正确区分**（显式错误码语义），非缺陷。
  * **外部有效性**：`real_library` 在 1 个真实文件上跑通（判定分布/位深升位/截止分布账），
    伪 HiRes 分类器按项目纪律标"线索/参考"、不升格为判据（自有验证集未支持）。
- **建设（真实发现 → 钉死）**：
  * ★ **发现链式渲染的 FFT 对齐非确定性**：同组 op 对象连渲多次，因 numpy `rfft/irfft` 末位
    比特依赖**缓冲区对齐（堆布局）**，跨不同堆状态会差一个 ULP（~1e-7 相对，约 −140 dBFS，
    低于 `roundtrip_floor_dbfs` 量具下限）。**不是算法缺陷**，但"逐字节相同"的判据会被它
    误伤。把"同组 op 任意排列 → 产物在 1 ULP(float32) 容差内一致"钉成回归测试
    `tests/test_edit_chain.py::test_reordering_a_mixed_stack_is_order_invariant`（替代原先
    会 flaky 的逐字节判据）。
  * **变异靶面扩展 + 上限敏感性纠正**：`packaging/mutation_probe.py` 把
    `core/analyzers/codec.py` 纳入靶面（纯决策逻辑：对照表 + 奈奎斯特缩放 + 中心距排序 +
    位深升位检测，出现在判定链里）。**先纠一个量具口径错误**：首轮用 `--max-mutants 40` 读出
    "0.667"——但工具本身提示"同样的代码，小上限会跑出更高的分（小上限 0.864 vs 400 上限
    0.619）"。**改跑满上限（400）才是真实下限**：从 **0.600（28/70 存活）**起步，补了
    候选上限取"最近邻 3 条"的选取语义、scale 公式、note 阈值方向、非正截止短路、`CodecAnalyzer.run`
    入口的 `or` 回落与 note 短路、位深 `claimed` 的 32/24 分支等判别性判据后 → **0.643
    （25/70 存活）**。**残余 25 个存活 = 可接受债务**：其中绝大部分是 `CODEC_SIGNATURES` 查表
    常量（区间互相重叠的社区汇总数据，单个边界改动常被相邻签名覆盖，逐边界填测试是低价值填充），
    少数是不可达的死边界比较（`42 cmp <=` 在 cutoff=0 处被 `not` 短路吃掉、`126 cmp <` 的
    阈值 21.41 高于整张表上限 20.6 故永不被任何命中截止点触及）→ 按纪律**不再用低价值判据填充**，
    记此为止。
- **门禁（全部不退化）**：ruff 全树 `All checks passed!`；全量变异门禁 `playback.py` 之外
  新增的 `codec.py` 靶面**满上限实测 0.643**（残余 = 查表常量 + 死边界，见上，记债务不填充）；
  既有 17 靶 **105/105 有效变异体全杀、0 测试缺失存活、10 项诚实等价登记**（1.000，下限 0.90）；
  `packaging/palette_report.py` 配色/面积配额不达标项 **0**、`perf_probe` 交互全在帧预算内、
  `mem_probe` 图元不累积/缓存有界/清理后回落：`tests/` 全套绿。
  ⚠ 顺手修了本轮暴露的一个**历史 flaky**：`test_ui_material.py::test_burst_does_not_leak`
  （通知风暴回收）在全树 1246 跑批负载下偶发失败——属该测试等待窗不足（负载下动画变慢，
  原 720ms 不够）。已把等待放宽到 1.5s（及其同伴 `test_notify_then_dismiss` 放宽到 600ms），
  5× 负载跑批复验稳定通过。其他历史 CHANGELOG 里那条"已知 flaky"记录可视为已清。

### 科研（**递归轮续：稀疏去削波 / decrackle / 健壮性重跑**）

> 接上一轮"探针找弱点"之后，继续把未覆盖的修复算子候选与健壮性门禁跑实——**结论同样是
> "验证既有设计、不引入改动"**，但把每个"不落地的结论"都钉成回归判据，免得以后被静默推翻。

- **稀疏 / 相位感知去削波（`sparse_declip_probe.py`）——验证现行 POCS，不替换**：在稀疏谐波音上
  ADMM（α=0.003）比 POCS 略好（+46.7 vs +40.6 dB），但在**密集削波（47.9% 饱和）上 ADMM 反而更差**
  （+7.71 vs POCS +7.63 dB，α=0.03 时塌到 +6.46 比基线还差）。探针自带的"欠定判据验证"证实
  `DeclipOp.ambiguous` 标志依据正确：余量为正（定解）时 SDR 随迭代单调涨、余量 ≤0（欠定）时
  钉死不动。⇒ **密集削波的恢复上限是欠定性的根本限制，不是算法缺陷**，POCS 已是该条件下最优，
  不引入稀疏/ADMM 分支（会拖慢且密集场景更差）。
- **decrackle（`decrackle_probe.py`）——确认"不落地"**：密集小脉冲（破音/噼啪）喂给**现有**
  `DeclickOp` 做参数普查 + 试了 LPC(64) 残差新量具。结论：① "decrackle" **不是新算法**——它
  就是更松的 `DeclickOp`（`threshold=3 / max_run=None` 在密集脉冲上 ΔSDR ≈ +3.1 dB，默认
  5/4 仅 +1.4，同一机制）；② 更松设置在**白噪（+285 检出）/ 合法瞬态（+286 检出）上大量误报**，
  默认设置这两类全 0 ⇒ 正是默认取 5/4 的"代价不对称"理由；③ LPC(64) 残差整体为负（ΔSDR −2.6 dB）。
  ⇒ 不另立 `DecrackleOp`（重复造轮子），密集破音本就在 `DeclickOp` 职责内。新增回归
  `tests/test_edit_decrackle.py` 钉死三条：默认在干净/白噪/合法瞬态 **0 检出**、默认在密集破音仍
  **正收益**、更松设置收益更高但**误伤真实信号**（量化"为什么默认不取它"）。
- **健壮性重跑（`robustness_probe.py`）——确认门禁未退化**：13/13 畸形语料全走显式路径
  （无 traceback / 无静默通过），**1064 个 fuzz 变异体未发现崩溃**。本轮无新增崩溃样本
  （如实报告：不是"通过安全测试"，是这次没撞上）。
- **真实曲库大盘点（`real_library_probe.py`）——本环境不可复算，记死端**：该探针需要外部
  `--json`（n=448 用户曲库导出）作为真值，本机无此文件 ⇒ 参数校验直接报错退出。属"外部有效性
  调查"而非门禁（探针自身也注明"没有 `--strict`"）。**跳过**，待有曲库导出再接。
- **变异白名单过期（**`packaging/mutation_probe.py` **）——修复一个量具自身的假阳性**：把
  全量变异门禁重跑时发现 `render.py` 报出"存活 `CHUNK_HOPS=512`"。核查后是**白名单行号过期**：
  2026-10-25 插入 `_render_chained` 后整文件行号下移，但 `EQUIVALENT` 表里 8 条
  `render.py`/`declick.py` 等价体仍用旧行号（68/124/126/345/355、84/85/86），量具按
  `文件:行号:故障` 精确匹配 ⇒ 旧行号匹配不到 ⇒ 本应等价的变异体被误报"存活"。已把 8 条全部
  对齐到当前行（76/168/170/406/416、148/149/150），并写脚本核过**整张白名单无残留过期行号**。
  重跑 `render.py`：**0 测试缺失型存活**（8/8 杀、1 诚实等价）。`CHUNK_HOPS` 的等价性用
  `chunk_hops=4 vs 512` 渲染同 op 输出逐位相同证过（确为纯吞吐旋钮、与结果无关）。
- ★ **第二个 flake 类根因（修掉）：libsndfile 的 PEAK 块创建时间戳**。全量跑批下
  `test_edit_cli.py::test_project_chained_render_and_stage_report` 挂了 3 次（断言
  "CLI 链式渲染 == 直接渲染**逐字节相同**"）。留档实核：失败样本 768088 字节里**唯一**差异在
  偏移 60——libsndfile 给 FLOAT WAV 写的 **PEAK 块里的 time_t 创建时间戳**（`0x6adf6043` vs
  `0x6adf6044`，两次渲染跨过 1 秒边界），**768 KB 音频数据逐位相同**。⇒ "字节相同"这类判据
  在 FLOAT WAV 上全是**秒边界 flaky**（`test_save_project_then_load_renders_the_same_bytes`、
  `test_empty_project_is_identity` 同型暴露面，只是此前运气好）。修法：`_bytes()` 比较前把
  PEAK 时间戳归零（布局 `'PEAK'+size+version+timeStamp`），让判据表达"同样的音频"而不是
  "两次渲染恰好落在同一秒"；用失败留档验证（归一化后相等 ✓、改音频数据仍能查出 ✓）。
  与上轮 FFT 对齐非确定性并列：**渲染内核两次实核均为逐位确定**，flaky 全部来自
  "判据把无关元数据当成了被测对象"。

- **render.py 满上限确认复跑（白名单对账后）：0.970**（165 有效、160 杀、15 等价、
  0 无效）——确认对账前的 0.924 是白名单漂移期的假低。5 个存活处置：射线法 2 条
  （`>→>=`/`+→−` 在整值顶点的测度零处滑过固定网格互证）补**顶点微扰网格**判据
  （±1e-6 随机微扰 50 顶点 × 40×40 探针，任何算子改动必翻转至少一点）；截断短读
  防御 3 条（本机 libsndfile 不可达，与既有同类条目同口径）登记。

### 科研（**学术前沿 4 候选：0 落地 / 2 实测否决 / 2 未测**）

- **候选 1 一致性交叉淡化（Zaviška 2021）——实测否决**：论文主张 POCS 后接 sin²
  交叉淡化可得 +2 ODG、少跑 1/3–2/3 迭代；**实测四档迭代增益恒 +0.00**——本项目的
  POCS 是"逐位写回已知样本"的精确投影，不存在论文所述硬替换阶跃（那是"先重建再
  覆盖"式实现的产物）。场景不匹配。
- **候选 2 Gerkmann–Hendriks 软 SPP 噪声跟踪——实测否决**：台阶床三档对照，
  **现行 bin 中位数在高床段显著胜出**（+0.52 vs GH 的 −0.74、固定谱 −1.19）——
  GH 的 DD 先验是语音模型，在音乐高噪声段跟不上；中位数是无模型鲁棒统计量。
- 候选 3（Rajmic 2019 广义投影，预期 >2× 加速）需公式推导、候选 4（UTOSF 2025）
  在中位数已胜出场景预期收益有限——**未测，如实标注**。
- 详见 `docs/editor_research.md §20⑧⑨`。净产出：4 候选 → 0 落地，两个高预期候选被
  **自有语料**证伪，避免引入无收益复杂度（与 §19 IMCRA、§20⑦ POCS 早停同一模式）。

### 新增（**外部工程实践借鉴：审计摘要 / 实现自证 / 证据分级**）

> 用户指示：盘点三个外部仓库（queyi_core 89 py、行远本体 29 工具、CPP-Bible 814 工具），
> "能抄的抄、标准库级改进直接内化、前沿自研、好用算法直接用"。记账见
> `docs/borrowed_practices.md`（含未采纳项与理由）。

- **★ `canonical_json` + `audit_digest`（借鉴 queyi_core 信任根）**：确定性序列化
  （sort_keys + 紧凑 + 非 ASCII 直出）⇒ 同一审计记录在任何机器/时区/小版本上**同字节**；
  记录摘要**剔除 `timestamp_utc`/`dest`**（同一修复的另一落盘位置/另一时刻必须同摘要）
  ——"可复算"从"同参数同结果"升级为**跨机可核对**。判据 3 条（同修复不同路径同摘要、
  键序无关、时间戳不参与）。
- **★ `kernel_self_digest`（实现自证）**：渲染模块文件自哈希进审计 `kernel_digest`
  ——两份审计记录 digest 相同 ⇒ 同一份渲染实现产出，**实现没被偷偷改过**（法证口径）。
- **★ 证据分级 L1–L5（借鉴 queyi 分级，按音频语义重定义）**：审计新增
  `evidence_levels` 表，每个数值字段标注 `L1_measured`（本次实测）/`L2_derived`
  （确定性推导）/`L3_setting`（调用方设定，**不是测量结果**）/`L4_protocol`（容器事实）/
  `L5_unknown`（弃权）；人读报告在每个数值后标注**〔实测〕〔推导〕〔设定〕**，
  并新增"证据锚"块（审计摘要 + 实现自证）。判据 2 条。
- **弃权四态对齐（借鉴 CPP-Bible 的 ABSTAIN 六态）**：本项目已有四种"做不到"的显式
  表达——`L5_unknown`（量具不可用）、`ambiguous`（欠定：多个合法解）、
  `read_incomplete`（源截断）、`noop`（无约束可利用）——登记为同一原则的三个实例：
  **不把"做不到"装成"做到了"**。
- 门禁：ruff 全绿、全量测试 0 失败。

### 科研（**POCS 早停：实测否决**）

- **POCS 早停实测否决**（Agent 调研的落地前探针，`docs/editor_research.md §20⑦`）：
  中度削波上 128 次迭代时 ΔSDR **仍在大幅改善**（64→128 涨 14.2 dB，末步 −101 dB）——
  早停不是"省时间不损质量"，是"省时间损全部质量"。POCS 对此问题次线性收敛，
  默认 128 次已是质量/时间折中。文献 1000–3000 次与本实测一致。超松弛 λ 记为
  未来方向（需专门收敛曲线研究）。consistency = 同一可行集不同求解器（约束等价）。

### 新增（**产品化三件：spec 优化 / 扩展指南 / i18n 第一步**）

- **Inspector poly 顶点明细判据**：选中 poly_gain 条目 ⇒ 审计区逐行 t/f
  （补接线测试；`_show_poly_points` 已在上一轮落地）。
- **★ PyInstaller spec 优化（Agent G 清单落地）**：① **UPX 关闭**（杀软误报重灾区
  ——ClamAV/AVG 直接报毒，体积收益远小于信任代价）；② **编辑内核全模块显式
  hiddenimports**（与 ANALYZERS 同理：`edit/` 子包此前全靠静态追踪，动态导入路径
  会静默缺失——`edit_cli`/`export_dialog`/全部算法模块逐个列出）。
- **★ 内核扩展指南（`docs/extending_edit.md`）**：把"加一个修复算子要改哪里"的
  隐含知识显式化——以 `DeclickOp` 度量的**9 文件触点清单**（内核 4 + 表面 5，其中
  batch/inspector 因 describe/to_dict 驱动**免改**）+ 判据/文档 3 类 + 已固化的
  自动化护栏清单（property/变异/白名单巡检/数字对账/审计报告自动覆盖）+ 版本纪律
  （迁移链判据模式）。可维护性的硬指标从"口口相传"变成文档。
- **★ i18n 第一步（Agent I 路线）**：TransportBar 全部 8 个 UI 字符串包 `self.tr()`
  （中文原文即 key 的 Qt 惯例）；`lupdate` 基线 `.ts` 生成并入库（`i18n/editor_zh.ts`，
  8 条抽取验证）；**迁移零风险判据** 2 条（tr() 无翻译时返回原文 ⇒ 按钮文本迁移前后
  逐位一致；.ts 基线覆盖全部字符串）——后续模块可按同模式渐进，不需要一次全量。
- 门禁：ruff 全绿、全量测试 0 失败。

### 新增（**文档数字对账工具化 + a11y 自动化（Agent I 清单落地）**）

- **★ 文档数字对账工具（`packaging/docs_number_audit.py` + 门禁测试）**："引用的数字
  必须能从仓库复算"从纪律变成注册表驱动的自动核对（5 条：denoise 头牌三格、
  declick 尖刺/级联）。**对账器的口径教训（写进工具头）**：复算必须**复用探针的
  场景构造与包装函数**——首轮近似重写复算出 +11.19 vs 文档 +11.7 的假阴性，
  换探针自身口径后 5/5 全过（口径对不齐时先怀疑量具、再怀疑文档）。
- **★ a11y 自动化**（Agent I 清单的可离屏部分）：① **WCAG 对比度判据**——主题
  文字在各级背景 ≥4.5:1（dark/light 双主题、text/text_dim/text_faint 全色对；
  弱文本历史踩过 3.46:1 的坑，此判据把"不再回退"钉死）；② **焦点链完整性**——
  编辑窗口 Tab 链覆盖可见可聚焦控件、无黑洞。屏幕阅读器桥（UIA/AT-SPI）离屏
  不可测，维持人工登记口径。
- **IMCRA 门控中位数：实测否决**（Agent C 方案①的落地前探针）：宽带突发场景下
  中位数跟踪 vs 固定谱在**干净段**几乎相同（−9.11 vs −9.12 dB）——突发的伤害不是
  "中位数被抬高后残留"（跟踪已即时回落），门控无收益可救。§19 的中位数方案维持。
- 门禁：ruff 全绿、全量测试 0 失败。

### 修复（**裸字节比较的系统性遗漏：第三类 flake 根因清零**）

- **★ session 测试的 `_sha` 与 dither 测试的 2 处裸比较**——都是 FLOAT WAV 的
  PEAK 时间戳秒边界 flaky（2026-10-26 在 edit_cli 首次发现的同根因）。全仓扫描
  `open(…, "rb")` 裸比较后全部归一化（`_sha` 哈希版 + `_bytes` 复用）。
  实证：修复前 session 文件 3 连跑 2 次失败（随机分布在不同渲染测试），
  修复后 **6+3× 全量套连跑 0 失败**。⇒ 全项目"逐字节相同"判据现在
  只有一个口径（PEAK 归一化），三类 flake（FFT 对齐 / 等待窗 / PEAK 时间戳）全部清零。
- **session.py 满上限变异 19/19 = 1.000**（前缀缓存新面 6 存活补 5 条单元判据：
  内容寻址键、缓存上限、move/remove 边界与掩码跟随、单操作直渲早退）。
  **编辑内核全部 10 靶满上限 ≥0.924、9 靶 1.000**。
- 前缀缓存键加**进程 ID**（tempdir 跨进程共享 ⇒ 并行测试互清缓存文件的竞态隔离）。
- Inspector 选中 poly 条目 ⇒ 审计区显示**顶点明细**（逐行 t/f——"记录即选区"
  要能被读到）；property 测试扩到**全部六算子** schema 幂等；路线图 P2/P3 状态更新。
- 门禁：ruff 全绿、全量测试 3× 连跑 0 失败。

### 新增（**不再保守：台阶咔哒落地内核（schema v3）+ 前缀缓存 + property 测试**）

- **★ 台阶咔哒第二检测臂落地内核（`DeclickOp.step_repair`，默认开；schema v2→v3）**：
  §20 的"级联待实现"兑现——`declick()` 升级为**双臂级联**：两臂**都在原始信号上检测**
  （平台臂看尖刺修复后的信号会把端点形态破坏——实测 w=3 从 +24 塌回 +0.60），修复按
  **平台先、尖刺后**（平台 run 覆盖完整跨度先插值）。实测：多平台 w=3 **+24.03**
  （单臂 +0.60）、w=5 +2.29（单臂 0 检出）；干净三素材 0 误报。**实现旅程的三个真发现**
  （全部实测记录）：① 阈值追踪全局 σ 的**反相关盲区**——L=41 的 σ 反涨（0.152→0.178）
  ⇒ 阈值更高 ⇒ 平台检出全灭，**L=21 才是甜点**（推翻探针阶段的 L=41 结论）；② 孤立
  单平台的中值残差 run 只有**尾缘 1–2 宽**（前缘被内容上升形态掩没）⇒ min_run 清掉
  ⇒ 检不出——边缘配对与平坦性签名两个候选都实测无分离力，**如实记为本臂效力边界**；
  ③ 掩膜并集有害、级联才是正确形态（§20 预判兑现）。**schema v3 + 迁移链惯例**
  （Agent D）：v1/v2 记录显式迁移补 `step_repair=False`（旧产物逐位复现）；
  审计口径名 `residual2_mad2scale_step_cascade` / `…_linear` 随臂切换；stats 记
  `step_samples/step_runs`。判据 4 条新增（平台恢复、臂关闭逐位回退、零误报钉死、
  min_run 下界）；CLI/报告/链/项目记录全表面随 schema v3。
- **★ EditSession 前缀缓存（Agent D 方案落地，限混域栈）**：算子不可变 ⇒ 启用栈的
  前缀渲染产物可 memo（tempdir + 内容哈希键），undo/redo 退化为缓存索引切换；
  **只对混域（chained）栈启用**——同域单路径栈是全 float64 直渲，FLOAT 中间层会把
  −144 dBFS 舍入带进"未编辑处"破坏 undo/redo **逐位相同**硬判据（实测抓到后收窄）；
  缓存有界（8 个中间文件，超限清理）。判据 2 条（混域命中+可复算、同域绕过）。
- **★ property-based 判据（Agent B 方案落地，Hypothesis 6.168 已在环境）**：三性质
  （schema 幂等、render 非破坏不变量的**随机化边界**、增益单调性）。property 立刻
  抓到判据自身的两处缺陷（单调性缺 g1≤g2 前置、function-scoped fixture health
  check）——自动扫组合空间的价值首验。
- **白名单自动化对账器**：行号漂移第 3 次咬人（本轮 10+ 条）后，把"±60 行窗口内找
  旧记号"的模糊定位写成脚本跑在巡检流程里（单候选自动改写，多候选报人工），
  102→103 条全部对齐。render.py 满上限复跑 **0.924**（172 有效，13 存活=漂移期产物
  对账后待复跑确认，7 无效=环境噪声）。
- 门禁：ruff 全绿、全量测试 0 失败。

### 科研（**多 Agent 并行调研轮：台阶咔哒机制找到 / property 测试 / 架构借鉴**）

- **★ 台阶咔哒（§17/§20 挂账）机制找到、融合形态待解**：多 Agent 调研推荐
  Vaseghi & Rayner 1990 的**长窗中值残差**（`x − median_filter(x, L)`）——实测
  （`declick_step_probe.py`）**全宽度覆盖**：w=3 +22.12 / w=5 +3.44（现行零检出）/
  w=8 +1.82（L=41 时）。四个机制发现全部实测：中值窗**自污染**是宽度依赖的根因
  （w=8 时 8/21 污染 ⇒ 残差 0.74 恰差 0.02 过 5σ；L=41 减半污染即解）；**min_run=3**
  清零瞬态误报（误报 run 全在宽 1–2、平台在 4–5，领域与两点检测器天然分离）；
  平坦性签名**无分离力**（否决）；**掩膜并集是有害组合**（C1 端点样本拉长插值跨度，
  w=3 从 +22 塌回 +0.60）⇒ 正确形态是**级联**（先修尖刺再检平台），未实现记下一步。
  挂账升级："没有机制" → "机制找到、级联待实现"。文献锚：Vaseghi & Rayner 1990。
- **非稳态噪声跟踪的可借鉴改进**（Agent C，公式经来源代码验证）：维持中位数方案，
  可移植 IMCRA 的**门控中位数**（双测试布尔掩码防宽带突发抬高中位数）与**条件更新率**
  （λ_t = λ+(1−λ)·p̂，疑似内容帧冻结）；Martin 2006 偏置补偿闭式仅在换最小值方案时
  需要（我们中位数偏置 1/ln2 天然小一个量级——这是它稳的根源，Agent 用蒙特卡洛验证
  D=100 帧最小值偏差 ≈98.6×）；Gerkmann–Hendriks 2012 作 A/B 对照。
- **property-based testing 候选**（Agent B）：3 个性质候选（schema 往返幂等、render
  纯函数不变量+选区外逐位不变的随机化边界、STFT/COLA 参数组合完美重建）+ 集成要点
  （deadline=None profile、health check 治理）——记入待办，未实现。
- **非破坏编辑架构借鉴**（Agent D）：① **前缀缓存增量渲染**（算子不可变 ⇒ 前缀
  hash 链可 memo，undo/redo 退化为缓存索引切换——30 算子链改尾部从 O(全链) 降
  O(尾部)）记为 EditSession 的性能方向；② 版本迁移的**有序迁移链**惯例（我们是
  单步 _migrate，版本多了再升级）；③ 序列化人可读（已满足）。**明确不抄**：
  Audacity 破坏式 undo（烘焙音频、栈不随工程保存）与 .aup3 不透明格式——与我们
  "操作栈是唯一真相"和 JSON 显式契约相抵触。
- **playback.py 满上限变异 46/46 = 1.000**（12 诚实等价）——大模块满上限扫全部完成。
- 门禁：ruff 全绿、全量测试 0 失败。

### 科研（**多 Agent 并行调研轮：台阶咔哒机制找到 / property 测试 / 架构借鉴**）

- **★ 台阶咔哒（§17/§20 挂账）机制找到、融合形态待解**：多 Agent 调研推荐
  Vaseghi & Rayner 1990 的**长窗中值残差**（`x − median_filter(x, L)`）——实测
  （`declick_step_probe.py`）**全宽度覆盖**：w=3 +22.12 / w=5 +3.44（现行零检出）/
  w=8 +1.82（L=41 时）。四个机制发现全部实测：中值窗**自污染**是宽度依赖的根因
  （w=8 时 8/21 污染 ⇒ 残差 0.74 恰差 0.02 过 5σ；L=41 减半污染即解）；**min_run=3**
  清零瞬态误报（误报 run 全在宽 1–2、平台在 4–5，领域与两点检测器天然分离）；
  平坦性签名**无分离力**（否决）；**掩膜并集是有害组合**（C1 端点样本拉长插值跨度，
  w=3 从 +22 塌回 +0.60）⇒ 正确形态是**级联**（先修尖刺再检平台），未实现记下一步。
  挂账升级："没有机制" → "机制找到、级联待实现"。文献锚：Vaseghi & Rayner 1990。
- **非稳态噪声跟踪的可借鉴改进**（Agent C，公式经来源代码验证）：维持中位数方案，
  可移植 IMCRA 的**门控中位数**（双测试布尔掩码防宽带突发抬高中位数）与**条件更新率**
  （λ_t = λ+(1−λ)·p̂，疑似内容帧冻结）；Martin 2006 偏置补偿闭式仅在换最小值方案时
  需要（中位数偏置 1/ln2 天然小一个量级——这是它稳的根源，Agent 蒙特卡洛验证
  D=100 帧最小值偏差 ≈98.6×）；Gerkmann–Hendriks 2012 作 A/B 对照。
- **property-based testing 候选**（Agent B）：3 个性质候选（schema 往返幂等、render
  纯函数不变量+选区外逐位不变的随机化边界、STFT/COLA 参数组合完美重建）+ 集成要点
  （deadline=None profile、health check 治理）——记入待办，未实现。
- **非破坏编辑架构借鉴**（Agent D）：① **前缀缓存增量渲染**（算子不可变 ⇒ 前缀
  hash 链可 memo，undo/redo 退化为缓存索引切换——30 算子链改尾部从 O(全链) 降
  O(尾部)）记为 EditSession 的性能方向；② 版本迁移的**有序迁移链**惯例；③ 序列化
  人可读（已满足）。**明确不抄**：Audacity 破坏式 undo 与 .aup3 不透明格式。
- **playback.py 满上限变异 46/46 = 1.000**（12 诚实等价）——大模块满上限扫全部完成。
- 门禁：ruff 全绿、全量测试 0 失败。

### 新增（**证据链 + ROI 渲染：Agent 调研落地两件**）

- **★ 审计证据链（SWGDE 法证口径）**：Agent 调研结论——RX/Audition 的历史记录**无机读
  导出**（RX 绑死私有 .rxdoc、Audition 只存操作名会话即焚），本项目的机器审计 dict
  **已领先全场**；两条补强中第一条落地：审计新增 **`source_sha256`/`dest_sha256`/
  `tool_version`**（流式哈希、内容寻址——**确定性**，同字节同哈希，不破坏 render
  纯函数纪律）；**`timestamp_utc` 由 CLI 层注入**（墙钟属调用方，三处审计写盘点统一）。
  法证市场判断：利基但客单价高，SWGDE 合规背书是真实壁垒（Agent 产出）。
  判据 2 条（哈希重算核对、不同源哈希不同、render 本体无时间戳）。
- **★ 编辑视图 ROI 快速重绘（Agent E 第一步落地）**：缩放/平移从"全幅重绘 518ms"
  改为**切片 + `set_data/set_extent`** 快速路径——**实测 30s 素材 133ms → 5ms
  （27.9×）**，连续 5 级缩放均值 4ms（帧预算内）。顺带修复一个真 bug：波形电平条
  把全曲包络 `linspace` 压进视野（缩放时时间轴错位），改为按真时间切片。
  叠加 artist（选区/套索/播放头）跨重绘存活。pyqtgraph 路线留作第二
  步（Agent E：ImageItem 快 75–150×，ROI 通路就绪后仅是渲染层替换）。

### 新增（**P5 生态起点：脚本化入口——"示例即判据"**）

- **★ `examples/restore_pipeline.py` + `docs/scripting.md`**：脚本化使用编辑内核的
  权威入口。示例端到端演示非破坏修复全流程（合成劣化素材 → 建修复链 → 存链预设 →
  渲染 → 审计 JSON + 人读报告），全部走公开 API、种子固定可复算；文档写清三层入口
  （内核 API / CLI / GUI 同一内核）与三条铁律（非破坏/可审计/有判据才做）。
- **示例即判据**（`tests/test_scripting_example.py`，2 条）：示例以子进程跑通（内核
  API 变更把它跑坏 = 判据红）、五件产物齐全、**再跑一遍审计逐字段一致**（渲染纯函数）、
  三算子按规范顺序实际生效（declick 检出>0 / declip 饱和>0 / dehum 压制实测为负）。
- 路线图 P5"脚本"项标记起点完成；工程格式（Project JSON v2 + 修复链）此前已收口。

### 修复（**schema v2 回归：批处理旧清单被严格解析拒绝**）

- **★ 回归修复（自查发现）**：上轮 schema v2 给 denoise 增加 `track_floor` 后，**v1 时代的
  批处理清单**（denoise 作业无该键）会被严格解析**整体拒绝**——旧清单不可复算是违反
  "可复算"承诺的回归。修法：`_migrate` 恢复**版本门控**（仅 ver ≤ 1 补缺失键；v2 起
  键必需，缺失按缺字段拒——"补什么"由版本决定，不许含糊）；批处理作业支持可选
  `"version": 1|2`（缺省 1 = 历史清单 ⇒ 显式迁移；2 = 严格 schema，缺键按**该作业**
  失败隔离）。判据 3 条新增（v1 清单迁移跑通、v2 缺键按作业隔离失败、好作业照常）。
- **批处理 per-job 审计/报告出口**：作业级可选 `"audit": path`/`"report": path`——
  每项渲染后落机器 JSON + 人读 Markdown（"可审计"在批量场景的出口；与 CLI 单件
  `--audit/--report` 同一口径）。判据 1 条。
- 门禁：ruff 全绿、全量测试 0 失败。
- **修复链 v1 文件迁移（同源问题）**：`load_chain` 此前只认当前版本——历史 v1 链文件
  （denoise 无 track_floor）被拒。与 `Project.from_dict` 同口径：接受 {1, 2}、v1 走
  `_migrate`（判据：v1 链载入后 `track_floor=False`、未知版本拒绝、空 ops 校验用
  **当前版本**测——旧用例用 v1 文件测空 ops 会在版本检查就抛，走错分支，
  变异 `or→and` 因此漏网，ops.py 变异 0.872→**1.000** 时抓出并修复）。

### 新增（**P2"矩形→套索"内核件：多边形时频选区增益 `PolyGainOp`**）

- **套索 view 打磨（自查抓到一个图元累积 bug）**：缩放/平移重绘不再吞掉进行中的
  套索叠加（`_redraw_all` 重画）；进行中**读数浮标**（顶点数 + Δt/Δf 包围盒实测，
  仿矩形的测层浮标）——首版读数文字没存 artist 引用，拖拽中逐帧堆积（实测 30 采点
  堆 30 个 text artist），修复后恒 1、松手清零（判据
  `test_lasso_readout_does_not_accumulate_artists`）；缩放持久性判据
  `test_lasso_overlay_survives_zoom_redraw`。
- **theory §12.1"看/刷口径"挂起项闭环**：套索顶点是连续 (t, f) 数据坐标、与显示
  网格无关 ⇒ 多边形语义下天然解耦（显示 nperseg=16384 / 编辑 n_fft=4096 的差异是
  **有意**的：显示要平滑统计、编辑要局部性）。`render.py` 的 DEFAULT_N_FFT 注记与
  `editor_view` 模块 docstring 双向更新。
- **CLI `--poly` 旗标（算子表面收口）**：`--gain -6 --poly "t1,f1;t2,f2;…"`——多边形
  选区增益的命令行出口（与 `--band` 平级；与 `--at`/`--band` 互斥、必须配 `--gain`、
  <3 顶点拒绝）。**每个内核算子至此都有 CLI 表面**。判据 2 条（渲染+save-project
  逐位一致、四组互斥/校验）；CLI 汇总打印补 poly 分支。
- **★ UI 套索捕获（P2 最后一公里）**：transport 新增"◌ 套索工具"按钮（与矩形选区
  **互斥**）；view 层自由拖拽采点（视野对角 0.4% 去密）→ 松手 **Douglas–Peucker 简化**
  （eps = 视野跨度 1%，顶点表要人可读——记录即选区，500 个噪声顶点没法人工核对）→
  `polySelected` 信号 → 算子面板"多边形增益"档应用 `PolyGainOp`。Escape 取消、
  误触（简化后 <3 顶点）不发信号。判据 3 条（捕获→简化→应用全链、<3 顶点不发、
  工具互斥）。实现踩坑：`_apply` 的入口守卫此前要求**矩形选区**存在——多边形增益的
  选区是多边形，守卫按算子类型区分。
- **★ `PolyGainOp`（kind `poly_gain`）**：顶点表 `(t, f)` 序列（隐式闭合）围成的时频
  多边形内部施加增益——**记录即选区**（顶点表就是可审计的选区本身）。属内判据用
  **射线法**（向量化、确定性），与 `stft.frame_times`/`bin_freqs` 的谱点口径逐位一致。
  **复用同一条 STFT/WOLA 渲染路径**（`_render_band`）：外扩窗宽、往返残差、时频点
  记账、规范顺序相位（4）全部同款——矩形顶点多边形 ≡ `BandGainOp` 的**锚判据**
  （1-ULP 容差，两次独立渲染的 FFT 对齐差）钉死复用性。
  * **边界语义（如实）**：多边形上没有半开区间的自然对应 ⇒ 边界上的谱点是否属内是
    **实现定义**——选区坐标应偏离谱点网格（判据用非网格坐标）。
  * 凹多边形正确性：内核射线法与**独立写的纯 Python 参照实现**在同一网格上互证；
    凹口内的内容实测不被触及（纯音探针——混音探针会把"凹口外被压的分量"算进
    能量比，量不出凹口本身，判据因此用纯音）。
  * **自相交（蝴蝶结）语义钉死**：射线法按**奇偶规则**确定——两瓣属内、腰部不属内；
    不拒绝（顶点表在审计可见、用户意图自担），行为可复算而非 undefined。实现细节：
    首个探针点恰好压在两对角线交点上（测度零退化），判据改用非退化腰点。
  * 校验：<3 顶点 / 非数对 / 负坐标 / 非有限值全拒绝（含**恰为 0** 与**恰 3 顶点**
    的边界正向钉死）；`gain_db=0` no-op 逐位不变；schema 往返（v2 记录含 poly_gain）。
    判据 12 条（`tests/test_edit_poly.py`）。
  * 实现踩坑（记录）：① px/py 传反（顶点是 (t,f)，点也必须 (t,f)）；② `S` 是三维
    (bins, frames, ch)，掩膜要补通道轴；③ `synthesize(keep=...)` 是**输出样本坐标**
    ⇒ 掩膜必须盖整块所有帧（与矩形路径同语义，上下文帧参与 OLA）；④ 射线法的
    inside 预分配要用**广播形状**。UI 套索捕获（view 层）留下一切片。

### 修复（**schema v2 回归：批处理旧清单被严格解析拒绝**）

- **★ 去噪逐帧噪声底跟踪落地（`DenoiseOp.track_floor`，默认开；schema v1→v2）**：
  收回 §18④ 量出的"~10 dB 收益留白"。三个机制先后实测（`denoise_nonstat_probe` 扩展）：
  ① 帧宽带能量 SPP——**设计否决**（持续音帧能量永不落到噪声底，恒冻结）；
  ② 逐 bin 滑窗最小值（Martin 最小统计量判据形态）——**实测灾难**（稳态 −19 dB：
  无偏置补偿的硬最小值棘轮式塌缩，正是 Martin 2001 偏置补偿要解决的坑）；
  ③ **bin 中位数比值（落地）**——噪声床台阶时所有噪声 bin 的 P/N 同倍数变化、
  内容只占少数 bin ⇒ 逐帧中位数天然剔除内容；指数分布中位数 ln2 偏置精确可校；
  递归平滑 λ=0.8、缩放 clip ±12 dB（宁可欠跟随）。**实测**：台阶床低参考总 ΔSDR
  **+2.74 → +9.40**（低床段 +2.04 → +9.03），高参考 +10.90 → +11.96；稳态四格代价
  **≤0.66 dB**。全表面：CLI `--no-floor-track`、GUI"底噪跟踪"勾选、审计口径名
  `wiener_dd_floor_tracked`/`wiener_dd_profile` 随语义切换、`floor_track_db` 进统计。
  **schema v2 + v1 显式迁移**：v1 记录补 `track_floor=False`（旧产物**逐位可复现**、
  新语义不静默套用，判据钉死；v3 拒绝）。判据 10 条新增（台阶收益、稳态代价界、
  合成数组逐值钉 `_floor_track`（上下 clip 都绑定的序列）、ln2 校正不饱和逐值、
  静音噪声段/段内静音的有限性、口径名双向、CLI 旗标、GUI 勾选）；内核/CLI/GUI 全绿。
  **变异 44/44 = 1.000**（1 诚实等价：gamma 守卫 1e-30 与 0 在 P≥0 域数值恒同）。
  详见 `docs/editor_research.md §19⑥`。文献锚：Martin 2001（最小统计量+偏置补偿）、
  Cohen 2002/2003（MCRA/IMCRA）——v2 不走最小统计量路线（v1b 实测灾难：
  无偏置补偿的硬最小值棘轮塌缩 −19 dB），内容剔除靠中位数稳健性，偏置有闭式解。

### 改进（**大轮次续：双尺度去咔哒落地 + 非稳态嗡挂账实测收口**）

- **★ `DeclickOp` 升级为双尺度检测（`residual2_mad2scale_linear`）**：检出 = 两点残差
  分别过**全段 MAD** 与**滑动局部 MAD**（`scipy.median_filter`、`mode="mirror"`）两把尺子、
  各过宽度守卫后**取并集**——α/守卫语义不变、**没有新旋钮**。实测（`decrackle_tf_probe`，
  谐波音 + 已知脉冲）：稀疏 10/s **+2.65** dB（旧单尺 +1.94，precision 1.000）、中密 +1.44、
  密集 +1.21，**三类干净素材零误报**——每一格不降、稀疏大升 ⇒ 直接当默认。
  * 纯局部尺子**不单独落地**（密集下被别的咔哒自污染 −0.2 dB；窗宽扫描无一格全面占优）；
    C3 时频两级（STFT 竖条帧）**实测否决**（全部语料零触发，第一级参数救不回）。
  * **实现踩坑**：局部尺子的 `mode="nearest"` 把 `residual` 的**哨兵零**（`r[0]=r[-1]=0`）
    复制进端部窗口，端部尺子被压塌、干净素材 1–40 采样被误检——改 `mirror` 后清零。
    这也是探针（截断窗）与内核首版（最近邻填充）语义差，探针数字仍是基准。
  * 算法口径名随语义升级：`residual2_mad_linear` → `residual2_mad2scale_linear`
    （4 处判据同步）；"代价不对称"判据在新内核下重钉（默认稀疏 +2.65/密集 +1.44；
    α=3+无守卫在同一语料 **−2.95**、白噪/瞬态各 434 误报）。变异 **40/41 = 0.976**
    （5 等价登记；`LOCAL_HALF=100` 为调优常数，窗宽扫描佐证 ±1 不改变结论）。
    小脉冲（0.02–0.2）与台阶型咔哒缺口**维持挂账**（两点残差的物理极限，见 §17/§18）。
- **非稳态嗡挂账实测收口（`dehum_jitter_probe.py`）**：FM 抖动嗡 × {±0.1, ±0.25(IEC 容差),
  ±1 Hz(抖晃)} × {0.5, 3 Hz} + 慢漂移斜坡 × Q ∈ {10,5,3} 共 **21 格**：**Q=10 全胜或打平**
  （静止 +16.90、±0.25 Hz +17.60、漂移 +15.53 dB）；Q=5 仅在最极端一格 +0.13 dB
  （噪声级），静止时代价 −2.34 dB。压制深度对抖动敏感（−52 → −17 dB，审计如实报）
  但净收益始终大正 ⇒ **固定 Q=10 维持**，自适应跟踪无收益空间。路线图挂账关闭，
  详见 `docs/editor_research.md §18③`。
- **非稳态噪声挂账实测收口（`denoise_nonstat_probe.py`）**：台阶噪声床语料（内容三段
  低→高→低；噪声参考取**选区外的纯噪声前缀**，低/高床各一版——唯一变量是"床变了"）：
  **最危险的方向（高参考学谱 → 低床段内容被当噪声减掉）没有发生**（低床段仍 +6.03 dB——
  20 dB 压制上限 + DD 平滑兜住）；欠压制方向 +12.03（DD 逐帧先验 SNR 部分自愈）。
  代价是**收益留白**：床台阶时错配参考掉到 +2.04（对床可得 ~+12）——"不修坏、但留白"
  ⇒ 逐帧噪声底跟踪记为**未来增强机会**，路线图挂账关闭，见 §18④。
  ⚠ 探针自身踩坑记录：首版把"含内容的段"当噪声段（违反 `DenoiseOp` 契约），
  量出 −9 dB 假伤害——那是"噪声段混入内容"的已知失败模式，不是非稳态；改纯噪声
  前缀后数字才反映真实问题（这也是"噪声段必须纯噪声"契约的一次实测验证）。
- **"噪声段混入内容"自检：统计量判据被实测否决 + 试听缓解**（`denoise_noisecheck_probe.py`）：
  局部谱突出度在**粉噪上假高**（14.6 dB，1/f 斜坡被局部中值窗当成"坡"）、**平滑混噪上
  假低**（2.1 dB，能量摊开无孤峰）⇒ 两组分布重叠、不立判据；"是否纯噪声"本质需要
  内容模型（神经领域，与本仓确定性/可审计约束冲突）。缓解 = GUI 新增**"试听噪声段"**
  命令（把学谱输入的时间窗设为引擎 loop）——人耳复核"噪声段必须只有噪声"契约；
  审计照旧记录 `noise_t0/t1/noise_samples`。挂账关闭，见 `docs/editor_research.md §18⑤`。

### 新增（**大轮次：P4 工作流收口——审计报告 / 导出预设 / 修复链**）

> 用户指令"全方位排查大问题 + 调研论文/前沿/优秀实践 + 打磨到极致"。排查结论：算法层
> （P3 全部修复算子）经多轮实测无缺陷可修；真正的大缺口在 **P4 工作流**——渲染内核早就
> 支持的全部能力（全参数导出、审计记录）在 UI/CLI 上只露了一个角。本轮把"可审计的修复"
> 承诺的**出海口**补齐。

- **★ 审计报告（`core/edit/report.py` + CLI `--report` + GUI"导出审计报告"）**：
  渲染层早就返回机器可读审计 dict，但**人可读的存档格式**一直缺失——委托方/未来的自己
  没法直接看。报告是审计 dict 的**纯函数**（同一条记录 ⇒ 逐字节同一份报告）：三条纪律
  写进模块头——机械生成不新增判断、`None` 一律排"未测"（"没量"≠"量了是零"）、标签表
  没覆盖的字段**原键名照排**（渲染层将来新增实测字段自动进报告，不被排版层吞）。
  CLI `--report r.md`（与 `--audit` 同用得机器+人两份；单算子/`--project`/`--chain` 三条
  路径都接），GUI 命令面板"导出审计报告"。判据 **20 条**（`tests/test_edit_report.py`）：
  纯函数性、未测口径、透传、真实渲染集成（单算子/链式/抖动/时频/去咔哒）、落盘一致性，
  以及变异轮补的 9 条排版细节（题头双界、帧数行内联时长、read_error 回落文案、
  `_measured_db` 后缀的 dB 排版、占比 ×100、标签不重复、无 `op` 键的阶段不崩、
  编号从 1 起、单算子不出 kind 行）。
- **★ 导出预设（`ui/editor/export_dialog.py`）**：GUI 导出此前把口径**写死**成
  "WAV+源子类型"——内核的 FLAC/AIFF、PCM_16/24/32/FLOAT、TPDF 抖动全参数能力在 UI 上
  不可达。现在导出先选**配方**（容器/子类型/抖动，可命名存为预设，tempdir JSON 与算子
  预设同一约定），两条校验**镜像内核规则**并提前到弹窗里报：子类型必须装进容器
  （`sf.check_format` 的回答为准，不在 UI 里背表）、抖动只对整型（`_check_dither` 同口径）
  ——不许"点完导出才炸"。判据 9 条（`tests/test_ui_export_presets.py`）+ 2 条 GUI 判据。
- **★ 修复链预设（`ops.save_chain`/`load_chain` + CLI `--chain` + GUI 存/载修复链）**：
  项目记录 = 源 + 栈（复算一个具体产物）；修复链 = **只有栈**（配方，套到任何源——
  "同一套修复批量过曲库"的入口）。严格口径与项目记录相同：版本不符/缺 ops/空 ops/
  未知 kind/多余字段全部拒绝；文件里**没有** source 字段（本质差别，判据钉住）。
  CLI `--chain` 走与手写 Project **完全相同**的链式渲染路径（判据：与手写栈字节相同），
  并与算子 flag/`--at` 互斥；GUI 逐个 `apply` 进会话（每步可撤销）再渲染预览。
  判据 8 条（`tests/test_edit_chain_preset.py`）+ 2 条 GUI 判据。
- **变异靶面**：`report.py` 入靶（"可审计"出海口上的标签/单位/未测口径错了会直接误导读者）。
  首轮 0.500 → 补 9 条排版判据 → **0.957**（22/23，余 1 条经核查为等价：`write_report`
  先 `abspath` ⇒ `dirname(p)` 永不为空，`or '.'` 支不可达——已登记）。
- **★ render.py 满上限变异首次跑完（163 候选全量，约 1.5 h）——0.987**：149 有效、
  147 杀、**0 超时**、14 诚实等价（前几轮对齐的白名单条目全部被正确识别，无一假存活）。
  2 个存活全部处置：① `_render_dehum` 的空选区守卫 `k1 <= k0 ⇒ continue`——**真缺口**，
  补判据 `test_selection_past_eof_renders_as_clean_copy`（选区拖过文件尾 = 干净跳过、
  整轨逐位不变；写成 `<` 会对空段做零相位滤波直接崩掉整个渲染）；② dehum 报错文案的
  `k1 − k0`（与 declip/declick 同型，只进字符串）——登记等价。
  **复跑验证（同日第二轮全量）**：**147/147 = 1.000**（0 存活、16 等价、0 超时）——
  两处处置双双生效，编辑内核最大的模块收口满分。⇒ **编辑内核 8 靶满上限全部 ≥0.957，
  render/ops/stft/declip/declick/dehum/denoise/corpus 七靶 1.000**。

### 科研（**大轮次调研：修复算法 SOTA 对照**）

- **去削波 SOTA（2024–2025）**：深度生成模型（扩散族，如 CQT-Diff 一类）在感知质量上
  领先，深度展开（unfolded Douglas–Rachford）是混合路线；纯凸优化/POCS 已成基线。
  **本仓取舍**：扩散/神经网络路线需要训练权重、GPU、且采样随机——与本项目
  **可复算、确定性、无外部产物**的"可审计"根基直接冲突（同一文件两次修复结果不同的
  "修复"没法写进审计）；且本仓第 4 轮已实测：密集削波的恢复上限是**欠定性**（信息论
  限制），换更强模型绕不开。⇒ POCS 保留为内核，SOTA 路线**记入调研账、不引入**。
- **去咔哒 SOTA**：主流仍是 AR（线性预测）逆滤波 + 阈值检出 + AR 插值（RX/Acon 同族）；
  2023–24 的推进在 warped prediction、稀疏/双向 AR。本仓第 4 轮已实测 LPC(64) 残差
  检出 **−2.6 dB**（谐波素材上被合法高频主导）⇒ 两点残差 + 全段 MAD 的现行选择有实测
  支撑；"平台/台阶型咔哒"缺口已如实挂账（需阶跃检测器，另一个量具）。
- **性能/视觉门禁补跑**：`kernel_bench` 全套分析 10.1 s；`band_perf` 10 s 选区 367×
  实时、整轨 25×、峰值内存 ~34 MB 与素材长度无关；`visual_regression` 三视图
  0.0000% 像素差（dark/100DPI 基线）。

### 科研（**递归轮 6：剩余探针全扫 + 编辑内核变异收口 + 量具自审工具化**）

- **剩余探针全扫（14 条）——13 绿 1 参数门槛**：`a11y_report`（登记核对：未登记项无）、
  `view_coverage`、`threshold_survey`（语料两侧覆盖 + 负对照"有牙齿"）、`hum_detect_probe`、
  `cutoff_calib`（全部通过）、`mask_tradeoff`（外扩跨度 + 硬掩码已在最优格）、`stft_bounds`、
  `colormap_probe`（相位场色图三条全过）、`hue_family_report`、`fakehires_eval`、
  `lowfreq_eval`、`declick_verify_probe`、`bandwidth_probe` 全部 rc=0；
  `cutoff_impact` 需要 `--old/--new`（对比两份曲库导出）——与 `real_library_probe` 同源死端，
  本环境没有曲库数据 ⇒ 记参数门槛，不算失败。
- **编辑内核变异收口（7 靶全部 1.000）**：把 `ops / stft / dehum / declip / corpus / declick /
  denoise` 七个编辑内核模块**全部跑到满上限**：
  * `ops` 26/26、`stft` 40/40（5 等价）、`dehum` 18/18、`declip` 27/27（2 等价）、
    `denoise` 25/25（1 等价）——**开门就是 1.000**；
  * `corpus` 77/80 → **1.000**：3 个存活里 2 个是过期登记（`peak>0` 守卫的行号漂移）、
    1 个是 `crackle_train` 的 `amp * sign`（`sign ∈ {±1}` ⇒ 乘除同值，可证明等价）——登记后归零；
  * `declick` **0.848 → 1.000**：5 个存活里 4 个是**真缺口**，补 3 条助手边界判据钉死
    （`apply_max_run` 的 `max_run=0` 透传、`_runs`/`_repair_runs` 的**尾端检出段**必须不越界
    且持左端点值、`fraction` 的分母在**立体声**下是 `n×ch`——历史判据全是单声道所以
    `n/ch` 变异体一直活着）；第 5 个（`|r| > α·s` 的 `>=`）是测度为零的浮点边界，按本表
    同一口径登记等价。
- **量具自审工具化（`mutation_probe.py --audit-registry`）**：白名单行号漂移这个失效模式
  已经**两次**让分数假跌（2026-10-16、2026-10-26），本轮把巡检做成**门禁本体**：
  每条 `文件:行号:故障` 登记，其行必须**存在且仍含故障旧记号**（`const:X->Y` 查 `X`、
  `cmp/bin/bool:A->B` 查 `A`）；`--audit-registry` 单独跑，普通跑批**自动巡检**、
  `--strict` 下过期 ⇒ 直接 rc=2 拒绝出分。本轮巡检一次性查出 **15 条过期**（spectrum 2、
  render 7、declick 2、dsp 2、playback 1、corpus 2 中的 2 处守卫漂移），全部对齐或删除
  （declick 的 2 条：一条是重复登记、一条的构造已被新判据**杀死**——杀死优于豁免），
  修后 **97 条全部对齐**。

### 新增（**编辑工作站 v1 骨架：REAPER 式停靠外壳 + 播放引擎 P1**）

> 用户拍板方向：REAPER/宿主式重型工业级界面 + miniaudio 播放 + 全栈编辑工作站。
> 规格与验收见 `docs/editor_v1_plan.md`（按 AGENTS.md §4 五步产出）。本条目是**切片 1–2**
> （播放引擎 + 外壳骨架），编辑视图与算子接入在后续切片。

- **P1 播放引擎 `core/edit/playback.py`**（路线缺的「第四根柱子」）：`Transport` 纯状态机
  （stopped/playing/paused，headless 可测）+ `PlaybackEngine`（miniaudio 后端，WASAPI 低延迟）。
  设备枚举、选区段试听、播放头回调（哑时钟 25ms，自由线程下 ≥80% 落在 [20,60]ms）、
  近似 X-run 计数、设备错误走 `on_error` 不崩。
  ⚠ **实测约束**：miniaudio 在 Windows **无 ASIO 后端**（只有 WASAPI/DSOUND/WINMM/NULL），
  低延迟走 WASAPI；`NULL` 后端用于无声卡自动化测试。ASIO 不假装支持。
  判据 `tests/test_edit_playback.py`（25 条全过）。
- **REAPER 式外壳 `ui/editor/`**：三个可浮动/标签化/重排的 `QDockWidget`（文件/编辑/检视）
  + 顶部 `TransportBar`（播放/暂停/停止、设备选择、时间读数、缩放、选区工具、修复算子下拉）
  + 复用既有 `CommandPalette`（Ctrl+K）、`theme`/`palette`/`widgets`/**设计系统零新增**。
  暗/亮切换不崩（旧 bug #21 防护）。`EditorWindow` 是 `MainWindow` 之外的独立类，**零回归风险**。
  启动入口：`audiocheck --edit` / `python -m audiocheck.ui.editor` / `audiocheck-edit`（gui-script）。
  判据 `tests/test_ui_editor_shell.py`（9 条 offscreen 全过）。
- 新增可选依赖 `edit = ["miniaudio>=1.60"]`（MIT，已 `pip-audit` 核，无 GPL 传染性）；
  分析 CLI 不引入（lazy import）。
- **门禁**：ruff 全绿；播放 25 + 外壳 9 = 34 新用例全过；既有 `tests/test_edit_*.py` 全过（无回归）。

### 新增（**编辑视图 + 算子接线 + A/B + 审计面板：切片 3–4**）

> 接上一条「骨架」。本条目把编辑画布、四个修复算子、可审计检视面板全部接入
> `EditorWindow`，形成端到端可用的工作站。规格与验收仍见 `docs/editor_v1_plan.md`。

- **编辑画布 `ui/editor/editor_view.py`**：`EditorView(BaseCanvas)` —— 频谱图（inferno）
  + 波形包络（min/max 半透明填充带）+ 黄色播放头 + 矩形时间×频率选区（青色）。
  滚轮 Ctrl/Shift 锚点缩放走 `ui/zoom.py` 纯函数（对数频率轴按倍率、线性时间轴按比例，
  边界钳回数据范围）；鼠标拖拽平移、选区工具框选后发 `selectionChanged(t0,t1,f0,f1)`。
  复刻 AGENTS.md 判据：缩放数学有穷举边界测试、缩放后频率刻度按视野重挑（3–8 个）。
- **算子面板 `ui/editor/ops.py`**：把「选区 + 算子种类」变成可审计修复，**不重写内核**，
  严格走 `core/edit` 边界层同一套调用：
  - 去削波 `bandwidth` ← `content_bandwidth`（砖墙截止 / 量不到⇒奈奎斯特⇒逐位不变），
    与 `edit_cli._auto_bandwidth` **逐字一致**（`AnalysisContext.spectrum()` 三分位谱 +
    `content_bandwidth(f, db, fs, thresholds)` 返回 dict）；
  - 去嗡 `f0` ← `detect_hum`（稳态证据；证据不足不自动填，不猜）；
  - 去噪噪声段 ← 第二段选区采集（`set_selection` 在 `denoise` 模式记 `noise_sel`）；
  - 时频增益 `BandGainOp` 直接吃矩形选区的 `[t0,t1]×[f0,f1]`。
  每一步落 `EditSession`（非破坏栈）+ `render`（返回审计记录），撤销到空栈渲染**逐位相同**。
- **检视面板 `ui/editor/inspector.py`**：操作栈（可复算记录，`describe()`）+ 最近一次渲染
  审计（`stages`/`changed_samples`/`suppression_db`/`roundtrip_floor_dbfs` 等，**只挑可读字段、
  不 dump 整本字典**）+ A/B 开关（`abToggled`）。A/B 把原曲与处理后并存，一键切换、读数差可见。
- **接线（`shell.py`）**：`EditorView` + `OperatorPanel` + `InspectorPanel` 三个真实面板塞进
  文件/编辑/检视三个 dock（编辑 dock 内视图在上、算子面板在下，垂直拆分，可浮动重排）；
  `TransportBar` 信号接 `PlaybackEngine`（play/pause/stop/zoom/选区工具/设备/算子）；
  `selectionChanged`→算子面板、`applied`/`auditReady`→检视面板、`abToggled`→A/B。
  选设备 / A/B 真实 WASAPI 切换在后续切片（目前仅状态提示，不假装支持）。
- **门禁**：ruff 全绿；新增 `tests/test_ui_editor_wiring.py`（7 条 offscreen：载入渲染、
  选区发射、band_gain GUI↔内核字节一致、撤销空栈逐位恒等、A/B 发射、open_file 接线）。
  播放 25 + 外壳 9 + 接线 7 = 41 新用例全过；既有 `tests/test_edit_*.py` 全过（无回归）。

### 新增（**编辑工作站 v1 算子互证：GUI↔内核逐字节相同（全 5 类算子）：切片 7**）

> 接切片 6。规格 §"算子接入"有一条硬验收（docs/editor_v1_plan.md:81）：**每类算子走 GUI 后，
> 须与内核同参数产出逐字节相同渲染（互证）**。切片 6 只覆盖了 `band_gain`，本切片把互证补满到
> 全部 5 类算子。内核零改动（只加接线测试）。

- **互证测试 `test_all_operators_gui_matches_core_render`**：对 `band_gain / declip / declick /
  dehum / denoise` 逐个走真实 GUI 路径（`OperatorPanel.load_source → set_selection → _apply`），
  然后把面板**实际应用的那个 op 对象**抠出来，丢进新的 `EditSession` 用内核 `render` 复渲染，
  断言两产物 `soundfile` 读出的数组**逐位相同**（`np.max(|a-b|)==0`）。
  关键：比对的是 **GUI 自己构造的同一个 op**，不是重抄参数——所以 `declip` 自动读出的 `bandwidth`、
  `denoise` 自动记的噪声段是否和内核一致，这里会被直接验证；任何"GUI 与内核口径漂移"都会现形。
  - 素材取 `conftest.audio`（合成、答案已知）：`band_gain`→`cd_full`、`declip`/`declick`→`clipped`
    （削波/咔哒素材）、`dehum`→`cd_full`（f0=50）、`denoise`→`cd_full`（选区同时当噪声段学谱）。
  - 结果：**5/5 全部逐位相同**，覆盖规格第 81 行未完成的互证缺口。
- **门禁**：ruff 全绿（src/packaging/tests）；连线测试 **17**（切片 7 增 1，覆盖 5 类算子互证）；
  `tests/test_edit_*.py` 全过（无回归）；`palette_report --shot`（4 张 editor 截图）/
  `perf_probe` / `mem_probe` 均通过（不退化）；`playback.py` 变异分保持 1.000（46/46，0 测试缺失存活）。

### 新增（**编辑工作站 v1 收口·超量轮：可交互算子栈 + 批处理（CLI/坞）+ 算子预设：切片 8**）

> 接切片 7，"来跑超大得一轮"。把"栈只能看不能动"与"多文件只能一条条点"两处显见产能缺口打掉：
> 内核 `EditSession` 长出**可重排/移除/开关**的栈操作（非破坏、可撤销边界清晰），GUI 检视面板
> 升级为**可交互列表**；批处理从"单条 `--project`"扩到"清单驱动的多文件 `--batch`"，并配底部批处理坞；
> 算子面板支持**命名预设**存取。内核改动走"栈内重排对同域无副作用、开关不进撤销"两条已证判据。

- **内核 `core/edit/session.py` 可交互栈**：
  - `move(src, dst)` 重排（dst 钳到 `[0, n-1]`，`move(0,0)`/`move(99,0)` 返回 `False` 不误伤）；
    同域栈重排**逐位不变**（已证 `PHASE`/`canonical_order` 使同域渲染与顺序无关，`render` 字节恒等），
    所以重排只是"显示态"操作，安全；重排会同步重映射 `_disabled` 索引、**清空 redo**（结构变更不混进内容撤销）。
  - `remove_at(idx)` 移除（越界返回 `False`）；移除后渲染字节与"从未加过该项"一致（已测）。
  - `toggle_enabled(idx)` 临时开关某 op，**不进撤销栈**（区别于"真的删了"）；`_disabled` 集合 +
    `live_ops()` 只返回启用项，`render()` 尊重该蒙版；关掉后渲染字节 = 该项从不存在（已测逐位相同）。
  - 判据 `tests/test_edit_session.py` 增 `test_move_reorders_stack_and_is_noop_for_same_domain` /
    `test_remove_at_drops_op_without_undo_and_changes_output` / `test_toggle_enabled_excludes_op_from_render`。
- **检视面板 `ui/editor/inspector.py` 升级为可交互栈**：`QListWidget` 列出每项 `describe()`，
  选中即可 `上移/下移/移除/开关`；禁用项前缀 `[关]` 且用 `theme.p.text_disabled` 灰色；任何改动
  走 `stackChanged` → 外壳重新 `render` 并刷新审计（实时所见即所得）。`load_project` 校验 source
  与当前会话同源，**不静默换源**。`set_audit` 只挑可读字段（同切片 3 约定，不 dump 整本字典）。
  - 判据 `tests/test_ui_editor_wiring.py` 增 `test_inspector_toggle_disables_op_and_rerenders` /
    `test_inspector_remove_and_move` / `test_inspector_load_project_replaces_stack`。
- **批处理 CLI `edit_cli.py --batch <清单.json>`**：清单是 `Project` 记录数组（每项=一个小工程，
  复用已验证的 `Project.from_dict` + `render`）；`source` 在作业项里给（批处理要枚举一堆输入）、
  `dest` 必填；支持 `subtype`/`overwrite`/`dither`。**逐项互相隔离**：单条失败（`kind` 不认识、
  源读不了、写出失败）就地接住、记成该项失败、继续跑其余；末尾汇总"几项成功/几项失败+原因"。
  退出码：全成功=0；**清单本身不可用**（读不了/不是数组/某项缺 `source`/`dest`）才=2（结构性缺陷整体拒）。
  `--batch-help` 打印清单格式文档（含退出码语义）。
  - 判据 `tests/test_edit_cli.py` 增 `test_batch_runs_all_jobs_and_isolates_failures` /
    `test_batch_rejects_non_array_or_missing_fields` / `test_batch_help_exits_zero`；
    连线 `tests/test_ui_editor_wiring.py` 增 `test_batch_panel_manifest_and_run` /
    `test_batch_cli_accepts_project_style_ops`。
- **算子预设 `ui/editor/ops.py`**：`current_preset()`/`apply_preset(cfg)`/`save_preset(name, path)`/
  `load_presets(path)`；命名预设（JSON）存当前算子种类+参数，载入即回填面板；命令面板增
  `("存算子预设"/"载算子预设")`。`dehum` 预设**保留 f0**（不把自动检测的定义域丢掉）。
  - 判据 `tests/test_ui_editor_wiring.py` 增 `test_operator_preset_roundtrip_and_load` /
    `test_operator_preset_dehum_keeps_f0`。
- **底部批处理坞 `ui/editor/batch.py` + `shell.py`**：`BatchPanel`（源/目标/操作表 + 增/导入工程/清空/
  导出清单）装进 `BottomDockWidgetArea` 新 dock；`runRequested` → 写临时清单 → 调 `--batch`；
  作业在加入前即经 `op_from_dict` 严格解析（未知 kind/缺字段当场拒）。
- **截图留档**：`packaging/ui_shot.py` 增 `editor_stack`（连堆 3 个算子 + 往批处理坞塞一条作业，
  验证可交互栈与底部坞都画得出）；`editor`/`editor_light` 复拍。三张均过 `palette_report --shot` 面积配额
  （高彩 ≤5%、中性占绝大多数），落 `shots/slice8/`。
- **门禁**：ruff 全绿（src/packaging/tests）；连线测试 **24**（切片 8 增 7：检视交互 3 + 预设 2 +
  批处理坞/CLI 2）；`tests/test_edit_*.py` + `tests/test_ui_editor_*.py` **共 341 条全过**（无回归）；
  `palette_report` / `perf_probe` / `mem_probe` 均通过（不退化）；`playback.py` 变异分保持
  1.000（33/33 有效变异体全杀、0 测试缺失存活、7 项诚实名等价登记）。
  ⚠ 一处设计修正：原 `--batch` 把"缺 `source`/`dest` 的单条"当逐项失败（RC 0），与帮助里
  "缺字段 ⇒ 2" 自相矛盾，已改为**前置结构校验整体拒（RC 2）**，运行期故障才走逐项隔离（RC 0）。

### 新增（**编辑工作站 v1 再推进：点击定位 + 撤销/重做 + 真实音频导出 + 选区测量：切片 6**）

> 接切片 5。把规格里「还差的硬交互」打完：时间轴点击定位播放头、撤销/重做（键盘 + 命令面板 +
> 走带区）、把当前操作栈渲染成真实 wav 导出（A/B 选源导出）、框选时实时 t/f 测层读数；并补齐
> 选区态 / 暗亮切换两套截图。内核零改动（本切片只动 UI 层 + 测试 + `packaging/ui_shot.py`）。

- **点击时间轴定位播放头**：`EditorView` 新增 `seekRequested(t)` 信号；鼠标在非选区工具下单击
  （位移 <3px 区别于拖拽）→ `seekTo(t)`（秒数先夹到 `[0,dur]`）→ 上层 `_on_seek` 调
  `engine.seek`、若正在播则续播、回写走带条时间读数。接线测试 `test_click_seek_positions_playhead`
  实测 `seekTo(1.5)→engine.position==1.5`、`seekTo(-5)→clamp 0`。
- **撤销 / 重做**：走 `EditSession.undo/redo`（非破坏栈，撤销到空栈渲染逐位恒等，切片 4 已钉）。
  两套入口都接上：**键盘** `Ctrl+Z` / `Ctrl+Y`（`EditorWindow.keyPressEvent`，与 `Ctrl+K` 命令面板
  互不抢键）；**命令面板** 新增「撤销 / 重做 / 选段试听 / 导出项目记录 / 导出处理后音频 / 导出 A/B 选源」。
  接线测试 `test_undo_redo_keyboard` 实测：应用 band_gain→1 op、undo→0、redo→1、空栈 undo 不崩。
- **真实音频导出**：
  - 「导出处理后音频」直接调 `EditSession.render(path, overwrite=True)`，产物可独立 `sf.read`、
    帧数与源一致、字节确有改动（接线测试 `test_export_audio_renders_real_wav` 钉：dest 存在 +
    `audit["domain"]=="band"` + `audit["frames"]` 等于源帧数 + `audit["dest"]==path`）。
    ⚠ `band_gain` 是时域渲染，审计走 `domain/ frames/ dest`，**不**含修复类才有的 `changed_samples`——
    测试断言改用存在的字段（不假装字段）。
  - 「导出 A/B 选源」：A=原曲缓冲、B=已渲染预览 wav（若 `self._ab_b` 为真）。
- **选区测量浮层**：框选 / 拖动时 `EditorView._draw_measure` 在画布上叠加 t 窗（Δt 起止 + 跨度）
  与 f 窗（Δf 起止 + 倍率）读数（青色，可审计、不靠肉眼）。接线测试
  `test_measurement_overlay_text_during_selection` 实测 `_measure_txt` 文本含 `Δt` 与 `Δf`。
- **截图补齐（规格 §截图留档）**：`packaging/ui_shot.py` 新增 `editor_sel`（选区 + 测层浮标）、
  `editor_light`（暗/亮切换）两套，离屏渲染；与既有 `editor` / `editor_ab` 一并过 `palette_report
  --shot` 面积配额（高彩 ≤5%、中性≈99.97%，全过，不退化）。
- **门禁**：ruff 全绿；连线测试 **16**（切片 6 增 4：点击定位、撤销/重做、真实导出、测层）；
  既有 `tests/test_edit_*.py` 全过（无回归）。
  **`playback.py` 变异分由切片 5 的「部分 1.000（33/33 子集）」补成完整 1.000（46/46，0 测试缺失存活）**：
  把该靶标跑满全部 58 候选，原基线实为 **0.882**（6 存活全在 X-run/EOF 近似区）。
  其中 5 条按**可证明等价**补登（与已白名单的 `434:cmp:>->>=` 同属时序阈值家族 + EOF 分支
  `>=`/advance 夹位逐位相同）；剩 1 条真缺口——`set_source_data` 的帧光标 `position*fs`——
  新增 `test_set_source_data_sets_cursor_from_proportional_position`（A/B 换缓冲后光标按比例重置、
  拉一帧从此处推进）钉死。这是切片 5 遗留的量具口径债，本切片顺手还清。

### 新增（**编辑工作站 v1 收口：A/B 真实切源 + 键盘选区 + 选段试听 + 导出 + 门禁三件套：切片 5**）

> 接上两条。把规格 §门禁与 §范围里**还差的硬骨头**全打完：A/B 之前只切标签没切播放源、
> 选区不能键盘微调、选段试听没接 loop、项目记录没导出；并补齐 `mutation/palette/perf/mem`
> 四道门禁与截图。

- **A/B 真实切换播放源**：`PlaybackEngine.set_source_data`（原地换缓冲、不重建 miniaudio 设备、
  避免爆音/丢帧，按"比例"夹播放头）；shell 把渲染预览 wav 缓存为 B 面，切换时换缓冲、
  切回 A 面原曲**逐位恢复**（接线测试 `test_ab_switch_swaps_real_buffer` 实测 A/B 字节差异正确）。
  之前的"仅状态提示"已替换为真实可听的 A/B。
- **键盘选区**：`EditorView.keyPressEvent` 支持方向键微调、Shift+方向扩展频率窗（步长取视野 2%，
  可见即所调）；无选区时方向键先建默认选区（验收 §"键盘可建/可调选区"）。
- **选段试听**：走带条新增「↻ 试听选区」，`selection→engine.set_loop(t0,t1)` 并从 t0 起播、
  播完停在 t1（REAPER 式框一段只听这段）。
- **导出项目记录**：检视面板「导出项目记录」写 `Project.to_dict` JSON（源路径 + 操作栈，可复算）；
  接线测试 `test_export_project_roundtrip` 验证 `from_dict` 还原同一栈、字典逐位相同。
- **门禁三件套（实测跑过）**：
  - `mutation_probe --only playback.py --strict`：**0.300 → 1.000（33/33 部分子集，0 测试缺失型存活）**。
    ⚠ 该轮是 `--max-mutants` 截断的**部分**结果，不是全量；完整 58 候选的真实基线是 0.882，
    缺口在切片 6 还清（见切片 6 门禁条目）。注册 playback 为靶标，补 20+ discriminating 断言
    覆盖状态机边界 / `DeviceInfo.__eq__`
    字段区分 / 帧生成器单 yield 契约 / 欠载近似 / `set_source_data` 比例 / `_start_device` 设备选择
    （后者用假 PlaybackDevice monkeypatch，不真开声卡）；7 条经核查登记为**等价**（浮点边界、
    零宽补零 no-op、哑时钟自旋守卫、时序非确定阈值等，每条写清理由）。顺带**修掉一个真 bug**：
    原 `_frame_generator` 双 yield 导致每两次回调返回空帧（真实后端会爆音），改成规范单 yield。
  - `palette_report --strict`（生成配色 0 不达标）+ 对 `shots/editor_v1/{editor,editor_ab}.png`
    量面积配额（高彩 ≤5%、中性 99.88%，均通过）。
  - `perf_probe --strict`（交互在帧预算内）、`mem_probe --strict`（图元不累积、缓存有界、清理回落）。
  - 截图：`packaging/ui_shot.py` 新增 `editor` / `editor_ab` 两套，离屏渲染默认三栏 + 应用去削波 +
    A/B 开启态。
- **门禁**：ruff 全绿；播放 25 → **43**（切片 5 增 18）、外壳 9、接线 13（切片 5 增 6）= 65 新用例；
  既有 `tests/test_edit_*.py` 全过（无回归）。

### 新增（**项目记录 CLI + 导出抖动；decrackle 判据先行后挂账**）

> `docs/editor_roadmap.md` 第十四步。判据 `tests/test_edit_cli.py`（+6）、
> `tests/test_edit_dither.py`（11）；`docs/theory.md §12.17`、`docs/editor_research.md §17`。

- **复合操作终于能走 CLI**：`--save-project` 写本次实际执行的项目记录（JSON 版本 1），
  `--project` 按记录**链式渲染**——规范顺序、审计记 `stages` / `reordered_from`，
  并逐阶段打印"操作 / 算法 / 覆盖帧数 / STFT 残差"。记录里的 `source` 必须与命令行
  一致（**不静默换源**）；坏 JSON / 未知 kind / 参数混用都拒绝（退出码 2、不 traceback）。
  判据：记录往返**字节相同**、源不一致拒绝、空栈恒等、链式阶段打印、参数互斥。
- **导出抖动（P0 补齐）**：`render(..., dither=True)` + CLI `--dither`。TPDF **+0.5 LSB
  偏置**——实测 libsndfile 的 float→PCM 是**截断**（0.0001 → 3 LSB），不加偏置会留
  **−0.499 LSB 直流**；加上后实测均值 **0.0013 LSB**、RMS **0.501 LSB**、最大 **1.277 LSB**，
  两次渲染**逐字节相同**（RNG 固定种子 0）。⚠ 全文件重量化 ⇒ 未编辑处不再逐位不变，
  所以只作导出选项；只对整型 subtype 有效（浮点目标上要求抖动 ⇒ 明确拒绝）。
  审计记 `dither` / `dither_lsb`。抖动区变异 **22/22**（位深表、TPDF 公式、偏置、
  种子、审计舍入逐条钉住）。
- **decrackle：判据先行，本轮不落地**：新增 `corpus.crackle_train`（位置/幅度真值）+
  `packaging/decrackle_probe.py`。实测：小脉冲（0.02–0.2）在现有两点残差上
  **TPR = 0%**（残差尺子被 17.6 kHz 合法谐波主导：干净素材 `max|r|=0.246`、`s=0.087`，
  小脉冲只给 γ=0.23–2.5 < α=5）；α=3 时 TPR 28–35% 但 precision 58–67%、ΔSDR 仅 +3 dB 量级；
  **LPC(64) 残差** precision 1–17%、ΔSDR **−14…−0.2 dB** ⇒ 否决。
  ⇒ 需要新检测量具（时频离群点 / 稳健局部尺度），语料与探针留给下一轮
  （`docs/editor_research.md §17`）。语料区变异 **35/35**（33 杀 + 2 等价）。
- **门禁**：ruff 全通；全量 **1157 passed / 1 failed**（仅已知 flaky toast）。

### 新增（**P3 第四个修复算子：去噪 —— 噪声段是必填输入，盲估计被实测否决**）

> `docs/editor_roadmap.md` 第十三步。`packaging/denoise_probe.py`：四候选 × 七场景 + 参数扫描；
> `docs/theory.md §12.16` / `docs/editor_research.md §16`；判据 `tests/test_edit_denoise.py`（23 条）。

- **先量盲估计（失败）**：按"最低 10% 帧"估噪声谱，在 **+10 dB SNR 的持续音**上
  ΔSDR **−8.7 dB**、能量比 −16 dB（把内容当噪声减掉——持续音每帧都响，"安静帧"也含音）。
  ⇒ **噪声段是必填输入**：`DenoiseOp(t0,t1, noise_t0, noise_t1, ...)`，CLI `--noise-at`；
  不给就拒绝（与 `--hum-f0` / `--bandwidth` 同一条纪律）。
- **四候选对比**（噪声谱从前 0.5 s 纯噪声段学；ΔSDR，白 +10/0/−5、粉 0）：
  谱减 +10.3/+10.4/+10.7/+11.3，**Wiener-DD +11.7/+12.7/+12.5/+14.0**，
  MMSE-LSA +11.3/+12.5/+12.6/+13.3，oracle +12.1/+13.2/+13.8/+14.2。
  纯噪声压制：**Wiener −19.9 dB**、MMSE −18.5、谱减 −15.3；musical-noise 代理
  Wiener **+0.01 dB** 最小。MMSE 只在 −5 dB 上多 0.07 dB ⇒ **不开第二分支**。
- **参数由扫描定（不是手感）**：n_fft **4096**（1024/2048/4096 的 ΔSDR +8.2/+10.8/+12.6）、
  α_dd **0.98**（0.90/0.95/0.98/0.99 ⇒ +11.1/+12.2/+12.6/+12.4、压制 −15.6/−18.6/−19.9/−20.0）、
  压制上限 **20 dB**（10/20/30 ⇒ ΔSDR +8.5/+12.6/+13.2，但伪影 +0.01/+0.01/+0.23 dB）。
- **瞬态守卫两种候选都被否决**：① 帧总能量 >2/4× 中位数：单采样脉冲只占帧能量千分之几
  ⇒ 永不触发；② "多少 bin 的 γ>4" 超过 5%/10%：持续音本身的谐波就占 ~4% 的 bin
  ⇒ 大量正常帧被误判、去噪停摆（ΔSDR +1.2 dB）；20% 又抓不到脉冲。
  ⇒ **不加守卫**，把"瞬态会被削软（−13.6 dB 峰值）"写进 CLI/审计；
  去噪在 `render.PHASE` 里排**修复算子最后**（`declick → declip → dehum → denoise → band_gain → gain`）。
- 落地 `core/edit/denoise.py` + `DenoiseOp` + render 的 denoise 路径 + CLI
  `--denoise/--noise-at/--reduction-db`；审计报 `noise_reduction_measured_db`（同一套增益
  作用在噪声段上实测）、`energy_ratio_measured_db`、`noise_frames/processed_frames`、
  `alpha_dd/n_fft/hop/g_min_db`。
- **边界如实写**：改整段选区（逐帧逐 bin 增益）；非稳态噪声没做（`N` 是平均谱）；
  噪声型内容不可分离（那类素材能量掉 ~10 dB 是性质）；噪声段必须 ≥ 一个窗长。
- **变异复跑**：`denoise.py` **25/25 = 1.000**（等价 1 条：`max(P,1e-30)→0` 只在 P=0 的
  bin 上有差别，且被增益地板托到同一值）；`ops.py` 新增区 6/6；`render.py` 新增区
  **定向** 19 杀 + 4 等价、0 存活（判据子集 = denoise + chain + render；旧区未改不重跑）。
- 判据：去噪 23 条 + 语料噪声 5 条 + CLI 4 条 + 链式顺序 1 条；全量 **1135 passed**
  （唯一失败是已知 flaky `test_ui_material::test_burst_does_not_leak`）。

### 新增（**复合操作：顺序实测 → 规范顺序 + 链式渲染**）

> `docs/editor_roadmap.md` 第十二步。`packaging/op_order_probe.py` 量顺序；
> `docs/theory.md §12.15` / `docs/editor_research.md §15`；判据 `tests/test_edit_chain.py`（8 条）。

- **先量**：三种缺陷同时存在且有真值的素材 × 三个修复算子的**六种排列**：
  SDR 极差只有 **0.44 dB**（顺序不是"生死攸关"），但**咔哒残差差 2.2 倍**
  （0.1538 vs 0.3448）、**嗡压制差 1.75 dB** ⇒ 需要有顺序，但不是随便排。
- **机制**（本轮新知识）：**去削波的带限投影会把孤立脉冲抹宽**（POCS 的低通投影对 δ 缺陷
  就是扩散）⇒ 之后两点预测残差抓不住 ⇒ **脉冲必须先修**；去嗡是 LTI、改动全部样本 ⇒ 最后；
  电平类最后（自动削波电平与全段 MAD 尺子都从数据里读）。
- 规范顺序 `declick → declip → dehum → band_gain → gain`（`render.PHASE`，同相位内稳定）；
  被规范化时审计记 `reordered_from`，逐阶段记 `stages`（算法/生效帧数/峰值）。
- 链式渲染：每步一个 `FLOAT` 中间文件；对 PCM16/24 与 FLOAT 源**不引入额外量化**，
  但阶段浮点结果舍入到 float32（≈ −144 dBFS）——**单算子路径仍是全 float64**，
  判据钉住"0 dB 增益 + 去咔哒"与直接路径**逐采样相同**（比的是音频采样，不是容器头字节）。
- **挂账**：CLI 仍只接受一个算子（复合走 Python 项目记录）；同域多选区重叠仍拒绝。
- **变异复跑**：`ops.py` 新增的 schema 解析 **22/22 = 1.000**。
  ⚠ 第一次跑是 14/22 —— 因为 `TARGETS` 的 `ops.py` 子集**没挂判据所在的会话文件**
  （`tests/test_edit_session.py`），8 条"假存活"；这条坑（子集必须包含判据文件）
  已在探针注释里第二次记录。补挂后 0 存活。
  另外补了两条真缺口：`source=""` 也算缺来源（`or→and`）、`DeclickOp(max_run=1)` **合法**
  （`<`→`<=` 会把它拒掉）。
- **变异复跑（链式区，定向）**：`render.py` 的 dispatch + 链式渲染区（130–250 行）
  **7/7 = 1.000**（杀死 5、等价 2）。⚠ 这是**定向**复跑（判据子集 = chain + render），
  不是整份 126 个候选：旧区自上一轮实测"无存活"后**代码未改**（只有行号下移），
  全量重跑要数小时且不增加信息。`TARGETS` 的 render 子集已挂 `tests/test_edit_chain.py`。
  过程中抓出一条**真判据缺口**：`PHASE` 的 `band_gain=3` 改成 `4` 会与 `gain` 平局，
  稳定排序保留用户反序 ⇒ 新增 `test_band_gain_phase_precedes_gain`。
  等价登记 2 条（`gain 4→5`、未知兜底 `99→100`，理由都是"只关心相对顺序"）；
  `render.py` 旧等价体的行号已按插入 `_render_chained` 后的位置**逐条对齐**。

### 新增（**撤销/重做 + 项目记录：判据全部逐位**）

> `docs/editor_roadmap.md` 第十一步。`core/edit/session.py`（`EditSession`）+
> `ops.op_from_dict` / `Project.to_dict|from_dict`；判据 `tests/test_edit_session.py`（11 条）。

- **撤销到空栈 ≡ 从未编辑**：与空栈项目渲染的 **sha256 相同**；**重做后与撤销前逐字节相同**。
- 栈语义：**新操作清空 redo**（不做分叉）；空栈的 `undo()/redo()` 返回 `False` 不抛异常；
  `project()` 返回**拷贝**（外部往 ops 上追加不会穿透回会话）；审计只含当前栈。
- **项目记录**（"可审计的修复"能存下来再跑）：五类操作 `to_dict → op_from_dict` 逐字段相等
  （2026-10-26 起为**六类**，加去噪）；
  `to_dict → JSON → from_dict` 后**渲染同样的字节**。解析严格：未知 kind / 多字段 / 少字段 /
  版本不符**都拒绝**（宽松解析会让"写了 `q=10` 却被悄悄丢掉"静默发生）。
- 边界如实写：**撤销历史不落盘**（会话状态而非作品状态）；不存"改过的音频"——
  撤销的代价是**重渲染**，取决于最近选区的大小。

### 验证 + 修复（**P0 往返测试补上 FLAC / AIFF；`FLOAT → .flac` 的半个目标文件**）

> `docs/editor_roadmap.md` 第十步。判据 `tests/test_edit_formats.py`（14 条）。

- **七种组合全过**（`wav/flac/aiff × PCM_16/PCM_24` + `wav FLOAT`）：选区之外**逐位不变**、
  选区内实测 **−6.000 dB**（容差 0.02）、子类型原样保留；**0 dB 恒等渲染整文件逐位不变**；
  渲染前后**源文件 sha256 不变**；去嗡在 FLAC 上同样只动选区内。
- **修掉一个真问题**：源 `FLOAT`（WAV）+ 目标 `.flac` 且未显式给 subtype ⇒ 继承 FLOAT ⇒
  libsndfile 在**目标已建了一半**之后才抛 `Invalid combination of format, subtype and endian`。
  新增 `render._check_subtype`：用 `sf.check_format` **在动目标之前**拒绝，
  信息里给修法（`subtype="PCM_24"`），判据断言**拒绝路径不留下半个目标文件**。

### 新增（**去嗡的 `f0` 自动检测** —— "稳态"才是判据，谐波族不是）

> 上一轮的挂账项。`packaging/hum_detect_probe.py` 量口径；`docs/editor_research.md §14`、
> `docs/theory.md §12.14`；判据 `tests/test_analyzer_hum.py`（**30 条**：8 条端到端 + 22 条打桩谱图）
> + CLI 侧 3 条；**变异复跑 76/76 = 1.000**（等价 4 条，理由已登记）。

- **先证伪直觉**：合成 55 Hz 衰减贝斯（6 次谐波 + 包络 + 揉弦）的 excess 是 **34.7 dB**，
  比 −30 dB 的嗡（14.0/8.1/6.2）还高 ⇒ **"三条线"不足以判嗡**。
- **实测判据**：帧间稳定性（嗡 ≤11.3 dB / 乐音 ≥495 dB）+ **安静帧里仍在**
  （嗡 ≥3.2 / 乐音 ≤−3.4）。三个门限 `4 / 30 / 0 dB` **都落在实测空隙里**，
  其中 `0 dB` 取的是**自然边界**（"安静帧里仍高于邻域"）。
- **亚 bin 插值**：2.93 Hz 频率格下不插值会把 60 Hz 报成 61；三 bin 抛物线插值后
  实测 50.02 / 60.04 Hz。窗长随采样率缩放 ⇒ 频率格恒定（低采样率下也可用）。
- 落地 `core/analyzers/hum.py`（`detect_hum`）+ CLI `--hum-auto`（与 `--hum-f0` **互斥**）+
  `DehumOp.f0_source ∈ {given, auto}` 进审计。**证据不足 ⇒ 拒绝**（退出码 2、
  打印 excess / 帧间稳定 / 安静帧三项实测），绝不偷偷填一个基频。
- **边界**：稳态低音持续音（管风琴、持续贝斯）与嗡**不可区分**——这是判据的性质，
  不是 bug；所以自动路径只"发现并报出证据"，是否动手由用户确认。
- **仍未做**：非稳态嗡（漂移/抖动）、与其它修复算子的叠加顺序。

### 新增（**P3 第三个修复算子：去嗡** —— 判据先行；Q 由频率漂移表定，不是手感）

> `docs/editor_roadmap.md` 第八步。`packaging/dehum_probe.py`：3 族候选 × 2 素材 × 50/60 Hz ×
> 嗡 −20/−30 dB，另加频率漂移扫描。实测与检索在 `docs/editor_research.md §13`、
> `docs/theory.md §12.13`；判据在 `tests/test_edit_dehum.py`（12 条）+ CLI 侧 5 条；**变异复跑 18/18 = 1.000**。

- **候选对比（SDR 相对真值，f0=50、嗡 −20 dB）**：带限白噪 因果 24.7 / 零相位 26.8 /
  零相位 Q=40 29.6 / **STFT 置零 ±24 Hz 21.0**；谐波音 27.4 / 33.3 / 36.1 / **46.9**。
  ⇒ **STFT 置零否决**（只在"嗡所在 bin 本来没内容"的素材上占优，宽带素材上崩——
  与 §7 "软掩码不进内核"同型）；**零相位 > 因果**（离线修复没有理由付相位失真）。
- **Q=10 的依据**：陷波宽 = `f0/Q`。嗡漂到 51 Hz、滤波器仍按 50 Hz 设计时，
  Q=10 仍压 **17.1 dB**、Q=20 只剩 8.3、Q=40 只剩 **2.9**（≈ 没去）。
  ⇒ 默认取**对输入误差鲁棒**的那个；SDR 代价 ~2.8 dB，小于"漏掉嗡"的后果。
  ⚠ ±1 Hz 是**工程假设（无外部出处）**。
- **零相位判据**：脉冲响应左右对称（实测最大不对称 2.7e-9，因果对照 3.9e-3）。
  ⚠ 不能拿"峰的位置"判——陷波 `b0≈1`，因果滤波的峰也在原位（实测踩过）。
- 落地 `core/edit/dehum.py` + `DehumOp` + render 的 dehum 路径 + CLI
  `--dehum/--hum-f0/--hum-q/--hum-harmonics`（`--hum-f0` **必须给**：基频是可测输入，不自动猜）。
  审计报 `harmonics_used / skipped_harmonics / suppression_db（实测，±5 Hz 带内增益）/
  changed_samples / energy_ratio_measured_db`。
- **效力边界（如实写）**：陷波是 **LTI ⇒ 整段选区都被改动**（不是"只改嗡样本"）；
  干净素材上 Q=10 的改动量 = SDR **27.6 dB**、能量 −0.017 dB ⇒ **收益由嗡的能量占比决定**。
- **挂账**：f0 自动检测（需先立"嗡线 vs 音乐基频"的判据，否则会把低音提琴当嗡清掉）、
  非稳态嗡（漂移/抖动）、与其它修复算子的叠加顺序。

### 修复（**真峰值分块扫描：第 2 块之后的 span 全部是 `None`** —— 新判据抓出来的真 bug）

> 发现方式：给 `dsp.py` 补变异判据时，"跨块 span"这一条直接把它照出来了
> （`packaging/mutation_probe.py` 的 `dsp` 子集 × `tests/test_dsp_edges.py` / `test_truepeak.py`）。

- **根因**：`true_peak_scan` 里 `seg` 是**相对本块起点 `pos`** 的切片，而段内索引写成
  `max(s0, pos) * TP_OS - lo`——`lo = (pos - a) * TP_OS` 是**相对读窗口起点 `a`** 的。
  两套坐标混用，第 2 块起 `i0 - lo` 大于 `seg` 长度 ⇒ 空切片 ⇒ 该段 `span_pk` 永不更新。
- **影响面**：`context.true_peak_scan()` 用默认块 **2^21 帧**（44.1k 下 ≈ 47 s）——
  **任何长于约 47 秒的文件，timeline 的逐段真峰值都会静默变成 `None`**
  （`loudness` 的整轨读数不受影响：它走 `global`）。
- **为什么旧测试没抓到**：所有 span 用例的素材都短于一个块（`a == pos == 0`，两套坐标重合）。
- **修法**：段内索引改成"相对 `pos`"（`(max(s0,pos) - pos) * TP_OS` 与 `seg[i0:i1]`），
  与 `seg` 同一坐标系。
- **回归判据**（`tests/test_truepeak.py::test_true_peak_scan_spans_across_blocks_match_single_block`）：
  200k 帧、3 个尖峰放在 3 个块里，**同一批 span 的多块读数必须与单块逐段一致**
  （`[-0.92, -4.44, -10.46] dB`）。修前第 2/3 段是 `None`。

### 新增（**判据补全：`causes.py` 33 条变异缺口** —— 粗档强度 + 门限等号侧）

- 新文件 `tests/test_causes_edges.py`（**12 条**，纯 dict 输入，不跑测测量层）。
- **粗档强度**：0.8（容器级失配，文案必须印 `96.0kHz`）/ 0.9（位级硬证据，`PCM_` 剥掉、位数保留）/
  0.5（统计吻合：中间带 / 低频隆隆 / 平缓滚降 / 刻意带限各一条）。
- **候选的触发边界**：中间带 `15 ≤ slope < 24` 与两侧**都含**（恰 24 ⇒ 硬砖墙 conf=1.0、
  恰 15 仍是中间带）；母带限幅 `clip > 0.005%` / `lra < 3.0` 两条**严格**且分开取；
  广播版 `low_pct <= 5.0` **含**、`wd < −18.0` **不含**；假立体声 `corr >= 0.98` 与
  `wd <= −30.0` **都含**。
- **用途矩阵**：`k >= 20 / >= 16`、`low_pct <= 5 / >= 60`、`wd < −18`、`um >= 15`、
  `lra < 3 / dr < 6 / lufs > −9`、`tp > 0.0`（**0.00 dBTP 不算超满刻度**）全部取等号两侧各一次；
  `bit_verdict or ""` 的 `or` 用**"无法统计"**钉住（改 `and` 会让它永远读不到）。
- 等价体登记 1 条：`line 80` 的 conf 除法（只在 `slope ≥ 24` 时求值 ⇒ 两版都被 `min(1,·)` 夹到 1.0）。
- **复跑验证**（`--only core/analyzers/causes --max-mutants 400 --mutant-timeout 180`）：
  **79/79 = 1.000**，存活 0（等价 3 条）。

### 新增（**判据补全：spectrum/verdict 残余** —— "索引 ±1" 这一族终于也被量出来了）

> 2026-10-21 的第一次补全后 spectrum+verdict 仍有 7 条存活（全在 spectrum）；
> 本轮补了 **3 条判据**把它们全部钉住，另外把 verdict 的两条短路从"单方向"改成"双方向"。

- **稀疏对数网格**（16 点，20 Hz…24 kHz）把中位数窗缩到 1–2 个点 ⇒ 切片端点 `+1`→`-1`
  的五处（粗搜索 below/above、守卫 pass/floor、第二步通带窗）全部可区分：
  原实现读 `(5812.66 Hz, 60.0 dB/oct)`，任一处改 `-1` 都**退回奈奎斯特**。
  ⚠ 真实谱不是这种形态——这条判据钉的是**下标算术**（真实网格上 ±1 点位移 < 0.1 Hz）。
- **粗搜索上窗 `1.6`**：5 kHz 浅台阶（−10）+ 8 kHz 深坑（−70），`min_drop_db=62` 时
  1.6 倍窗只看到 −10 ⇒ 无候选（回落奈奎斯特）；改成 2.4 会报出 ~5 kHz 的**假截止**。
- **`band_balance` 的两条边界各测一次**：内部点 + sub 下边界（20）+ air 上边界（20000），
  `>=` 丢下边界（sub 0%）、`<` 收上边界（air 25%）——都杀得掉。
  ⚠ 若所有点都压在边界上，两条变异会让每个频段同时多/少一个点、占比仍是 1/7（实测踩过）。
- **守卫等号侧**：`pass_lvl - floor_lvl >= min_stop_db`，同一素材取 `min_stop_db=10`
  恰好相等 ⇒ 必须算通过（改 `>` 就整体回落奈奎斯特）。
- **verdict 的两条短路改成双向**：`and`→`or` 用"等级 ok 但文案非空"的格子（`lufs` 的
  "响度战"提示**不改变等级**）；`!=`→`==` 用"削波降级"的格子（文案必须进结论）。
- **复跑验证**：`--only core/analyzers/spectrum` ⇒ **94/94 = 1.000**；
  `--only spectrum --only verdict` 的 189 条里 verdict 部分 **95/95 = 1.000**（等价 14 条）。
  ⇒ 四个模块（dsp / causes / spectrum / verdict）**变异分数全部 1.000**。

### 新增（**判据补全：`dsp.py` 116 条变异缺口** —— 按函数逐组填，打桩喂格子）

> 回填整体复跑里**最大的一块**（`dsp` 116 / `causes` 33 / `verdict` 31 / `spectrum` 29）。
> 新文件 `tests/test_dsp_edges.py`（**36 条**）；`TARGETS` 的 `dsp` 子集已挂上。

- **门限积分响度**：`10·log10` 的系数（改成 11 ⇒ 相对门限挪 2.7 dB、读数 26.71→29.31）、
  默认读块 `1<<20`（签名钉住）、`hop == 1` 是**合法**采样率（只有 0 才拒）、
  `si` 恰好 **4** 块的等号、以及**跨读块续接**（`acc_n` 的 `hop ± acc_n`：
  素材在 chunk 边界后 800 采样放突发，改符号会让后面的块全丢 / 该步电平压低）。
- **谱特征**：`10^(db/10)` 的底数（0/−10 dB 两点 ⇒ 质心 90.9 Hz）、滚降点 **0.85/0.95**（九等分 + 尾 ⇒ 下标 7/8）、
  平坦度的 `p > 0`（−inf 格不入几何平均 ⇒ 2.0）、`+1e-30` 的**加性**（−400 dB ⇒ 1.0）、
  5 位舍入（0.94281）、噪声底 **第 5** 百分位。
- **立体声**：M/S 的减法定与 `10·log10(side²/mid²)`（L=1/R=0.5 ⇒ −9.5 dB）、
  `corr` 的分子/分母（1/0 交替 ⇒ 0.707 + 3 位舍入）、**恰好 0.3 / −0.1** 的单声道兼容性等号侧
  （13+1 / 7−1 的 20 周期，均值逐位等于字面量）、完全单声道 ⇒ `width_db == −300.0`（epsilon 位）、
  4M 采样上限（`inspect.getsource` 钉住——行为判据要 ~400 MB 峰值，不合算）。
- **相位场**：`hop = 3//4`（8192 采样 ⇒ 4 帧、帧距 3072/fs；用 `t` 与 `noverlap` 双钉）、
  静音 ⇒ ρ 全 0（不是 nan）、**弱侧 −33 dB 无定义 / −27 dB 有定义**（30 dB 判据的两侧）。
- **局部化反相**（打桩 `signal.stft` / `signal.lfilter`：直接喂时频格，"功率"成为精确输入）：
  分档门限 **1% / 10% / 50% 的等号侧**（100 个等功率格 ⇒ 占比恰 k/100）、
  `anti_energy_pct` 3 位（1/7 ⇒ 14.286）、**ρ 恰好 −0.9 不算反相**（常数 `mov` ⇒ 2·(−0.9)/2 逐位 −0.9）、
  无格子 ⇒ `−300.0 dB`、`active` 的 **40 dB 门限**（峰值 2.0、反相格 4e-4 ⇒ 0.02%）与
  **恰好等于门限**（`ANTI_FLOOR_DB = 10·log10(4)` ⇒ 阈值逐位 0.5，格功率 0.5）、
  份额分母的 **1e-30 是有效量**（单格 1e-29 ⇒ 90.909%）、`frame_peak` 的 **1e-30** 决定
  "极小声的格子算不算"（1.05e-33 vs 阈值 1.1e-33）、**不等幅反相**（0.5/−0.25 ⇒ ρ=−0.8 不报，
  分母写减号会误报"严重"）、静音早退哨兵 `−120.0`。
- **直流**：`n <= 0` 守卫、`sum/n`、6 位小数、`20·log10(|dc|+1e-12)`（dc=0 ⇒ **−240.0**）。
- **过采样**：`kaiser, beta=10`（判据取"**不是** 15"：两者差 8e-7，1e-12 容差能分开）。
- **真峰值扫描**：`TP_TRIM = 64` 的前后借窗（打桩 `sf.read` 记录 `(start, frames)`）、
  减半重试下限 **65536**（桩：>65600 就抛 ⇒ 原实现 frames_read=65536，改 1<<17 ⇒ 0）、
  `20·log10(pk + 1e-12)`（1e-12 尖峰 ⇒ −233.98；改成减 ⇒ −inf、改成 0 ⇒ −240）。
- **等价变异体登记 12 条**（`EQUIVALENT`，每条给了可证明的理由）：三处 `+1e-30` 的加/减与 `1e-30 -> 0`
  （z ≥ 0、z=0 时都低于绝对门限）、`total < hop*4` 的 `*->/`（与末尾 `si<4` 同输出）、
  `si < nsteps` 的等号（nsteps 个满块消费完后再也凑不满一个 hop）、
  `corr` 分母的 epsilon（全零时 0.0 / −0.0 比较相等）、L/R 互换（ρ 与 undef 对称）、
  `total <= 0`（空文件循环不执行）、`i1 > i0`（空切片）。
- **复跑验证**（`--only core/dsp --max-mutants 400 --max-seconds 3000 --mutant-timeout 180`）：
  **279/279 = 1.000**，存活 **0**（其中 **6 条**是"超时判死"——它们让子集跑过 180 s，
  属于非终止/极慢型；这条口径与 `_run_subset` 的超时守卫一致）。
  即：本轮没有"测试缺失"型存活，等价体白名单在这轮**未被用到**（没有存活需要解释）。

### 新增（**判据补全：spectrum/verdict 残余 11 条** —— 前一轮"同时取等号"漏掉的那类）

- `spectrum`：`order = 30`（注册顺序）、**恰好 −60.0 dBFS 不是静音**、
  粗搜索 `>= min_drop_db` 的等号（0/−10 dB 台阶、`min_stop_db=0` ⇒ 定位 ~8 kHz；改 `>` 回落奈奎斯特）、
  `band_balance` 的**左闭右开**（8 个频点压 7 个边界 ⇒ 各 14.3%；改 `>` 丢下边界、改 `<=` 把 20 kHz 塞进 air）、
  `bands_pct` 的 `10**(db/10)`（1 kHz/5 kHz ⇒ 90.9/9.1；底数 11 ⇒ 89.0/11.0）。
- `verdict`：**把"截止点"与"滚降"两条等号分开取**——上一轮两条同时取等号，
  任一条改成 `<=` 时另一条仍为 False ⇒ 两个变异体都杀不掉（**这是本轮最重要的方法论纠错**）；
  Hi-Res 三条分支的文案数字（96.0kHz / 41.9kHz+87% / 40.0kHz+96.0kHz——`÷1000`、`×100` 的位数）；
  `conf` **恰好 0.5** 算证据够（`slope = 16.0` ⇒ 8/16 逐位 0.5）；
  `advice` 的**缺格**（`{"…": None}` 必须短路跳过，`or→and` 会取 `None["reason"]`）；
  全 ok 时**一个"；"都不出现**（`and`/`!=` 两个方向的短路）。

### 新增（**判据补全：spectrum 29 + verdict 31 条变异缺口** —— 全部落在"等号侧/短路/单位"上）

> 回填 2026-10-20 整体复跑（**531/740 = 0.718**，209 存活）里的**前两组**。
> 复活的原因很集中：既有用例**都取在门限中间**，没有一条落在"恰好等于门限"上。

- 新增 `tests/test_verdict_edges.py`（**19 条**）：每条都取在**等号**或只差一个最小可表示量——
  `bit_gap >= 4`（恰差 4 bit 就 warn）/ `clip_pct` **严格**（恰 0.005% 不算削波）/
  `true_peak > 0.0`（**0.00 dBTP 不是过载**）/ `lra == 3.0`、`dr == 6.0`、`width == −18.0` 都不告警/
  `brickwall >= 24` **是**硬砖墙 / `conf == 0.5` **算**证据够 / 升频上限 `k <= 22.5` **含等号**/
  容器 `fs > 88200` **不含等号**/ 两条"证据不足"都**严格** /
  交叉核对里 `k = cutoff/1000` 用 **30 kHz** straddle 住（除以 1500 会误报）/
  文案里的 `96.0kHz`、`20.0kHz`、`24`、`16` 逐字钉 / `bit_note` **有则必带**、`bits` 缺失**不许印 None** /
  三个聚合短路各测一个方向（空文案不追加、`bw==ok 且 level!=ok` 才加前缀、`advice` 不留多余空格）。
- 新增 `tests/test_spectrum_units.py`（**23 条**）：**差分判据**（逐点实现当真值）+ **每个常数各一条**。
  * 平滑：**空/单点/重复频点/非单调/含 0 Hz/极稀疏/几何/非线性** 八类网格 × 三档 fraction 全一致；
    重复频点**必须仍走向量分支**（`diff >= 0` 的等号；用打桩确认，不只是数值相等）；
    fraction 越大窗越窄；首尾 bin 的 `searchsorted` 两侧 side 用"只有最低频有点"的谱钉住。
  * 功率域：`+1e-20` 的**加性**口径（−400 dB 输入 ⇒ −200；−200 输入 ⇒ −196.99，不是原样）。
  * 截止点：`len(f) < 16` 的等号（15 拒、16 收）/ 1/12 倍频程粗网格 / 上方 4% 守卫 /
    `need` 变大后读出点**不下移** / 通带窗 `[coarse/2.56, coarse/1.6]`（**倾斜通带**下仍贴住真边缘）/
    陡度取 `s[j0]-s[j1]`（90 dB 砖墙 ⇒ 读数 80–100）/ **`cut < min_hz` 是死分支**（已证明并钉住行为）。
  * 展示口径：`cutoff_khz`/`cutoff_ratio` 的换算与位数**按定义重算比对**；频段用**线性功率**。
- `TARGETS` 子集补挂这两个文件（不挂就会"全假存活"——同型坑第四次复查）。
- 验证：全量 **958 收集 / 957 passed**（唯一失败仍是 `test_ui_material` 的**已知 flaky**）。

### 新增（**去咔哒"第二级复核"：宽度守卫 `max_run=4`** —— 用代价表定的，不是手感）

> `docs/editor_roadmap.md` 第七步 ①。上一轮把 Adobe 的 "Second Level Verification" 记成
> "闭源标杆单列一级"但**没量过**；这一轮把它变成**可复算的代价表**
> （`packaging/declick_verify_probe.py`）。实测与判据见 `docs/editor_research.md §12`、
> `docs/theory.md §12.12`。

- **先量出一个上一轮没说透的缺口**：`corpus.click_train` 只造**单采样**咔哒；
  额外造 2/3/5/8 采样宽后发现——`r[n]` 在**平台内部 ≈ 0**，检测器**只看两个边**
  ⇒ ΔSDR 从 **+39 / +37**（宽 1/2）掉到 **+1.5 / +0.5**（宽 3/8）。
  候选修法"按幅度沿两端扩展"**实测否决**（残差尺度 ≠ 幅度尺度，扩展会一路吃穿信号）
  ⇒ 修平台需要**台阶检测器**，**如实挂账**。
- **宽度守卫**：只接受宽度 ≤ `max_run` 的检出段，默认 **4**。取值来自代价表
  （平滑音 + 5 ms 起音，α=5）：`L=3` 会把 **2 采样咔哒整段丢掉**（+37.37 → 0.00），
  `L=4` **保住 1/2 采样咔哒**且把合法起音的破坏从 **30.55 dB 抬到 56.51 dB**（300 → 40 采样）。
  ⇒ "4" 由**离散事实**（丢不丢咔哒）定出，不是拟合出来的门限。
- 审计新增 `max_run` / `rejected_runs`（"复核挡掉了多少"**必须可见**）；
  `--max-run 0` 显式关闭守卫；`DeclickOp` 校验 `max_run ≥ 1` 或 `None`。
- **挡不住的（如实写）**：亚毫秒起音（0.2 ms 的检出段宽度也是 2，与咔哒重叠）；
  平台型咔哒（守卫放行但修不到平台）。
- 新增判据 **4 条**（`tests/test_edit_declick.py` 的"5. 宽度守卫"：守卫效力 ≥20 dB / 1·2 采样咔哒不丢
  且 `max_run=3` 确实会丢 / **平台缺口如实钉住**（3·8 采样 <5 dB）/ 校验与审计字段）。
- 文档：`docs/theory.md §12.12`、`docs/editor_research.md §12`、`docs/editor_roadmap.md` 第七步 ① 收口。

### 新增（**P3 第二个修复算子：去咔哒** —— 判据先行；量出来的关键是"代价不对称"）

> `docs/editor_roadmap.md` 第六步。`corpus.click_train` 早两轮就把**位置已知**的咔哒备好了，
> 所以这一轮同样**先把口径量出来再落地**（`packaging/declick_probe.py`：四种素材 × 六档阈值）。
> 判据写进 `docs/theory.md §12.11`、实测与检索在 `docs/editor_research.md §11`。

- **两条"想当然"被实测否决**：
  ① **邻域中值残差**（平滑素材上 α 扫到 12 仍有 **15% 假阳性**——正弦的局部中值本身偏离样本值，
     残差被**波形**主导）⇒ 改用**两点预测残差** `y[n]−(y[n−1]+y[n+1])/2`
     （对 `f ≪ fs` 的平滑内容是 `≈ (2πf/fs)²·|y|`，比咔哒**小两个数量级**）；
  ② **局部滑动尺子**（同一误报率下 SDR **更差**：合法瞬态 α=5 局部 −3.71 vs 全局 **+6.53**）
     ⇒ 用**全段 MAD**（`median(|r|)/0.6745`）。
- **阈值 = 5.0**（不是 4.0，也不是手感）：实测**代价不对称**——FPR 到 ~1e-3 就把信号**修坏**
  （白噪 **−9.96 dB**、合法瞬态 **−7.36 dB**），而 TPR 从 1.00 掉到 0.60 只让收益从 +14.2 变 +3.6
  ⇒ 规则 = "**先压误报、再看收益**"。α=5 时**谐波音 / 带限白噪 / 合法瞬态的误报均为 0**，
  且四种素材 ΔSDR 全为正（+40.6 / +3.6 / +2.0 / +6.5 dB）。
- 新增 `core/edit/declick.py` + `DeclickOp` + render 的 declick 路径 + CLI `--declick`：
  **只改检出样本**（选区外**逐位不变**、选区内**未检出样本逐位不变**；逐声道独立检测）。
  审计报 `threshold / detected_samples / fraction / residual_scale / changed_samples / 能量比`；
  **改动面 > 0.5% 时主动警告**（"阈值可能压到误报区"，实测那一带就是把信号修坏的地方）。
- **效力边界（全部实测，写进算子 docstring）**：**收益由素材的可预测性决定**——
  平滑 **+40.6 dB**、合法瞬态 +6.5、谐波音 +3.6、**带限白噪只 +2.0**（噪声本身不可预测
  ⇒ 预测残差里没有可分离的咔哒成分）。两条新拒绝：同类选区**重叠**、与其它域**混用**。
- 检索锚点（`§11.3`）：KU Leuven 那篇明说**"咔哒位置实际未知 ⇒ 需要检测方法"**（与我们同向）；
  工程界默认阈值 **4σ**（`@audio/denoise`，注明 Janssen 1986 / Godsill–Rayner 1998）与我们同量级；
  **Adobe Audition 的 Click/Pop Eliminator 专门有 "Second Level Verification"** 处理
  "小号/萨克斯/女声/军鼓这类正常高峰被误判"——**误报会修坏是已知问题，闭源标杆也单列一级**。
- 新增判据 **14 条**（`tests/test_edit_declick.py`）+ CLI 侧 2 条。
- 变异复跑（`--only core/edit/declick --only core/edit/render --only core/edit/ops`）：
  首跑 **0.895（119/133，14 存活）**；补 4 条判据 + 登记 **7 条可证明的等价体**后复跑 ⇒
  **`declick.py` 22/22 = 1.000（存活 0，等价 6）**，`render` / `ops` **无存活**。
  补的判据：残差的**首尾口径与"3 个样本"边界**、**数字静音里的孤咔哒不检测**
  （**保守侧的代价**，如实钉住、不许将来偷偷放宽）、**模块层**阈值守卫、
  **占比 / 改动帧数 / 尺子小数位**（占比那条只有**立体声**素材才分得出 `/(n·ch)` 与 `/(n/ch)`）。
  等价体都是"**不可达**"或"**概率为 0**"两类（首尾样本残差恒 0 ⇒ 检出段永远不含首尾 ⇒
  `_repair_runs` 的单侧分支不可达；空选区在 declick 路径上不产出任何字节）。
- 文档：`docs/theory.md §12.11`、`docs/editor_research.md §11`、
  `docs/editor_roadmap.md` 第六步（完成）+ 第七步（建议：检测侧做强 + 还历史债）。

### 新增（**"severe 卡住"的机制 + 可辨识性预判** —— 把"修不了"变成**事先算得出来的告警**）

> `docs/editor_roadmap.md` 第五步 ②。上一轮把 severe（饱和 >20%）的"提升只有 +4 dB 量级"记成
> **现象**；这一轮把它做成**机制**。第五轮科研反哺记在 `docs/editor_research.md §10`、
> 判据写进 `docs/theory.md §12.10`；决策实验 = `packaging/sparse_declip_probe.py`。

- **四组实测把"迭代不够"排除掉**（同一份 severe，f=22.4%，SDR dB）：现用三相交替投影
  400→8000 次 **13.58→13.69（钉住）**；**过松弛**（ω=1.2/1.5）也**不动**；
  **只留两个凸集**（去掉非凸的不等式相交）更差且同样钉住（11.10→11.11）；
  而**同一素材、`B` 给真值 18 kHz** ⇒ 20.93→**23.14（一直在涨）**。
  ⇒ 差别**不在算法/迭代数/松弛**，而在**给的 `B` 让解集是否唯一**。
- **机制（算出来的，不是拟合）**：可行解集 = `{带限(B)} ∩ {已知样本 = y}`；
  带限子空间实维数 ≈ `(B/(fs/2))·N`（**时间-带宽自由度**，Landau–Pollak 1962「已核」），
  已知样本给 `(1−f)·N` 条约束 ⇒ **解集维数/N ≈ B/(fs/2) − (1−f)**
  ⇒ **欠定 ⟺ `f > 1 − B/(fs/2)`**。**验证表**（只改 `B`）：余量 +0.026 ⇒ SDR 一直涨；
  余量 −0.057 / −0.124 ⇒ 全程钉住 ⇒ 门槛**单调可预测**。
- **落地**：`declip.reconstruct` 新增两条**如实记账**（进审计与 CLI）——
  `ambiguity_margin` / `ambiguous`（**余量 ≤ 0 ⇒ 明说"解不唯一、加迭代也逼近不了真值"**），
  以及 `step_rms_db`（迭代**末步**相对本段的 dB）+ `converged`（末步已到**浮点精度**，
  **数值判据**而非手感门限）。⚠ 欠定时两者会"打架"（step 从 −75 走到 −141 dB 而 SDR 钉住）
  ⇒ CLI 把两者**并列打出来**，不许只报 step 误导用户。
- **稀疏先验不落地（判据不过就不加）**：探针里实现了 analysis-ℓ1 的 ADMM
  （`minimize ‖A x‖₁ s.t. x ∈ C`；`A` = 本项目 WOLA STFT，`S A = I` 且 `A*A = diag(Σw²)`
  ⇒ x-update 有**闭式** `P_C(S(z−u))`），**两种稀疏性相反**的素材各量一遍：
  **谐波音（稀疏）** 46.7 / 16.6 / 7.7 vs POCS 40.6 / **17.1** / 7.6；
  **带限白噪（最不稀疏）** ~50 / 31.6 / 12.8 vs POCS **45.8** / **40.4** / **13.6**
  ⇒ 只在"稀疏素材 + 轻削波"胜出（+6.1 dB），其余全面不如 POCS 且更慢更复杂 ⇒ **不进内核**。
- 新增判据 **5 条**：内核侧 3 条（欠定标志**真的能预测**"加迭代是否有用"／step 与 converged
  是**量出来**的／no-op 时这两组字段**不许编数**）+ CLI 侧 2 条（欠定必须打 ⚠ 告警；
  定解必须报"还能不能动"）。
- 文档：`docs/theory.md §12.10`（解集维数 + 门槛 + 三条边界）、`docs/editor_research.md §10`
  （四组实测 + 验证表 + 稀疏族实测 + 锚点：Landau–Pollak 1962「已核」／压缩感知 `m ≥ 2s`
  「二手」／Axioms 2019 的"投影加速"定位）、`docs/editor_roadmap.md` 第五步 ② 更新。
- 变异复跑（`--only core/edit/declip`）：**27/27 = 1.000，存活 0，等价 2**（等价体：`sign` 只在
  饱和样本处被读；`margin ≤ 0` 的等号概率为 0）。⚠ 复跑还**逼出一个设计问题并当场改掉**：
  `converged` 最初写成"末步 ≤ `1e-12 ×` 本段 RMS"，一跑就带出 **4 个不可证明的存活体**
  （`1e-12` / `scale` / `20.0` 的变形在被测输入上同值——门限本身是**凭空造的**）
  ⇒ 改成**精确判据** `末步 == 0`（达到不动点），4 个变异体随之消失、且代码里少了一个门限。
  （这正是"变异测试当设计审查用"的一条实例。）

### 新增（**"编辑要的 `B` 谁给"** —— 测量层加了一条内容带宽出口，口径用真值量出来）

> `docs/editor_roadmap.md §8` 第五步的 ①。上一轮把 `B`（内容带宽）当**输入**收下、但把真值带宽
> **白给**了（`docs/editor_research.md §8.4` 挂的那笔账）。这一轮还账：**先量两条现成读数 →
> 定口径 → 才接线**。全部数字出自 `packaging/bandwidth_probe.py`（2 s 素材，48 kHz）。

- 新增 `spectrum.content_bandwidth(f, db, fs, thresholds)`：**内容带宽 `B` 的单一出口**
  （编辑层仍**不** import 测量层 ⇒ 由 CLI 在边界层接起来）。口径**实测定的**：
  **有砖墙截止 ⇒ `B = cutoff`**（`detect_cutoff` 已校准；本轮实测 48 kHz 的 6–21 kHz 网格
  最大偏差 **0.49%**，**moderate 削波后仍 +0.0%**）；**量不到 ⇒ `B = 奈奎斯特`**
  = "没有可用的带限先验" ⇒ 去削波**逐位不变**（如实记 `noop`）。
- **实测否掉两条"看起来能用"的读数**（本轮的负结果，正是它定死了口径）：
  ① **能量滚降点**（`rolloff95`，即文献里的 *spectral rolloff point*——"总能量 95% 落在其下的
     那个频率"）系统**偏低**：砖墙素材 −4.9%、谐波音 **−17.5%**、自然滚降素材给出 12.5 kHz
     而真实内容到 ~20 kHz ⇒ 拿它当 `B` 会把 SDR **修坏 −1.5 dB**（用 `cutoff` / 真值是 **+34.2 dB**）；
  ② "只用**没有饱和样本的帧**量谱" ⇒ **0/10 帧可用**（饱和样本沿波形峰散布，任何长度的帧
     几乎都含至少一个）⇒ 不可行。
- `DeclipOp` 新增 `bandwidth_source`（**来源记账，不参与任何算式**）+ CLI **`--bandwidth` 省略即自动**
  ⇒ 审计记 `bandwidth_source ∈ {given, auto_cutoff, auto_nyquist}`——"依据哪条测量"入账。
- **效力边界（全部实测；进拒绝路径与文档）**：**severe 削波**（饱和 >20%）后砖墙证据被削波谐波
  **填掉** ⇒ 自动读数退回奈奎斯特（去削波逐位不变；想修 severe 得显式给 `B`，与"提升随饱和变密
  而变小"同源）；**canonical 谱的长度门槛实测在 0.90–0.95 s 之间**（0.95 s 有、0.90 s 无）
  ⇒ 量不出谱时**明确拒绝**自动定带宽（不猜一个 `B`）。
- 新增判据 **9 条**：`tests/test_bandwidth.py`（7：砖墙网格 ≤2%／moderate 保住证据／
  severe 如实拒绝／满带宽与自然滚降都退奈奎斯特／谱缺失回落／`thresholds` 真生效／
  闭环"量出来的 `B` 真能修好"）+ `tests/test_edit_cli.py`（+2：自动路径端到端、太短明确拒绝）。
- 探针 `TARGETS`：`spectrum.py` 子集补挂 `tests/test_bandwidth.py`、`ops.py` 子集补挂
  `tests/test_edit_cli.py`（新增代码的判据必须挂上，否则那一族变异体"全假存活"——这条坑本轮
  又在 `spectrum.py` 上复查了一遍）。
- 文档：`docs/theory.md §12.9`（口径 + 三条边界）、`docs/editor_research.md §9`
  （实测表 + 检索锚点：*spectral rolloff point* 的通行 85%/95% 能量定义「二手」；
  BWE/语音超分一脉把这个对象叫 **effective bandwidth / `f_eff`**「arXiv 预印本，只作术语依据」）、
  `docs/editor_roadmap.md` 第五步 ① 收口。
- 变异复跑（`--only core/analyzers/spectrum --only core/edit/ops`）：
  **`ops.py` 6/6 全杀 = 1.000**（含新加的 `bandwidth_source`；子集补挂了 `tests/test_edit_cli.py`）。
  ⚠ **`spectrum.py` = 0.695（66/95，29 存活，等价 16）——但 29 条存活里没有一条是本轮的代码**：
  `content_bandwidth` 的 4 个变异体**全被杀**，存活的是该文件**既有的债务**
  （截止点判据的内部常量、`band_size`/`band_balance`/`SpectrumAnalyzer.run` 的边界），
  与 CHANGELOG 里"整体 0.619 ／ spectrum/dsp/causes/verdict 剩 ~210 条**未还债务**"那节**同源**。
  ⇒ 本轮**只报数、不动它**：填这批缺口要把 `packaging/cutoff_calib.py`（§1.6 那张误差曲面）
  升级成**判据**，属一次专门的"判据补全"轮 ⇒ **挂账**。
  （对照：若不把 `tests/test_bandwidth.py` 挂进该模块的子集，这 4 个变异体会**假存活**
  ⇒ 本轮实际是把该模块的分**抬**了，不是压了。）

### 新增（**P3 第一个修复算子：去削波** —— 判据先行；量出来的关键结果是"插值会把 SDR 修坏"）

> `docs/editor_roadmap.md §8` 第四步。循环照旧：**先建判据（合成语料）→ 把候选量一遍 →
> 拿量出来的"不对劲"去检索 → 才落地**。全过程记在 `docs/editor_research.md §8`，
> 数学判据在 `docs/theory.md §12.8`，决策实验在 `packaging/declip_probe.py`。

- 新增 `core/edit/corpus.py`：**有真值的合成语料**（修复算法的判据地基）——带宽已知的干净信号
  （`harmonic_tone` / `band_limited_noise`，带宽是**真值**，可核）、**硬削波三档** `CLIP_TIERS`
  （相对峰值 0.85/0.55/0.28；severe 档饱和占比实测 **22.7%（噪声）/ 47.9%（谐波音）**——
  把 Záviška/Rajmic 2021「既有基准太软」那条教训钉成**语料设计判据**：severe > 5%）、
  位置已知的脉冲 `click_train`（给 `DeclickOp` 备料）。`ceiling_of` = 从数据里**量回**削波电平
  `c` 的入口（**可测输入，不是猜**，`docs/theory.md §12.2`）。
- 新增 `packaging/declip_probe.py`（**决策实验**：四种候选、同一把尺子 `sdr_db` + `energy_ratio_db`）：
  局部插值（线性 / 三次多项式 / 三次样条）**把 SDR 修坏**（带限白噪：light 43.5→**21.3**、
  moderate 23.3→**8.5**、severe 10.1→**1.6**）；纯 Papoulis–Gerchberg 用真值带宽很好，
  但**带宽取窄会反伤**（severe ×0.5 ⇒ **0.47 dB**）；**POCS**（pg + 削波**不等式** `|x| ≥ c` 同号）
  全面最优（**312.8 / 42.8 / 14.1**），且带宽取窄**只退化到"不改"**。
  ⇒ **落地 POCS**、插值族**不进内核**（工程界的 Audacity ClipFix 正是被否决的这一族）。
  机制：饱和样本的 `±c` 其误差被"**越出 `c` 多少**"限住，插值拿**不相关**的锚点去猜 ⇒ 越修越远。
- 新增 `core/edit/declip.py` + `ops.DeclipOp` + `render` 的 declip 路径 + CLI `--declip`：
  **只改饱和样本**（已知样本每轮写回 ⇒ 逐位不变），选区之外**原样拷贝**。两个输入都**可测**：
  `ceiling`（`None` ⇒ 取**本段观测峰值**，审计记 `ceiling_source`）、`bandwidth`（**必填**；
  `≥ 奈奎斯特` ⇒ 无约束可利用 ⇒ **逐位不变**、记 `noop`）。审计新增两条**实测**：
  能量比（防"变安静被当成修好"，PHAIN 的警告）与带外变化（证明带限投影确实生效，实测 **−15.5 dB**）。
- **效力边界**（全部实测，写进审计 / CLI / 文档）：`B` 由调用方给（本模块**不估**）；
  提升随饱和变密而变小（light **+269** / moderate **+19.5** / severe **+4.0 dB**——与
  Záviška/Rajmic/Mokrý, *Signal Processing* 192 (2022)「提升随输入 SDR 增大而增大」同向）；
  `c` 必须取自**被编辑文件自己**的样本（float32 存回后是**量化**的 ⇒ 拿写入前的理论电平去比会把
  饱和集合**判空**，实测 `|y| ≥ 0.5225` 一个都匹配不到）；**不能分块**（带限是**全局**约束）
  ⇒ 选区整段载入内存（内存 ≈ 选区采样 × 声道 × 8 B × ~4）。
- **两条新拒绝**（顺序语义未立判据 ⇒ 宁可不做）：多个去削波选区**重叠**；
  与 `GainOp`/`BandGainOp` **同栈混用**。
- 新增判据 **49 条**：`tests/test_edit_corpus.py`（20）+ `tests/test_edit_declip.py`（26）
  + `tests/test_edit_cli.py`（3）。语料的**常量与默认值都钉死**（改一个数就是换一把尺子），
  生成器按文档式子**独立重算 → 逐位相同**（可复算）。
- 文档：`docs/theory.md §12.8`（去削波的判据 + "插值为什么错"）、`docs/editor_research.md §8`
  （实测表 + 带问题检索到的锚点：Papoulis 1975 / Gerchberg 1974 / Rajmic et al. Axioms 2019 /
  Záviška et al. JSTSP 2021 / Signal Processing 2022）、`docs/editor_roadmap.md` 第四步收口
  + 第五步（给 `B` 找"不猜"的来源、密集削波那一族）。
- 探针 `TARGETS` 新增 `core/edit/declip.py` + `corpus.py`（前者改的是**真样本**，
  后者是**真值本身**）。首次跑出 **0.906（58/64，6 存活）**，把其中 2 条补成判据、
  4 条登记为**可证明的等价体**（`sign` 只在饱和样本处被读／两条防御性 `peak > 0` 守卫／
  `sign ∈ {±1}` 的乘法与除法同值）后复跑：
  **60/60 = 1.000，存活 0，等价 4**（`packaging/mutation_probe.py --only core/edit/declip
  --only core/edit/corpus`）。补的两条判据：语料的**最长段口径**（段长 = `e − s`）与
  `click_train` 的**默认 `amp`**（原先测试总是显式传参 ⇒ 默认值没被测到）。
  ⚠ 口径如实记：本次单目标上限 `--max-mutants 200`（两模块候选 17 + 47 = 64，
  **无截断**：等效体单列，故分母是 60）。
- ⚠ **探针维护（同一轮，两条坑都留了记录）**：
  ① `render.py` 加了 declip 路径 ⇒ **8 条等价体的行号全部漂移**，按探针自己的教训
     （"行号键会漂，改被覆盖的源码后必须重跑核对"）重新对齐并**重跑核对**；
  ② `ops.py` / `render.py` 的测试子集**补挂 `tests/test_edit_declip.py`**——新算子的
     `__post_init__` / `to_dict` / `describe` 与 declip 渲染路径**只被它构造到**，
     不挂就会"全假存活"（同型坑先前在 `test_edit_stft.py` 上踩过一次）。
  首次跑 `--only core/edit/render --only core/edit/ops` 出 **0.904（85/94，9 存活）**；
  补齐 6 条判据 + 1 条文案等价体后复跑 ⇒ **93/93 = 1.000，存活 0，等价 9**。
  补的判据：`DeclipOp` 三个边界（`ceiling=0` / `bandwidth=0` 拒、`t1<=t0` 拒、`iterations=1` 允）、
  审计两条**实测**的小数位（与重算值同 3 位）、**带外上界含奈奎斯特那条 bin**、
  选区落在文件之外＝no-op、审计报**算法名与域**。另修一处**口径**：`_declip_record` 的
  带外上界由 `fs/2` 改成 `fs`（半开区间 ⇒ 含奈奎斯特 bin），并补了对应判据。
- ⚠ **整体分挂账**：本轮 `TARGETS` 由 7 模块变 9、且 `render.py` 行号变了 ⇒ 项目**完整**
  变异分要用**新集合**重跑（`--max-mutants 400` 口径），**本轮不做**；旧记录
  `404/653 = 0.619` 仍是最近一次**完整**测量的值（见下文"上限口径"一节），**不许当成
  本轮分数**。编辑侧四个模块（`declip` / `corpus` / `render` / `ops`）本轮已逐模块复跑至 **1.000**。
- 验证：编辑侧 6 个测试文件 **116 passed**；`ruff` 全树绿；全量 **881 收集 / 880 passed**
  （唯一失败仍是 `tests/test_ui_material.py::TestToastLifecycle::test_burst_does_not_leak`
  那条**已知 flaky**——全量负载下动画慢，单独跑必过，与本次改动无关）。

### 判据补全（**编辑三件的 27 条存活**：把"导出结果"这条通路钉到 0 存活）

- 上一轮的**完整**变异探针（`--max-mutants 400`，653 个有效变异体）里，`core/edit/` 三件
  （`render.py` 80 + `stft.py` 45 + `ops.py` 2）报 **27 条存活**、分数 **0.787**。
  本轮把其中**能判定的**逐条转成判据或登记为**可证明的等价体**，复跑后：
  **114/114 = 1.000，存活 0，等价 13**（`packaging/mutation_probe.py --only core/edit`）。
- 新增/改写的判据 **14 条**（`tests/test_edit_stft.py` + `test_edit_render.py`），每条都来自
  一个"改一个字符也测不出来"的位置：
  * **掩码覆盖数**：`applied_tf_points` 必须精确等于 `170 bins × 24 frames = 4080`；
    再把 `t1` 取成 `47552/48000`（**故意**让一个帧中心恰好落在 `t1` 上）⇒ 必须是 `170 × 23`——
    一条判据同时钉住**两条半开区间边**与"计数必须是整数"；
  * **频率下界闭**：`f0 = 7031.25`（恰为 bin 中心）与一条该频率的正弦 ⇒ 实测 −15.1 dB
    （**不是 −40**：邻居 bin 599 在带外，这是正确行为，判据写成区间并把机制写清）；
  * **小数位**：`duration_s` 用 48001 帧的素材才分得出 `round(…,6)` 与 `7`；
    `peak/残差/实测衰减` 必须**自己等于** `round(自己, 3)`；残差量级钉成 **−320…−300 dB** 区间
    （float64 舍入 ⇒ −300 量级；写成 `30·log10` 会跑到 −450 dB）
  * **静音不崩**：数字静音素材 ⇒ `peak_out_dbfs` / `roundtrip_floor_dbfs` 都必须是 `None`
    （判据写成 `> 0`；改成 `>= 0` 会去算 `log10(0)` 直接抛异常）；
  * **`_EPS` 可达性**：实测扫过 `(fs, t)` 网格（t 步长 1e-4 s、到 300 s、5 种采样率），
    fs=48000 下**第一个可区分**的 t 是 **0.0085**（`0.0085·48000 = 408.00000000000006`）
    ——上一版判据用的是 `0.1 s`，而 `0.1·48000` **恰好是整数**，所以它测不出这个常量；
  * **小块长 + 后段选区**：`k0−pos` / `if k1 <= k0` / `applied_frames` 跨块累加，一起钉住；
  * **满刻度不算削波**：PCM_16 的 ±1.0 是合法码值 ⇒ `clipped_frames` 必须为 0（判据是严格 `>`）；
  * **上下文偏移**：把一段只在 `[0.6,0.7)` 出现的带内正弦压掉 —— 偏移写错会"整体挪走"，
    稳态素材上听不出来、这条判据看得出来；
  * **WAV 截断的自校正**：实测本机 libsndfile 对截断的 WAV **按文件大小重算帧数**
    （`claimed` == 实有）⇒ 我们不误报 `read_incomplete`；而截断的 **FLAC** 仍声称原帧数、
    读到损坏处**抛** `LibsndfileError` ⇒ 走 `except` 支。两条合起来证明
    `_render_band` 里那条"短读"分支在**已验证的两种容器上不可达**（保留为**防御性**）。
- 登记 **13 条可证明的等价体**（`EQUIVALENT` 白名单，每条写清"为什么输出逐位相同"）：
  块长恒为 hop 整数倍（`CHUNK_HOPS ±1`）、`while pos < end` 的等号、`min(blocksize, end−pos)`
  的加号（首段/尾段 + EOF 截短）、缓冲/累加器**加长**（请求区间定界在前）、报错文案里的算术、
  截断分支的 `break`（`read_error` 已置 ⇒ 外层立刻退出）、`wss > _WSS_MIN` 的等号不可达、
  快/通用 OLA 路径数学同式（只差求和次序）、`k1 <= k0` 只在 `k1 == k0` 时不同（空切片 + 增量为 0）。
- **探针本身的三处改进**（都是这轮暴露的口径问题）：
  ① 新增 **`--only <子串>`**（单模块迭代，省掉 out/ 临时脚本）；
  ② `--max-mutants` **截断会显式报出**并标注"分数是**部分**结果"；
  ③ `TARGETS` 里 `ops.py` 的子集补挂 `tests/test_edit_stft.py`（原先漏挂 ⇒ 它的变异体全假存活）。
- 验证：编辑四文件（stft/render/ops/cli/metrics 的判据）**67 passed**；`ruff` 全树绿；
  编辑三件变异分数 **1.000**（114/114）。全量 **832 条收集 / 831 passed**
  （唯一失败仍是 `test_ui_material` 里那条**已知 flaky**）。

### 新增（**编辑侧测量层** + "设定 vs 实测"进审计 + **用实测把软掩码关掉**）

> `docs/editor_roadmap.md §8` 第三步。这一轮**不加编辑功能**：先把"改完到底怎么样"变成
> 可复算的数，再用数决定要不要上软掩码（`docs/editor_research.md §6` 表 2 的待办）。

- 新增 `core/edit/metrics.py`：四个**只出数、不出"好/坏"**的函数——
  `band_gain_db`（实测带内衰减）、`envelope_width_ms`（脉冲包络宽度 = 涂改量）、
  `sdr_db`（**全局口径**，明确不是 BSS_EVAL 的投影式）、`energy_ratio_db`（幅度缩水守卫）。
  ⚠ 模块**不设任何门限**（门限属于上层）——"加阈值前先跑区分力检验"是本仓铁律。
  **10 条判据**（`tests/test_edit_metrics.py`）全是"答案本来就知道"的构造：
  整段 ×0.1 ⇒ −20.00 dB；误差能量恰 1% ⇒ SDR **恰** 20.00 dB；**纯缩放不许改变**包络宽度。
- **审计与 CLI 升级**（"可审计的修复"的落点）：频域操作新增
  **`attenuation_measured_db`**（在**同一套 STFT 表示**里量的：掩码前/后的带内能量比），
  CLI 打成「**设定 −40.0 dB ｜ 实测 −40.00 dB**」。
  ⚠ 与"在导出信号上再量一次"**不是同一口径**：后者会被分析窗泄漏压浅
  （宽带素材实测 −18.6 vs 设定 −20；判据把它钉成区间，见 `test_band_gain_db_is_shallower_...`）。
- 新增决策实验 **`packaging/mask_tradeoff.py`**（4 种掩码 × 2 种写回 × 4 列实测）：
  **结论 = 软掩码不进内核**——谱底 −40 dB 花掉 20 dB 压制度、包络跳变**一模一样**；
  50 ms 时间锥形花掉 50 ms 选区精度（边界 +5ms 处 **−42.3 → −3.1 dB**）只换 1.6 倍包络改善；
  "不外扩跨度、靠锥形压台阶"是**假需求**（1.0e-02 仍比当前的 1.4e-04 差 70 倍）。
  带外污染在 8 个变体上全 **−0.00 dB**。
- **科研反哺第二轮**（`docs/editor_research.md §7`）：把"为什么**不**搬那套机制"写成**依据**
  而不是"懒得做"——musical noise 的成因是"增益由带噪估计**逐帧随机**导出"
  （微软研究院 TR-2000-86 口径），我们的掩码是**选区内常数增益** ⇒ 帧间抖动方差为 0，
  **机制不存在**；transient smearing 是**相位声码器丢相干性**的产物（arXiv 预印本）、
  我们不做时间伸缩 ⇒ 同样不适用。⚠ 明确写出**何时重开**：编辑升级到"挖点/补点"
  （时频修音 / declip / declick）时。
- 文档：`docs/theory.md §12.7 ⑤`（掩码形状只谈代价）、`docs/editor_roadmap.md`
  （第三步收口 + 第四步建议 = P3 修复算法的判据骨架，`sdr_db`/`energy_ratio_db` 已就位）。
- **修量具**（本轮变异探针暴露的两处口径问题）：
  ① `TARGETS` 里 `core/edit/ops.py` 的子集**漏挂** `tests/test_edit_stft.py` ⇒ 它的变异体
  全假"存活"；② 探针现在**显式报出** `--max-mutants` 截断与"分数是**部分**结果"——
  **同一份代码，小上限跑 0.864、400 上限跑 0.619**，以前记的高分是**上限口径**造成的虚高
  （教训写进探针 docstring）。
- ⚠ **如实记账**：`--max-mutants 400` 下的首次**完整**变异分数 = **404/653 = 0.619**，
  存活 **249**（散布在 spectrum / dsp / verdict / causes / edit 三件），等价 30。
  **这是债务、不是"通过"**：本轮钉死了判定层那 12 条（见上一条），其余按模块逐步填。
- ⚠ **2026-10-20 更新（9 模块 / `TARGETS` 扩到编辑侧全量后复跑，**无截断**）**：
  **531/740 = 0.718，存活 209，等价 47**。分母从 653 涨到 740（新增 `declip` / `corpus`
  两个模块），分数 0.619 → **0.718** 的增量**全部来自编辑侧**（五个编辑模块已逐个复跑到
  **1.000**：`stft` / `render` / `ops` / `declip` / `corpus`；`metrics` 尚未登记为 target）。
  ⚠ 存活 209 条**全部集中在 4 个既有模块**，分布是
  **`dsp.py` 116 ｜ `causes.py` 33 ｜ `verdict.py` 31 ｜ `spectrum.py` 29** ⇒
  下一轮的"判据补全"就照这个分布打（先 `spectrum`+`verdict` 这一组，它们与判定口径最贴）。
  （复算：`python packaging/mutation_probe.py --max-mutants 400 --max-seconds 7200`，日志 `out/_mut_full.log`。）
- 验证：全量 **818 条收集 / 817 passed**；`ruff` 全树绿。
  唯一失败仍是 `test_ui_material::TestToastLifecycle::test_burst_does_not_leak`
  （全量并发下的**已知 flaky**，单文件跑过）。

### 判据补全（**判定层 12 条边界翻转**：第二次变异轮的发现）

- 上一轮变异探针（`packaging/mutation_probe.py`，162 个有效变异体，**该轮上限更小**，
  与下面 653 个的那轮不可比）在判定层报
  **12 条存活**：`verdict.py` 5 条（`>=`→`>` ×4、`and`→`or` ×1）＋`causes.py` 7 条
  （`>=` / `<` 的边界翻转 ×7）。**全部是"阈值没被正好等于过"** ⇒ 真·**测试缺失**，
  不是等价变异体（等价变异体的口径见探针的 `EQUIVALENT` 白名单）。
- 在 `tests/test_judgment_boundaries.py` 补 **11 条**边界判据，走**判定层纯函数**
  （`axis_bandwidth` / `cause_candidates`）直接喂"正好落在阈值上"的测量值，逐条钉住
  "往哪边倒"：`lossy`（44.1k 与 48k 两档）、`up_44`、`ltd`、`conf_low`（构造出
  `conf == 0.5`，**且两处支路都喂到**——有损档与强有损档）、`brick_db`（正好 24 dB/oct
  **是**硬砖墙）、`brick_flat_db_per_oct`（正好 15 dB/oct **不算**平缓滚降，一条同时管住
  `gentle` 与 `gentle_rolloff` 两处）、`noise_like_flatness`（0.5 **算**噪声状）、
  `vinyl_rumble_pct`（25% **算**隆隆）、`upsample_fs`（正好 88.2k **不算**超 48k）、
  `clip_pct`（正好等于告警线 **不**触发）。
  ⚠ **不改任何门限**（`docs/thresholds.md` 的取值口径没动），只把现有语义写成判据。
- **本轮新增第 2 个校验工具** `packaging/mutant_spotcheck.py`：把"已知存活清单"逐条改回去、
  跑定向判据看抓不抓得住（20 秒级，配合通用的 `mutation_probe.py` 用；按**行号**定位，
  行号对不上直接报错，不静默改错地方）。
- 验证：`tests/test_judgment_boundaries.py` **27 passed**（16 原有 + 11 新增）；
  `python packaging/mutant_spotcheck.py` ⇒ **12/12 被杀死**（工具的输出即证据）。
  ⚠ **整体**变异分数由完整探针复核（`packaging/mutation_probe.py --strict`）；本轮只**逐条**
  钉死了这 12 条，未扩大到别的模块。

### 新增（**时频编辑内核** —— 频谱编辑的地基 + 三条**实测出来**的效力边界）

> `docs/editor_roadmap.md §8` 第二步。上一步立起了"源 + 操作栈 → 目标文件"这条时间域通路，
> 这一步把**频域**通路立起来：时频选区的增益。仍然不碰 UI。

- 新增 `core/edit/stft.py`：WOLA STFT（`analyze` / `synthesize` / `roundtrip` /
  `window_sq_sum`），把 `docs/theory.md §12.1` 的 COLA/NOLA 判据落成代码。三个实现要点：
  ① **逐样本**除以真实窗平方和 `Σ_m w²[n−mH]`（不是常数缩放——后者在边界与不满足 COLA 的
  组合上留残差）；② `hop | n_fft` 时把帧拆成 `(R=n/hop, hop)`、对 R 条对角线做向量加
  （把 O(帧数) 次 Python 循环换成 R 次 numpy 调用）；③ 提供 `window_sq_sum()` 作为
  **COLA 判据的量化入口**（判据与合成复用同一份算法，不搞两份会分叉的实现）。
- 新增 `ops.BandGainOp`：时频选区增益（谱点被改 ⟺ 帧**中心**时刻 ∈ `[t0,t1)` **且**
  bin**中心**频率 ∈ `[f0,f1)`，双半开）+ `render.render_band` + CLI `--band f0-f1`
  （`--n-fft`/`--hop` 可调）。**时间域与频域操作不许混用**——顺序语义还没立判据，直接拒绝。
- **分块流式**：写出的跨度 = 选区两侧各外扩一个窗宽，块长与 hop 对齐（跨块帧网格一致）。
  实测（`packaging/band_perf.py`，10 分钟 / 230 MB 立体声）：**10 s 选区 = 1.88 s**
  （耗时 ∝ 跨度、与素材长度无关）、**整轨 = 24.24 s（24.8× 实时）**，
  峰值缓冲上界 ~34 MB 且**只随块长变**。
- 新增 **16 条判据**（`tests/test_edit_stft.py`）+ **5 条 CLI 判据**。

**三条实测出来的效力边界**（都进了代码注释、审计字段与 CLI 上报，不是"保险起见"）：

1. **写出的跨度必须外扩一个窗宽** —— 掩码按"帧中心"生效，而被改的帧的**窗口**会伸到
   选区外 ±`n_fft/2`。若在选区边界上把重建硬切，被压掉的成分会留一个**台阶**。
   实测（`packaging/stft_bounds.py case_1`）：硬切时边界处 8 kHz 包络 **0.1923 → 0.1167**
   （比值 0.61）、逐采样跳变 **7.56e-02**；外扩后同一处只有 **1.44e-04**（差 **523 倍**，
   而 1.44e-04 正是窗锥形的自然斜率）。判据 = Hilbert 包络的**逐采样最大跳变 < 0.01**
   —— 阈值两侧是 1.44e-04 与 7.56e-02，落差 5×/7×，不是手感。
2. **float32 素材"没被掩码覆盖"的采样也会动** —— 源是 float32 时量化底散布在**全频段**
   （实测：素材 3–9 kHz 的谱比谱峰低 **−145.5 dB**，正是 24 位 float32 的底），band 掩码
   把它一起乘 −40 dB ⇒ 波形随之变。实测 max|Δ| = 1.490e-08 = 2^-26，而素材峰值 0.3 的
   float32 ULP 是 2^-25 ⇒ **半个峰值 ULP**。判据据此写成"≤ 1 个**峰值量级**的 ULP"
   （锚在峰值上，不是每个采样各自的量级——这是"全频段底整体平移"，近零采样会有很多个
   "自己量级"的 ULP），而不是"逐位不变"：**后者对 float32 源根本做不到，写上去就是自欺**。
   float64（`DOUBLE`）素材则照旧要求跨度外逐位不变、跨度内未受影响区 ≤1e-12。
3. **分块粒度不许改变结果** —— `chunk_hops` 从默认 512 缩到 4（13 块），导出**逐位相同**。
   这条盯的是接缝：块的帧网格、两侧上下文、掩码取到的帧集合都会随块边界变。
- 审计新增字段：`domain`（`time`/`band`）、`n_fft`/`hop`/`window`、
  **`roundtrip_floor_dbfs`（实测的往返残差）** + `roundtrip_floor_samples`（在多少个采样上量的）、
  每操作 `applied_tf_points`（被改的时频点数；`applied_frames` 在频域路径下是"选区覆盖的
  采样数"，两者不是一回事，单列出来免得读者把前者当成编辑幅度）。
- CLI 把"**时频编辑不是位精确的**"作为**第三条显眼上报**（附实测残差与 ms 级效力边界）。
- `synthesize` 增**覆盖守卫**：`pad=0` 时帧网一旦被截断，`Σw²` 会塌到 ~1e-12、把浮点噪声
  放大成波形 ⇒ **抛错**（正确用法是"自带一个窗宽的真实上下文 + `keep` 只取中间"）。
- 变异测试 `TARGETS` 增 `core/edit/stft.py`；`core/edit/render.py` 的子集加入
  `tests/test_edit_stft.py`。
- 新增入库探针 **`packaging/stft_bounds.py`**（三条边界的**可复算**来源，不许手算当实测）
  与 **`packaging/band_perf.py`**（性能/内存口径）。
- 验证：全量 **796 条收集 / 795 passed**；`ruff` 全树绿；
  唯一失败是 `test_ui_material::TestToastLifecycle::test_burst_does_not_leak`——
  **全量并发下的已知 flaky**（单文件跑过；本轮与它无关，行为未变）。

### 新增（编辑器 **CLI 入口** + 编辑器方向的**市场 / 文献 / 数学基础**）

**① CLI 入口 —— 让 P0 内核"能用"**

- 新增 `src/audiocheck/edit_cli.py` 与 `[project.scripts] audiocheck-edit`：
  `audiocheck-edit <源> <目标> --gain -6 --at 12.5-14.0 [--subtype PCM_16] [--overwrite] [--audit 记录.json]`
- 退出码：`0` 成功 ｜ `2` 参数/用法 ｜ **`4` 源读不了** ｜ **`5` 目标已存在**（未给 `--overwrite`）；
  与既有 CLI 同一套"**不许 traceback**"约定（E2.1）。
- **两个必须显眼的如实上报**：目标编码放不下的**削波帧数**、源**疑似截断**（声称 vs 实读）。
- 新增 **8 条判据**（`tests/test_edit_cli.py`），含**真子进程**跑坏源 → `rc=4` 且输出无 `Traceback`。

**② 市场 · 文献 · 数学基础 —— 新增 `docs/editor_research.md`**

- **市场**（**二手来源，已逐条标可信度**）：RX 的 Spectral Repair = **模式化插值**；
  SpectraLayers = **分层 + 谐波/噪声分离**；Audacity"**缺外科级精度**"；
  以及一句关键的实话——"**文件缺字节不是修复能解决的**"（编辑器要继承判定层这条诚实）。
  ⚠ **不做**任何份额/用户量的量化断言（无权威源）。
- **文献（已核到出处）**：**Adler et al., IEEE TASLP 2012《Audio Inpainting》**——把削波/脉冲/丢包
  统一成"**已知位置的缺失**"，这是所有修复算法的**理论伞**；**PHAIN（IEEE 2024）**指出纯稀疏推广会
  **压小幅度**、忽略正弦的时间连续性；**Záviška/Rajmic, IEEE JSTSP 2020** 去削波综述
  （SDR + 感知指标大规模评测），其续作**明确指出"既有基准的削波程度偏温和"** ⇒
  **我们自建合成语料要用更狠的档**；**Lipshitz/Vanderkooy/Wannamaker** 抖动与噪声整形。
- **数学基础 → 已落纸**：新增 `docs/theory.md §12 编辑器数学基础`，六条**推导**各带"**它是判据，
  不只是知识**"：12.1 STFT 与完美重建（COLA/NOLA，**改 hop/窗必须重验**，且**视图与编辑
  必须共用同一套 STFT 参数**）；12.2 削波模型的**可辨识性**（饱和段位置/个数/深度 `c` 都是
  **可测输入**，不是猜；评测用 SDR + **能量比**防"压小幅度"）；12.3 inpainting 的
  analysis vs synthesis 稀疏（**选型必须用两条指标一起判**）；12.4 量化误差谱与噪声整形
  （**误差谱可算可画**⇒抖动可验证；⚠ 本仓 `render()` **尚无抖动**，如实挂账）；
  12.5 带限插值的**可测取舍**（通带纹波/阻带衰减）；12.6 相位感知与瞬时频率
  （**修复边界的相位不连续**是可测失败形态）。
- **落地映射表**：算法簇 → 计划中的 `Op` → **先建的客观判据** → 依赖文献。
- **未核清单（诚实留白）**：去噪经典出处、重采样理论出处、Godsill & Rayner、STFT 经典出处、
  市场量化数据——**下一轮补，不许当已核用**。

验证：全量 **774 passed**（+8）；`ruff` 全树绿；
`test_ui_material::test_burst_does_not_leak` 为**已知 flaky**（单文件 22 passed，与本次无关）。

### 新增（**编辑内核第一步：非破坏渲染** —— 编辑器四根柱子里的 I/O + 渲染两根）

> `docs/editor_roadmap.md §8` 的第一步。**不先碰 UI**：先把"源 + 操作栈 → 目标文件"
> 这条立起来——它立刻可验证，且是后面一切的地基。

- 新增 `core/edit/`：`ops.py`（**非破坏**操作模型 `GainOp` / `Project`，可 `to_dict()` 供审计）
  + `render.py`（**分块流式**渲染 + 返回**审计记录**）。三条硬约定：
  ① **非破坏保证**：目标 == 源 ⇒ `ValueError`（`overwrite=True` 也不行）；目标已存在默认拒绝；
  ② **选区半开 `[t0, t1)`**（样本 `k` 受影响 ⟺ `t0 <= k/fs < t1`），`gain_db == 0.0` 按 **no-op
     整条跳过** ⇒ **没动过的采样逐位不变**；
  ③ **削波不静默**、**截断源不许 traceback**（沿用 E2.1 规矩）：审计给出 `clipped_frames`、
     `read_incomplete`、`frames_claimed`。
- 新增 **13 条判据**（`tests/test_edit_render.py`）：浮点 / PCM_16 **往返逐位不变**、
  区外逐位不变、区内恰为设定 dB、**半开区间端点口径**、多操作按序复合、审计字段、
  **拒绝覆盖源 / 已存在目标**、削波上报、截断源上报；以及
  **★ 与测量内核互证**——用**同一个** `engine.analyze` 再量一次：素材做成"峰值只在选区内"，
  对选区 `−20 dB` 后**峰值必须落在选区外的 0.1**（≈ −20 dBFS）而不是 0.9×0.1（≈ −40.9）
  ⇒ **一个数字同时证明"作用对了"与"没越界"**。
- **纳入变异测试**：`TARGETS` 增加 `core/edit/render.py` 与 `ops.py`（导出结果就是用户手里的东西，
  抽样偏一位 / 端点判错都属于"安静地给错数"）。
- 验证：全量 **766 passed**（+13）；`ruff` 全树绿。
  `test_ui_material::test_burst_does_not_leak` 是全量并发下的**已知 flaky**（单文件 22 passed），
  与本次改动无关。

### 范围变更（**从"体检"走向"专业音源编辑制作"** —— 推翻一条已入库的旧决定，记账不改口径）

- **旧口径**（已入库）：`docs/ui_compare.md`「频谱编辑/修复 = RX 与 Audacity 的地盘，
  audiocheck 的职责是**判定不是修复**」＋ 本文件早前一句「频谱编辑、实时分析、房间声学
  **三项明确不追**」。**用户 2026-10-18 决定改为**：往**专业音源编辑制作的开源软件**靠、
  **填 Windows 市场**；前提**极致与专业**；可借鉴但**自研为主**。
- **新增** `docs/editor_roadmap.md`：市场事实（Win 原生 + 开源 + 专业级频谱编辑/修复
  **无强玩家**，缺口真实；但**开源不是卖点，能力才是**）、**差异化定位 = 可审计的修复**
  （竞品黑盒，我们每次修复给"改了什么/为什么/依据哪条测量/置信度/可复算"）、现状盘点
  （**播放 / 编辑模型 / 选区 / 撤销 / 渲染 四根柱子一根都没有**）、P0→P5 分级路线
  （每级带**可测出口判据**）、非目标、风险、第一步。
- **第一步（建议直接开工，不先碰 UI）**：`core/edit/` 的**非破坏增益渲染**
  （读 → 对时间选区 −X dB → 写），判据＝① 选区外**逐位不变**（真曲库文件）
  ② 与**测量内核互证**（改完再量，证明确实只改选中时段）。
- `docs/ui_compare.md` 该行已标注"**此条旧口径已被取代**"。

### 新增（**换刻度**：Bark / Mel / ERB / 线性 频率轴）

> 交接文档挂账 #1 收口。此前只画了 Bark 临界带的**线**（"底纹"），没做**换刻度**。

- 新增 `core/scales.py`：五个刻度的**解析变换 + 逆变换**，出处逐条写进 docstring
  （Traunmüller 1990 Bark / HTK-O'Shaughnessy 1987 Mel / Glasberg-Moore 1990 ERB-rate）。
  `display_grid()` 给出"在该刻度上等距"的显示网格；未知名字**回落默认**而不抛异常。
- 接线：`BaseCanvas.freq_scale` + `set_freq_scale`（幂等，延迟重绘沿用 P7 约定）；
  频谱图 / 相位场的显示网格、extent、ylim、网格线、Bark 线、**游标反算**全部按当前刻度；
  缓存键加入 `freq_scale`（换刻度必然重算）。
- `_apply_freq_axis(ax, f_max, p, scale)`：**标签永远是 Hz**，只有位置按刻度铺
  （读者要看频率，不是 Bark 数）。
- 窗口：命令面板新增"频率刻度：对数/Bark/Mel/ERB/线性"，QSettings 持久化。
- **一处数学**：`charts.bark` 直接绑定 `scales.bark`（此前两份实现，虽当前一致但迟早分叉，
  分叉后"临界带线"与"Bark 轴"会对不上且没人看得出来）；`bark_to_hz` 收口到 `scales.bark_inv`。
- 判据（22 + 5 条）：严格单调、互逆（相对误差 < 1e-9）、锚点
  （bark(1000)=8.527、mel(1000)=1000、erb(1000)=15.62）、网格在自身刻度上等距；
  **渲染级**验"Bark 下轴上限 == bark(f_max)、1 kHz 刻度落在 bark(1000)"；
  **纪律**：判定层不许 import 显示刻度（判据跟着显示选项变是最难发现的错误）。
- 验证：全量 **734 passed**；ruff 全树绿；视觉回归 0.0000%（默认仍对数，渲染未变）。

### 新增（判定层纳入**变异测试**：18 个存活 ⇒ 补 14 条边界测试 + 4 条等价登记）

> 此前变异测试只覆盖 `spectrum.py` + `dsp.py`——**测量再好，结论判错一样没用**。

- `mutation_probe.TARGETS` 增加 `verdict.py` + `causes.py`（子集：
  `test_verdict` / `test_c4_granularity` / `test_judgment_boundaries`）。
- **首轮结果：88/106 = 0.830，18 个存活**（低于 0.90 下限）——其中 **8 个是边界翻转**
  （`<=`→`<`、`>=`→`>`）。根因很一致：原有测试只验"阈值里面一点"和"外面一点"，
  **从没验过正好等于阈值**。而真实曲库实测 **57% 的文件距最近判定线 <3%**
  （见 `docs/real_library.md`）——边界恰恰是用户最常踩到的地方。
- 新增 `tests/test_judgment_boundaries.py`（14 条）：容器分界 45000/50000 Hz、
  `cd_full` / `hi_ok` / `hi_ratio` 的等号侧、`bit_padded and bit_origin` 的合取、
  `clip > clip_severe_pct` 与 `lufs > loud_war_lufs` 的**开区间**、用途矩阵的
  削波线与"两个动态量都缺"的合取、成因层的 `not hard and not gentle`（平缓滚降
  不许冒"中间带"候选）。
- 等价体登记 4 条（都写了"为什么"）：`_RANK_UNKNOWN` 哨兵 99→100、
  `_worst` 的 `>`→`>=`（平局赋同一个值）、`verdict.order` 90→91（无 91）、
  `causes.order` 89→90（与 verdict 平局，稳定排序保持注册序）。
- **实测：0.830 → 0.980（100/102，等价 18，存活 2）**，`--strict` 过。
  剩下 2 条（`low_pct` 的求和、`clip_advice_pct` 的开区间）补测试后**逐个定点验证
  均被杀死**（pytest rc=1）——见 `test_low_pct_is_sub_plus_bass_not_difference` 与
  `test_usability_clip_advice_boundary_is_exclusive`。

### 新增（E5 帮助面板：快捷键 / 视图 / **效力边界** / 阈值出处，内容全部派生）

- `widgets.HelpDialog` + 窗口 `open_help()`：**F1** 与命令面板"帮助 / 快捷键"均可打开。
- **内容不许手写**：快捷键来自 `window._cmds`（同一真源）、视图来自 `VIEW_KEYS`、
  效力边界来自 `causes.boundaries()`、阈值出处来自 `config.provenance_summary()`。
  手写的帮助会在加功能后过期，而**过期的帮助比没有帮助更坏**（用户照着做发现不灵）。
- 判据（4 条）：命令表里每个带快捷键的条目都必须出现在帮助里；效力边界逐条出现；
  空数据也能构造（可测性）；F1 真的在命令表里。
- 顺带：`test_truepeak` 的 `_write` 加"写完立刻验在 + 报大小"——该文件曾**偶发**
  `FileNotFoundError` 且至今**不可复现**（§6 欠账 #7），按"不猜着改"只加可观测性，
  下次复现能立刻区分"写没落盘"与"打开时丢了"。连跑 3 次无复现。

### 新增（**带宽判定边界余量**：把"贴着判定线"变成用户可见，门限一个没动）

> 背景：真实曲库暴露的脆弱点——96k 的「伪 Hi-Res / 半 Hi-Res」两档在谱上只差
> 约 2 dB（24–30 kHz：−44.4 vs −42.0 dB），判定却跨两级、全压在 `hi_ok=24.5` 上。
> **没有改门限**（拿一份曲库去调参是不允许的，`AGENTS.md` §3.4；要做对需要独立标注）。

- 新增 `verdict.bandwidth_margin(fs, cutoff_hz, th)`：按采样率档选出**适用的判定线**
  （44.1k：`cd_full/ltd/lossy`；48k：`full_48/up_44/ltd/lossy`；
  >48k：`hi_ok` 与 `hi_ratio×奈奎斯特`），返回**离最近那条线多远**（kHz 与 %）。
- 判定结果新增字段 `bandwidth_margin`；CLI 带宽行追加
  `｜ 距「<档名>」线 0.15kHz（0.7%）`。
- **刻意不设"敏感/不敏感"的二元判词**——那需要一个新的门限，而新门限必须有出处。
  这里**只报测量值**；`test_margin_is_disclosure_not_a_threshold` 钉住字段集合，
  防止后人偷偷把判词加进来。
- **真实曲库实测（450 文件，`out/music_final.json` 离线复算）**：
  距最近判定线 **<1% 的 69 个（15.3%）**、1–3% 的 187 个 ⇒ **256/450（57%）在 3% 以内**。
  典型例：`02. Pixel Time.flac` 截止 **21.15 kHz**，只比 `up_44=21.0` 高 **0.15 kHz（0.7%）**
  就判 **bad（疑似升频）**；而 `20.94 kHz` 的那个判 **warn**——0.2 kHz 之差跨两级。
  现在这两条的输出都会把"距线多远"印出来，读者不必自己去猜它有多确定。
- 验证：全量 **707 passed**（+5）；ruff 全树绿；`docs/real_library.md` 追加实测分布。

### 科研（**C 块文献债补齐**：有损/转码链识别 + 母带处理痕迹各补到同行评审锚点）

- ★ **有损/转码链识别 → 已核**：找到**单次有损的编码参数识别**的同行评审方法——
  **B. Kim, Z. Rafii. *Lossy Audio Compression Identification*. EUSIPCO 2018**
  （DOI 10.23919/EUSIPCO.2018.8553611）。更关键的是
  **Koops, Micchi, Quinton. *Robust Lossy Audio Compression Identification*. ISMIR 2024**
  （arXiv:2407.21545）**直接印证本项目的立场**：盲检测模型"在测试集近乎完美"是**假象**——
  它们**依赖训练集的截止频率**、跨参数泛化很差 ⇒ **截止点才是鲁棒的那条信号**，
  ML 分类器（含本项目已关闭的假 Hi-Res 分类器）容易过拟合。
- ★ **母带处理痕迹 → 已核**：**E. Vickers. *The Loudness War: Do Louder, Hypercompressed
  Recordings Sell Better?* JAES 59(5):346–351, 2011**（+ AES Conv. 129, 2010）；
  **Croghan/Arehart/Kates, JASA 132(2):1177–1188, 2012**。
  ⚠ 但 **PLR/DR 的判据定义与门限**仍来自行业工具 ⇒ 候选仍工程估计、门限不许升格。
- 落点：`docs/research.md §4`（两行状态更新 + §4.1 加"外部印证"）+ §5 的 C 块下一步改为
  "可动阈值"；`docs/thresholds.md` 不变（**门限本身仍未升级**——锚点证明"现象可测"，
  不等于"门限该取多少"）。
- **落地到用户可见文案**：成因候选「有损转码」的出处说明与"效力边界"里"有损码率的精确值"
  一条，改为引用该同行评审方法（Kim&Rafii EUSIPCO'18）——门限仍标工程估计。

### 绘图（感知刻度底纹 + 曲线轴同口径 + 色窗缓存；P10 **归因实测**）

- ★ **① 频谱图重绘 —— 先画像（`out/_spec_profile.py`）**：全长 `redraw()` ≈ 507 ms，其中
  **`draw()`（Agg 渲染）= 314–389 ms**；`_resample_rows` 缓存命中 0 ms、`_adaptive_vmin` 10 ms；
  `interpolation` nearest↔none 只差 ±4 ms（**不是杠杆**）。⇒ 大头是 **Agg 的文字/图像渲染本身**，
  不是我们的代码（环境注：本机 Agg 约为常规桌面 1/3 速度，见 `perf_probe.py` 口径注）。
  只取一个**纯收益**：`_adaptive_vmin`（648k 点 nanpercentile）按**同一把键**缓存
  （频谱图与瀑布图各约 −10 ms/次重绘）。
- ★ **② 感知刻度底纹**（"Bark 底纹"欠账的**线**形态）：频谱图与相位场的**对数频率轴**上，
  加 `bark_edges()` 的**临界频带边界线**（与频谱曲线同一套口径），极淡、在图元之上、**不遮内容**。
  ⚠ **只说"线"，不说"换刻度"**：可切换 Bark/Mel/ERB **刻度**仍是更大的事，**未做**（如实挂账）。
- ★ **③ 曲线轴同口径**：多文件对比图的频率轴此前只设 `xlim` ⇒ 退回默认对数轴的
  10²/10³/10⁴ 三个刻度（关键区 10k–22k 无刻度）。抽 `_apply_curve_ticks()`，与频谱曲线**共用一处**
  （视野感知刻度 + 2/5 次刻度）。
- **视觉回归按规矩重生成**（`visual_regression.py --render`）：只有 `spectrogram.png` 变化，
  即差异**只来自 Bark 线**、没有别的回退（2090‰ → 0）。
- 新增 1 条测试（曲线轴刻度多于默认三档）；全量 **701 passed**；ruff 全树绿。

### 绘图（瀑布图**数轴 / 建模精度 / 交互**三修 —— 先量后改，各带回归）

- ★ **数轴口径统一**：瀑布图的 x 轴（对数频率）此前只标十进制整数 `10/100/1k/10k`，
  比频谱图（1-2-5 主刻度）少了 20/50/200/500/2k/5k/20k——**同一份数据在两张图上刻度不一致**。
  新增 `_freq_major_pos_labels()` 复用 `_freq_axis_ticks()`，口径集中一处（3D 轴短，单位只标在最大刻度）。
  实测（`out/_axis_probe.py`）：瀑布旧轴缺 7 个刻度。
- ★ **修 z 轴（强度）刻度**：旧写法 `[round(vmin), −60, −40, −20, 0]` 在 vmin ∈ (−60, 0) 时
  **非单调**（如 vmin=−57.5 → `[−57, −60, −40, −20, 0]`，−60 掉到 zlim 底之外、被画到框外）。
  **实测：5% 分位 ≥ −25 dB 的"响度战"素材就会踩到**——而那恰恰是本工具最常看的素材。
  新增纯函数 `_db_ticks()` ⇒ **升序、去重、全部 ≥ vmin、永远含 vmin 与 0**。
- ★ **建模精度：时间向峰值保持**（`_peak_hold_cols()`）。瀑布此前是 `range(0, n, step)` **直接抽样列**
  ⇒ 落在未采样列上的**瞬态被整条丢掉**（频谱图一直做峰值保持——口径两份，而"咔哒/削波/瞬态"
  正是本工具要看的形态）。实测：奇数列上的 +40 dB 突发，旧实现看不到、新实现保留。
  游标读数（`_surf`）同步改用峰值保持后的值——读数与眼睛看到的填充片/脊线**同源**。
- ★ **交互性**：瀑布图加**滚轮缩放**（纯函数 `_zoom_after`，钳制 [0.6, 2.4]，与频谱图/对比图对齐）；
  双击 / R 复位时同时复位缩放。
- 新增 **9 条测试**（`tests/test_ui_waterfall.py`：刻度单调/去重/有界、频轴同口径、缩放纯函数、
  画布 z/x 刻度、瞬态保留）；全量 **701 passed**；ruff 全树绿；
  `perf_probe --strict` 过（P8 拖拽手势 ~1.0 s，未回退）。

### 修复（变异探针**白名单过期**：分数假跌 0.946 → 0.760，对齐行号后回到 **1.000**）

- **现象**：`mutation_probe --strict` 报 0.760 < 0.90 ⇒ 门禁红。但 18 条"存活"里
  **16 条其实是被 `EQUIVALENT` 白名单登记的等价体**——只是**行号对不上**了。
- **根因**：白名单键是 `路径:行:类型:old->new`，**按行号**索引；而 `_smooth_log_axis`
  向量化（`bc7ca95`）+ `detect_cutoff` v4.1（`2e76280`）两次改源码把行号整体推后
  12–26 行 ⇒ 一批本该等价的变异体被判成"测试缺失"。**这是量具坏，不是测试差**
  （探针纪律第 4 条就是"分数异常先怀疑量具"）。
- **修法**：① 重新对齐 14 条等价登记的行号；② 为两次改动**新引入**的 2 条分叉
  （`spectrum.py:31` `f.size<2`、`spectrum.py:187` `if jl2>jl`）补写等价理由
  （含实测数据与可达域说明）；③ 补 **2 条真测试**填掉 2 个真缺口：
  * `test_smooth_vectorized_preserves_input_dtype`（float32 输入必须回 float32——
    `!=`→`==` 会让 dtype 静默变宽）；
  * `test_lra_requires_at_least_four_short_term_blocks`（3.2 s→无 LRA、3.35 s→有 LRA，
    钉住 `st_ms.shape[0] >= 4` 这条边界）。
- **结果**：**59/59 = 1.000**，存活 0，等价 21；`--strict` 过。
  探针注释加 ⚠⚠：**白名单按行号键、改源码后会漂**——改完必须重跑核对，别把假跌当"测试差"。

### 修复（Windows **MAX_PATH**：>260 字符的路径曾被**静默丢弃**，现可收录并分析）

- **现象**：用户曲库里 2 个 **268 字符**的 `.wav`（日文长专辑名）被 `os.path.isfile`
  判 False ⇒ `collect_files` **无声漏掉**（第一轮结果 448/450，少的就是它们）。
- **实测支持面**（`out/_maxpath*.py`，2026-10-17）：`\\?\` 前缀被 **libsndfile
  （`sf.read`/`info`/`write`）、内建 `open`、`mutagen.File`、`os.walk`/`isfile`/`getsize`
  全部接受**；普通长路径则在 libsndfile 处直接 `System error`。
- **修法**：新增 `context.io_path()`（Windows 上补 `\\?\`、UNC 走 `\\?\UNC\`）与
  `display_path()`（剥前缀）。`collect_files` 用 `io_path` 探测/遍历、返回**剥净的普通
  路径**；`analyze`/`AnalysisContext` 只在**真正读文件时**换成 `io_path`；`views.py`
  的三个读点同步。⇒ 收录数 **450/450**，且结果里的 `path` 仍是干净路径。
  **终局全跑真实曲库 450/450、0 error**：2 个 268 字符文件拿到正常结论
  （`level=warn`，cutoff 20.84 / 21.04 kHz，44.1k）。
- 新增 `tests/test_paths.py`（7 条，含 Windows 真造 >260 路径的端到端）。
- 非 Windows 零影响（`io_path` 原样返回）。

### 修复（matplotlib **PendingDeprecationWarning**：`set_constrained_layout_pads`）

- `ui/charts.py` 每次建画布都触发 `set_constrained_layout_pads` 的 PendingDeprecation
  警告（它不是 `DeprecationWarning`，不受 `filterwarnings` 的 `ignore::DeprecationWarning`
  覆盖 ⇒ pytest 汇总里每次都刷）。改用 `fig.get_layout_engine().set(...)`，**留白口径不变**。

### 科研（**编码器截止表取到一手来源** + 母带痕迹找到同行评审锚点）

- ★ 「编码器截止表只有社区资料、**未取到一手表格**」这一记账**已更正**（2026-10-17）：
  取到两个**第一方**来源——① LAME 项目官方 GPSYCHO 页（128 kbps 低通约 15.5 kHz、
  256 kbps 不做低通）；② FFmpeg 源码宏 `AAC_CUTOFF_FROM_BITRATE`（AAC 立体声
  192/256/320 kbps ≈ 18.0/20.0/22.0 kHz）。⚠ 但 `ltd=19.0`/`lossy=16.0` 这两个**判据门限
  本身仍无外部出处** ⇒ 类别**保持"工程估计"**。记录见 `docs/thresholds.md §3.2`、
  `docs/theory.md §5`。
- 「母带处理痕迹」从"仍未清"→**部分已核**：找到同行评审锚点 **Croghan, Arehart, Kates,
  JASA 132(2):1177–1188, 2012**（重度压缩/限幅损害听感）；但 PLR/DR 的**判据定义与门限**
  仍来自行业工具 ⇒ 候选仍"工程估计"、门限不许升格。见 `docs/research.md §4`。

### 精度（`detect_cutoff` v4.1：修一个**实测的系统性偏差** —— 最大误差 25% → 1.3%）

- ★ **发现**：把截止点检测的误差**曲面**量出来（入库探针 `packaging/cutoff_calib.py`，
  密集网格 × {44.1k,48k,96k}），96k 低截止处**系统性偏高**：**+25%（6 kHz）、
  +15.6%（8 kHz）、+14.7%（9 kHz）**；44.1k 亦有 +10%。此前 E1 属性测试只敢覆盖
  ≥12 kHz、容差松到 15%（当时如实登记"9–12 kHz 精度判据未立"）。
- ★ **机制**（打中间量证实，不是猜）：粗候选来自 1/12 倍频程步进，常落在真边缘
  上方 **1.2–1.33×**；此时用来算阈值的**通带参考窗** `[fc/1.6, fc]` **跨骑**了边缘
  ⇒ 窗内通带占比 <50% ⇒ 中位数**塌到阻带底** ⇒ `thr=(通带+阻带)/2` 深陷数值
  噪声底 ⇒ 命中的是"噪声底越过该电平"的位置，**系统性偏高**。
- ★ **修法（最小改动）**：通带参考窗改为**带间隙、不跨骑**的 `[fc/2.56, fc/1.6]`
  （=1.6² 带状，因候选 <1.6× 真边缘，此窗必在通带内）。**粗搜索与守卫窗一字未改**
  ⇒ **"是否判为带限"的集合不变**（截止点为 None vs 有值 的决定不动），
  只有带限文件的截止点**读数**变准。
- ⚠ **但读数变化会传导到判定等级**（先前的草率结论已更正）：判定把 `cutoff_hz` 与
  `hi_ok=24.5`/`ltd=19.0`/`lossy=16.0` 等门限比较，读数下移会让**跨门限的文件改档**。
  用同一份真实曲库做**只换 cutoff 实现**的对照跑（`out/music_old.json` vs
  `out/music_new.json`，448 文件；复算器 `packaging/cutoff_impact.py`）实测：
  - **等级翻转 55/448（12%）**：`ok 82→78`、`warn 226→227`、`bad 140→143`；
  - **截止点中位变化 0.12 kHz、最大 11.35 kHz**（96k 文件的旧值虚高最甚，如
    某 96k 曲 **35.13 → 23.78 kHz**）。
  这是**修正在起作用**：旧值把 96k 内容截止点报得虚高 ⇒ 误判"真 Hi-Res"；
  新值更接近真值（合成标定 ≤1.3%）。**口径变更记账**：`docs/theory.md §1.6`、
  `docs/real_library.md` 更新为新分布（全量 450 文件：**ok 78 ｜ warn 229 ｜ bad 143**）。
- **实测**（250 Hz 步长扫 [6000,21000]）：最大相对偏差 **25.0% → 1.27%**、
  最大跨采样率散布 **10.2% → 0.69%**。
- **属性测试收紧**：`test_cutoff_consistent_across_sample_rates` 从
  「≥12 kHz、容差 15%」→ 「**[6,21] kHz、容差 3%**」（2.4× 余量）。
- **口径变更 1 条**：带限文件的 `cutoff_hz` 读数整体下移、更接近真值；**并因此移动 55/448 的判定等级**（见上）。记录见 `docs/theory.md §1.6`。

### 性能（内核：**一趟解码、一次过采样** —— 合成语料 −40%，真实 12 分钟文件 −36.5%）

**先画像再动手**（新增入库探针 `packaging/kernel_bench.py`）。真实文件上逐分析器计时，
热点**不是**"我以为"的平滑循环，而是 **`timeline`（占 44–51%）** 与 `loudness`——
画像推翻了直觉，这就是"先量再改"的价值。

| 改动 | 做法 | 实测 |
|---|---|---|
| ★ **真峰值一次过采样、整轨与逐段共用** | `dsp.true_peak_scan()`：分块读（带 `TP_TRIM` 上下文、只取中段——与 `true_peak()` 同一套）时**同时**更新整轨最大与落在本块内的各段最大；`true_peak()` 改成它的**一个入口**（不存两份实现）；`timeline` 不再逐段过采样（最多 96 遍）、不再写 96 个临时 wav | timeline **15.38 → 5.70 s** |
| ★ **去掉 `dc_offset` 的整轨重复解码** | `basic` 那一趟顺手累加逐声道有符号和（`b.sum(axis=0)`，与 `dsp.dc_offset` 浮点口径**逐位一致**），`dc_offset` 分析器取用 | dc_offset **1.87 → 0.000 s** |
| ★ **`_smooth_log_axis` 向量化** | 8193 点**逐点布尔掩码**（0.245 s/次）→ `searchsorted` + 前缀和（0.0007 s，**357×**）；语义一字未改，逐点实现**保留**下来当独立参照（测试拿它当 oracle） | spectrum 0.81 → 0.47 s |

**前后对比**（同一语料、同一脚本；"改动前"用 `git worktree` 在 `51b613d` 上跑）：

```
合成 44.1k/16 180 s ： 9.412 →  5.476 s（−41.8%）
合成 96k/24   180 s ：20.170 → 12.144 s（−39.8%）
真实 44.1k 13.6 分钟：35.08  → 22.27  s（−36.5%）
真实 96k    3.5 分钟：27.98  → 18.08  s（−35.4%）
全量测试：            254.7  → 189.8  s（−25%，同一批机器）
```

**口径变更 1 条（逐段真峰值）**：段真峰值从"把段当**独立文件**过采样"（两端硬切
⇒ 滤波器振铃**补出假峰**、且每段一次磁盘往返）改成"整轨一次过采样后取段内最大"
⇒ **更准**。整轨真峰值**逐位不变**（同一算法，且已用另一条路径 `_true_peak_of` 互证）。
段内帧→4× 采样索引的映射由 `test_true_peak_scan_spans_locate_hot_region` 钉住
（冷段 ≈ −10.5 dBFS、热段 ≥ 0；映射错一位不会报错，只会安静地给错数）。

**顺带修掉一个真问题**：`true_peak_scan` 遇到**截断**（读不满）时若一次要的帧太多会
直接抛 ⇒ `frames_read` 停在 **0**，连前面可读的内容一起丢（实测踩到）。改为**块长减半
重试**（下限 65536，与 `basic` 的读块一致），并报 `incomplete`。

**新增 5 条测试**：向量化 vs 逐点 oracle（三网格 × 三 fraction + 非单调兜底）、
scan 整轨值 vs 独立过采样参照、段定位、截断上报、`loudness` 的**哨兵语义**
（`true_peak_dbfs=None` 是合法结果，不许被当成"没传"而重算）。全量 **683 passed**。

### 新增（**外部有效性第一轮：真实曲库大盘点** —— `docs/real_library.md`）

- **第一次在真实音频上跑判定**（此前所有"答案已知"验证都在**合成**语料上）。
  语料：用户曲库 450 文件 / 17.7 GB（405 FLAC + 28 MP3 + 17 WAV），实跑 **448 个**
  ｜ **合格 80 ｜ 有疑 228 ｜ 不达标 140**（约 19 min，8 进程）。
- ★ **独立复核 5/5 一致**：**不看工具的 cutoff**，自己算 Welch 谱核对
  升频 / 伪 Hi-Res / 半 Hi-Res / 有损溯源 / 真 Hi-Res 各一例——工具判定都站得住
  （例：88.2k 那首在 30–45 kHz 有 **−15.7 dB** 强能量，判"真 Hi-Res"是对的）。
- ★ 已关闭的**假 Hi-Res 分类器**第一次有真实数字：参照『真』(n=28) 误报 **0.071**、
  参照『假』(n=20) 检出 **0.100**，**两组概率分布几乎完全重合**（中位 **0.412 vs 0.418**）
  ⇒ 该特征在真实曲库上**没有区分力**，"升格为判据"的前提**被否掉**（不是"还没做"）。
  详见 `docs/research.md §4.1`。
- ⚠ **薄分界如实挂账**：96k 的「伪 Hi-Res」与「半 Hi-Res」在 24–30 kHz 只差 **~2 dB**
  （−44.4 vs −42.0），判定却跨两级、全压在 `hi_ok=24.5` 上。**没有改阈值**——
  改它就是拿一份曲库去调参；要做对需要**独立标注**（本轮没有）。

### 修复（真实曲库暴露的三个缺陷 —— 合成语料测不出来）

- ★ **`bit_depth_origin` 在 24bit 立体声上整个失效**：`np.bitwise_or.reduce(x)`
  默认**只沿 axis 0** 归约 ⇒ 立体声（读成 `[n, 2]`）返回**每声道一个值** ⇒ `int()`
  抛 `TypeError: only 0-dimensional arrays can be converted to Python scalars`，
  被引擎吞成"分析器告警" ⇒ **"位深升位（伪 Hi-Res）检测"在 24bit 立体声上永远跑不出来**，
  而那正是伪 Hi-Res 的主要形态。此前 `tests/test_codec.py` 的位深用例**全部是单声道**
  （1-D ⇒ 默认 axis=0 恰好等于全归约），恰好绕过。修：`axis=None` + 3 条立体声回归。
- ★ **截断文件"能打开、读数错、只说一句英文"**：库里 3 个长曲（Roland Kayn）头部声称
  49.1/29.7/15.1 min、`sf.info()` 照旧返回满长帧数，实际只读到 **99.5–99.9%** 就抛
  `Internal psf_fseek() failed.`（下载没下全的典型形态）。修：`basic` 接住读异常，
  产出 `read_incomplete` / `read_frames` / `read_frames_claimed`，并在 `errors` 里写
  **可读**的"疑似截断（头部声称 N 帧，实读 M 帧 = P%）"。**刻意不判 `level=error`**
  （可读 ≥99.5%、统计有效；判 error 会把整条结论丢掉）——取舍写在源码注释里。
  回归：截断到 70% 的确定性 FLAC（`test_robustness.py`）。
- ★ **超过 Windows MAX_PATH（260）的路径静默消失**：库里 2 个 **268 字符**的 `.wav`
  （日文长专辑名 + 长曲名）`os.path.isfile` 为 False ⇒ 从结果里**无声消失**。
  同类坑还有我自己造的清单：PowerShell `Out-File -Encoding utf8` **带 BOM** ⇒
  `--list` 用 `utf-8` 读会把 BOM 留在**第一行**路径上（实测 6 个文件只分析出 5 个）。
  修：① `--list` 改 `utf-8-sig`；② CLI 新增 `_warn_uncollected()`——显式目标没被收录就
  **打印原因**（含 MAX_PATH 提示），遵守"查不到与没问题必须可区分"。
  **未修**：MAX_PATH 本身（>260 的真实文件仍不可达；`\\?\` 是否被 libsndfile 接受
  **未验证**），如实挂账。

### 修复（E1.2 量具第三次坏：变异探针**没有单体超时** ⇒ 一个病态变异体挂死整轮）

- **现象**：`python packaging/mutation_probe.py` 在**同一个** pytest 子进程上耗掉
  **12 分钟**仍不返回（CPU 一直满）；`--max-seconds` **拦不住**——时长检查只在
  **两个变异体之间**做，而 `subprocess.run` 没有 `timeout`。
- **根因（两个，实测定位）**：① `spectrum.py` 的粗搜索 `cur = cand / step` 被改成
  `cand * step` 后**向上**走 ⇒ 非终止；② `dsp.py` 的 `if acc_n == hop` 改成 `!=`
  会使 `loudness` 内层 `while p < y.shape[0]` 因 `take = 0` 空转；同理
  `while pos < total` 改成 `<=` 会让 `true_peak` 永不退出。
- **修法**：新增 `--mutant-timeout`（默认 180 s，实测两个定向子集分别只要 17 s / 12 s），
  `_run_subset` 带 `timeout`，超时返回码 3、**计为被杀**（测试以"挂起"而非断言的方式
  检出病态变异体，与主流变异框架 timeout=killed 口径一致），报告单列"其中超时 N"。
  并逐体打印进度 + `flush=True`——没有进度就**看不出量具是否挂了**。
- **旧账更正**：原记的基线 **0.524** 是 `--max-seconds 300` 的**部分**结果。修好量具后
  跑满 80 个候选，**首个可复算的完整基线 = 0.390**（30/77，47 存活）。

### 修复（E1.2 补齐：分数 0.390 → **0.946**，填掉 14 条测试缺口）

- **新增 14 条测试**，全部落在探针的定向子集里（`test_cutoff` / `test_engine` /
  `test_verdict` / `test_dsp` / `test_dsp_standard` / `test_truepeak` / `test_timeline_loudness`）：
  * ★ `smooth_power` 的 **dB 刻度**（平坦谱必须逐点回到原值）——此前只被一条松断言
    `slope > 40` 覆盖，系数改成 `11/10`、写成 `10**(db*10)`、或用 `10**(db/15)`
    都能照样通过。这是本轮最实的一个缺口（换算错了"dB"就不是 dB）。
  * `smooth_log` / `smooth_power` 默认平滑宽度 **1/6 倍频程**（且默认真的生效）。
  * `detect_cutoff` 输入守卫：`None` / 长度 **15 与 16 的边界**（<16 拒绝、≥16 可处理）。
  * 搜索下界 **100 Hz**：90 / 110 / 130 Hz 砖墙都必须检出——**只测 130 会得到一个
    "这条判据有牙齿"的错觉**（下界抬到 150 时只有 90/110 会漏，130 的候选落在 160 Hz）。
    这一条本身就是本轮实测出来的。
  * **对数网格**上的定位精度 ≤2%：线性网格高端一个 bin 只有 0.017%，藏得住
    "边缘偏一个 bin"（对数网格上一个 1/12 倍频程 bin 是 5.9%）。
  * `TP_OS` **恰好 4**（实测 5× 与 4× 的真峰值读差 **0.03–0.04 dB** ⇒ 提高倍数不是
    免费的、且会动读数，所以改它必须是有意识的决定）。
  * `PHASE_UNDEF_DB`（弱侧低 35 dB ⇒ ρ 必须置 0）、`ANTI_FLOOR_DB`（低 45 dB 的反相
    内容不计入）、`ANTI_COH_SEC` **恰好 0.5 s**。
  * `phase_field` **真的被调用**（此前只有一条"源码里复用了它"的断言，从没调用过）
    + 两个入口的 **STFT 窗口径 4096**（4096/4097 的频点数都是 2049，只有间距能区分）。
  * `spectral_features` 全零功率 ⇒ `{}`；`stereo_features` 缺声道 / **1024 边界**；
    `loudness` 退化采样率（fs<10 ⇒ hop=0）；短时块核必须是**均值**——`lra` 是极差、
    对常数平移不敏感，所以只有 `st_max`/`st_min` 抓得到。
  * ★ 静音守卫（`SpectrumAnalyzer`）：极安静素材必须判 `silent`，而**峰值 0.0 dBFS
    不许被判静音**（老坑：`x or -999` 会因 0.0 是假值踩它）。
- **等价登记 21 条**（共 24 条），每条都写了"为什么"——其中**可证明**的有 4 条：
  `kern` 在 ρ̄ 的分子分母同比出现；`j1 > j0` 与 `>=` 在 j1==j0 时都返回 0.0；
  `run >= need` 与 `> need` 定位的是同一条连续段的上端（`j0 = i + need − 1` 不变）；
  `order 30→31` 在现有 order 集合里不改变排序。
- **结果**：**0.946**（53/56，1 个超时计为杀死，3 存活）。剩余 3 条**低价值边界**如实挂账：
  `spectrum.py:25`（窗口内恰好 2 点）、`spectrum.py:87`（粗搜索步长 1/12）、
  `dsp.py:138`（`st_ms` 恰好 4 行）——三者都需要"恰好相等"才分叉，未构造反例，
  也**不是为了凑分去测**（`--strict` 下限已提到 0.90）。
- **验证**：全量 pytest 绿；ruff 全树干净。

### 口径变更（**局部反相指标纠错** + `anti_scope` 接入判定）

> 两次实测纠错，都是"指标说一套、现实另一套"。第一条直接改了**用户可见的错**。

- ★ **发现并修复**：`dsp.anti_phase_scan` 把**左右不相关的正常立体声**判成
  "反相明显"——实测独立双声道噪声的反相能量占比 **32.9%**，而**真缺陷**
  （某频段反相）只报 **0.76%** 被漏掉。根因：单帧 ρ 对不相关内容是**重尾**
  （ρ<−0.5 占 ~33%，−0.98 时仍 6.3%）⇒ 换任何单帧门限都压不下去。
  **修法**：改用**跨帧相干** ρ̄ = `2·Re⟨L·conj(R)⟩_t/(⟨|L|²⟩_t+⟨|R|²⟩_t)`
  （`ANTI_COH_SEC=0.5s`），随机符号被平均消掉：不相关 → 0，真反相 → −1。
  实测：独立立体声 **0.07%**、真音乐样（相关 0.5）**0.01%**、全带反相 **100%**。
  `ANTI_RHO` −0.5 → **−0.9**（相干口径）。**GUI 此前会把正常立体声标成
  "反相范围：明显"——这是用户可见的错误结论，本轮修正。**
- **`anti_scope` 从"只算不判"接入判定**：`dsp.anti_phase_scan` 的 docstring
  写着"给判定用"，但判定层从没读过它（grep 实证）。现在
  `verdict.axis_channels` 对"明显/严重"告警（**可用性轴**，不驱动等级）。
  反相是**现象层的立体声缺陷**（错线/反相叠加/极性翻转），不是成因，
  所以不进 `causes` 候选集。
- **C0-3 同型收编**：三个分档门限原硬编码在 dsp 里（设置里改不到、也没进普查）
  ⇒ 新增 `anti_scope_minor/major/severe`（0.01/0.10/0.50），调用点显式传
  `ctx.thresholds`；登记 `PROVENANCE` + 普查 `FIELD_MAP`（实测三档两侧都有样本：
  36/7、35/8、1/42）。`stereo.py` 的 `except: pass` 改为**写进 errors**
  （不再静默）。
- 回归：`test_anti_phase_scan_uncorrelated_stereo_is_not_flagged`（钉 33% 教训）、
  `test_anti_phase_scan_correlated_stereo_is_clean`、
  `test_channels_warns_on_severe_anti_phase` 等 8 条新增。
- 验证：全量 **654 passed**；普查 `--strict` 过；ruff 全树绿。

### 修复（E2.1 **不可信输入的显式错误路径**：CLI 曾对非音频文件打 traceback）

- **发现**：`analyze()` 在打开/解析容器失败时**抛 `LibsndfileError`**，批量路径
  `pipeline._job` 不接、`cli` 主路径也不接 ⇒ 用户拿到**整条 Python traceback**，
  退出码 1。规格明确要求"有错误码 + 可读信息，不许 traceback"。
  （GUI 侧本来就接了 `AnalyzeWorker.fail`，所以只有 CLI/批量暴露。）
- **修法（在引擎层，CLI/GUI/JSON 三处一并受益）**：`engine._unreadable()`
  产出与正常结果**同构**的显式错误结果（`level="error"`、
  `verdict="**无法读取**：<原因>"`、`errors=[...]`、缺的字段给 None），
  `analyze()` 不再抛异常；`pipeline._job` 再兜一层。
- **退出码**：新增 **4 = 有文件读不了**（与 3 = 分析告警 区分）；
  `cli.show()` 对错误结果只打**一行可读信息**。
- 实测：非音频/空文件/伪造头部 ⇒ `level=error` + 可读 verdict + **rc=4**；
  好文件仍 rc=0；JSON 导出带错误结果。

### 新增（E2 健壮性与安全：畸形语料 + fuzz + SBOM + 可复现构建结论）

- `packaging/robustness_probe.py` + `tests/test_robustness.py`（18 条）：
  **12 条确定性畸形语料**（空 / 纯文本 / 截断 RIFF / 半截 WAV / 只有头部 /
  全零 / 伪造巨大长度 / 只有 ID3 / 截断 FLAC/Ogg / 伪 MP3 头）+ **有界随机变异**
  （固定种子，可复现）。三层判据：不抛异常 / `level` 只能取四值之一（防静默通过）/
  **真实 CLI 子进程输出里不许有 `Traceback`**。
  **实测：12/12 全部走显式路径；fuzz 34 个变异体未发现崩溃**
  （如实报告：不是"通过安全测试"，是这次没撞上）。
- `packaging/sbom.py`：**离线** SBOM（CycloneDX 1.5 风格），从已安装分发元数据
  沿 `Requires-Dist` 递归收传递依赖（实测 16 个组件）。审计状态**如实写进产物**。
- `docs/supply_chain.md`：SBOM / 审计 / 可复现构建的实测记录与**未做项**。
- **可复现构建（E2.3）实测结论**：默认构建**不可**逐字节复现——两次 wheel
  大小相同、逐条目内容与顺序完全一致，差异仅 **12 字节**，全在 zip 局部文件头的
  **mtime**，且只出现在构建期复制生成的 `dist-info/licenses/LICENSE`；
  设 `SOURCE_DATE_EPOCH` 后**逐字节一致**（实测 sha256 相同）。
  只给结论与差异来源，**未**改默认构建流程（属发布批次决定）。
- CI 新增 `supply-chain` job（健壮性 `--strict` + SBOM 上传为 artifact）。
- **未做（如实登记）**：`pip-audit` 依赖审计需联网，本机离线 ⇒ 未做。

### 修复（E1 属性测试发现的**核心判定缺陷**：截止点漏检 ⇒ "满带宽"假阴性）

- `core/analyzers/spectrum.py::detect_cutoff` 的粗搜索自高向低取**第一个**满足
  `min_drop_db` 的点；当截止点约为奈奎斯特 10–25% 时，该候选会落在**过渡带**上，
  随后被 `min_stop_db` 守卫否决并**整体返回奈奎斯特** ⇒ 内容被判"满带宽"。
  实测命中率损失（250 Hz 步长扫 9–21 kHz）：**44.1k 6/53、48k 6/53、96k 12/53**，
  含 **44.1k@16.5 kHz** 这种典型有损截止。
- 修法：守卫不过就**从候选下方继续搜**，直到找到真正的砖墙或搜到下限。
  **可证明不回归**：原本成功的输入其首个候选即过守卫 ⇒ `coarse` 与结果逐位不变
  （实测 44.1k 矩阵 9 个点全部逐位一致；48k@6k、96k@10k/12k 由"未检出"变为
  6416 / 10852 / 12797 Hz，误差 6.6–8.5%）。修复后三档采样率的漏检密度 **18 → 0**。
- 回归：`tests/test_props.py::test_low_cutoff_is_not_reported_as_nyquist`（9 个
  参数点）+ `test_cutoff_consistent_across_sample_rates`（属性，容差按实测标定）。
- 纪律：C1 阈值普查 **44/44 检出率不变**、全部字段仍有区分力；全量测试绿。

### 新增（E1 测试有效性：属性测试 + 键盘 UI 自动化 + offscreen 收编）

> 依据 E 块提示词 E1。**不引入新依赖**：属性测试用已装的 `hypothesis`，
> UI 自动化用随 PySide6 一起的 `PySide6.QtTest`（未引 pytest-qt）。

1. **属性测试** `tests/test_props.py`（15 条）——把四条**物理/数学必然**的不变量
   从"手挑几个点"扩展到随机输入（Hypothesis 生成 + shrink）：
   * **幅度单调**：任意频率/幅度的正弦，幅度减半 ⇒ LUFS 降 6.0206 LU
     （容差 0.15 是 `loudness` 的 `round(...,1)` 显示口径；另有一条直接量
     `_gated_loudness` 的**精确** 20·log10(2)）。
   * **采样率不变性**：同一带限内容在 44.1k/48k/96k 下检出一致——**容差按实测标定**
     （250 Hz 步长扫 [12000,21000] × 三档率，最大偏差 10.8% ⇒ 取 15%）；
     并如实登记 9–12 kHz 段偏差可达 15%、精度判据**未立**。
   * **窗长权衡**：窗越长 Δf 越小、Δt 越大（量 `views._stft_power` 真实现的轴间距）。
   * **位深单调**：量化 SNR ≈ 6.02·B + 1.76 dB 且随 B 递增；另有"内容量化到 q bit
     装进 24bit 容器 ⇒ 检出有效位 = q"（写入须走 int32 左移对齐——实测 `sf.write`
     直接喂 float 会因 libsndfile 的 `2²³−1` 缩放把低位填满）。
2. **键盘主流程 UI 自动化** `tests/test_ui_keyboard_flow.py`（6 条 + 15 子测试）：
   命令面板键盘执行、**每张画布 Tab 可达**、快捷键不抛异常、载入文件后端到端切遍
   10 个视图。**抓到 3 个真实缺陷**（见下）。
3. **offscreen 收编**：21 个 `test_ui_*.py` 各自的
   `os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")` 收进 `tests/conftest.py`
   一处（保留"仅当未设置时"语义；pytest 先加载 conftest，故仍早于任何 PySide6 导入）。

### 修复（E1 UI 自动化抓到的两个键盘可达性缺陷）

- **命令面板缺视图**：跳转列表手写，B14 加了「相位场」却漏登记、`compare` 也从没进过
  ⇒ 键盘到不了这两个视图。改为**从 `VIEW_KEYS` 派生**（唯一真源，闭包用默认参数绑定），
  并加守卫 `test_command_palette_lists_every_view`。
- **两张画布 Tab 不可达**：`SpectrumCanvas` / `CompareCanvas` 是 `ClickFocus`
  （鼠标可点、Tab 到不了），与其余 6 张 `StrongFocus` 不一致。改为 `StrongFocus`；
  a11y 测试由"`!= NoFocus`"**收紧为"含 `TabFocus`"**（原判据放过了 ClickFocus），
  `a11y_report` 的清单列同步改为**Tab 可达**。

### 修复（P7 那条延迟重绘测试的偶发）

- `test_ui_timeline.py::test_theme_change_defers_canvas_redraw_then_completes` 依赖
  `MainWindow` 的初始配色，而 `_restore()` 会从 **QSettings** 读上次主题
  （被别的测试改过就变浅色）⇒ "深→浅"换肤没换色 ⇒ 全量下偶发失败。
  改为**独立画布 + 显式深色起点 + 前提自检**，并用**显式 `paintEvent`**
  触发延迟帧（离屏下事件循环不保证投递 `update()` 的 paint）。连跑 3 次稳定。

### 新增（E1.2 变异测试：自实现 AST 注入 + 进 CI）

- `packaging/mutation_probe.py`：**自实现**变异测试（本机无 `cosmic-ray`/`mutmut`，
  离线装不了；项目先例是自写 CIEDE2000/CVD——测量脚本不进运行时，不构成新依赖）。
  AST 注入四类故障（比较符 / 算术符 / 布尔符 / 数值常量），把 `src`+`tests` 复制到
  临时工作区跑**定向测试子集**（`-x` 早停），报分数 + **逐个存活变异体的结论**。
  `--max-seconds` 限定时长（E1 要求）；`--strict` 分数 < 基线即红。已进 CI
  （`mutation` job）。
- **首轮实测：有效变异体 21 ｜ 杀死 11 ｜ 存活 10 ｜ 等价 3 ⇒ 分数 0.524**
  （E0 基线未立，此值即首个基线，`MIN_SCORE = 0.50`）。
- 登记的**等价变异体**（语义不变，测不出来是对的）：`TP_OVERLAP ±1`、
  `TP_TRIM ±1`、`_smooth_log_axis` 的 `fraction` 默认值（**从不被使用**——
  两个调用方都显式传参）。
- 列出的**测试缺失**（真发现，10 条）：`smooth_log`/`smooth_power` 的 `fraction`
  默认值、`detect_cutoff` 的 `None` 守卫（无测试走 None 输入）、`cur > floor_hz`
  与 `cut < min_hz` 的边界比较、`f` 上的 `<=` 比较、`TP_OS = 4`、
  `PHASE_UNDEF_DB`/`ANTI_FLOOR_DB` 等常量未钉。
- ⚠ **开发中量具坏了两次，症状都是"全存活/分数 0"**（看起来像"测试极差"）：
  ① `_candidates` 用 `id(node)` 标识目标，而变异时**重新 parse** 了一棵树 ⇒ id
  永不匹配 ⇒ 变异静默不生效；② 未把工作区 `src` 放进 `PYTHONPATH` ⇒ 本仓库是
  editable 安装，**真实仓库**代码被 import（`tests/test_cutoff.py` 这类
  **不带 `sys.path.insert`** 的文件尤其如此）⇒ 变异体根本没加载。
  教训：**分数异常低先怀疑量具**。两条都写进探针 docstring。

### 新增（E1.3 视觉回归：关键视图与入库基线逐像素比对）

- `packaging/visual_regression.py` + `tests/test_visual_regression.py`（2 条）：
  **自实现**像素比对（本机无 `pixelmatch`，比对本身很简单，不必引依赖）。
  三张关键视图（频谱曲线 / 频谱图 / 时间定位）在**固定 100 DPI + 内置 dark 主题 +
  固定 720×430**下渲染，与入库基线 `tests/baselines/*.png` 逐像素比。
- **三条纪律逐条落实**：① 固定 DPI/主题；② 阈值显式（单通道容差 `PIXEL_TOL=12`、
  允许差异像素 `DIFF_FRAC_TOL=0.5%`）；③ 基线图**入库**（+ `meta.json` 记录
  matplotlib 版本 / 平台 / DPI / 尺寸 / 主题 / 每图 sha256）。
- **实测确定性**：同代码两次渲染差异像素 **0.0000%**（0.5% 阈值余量极大）。
- **诚实边界**：`meta.json` 记了 matplotlib 版本与 `sys.platform`，**不符即跳过并说明**
  ——跨版本/跨平台字体栅格化本来就会变，假装可比会让 CI 无意义地红。
  因此**未加进 CI**（基线是 Windows+该版本钉住的；跨平台需各留一套基线）。
- 顺带修一处**探针自身**的数据构造错：合成 `S` 的行数必须 == `len(f)`
  （不匹配时 `_resample_rows` 会取到空切片而抛 ValueError）。

> **E1 未完成项**：无（E1.1/E1.2/E1.3/E1.4/E1.5 均已落地；E1.2 的"测试缺失"10 条
> 作为**发现**登记在 `mutation_probe` 的输出里，未逐条补测）。

### 口径变更（B11-B 细腻级：720 行 + 色窗自适应）

> 依据全在 `docs/views_coverage.md`（E1 先量后改的实测矩阵与基线）。

1. **渲染行 360 → 720**（`charts.py::RESAMPLE_ROWS`）。实测收益边界：
   @1kHz 行距 19.71→9.79Hz（**2.01×**，行距粗过 bin 的高频区定位误差减半）；
   **50Hz 占行不变（7→7）**——低频由 bin 泄漏限制，翻倍行数加不了信息
   （B11 提示词原判据被实测推翻，判据按实测改写）。渲染 1.62→2.00ms（1.24×）。
2. **频谱图/瀑布图色窗下界改自适应**：`vmin = max(−db_range, 内容底 p5 − 10dB)`
   （`charts.py::_adaptive_vmin`）。
   * **db_range 仍是硬上界**——自适应只单向收紧，永远不超出用户窗
     （"别做每文件自适应"的旧警告因此仍成立：量纲守恒靠这条）；
   * 实际窗宽**印在色条标签与披露文案**（"峰值下 NdB"，收紧时标"自适应"）；
   * ⚠ 提示词写的 "floor + 10" 实测语义应为 **floor − 10**（窗底须在内容底
     之下；+10 会把内容底本身截进同一色，正是它要治的病）。
3. **披露文案补全**：频谱图 "全曲 ｜ 每列 200ms ｜ Δf≈X Hz ｜ 峰值下 YdB"
   （显示列宽 200ms 是实测值——原始 hop 69.7ms 被峰值保持压过，写 70ms 不诚实）；
   瀑布披露补实际色窗。
4. **修一个潜伏 bug（720 行暴露）**：`_resample_rows` 在**行数 > bin 数**时
   bin 耗尽取空切片抛 ValueError；而重画在 showEvent 里，异常从 Qt 事件处理
   冒泡破坏事件循环 ⇒ 症状是**下一次建画布段错误（exit 139）**，离根因极远
   （`test_ui_memory.py::TestCursorBus` 100% 复现，360 行时潜伏）。
   修法：耗尽后最近邻重复（不制造数据）。已补回归测试两条（含 signed 路径）。
5. `tests/test_ui_b9_chart.py` 的源码断言随口径更新（`vmin=-self.db_range`
   → `vmin=self._vmin`）：逐条判明是**口径变了**（B11-B 有意为之）而非实现错。

### 新增（B11-E1 视图覆盖矩阵与精度基线，只量不改）

- `packaging/view_coverage.py`：一条命令实测全部视图的覆盖矩阵 + 50Hz 占行
  基线 + 渲染耗时基线（`--strict` 时 @1k 行距未翻倍返回 1）。
- `docs/views_coverage.md`：矩阵 + 两条实测纠正（显示列 200ms 不是 70ms；
  720 行收益在高频不在低频）+ 精度基线锚 + 色窗自适应口径。
- `tests/test_views_coverage.py`：覆盖矩阵 ↔ 代码断言（镜像 B6 手法）+
  `_adaptive_vmin` 四条纪律判据 + rows>bins 回归。

### 新增（C7 双分辨率 STFT：低频根治）

> 评估见 `packaging/lowfreq_eval.py`；判据见 `tests/test_views_dualres.py`。

- **频谱图低频段（<546Hz）用 16384 窗重算并并入**，拼成「低频细、高频粗」的
  非均匀频率轴返回。`charts._resample_rows` 用 `searchsorted(f, …)` 对任意单调
  f 都正确 ⇒ 低频真实细节进图（可分辨间距 30→10Hz，3×），不再被插值抹平。
- **只动视图 STFT**：判定曲线走 `context.spectrum()` 的 16384 锁死口径，与本处
  两套独立参数、互不影响；改视图窗不会动结论（不撞"结论可复算"）。
- **默认开、可关**：`config.dual_resolution_stft=True`；`ViewWorker` 读
  `TH.dual_resolution_stft`。`nperseg ≥ 16384` 时自动退化为单分辨率。
- **代价 ≈2× 视图成本**（低频段多一遍 16384 窗；同重叠比下 16384 窗本身 ≈1×
  主窗耗时），可接受（视图算一次即缓存）。
- 改动文件：`core/views.py`（`_dual_res_spectrogram` / `_align_time` / 常量 /
  `spectrogram` 签名）、`config.py`（字段 + 出处登记）、`ui/window.py`
  （`ViewWorker` 透传标志）、`docs/views_coverage.md`、`tests/test_views_dualres.py`。

### 新增（timeline 逐段响度维度：LUFS / 真峰值）

> 判据见 `tests/test_timeline_loudness.py`。

- **时间定位补上响度轴**：每个分段在削波/峰值/RMS 之外增加 ITU-R BS.1770 的
  集成响度（LUFS）与真峰值（4× 过采样）——响度战争/压限/局部削峰因此有**段级**
  证据，不再只有整轨一个数。输出新键 `timeline` 行内 `lufs`/`true_peak_dbfs`
  （缺数据为 None，不静默 0）。
- **口径与整轨同一套数学**：`segment_loudness` 复用 `dsp.k_weighting` /
  `dsp._gated_loudness` / `dsp.true_peak` 与 `dsp.loudness` 完全一致的分块
  （100ms 步 / 400ms 块门限积分）。**硬证据**：整轨喂进 `segment_loudness`
  与 `dsp.loudness` 逐位相等（tone 与噪声各测一次，测试 1 条钉住）。
  dsp.py 是核心资产，本次**零改动**。
- **只报数值不判级**：note 保留"逐段频谱未做"老规矩，并如实标出响度维度
  "只报数值"。段内门限是段级独立的：段 LUFS 可与相邻段比较，**不能**与
  整轨 LUFS 相加/平均（门限窗口不同，docstring 已写清）。
- **新问题清单**：真峰值 ≥ 0 dBFS（数字满刻度硬上限，事实性判据）的段进
  `timeline_loudness_problems`，文案带"疑似"（顶到上限是事实，削峰是推断）；
  CLI 以"响度"前缀并列展示（与"时段"削波段同格式）。
- 性能：每段多一次 4× 过采样真峰值（段经临时 FLOAT wav 复用 `dsp.true_peak`
  标准实现，不另写过采样数学）；40s 双声道文件全量分析耗时增加可忽略。
- 改动文件：`core/analyzers/timeline.py`（`segment_loudness` / `_tp_of` /
  `run` 循环 / 口径 docstring）、`cli.py`（渲染）、`docs/thresholds.md`、
  `tests/test_timeline_loudness.py`（9 条）。

### 修复（C7 遗留：阈值普查字段覆盖与文档同步）

- `packaging/threshold_survey.py` 的 `FIELD_MAP` 漏登记 `dual_resolution_stft`
  ⇒ C1 覆盖测试红（C7 提交时未跑 C1，属漏检，现补）。
- 普查脚本区分**三类** None 字段标签：算法参数（影响检出率）/ **视图渲染选项**
  （不影响判定也不影响检出率）——后者贴错标签等于谎报它参与检出。
- `docs/thresholds.md` 同步到机器输出：普查表补 C4 成因候选 5 个字段 +
  seg_* + dual_resolution_stft；出处总账 33→42 字段（41 工程估计 + 1 一手文档，
  数字由 `config.provenance_summary()` 实取）；§2.1 补三分类说明。

### 新增（timeline 视图面板：时间定位进 GUI）

> 判据见 `tests/test_ui_timeline.py`。

- **第 8 个视图**：侧边栏「时间定位」（clock 图标）→ `TimelineCanvas`。
  上半轴 LUFS（实线）/ 真峰值（虚线）按段心时间 + 0 dBFS 事实参考线；
  下半轴削波率柱——**柱宽 = 步长，空隙即重叠**（画满整段会把 50% 重叠
  画成"每段全覆盖"）。
- **数据来自判定结果、不重读文件**：读 `r["timeline"]`（与 BandCanvas 同模式
  `set_data(r)`）；问题段遮罩/红柱**直接取内核清单**
  （`timeline_loudness_problems` / `timeline_problems`）——显示层不重造判据。
- **None 处断线**：lufs 缺口跨点不连线（"没测到"不许画成"测到了"）；
  数字静音/门限全灭的段只缺线不缺轴。
- **空态如实**：无 timeline（浅模式/短文件）显示内核 note 原文；指标页新增
  「时间定位口径」一行（长文 tooltip，与既有长值同模式）。
- 口径披露写在轴上：「时间（段 10s · 步 5s）」从数据直算，不写死。
- 两处**既有测试按行扫描的边界情形**（成因已写进代码注释）：`ms=` 写在续行上
  被 `test_ui_motion` 的 `plot(` 按行豁免漏掉误报 ⇒ 用规范长名
  `markersize=`；`"真峰值 dBTP"` 的混排普通空格 ⇒ U+00A0（`test_ui_typography`
  口径，与 window.py 既有标签一致）。
- 改动文件：`ui/charts.py`（TimelineCanvas）、`ui/window.py`（VIEW_KEYS/stack/
  侧边栏/on_select/clear/apply_theme/命令面板/指标行）、`ui/theme.py`（clock
  图标）、`tests/test_ui_timeline.py`（12 条）、`tests/test_ui_views.py`（+1 映射）。
- 验证：全量 **585 passed** + ruff 全树绿；palette/hue_family/mem_probe 全过；
  **深/浅两主题各渲染一帧人工看过**（前半正常后半削波素材：LUFS 阶梯、
  真峰值贴 0、遮罩与红柱位置正确）。

### 已知欠账（探针实测留档；P7 已关闭）

- ~~`perf_probe` P7 换肤 apply_theme p95 ≈ 1950 ms > 1500 ms 阈值~~ **已修复**
  （见下方"修复（P7 换肤性能）"：样式表挂窗口 + 画布重绘延迟一帧，p95
  1949 → 266–319ms，`--strict` 退出 0）。当时的诊断留档：约一半成本在
  `QApplication.setStyleSheet`（13 KB QSS 全局 repolish，裸 app 即 225ms
  ——Qt 解析成本的地板），其余在画布同步重绘与表格重建。
- `test_ui_material` 的 toast 连发回收在特定执行顺序（views 在 material
  之前）下偶发不过 720ms 窗口——**既有问题，HEAD 同样复现**（按字母序的
  全量套件不受影响），疑似存话期短于淡入时长时动画失靶的已知时序坑，
  留待专项（不在本发布块解决范围）。

### 修复（P7 换肤性能：p95 1949 → ~300ms，探针全绿）

- **样式表挂窗口不挂 QApplication**（`window.py::apply_theme`）。实测交替换肤：
  应用级 `setStyleSheet` 227–776 ms，窗口级 54 ms——**4.2×**。原因：应用级
  强制整个 app 的样式对象重建（QStyle 重初始化 + 全量 repolish）。视觉等价
  前提已逐个核实：CommandPalette / SeedDialog / SettingsDialog 都 parent=self；
  FullscreenView 无父但自带完整样式表（黑底，不依赖本条）。
- **画布重绘延迟一帧**（`charts.py::BaseCanvas`）：`apply_theme`（有数据时）
  只置 `_stale` + `update()`，图元重建移到下一帧 `paintEvent`（_stale 钩子），
  与其他 deferred repaint 同批完成——同步重绘可见画布实测 ~190ms
  （频谱图重建全部图元）。数据到达路径（`set_data`）仍同步重绘。
- 判据：`perf_probe` P7 p95 **1949 → 266–319ms**（6.5×），`--strict` 退出 0；
  回归测试钉住机制（换肤后标脏、paint 后以新主题重建，量图元颜色）。
- 探针口径更新：`test_ui_theme_consistency` 的 QSS 探针从 `APP.styleSheet()`
  改为 `w.styleSheet()`——不变量（self.theme 与生效 QSS 同源）没变，
  读取位置随架构口径更新（判明是口径变了而非实现回归，同 B11 先例）。
- 已知欠账更新：P7 **已修复**（此前登记的 perf_probe 欠账关闭）。
- 顺带发现（既有，非本批次引入）：`test_ui_material` 的 toast 连发回收在
  "views 在 material 之前"的执行顺序下偶发不过 720ms 窗口（HEAD 同样复现，
  按字母序的全量套件不受影响——views 在 material 之后）。疑似存话期短于
  淡入时长时动画失靶的已知时序坑（见该测试自身 docstring），留待专项。

### 新增（瀑布图游标读数：悬停读 时间/频率/电平）

> 判据见 `tests/test_ui_waterfall.py`。

- **悬停读数**（`charts.py::WaterfallCanvas`）：鼠标在瀑布上移动时，
  浮层显示光标下表面点的 **时间 / 频率 / 电平**（"0:27 · 208Hz ·
  -14dB"）。用 **Qt 子 QLabel**（`#CursorReadout`，主题 QSS 新增规则）
  而不是画进 matplotlib——3D 整帧重绘实测 ~370ms，读数不值得每次
  鼠标移动都重画；标签只改文字与位置，零重绘。
- **拾取是真实投影**：`_nearest_surface`（纯函数）把表面网格点
  （对数频点 × 片号 × dB）全部 `proj_transform` + `transData` 投到
  屏幕像素，取离光标最近的点；超距（>80px）判"不在表面上"→ 隐藏
  （查不到就不显示）。回归测试钉住"已知网格点 → 投影 → 原样命中"
  （时间/频率/电平全对，不是解析预测）。
- **状态切换**：拖拽旋转中隐藏（视角在变）、离开画布隐藏、无数据
  清缓存隐藏。
- 踩坑留档：`proj_transform` 只接一维向量（内部 `np.dot(M, (4,N))`），
  网格必须先 ravel；表面缓存的频轴必须存 **lf（log10）** 不是 fl——
  3D 图的 x 坐标就是 lf，存线性 Hz 会把投影点全甩出画布（实测
  -9612px）。
- **Δf 披露核实**：瀑布图上"Δf ≈ X Hz ｜ Δt ≈ Y s ｜ 峰值下 NdB"
  B14 既有（记忆里曾误记"未做"），本文件钉住防回潮。
- 验证：全量 **601 passed** + ruff 全树绿；palette 探针过；深浅两
  主题窗口截图人工看过（读数浮层位置正确）。

### 新增（E3 无障碍第一轮：五方面三态表 + 可访问名 + 焦点可见）

> 判据见 `tests/test_ui_a11y.py`；审计见 `packaging/a11y_report.py`；
> 三态表与登记见 `docs/accessibility.md`。

- **可访问名补齐**：8 张画布逐个 `setAccessibleName` + 文字替代描述
  （数值权威出口指向元数据页 KV 表）；搜索框 / 筛选框 / 文件表补名。
- **焦点可见（两条判据）**：① 对比度——`palette.CHECKS` 新增焦点环
  `border_focus` 对 surface/bg ≥3:1（实测 4.62–5.97:1，两主题达标）；
  ② 像素——`BaseCanvas.paintEvent` 补焦点环（自绘控件 QSS 管不到，
  用同一 `border_focus` 令牌），测试实测"聚焦前后 render 基线噪声 0、
  聚焦后边框带 3880 像素差异"（离屏 repaint 异步，等落定）。
- **诚实登记（KNOWN_GAPS）**：输入描边 1.18–1.33:1 不达标（改它 = 动
  B 块视觉语言，属 B 块决定，按 1.0 登记且报告显示实测值）；
  声音与语音**不适用**（音频分析工具，播报污染听音环境）；
  图表逐点朗读未做（Qt 无原生图表无障碍 API）。
- **纪律**：**不宣称"符合 WCAG"**——只做"按 Qt 五方面自查"，三态表见文档。
- `packaging/a11y_report.py`：离屏审计脚本（`--strict` 守未登记缺口）。
- 改动文件：`ui/charts.py`（焦点环）、`ui/window.py`（可访问名 ×11）、
  `ui/palette.py`（CHECKS +4）、`docs/accessibility.md`（新）、
  `packaging/a11y_report.py`（新）、`tests/test_ui_a11y.py`（新 7 条）。
- 验证：全量 **592 passed** + ruff 全树绿；palette/mem 探针全过；
  a11y_report --strict 退出 0。

### 修复（色图判据进 CI：相位场发散色图补齐 + 结构性缺陷）

- **发现 CI 从未真正跑过 GUI 测试**（`.github/workflows/ci.yml`）：测试 job
  只装 `.[dev]`，而 **19/43 个测试文件 import PySide6**、色图测试还需
  matplotlib ⇒ 在 CI 上 collection 阶段就 ImportError，test job 不可能绿。
  修：装 `.[dev,gui]` + 全局 `QT_QPA_PLATFORM=offscreen`（Linux 无 DISPLAY）。
  ⚠ 这一条**只能靠静态检查证明**（本地无法跑 GitHub Actions）——见下。
- **新增 `color-probes` job**：`colormap_probe.py --strict` +
  `palette_report.py --strict`。两者都是**确定性纯色彩数学**（无渲染不确定性），
  适合进 CI；`perf_probe`/`mem_probe` 是时序探针（阈值按本机调），**不进 CI**
  （会 flaky）——分工写进 ci.yml 注释。
- lint job 的 `ruff check` 由 `src tests` 扩到 `src tests packaging`（探针脚本
  此前不受 lint 守卫；本地已验全绿）。
- **补相位场发散色图判据测试** `TestPhaseColormapDivergingNorms`（3 条）：
  此前 `colormap_probe.measure_diverging` 的三条判据（两臂单调 / 中点最亮 /
  高彩只在一臂）**只在手动跑探针时才查**，现钉进 `test_ui_b9_chart.py` 进 CI。
  顺序色图（inferno ΔE CV ≤ 0.40）三条早已钉住，本轮不动。
- **修一个结构性缺陷**：`test_ui_b9_chart.py` 的 `if __name__ == "__main__"`
  块错位在**文件中段**（`TestCompareGridMode` 之前）⇒ 直接 `python <该文件>`
  会静默跳过它之后的整个类（pytest 不受影响，故一直没暴露）。移到文件末尾。

> 口径：CI 的"能否绿"由**静态核对**（19 个文件 import PySide6 ↔ install 步骤）
> 证明，不宣称已在 GitHub Actions 上跑过。色图判据的数值由
> `colormap_probe.py --strict` 与 `TestSpectroColormapPerceptualNorms` /
> `TestPhaseColormapDivergingNorms` 双向给出（inferno ΔE CV = 0.258 ≤ 0.40）。

### 口径变更（C4 判定颗粒度）

> 逐行 CLI diff 留档：`out/_before_demo{1,2,3}*.txt`（改前，`f5bb3b6` worktree）
> vs `out/_after_demo{1,2,3}*.txt`。**三个 demo 的 `level` 无一变化**（demo 语料
> 里没有"低截止 + 平缓滚降"的样本）；变化全在文案与新增结构。
> 等级变化的那一条（C4.6）用合成谱单独验：`test_c4_granularity.py`。

1. **低截止 + 砖墙不足：`bad` → `warn`**（`verdict.py::axis_bandwidth`，C4.6）。
   有损溯源是对**成因**的硬断言；滚降平缓（conf < `conf_low`）时它同样可能
   是母带/录音链的频响。文案改"疑似带限…证据不足（工程估计，无出处）"。
   `slope=None`（未测到）**不改旧行为**——最小改动只动"有数据但证据弱"。
2. **判定行分隔符 `｜` → `；`**（C4.4）：`｜` 把多个现象糊成一行正是本轮要
   消灭的形态。成因另起 `causes` 层、用途另起 `usability` 层。
3. **建议改由用途矩阵渲染**（`advice()`）：与矩阵同一套口径（老实现两处各写
   一遍 DR 门限差点造成"判定说偏低、建议不说"）。新增"响度战"进动态格；
   "DR 偏低 → 音量匹配盲听"并入"大音量失真"格（大音量失真可能来自文件）。
4. **CLI 每文件新增两行**：`成因`（每条候选：疑似 + 强度 + 证据 + 反证 /
   无法区分）、`时段`（问题段定位，最多 6 行）。**汇总新增一行**
   `效力边界（本工具不能告诉你的）：…`（C4.5，每次运行印一次）。
5. **`--json` 导出新增字段**（向后兼容，不改旧字段）：
   `phenomena[]` / `causes[]` / `usability{}` / `boundaries[]` /
   `timeline[]` / `timeline_problems[]` / `timeline_note`。
6. **阈值出处汇总 33 → 41 个字段**（新增 8 个，全部工程估计）。

### 新增（C4 判定颗粒度：成因候选 × 用途矩阵 × 时间定位）

- `core/analyzers/causes.py`（order=89）：**成因候选层**（9 类候选，每条带
  证据/反证/强度/出处状态；证据不足并列候选并标"无法区分"）+ **用途矩阵**
  （10 个用途 × 可用/受限/不可用/不可判 + 每格一句依据）+ `boundaries`。
  * 强度口径：砖墙类复用带宽轴 `conf`；非 conf 派生是粗档工程估计
    （0.9 位级 / 0.8 容器级 / 0.5 统计吻合），文案一律带"疑似"。
  * **AI 生成音频不列**（未取到出处，不猜）。
  * **类噪守卫**（`noise_like_flatness`）：谱平坦度 ≥0.5 时，"低频隆隆/
    低频缺失"这类占比证据名不副实（实测踩过：给白噪 demo 判出"疑似磁带
    转录"——它的"底噪"就是信号本身）。守卫只管占比类候选；截止/砖墙类
    证据对噪声同样成立（砖墙白噪是有损判定的标准素材），不守卫。
- `core/analyzers/timeline.py`（order=86）：**时间定位**。分段（10s/50%，
    参数在 config）+ 逐段时域统计（削波/峰值/RMS）→ 问题段（"2:10–2:20
    削波 3.1%"）。段数 < `seg_min_count` 明说"不下分段结论"；与整轨结论并列。
    逐段频谱**未做**（区分力未立，如实写进 note）。
- `config.Thresholds` 新增 8 字段（全工程估计，逐条登记出处）：
  `vinyl_rumble_pct` / `noise_like_flatness` / `broadcast_low_cut_pct` /
  `fake_stereo_corr` / `fake_stereo_width_db` / `seg_seconds` / `seg_overlap` /
  `seg_min_count`。
- `tests/test_c4_granularity.py`：27 条。覆盖 C4.6（含"陡砖墙仍判 bad"的
  反向保护）、成因九类、易混对并列、类噪守卫、用途矩阵每格带依据、
  时间定位（前 5s 干净/后 5s 削波的合成文件）、无总分、`｜` 不糊行、
  boundaries 存在、逐声道宽度数值（10·log10(side/mid) 断言 −9.54 dB）。
- `packaging/threshold_survey.py`：新阈值进普查映射（判定阈值两侧定向
  造样；`seg_*` 算法参数显式标 None）；`_observe` 补 `flatness`/`corr`。
  实测新字段在既有语料上**全部两侧有样本**。
- 文档：`docs/theory.md` §9.2（C4 三条输出规矩）+ §10（C3 方法与负结论，
  C3 的文档欠账本轮补齐）；`docs/research.md` §4 假 Hi-Res 行更新为
  "C3 已试并关闭"。

### 实测与记账

- **timeline 增量成本**（3 分钟 44.1k 文件，35 段）：全量管线 4.43s vs
  不含 timeline 4.12s ⇒ **+0.31s（7%）**。逐段解码全长一次的代价，接受。
- **C4 修订（砍底噪证据）**：`noise_floor_db`（谱低端百分位）是 PSD 域
  绝对值、**随节目电平缩放**（实测：加噪到 −30 dB 相对也只到 −87，够不到
  原拟的 −70 门限）⇒ 黑胶/磁带候选的"底噪抬升"证据**整体移除**，只保留
  可枚举证据（隆隆 / 平缓滚降），两者靠"无法区分"并列。原
  `vintage_noise_db` 字段已删（未进过任何提交）。
- **demo 的成因层无误报**：三个 demo（白噪/类噪素材）上 causes 均为空
  或只有正确候选（demo2_截止 ⇒ 有损转码，强度 1.00）。

### 口径变更（C2 阈值出处账）

1. **`--json` 导出的结构变了**：由裸数组改为
   `{"threshold_provenance": {...}, "results": [...]}`。
   理由：拿到结果的人必须知道**哪些阈值有依据**。这是对外契约变更，故单列。
2. **CLI 汇总新增一行**：`阈值出处：33 个字段 ｜ 标准/一手文档 1 ｜ 工程估计（无出处）32`。
3. **设置对话框**每个阈值标签加了 tooltip，写明该数的类别与依据。

### 新增（C2 阈值出处账 / P0-2）

- `config.PROVENANCE`：**33 个阈值字段逐条登记**出处，四类
  （标准 / 一手文档 / 二手 / 工程估计）+ 依据文本。
  ⚠ **依据必须自足**——不许写"同上"（机器判据实测抓到过三条）。
- `config.provenance()` / `provenance_suffix()` / `provenance_summary()`。
- `tests/test_c2_provenance.py`：每个字段都要登记；类别合法；
  标 `标准`/`一手文档` 的**依据必须具体**（不许"见资料"）；标 `工程估计` 的**必须明说无外部出处**。
- `docs/thresholds.md §3`：完整出处表 + 降级措辞的落点说明。

**实测结论：33 个字段里只有 1 个有外部依据**（`loud_war_lufs`，来自 Spotify 官方支持页，
且是**从官方数据推导**而非直接规定）。其余 32 个全部是工程估计 —— 这不是失败，是如实记账。

### 口径变更（C0 判据层卫生）

> 逐行 CLI diff 留档：`out/_baseline_c0.txt`（改前，`a0fe00c` worktree）vs `out/_after_c0.txt`。
> **在 demo 语料上差异 0 行** —— C0 是纯收编，不调参。

1. **`brick_db`：40.0 → 24.0**（`config.Thresholds`）。
   该字段原先**在设置对话框里可调、但没有任何代码读它**——用户设了它什么也不会发生。
   而 `verdict.py` 真正用的是硬编码 `>= 24.0`。现统一取真口径 24.0 并让判定读它。
   因为 40 从未被读，**行为零变化**。
2. **位深"无法统计"：`ok` → `warn`**（`verdict.py::axis_bitdepth`）。
   分析器的 `bit_verdict` 本来就区分了"不适用"与"无法统计"，判定层原先没读它，
   于是"测不出来"与"没问题"长得一样。现在"无法统计"报"有疑"并说明原因；
   "不适用（浮点/32bit 整数）"仍判 ok（不误伤）。
3. **未判定进入汇总**（`summarize.py` / `cli.py`）。
   原先未判定行既不计入 ok/warn/bad、也不出现在汇总里 ⇒ 浅模式跑完显示
   "合格 0 ｜ 有疑 0 ｜ 不达标 0"，读起来像"全都没问题"。现在单列"未判定 N"。
4. **指标改名：`noise_floor_db` 的界面标签"噪声底" → "谱低端百分位"**。
   它实际是**频谱第 5 百分位**，不是静音段测出的噪声底。数据键名保留（JSON 兼容）。
   新规矩进 `docs/theory.md §9.1`：**UI 不许展示 §9 表里没有的数字**。
5. **`detect_cutoff` 的 3 个参数现在真的生效**（`spectrum.py`）。
   原先调用点只传 `min_drop_db`，其余三项（`min_hz`/`need`/`min_stop_db`）永远取函数默认值
   ⇒ 用户在设置里改它们同样"设了没反应"。现四项全部显式传 `ctx.thresholds`。

### 新增（C0 判据层卫生：魔数收编 + 防回潮）

- `verdict.py` 的 **15 处**硬编码数字收编进 `config.Thresholds`（审查报告列了 12 处，
  **实际是 15 处**——补收编时又发现 `fs<=45000` / `fs<=50000`（容器分界）与位深差 `>=4`）。
  新增字段 17 个，**值一律不变**。
- 削波的**三条线**（告警 0.005% / 严重 0.5% / 建议文案 0.01%）**明确各自语义、不合并**。
- 新增 `tests/test_c0_judgment_hygiene.py`：分析器里每个数值字面量必须在**逐条带理由**的
  白名单里，否则失败；白名单有陈旧条目也失败；`verdict.py` 不许出现非结构性数字。
  （已验有牙齿：注入一个 `18.7` 当场报错。）

### 新增（B7 视觉重构：锚点色彩 / 字号令牌化 / 图表规范 / 材质）

用户方向："把'高级'从形容词变成可测参数"。承接 B1/B2/B3，三个 commit：
`b738721` B7.1 色彩 → `3ba3948` B7.2+B7.3 字号与图表 → B7.4 材质。

#### 先取证：提示词里三处要纠正（其余全部复现）

复现无误的：锚点图 3.62%（提示词 3.64%）/ C<0.05 86.17%（86.2%）/
L>0.7 84.69%（84.7%）/ 主族 89.3%（89%）/ P50 0.123（一致）；
网格线 1.20:1 与 1.32:1（逐位相同）；deco 贡献全图高彩 42.4%（我独立测到 43%）。

**要纠正的三处**：

1. **§3.1 的"现状"用错了种子**：提示词写 `palette.build('#c2607a', …)`
   （primary C0.127 H4），仓库实际是 `#f97083`（**C0.167 H14**）。
   结论"不是 primary 不够艳"反而更强——彩度已在锚点区间内。
   该表不能当基线复算。
2. **§3.3 的字号计数**：说"表外 7 种"，实为 **8 种**
   （7.0/7.5/7.8/8.0/8.5/9.0/9.5 **+ 19.0**；19.0 在正文里被提到却没计入）。
3. **§2 规则 1/2 跨主题误用**：锚点图 **L>0.7 占 84.69%**，是**高调亮图**；
   而暗色主题 L>0.7 只有 **2.07%**。"点缀 = 更暗 + 更暖 + 更彩"只对亮底成立，
   暗底上点缀必须更亮（primary L0.620 > 底 L0.165）。可迁移的是**比例关系**：
   彩度差（≥30 倍）、冷暖对立（底 286° / accent 14°）、单族占比。

#### B7.1 色彩：收 deco + 让判定结论条承载 accent

按勘误 §5.6 的处置顺序「① 先收 deco → ② 再量 → ③ 加大玫瑰面积」执行：

- **① 收 deco**：卡片顶条从六色环改成**中性顶光**（18 条彩带连成 6 条横贯全屏的
  色带，面积判据抓不到，却把主族压到 64.1%）。deco 保留在**小色块**上
  （导航图标 17×17、品牌条 3 段），calm 档彩度 0.10 → **0.082**，压到高彩口径之下。
- **③ 加大 accent 面积**：**判定结论条**成为判定语义唯一的大面积着色
  （slab 的 L 与 C 由 sRGB 色域反解：dark 0.38/0.115、light 0.79/0.115；
  彩度取锚点 P50 0.123 略下方，**不超锚点**）。新增 4 个令牌 + 7 对 WCAG 判据。

| 项 | 改前 | 改后 | 目标 | 锚点 |
|---|---|---|---|---|
| 高彩面积 C>0.10（dark） | 0.68% | **3.40%** | 3.0–4.0% | 3.62% |
| 高彩面积（light） | 0.59% | **3.39%** | 3.0–4.0% | — |
| 单一色相族（dark） | 64.1% | **100%** | ≥85% | 89.3% |
| 高彩彩度 P50/P90 | 0.139/0.168 | 0.115/0.144 | 0.10–0.20 | 0.123/0.167 |

（B7.4 之后复测：3.55% / 3.55% —— 加上加粗后的数据曲线与描边。）

**未达标项与原因**：暗色主题的 `warning_slab` **到不了 C0.10**（实测 0.073）——
sRGB 色域里 H85 在 L0.36 处的彩度上限就这么大（黄的可用彩度远小于红/绿）。
后果是一条**判据层面的**事实：高彩面积**依赖当前选中的判定状态**。
已写成"断言它 ≤ 口径"的测试并在注释里写明理由。

**口径依赖（必须连空间一起说）**：同一张图 OKLCH C>0.10 是 0.68% 时，
HSV S>0.35 是 1.83%、HSL S>0.35 是 21.9%——**差 6 倍**。口径真源：
`palette.HIGH_CHROMA_C`，量尺 `packaging/hue_family_report.py`。

#### B7.2 字号令牌化

`Type` 加唯一一档 `chart_tick = 9.0`，35 处按**语义**归位（不是机械替换）：

| 原值 | 语义 | 归到 |
|---|---|---|
| 7.0 / 7.5 / 7.8 / 8.0 / 8.5 / 9.0 / 9.5 | 刻度、图例、轴标注、悬停读数、色条标签、频段名 | `chart_tick` 9.0 |
| 10.0 | 子图标题（"真实带宽分布"/"结论构成"） | `micro` 10.0 |
| 11.0 | 空态与提示文字（"选择文件后…"） | `caption` 11.0 |
| 19.0 | 饼图中心的文件数（Hero 数） | `heading` 20.0 |

⚠ 与提示词两处分歧：① 它说"19.0 → display"，但表里 display=27、heading=20，
19 的语义是"图里那个大数字"，最近的是 heading（提示词当时以为表里没有 16/20/27）；
② 8 个原值全部**合并到一档**，不是每档补一个令牌——逐个补等于把散点搬进表里。
③ 轴标题原值也是 9，于是与刻度同号；本轮遵从提示词的映射，作为观察记下。

**视觉影响（提示词标为高风险）已量化**（`git worktree` 前后对照）：

| 画布 | 刻度字号 | 绘图区占比 | 可见刻度 |
|---|---|---|---|
| 频谱曲线 | 8.0 → 9.0 | 81.0% → 79.4% | 10 → 10 |
| 频段能量 | 9.5 → 9.0 | 80.2% → 79.7% | 7 → 7 |
| 总览 | 8.0 → 9.0 | 42.0% → 41.2% | 7 → 7 |

**刻度一个都没被挤掉**（信息量没减少）。

⚠ **探针第一版量错了对象**：用了 `cv.draw()` 而非 `cv.redraw()`——
`FigureCanvasQTAgg.draw()` 只渲染，`_ax_style()` 在 `redraw()` 的构建路径里。
改前改后都读到 matplotlib 默认值 10.0，差点得出"字号变大但没有代价"的结论。

#### B7.3 图表规范

- **网格线不动**（1.20 / 1.32 ≤ 1.5），加测试挡"顺手加强"。
- **data-ink ratio 先标定**：频谱曲线 **1.36%**（总墨 22.9 万像素）、
  频段能量 **26.96%**（总墨 3.0 万）。**标定推翻了 Tufte 式高比值目标**：
  频谱图 98% 的墨是 Bark 频带底纹与网格，而它们**必须保留**（判据的一部分）。
  第一版我写了 `>2%` 的门槛然后它红了——那正是"先定阈值再标定"的错误动作。
- **信息性非数据墨必须保留**：加测试断言参考线 / 单位 / 效力边界文字的代码路径仍在。

#### B7.4 材质与特效

| 判据 | 落点 | 实测 |
|---|---|---|
| 核心锐利 | 数据曲线 1.5–2px、无模糊 | 频谱主曲线 **.85 → 1.5**（原来是发丝线）；对比/瀑布 1.15/1.25 → 1.5 |
| 亮边配暗线 | 浮层上缘亮线 + 下缘暗线成对 | Qt **不能在同一个边叠两条线**，配对实现为上亮下暗并登记该限制 |
| 二次反光 | 结论条描边与底色**同色相** | 暗色下判定色更彩（0.155 vs 0.115）；**亮色下不成立**（warning 的判定色被自己的 WCAG 拟合降到 0.107）→ 判据退为"同色相 + 可辨 ≥1.15:1"，实测值记此 |
| 光强分级 | 最彩的**面**必须是结论条 | 通过（把 slab/胶囊底/卡片/侧栏按彩度排序） |
| ≤3 色族 | 高彩像素 30° 桶数 | **1 个**（100% 单族） |
| 阴影不用纯黑 | 令牌与 QSS 无 `rgba(0,0,0,…)` | 旧 `shadow` 是 `rgba(0,0,0,.45)` → **已换**为 `rgba(2,6,23,.45)`；扫描限定在阴影行（`palette.self_check()` 里有个 `#000000` 的**测试种子**，不是阴影色） |

#### 判据

`tests/test_ui_b7_color.py`（14）+ `tests/test_ui_chart_norms.py`（11）+
`tests/test_ui_b7_material.py`（11）= **36 条新增**。三处"判据自身被测"：
字号扫描要能抓到一个造的违规样本、data-ink 只断言两侧非空、
色相族复用量尺而不内联一份 OKLCH（第一版内联时引用了不存在的函数，
被 `except ImportError` 吞成 skip——**判据静默失效**）。

**同一课第三次**：创建整窗/画布的测试**没拆**，导致后续 `QTimer.singleShot`
不按时触发（`test_ui_material` 的"8 条通知必须全回收"变成 8 != 0，单独跑却绿）。
B7.1 的 MainWindow 与 B7.2 的画布各踩一次，两处 teardown 都写清了症状与定位过程。

#### 验收

- pytest 334 → **370 全过**；ruff All checks passed
- `palette_report --strict` / `hue_family_report --strict` 0 项不达标
- `mem_probe --strict` 通过；`perf_probe --strict` 通过（P1 p50 16.6ms / P7 p50 957ms）
- 截图 A/B：`shots/b7`（B7.1）、`shots/b72`（B7.2）、`shots/b74`（B7.4）

#### 未做与原因

- **直接标注替代图例**（B7.3 第 3 条）：对比图仍用图例框。曲线末端直接标注要
  处理重叠/裁剪/十条曲线的情形，属于图的重新设计，与"字号令牌化"混在一个
  commit 里会让 revert 边界不清。已列入下一轮。
- **真正的环境光反弹（glow）**：B2 已判决"辉光退场"，所以"二次反光"用同色细
  描边表达（面自己的光落在自己边缘），不是向外发散的辉光。

### 新增（B 块之三：交互与动效收编）

用户方向"仪器排版 + 克制材质 + 配额制配色"的第三块。B2 已经把 Toast 与仪表环
的动画收编进 `motion`，B3 把**规则本身**变成判据，并补掉状态族的缺口。

#### 两条规则（各自有测试）

- **动效时长只许来自 `theme.Motion`**（或 `motion.FAST|BASE|SLOW`，它们是别名）。
  实测只揪出一处手写：`motion.fade(cv, self, ms=260)`——260 不在任何一张表里，
  改成 `Motion.base`（200）。取 `base` 而不是 `slow`：`slow` 是"大容器展开"的量级，
  而这里是一块画布的内容到达。
- **节流/去抖间隔不是动效时长**，不塞进 `Motion` 表，但必须**具名 + 在原处说明依据**。
  它们的值各自绑着一段性能事实（`VIEW_DEBOUNCE_MS = 28`：人眼看不出合并；
  `REFRESH_COALESCE_MS = 120`：与文件到达同量级），搬到远处的表里反而离家。
  实测揪出一处裸数字：`_drag_timer.setInterval(120)` → `DRAG_THROTTLE_MS`。

#### 状态族

补齐三处缺口（QSS 空态扫描 + 表驱动判据）：

| 控件 | 缺的 | 为什么必须有 |
|---|---|---|
| `QLineEdit` / `QComboBox` / `QSpinBox` | `:disabled` | 少了它，**不可编辑的输入框与可编辑的长得一模一样**——用户会去点，然后以为界面卡了 |
| `QScrollBar::handle` | `:pressed` | 滚动条是拖拽类控件：悬停=准备好、按下=正在拖。少了它，拖动过程中的手感与悬停无异 |
| `#WinBtn`（窗口按钮） | `:pressed` | 按下即生效的按钮，点下去那几十毫秒里没有任何反馈 |
| `#WinBtn#Close` | 与 `:hover` 同色 | 悬停已是全红、按下还是同一个红＝没有反馈。新增 `danger_lo` 令牌（比 `danger` 深一档） |

#### 判据（`tests/test_ui_motion.py`，12 条）

- 手写动效时长 = 0（**判据要写准**：`ax.plot(..., ms=4)` 是 matplotlib 的
  marker size，不是毫秒——第一版把它当违规报出来，这种误报迟早会让人把规则注释掉）
- 裸的节流间隔 = 0；`motion` 的四个入口都有 `ms` 参数且默认值在表里
- **动画对象只许在 `motion.py` 里创建**（那是唯一有 `isValid` + `deleteLater`
  清理的地方，别处再造一个就是又开一条泄漏面）
- **没有常驻计时器**：每个 `QTimer` 必须在 6 行内出现 `setSingleShot(True)`
- 动效不许伸进 `charts.py`（画布一次重画是百毫秒量级，拿它做动画会顶穿帧预算）
- `pulse` 只在判定态一处，且**真的被 `lv in ("warn", "bad")` 守着**（不是无条件脉）
- `stagger` 只用在"内容到达"一处
- QSS 状态族表驱动覆盖 + **不许有空规则**（`#X:hover { }` 是"看起来做了"的规则）
- `:pressed` 不许与 `:hover` 是同一份声明

#### 实测

| 项 | 数值 | 口径 |
|---|---|---|
| pytest | **333 全过**（新增 11） | 322 → 333 |
| ruff | All checks passed | — |
| `mem_probe --strict` | 通过 | 通知风暴后动画数归零 |
| `palette_report --strict` | 0 项不达标 | 面积配额 ✓ |
| P1 悬停 | p50 **17.5 ms** ｜ P7 换肤 p50 **1000 ms**、p95 **1218** | 通过 |

**关于 P7（补记第三次测量）**：B2 提交时 P7 p95 是 1613 ms（越 1500 红线），
当时用 `git worktree` 在 B1 提交上跑同一套探针得到 p95 1646 —— 证明不是 B2 引入的。
B3 提交前再测，同样的代码路径得到 p50 **1000**、p95 **1218**（通过）。
三次测量（952 / 1182–1278 / 1000）覆盖了同一份代码的两倍区间，
**结论：这台机器的会话内漂移可达 ±30%，跨时段比较绝对值没有意义。**
阈值本身的标定需要一台安静的机器，**不要为了让 CI 变绿去调高它**。

### 新增（B 块之二：克制材质 + 配额制配色）

用户方向："仪器排版 + 克制材质 + 配额制配色"。B1（排版/字距/行高/字体）已单独
提交，这一条是 B2（材质与配色）。**先取证再动手**，三处提示词给的说法在实测下
需要修正，逐条记在下面。

#### 配色：中性阶换锚点（配额制的前提）

- **中性阶不再跟随种子色**。原先整张界面（占 90% 面积的中性表面）都被种子色染
  一遍，换个种子色连背景灰都变——"一枚 accent"事实上是"一枚 accent + 一整套染色"。
  新增 `palette.NEUTRAL_HUE = 286.0`，锚点来历：对用户给的参考图 16.4 万像素做
  色相直方图，中性主峰簇落在 **280–291°**，取中位。种子色此后只决定 accent 一族。
- **内置主题换锚**：`BUILTIN_SEED` `#7c5cff` → **`#f97083`（玫瑰）**、
  `BUILTIN_VIBE` `pop` → **`calm`**。参考图的强调色实测 OKLCH H≈14°/C≈0.168/L≈0.715。
  `calm`/`pop` 两档的**取值一个没改**（0.012 / 0.030 原样），改的只是默认用哪一档；
  `pop` 仍在 `Ctrl+M` 与种子色对话框里可选。**这是判断，不是实测结论。**
- **`accent2` 从"分裂互补 +150°"改为"锚在中性色相的冷 counterpart"**。原先它从
  种子色出发转，种子一变第二枚就跟着跑（玫瑰种子会转到 164° 青绿）。现在是
  "一枚暖 + 一枚冷"的**一对**。附带规则：**色相分不开就换明度**——种子色本身
  落在冷区时（比如蓝紫），两枚只差二十几度，此时 `accent2` 压深 0.18，保证
  "色相与明度至少给一个"（数据可视化的基本要求）。
- **色温分层上限 0.16 → 0.08**（`WARM_COOL_MAX`）：换锚点后重测，0.16 会把浅色
  主题的表面推到 301°（品红侧），跑出参考图那 280–291° 的窄带。

#### 材质的三个坑（都靠实测定位）

- **投影**（`ui/material.py`，新模块）：Qt 的 QSS 没有 `box-shadow`，用
  **同心层叠**近似。关键是用 `CompositionMode_Source` 而不是默认的 SourceOver：
  SourceOver 把每层 alpha **累加**，于是 32 层的 far 档在边缘（实测 alpha 231）
  反而比 12 层的 near 档（214）更暗——"更远的光源投出更淡的影子"被算法吃掉了。
  Source 是覆盖：距离 d 处的 alpha 就是曲线在 d 处的值。实测 far 边缘 54 < near 105，
  且 far 铺到 30px、near 只到 12px。
- **顶光**：浮层（菜单/命令面板/通知）上缘 1px 内高光。**必须写在 `border`
  简写之后**——同特异度下后写的赢，`border` 会重置 `border-top`。第一次就写反了，
  那条顶光被静默吃掉，界面只是"少了点什么"。
- **抗色带噪点**：≤2/255、固定种子（截图对比要能复现）。**第一版做成了半透明的
  覆盖层子控件**（`WA_TranslucentBackground`），改成在侧边栏/标题栏自己的
  `paintEvent` 末尾自绘。理由：半透明子控件会让 Qt 在每一次重绘里把父控件背景
  重放到该区域，而噪点只有 2/255 的幅度，为它加一层合成本末倒置。

#### 顺手修掉的四个既存 bug

| # | 问题 | 症状 | 修法 |
|---|---|---|---|
| 1 | **圆角令牌形同虚设**：QSS 模板里 `border-radius:{lg}` 拿到的是 `Space.lg`（16px），而 `Radius.lg` 是 8px——两张令牌表共用占位符名，`format()` 只喂了 Space | 卡片圆角实际 24px，而卡片强调线按 `radius.xl-1` 算出来是 11px，两者对不上；"圆角同族"从未落地 | 半径换独立占位符 `r_sm/r_md/r_lg/r_xl`，值来自 `Radius` |
| 2 | **通知渲染在右上角**（文档写"右下角"） | `addStretch(1)` 加在最前、通知又被插到它**之前** → 通知被顶到顶部 | 弹簧留在首位，通知追加到它后面 |
| 3 | **孤儿动画**（内存基准抓到） | 存活期短于淡入时长时，退场换掉 opacity effect → 旧动画失去目标 → Qt **不发 `finished`** → 清理路径永不执行。40 条通知留 **40 个动画**，而内存只涨 **2.4 MB**（只看内存发现不了，必须数对象） | `motion._abort_prev()`：换 effect 前先拆掉在飞的动画 |
| 4 | **`fade(..., out=True)` 参数名错**（B1 引入） | `fade` 的参数叫 `end`；TypeError 被吞在槽里，**通知永远不会消失** | 改用 `end=0.0` + `cleanup_extra`，并补测试 |

#### 实测（这一轮的数字，连难看的都记）

| 项 | 数值 | 口径 |
|---|---|---|
| pytest | **322 全过**（新增 33） | 300 → 322 |
| ruff | All checks passed | — |
| `mem_probe --strict` | **通过** | 修孤儿动画后 |
| 高彩面积（dark / light / pop） | **0.68% / 0.59% / 0.81%** | OKLCH C>0.10，配额 ≤5% |
| 中性面积（dark / light） | **96.9% / 98.1%** | OKLCH C≤0.0125 |
| P1 悬停 | p50 **20.5 ms**（B1 代码同状态 **19.9/20.4**） | blit 快路 |
| P7 换肤 | p50 **1203 ms**，p95 **1613 ms**（红线 1500） | B1 代码同状态 p50 **1182/1278**、p95 **1646** |

**关于 P7 越线（必须写清楚，不许含糊过去）**：本轮改动后 P7 p95 越过了 1500 ms
红线，但**这不是本轮引入的**。用 `git worktree` 在 B1 提交（`ca97998`，不含任何
B2 改动）上跑同一套 `perf_probe.py`，同一机器状态得到 p50 1182/1278、p95 1646——
与 B2 的 1154–1203 完全重叠（B2 甚至略快）。结论：

1. **本会话内机器状态漂移了约 30%**：同一份代码在会话早期测得 P7 p50 952 ms，
   后期测得 1182–1278 ms。跨时段比较绝对值没有意义。
2. 阈值 1500 ms 是在漂移前标定的，**当前状态下它连基线代码都通不过**。
   按本项目"换到更快/更慢的机器上应重跑再定"的规矩，这条阈值需要在一台
   安静机器上重新标定，**不要**为了让 CI 变绿去调高它。
3. 本轮真正量到的性能事实是：**加噪点层、投影缓存、QSS 变大（8917→9268 字符）
   都没有可测量的代价**。第一个版本（半透明覆盖层）曾测到 P1 19.6 / P7 1178，
   但那组数字与同时段噪声无法区分，所以"改成自绘"的收益只有定性证据
   （少两个常驻控件、少一次父背景重放），没有定量证据——**这一点如实记录**。

**与参考图的对比**（这条比"是否达标"更有用）：同一口径下（OKLCH C>0.10），
参考图是 **3.32%**，本界面是 **0.68%**；HSV 口径下是 4.68% vs 1.83%。
即**我们比参考图克制约 2.5–5 倍**。配额是上限、不是目标，所以这不叫不达标；
但如果"玫瑰色不够明显"，**该加大的是 accent 的面积，不是它的彩度**
——彩度已经实测到位（primary C=0.167，与参考图 0.168 一致）。

#### 判据与工具

- `tests/test_ui_palette.py`（53 条）：仪器灰的色相不随种子变、彩度在配额带内、
  两枚强调色"色相或明度至少一个分得开"、阴影双档的方向与单调性、
  阴影色不是纯黑、顶光比描边亮。
- `tests/test_ui_material.py`（22 条）：噪点确定性/幅度/两档灰、投影的
  "卡片位置必须透明"与"越远越淡、羽化半径处归零"、far 档更虚更淡、
  顶光规则在 `border` 之后、通知生命周期（含"存活期短于淡入"那条泄漏）、
  部件级样式表上限（26 处，只许调小）。
- `packaging/palette_report.py` 新增 `--shot`：**量渲染截图**的高彩/中性面积
  （这条只能量像素，令牌只能说明"有哪些颜色"）。同时把 OKLCH 与 HSL/HSV
  两个口径一起打印——本轮发现同一张图在三个空间里能差 6 倍，而引用的那族
  数字（"4.59%"）只说了值、没说空间。
- `packaging/ui_shot.py` 新增：**进程内** `QWidget.grab()` 出图（秒级），
  与 `shot.ps1` 的"打包 + `PrintWindow`"分工不同——前者用于迭代，后者用于最终验收。
  它自己踩过一次坑：漏调 `T.init_fonts(app)` 会得到"满屏方框"的截图，
  而那种图会被误读成"界面渲染坏了"。

#### 已知缺口（不假装做了）

- **投影只用在通知上**。菜单与命令面板的投影需要它们是 frameless 的浮层，
  本轮没动它们的窗口标志（`QMenu` 由 Qt 管、改它的绘制风险大于收益）。
- **单行标签的行高**仍是字体自然行距（B1 的缺口，未变）。
- P7 阈值待在一台安静的机器上重新标定（见上）。

### 新增（第六轮：图表能放大、能平移、能关曲线）

用户拍板的第一块（A 图表可交互性），对应 `docs/ui_compare.md` 的 A1–A3。
纪律照旧：**先量再改**，量到的数字连难看的都记下来。

- **`ui/zoom.py`（新模块）**：缩放/平移的**纯函数**（`zoom_span` / `pan_span` /
  `clamp_span` / `zoom_step` / `pick_ticks`）。单独成模块的理由：缩放的数学
  （对数与线性不是同一套公式、锚点不动、边界贴边不回弹）必须能不依赖 Qt 测——
  写进画布类就只能靠肉眼验，而它恰恰最容易错。
- **频谱曲线**：滚轮缩放（以鼠标位置为锚点）、左键拖拽平移、`+`/`-`/`0`
  键盘缩放与复位、右键放大一级、方向键平移。换文件自动回全距
  （奈奎斯特随采样率变，留着的缩放区会把新文件显示成空白低频段）。
- **多文件对比**：同一套缩放/平移，外加**点图例开关任意一条曲线**
  （十条曲线叠着时，第一件事是"关掉几条"）。
- **刻度跟着视野走**（`pick_ticks`）：固定刻度表在放大到 100–200 Hz 时只剩
  两个刻度，等于"能放大但读不了数"。现在窄视野自动改用线性好数步长，
  宽视野保持 1/2/5。
- **重画合并**：滚轮是连续事件，一路滚下去会触发十几次整图重画。
  28 ms 单例计时器把同一帧的多次请求合成一次——与瀑布图拖拽限流同一个思路。

### 实测（这一轮的数字，连难看的都记）

| 项 | 数值 | 口径 |
|---|---|---|
| 缩放一格（`_apply_view`） | **p50 119.9 ｜ p95 128.5 ms** | 含一次静态层重建 |
| 同一画布只 `redraw()` 不重建静态层 | 70.5 ms | 差的就是重建那 ~50 ms |
| 缩放事件本身（`_zoom`） | **0.1 ms** | 只改状态；重画走合并计时器 |
| 悬停读数（blit 快路） | p50 9.9 ｜ p95 10.6 ms | 没被缩放改动破坏 |

两条必须写下来的教训：

1. **"基准也会骗人"又中了一次**：如果量的是 `_zoom` 调用本身，会得到
   "缩放不要钱"（0.1 ms）的错误结论——重画被 28 ms 计时器延后了。
   必须量 `_apply_view`。这与 `perf_probe.py` 第一版掉进 `draw_idle()`
   异步陷阱是同一种错误。
2. **缩放 120 ms 超出 16.7 ms 帧预算**。它是**离散**操作（一次滚轮一格），
   连续滚动时限流到 ~8 fps；真正连续跟手的悬停仍在 10 ms 量级。
   优化方向明确且已记账：把 `_static_layer` 拆成"数据层"（曲线/Bark 底纹/
   截止标注，只在数据变化时重建）与"视图层"（刻度/范围，每次缩放都更新），
   可降到 ~70 ms。**本轮没拆**——那张图上有六处踩坑修出来的细节
   （见 CHANGELOG 历史），为一处未经验证的优化动它不值。
3. **记账缺口**：这两个数字来自 `out/_probe_zoom_cost.py` 一次性脚本，
   **没有进 `perf_probe.py`**，所以目前不可复算。下一轮补 P13/P14。

### 新增（第五轮：五颜六色的 ACG 档 + 装饰色阶）

用户原话是"UI 能不能有五颜六色 ACG 色调的，感觉现在这个好丑"。
上一版的配色是**一色灰底 + 一处主色**——克制是它，但也没法让它缤纷：
想鲜艳就得手改色号，而手改的色号不会自己达标。本轮把"风格"也变成一组
**可验证的参数**，而不是又挑几个好看的颜色：

- **风格档 `vibe`（与明暗正交的第二根轴）**
  - `calm`（默认）：表面 chroma 0.012/0.008，表面单一色相，装饰色阶彩度 0.10
  - `pop`：表面 chroma 0.030/0.020，**表面色相阶梯**（bg→raised 共 +30°），
    装饰色阶彩度 0.18
  - 两档走**同一套** WCAG 判据（`audit()` + `audit_deco()`，生成时 strict），
    所以"缤纷"不是"放弃可访问性换来的鲜艳"
- **装饰色阶 `deco`**（六等分色环，6 档）：并列关系的多色。
  用于侧边栏导航图标（每项一个色）、指标卡顶端 2px 色条（按槽位）、
  多文件对比的第 3–7 条曲线。**不承担语义**——合格/有疑/不达标
  永远只有那三个固定色相（`tests/test_ui_palette.py` 有一条专门钉这个）。
  每一档都按"对它会被画上去的底色 ≥3:1"生成：`surface`（侧边栏）、
  `plot_bg`（图表）、`primary_soft`（激活导航的胶囊底），`audit_deco()` 逐条算。
- **内置主题改为生成**（`theme.py`：`BUILTIN_SEED = "#7c5cff"`，`pop` 档）：
  不再手写 30 个色号，改由 `palette.build()` 算出后冻结成模块常量。
  两个好处：①换种子或改生成规则，内置主题自动跟着走；
  ②"手改一个色号又改回去"这类回归有了生成器挡着。
- **界面接上**：`Ctrl+M` / 命令面板「切换配色风格」在克制↔缤纷之间切换
  （不动深浅——那是 `Ctrl+D`）；种子色对话框加了一排 **ACG 预设**
  （樱 / 初音 / 霓虹 / 星野 / 蜜柑 / 薄荷）+ 克制/缤纷下拉 + **装饰色阶渐变预览**；
  `--vibe` 启动参数；`seed_vibe` 进 QSettings。
- `packaging/palette_report.py` 增加 `--vibe` 与装饰色阶审计、色卡图上
  多出 6 小列（`d0..d5`）。

### 新增（第五轮：与专业工具的差距清单）

`docs/ui_compare.md`。纪律：**先划清哪些有出处、哪些没有**——
"它们有什么"全部来自官方页面（RX / REW / Audacity / SPAN / Spek /
Sonic Visualiser，逐条给链接），本项目自己的数字全部来自探针/测试；
两边的取证强度不对等，所以表里把「官方页说了什么」「audiocheck 实测」
「我的判断」**分成三列**。Adobe Audition 官方文档页对本机返回 403、
friture 文档页 404——**它们没有进入对比**，而不是"印象里它们有 X"。

结论摘要：差距集中在**图形的可交互性**（缩放/平移/光标/图例），
不在"有没有这张图"；频谱编辑、实时分析、房间声学三项**明确不追**
（那是 RX / SPAN / REW 的地盘，追了会稀释定位）。

**这一轮自己量出来又改回去的一处**（记下来，因为它是"看不见的回归"）：
指标卡色条第一版是每张卡 `setStyleSheet` 上色。功能全对、测试全绿，
但换肤基准（`perf_probe.py` P7）从 310 ms 涨到 460 ms。逐项二分
（用环境变量逐项关掉新特性、每项一个独立进程）定位到：18 张卡各带一份
**私有样式表**，`app.setStyleSheet()` 要逐个重新解析，单项多花 ~90 ms。
改成 **app 级 QSS 的属性选择器**（`QFrame#CardAccent[slot="3"]`）之后回到基线。
教训：部件级 `setStyleSheet` 在这个代码库里不是"顺手的小样式"，
它是换肤的固定成本，**每加一份就多付一次**。

本轮净成本（四轮交替 A/B 实测）：`setStyleSheet` min 100→116 ms、
p50 117→122 ms（QSS 7220→8055 字符、部件 +19 个）。`apply_theme` 整体
仍在 P7 阈值（1500 ms）内，但**比上一轮贵 ~10%**——记在这里，
不藏在"通过"两个字里。

### 修复（第四轮：中文方框 / CLI 编码）

- **整屏中文渲染成方框**。根因两层：
  1. QSS 的 `font-family` 写成了多族链（`"Microsoft YaHei UI","Microsoft YaHei",…`），
     而 **QSS 不支持多族回退**——族名与系统实际名称对不上就回退到**默认字体**，
     那个默认字体没有汉字字形。修法：字体族**运行时解析**
     （`T.init_fonts(app)`：注册字体文件 → 设应用字体 → 写进主题令牌），QSS 只写一族名；
  2. `QTableWidget { font-family:{mono} }` 把**整张表**设成等宽，而文件表有中文
     文件名、元数据表更是整页中文；`KVTable` 还按**键名**判断等宽（给中文**值**
     套等宽字体），`StatCard` 的 Hero 数值同理。
     修法：字体**按值的实际内容**选（`theme.pick_text_font`）。
- **CLI 在 GBK 控制台乱码/崩溃**：所有文案都是 UTF-8 源码里的中文，`｜`、`…`
  这类字符 GBK 编不出来 → `print()` 抛 `UnicodeEncodeError`，用户看到 traceback。
  新增 `cli._setup_console()`：切控制台到 UTF-8 + `errors="replace"` 兜底。

### 新增（第四轮：色调律动）

- **渐变**：标题栏 / 侧边栏 / 卡片 / 进度条用 `qlineargradient`（只用现有令牌，
  **没有新增任何色号**），给出"光从上面来"的方向感。
- **次级强调色 `accent2`**（分裂互补，色相 +150°，同明度同彩度）：纳入 WCAG 判据
  （非文本 3:1），用于进度条渐变与多文件对比图的前两条曲线。

### 新增（第三轮：动效模块化 + 色温分层 + 时频图性能）

- **`ui/motion.py`**：动效从窗口类的私有方法提成可复用模块（`fade` / `stagger` /
  `pulse` + 缓动名表 + 令牌化时长）。清理逻辑集中一处（`isValid` 守住
  "部件先走、效果后到"的竞态）。**没有 `slide`**：布局管理的控件没有可靠的位置
  动画，硬做出来的是假动效——不提供，并写明理由。
- **信息驱动的动效**：结论为「有疑 / 不达标」时判定徽标脉两次。常驻装饰会持续
  吃 CPU 且被习惯性忽略。
- **配色的色温分层**（`_warm_cool_hue`）：中性阶与文字阶按明度往冷/暖各偏一点，
  **暗处偏冷、亮处偏暖**（自然光照的规律）。偏移随 |L−0.5| 增长、封顶 16%、
  沿**短弧**转；5 条新测试钉住方向与边界，WCAG 判据照旧全过。
- **时频图性能**（本轮最硬的数字）：
  | 场景 | 改前 | 改后 |
  | --- | --- | --- |
  | 频谱图重绘（A/B 同轮对照） | 352 ms | **170 ms** |
  | 瀑布图一次拖拽手势 | 674.7 ms | **261.9 ms** |
  | 悬停（blit）p95 | 13.9 ms | 13.7 ms（不变） |

  做法：频谱图从 `pcolormesh` + 对数轴（约 180 万个多边形）改为
  **对数频域重采样 360 行 + `imshow`**（纵轴用 log10 坐标 + 手动刻度，
  观感等价；重采样结果按数据身份缓存）；瀑布图每条谱线 2000 → 300 点。
  `perf_probe.py` 新增 **P10/P11**，把新旧两种口径在**同一次运行内 A/B 对照**
  量出来——"我觉得快了"从此有数字。

### 新增（第二轮：让色彩系统"能用"、让界面动起来、频谱曲线专业化）

- **种子色主题接进界面**：`SeedDialog`（Ctrl+G / 命令面板）——8 个预设 + 手输 hex +
  深浅切换，**实时预览生成的配色**，并把"全部 15 条 WCAG 判据通过 ✓｜文字类最弱
  一环 xx:1"直接写进对话框。配色是算出来的，就让用户看见它是怎么算出来的。
  QSettings 记住上次种子；`--seed #hex [--mode dark|light]` 启动参数；
  Ctrl+D 在种子色主题下切换的是**该种子的深浅**，而不是跳回内置主题。
- **动效**（全部走 Qt 属性动画，一条都不碰 matplotlib——画布一次重画是百毫秒量级，
  用它做动画会顶穿帧预算）：仪表环形进度缓动、切页淡入、指标卡错峰浮现。
  效果与动画**用完即拆**，且必须先用 `shiboken6.isValid` 判断再清理——
  `setGraphicsEffect(None)` 本身就会让 Qt 释放旧效果（实测 RuntimeError）。
- **频谱曲线专业化**（主证据图，画幅 6.2×3.6 → 7.0×4.4）：
  Bark 临界频带底纹（对数轴等距 ≠ 感知等距）、1/3 倍频程平滑线与原曲线叠放、
  噪底参考线（有测量值才画）、次刻度改回 2/5 两档、
  频段柱状图弃 viridis 改**主色派生的 OKLCH 等明度渐变**（频段是有序类别）。
- `packaging/palette_report.py` / `mem_probe.py` / `perf_probe.py`（上一轮已加，此处补记：
  三条基准跑法见 §10）。

### 修复（第二轮）

- 栅格测试当场抓到种子色对话框尺寸写成 110/90/30/38 → 归位 112/88/32/36。
- 动效清理时对已删除的 Qt 对象调用 `deleteLater` → `RuntimeError`；
  改为 `shiboken6.isValid` 判断后再清。

### 修复

- **手选主题的对比度不达标**（审计出来的，不是猜的）。改动都是 OKLCH 的 L 轴上
  最小步长，**色相不变**，所以"修完之后还是那个颜色，只是更清楚"：

  | 位置 | 改前 | 对比度（要求 4.5） | 改后 |
  | --- | --- | --- | --- |
  | DARK 弱文本 `text_faint` | `#626c80` | 3.46 | `#747f93` → 4.52 |
  | LIGHT 弱文本 `text_faint` | `#8a94a8` | 3.05 | `#6d778a` → 4.51 |
  | LIGHT 合格胶囊 `success` | `#15a34a` | **2.99** | `#008137` → 4.54 |
  | LIGHT 有疑胶囊 `warning` | `#b97a04` | **3.29** | `#9b6500` → 4.52 |
  | LIGHT 不达标胶囊 `danger` | `#dc2626` | **4.23** | `#d61e21` → 4.52 |
  | DARK 主按钮白字 | `#fff` on `#3d7dff` | **3.77**（悬停时 3.00） | 改墨字 `#0d0f13` → 5.16 |

  浅色主题的三个判定胶囊是**全应用最关键的一行信息**（"这批音源能不能用"），
  而它们原本低于 AA。主按钮白字同理：白字压在亮蓝上只有 3.77:1，压深主色会
  把品牌色变闷，所以选了"文字去适配底色"这条路。
- **`QPushButton:hover` 里写死 `#1c212b`**：浅色主题下悬停按钮变成近黑底 +
  深色字（约 1.1:1，**字直接消失**）。改用 `hover_hi` 令牌，并加了一条测试
  扫描 QSS 模板：除登记过的白字外不许再出现写死的颜色。
- **换肤不重刷内联样式控件**：指标卡（`StatCard` 自己 `setStyleSheet`）与判定条
  （`_banner_update` 拼的 inline style）的颜色**不来自 QSS**，换肤后保留旧主题的
  文字色 —— 深色切浅色时数值几乎看不见，而"切换主题"看起来是成功的。
  新增 `_refresh_selection_theme()`；`tests/test_ui_theme_consistency.py` 钉住。
- **令牌角色冲突**：`primary_hi` 一个名字被当两种用途——"主按钮悬停**底色**"
  （要够暗，按钮文字才达标）与"激活导航**文字**"（要够亮，压在 `primary_soft`
  上才达标），方向互斥，实测激活导航只有 3.2:1。拆出 `primary_text`；
  另加 `on_primary` 承接"主色上的文字"——不是所有主色都配得起白字。

- **内存随使用线性增长**（严重）：`SpectrogramCanvas.redraw()` 每次重画都
  `fig.colorbar(...)` 新建一个色条 Axes，而 `ax.clear()` 带不走它；色条握着
  mappable → 握着那份 S 矩阵，**永远回收不掉**。实测（`packaging/mem_probe.py`）：
  频谱图每次重画 **+17.9 MB**、点选 10 个文件 **+178.8 MB**、切换主题 30 次
  **+576.5 MB**、一整段流程净增 **+845.7 MB**，`figure.axes` 从 2 涨到 31 个。
  改为**建一次、之后只换 mappable**（`Colorbar.update_normal`）。
  修后：每次重画 +0.03 MB，axes 恒为 2，换肤 30 次 +3.5 MB，全流程净增 +43 MB（首轮预热后逐轮为 0）。
- **视图缓存按条数限制**（12 条）：单份 `(t, f, S)` 的大小取决于 `nperseg` / 时长 /
  `max_cols`，改一个常量就能让「12 条」变成 GB 级。改为**按字节预算**淘汰
  （`CACHE_MAX_BYTES = 96 MB`，FIFO，至少留 1 份）。同时把 S 从 float64 降到
  **float32**（dB 只需 0.01 dB 精度），缓存与 `pcolormesh` 内部缓冲直接减半。
- **视图数据重复计算**：快速来回点选同一文件会为同一 `(文件, 视图)` 起多个线程，
  每个都白读一遍盘 + 白做一遍 12 s / 4096 点 STFT。改为按 key 登记 + 去重，
  线程结束自动摘掉（顺带把 QThread 的引用管理写清楚）。
- **只算当前在看的那张图**：点选文件时不再同时生成频谱图 + 瀑布图，切到对应页才补算。
- **通知动画永不回收**：`QPropertyAnimation` 以 ToastHost 为父对象且只 `start()`，
  每次通知泄漏 2 个动画 + 1 个弹窗（动画握着 target）。改为 `DeleteWhenStopped`。
- `.gitignore` 漏掉基准探针的输出目录：70 s × N 个 PCM_16 wav（每个约 12 MB）
  会被 `git status` 当成待提交文件。

### 新增

- **`ui/palette.py`：从种子色生成整套语义色（OKLCH + WCAG 对比度校验）**
  - OKLab/OKLCH 换算按 Ottosson 2020 的公式实现，测试拿**原文参考值**当锚点
    （纯红/绿/蓝的 L/C/h，例如 `#0000ff → h 264.05`）——公式抄错立刻露出来，
    而不是等到某张图"看着怪"；
  - 超色域时**保色相降 chroma**，不是把 RGB 硬夹（硬夹会改成另一个颜色）；
  - 生成规则：种子色只提供**色相**；中性阶固定 L 阶梯（带极低 chroma 的种子色温度）；
    文本阶沿 L 轴二分到达标；语义色色相固定在常用区间（绿 145° / 黄 85° / 红 25° / 青 230°）；
  - `audit()` 的 15 条判据来自 WCAG 2.1（正文 7.0、次要与小字 4.5、非文本 3.0），
    生成时 `strict=True`，**不达标直接抛异常**而不是打个警告放过去；
  - `packaging/palette_report.py`：对照度报告 + 色卡图（`--seed` 可换种子）。
- **`packaging/perf_probe.py`**：交互帧时间基准，详见下文「性能」。
- **`packaging/mem_probe.py`**：内存基准探针（7 个场景 + 阈值判据 + `--strict` + `--trace` 归因）。
  文件头顺便记下两个**探针保真度**的坑：`GetProcessMemoryInfo` 住在 **psapi.dll**
  （kernel32 只有 `K32` 前缀那版）；`processEvents()` **不派发** `DeferredDelete`，
  不手动 `sendPostedEvents` 就会看到「每点一次涨 0.6 MB」的**假泄漏**。
- **`tests/test_ui_memory.py`**（12 用例）：把"必须有界"的四件事钉死——图元数、
  缓存字节、线程去重、按需生成；另有惰性重画与拖拽节流两条不变量。
  断言全部用**对象计数**而不是进程内存（后者受分配器与字体缓存影响，
  放进 CI 只会得到偶发失败）。
- **`packaging/perf_probe.py`**：交互帧时间基准（9 个场景，含"悬停三版对照"），
  详见下文「性能」。

### 性能

先量后改。`packaging/perf_probe.py` 把每个交互的 p50/p95/max 摆出来，
三处改动全部由数字驱动（本机环境见该文件头：自由线程 CPython 3.14，
单核约为常规桌面 1/3，裸 matplotlib 复绘一张 600 点图要 29 ms）：

| 动作 | 改前 | 改后 | 做法 |
| --- | --- | --- | --- |
| 悬停 | 177 ms（最初的全量重建）/ 131 ms（上轮的"分层"） | **15.7 ms** | 背景快照 + `draw_artist` + `blit` 局部重绘。blit 本体仅 0.15 ms，其余是 Qt 拷 3.5 MB 像素 |
| 切页（数据未变） | 438 ms | **3.8 ms** | 同一份数据再喂一次不再重画（`_load_view` 命中缓存时每次切页都会重复喂） |
| 表格刷新 200 行 | 214 ms | **38 ms** | 批次概览画布不可见时不重画 |
| 分析中每回来一个文件 | 37–214 ms | **0.00 ms** | 表格刷新合并到 120 ms 一次（`_schedule_refresh`） |
| 换肤 | 1589 ms | **788 ms**（无选中）/ 1003 ms p50、1411 ms p95（有选中，含 18 张指标卡重建） | 只重画当前可见的画布，其余标脏等 `showEvent` 补画 |
| 一次拖拽手势（20 次移动） | ~7.3 s | **0.85 s** | 3D 重绘限流到 ~8 fps，松手补最终帧；角度每次移动都照常更新 |

**"上轮的悬停优化"值多少，现在有数了**：静态层 / 悬停层分离只省下"重建图元"
那一部分（177 → 131 ms），大头始终是整图 `draw()`。真正解决问题的是 blit。

留给下一轮的已知成本（有实测数字，别再靠感觉）：频谱图一次重绘 470 ms，
其中 `draw()` 约 370 ms（mesh 83 ms + constrained layout 70 ms + 刻度文本）；
换成 `imshow` + 对数重采样行可以再降一个量级，但会动到频谱图的观感，需谨慎。

## [1.0.0] - 2026-10-11

首个正式版本：从一堆脚本重构为可扩展的 Python 包。

### 新增

- **插件化分析引擎**：分析器自动发现，加一个指标 = 加一个文件（`core/analyzers/`）。
  内置 7 个：`basic` / `spectrum` / `loudness` / `dc_offset` / `stereo` / `metadata` / `verdict`。
- **标准级 DSP**：K 加权与门限积分响度按 **ITU-R BS.1770-4** 实现（系数按采样率实时重算）；
  **LRA** 按 EBU R128 Tech 3342；**真峰值** 4 倍过采样。
- **元数据取证层**：跨格式标签归一化，提取出版商/厂牌、ISRC、版权、注释，以及**编码器串**。
- **GUI 新增视图**：3D 瀑布频谱（可拖拽旋转）、元数据页（标签 + 全部指标）、
  环形仪表、18 项指标卡。
- **CLI**：`audiocheck` 命令，支持 `--json/--csv/--plot/--only/--config/--list-analyzers`。
- **测试套件**：68 个用例，全部基于合成音频，可离线回归。

### 修复

- **截止点检测的系统性偏差**（两处，都是"会把正常音源判成有损"的严重错误）：
  - v1 恒定偏低 N-1 步 → 44.1k 满带宽文件恒报 19.64kHz，**把满带宽 CD 误判成"轻微带限"**；
  - v2 在 dB 域平滑，把砖墙抹成 12% 宽的过渡带 → 检出值**系统性偏高约 8%**。
  - v3 改为**功率域平滑 + 半功率点定位 + 陡度守卫 + 顶部深坑兜底**，合成基准误差 <1%。
- 砖墙判定符号写反（`slope` 是正值下降量，代码在比负值），导致砖墙**从未触发过**。
- 响度/真峰值/DR/削波/采样间过载等标记未纳入结论，现已写入判定与建议。
- `numpy.float32` 不是 `float` 子类，直接 `json.dump` 会 `TypeError`（且只有部分字段出错，难查）。
- QThread 对象被 GC 导致进程硬崩（两个视图同时启动时后者覆盖前者的引用）。
- 变量遮蔽（`for i, w in ...` 覆盖了外层代表 QWidget 的 `w`）导致
  `RuntimeError: Internal C++ object already deleted`，报错完全不提示真因。
- Qt6 不再自带字体，未注册系统字体时中文全变方框。
- matplotlib 3D 轴与 `constrained_layout` 冲突，会把轴挤成 0 宽。

### 变更

- 包结构改为 `src/` 布局，`import audiocheck` 不拖入 Qt（界面依赖懒加载）。
- 所有阈值集中到 `config.Thresholds`，可从 JSON 载入、界面可调、测试可覆盖。
