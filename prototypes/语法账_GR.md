# GR 设计语法账（B12 §1）

> 对象：`C:/Users/ASUS/Downloads/fb2k_theme/Georgia-ReBORN-extracted/Georgia-ReBORN-master/`
> 口径：每条结论带 **`文件:行`**，或（截图类）带实测数值。**这是解构，不是观后感。**
> 状态：**首轮勘察版**——已拿到"层次/光/深度/色彩系统"四条的一手证据；
> 节奏/圆角与边/信息密度梯度三条**尚缺数值**（见文末"缺口"，下一轮补齐，不猜）。

## 0. 皮肤层在哪（先定位，免得量错文件）

绘制/皮肤层不在 `scripts/`（那里是 eslyric 的第三方库），而在：

| 文件 | 行数 | 职责 |
|---|---|---|
| `profile/georgia-reborn/scripts/Base/gr-color-styles.js` | 1028 | 背景图与面板的**混合/模糊**（"样式"层） |
| `profile/georgia-reborn/scripts/Base/gr-color-chameleon.js` | 1299 | **变色龙**：单色动画器 |
| `profile/georgia-reborn/scripts/Base/gr-color-manager.js` | 2501 | **主色提取与评分**（配置对象 + 令牌名） |
| `profile/georgia-reborn/scripts/Base/gr-color-system.js` | — | 全主题动画（`ChameleonColorSystem`） |
| `profile/georgia-reborn/scripts/Base/gr-color-palette.js` | — | 调色板生成 |
| `profile/georgia-reborn/scripts/Base/gr-color-themes.js` | — | 主题定义 |
| `profile/georgia-reborn/scripts/Base/gr-color.js` | — | 颜色工具 |

## 1. 层次模型（层次数 + 每层怎么处理）

**GR 的层次不是我原先以为的"bg → 面板 → 内容"，而是"bg 图 →（按亮度自适应的）混合层 → 面板 → 内容"。**

证据：`gr-color-styles.js:360` `setStyleBlend(image = grm.ui.albumArt, cache = grm.ui.cachedAlbumArtColors)`
——混合的输入是 **`albumArt`**（专辑封面），不是一张固定的背景图。

⇒ **层次是由"内容"驱动的**：同一套语法，换一张封面，整屏的底色/混合/blur 都跟着变。
**可迁移**：把"背景 = 内容（频谱/封面）"这条学过来，就是"氛围由测量结果驱动"——与我们 G1/G2 的**固定公式**是**相反的思路**（见 §5 决策点）。
**不可迁移**：它是播放器，封面是它的天然输入；我们的"封面"是频谱图，直接垫图会与数据墨打架（B10.5 判据 4 已量过：氛围盖曲线）。

## 2. 光

**GR 的"光"来自两处，而且都是算出来的，不是画上去的**：

1. **背景图本身的亮度**（`gr-color-styles.js:178`）：
   ```js
   const blur = this.getStyleBlendImageBlur(imageLuminance, currentBgLuminance, alpha, imageSaturat…)
   ```
   ——blur 的入参里同时有**图片亮度**与**当前背景亮度**，即"底越暗/越亮，模糊与混合跟着变"。
2. **`gr-color-styles.js:202`** 的日志把这条路径写明了：
   `_formatStyleBlendImage => Progressive blur: ${blur}px → downsca…`
   ——**渐进模糊**（progressive blur），不是单一半径。

⇒ **可迁移（高置信）**："光要跟着底色的实测亮度走"。我们 B10.5 的固定 alpha=0.45 是**常数**，GR 是**函数**——这是它能"有层次但不死"的机制原因。
⇒ **可迁移**：渐进模糊——我们目前只有一个 1px 亮边 + 0.08 淡染（都是常数），没有"随位置变化"的光。

## 3. 深度（面板怎么浮起来）

证据：`gr-color-manager.js:491` 的令牌名列表含 **`'bg', 'shadow', 'detailsBg'`**
——即 GR 的令牌层里**有 `shadow` 与 `detailsBg` 两个独立角色**：面板不是"靠透明度浮"，而是**透明度 + 阴影 + 一个专门的详情底色**三者分工。

⇒ **可迁移**：`detailsBg`（详情区专用底色）这个角色我们没有——我们的面板只有一种 surface，所以"S1 的读数区"和"曲线区"是同一个底，层次差出不来。
⇒ **待补数值**：`shadow` 的具体参数（blur/offset/alpha）本轮未抽到，缺口登记。

## 4. 色彩系统（克制程度与分配）

**GR 的色彩是"从封面里算出一枚主导色，再把整个主题沿着它动画过去"**：

