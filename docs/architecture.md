# 架构与扩展指南

## 分层

```
audiocheck/
├── config.py            阈值与频段定义（所有"魔法数字"的唯一出处）
├── cli.py               命令行入口
├── summarize.py         批量结果 → 曲库特性报告
├── core/                分析引擎（纯计算，不依赖 Qt）
│   ├── registry.py      分析器注册表  ← 扩展点
│   ├── context.py       共享上下文（缓存频谱/解码片段、承载累积结果）
│   ├── metrics.py       指标元信息（标签/单位/小数位/分组）
│   ├── dsp.py           BS.1770 等标准原语
│   ├── views.py         频谱图/瀑布图的数据生产（给人看的图，不是指标）
│   ├── engine.py        调度与结果组装
│   └── analyzers/       各分析器，**自动发现**
└── ui/                  Qt 界面（可选依赖，懒加载）
    ├── theme.py         配色字典 → QSS + 手绘矢量图标
    ├── widgets.py       环形仪表、键值表
    ├── charts.py        各视图画布 + 表格绘制委托
    └── window.py        主窗口
```

依赖方向严格单向：`ui → core → config`。`import audiocheck` 不会拖入 Qt。

---

## 加一个新指标（3 分钟）

在 `src/audiocheck/core/analyzers/` 下新建一个文件即可，**不需要改任何其它文件**：

```python
# src/audiocheck/core/analyzers/polarity.py
from ..registry import Analyzer, register
import numpy as np

@register
class PolarityAnalyzer(Analyzer):
    key = "polarity"
    label = "极性 / 声道相位"
    order = 45                     # 越大越晚跑
    requires = ("stereo",)         # 声明依赖，引擎按拓扑序调度

    def run(self, ctx) -> dict:
        seg, fs = ctx.segment(seconds=30)
        if seg is None or ctx.channels < 2:
            return {}
        l, r = seg[:, 0].astype(float), seg[:, 1].astype(float)
        inverted = float(np.corrcoef(l, r)[0, 1]) < -0.5
        # 声明显示元信息 → 界面指标卡与导出自动带上
        ctx.metric("polarity_inverted", "疑似极性反接", group="立体声", primary=True)
        return {"polarity_inverted": bool(inverted)}
```

生效范围：

| 位置 | 效果 |
| --- | --- |
| `analyze()` | 结果里多出 `polarity_inverted` |
| `audiocheck --list-analyzers` | 列出新分析器 |
| GUI 总览页 | 指标卡自动出现（因为 `primary=True`） |
| GUI 元数据页 | 「全部测量指标」自动分节显示 |
| HTML/CSV/JSON 导出 | 自动包含 |
| `python -m audiocheck.summarize` | 可用新字段做汇总 |

约定：
- `run()` 必须返回 **可 JSON 序列化的原生类型**（numpy 标量请 `float()/int()` 包一层）。
- 抛异常不会中断整体分析，错误会记进 `result["errors"]`。
- 文件名以下划线开头会被跳过（放私有工具）。

---

## 加一个新视图

1. 在 `ui/charts.py` 继承 `BaseCanvas`，实现 `redraw()`（`self.p` 是当前主题配色）。
2. 在 `ui/window.py` 的 `_build_right()` 里实例化，并加进那个 `for cv, name, tip in (...)` 元组。
3. 在 `apply_theme()` 的画布循环里加上它。

数据源建议放在 `core/views.py`。

---

## UI 层的资源边界（改动前必读）

界面层最贵的资源不是 CPU，而是**被一直引用着的数组**：一份频谱图数据 `(t, f, S)`
约 0.7–3 MB、瀑布图约 0.2 MB，而 matplotlib 的图元会把它们连带引用住。
这些对象不报错、不进日志，只表现为「开着它翻半小时文件，任务管理器里 1 GB」。
四条硬边界，改动画布 / 列表 / 线程前先读：

