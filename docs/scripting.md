# 脚本化使用 audiocheck（P5 生态起点）

audiocheck 的编辑内核是**纯 Python 公开 API**——不装插件、不进进程间通信，
`import` 即用。本文是脚本入口的权威说明；配套的完整可运行示例在
`examples/restore_pipeline.py`（由 `tests/test_scripting_example.py` 以子进程运行——
**示例即判据**：示例坏了判据红，文档永远不会描述一个不存在的接口）。

## 三层入口

| 层 | 入口 | 适合 |
|---|---|---|
| **内核 API** | `from audiocheck.core.edit import …` | 脚本/批处理/研究；本文主体 |
| **CLI** | `python -m audiocheck.edit_cli <源> <目标> …`（含 `--batch`/`--project`/`--chain`） | 命令行/工作流编排 |
| **GUI** | `python -m audiocheck.ui.editor`（REAPER 式工作站） | 交互修复 |

三层走**同一个内核**：GUI 应用一个算子与脚本里 `session.apply(op)` 是同一份代码、
同一份审计口径。

## 核心模型（三条铁律）

1. **非破坏**：`Project = 源 + 操作栈`，源文件只读；`render()` 是纯函数——
   同一输入必得同一输出（可复算）。
2. **可审计**：每次 `render()` 返回**审计 dict**（每操作：改了什么/依据什么/实测结果）；
   `report.write_report(audit, "report.md")` 排成人可读 Markdown。
3. **有判据才做**：每个算子的默认参数都有实测出处（`docs/editor_research.md`）；
   脚本里改默认值前请先读效力边界。

## 最小示例（5 行）

```python
from audiocheck.core.edit import Project, GainOp, render

audit = render(Project("in.wav", [GainOp(1.0, 2.0, -3.0)]), "out.wav", overwrite=True)
print(audit["ops"][0]["applied_frames"], "帧被改")   # 选区 [1,2)s：源需覆盖该区间，否则 0 帧
```

## 修复链（推荐形态）

```python
from audiocheck.core.edit import (DehumOp, DeclipOp, DeclickOp, Project,
                                  render, save_chain, load_chain)
from audiocheck.core.edit.report import write_report

chain = [DehumOp(0.0, 4.0, 50.0),      # 顺序不必手工排：渲染按 render.PHASE 规范执行
         DeclipOp(0.5, 4.0, 21000.0),
         DeclickOp(0.5, 4.0)]
save_chain("chain.json", chain)        # 不绑源的"配方"，可套到任何文件
ops = load_chain("chain.json")         # 严格 schema（版本化，旧文件显式迁移）
audit = render(Project("noisy.wav", list(ops)), "clean.wav", overwrite=True)
write_report(audit, "clean.report.md")
```

完整流程（含劣化素材合成）见 `examples/restore_pipeline.py`。

## 批处理

清单 JSON（每项一个小工程，互相隔离）：

```json
[{"source": "a.wav", "dest": "a.out.wav", "ops": [],
  "subtype": "PCM_24", "audit": "a.audit.json", "report": "a.report.md"}]
```

```bash
python -m audiocheck.edit_cli --batch jobs.json
```

`--chain` 可把同一份修复配方套到每个文件；`--project` 复算一个保存过的完整工程。

## 纪律（脚本作者同样受约束）

* **源永远不被覆盖**（渲染层硬保证）；目标默认不许覆盖已存在文件（显式 `overwrite=True`）。
* 改参数 = 改判据对象：每个默认值（阈值/Q/压制上限/跟踪开关）都有实测表；
  脚本里覆盖它们之前，先在 `docs/editor_research.md` 里找到那张表。
* 审计 dict 与报告**不要手工修饰**——它们是机器产物；要引用数字，引用探针
  （`packaging/*_probe.py`）复算。
