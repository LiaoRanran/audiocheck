# audiocheck 编辑工作站 v1 交付规格（重型工业级）

> 本文件是 **开工依据**：按 `AGENTS.md` §4 的"需求细化协议"产出，含范围、明确不做、
> 判据与验收。动手前先读 `docs/editor_roadmap.md`（方向）与 `docs/editor_research.md`（判据）。
>
> 用户拍板（2026-10-26）：
> - **界面范式**：REAPER / 宿主式（多停靠面板可浮动/标签化 + 全局走带条 + 大时间轴编辑区 + 专业暗色高密度）。
> - **播放引擎**：miniaudio（路线 P1；低延迟、WASAPI 独占模式、设备热插拔、X-run 计数）。
>   ⚠ 探明：miniaudio 在 Windows **无 ASIO 后端**（只有 WASAPI/DSOUND/WINMM/NULL），
>   低延迟走 WASAPI 独占；不假装支持 ASIO。
> - **第一版边界**：全栈编辑工作站（播放 + 波形/频谱编辑视图 + 矩形时间×频率选区 + 四类修复算子 + A/B + 审计面板，全接 GUI）。

---

## 目标（一句）
把"可审计修复"从命令行提升到 **REAPER 式停靠面板工作站**：能播放、能在频谱/波形上框选区、
能对选区跑去削波/咔哒/嗡/噪四类算子、能 A/B、每次操作都出可复算的审计记录。

## 分类与定位
- 类型：**加功能（新子系统）**，不是改数值/判据。
- 子系统：新增 `core/edit/playback.py`（P1 柱子，现为零）、新增 `ui/editor/`（REAPER 式外壳）。
- 影响：不改动既有判定/分析内核；编辑层**仍不 import 显示层**；测量层只经 CLI 边界把 `content_bandwidth` 喂给编辑层（已有纪律）。

## 信息架构（REAPER 式）
```
┌──────────┬───────────────────────────┬──────────────┐
│ FileDock │ EditorDock（波形+频谱+选区）│ InspectorDock│
│ (文件/批) │  ┌─ 时间轴刻度 / 缩放锚点 ─┐ │ 审计记录      │
│  + 批处理 │  │ 频谱图 + 波形 + 选区矩形 │ │ 操作栈        │
│  + 队列   │  │ 播放头（跟播）          │ │ A/B 切换      │
│           │  └────────────────────────┘ │ 参数/依据    │
├──────────┴─────────── 走带条 ─────────┴──────────────┤
│ ▶/⏸ 设备选择 时间/采样 缩放 选区工具 修复算子下拉     │
└───────────────────────────────────────────────────────┘
```
- 三块 `QDockWidget`，可浮动/标签化/重排（REAPER 的核心交互）。
- 顶部 `TransportBar` 常驻（全局播放控制）。
- 复用既有 `theme.py` / `palette.py` / `widgets.py`（设计系统不变，只加工业布局）。

## 范围（In scope）
1. **播放引擎** `core/edit/playback.py`：设备枚举、WASAPI/ASIO 后端选择、低延迟播放、
   播放头回调（~每 20–30 ms 抛位置）、停止/暂停、设备故障（热插拔）回调、X-run 计数（尽力）。
2. **REAPER 式外壳** `ui/editor/`：`EditorWindow`（或扩展 `MainWindow` 的新模式）、
   三个可停靠面板、走带条、命令面板沿用既有 `CommandPalette`。
3. **编辑视图**：频谱图（复用 `SpectrogramCanvas` 数据）+ 叠加波形 + **矩形时间×频率选区**（拖拽）。
4. **选区→算子**：把现有 `DeclipOp/DeclickOp/DehumOp/DenoiseOp` 经 `render` 接到 GUI：
   选区即时间×频率约束，`--band` 接频率窗、时间窗接 render 的 segment。
5. **A/B**：原始 vs 处理后并存，一键切换 + 读数差。
6. **审计面板**：渲染审计 + 操作栈 + 项目记录（JSON 可复算，已有 `Project`）。

## 明确不做（Out of scope）
- 多轨 / MIDI / 虚拟乐器 / 总线混音（路线 §6 非目标）。
- VST3/CLAP 插件宿主（P5，本轮不做）。
- decrackle（语料/探针已挂账，本轮不落地，见 `editor_research.md §17`）。
- 实时处理宿主（非目标）。
- MP3/AAC 写回（只读+编辑后导出路径，沿用既有）。
- 密集削波稀疏解 / 非稳态嗡 / 瞬态保护（路线图已挂账，不重开）。