- `gr-color-chameleon.js:16–17`：`A class that is responsible for the standalone chameleon single color animator. For animating the full theme, use {@link ChameleonColorSystem}.`
- `gr-color-manager.js:35`：`The configuration defines the scoring algorithm parameters for ranking dominant colors in imag…`
- `gr-color-manager.js:60–61`：`FREQ_LOG_SCALE_HIGH - The log curve weight for very dominant colors (0.25)` / `FREQ_LOG_SCALE_MED - The log curve weight for moderately dominant colors (0…`
- `gr-color-manager.js:164`：`The object controls how minimum frequency thresholds adapt to color dominance in the image.`

⇒ **它的主导色是"评分排序"出来的**（频率 × 对数曲线权重，阈值还随图像自适应），不是取平均色。
⇒ **可迁移（高置信）**：我们的 `PAL` 是种子色派生的**静态**调色板；GR 的做法是"**从内容里评分选主导色**"。我们的频谱图有 D 色轨 + 判定档色，正好可以喂同一个思路——**但主导色必须仍受判定语义约束**（判定色不许被封面/内容改掉，那是功能色）。
⇒ **不可迁移**：全主题随封面动画（播放器语义）。我们是测量工具，底色随文件跳动会让"两个文件的对比"变得不可比。

## 5. 字体（顺带解掉前两轮的硬伤）⚠ 含许可警告

`profile/georgia-reborn/fonts/Windows/` 里**捆绑着字体文件**（不是靠系统安装）：

```
Helvetica Neue LT Pro 35 Thin / 45 Light / 55 Roman / 65 Medium / 75 Bold  (.otf, 34–41 KB)
Neue Haas Grotesk Disp Pro 35 XLt.otf  (70 KB)
OpenSans-*（Regular/Light/Bold/ExtraBold + Italic）
```

⇒ 我在 B10 差异说明里写的"Helvetica Neue LT Pro 本机没有"**要更正为**：*系统未安装，但 GR 主题随包带了文件*。Qt 可运行时装载，本项目已有该模式（`theme.py` 的 `_MONO_FILES`/`_FIGURE_FILES` + `QFontDatabase.addApplicationFont()` 再查 `families()`）。

⚠ **许可必须分开记账**：Helvetica Neue LT Pro 是**商业字体**。GR 随包分发 ≠ 我们可以分发。所以：
- **GR 的 .otf 只用于本机对照与量测**（量它的字号/行高/字距，对齐语法）；
- **出货走 IBM Plex + 思源黑体（免费）**，同样用 `addApplicationFont` 捆绑。
- 两件事分开写，否则"抄语法"会顺手变成"抄字体授权"。

## 6. 缺口（下一轮补，不猜）

| 维度 | 缺什么 | 下一步怎么取 |
|---|---|---|
| **节奏** | 间距/行高/密度的梯度与疏密比 | 在 `gr-color-*.js` 里没抽到 spacing/padding 数值 → 改查 `profile/georgia-reborn/*.js` 的布局层与 `configs/` |
| **圆角与边** | 圆角半径、边框粗细与颜色 | 同上（`radius/borderWidth` 在皮肤文件里零命中，说明它在布局/控件层） |
| **信息密度梯度** | 截图分区统计 | 需真渲染截图（B12 §2 的公开材料）或运行 GR 截图 |
| **`shadow` 具体参数** | blur/offset/alpha | `gr-color-manager.js` 的 `shadow` 令牌定义处 |

## 7. 已可下的三条高置信结论（先用起来）

1. **光与混合是"底色的函数"**，不是常数（§2 证据）——我们现在的 alpha=0.45、0.08 淡染、1px 亮边**全是常数**，这是"有层次但死"的机制原因。
2. **面板需要 `detailsBg` 这类第二底色**，否则所有面板同底、层次差出不来（§3 证据）。
3. **主导色是评分选出来的，且评分阈值随内容自适应**（§4 证据）——我们的静态 PAL 与它差一个"从内容取色"的机制；但**判定语义色必须排除在这个机制之外**（功能色不许被内容改）。

**冲突登记（B12 §4 要求逐条列出）**：与"克制"纪律的冲突在 §2/§3——"光随底色变化"意味着同一界面在不同位置有不同亮度，与我们"高彩面积 ≤1%"和"一屏一处最强"的口径**需要重新表述**（不是冲突不可解，是口径要跟着变）。**改判据要报数**：本轮不动判据，先把样板屏做出来，附实测面积与对比，由你决定口径怎么改。

---

