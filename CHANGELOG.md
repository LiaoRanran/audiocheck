# 更新日志

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [未发布]

### 修复

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

- **`packaging/mem_probe.py`**：内存基准探针（7 个场景 + 阈值判据 + `--strict` + `--trace` 归因）。
  文件头顺便记下两个**探针保真度**的坑：`GetProcessMemoryInfo` 住在 **psapi.dll**
  （kernel32 只有 `K32` 前缀那版）；`processEvents()` **不派发** `DeferredDelete`，
  不手动 `sendPostedEvents` 就会看到「每点一次涨 0.6 MB」的**假泄漏**。
- **`tests/test_ui_memory.py`**（8 用例）：把"必须有界"的四件事钉死——图元数、
  缓存字节、线程去重、按需生成。断言全部用**对象计数**而不是进程内存
  （后者受分配器与字体缓存影响，放进 CI 只会得到偶发失败）。

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
