# v1.1.6 — 一条命令跑多道修复；核对结果能给 CI 读

## 复合算子：`--op`

从 v1.0 起，`docs/editor_roadmap.md` 上挂着一条「CLI 仍只接受一个算子（复合走
Python 项目记录）」。这版做掉了：

```bash
audiocheck-edit-cli in.wav out.wav \
    --op "declick@0-2:threshold=5" \
    --op "declip@0-2:ceiling=0.34,bandwidth=21000" \
    --op "dehum@0-2:f0=50,q=8"
```

`--op` 可重复，**出现顺序就是操作栈顺序**。这相当于把"先做去咔哒、再去削波、
最后去嗡"这套配方从"写一个 JSON 再喂进去"变成一行命令。

支持全部七个算子：

| kind | 必填 | 可选 |
|---|---|---|
| `gain` | `at`、`gain=dB` | — |
| `band_gain` | `at`、`band=f0-f1`、`gain=dB` | — |
| `poly_gain` | `points=t,f;t,f;...`（≥3 对） | `gain=dB`（默认 −6） |
| `declip` | `at`、`bandwidth=Hz` | `ceiling`、`iterations` |
| `declick` | `at` | `threshold`、`max_run`（0=关） |
| `dehum` | `at`、`f0=Hz` | `q`、`harmonics=1,2,3` |
| `denoise` | `at`、`noise=t0-t1` | `reduction_db`、`no_floor_track=1` |

### 一个容易踩的语法坑

`harmonics=1,2,3` 里的**逗号是取值**，不是参数分隔符。否则谐波列表会被切成
三个伪参数，报一堆"不认识的键"。解析规则是"取值吃到下一个 `标识符=` 之前"，
所以 `f0=50,harmonics=1,2,3,q=8` 能正确切成三项。

### 顺序语义（不是参数顺序）

多算子走链式渲染，按 `render.PHASE` **规范排序**：脉冲类先、稳态嗡最后。
被重排时审计记 `reordered_from`，原始顺序也能查。这是既有行为
（`--project`/`--chain` 一直如此），`--op` 不改变它。

### 校验

- 与单算子开关（`--gain`/`--declip`/…）、`--at`/`--band`/`--poly`、
  `--chain`/`--project` **全部互斥** —— 混用会让"按哪个跑"变成猜测。
- 参数错误一律**在渲染之前**报出（退出码 2），不会留下半成品产物。
- `--op` + `--save-project` 产出的记录能被 `--project` 复算（判据比对产物内容）。

## 核对结果给 CI 读：`--verify-json`

`--verify-audit` 之前只有人读文本，CI 只能 grep 中文。现在可以另存结构化报告：

```bash
audiocheck-edit-cli --verify-audit 审计目录/ --verify-json 报告.json
```

```json
{"ok": false, "n_total": 3, "n_bad": 1,
 "records": [{"path": "...", "ok": true,
              "audit_digest": {"claimed": "...", "recomputed": "...", "match": true},
              "kernel_digest": {"recorded": "...", "local": "...",
                                "same_implementation": true},
              "source_sha256": "...", "tool_version": "1.1.6"}]}
```

脚本直接读 `ok` / `n_bad` / 逐条的 `match` 与 `same_implementation`。
读不了的文件也进报告（`ok:false` + `reason`），不整体崩。

## 量具：`edit_cli` 变异 0.870 → 0.962

新增代码带来 143 个变异体（原 115），三轮收口：

| 轮次 | 分数 | 补了什么 |
|---|---|---|
| 新增后初测 | 0.825 | — |
| 1 | 0.925 | `--op` 的 at 缺失 / max_run=0 / poly 默认增益 / 退出码 5-4-2 三分 |
| 2 | 0.955 | 计数守卫的平衡组合（给 2 个、4 个算子时减法得 0） |
| 3 | **0.962** | `n_single` **每一个 `+` 号**逐位置覆盖 |

### 这轮学到的一条方法论

`(1 if a) + (1 if b) + (1 if c) + (1 if d) + (1 if e)` 有 4 个 `+`。把任一换成
`-` 时，**只有当该位置能让总和翻成 0 的组合**才可观测。第一版测试只用
`declip + declick`（能杀第 1、2 个 `+`），漏了需要 denoise 参与的第 3、4 个
—— 补了逐位置对照表后才全部杀死。

**"给了组合"不等于"覆盖了每个变异位置"**。这跟之前那次
`fraction = 0.0` 时 `100.0→150.0` 抓不住是同一类教训：判据的**输入**必须先
验证它真能让判据产生分辨力。

## 验证

- 全量 **1585 项测试通过**（+13）；变异白名单 115 条对齐；文档数字对账 5/5。
- 打包 254.7 MB，冒烟 8 项全过。
- 打包版新功能端到端 **5/5**：复合算子 3 项、审计 `chained=True`、
  `--verify-json` 生成与结构、`harmonics` 逗号解析。

---

安装：`pip install audiocheck`，或下载 `audiocheck-1.1.6-win64.zip` 解压即用
（同一个 exe 覆盖界面 / 体检 CLI / 修复 CLI 三种能力）。

SHA256: `58abdf6cd65a317464f6ff59daacf7b1fa18c2b6eec961365a48b0c6498b2b08`