# 第二轮勘察（补：节奏 / 圆角与边 / 字号）+ §2 材料实测

> 上一轮的缺口表以本节为准。

## 8. 节奏与字号：**几何是参数化的，颜色才是计算化的**

上一轮在颜色层里 `radius` **零命中**、`spacing` 只命中一个调试覆层、`fontSize` 读到
`grSet.menuFontSize_layout`（`gr-buttons.js:1112`）。顺着这条线找到设置层：

**`profile/georgia-reborn/scripts/Base/gr-config-defaults.js`**（几何/透明度的真源，
本轮抽到 200 条相关键），其中最关键的一条：

```js
gr-config-defaults.js:639  menuFontSize_default     = HD_QHD_4K(12, 14)
gr-config-defaults.js:642  lowerBarFontSize_default = HD_QHD_4K(18, 20)
gr-config-defaults.js:644  lowerBarFontSize_compact = HD_QHD_4K(16, 18)
gr-config-defaults.js:649  popupFontSize_artwork    = HD_QHD_4K(14, 16)
```

**两条语法结论**：

1. **字号 = f(分辨率档, 布局模式)**，不是固定 px：`HD_QHD_4K(a, b)` 按屏幕档位取值（HD 12 → QHD 14）。我们的 `Type` 是**一档固定 px**（10/11/12/14…）——在 2K/4K 屏上会显得小，这是我此前所有界面的**结构性**问题（不是审美问题）。
2. **每个文本元素有 3 套字号：`_default` / `_artwork` / `_compact`**，而且**当视觉区（封面）变大时文字让位**（`lowerBarFontSize`: 18 → artwork 16）。这是一条可以直接用的层级规则：**主角变大，配角缩小**——比"一屏 ≤5 种字号"这种计数式纪律更接近真实意图。

⇒ **可迁移（高置信）**：字号按"分辨率档 × 布局模式"给；**样板屏按 `default` 与 `artwork` 两套**做——恰好对上 S1（面板为主，default）与 S2（曲线为主，artwork）。
⇒ **冲突登记**：我们"字号层级 ≤5 种"的纪律与它**不冲突但口径要换**——GR 不限种类，它保证"同屏内的比例关系"。建议样板屏按 GR 口径走，并把我们的纪律改写成"同一屏内字号比例必须成组（1:1.15:1.5…）"。

## 9. §2 材料：RX 拿到了、**Roon 的没拿到（如实记）**

网络实测：`rx.izotope.com` **SSL 挂**（`UNEXPECTED_EOF_WHILE_READING`）；
`www.izotope.com` 200 / `roon.app` 200 ⇒ **主站可达**，§2 可行。已下到
`out/b12_ref/`（口径与 GR 的 bg.jpg 实测完全一致，可横向比较）：

| 文件 | 尺寸 | C≥0.05 | C>0.10 | L 中位 | L 范围 | 色相簇(°)→占比 |
|---|---|---|---|---|---|---|
| `izotope_rx_0.png`（官方产品图） | 1920×1080 | **41.1%** | **28.15%** | **0.315** | 0.18→0.67 | **30:79.2**, 240:18.3 |
| `izotope_rx_1.jpg`（YouTube 缩略图） | 1280×720 | 35.8% | 13.72% | 0.302 | 0.14→0.97 | 30:45.6, 240:20.6, 0:15.5 |
| `roon_0.png` ⚠ 非 UI | 400×200 | 38.3% | 30.39% | 0.281 | 0.00→0.94 | 330:32.0, 270:28.4, 300:17.4 |
| `roon_1.png` ⚠ 非 UI | 400×219 | 4.5% | 0.95% | 0.247 | 0.00→0.89 | 30:54.8, 60:32.7 |

**RX 的可迁移结论**：**深底（L 中位 0.315）+ 一枚暖强调（30° 橙）+ 冷色数据（240°）**。
30° 橙占高彩像素的 79.2% ——它的强调色**只有一枚**，克制程度与我们一致；
C>0.10 的 28% 主要来自**数据本身**（频谱/波形），不是 UI 外壳。

⚠ **两条必须如实说明的边界**：
1. `izotope_rx_0.png` 是**产品营销图**（含宣传背景与排版元素），**不是纯 UI 截图**——上面的数字只能说明"官方对外传播的视觉语言"，**不能直接当 UI 令牌**。
2. **Roon 的两张（400px 宽）不是 UI 截图**，是页面上的小图/logo 卡。**我不会拿 logo 去冒充"Roon 的排版语法"**——那正是"未标定先定阈值"的另一种形态。Roon 需要重新取材料（帮助文档配图或产品页的懒加载大图）。