## 判据与验收（可验证，逐条）
- **播放引擎**（headless 可测，不依赖声卡）：
  - 设备枚举返回非空 `[(name, backend)]`，能从名字回查后端 ✅
  - `open()` 在 `oaDummy`/静默后端可构造（不真出声也能测状态机）
  - 状态机：`stopped→playing→paused→playing→stopped` 全转移合法；非法转移不崩
  - 播放头回调频率落在 `[20, 60]` ms 区间（mock sink 推进时间）
  - 设备拔掉触发 `on_device_error`（注入模拟），不抛未捕获异常
  - X-run 计数在溢出时 +1（构造溢出条件）
  - **测试**：`tests/test_edit_playback.py`（≥20 条）；变异覆盖核心状态机。
- **外壳/布局**（沿用本仓 UI 纪律）：
  - 三个 `QDockWidget` 可 `setFloating` / `tabify` / 重置；重置后回到默认三栏
  - 走带条在 offscreen 下可构造、按钮 `StrongFocus`（a11y 旧纪律）
  - 间距走既有 `Space` 令牌（4px 栅格），新增布局不许脱栅（复用 `test_ui_grid` 口径）
  - 配色走 `palette` 生成，新增语义色必须进 `audit()`（WCAG 判据）
  - 暗色/浅色切换后编辑视图与面板重刷（复用 `_refresh_selection_theme` 机制，旧 bug #21 不许复发）
  - **测试**：`tests/test_ui_editor_shell.py`（offscreen，沿用 `test_ui_keyboard_flow` 套路）。
- **选区**（可测）：
  - 拖拽产生 `Rect(t0,t1,f0,f1)`，四角在视图坐标系内夹紧；越界回弹
  - 选区 ⇒ 频率窗 `[f0,f1]` 传给 `BandGainOp` 等同口径（复用 §12.7 外扩跨度）
  - 选区 ⇒ 时间窗 `[t0,t1]` 传给 render 的 segment；选区外逐位不变（复用 P0 往返判据）
  - 键盘可建/可调选区（方向键微调、Shift+方向扩展）
- **算子接入**（复用既有判据）：
  - 每类算子走 GUI 后，与 CLI 同参数产出**逐字节相同**渲染（互证，沿用 P0）
  - 审计面板显示 `changed_samples / suppression_db / ambiguity / residual_scale` 等（已有字段）
- **A/B**：切换不改源字节；A/B 读数差 == 操作实测差（容差内）
- **门禁**：`ruff check src tests packaging` 全绿；`pytest` 新模块全过；
  `mutation_probe` 覆盖 `playback.py` 状态机（下限 0.90）；
  `palette_report --strict` / `mem_probe --strict` / `perf_probe --strict` 改动相关项前后各跑一次。
- **截图留档**：`shots/editor_v1/` 三栏默认布局 / 选区 / A/B / 暗亮切换 各一张（沿用 `shot.ps1`）。

## 风险与回滚
- **miniaudio 许可**：MIT（已 `pip-audit` 可核）。新增依赖须在 `pyproject` 记 + `packaging/sbom.py` 复算；GPL 传染性无（miniaudio 是 MIT）。
- **自由线程解释器**（本机 3.14 no-GIL）：纯 Python 播放调度慢 ~70%，但播放走 miniaudio 的 C 层回调，Python 只做状态机，影响小；若卡顿再测 GIL 构建。
- **回滚**：每步独立提交；播放引擎零 UI 依赖，可单独回滚。

## 先读（文件 + 章节，精确到节）
- `docs/editor_roadmap.md` §1–§14（范围/非目标/已收口）
- `docs/editor_research.md` §7–§17（各算子判据与挂账）
- `src/audiocheck/core/edit/{render,ops,session,stft,metrics,corpus}.py`（既有内核，必须复用不可重写）
- `src/audiocheck/ui/{theme,palette,widgets,charts,window}.py`（设计系统，布局必复用）
- `tests/test_edit_*.py`（既有判据写法，新测试照此钉）
- `AGENTS.md` §3 七条宪法、§6 环境坑（无 QApplication 调字体、deleteLater 假泄漏等）

## 交付顺序（分片，每片可验收）
1. **P1 播放引擎** `core/edit/playback.py` + `tests/test_edit_playback.py`（先量后改，本片不碰 UI）
2. **REAPER 式外壳骨架** `ui/editor/`：三 dock + 走带条（offscreen 可构造）+ 主题/命令面板复用
3. **编辑视图**：频谱+波形+矩形选区（缩放/平移复用 `ui/zoom.py`）
4. **算子接入 + A/B + 审计面板**
5. **门禁 + 截图 + CHANGELOG + 打包真启动一次**