| 边界 | 位置 | 规则 |
| --- | --- | --- |
| 图元 | `charts.SpectrogramCanvas._colorbar` | 色条**只建一次**，之后 `update_normal(mesh)`。`fig.colorbar()` 每调一次都会往 `fig.axes` 里加一个 Axes，而 `ax.clear()` 带不走它；旧色条握着 mappable → 握着 S 矩阵。实测每次重画 **+17.9 MB** 且不可回收 |
| 缓存 | `window.MainWindow.CACHE_MAX_BYTES` | 视图数据**按字节**淘汰（FIFO，至少留 1 份）。按条数限制是错的：单份大小取决于 `nperseg` / 时长 / `max_cols` |
| 线程 | `window.MainWindow._view_workers` | 按 `(路径, 视图)` 登记的**字典**：既持有引用防 GC（QThread 被回收而线程仍在跑会让进程硬崩），又天然去重；线程 `finished` 时摘掉 |
| 生成时机 | `window.MainWindow._load_visible_view` | 只为**当前可见页**生成数据。频谱图与瀑布图各要读盘 + 12 s 的 4096 点 STFT，选一次文件算两份是白算 |

另外两条同源规则：

- 以组件为父对象的 `QPropertyAnimation` 必须 `start(DeleteWhenStopped)`，
  否则动画（以及它的 target）永不销毁 —— `widgets.ToastHost.notify` 踩过。
- 用 `deleteLater()` 回收对象时，测试 / 探针里要手动
  `sendPostedEvents(None, QEvent.DeferredDelete)`：`processEvents()` 不派发延迟删除事件，
  漏派发会让人看到「每点一次涨 0.6 MB」这种**假泄漏**（真实运行时 `exec()` 会自己派发）。

量化工具：`python packaging/mem_probe.py [--strict] [--trace]`，场景与判据见该文件头部。
不变量回归：`tests/test_ui_memory.py`（断言用对象计数，不用进程内存）。

---

## 色彩系统（`ui/palette.py`）

配色不是"挑好看的颜色"，而是一组**可计算的约束**：`ui/palette.py` 从一个种子色
生成整套语义色，生成时就跑 WCAG 对比度判据，**不达标直接抛异常**。

```
种子色 ──OKLCH──▶ 只取色相 ┬─▶ 中性阶（固定 L 阶梯 + 极低 chroma）
                          ├─▶ 文本阶（沿 L 轴二分，直到对比度达标）
                          ├─▶ 主色（保种子色，白字/墨字由对比度决定）
                          └─▶ 语义色（色相固定：绿 145° 黄 85° 红 25° 青 230°）
                                        │
                                        ▼
                             audit() 15 条 WCAG 判据 ──不达标──▶ ValueError
```

几条必须遵守的规矩：

- **色相与明度分开考虑**。HSL 的 L 是算术明度：同一 L 下黄色比蓝色亮得多，
  所以"加亮 10%"在不同色相上是完全不同的视觉幅度。OKLCH 是感知均匀空间，
  等 L 步长 ≈ 等视觉亮度步长，`fit_contrast()` 就是沿这条轴做二分。
- **超色域时保色相降 chroma**。把 RGB 硬夹到 [0,1] 会连带改变色相——
  "我调的是绿，出来的是黄绿"。
- **一个令牌一个角色**。`primary_hi`（底色角色，要够暗）与 `primary_text`
  （文字角色，要够亮）方向互斥，早先挤在同一名字里，激活导航实测只有 3.2:1。
  `on_primary` 同理：不是所有主色都配得起白字。
- **换肤要重刷内联样式控件**。QSS 管不到 `StatCard` / 判定条（它们各自
  `setStyleSheet`），不重刷就会留着旧主题的文字色（详见 `_refresh_selection_theme`）。
- **QSS 模板里不许写死颜色**。`QPushButton:hover` 曾写死 `#1c212b`，
  浅色主题下悬停把深色字画到近黑底上。测试会扫描模板源码。

量化工具：`python packaging/palette_report.py [--seed "#e0a92e"]`（对照度报告 + 色卡 PNG）。
回归：`tests/test_ui_palette.py`（文献参考值锚点 + 18 组种子 × 2 模式全达标 +
两个内置主题全达标 + QSS 无写死颜色）。

---

## UI 层的时间预算（改动前必读）

