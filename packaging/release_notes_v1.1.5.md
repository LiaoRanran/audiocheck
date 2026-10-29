# v1.1.5 — 修复能力进了发布包；审计终于能被核对

## 你手上的 exe 现在能做三件事

v1.1.4 把「带参数走 CLI」补上了，但只接了**体检**。实测：

```
audiocheck.exe 源.wav 目标.wav --gain -3 --at 0-0.5
→ audiocheck: error: unrecognized arguments: --gain -3 --at 0-0.5
```

去削波 / 去咔哒 / 去嗡 / 去噪 / 批处理 / 审计核对**全部不可达**——而"可审计的修复"
正是这个项目区别于普通音频工具的地方。

现在同一个 exe 三层分派：

| 调用 | 行为 |
|---|---|
| 双击 / 无参数 / `--seed` 等 | 开桌面界面 |
| `--gain` / `--declip` / `--batch` / `--verify-audit` … | **编辑 CLI** |
| 其余参数 | 体检 CLI |

## 审计能被核对了（这是"可审计"的最后一公里）

审计记录里早就有 `audit_digest` / `kernel_digest` / `source_sha256`，
但**没有任何命令能核对它们**——对方给你一份审计 JSON，你只能自己写脚本重算。

```bash
audiocheck.exe --verify-audit 对方给的审计.json
audiocheck.exe --verify-audit 一批审计/        # 目录递归
```

```
✓  audit_digest 一致
    记录 5258ddff63e8031c46e3a570b7bb2db...
    内核 **同一实现**（记录 8b64d9dd717f… ｜ 本机 8b64d9dd717f…）
    输入内容 sha256 0ad0443d25326726…
```

三个摘要各证明一件事：**同一份修复**（`audit_digest`，剔除路径/时刻/产物哈希）、
**同一份实现**（`kernel_digest`）、**同一份输入内容**（`source_sha256`，跳过 PEAK）。
退出码 0/1，可直接进 CI。**不渲染任何音频。**

### 上线即暴露三处口径缺陷（本版修掉）

1. **自指字段**：渲染时先算摘要再写入记录，复算时 `audit_digest` 已经在 dict 里
   ⇒ 复算等于"摘要 + 摘要"再哈希，**永远对不上**。连原样未改的记录都核不过。
2. **计算顺序**：摘要算完之后还有一大段按 domain 追加字段
   （`algorithm` / `n_fft` / `roundtrip_floor_*`）⇒ 这些字段没进摘要。
3. **链式路径**：`_render_chained` 拿最后一段的审计当 `final`，再并入
   `ops` / `stages` / `chained` / `reordered_from` ⇒ 同样漏算。

这三条在核对器存在之前**完全不可见**——"能生成摘要"和"摘要能被复算"是两件事，
而核对器本身就是它们缺失的那个判据。

> ⚠ **1.1.5 之前产出的审计记录必然核不过**（口径变了）。核对器会显式提示
> "该记录由 audiocheck 1.1.4 产出……这不代表文件被篡改"，而不是让你以为文件被动过。

## 打包版批处理并行：此前三项全崩

Windows 的 `multiprocessing` 用 spawn，子进程会**重新执行 exe**；PyInstaller
引导程序把没走 `freeze_support()` 的 worker 杀掉：

```
✗ [#0] 失败：worker 崩溃：A process in the process pool was terminated abruptly
```

源码运行完全正常，**只在发布版复现**。两层修复：入口调
`multiprocessing.freeze_support()`；frozen 环境下批处理降级为**线程池**
（渲染是 numpy/scipy，大量释放 GIL，并行度真实）。

## 两处静默失败

- **`--batch` 完全忽略命令行 `--overwrite`**：只读作业项里的 `"overwrite"`，
  用户加 `--overwrite` 重跑批处理**完全无效**（全部报"目标已存在"）。
- **选区越界时静默成功**：渲染层把越界区间夹紧到空集（这是正确行为），
  但退出码 0、报告一片正常、产物与源逐位相同 ⇒ 用户以为修了，实际一个样本没动。
  现在显式警告：

```
⚠ **选区没有命中任何样本（no-op）**：目标文件与源逐位相同。
   检查 --at/--band/--poly 是否落在素材范围内（本文件时长 2.000 s）
```

## 量具：`edit_cli` 变异收口 0.658 → 0.870

把它注册为变异目标后暴露 40 个存活点，六轮收口（新增 63 项判据）：

| 轮次 | 分数 | 关键动作 |
|---|---|---|
| 初测 | 0.658 | — |
| 1 | 0.718 | 报告数字与审计逐项对齐 |
| 2 | 0.752 | 互斥校验（去嗡/去噪各 8–12 个参数逐个）+ 退出码 2/4/5 三分 |
| 3 | 0.769 | 百分比判据强化（必须 ≠150×fraction） |
| 4 | 0.809 | declip 的 ambiguity / ceiling_source、dehum 的 f0_source 分支 |
| 5 | 0.843 | 改动面表达式**双侧**判据（稀疏不报 + 密集必报） |
| 6 | **0.870** | `_print_chain` 纯函数判据（合成 dict 喂 None/缺键/多声道） |

合并变异（`render.py` + `edit_cli.py`，317 体）：**0.927**。

**踩过的坑（第三次同类）**：declip 报告测试最初用 `np.clip(x*1.7, -0.7, 0.7)`
造"饱和"素材，实测 `fraction = 0.0` —— **0 乘任何数都是 0**，`100.0→150.0`
变异完全抓不住。改成硬削波 + ceiling 严格小于峰值后 `fraction≈0.89`，
判据才有分辨力。**判据的素材必须先验证"它真能让判据有分辨力"。**

## 其它

- `--verify-audit` 支持目录递归；空目录 ⇒ 退出码 2（不静默成功）。
- `audiocheck-edit --version` / `--doctor` 补上。
- `--edit a.wav b.wav` 直接载入（此前位置参数被静默忽略）。
- 文档全面对齐：README（三种能力分派表 + 退出码约定 + 核对 API）、
  CHANGELOG（补齐 1.1.1–1.1.5）、`docs/scripting.md`（三个摘要口径表 + no-op 章节）、
  `examples/verify_audit.py`（追加 CLI 核对演示）。
- `packaging/realign_whitelist.py`：白名单行号对齐器常驻（单候选自动改，
  多候选报人工——宁可报错不许猜）。

## 验证

- 全量 **1556 项测试通过**；变异白名单 112 条对齐；文档数字对账 5/5。
- 打包 254.7 MB，冒烟 **8 项全过**（含编辑 CLI 与核对器）。
- 打包版端到端 **8/8**：修复+审计+报告、核对 PASS/FAIL、批处理并行、
  目录核对、越界 no-op、体检 JSON、失败退出码。

---

安装：`pip install audiocheck`，或下载 `audiocheck-1.1.5-win64.zip` 解压即用。

SHA256: `a45c96881d73ec6f6dfa4437e11d437d157f0387c678e92e7e9b12df1c039102`
