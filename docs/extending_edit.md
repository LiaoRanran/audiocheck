# 编辑内核扩展指南：加一个修复算子要改哪里

> 本文是"隐含知识显式化"：加一个新算子（如 `DecrackleOp`、`DeHum2Op`）的**完整触点清单**。
> 以 `DeclickOp` 为基准度量：**9 个源文件 + 3 类文档/判据**。漏一步 = 某个表面静默缺失。

## 触点清单（按依赖顺序）

### 内核（4 文件，必须）

| # | 文件 | 做什么 |
|---|---|---|
| 1 | `core/edit/<algo>.py` | 算法本体：`def <algo>(y, …) -> (out, stats)`；默认常数**必须有实测出处**（探针 + docs/editor_research.md 条目） |
| 2 | `core/edit/ops.py` | `<Op>` 不可变 dataclass：校验（`__post_init__`）、`to_dict`、`describe()`（人可读，Inspector 直接用）；**`_OP_SCHEMA` 注册**（kind → 类 + 精确键集）；`op_from_dict` 的特殊类型还原（tuple 等） |
| 3 | `core/edit/render.py` | ① `render()` 分派分支（全同域走专用路径，否则进链式）；② `PHASE` 相位号（规范顺序——依据要实测：`op_order_probe` 模式）；③ 专用 `_render_<algo>` 或复用既有路径；④ `_audit` 的 `algorithm` 口径名 |
| 4 | `core/edit/__init__.py` | 导出 Op 类与便捷入口 |

### 表面（5 文件，按产品形态取舍）

| # | 文件 | 做什么 |
|---|---|---|
| 5 | `edit_cli.py` | CLI 旗标（与既有旗标的**互斥校验**）、算子构造分支、汇总打印分支 |
| 6 | `ui/editor/ops.py` | 算子下拉档位 + `_apply` 分支 + 参数控件 |
| 7 | `ui/editor/shell.py` | 命令面板条目（`_build_commands`） |
| 8 | `ui/editor/batch.py` | （自动）批处理走 `to_dict` JSON，**无需改**——除非有特殊选区交互 |
| 9 | `ui/editor/inspector.py` | （自动）`describe()` 驱动，**无需改**——除非需要选中明细（参照 poly 顶点表） |

### 判据与文档（3 类，一个不能少）

1. **合成语料探针**：`packaging/<algo>_probe.py`（候选对比 + 参数扫描 + **误报账**——
   "代价不对称"是本项目的默认纪律）；
2. **判据文件**：`tests/test_edit_<algo>.py`（非破坏逐位、正收益、误伤钉死、schema 往返）；
3. **文档**：`docs/editor_research.md` 一节（机制/实测表/否决项如实记账）+ CHANGELOG +
   `docs/scripting.md`（若 CLI 旗标变化）。

## 已固化的自动化护栏（新算子自动被覆盖）

* **schema 幂等 property**（`tests/test_properties.py`）：把新 Op 加进算子列表即被扫；
* **变异测试**：`packaging/mutation_probe.py` 的 TARGETS 加一行（模块 → 判据文件）；
* **等价体白名单**：源码行号漂移由 `--audit-registry` 自动巡检（过期 = 分数不可信）；
* **文档数字对账**：`packaging/docs_number_audit.py` 注册表加一行；
* **审计报告**：`core/edit/report.py` 的 `_KIND_TITLES` 加一个中文名（其余字段自动照排）。

## 版本纪律

`_OP_SCHEMA` 增键 ⇒ `PROJECT_VERSION` +1 + `_migrate` 显式迁移
（旧记录补默认值，语义 = "当时的行为"；判据：迁移后渲染与旧版逐位一致）。
参考 v1→v2（track_floor）、v2→v3（step_repair）。
