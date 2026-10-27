# B14 收口交付报告（PM 接手，2026-10-14）

> 用户："干活那个 Agent1 太墨迹了，剩下交给你干完。" 本报告如实记：**定义/判据层已全落 + 证据矩阵已出 + 探针已跑**；
> **`src/` 的氛围/深度接线（移植）是最后一步，本轮未落地**——不粉饰。

## 1. 已落地的（全部有 commit + 判据 + 全量绿）
| commit | 内容 |
|---|---|
| `4e41dac` | 字体落地 + 回归红①（fonts 契约统一真源 / 三族断言 / QSS 不写 font-family） |
| `8604996` | 回归红②③（thin space 按 Plex/Noto 换字符 U+00A0；自违例消息） |
| `de924ed` | 回归红④（曲线底换成真实面板合成底；彩度判据按 CS1 量氛围层） |
| `39ed047` | 3A 分辨率字号 `typescale.py`（三档 / 比例跨档不变 / BASE=Type / tabular 声明） |
| `8fe3f6e` | 3B 半径（nano/tiny/instrument 档；裸半径清零；判据零容忍） |
| `05104e4` | 3B 边框（面板双线 + 顺序判据 + 边框对比判据 + DPR 发丝线函数） |
| `5ed0e61` | 3C 图标（注册表补 7 个；"无静默空白"判据三条） |
| `b9f7625` | 3C 接线（多态 QIcon 三态着色） |
| `2d155a2` | 3D 交互（focus ring 真缺陷修复 + 六态判据 + 无 box-shadow 判据） |

**全量**：458 passed（3D 后）、默认 `ruff check` 全树绿。四条回归红全消。

## 2. 证据矩阵（本轮 PM 出，`shots/b13/`）
- **三档字号梯度**（同版面，档位只改字号）：`shell_HD_light_default` / `shell_QHD_light_default` / `shell_4K_light_default`（+ 各自 `_hover`）——HD vs 4K 像素差异 **23%**（字号梯度真实）。
- **双主题**：`shell_HD_dark_default/_hover`——light 面板中位 **222/223**、**dark 面板中位 41**（暗色生效）。
- **交互态**：每档 `_default`（nav0 active）/ `_hover`（nav1 active + nav2 hover + 按钮 hover）。
- **图标矩阵**：`icon_matrix.png`（27 个图标一屏）。
- 渲染：系统 python + offscreen + 字体捆绑断言；`b13_front.py` 已支持 `B13_THEME` / `B13_TIER`。

## 3. 探针（`packaging/`）
| 探针 | 结果 |
|---|---|
| `mem_probe --strict` | ✅ 通过（图元不累积、缓存有界、清理回落；净增 +63.3 MB 有界） |
| `perf_probe --strict` | ✅ 通过（交互在帧预算内：P8 拖拽 337ms / P10 频谱图 280ms） |
| `palette_report --strict` | ✅ 0 不达标（生成配色 + 面积配额） |
| `hue_family_report` | ⚠ 见 §4（**不是 bug，是判据口径要按 CS1 分层**） |

## 4. ⚠ 新发现：hue_family 判据同样"量错了层"（CS1，同 ④ 型）
`hue_family_report shots/b13/shell_HD_light_default.png`：**单一色相族 ✗ 37.1%**（0°:37.1 / 330°:24.5 / 300°:24.4 / 270°:12.8）。
根因：**G1 氛围本身跨 270°→30° 多色相**，把"单一族 ≥85%"稀释。那条 ≥85% 是 **chrome 中性底时代**的口径（B7 的"高彩几乎全在一块判定色上"）。
⇒ 与 ④/彩度判据**同型**：应按 **CS1 分层**——**量 chrome/数据的高彩色相族，氛围层单列**。**登记为判据口径待改项**（不静默改）。

