# 脚本化使用 audiocheck（P5 生态起点）

audiocheck 的编辑内核是**纯 Python 公开 API**——不装插件、不进进程间通信，
`import` 即用。本文是脚本入口的权威说明；配套的完整可运行示例在
`examples/restore_pipeline.py`（由 `tests/test_scripting_example.py` 以子进程运行——
**示例即判据**：示例坏了判据红，文档永远不会描述一个不存在的接口）。

## 三层入口

| 层 | 入口 | 适合 |
|---|---|---|
| **内核 API** | `from audiocheck.core.edit import …` | 脚本/批处理/研究；本文主体 |
| **CLI** | `audiocheck-edit-cli`（或 `python -m audiocheck.edit_cli`）；含 `--batch`/`--project`/`--chain`/`--verify-audit`/`--version`/`--doctor` | 命令行/工作流编排 |
| **GUI** | `audiocheck-edit`（或 `python -m audiocheck.ui.editor`，REAPER 式工作站） | 交互修复 |

> 发布包（`audiocheck-1.1.4-win64.zip`）里只有 **`audiocheck.exe` 一个文件**：
> 无参数开界面、带参数走 CLI（v1.1.3 及更早没有这个分派，任何参数都会卡死）。

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

## 复合算子（`--op`）

一条命令跑多道修复（此前只能写项目记录 / 修复链 JSON）：

```bash
audiocheck-edit-cli in.wav out.wav     --op "declick@0-2:threshold=5"     --op "declip@0-2:ceiling=0.34,bandwidth=21000"     --op "dehum@0-2:f0=50,q=8"
```

**语法**：`kind@t0-t1:key=value,key=value`，`--op` 可重复，
**出现顺序就是操作栈顺序**。`@t0-t1` 也可写成 `at=t0-t1`。

| kind | 必填 | 可选 |
|---|---|---|
| `gain` | `at`、`gain=dB` | — |
| `band_gain` | `at`、`band=f0-f1`、`gain=dB` | `n_fft`/`hop`（命令行给） |
| `poly_gain` | `points=t,f;t,f;...`（≥3 对） | `gain=dB`（默认 −6） |
| `declip` | `at`、`bandwidth=Hz` | `ceiling`、`iterations` |
| `declick` | `at` | `threshold`、`max_run`（0=关） |
| `dehum` | `at`、`f0=Hz` | `q`、`harmonics=1,2,3` |
| `denoise` | `at`、`noise=t0-t1` | `reduction_db`、`no_floor_track=1` |

⚠ **`harmonics=1,2,3` 的逗号是取值**，不是参数分隔符（解析规则：取值吃到下一个
`标识符=` 之前）。所以 `f0=50,harmonics=1,2,3,q=8` 能正确切成三项。

⚠ **渲染顺序不是参数顺序**：多算子走链式渲染，按 `render.PHASE` 规范排序
（脉冲类先、稳态嗡最后）；被重排时审计记 `reordered_from`。
这是既有行为（与 `--project`/`--chain` 一致），`--op` 不改变它。

⚠ `--op` 与单算子开关（`--gain`/`--declip`/…）、`--at`/`--band`/`--poly`、
`--chain`/`--project` **全部互斥**（混用会让"按哪个跑"变成猜测）。
参数错误一律在**渲染之前**报出（退出码 2），不会留下半成品产物。

## 批处理

清单 JSON（每项一个小工程，互相隔离）：

```json
[{"source": "a.wav", "dest": "a.out.wav", "ops": [],
  "subtype": "PCM_24", "audit": "a.audit.json", "report": "a.report.md"}]
```

```bash
python -m audiocheck.edit_cli --batch jobs.json --jobs 8
```

`--chain` 可把同一份修复配方套到每个文件；`--project` 复算一个保存过的完整工程。

**`--jobs N` 并行**（v1.1.4）：每项作业在**独立进程**里跑（渲染是 CPU 密集的
numpy/scipy，进程隔离既避开 GIL 也避开前缀缓存的跨线程共享面）。
串行与并行走**同一份作业逻辑**，产物内容逐项一致（判据比对内容哈希）。
`--jobs 0`（默认）= 按 CPU 核数；`1` = 串行。

**退出码**（v1.1.5 修正）：`0` = 全部成功；`1` = **有作业失败**（其余照常跑完）；
`2` = 清单本身不可用（结构性错误，一项都没跑）。

**`--overwrite` 对批处理生效**（v1.1.5 修）：命令行开关与作业项里的
`"overwrite": true` **取或**——此前只读作业项，用户加 `--overwrite` 重跑完全无效。

> ⚠ v1.1.3 及更早的实现是 `return 0 if n_fail == 0 else 0`——两个分支都是 0，
> 即"无论成败都报成功"。批处理里 99 项炸 98 项，`&& echo ok` 照样打印 ok。

## no-op 不代表成功

选区完全落在素材之外时，渲染层把区间夹紧到空集（`applied_frames = 0`）——
这是**正确行为**（不该越界改文件），但命令退出码仍是 0、报告里会出现：

```
⚠ **选区没有命中任何样本（no-op）**：目标文件与源逐位相同。
   检查 `--at`/`--band`/`--poly` 是否落在素材范围内（本文件时长 2.000 s）
```

脚本要用退出码判断"有没有真的改东西"时，**还得看这一行**（或比对
`audit["ops"][i]["applied_frames"]`）。显式 `--gain 0` 是用户要求不变，
**不会**触发这条警告。

## 核对审计（跨机验证）

```bash
python -m audiocheck.edit_cli --verify-audit 对方给的审计.json
python -m audiocheck.edit_cli --verify-audit a.json --verify-audit b.json
```

**不渲染任何音频**，只重算摘要并比对。退出码 `0` = 全部一致，`1` = 有不一致。

三个摘要各证明一件事，别混：

| 字段 | 证明什么 | 参与摘要的字段 |
|---|---|---|
| `audit_digest` | **同一份修复**（同输入内容 + 同操作栈 + 同产物特征） | 除 `audit_digest` 自身、`timestamp_utc`、`dest`、`dest_sha256`、`source` 之外全部 |
| `kernel_digest` | **同一份实现**（渲染模块文件自身的 SHA256） | — |
| `source_sha256` | **同一份输入内容**（WAV 跳过 PEAK 块） | — |

剔除 `timestamp_utc`/`dest`/`dest_sha256`/`source` 的理由：它们随落盘漂移
（换个目标路径、换个时刻跑）。剔除 `audit_digest` 自身的理由是**自指**——
渲染时先算摘要再写入记录，复算时它已在 dict 里。

Python 侧同样可用：

```python
from audiocheck.core.edit.render import audit_digest, kernel_self_digest
assert audit_digest(rec) == rec["audit_digest"]
same_impl = rec["kernel_digest"] == kernel_self_digest()
```

## 纪律（脚本作者同样受约束）

* **源永远不被覆盖**（渲染层硬保证）；目标默认不许覆盖已存在文件（显式 `overwrite=True`）。
* 改参数 = 改判据对象：每个默认值（阈值/Q/压制上限/跟踪开关）都有实测表；
  脚本里覆盖它们之前，先在 `docs/editor_research.md` 里找到那张表。
* 审计 dict 与报告**不要手工修饰**——它们是机器产物；要引用数字，引用探针
  （`packaging/*_probe.py`）复算。手工改过的记录会被 `--verify-audit` 判为不一致
  （这是设计意图，不是误报）。