一次 matplotlib 重绘的代价按图分：频谱曲线 ~90 ms、频谱图 ~470 ms、3D 瀑布 ~370 ms
（本机实测，环境见 `packaging/perf_probe.py` 头部）。**任何"每次鼠标移动/每次数据回来"
都会触发的路径，都不能包含整图 `draw()`。** 四条约束：

| 约束 | 位置 | 做法 |
| --- | --- | --- |
| 悬停走 blit | `charts.SpectrumCanvas._blit` | 静态层画完拍一张栅格快照，悬停只 `restore_region` + `draw_artist`(3 个图元) + `blit`（0.15 ms）。**不要**退回 `draw_idle()` 当常态——快照失效时应当场重建一张 |
| 看不见就不画 | `charts.BaseCanvas.redraw_if_visible` | 画布在 `QStackedWidget` 里同一时刻只有一张可见，其余标脏、等 `showEvent` 补画。6 张全量重画是换肤 1.6 s 的主因 |
| 同数据不重画 | `charts.SpectrogramCanvas.set_data` | 对象身份相同的 `(r, data)` 再喂一次直接返回——`_load_view` 命中缓存时每次切页都会重复喂（曾值 438 ms/次） |
| 高频路径要限流/合并 | `WaterfallCanvas._drag_timer`、`MainWindow._schedule_refresh` | 拖拽 3D 旋转限流到 ~8 fps、松手补一帧（角度每次都更新，只限重绘）；分析时每文件的表格刷新合并到 120 ms 一次 |

配套工具：`python packaging/perf_probe.py [--strict]`（9 个场景，含悬停三版对照）。

**已知成本**（有数字，别靠感觉）：频谱图一次重绘 470 ms = `draw()` 370 ms
（mesh 83 + constrained layout 70 + 刻度文本）+ pcolormesh 83 + `ax.clear` 15。
下一步若还要压，方向是把 S 重采样到对数频轴后用 `imshow`（会动观感，需谨慎）。

---

## 为什么截止点检测这么绕（三代演进）

这是本项目最关键也最容易写错的算法。三代实现都留在了注释与测试里：

| 版本 | 做法 | 结果 |
| --- | --- | --- |
| v1 | 按 1/12 倍频程步进下降，累计 N 步取当前频率 | 恒定偏低 N-1 步：44.1k 满带宽文件恒报 `22050/2^(2/12)=19.64k`，**把满带宽 CD 误判成"轻微带限"** |
| v2 | 真实频点上找最高连续超阈段 + dB 域深阈值 | dB 域平滑把砖墙抹成 12% 宽的过渡带，检出值**系统性偏高约 8%** |
| v3 | **功率域平滑 + 半功率点 + 陡度守卫 + 顶部深坑兜底** | 合成基准误差 <1%，砖墙陡度可测 |

关键点：

1. **必须在功率域平滑**。dB 域是算术平均，一路通带加一路阻带会得到 `(0 + -90)/2 = -45 dB`，边缘被抹平；功率域平均则正好得到半功率点 −3 dB。
2. **陡度守卫必不可少**。音乐高频本来就会自然滚降，不设守卫会把「20 dB/oct 的普通衰减」误报成截止，进而把正常音源打上"有损"标签。
3. **守卫失败的两种情况要分开**：真没截止 vs 截止太贴近奈奎斯特（1/3 倍频程里塞不下落差）。用「顶部 5% 频段相对通带的落差」区分——否则 48k 容器里 21.4k 的升频文件会被漏判成「真 48kHz 带宽」。

回归测试见 `tests/test_cutoff.py`，合成基准是"白噪声 + 频域硬切"。

---

## 测试策略

全部测试使用**合成音频**，不依赖任何外部素材，可离线跑：

- 截止点用「白噪声 + FFT 砖墙」——频谱平坦，截止点就是标准答案；
- 响度用「同一正弦、幅度减半」，验证 **6.02 LU 的标度不变性**（比验证绝对值更稳）；
- 真峰值用正弦的已知峰值；
- 缓滚降用巴特沃斯，专门验证"不被误判为截止"。

`tests/conftest.py` 是唯一的素材来源，改算法先看它。