⇒ §2 当前状态：**RX 有可用材料（1 张，且已标注其性质）**；**Roon 待重新取样**；**Ableton 零成本**（本机 `rengwu/ableton-live-ds` tokens，下一轮并入同表）。

## 10. 更新后的缺口（下一轮）

| 维度 | 现状 | 下一步 |
|---|---|---|
| 节奏 | ✅ 部分（字号档位与三套模式已到手） | `gr-config-defaults.js` 里的 spacing/padding/布局宽高（200 条里还有大量没抽） |
| 圆角与边 | ❌ 仍零命中 | 大概率在 `gr-button`/`gr-panel` 的**绘制**代码里（用 `roundRect` 之类），换关键词搜 |
| 信息密度梯度 | ❌ | 需真渲染截图（GR 运行截图）或官方材料分区统计 |
| Roon 材料 | ❌ 取到的是缩略图 | 换取样路径（帮助文档配图 / og:image 之外的懒加载大图） |
| Ableton tokens | ⏳ 本机已有 | 下一轮并入同表（零成本） |

---

# 第三轮（总纲任务 1/2/3）

## 11. 圆角与边（任务 1）✅ 取到

上一轮 `radius` 零命中的原因找到了：**GR 的函数名是 `FillRoundRect`，不是 `roundRect`**
（我上一轮的关键词在大小写上吃亏）。

```js
profile/georgia-reborn/scripts/Base/gr-main-components.js:1432   this.arc1 = 5;
profile/georgia-reborn/scripts/Base/gr-main-components.js:1434   this.arc2 = 4;
profile/georgia-reborn/scripts/Base/gr-main-components.js:1460   gr.FillRoundRect(this.j.x - this.j.w/2,
                                                                 this.j.y, this.j.w, this.j.h, this.arc1, this.arc2)
```

**两条结论**：

1. **圆角半径 = 4–5px**，而且是 **`arc1`/`arc2` 两个值**（不是单一 radius）——即它区分了横向/纵向弧（或内外弧），这在我们的 `Radius` 令牌里没有对应物（我们只有一个 `lg`/`xl`）。
2. **我们的圆角是 12/16px**——**大了 3–4 倍**。这是"廉价感/网页感"的一个**可测来源**：GR 是仪器（近乎方角），我们是 web 卡片。

⇒ **可迁移（高置信）**：样板屏把圆角降到 **4–5px** 量级。
⇒ **冲突登记**：与"圆角是层级语言（卡片越大圆角越大）"的通行做法冲突。GR 的答案是"**层级靠明度与边框，不靠圆角大小**"——这条要在样板屏上验证。

## 12. 间距与阴影（任务 1 的间距补全）✅ 部分

`gr-config-defaults.js` 里取到（都是**小数值**）：

```js
:812   transportButtonSpacing_default = 5
:1712  lyricsLineSpacing      = 24     :1713  lyricsSentenceSpacing = 14
:1847  lyricsLineSpacing      = 40     :1848  lyricsSentenceSpacing = 30
:2604  grCol_shadow           = '000000'
```

**三条结论**：

1. **间隔 5**（不是 4 的倍数）——我们的 `Space` 纪律"**全部 4 的倍数**"在 GR 这里**不成立**。它不是无序，而是**以 5 为基准的紧凑刻度**（5 / 14 / 24 / 30 / 40）。
2. **行距成对出现**：`lyricsLineSpacing` 24 与 `lyricsSentenceSpacing` 14（另一套 40/30）——**行内 vs 句间是两个令牌**，比值约 1.7 与 1.33。我们只有单一 `Leading` 倍率，没有"层级之间的间距比"这个维度。
3. **`grCol_shadow = '000000'`**：GR 的阴影色是**纯黑**。而我们 B2 的 `SHADOW_TIERS` 特意用了偏冷近黑（`rgba(2,6,23,…)`）并写了一段理由（"纯黑压在冷灰底上会发脏"）。两者矛盾 ⇒ **决策点**，见 §14。

⇒ **冲突登记（第二条）**：我们的"间距全为 4 的倍数"（有判据 `test_ui_grid`）与 GR 的 5/14/24/30/40 直接冲突。**改口径要报数**：样板屏会同时给出"按 4 网格"与"按 GR 刻度"两版实测面积/对比，交你定。

## 13. Ableton tokens（任务 2）✅ 取到，但来源要如实说明

