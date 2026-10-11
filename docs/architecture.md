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
