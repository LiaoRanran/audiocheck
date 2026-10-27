# 外部工程实践借鉴记账（2026-10-27）

> 纪律：**能抄的抄、不引用的（标准库级改进）直接内化；前沿技术自研落地；好用的算法直接用**。
> 本文登记"看了什么、抄了什么、为什么"，避免重复调研，也避免"借鉴了但装作原创"。

## 一、queyi_core（`vendor/queyi_core`，89 py / 1.6MB）

**它的三层架构**：领域无关协议内核（纯标准库）→ 可插拔保护器 → 领域内容。
核心思想：**"防止系统自己骗自己"的运行时闸门**（shadow/enforce 双模式）+ 信任根自证。

### 已内化（`core/edit/render.py`）

| 借鉴项 | 我们的落地 | 为什么 |
|---|---|---|
| `canonical_json`（sort_keys + 紧凑 + 非 ASCII 直出） | `render.canonical_json` | `json.dump(indent=2)` 随键序/空格漂移 ⇒ 哈希不能当"同一份记录"的证据 |
| `digest_of`（确定性序列化后 sha256） | `render.audit_digest`（**剔除 timestamp/dest**） | 同一修复的两个落盘位置/不同时刻 ⇒ 必须同摘要，否则"可复算"无法跨机核对 |
| `kernel_self_digest`（内核文件自哈希） | `render.kernel_self_digest` → 审计 `kernel_digest` | "可复算"不止同参数同结果，还包括**实现没被偷偷改过**（法证口径） |
| 证据分级 L1–L5 | `render._EVIDENCE_LEVELS` / `evidence_level()` → 审计 `evidence_levels` + 报告〔实测〕〔推导〕〔设定〕 | 每个数字标注"有多可信"；`None` 明确为 **L5 弃权**而非"零" |

### 未采纳（诚实记账）

- **四状态机 / 插件注册 / 熔断器**：本项目的"流程"是渲染管线而非知识判定，
  状态机模型不匹配；熔断器针对长跑外部调用，我们无此形态。
- **`shadow|enforce` 双模式**：我们的判据已全部是 enforce（测试失败即红），
  不需要影子模式。

## 二、行远本体（`前途资料库/tools`，29 工具）

**它的核心**：把"科研级"当**可统计对象**——覆盖率、格式一致性、无源数字清单。

### 已内化

| 借鉴项 | 我们的落地 |
|---|---|
| `math_audit` 的"数字必须能复算"思想 | `packaging/docs_number_audit.py`（注册表 + `--strict` 门禁）|
| `cite_audit` 的"无凭据数字清单" | 同上：注册表里每条必须给**复算函数**，否则不加 |
| "口径标注"（访问日/抓取日） | 审计 `timestamp_utc`（CLI 注入，render 保持纯函数）|

### 未采纳

- **HTML/PDF 书稿流水线**：文档形态不同（我们是代码库 + Markdown 文档），
  但"每部数字密度"这个思路已转化为 `docs_number_audit` 的"每条核心数字都有复算函数"。

## 三、CPP-Bible（814 工具）

**它的核心**：极端密集的对抗性自检（adversarial loop / 校准追踪 / 弃权分类器）。

### 已内化

| 借鉴项 | 我们的落地 |
|---|---|
| **ABSTAIN 六态**（SUPPORTED/REFUTED/UNDECIDED/INSUFFICIENT_EVIDENCE/CONFLICTED/STALE）——"弃权不是失败，是系统能力" | 弃权四态的音频版：`L5_unknown`（量具不可用）+ 审计里 `ambiguous`（欠定：多个合法解）+ `read_incomplete`（源截断）+ `noop`（带宽无约束，如实不做）——**四种"做不到"都不装成"做到了"** |
| **"新工具必有 `--check`（只读自验证，exit 0 = 通过）"** | 我们所有探针的 `--strict` / `--audit-registry` / `docs_number_audit --strict` 已同构 |

### 未采纳

- **adversarial loop / 权重校准**：面向 LLM 判定的对抗迭代，本项目的算法是确定性的，
  对抗形态应为 fuzz + 变异测试（已有：2000+ fuzz、变异满上限 10 靶）。

## 四、总结：三条纪律的共同内核

三个仓库（跨领域：C++ 知识库、生涯规划、协议内核）收敛到同一条工程哲学：

1. **可复算**（数字要能从仓库算出来）→ 我们：探针 + 数字对账 + 审计摘要；
2. **显式弃权**（不知道就说不知道）→ 我们：L5/ambiguous/noop/read_incomplete；
3. **自证**（实现本身可核对）→ 我们：kernel_digest + 变异测试 + 白名单巡检。

这三条正是本项目"可审计的修复"定位的骨架——借鉴后补上了**"记录自身可核对"**这块
（`audit_digest`）与**"每个数字都标可信度"**这块（evidence_levels）。