## 5. 未落地 = 最后一步（`src/` 氛围/深度接线，如实记）
定义/判据/证据都就位，但**把 `b13_front.py` 的语法搬进真程序**这一步未做：
- **氛围层**：`window.py` 内容外面包一层氛围 `QImage` 底（G1 默认）+ 面板半透 veil（`veil_alpha` 光随底色）。
- **深度三层**：面板 `QGraphicsDropShadowEffect` + `detailsBg` 第二底色。
- **接线缺口（累计表）**：① 分辨率 tier→令牌/QSS + `changeEvent`；③ tabular 接真件；④ 发丝线逐件接线；⑥ 图标分配核对（真程序 SideBar 已带图标，待核）；⑦（新）hue_family 判据 CS1 分层。
- **风险提示**：真程序默认 `T.DARK`（`window.py:199`），而判过的样板屏是 **light** ⇒ 移植要先定"亮色默认"（B9.4）还是保留暗色默认。

## 6. 下一步（收口的最后一步，一句话）
把氛围 + 深度 + 分辨率/发丝线/tabular 接线落进 `window.py`/`widgets.py`，跑全量 + 四探针 + 出完成品图（真数据），闭缺口 ①③④⑥⑦。

## 7. 攻坚结果（PM 续，2026-10-14）：氛围层进真程序且可见

- 新增 `src/audiocheck/ui/atmosphere.py`（G1/G2 公式 + veil + `AtmosphereHost`；颜色经 `oklch_to_srgb`，与 `_kit` 同源）——**隔离渲染验证通过**（G1 梯度：角 (63,72,111)→心 (197,116,190)）。
- `window.py`：内容包一层 `AtmosphereHost`；内容内缩 **16px** ⇒ **G1 极光露在边衬**（D0"氛围退到边衬"）。实测边衬中位 **(125,146,208)=G1**、整图 uniq 1849→4430。
- 提交 `b6e3904`（结构）→ `fa13534`（可见 18px）→ `018c919`（16px）。**全量绿 + 默认 ruff 全树绿**。

**踩的两个坑（已修 + 记账）**：
1. **亮色默认没生效**：只改 `self.theme` 会被 `_restore()` 的持久化配置盖掉，且 build 时 sidebar 用 LIGHT、restore 后 theme=DARK ⇒ **不一致**（`test_sidebar_nav_icons_use_the_deco_ramp` 红）。⇒ 亮色默认必须走 **config/_restore 路径**（已回退，未做）。
2. **18px 撞 4pt 栅格**：`test_contents_margins_on_grid` 要求边距在 4pt 栅格 ⇒ 改 16px。

**仍未落地（收口最后一步）**：氛围**铺满**（现只在 16px 边衬；铺满=内容容器半透 = 面板 veil）；分辨率 tier 接线（`changeEvent`）；tabular 接真件；发丝线逐件接线；图标分配核对；hue_family 判据 CS1 分层；亮色默认（config 路径）。

**结论**：氛围层**已进真程序且可见**，全量绿；"氛围铺满 + 其余接线 + 亮色默认"是收口最后一步。

## 8. 氛围铺满续做（PM，2026-10-14）

- 第一步已提交（`4b1e097`）：全局 `QWidget` / `#SideBar` / `#Card` 底色 → `rgba(0.86)`，侧栏透出 G1（**34,35,51**，之前 13,14,19）。
- 续做尝试（让中心区也铺满）：把全局 `QWidget` 改**透明**、只给语义容器 rgba ⇒ **没修好中心区（仍 20,20,26）+ 破了 `test_ui_material::test_burst_does_not_leak`** ⇒ **回退**（全量绿已恢复）。
- **中心区的真堵点**：内容是多层嵌套（stack → page → empty-state …），每层 `rgba(0.86)` 叠起来 ≈ `0.86^N` ≈ 不透明 ⇒ 氛围透不进。**一刀切全局透明是错解**——要铺满得**逐层把内容容器设透明**（保留侧栏/卡片的 rgba）。
- **下一步（铺满收口）**：给 `stack` / 各 page / `empty` 等中心容器设透明，并查清 Toast 测试与"全局透明"的耦合。

## 9. 下一轮范围（用户定）：氛围铺满 → 组件/UI 加强 → 字的排版
- 字的排版：3A 的 `typescale`（分辨率字号）**还没接线**，tabular（Plex Mono 列对齐）也**只到声明**——正好并入"字的排版"这一轴。