⚠ **本机没有 `rengwu/ableton-live-ds` 仓库**（全局搜 `C:\Users\ASUS` 下的 Documents/Downloads/Desktop/WorkBuddy/CodeBuddy 只找到两个派生 HTML）。
所以任务 2 的诚实形态是：**从这两份派生 HTML 抽**，并注明**不是上游仓库**：

`C:/Users/ASUS/WorkBuddy AI/2026-10-13-16-14-09/专业工具语言_按Ableton设计系统.html`（11 KB，16 个 CSS 变量）：

| 组 | 值 | 说明 |
|---|---|---|
| 中性 | `--chrome #3f3f3f` `--work #929292` `--dlg #dadada` `--mod0 #1d1d1d` `--mod1 #2f333c` | 五个中性档 |
| 文字 | `--tx #dadada` `--tx2 #929292` `--tx3 #6e6e6e` | **三档文字**（对应我们的 text/text_dim/text_faint ✅ 共性） |
| 功能色 | `--amber #f4bc41` `--cyan #5bc7d8` `--mint #72f6aa` `--red #e0584c` | 四枚功能色，其余近无彩 |
| 字号 | `10 / 11 / 12 / 15` | **四档**（比我们的 8 档少一半） |
| 间距 | `4 / 5 / 6 / 24` | 小刻度 + 一个大刻度 |
| 字体 | `--ui: "IBM Plex Sans","Noto Sans SC"` ／ `--mono: "IBM Plex Mono","Consolas"` | **与总纲 1.4 的发货字体栈完全一致** |

色相簇实测：**60°→10 个(C 0.01，即中性)**、240°→3 个(C 0.06)、其余零星 —— ⇒ **它的 UI 近无彩，彩色只给功能色**。

`vivid档_Ableton排版_色彩推移.html`（13 KB）则是**同一套布局的玫瑰单色梯**：
`--bg #af355e` → `--surface #9c2a44` → `--surface-hi #89202b` → `--raised #741a15` → `--plot #580e00`，边框 `#bb6c71`。

⇒ **可迁移（高置信）**：
1. **字体栈这三份材料是一致的**（HTML 里写着 IBM Plex + Noto Sans SC）⇒ 任务 5 的字体决策**有本机先例**，不是新拍的；
2. **字号四档、间距 4/5/6/24**——与我们"8 档字号 + 4 网格"相比，它是**更少档位 + 更紧凑刻度**；
3. **玫瑰单色梯是"同一布局换一套色"的既有做法**（vivid 档）——正好是我们要的"样板屏 × 双态"的先例。

## 14. 三个决策点（合并账的输入，任务 4 用）

| # | 矛盾 | 双方证据 | 提案（报数后定） |
|---|---|---|---|
| D1 | **圆角** | GR 4–5px（近乎方角）⇄ 我们 12/16px | 样板屏取 5px；层级改由明度/边框承担 |
| D2 | **间距刻度** | GR 5/14/24/30/40（非 4 倍数）⇄ 我们全 4 的倍数（有判据） | 样板屏同时出两版实测，由你定口径 |
| D3 | **阴影色** | GR `000000` 纯黑 ⇄ 我们 `rgba(2,6,23,·)` 偏冷（B2 有理由） | 样板屏两版对比，看"脏不脏" |

## 15. Roon（任务 3）❌ **本轮缺材料**（两次尝试，如实记）

| 尝试 | 结果 |
|---|---|
| `roon.app` 主站 `<img>` | 只取到 400px 的 logo 卡 ⇒ **按红线②作废** |
| `help.roonlabs.com/portal/en/kb/roon` | 候选图 **0 个**（内容 JS 渲染，静态 HTML 里没有 `<img>`） |
| `roon.app/en/features` | **HTTP 404** |

⇒ 按总纲的授权（"若多次取不到真 UI，记「Roon 本轮缺材料」，仍推进 §3/§4"），
**Roon 记缺材料**，不拿 logo/缩略图冒充排版语法（那正是红线②要防的）。
合并账将用 **GR + RX + Ableton** 三家——已够提炼共性与决策点。

## 16. 任务 1–3 结论汇总（给合并账的原始素材）

- **共性候选**（≥2 家）：深/中明度底 + 少而饱和的功能色（GR 封面取色 / RX 30° 橙 / Ableton 四色）；三档文字（Ableton `tx/tx2/tx3` ↔ 我们 text/text_dim/text_faint）；小圆角（GR 4–5px）。
- **单家独有**：GR 的封面驱动层次与渐进模糊；RX 的"数据即色彩"（C>0.10 的 28% 来自频谱）；Ableton 的四档字号与近无彩 UI。
- **矛盾**：D1 圆角 / D2 间距刻度 / D3 阴影色（上表）。
