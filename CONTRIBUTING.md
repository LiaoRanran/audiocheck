# 贡献指南

感谢参与。本项目最看重两件事：**结论可复算**、**错误可回归**。

## 环境

```bash
git clone https://github.com/yourname/audiocheck.git
cd audiocheck
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -e ".[dev,gui]"
pytest -q
```

## 改动要求

1. **每个新指标都要有测试**。测试必须用**合成信号**（见 `tests/conftest.py`），
   不要依赖外部音频文件——否则别人跑不了，也就没法回归。
2. **优先验证"标度不变性/相对关系"，而不是绝对数值**。
   例：验证"幅度减半 → 响度降 6.02 LU"，比断言"响度 == -10.3 LUFS"稳得多。
3. **阈值不要写死在代码里**，加到 `config.Thresholds`，界面与测试都能覆盖。
4. **算法改动必须解释"为什么上一版是错的"**。这个项目的核心算法（截止点检测）
   前后写错两代，注释里保留了两代错法的成因——这是最值钱的部分，别删。

## 加一个新指标

见 [`docs/architecture.md`](docs/architecture.md#加一个新指标3-分钟)。核心是：
在 `src/audiocheck/core/analyzers/` 新建文件、挂 `@register`，其余自动生效。

## 提交信息

用祈使句、说清"为什么"：

```
修正截止点检测的系统性偏差

v1 实现恒定偏低 N-1 步，导致 44.1k 满带宽文件被误判为"轻微带限"。
改为功率域平滑 + 半功率点定位，合成基准误差从 +8% 降到 <1%。
```

## 不要做的事

- 不要为了"看起来更专业"而引入无法解释的指标。**看不懂的指标不如没有**。
- 不要把"查不到"写成"没有"。三态（有/无/未见证据）是这个项目的底线。
- 不要在没有测试的情况下改 `detect_cutoff` / `dsp.loudness` / `verdict`。

## 代码风格

`ruff check src tests` 必须通过（配置见 `pyproject.toml`）。行长 100。
